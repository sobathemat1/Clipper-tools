"""Core video download, CUDA/CPU transcription, highlight detection, and NVENC/x264 rendering pipeline."""

import copy
import functools
import gc
import json
import os
import platform
import re
import subprocess
import wave
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import numpy as np

from backend.app.config import DEFAULT_SPLIT_CONFIG, YTDLP_BIN


_NVENC_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
_X264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def normalize_video_url(url: str) -> str:
    """Normalize YouTube and streaming URLs for duplicate detection."""
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    qs = parse_qs(parsed.query)
    path = parsed.path.rstrip("/")

    if host == "youtu.be":
        vid = path.lstrip("/").split("/")[0]
        if vid:
            return f"youtube:{vid}"

    if host == "youtube.com" or host.endswith(".youtube.com"):
        if path == "/watch" and qs.get("v"):
            return f"youtube:{qs['v'][0]}"
        for prefix in ("/shorts/", "/live/", "/embed/"):
            if path.startswith(prefix):
                vid = path[len(prefix):].split("/")[0]
                if vid:
                    return f"youtube:{vid}"

    filtered_qs = {k: v for k, v in qs.items() if k not in {"si", "feature", "t"}}
    normalized_query = urlencode(sorted(filtered_qs.items()), doseq=True)
    return urlunparse((parsed.scheme.lower(), host, path, "", normalized_query, ""))


def _detect_cpu_info() -> tuple[str, int]:
    cores = os.cpu_count() or 1
    cpu_name = platform.processor() or platform.machine() or "CPU"
    try:
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    cpu_name = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    return cpu_name, cores


def _detect_ram_gb() -> float:
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except Exception:
        pass
    return 0.0


def _detect_gpu_info() -> tuple[str, int]:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 2:
                return parts[0], int(float(parts[1]))
            return parts[0], 0
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["lspci"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "VGA compatible controller" in line or "3D controller" in line:
                    desc = line.split(":", 2)[-1].strip()
                    return desc, 0
    except Exception:
        pass

    return "Tidak ada GPU NVIDIA (Mode CPU)", 0


@functools.lru_cache(maxsize=1)
def detect_compute_capabilities() -> dict[str, Any]:
    """Auto-detect CUDA Whisper and FFmpeg h264_nvenc support with CPU/libx264 fallback."""
    device_pref = os.getenv("CLIPPER_DEVICE", "auto").strip().lower()
    if device_pref == "cpu":
        whisper_device, whisper_compute = "cpu", "int8"
    else:
        has_cuda = False
        try:
            import ctranslate2

            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False
        if has_cuda:
            whisper_device, whisper_compute = "cuda", "int8_float16"
        else:
            whisper_device, whisper_compute = "cpu", "int8"

    encoder_pref = os.getenv("CLIPPER_VIDEO_ENCODER", "auto").strip().lower()
    if encoder_pref == "libx264":
        video_encoder, encoder_args = "libx264", list(_X264_ARGS)
    else:
        nvenc_ok = False
        try:
            probe = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.04",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            nvenc_ok = probe.returncode == 0
        except Exception:
            nvenc_ok = False

        if nvenc_ok:
            video_encoder, encoder_args = "h264_nvenc", list(_NVENC_ARGS)
        else:
            video_encoder, encoder_args = "libx264", list(_X264_ARGS)

    if whisper_device == "cuda" and video_encoder == "h264_nvenc":
        mode = "GPU (CUDA + NVENC)"
    elif whisper_device == "cuda" or video_encoder == "h264_nvenc":
        mode = "GPU Partial"
    else:
        mode = "CPU Only"

    cpu_name, cpu_cores = _detect_cpu_info()
    ram_total_gb = _detect_ram_gb()
    gpu_name, gpu_vram_mb = _detect_gpu_info()

    return {
        "mode": mode,
        "cpu_name": cpu_name,
        "cpu_cores": cpu_cores,
        "ram_total_gb": ram_total_gb,
        "gpu_name": gpu_name,
        "gpu_vram_mb": gpu_vram_mb,
        "whisper_device": whisper_device,
        "whisper_compute_type": whisper_compute,
        "video_encoder": video_encoder,
        "encoder_args": encoder_args,
    }


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run subprocess command and raise RuntimeError with stderr on failure."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}):\n{proc.stderr}")
    return proc


def probe_video_dimensions(video_path: Path) -> tuple[int, int, float]:
    """Return (width, height, duration) for a video file using ffprobe."""
    proc = run_cmd([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ])
    info = json.loads(proc.stdout)
    stream = (info.get("streams") or [{}])[0]
    fmt = info.get("format") or {}
    return (
        int(stream.get("width", 1920)),
        int(stream.get("height", 1080)),
        float(fmt.get("duration", 0.0)),
    )


def extract_preview_frame(
    raw_mp4: Path,
    preview_jpg: Path,
    timestamp: float = 10.0,
) -> Path:
    """Extract a representative JPEG frame for Split-Screen coordinate setup."""
    _, _, duration = probe_video_dimensions(raw_mp4)
    seek_t = min(timestamp, max(0.0, duration * 0.25)) if duration > 0 else 0.0
    run_cmd([
        "ffmpeg", "-y",
        "-ss", f"{seek_t:.2f}",
        "-i", str(raw_mp4),
        "-frames:v", "1",
        "-q:v", "2",
        str(preview_jpg),
    ])
    return preview_jpg


def fetch_url_title(url: str) -> str:
    """Fetch video title via yt-dlp without downloading the video."""
    try:
        proc = run_cmd([YTDLP_BIN, "--skip-download", "--print", "title", url])
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    except Exception:
        pass
    return "YouTube / Stream Clip Job"


def prepare_media(
    source_type: str,
    source_value: str,
    job_dir: Path,
) -> tuple[Path, Path, Path, str]:
    """Download/locate raw.mp4, extract 16kHz mono WAV audio, and capture preview frame."""
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4 = job_dir / "raw.mp4"
    audio_wav = job_dir / "audio_16k.wav"
    preview_jpg = job_dir / "preview.jpg"

    if source_type == "url":
        title = fetch_url_title(source_value)
        if not raw_mp4.exists():
            run_cmd([
                YTDLP_BIN,
                "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
                "--merge-output-format", "mp4",
                "-o", str(job_dir / "raw.%(ext)s"),
                source_value,
            ])
            if not raw_mp4.exists():
                raise FileNotFoundError(f"Downloaded video not found at {raw_mp4}")
    elif source_type == "local_file":
        candidate = Path(source_value)
        if not raw_mp4.exists() and candidate.exists():
            raw_mp4 = candidate
        if not raw_mp4.exists():
            raise FileNotFoundError(f"Uploaded video not found at {raw_mp4}")
        title = Path(source_value).stem or "Local Video Job"
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    if not audio_wav.exists():
        run_cmd([
            "ffmpeg", "-y",
            "-i", str(raw_mp4),
            "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
            str(audio_wav),
        ])

    if not preview_jpg.exists():
        extract_preview_frame(raw_mp4, preview_jpg)

    return raw_mp4, audio_wav, preview_jpg, title


def transcribe_audio(
    audio_wav: Path,
    job_dir: Path,
    model_size: str = "small",
    language: str = "auto",
) -> dict[str, Any]:
    """Transcribe audio using faster-whisper (CUDA with automatic CPU fallback) and cache transcript.json."""
    transcript_path = job_dir / "transcript.json"
    if transcript_path.exists():
        return json.loads(transcript_path.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    caps = detect_compute_capabilities()
    lang_arg = None if language == "auto" else language

    def _run_transcribe(device: str, compute_type: str) -> dict[str, Any]:
        loaded = WhisperModel(model_size, device=device, compute_type=compute_type)
        try:
            segments_iter, info = loaded.transcribe(
                str(audio_wav),
                language=lang_arg,
                word_timestamps=True,
                vad_filter=True,
            )
            segments_data: list[dict[str, Any]] = []
            words_data: list[dict[str, Any]] = []
            for seg in segments_iter:
                seg_words: list[dict[str, Any]] = []
                for w in seg.words or []:
                    text = w.word.strip()
                    if not text:
                        continue
                    item = {
                        "word": text,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                    }
                    seg_words.append(item)
                    words_data.append(item)
                segments_data.append({
                    "start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                    "text": seg.text.strip(),
                    "words": seg_words,
                })
            return {
                "language": info.language,
                "duration": round(float(info.duration), 3),
                "segments": segments_data,
                "words": words_data,
            }
        finally:
            del loaded
            gc.collect()

    try:
        result = _run_transcribe(caps["whisper_device"], caps["whisper_compute_type"])
    except RuntimeError:
        if caps["whisper_device"] != "cuda":
            raise
        result = _run_transcribe("cpu", "int8")

    transcript_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def compute_per_second_rms(audio_wav: Path, total_seconds: int) -> np.ndarray:
    """Compute normalized [0, 1] per-second RMS energy from 16kHz mono WAV."""
    with wave.open(str(audio_wav), "rb") as wf:
        sample_rate = wf.getframerate()
        raw_bytes = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    rms = np.zeros(total_seconds, dtype=np.float32)
    for sec in range(total_seconds):
        s_idx = sec * sample_rate
        e_idx = min((sec + 1) * sample_rate, len(samples))
        if e_idx > s_idx:
            chunk = samples[s_idx:e_idx]
            rms[sec] = float(np.sqrt(np.mean(chunk * chunk)))

    max_rms = float(np.max(rms)) if len(rms) > 0 else 0.0
    if max_rms > 0:
        rms /= max_rms
    return rms


_REACTION_KEYWORDS = {
    "anjir", "anjay", "gila", "sumpah", "sumpil", "parah", "kaget", "mati",
    "menang", "waduh", "aduh", "astaga", "buset", "kocak", "lucu", "wtf", "what",
    "fuck", "fucking", "shit", "damn", "dead", "omg", "gg", "wkwk", "haha",
    "cok", "coh", "cuy", "nabrak", "oleng", "oling", "rusak", "gagal", "jatuh",
    "rem", "tolong", "bahaya", "salah", "putar", "lawan", "challenge", "ngebut",
}

_INTRO_FILLER_PHRASES = (
    "selamat sore", "selamat malam", "selamat siang", "selamat pagi",
    "upsen", "absen", "balik lagi", "cek suara", "tes suara",
)


def compute_per_second_penalty(
    segments: list[dict[str, Any]],
    total_seconds: int,
) -> np.ndarray:
    """Dampen livestream intro roll-calls and mic checks on long VODs."""
    penalty = np.ones(total_seconds, dtype=np.float32)
    if total_seconds > 300:
        penalty[:60] = 0.65
    for seg in segments:
        txt = str(seg.get("text", "")).lower()
        if any(phrase in txt for phrase in _INTRO_FILLER_PHRASES):
            s_i = max(0, int(float(seg.get("start", 0.0))))
            e_i = min(total_seconds, int(float(seg.get("end", 0.0))) + 1)
            if e_i > s_i:
                penalty[s_i:e_i] *= 0.55
    return penalty


def compute_per_second_audio_spikes(rms_scores: np.ndarray) -> np.ndarray:
    """Compute normalized [0, 1] sudden loudness spikes over a 15-second local baseline."""
    total_seconds = len(rms_scores)
    spike = np.zeros(total_seconds, dtype=np.float32)
    for t in range(total_seconds):
        w_start = max(0, t - 12)
        w_end = min(total_seconds, t + 3)
        local_mean = float(np.mean(rms_scores[w_start:w_end]))
        spike[t] = max(0.0, float(rms_scores[t]) - local_mean)
    max_spike = float(np.max(spike)) if total_seconds > 0 else 0.0
    if max_spike > 0:
        spike /= max_spike
    return spike


def compute_per_second_reaction(
    words: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    total_seconds: int,
) -> np.ndarray:
    """Score per-second gaming/reaction keywords and expressive punctuation (! or ?)."""
    react = np.zeros(total_seconds, dtype=np.float32)
    for w in words:
        sec = int(float(w["start"]))
        if not (0 <= sec < total_seconds):
            continue
        raw_word = str(w.get("word", ""))
        cleaned = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", raw_word).lower()
        if cleaned in _REACTION_KEYWORDS:
            react[sec] += 1.0
        if "!" in raw_word or "?" in raw_word:
            react[sec] += 0.6

    for seg in segments:
        text = str(seg.get("text", ""))
        if "!" in text or "?" in text:
            sec = int(float(seg.get("start", 0.0)))
            if 0 <= sec < total_seconds:
                react[sec] += 0.5

    max_react = float(np.max(react)) if total_seconds > 0 else 0.0
    if max_react > 0:
        react /= max_react
    return react


def compute_per_second_speech(words: list[dict[str, Any]], total_seconds: int) -> np.ndarray:
    """Compute normalized [0, 1] per-second speech density from word timestamps."""
    speech = np.zeros(total_seconds, dtype=np.float32)
    for w in words:
        sec = int(float(w["start"]))
        if 0 <= sec < total_seconds:
            speech[sec] += 1.0
    max_speech = float(np.max(speech)) if len(speech) > 0 else 0.0
    if max_speech > 0:
        speech /= max_speech
    return speech


def slice_words_for_window(
    transcript_words: list[dict[str, Any]],
    start_time: float,
    end_time: float,
) -> list[dict[str, Any]]:
    """Return deep-copied word dicts intersecting [start_time, end_time]."""
    return [
        copy.deepcopy(w)
        for w in transcript_words
        if float(w["end"]) > start_time and float(w["start"]) < end_time
    ]


def _snap_within_bounds(
    target: float,
    boundaries: list[float],
    min_val: float,
    max_val: float,
    tolerance: float = 3.0,
) -> tuple[float, bool]:
    best = min(max(target, min_val), max_val)
    best_dist = tolerance + 1e-6
    matched = False
    for b in boundaries:
        if min_val <= b <= max_val:
            dist = abs(b - target)
            if dist <= tolerance and dist < best_dist:
                best = b
                best_dist = dist
                matched = True
    return best, matched


def _snap_preferred(
    target: float,
    seg_boundaries: list[float],
    word_boundaries: list[float],
    min_val: float,
    max_val: float,
) -> float:
    snapped, matched = _snap_within_bounds(
        target, seg_boundaries, min_val=min_val, max_val=max_val, tolerance=4.5
    )
    if matched:
        return snapped
    snapped_word, _ = _snap_within_bounds(
        target, word_boundaries, min_val=min_val, max_val=max_val, tolerance=3.0
    )
    return snapped_word


def find_highlights(
    audio_wav: Path,
    transcript: dict[str, Any],
    num_clips: int = 3,
    target_duration: float = 35.0,
) -> list[dict[str, Any]]:
    """Detect top non-overlapping highlight windows using 4-signal audio & reaction analysis."""
    duration = float(transcript["duration"])
    total_seconds = max(1, int(np.ceil(duration)))
    has_audio = audio_wav.exists()

    if has_audio:
        rms_scores = compute_per_second_rms(audio_wav, total_seconds)
        spike_scores = compute_per_second_audio_spikes(rms_scores)
    else:
        rms_scores = np.zeros(total_seconds, dtype=np.float32)
        spike_scores = np.zeros(total_seconds, dtype=np.float32)

    words = transcript.get("words", [])
    segments = transcript.get("segments", [])
    reaction_scores = compute_per_second_reaction(words, segments, total_seconds)
    speech_scores = compute_per_second_speech(words, total_seconds)
    penalty_scores = compute_per_second_penalty(segments, total_seconds)

    win_len = max(5, min(int(round(target_duration)), total_seconds))
    candidates: list[tuple[float, float, float]] = []
    max_start = max(0, total_seconds - win_len)
    for start_sec in range(0, max_start + 1):
        end_sec = min(float(total_seconds), float(start_sec + win_len))
        s_i = start_sec
        e_i = max(s_i + 1, int(end_sec))
        hook_end = min(e_i, s_i + 6)
        speech_avg = float(np.mean(speech_scores[s_i:e_i]))
        reaction_avg = float(np.mean(reaction_scores[s_i:e_i]))
        penalty_avg = float(np.mean(penalty_scores[s_i:e_i]))
        if has_audio:
            rms_avg = float(np.mean(rms_scores[s_i:e_i]))
            spike_avg = float(np.mean(spike_scores[s_i:e_i]))
            hook_avg = float(np.mean(0.5 * spike_scores[s_i:hook_end] + 0.5 * reaction_scores[s_i:hook_end]))
            raw_score = 0.22 * rms_avg + 0.28 * spike_avg + 0.38 * reaction_avg + 0.12 * speech_avg + 0.08 * hook_avg
        else:
            hook_avg = float(np.mean(reaction_scores[s_i:hook_end]))
            raw_score = 0.65 * reaction_avg + 0.35 * speech_avg + 0.08 * hook_avg
        score = raw_score * penalty_avg
        candidates.append((score, float(start_sec), min(duration, end_sec)))

    candidates.sort(key=lambda item: item[0], reverse=True)

    min_gap = max(2.0, min(30.0, (duration - num_clips * win_len) / max(1, num_clips * 2)))
    selected: list[tuple[float, float, float]] = []
    for score, s, e in candidates:
        if all(e <= sel_s - min_gap or s >= sel_e + min_gap for _, sel_s, sel_e in selected):
            selected.append((score, s, e))
        if len(selected) >= num_clips:
            break

    if len(selected) < num_clips:
        for score, s, e in candidates:
            if any(abs(s - sel_s) < 1e-6 for _, sel_s, _ in selected):
                continue
            if all(e <= sel_s - 0.5 or s >= sel_e + 0.5 for _, sel_s, sel_e in selected):
                selected.append((score, s, e))
            if len(selected) >= num_clips:
                break

    selected.sort(key=lambda item: item[1])

    seg_starts = [float(seg["start"]) for seg in segments]
    seg_ends = [float(seg["end"]) for seg in segments]
    word_starts = [float(w["start"]) for w in words]
    word_ends = [float(w["end"]) for w in words]

    max_dur = max(45.0, float(target_duration) + 10.0)
    clips: list[dict[str, Any]] = []
    for idx, (score, raw_start, raw_end) in enumerate(selected):
        prev_end = clips[-1]["end_time"] + 0.5 if clips else 0.0
        next_start = selected[idx + 1][1] - 0.5 if idx + 1 < len(selected) else duration

        min_allowed_start = max(0.0, prev_end)
        max_allowed_end = min(duration, max(min_allowed_start + 5.0, next_start))

        min_clip_len = min(10.0, max(2.0, (raw_end - raw_start) * 0.5))
        snapped_start = _snap_preferred(
            raw_start,
            seg_starts,
            word_starts,
            min_val=min_allowed_start,
            max_val=max(min_allowed_start, raw_end - min_clip_len),
        )
        snapped_end = _snap_preferred(
            raw_end,
            seg_ends,
            word_ends,
            min_val=min(max_allowed_end, snapped_start + min_clip_len),
            max_val=max_allowed_end,
        )

        if snapped_end - snapped_start > max_dur:
            snapped_end = snapped_start + max_dur

        snapped_start = round(snapped_start, 3)
        snapped_end = round(snapped_end, 3)
        clip_words = slice_words_for_window(words, snapped_start, snapped_end)
        snippet = " ".join(w["word"] for w in clip_words[:6]).strip()
        title = f"Clip #{idx + 1}" + (f" — {snippet}..." if snippet else "")

        clips.append({
            "clip_index": idx + 1,
            "title": title,
            "start_time": snapped_start,
            "end_time": snapped_end,
            "score": round(score, 4),
            "words": clip_words,
        })

    return clips


def _format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean_ass_word(text: str) -> str:
    return text.replace("{", "").replace("}", "").replace("\\", "").strip().upper()


def generate_clip_ass(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
    hook_text: str = "",
) -> None:
    """Generate TikTok/Reels active-word .ass subtitle file for a clip."""
    clip_dur = max(0.1, clip_end - clip_start)
    clip_words: list[dict[str, Any]] = []
    for w in words:
        w_start = float(w["start"])
        w_end = float(w["end"])
        if w_end <= clip_start or w_start >= clip_end:
            continue
        rel_start = max(0.0, w_start - clip_start)
        rel_end = min(clip_dur, max(rel_start + 0.08, w_end - clip_start))
        cleaned = _clean_ass_word(str(w["word"]))
        if cleaned:
            clip_words.append({"word": cleaned, "start": rel_start, "end": rel_end})

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for w in clip_words:
        if current:
            gap = w["start"] - current[-1]["end"]
            projected_chars = sum(len(x["word"]) for x in current) + len(w["word"]) + len(current)
            if gap > 0.6 or len(current) >= 4 or (len(current) >= 3 and projected_chars > 20):
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)

    header = """[Script Info]
Title: Clipper Tools Dynamic Subtitle
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,340,1
Style: HookBanner,DejaVu Sans,58,&H0000FFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,14,0,8,60,60,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    clean_hook = _clean_ass_word(hook_text)
    if clean_hook:
        lines.append(
            f"Dialogue: 1,0:00:00.00,{_format_ass_time(clip_dur)},"
            f"HookBanner,,0,0,0,,{clean_hook}"
        )
    for chunk in chunks:
        for i, active_word in enumerate(chunk):
            ev_start = active_word["start"]
            ev_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else active_word["end"]
            if ev_end <= ev_start:
                ev_end = ev_start + 0.08
            parts = []
            for j, token in enumerate(chunk):
                color = "{\\c&H0000FFFF&}" if j == i else "{\\c&H00FFFFFF&}"
                parts.append(f"{color}{token['word']}")
            lines.append(
                f"Dialogue: 0,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},"
                f"Default,,0,0,0,,{' '.join(parts)}"
            )

    ass_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _clamp_box(x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x = max(0, min(x, frame_w - 16))
    y = max(0, min(y, frame_h - 16))
    w = max(16, min(w, frame_w - x))
    h = max(16, min(h, frame_h - y))
    return x, y, w, h


def render_clip_nvenc(
    raw_mp4: Path,
    ass_path: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    layout_mode: str = "fullscreen",
    split_config: dict[str, Any] | None = None,
) -> None:
    """Render a 1080x1920 vertical clip with NVENC (or libx264 fallback) and .ass subtitles."""
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    caps = detect_compute_capabilities()

    def _build_cmd(encoder_args: list[str]) -> list[str]:
        if layout_mode == "fullscreen":
            vf = f"crop=ih*9/16:ih,scale=1080:1920,setsar=1,ass='{ass_escaped}'"
            return [
                "ffmpeg", "-y",
                "-ss", f"{start_time:.3f}",
                "-to", f"{end_time:.3f}",
                "-i", str(raw_mp4),
                "-vf", vf,
                *encoder_args,
                "-c:a", "aac", "-b:a", "192k",
                str(output_path),
            ]
        if layout_mode == "splitscreen":
            cfg = {**DEFAULT_SPLIT_CONFIG, **(split_config or {})}
            frame_w, frame_h, _ = probe_video_dimensions(raw_mp4)
            cx, cy, cw, ch = _clamp_box(
                int(cfg["cam_x"]), int(cfg["cam_y"]), int(cfg["cam_w"]), int(cfg["cam_h"]),
                frame_w, frame_h,
            )
            gx, gy, gw, gh = _clamp_box(
                int(cfg["game_x"]), int(cfg["game_y"]), int(cfg["game_w"]), int(cfg["game_h"]),
                frame_w, frame_h,
            )
            filter_complex = (
                f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:640,setsar=1[cam];"
                f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale=1080:1280,setsar=1[game];"
                f"[cam][game]vstack=inputs=2,ass='{ass_escaped}'[out]"
            )
            return [
                "ffmpeg", "-y",
                "-ss", f"{start_time:.3f}",
                "-to", f"{end_time:.3f}",
                "-i", str(raw_mp4),
                "-filter_complex", filter_complex,
                "-map", "[out]", "-map", "0:a?",
                *encoder_args,
                "-c:a", "aac", "-b:a", "192k",
                str(output_path),
            ]
        raise ValueError(f"Unsupported layout_mode: {layout_mode}")

    try:
        run_cmd(_build_cmd(caps["encoder_args"]))
    except RuntimeError:
        if caps["video_encoder"] != "h264_nvenc":
            raise
        run_cmd(_build_cmd(list(_X264_ARGS)))
