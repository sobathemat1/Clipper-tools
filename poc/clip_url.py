"""End-to-end PoC URL clipper for Clipper Tools.

Pipeline:
1. Download video via yt-dlp (up to 1080p MP4) and extract 16 kHz mono WAV audio.
2. Transcribe with word-level timestamps using faster-whisper on CUDA.
3. Detect top non-overlapping highlights via hybrid audio RMS energy + speech activity.
4. Generate TikTok/Reels active-word .ass subtitles.
5. Render 1080x1920 clips using FFmpeg h264_nvenc (Fullscreen 9:16 + Split-Screen sample).
"""

import argparse
import gc
import json
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


ROOT_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT_DIR / "storage" / "jobs"
YTDLP_BIN = "/home/angger/anaconda3/envs/clipper/bin/yt-dlp"


def run_cmd(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and raise on non-zero exit."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if proc.returncode != 0:
        err = proc.stderr or ""
        raise RuntimeError(f"Command failed ({' '.join(cmd)}):\n{err}")
    return proc


def get_video_id(url: str) -> str:
    """Resolve video ID from URL via yt-dlp."""
    proc = run_cmd([YTDLP_BIN, "--skip-download", "--print", "id", url])
    video_id = proc.stdout.strip().splitlines()[-1].strip()
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {url}")
    return video_id


def download_and_extract_audio(url: str, job_dir: Path) -> tuple[Path, Path]:
    """Download video up to 1080p MP4 and extract 16 kHz mono WAV audio."""
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4 = job_dir / "raw.mp4"
    audio_wav = job_dir / "audio_16k.wav"

    if not raw_mp4.exists():
        print(f"[1/5] Downloading video to {raw_mp4}...")
        t0 = time.perf_counter()
        run_cmd([
            YTDLP_BIN,
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
            "--merge-output-format", "mp4",
            "-o", str(job_dir / "raw.%(ext)s"),
            url,
        ])
        if not raw_mp4.exists():
            raise FileNotFoundError(f"Expected downloaded video at {raw_mp4}")
        print(f"      Downloaded in {time.perf_counter() - t0:.2f}s")
    else:
        print(f"[1/5] Using cached video: {raw_mp4}")

    if not audio_wav.exists():
        print(f"      Extracting 16kHz mono audio -> {audio_wav}...")
        run_cmd([
            "ffmpeg", "-y",
            "-i", str(raw_mp4),
            "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
            str(audio_wav),
        ])
    return raw_mp4, audio_wav


def transcribe_audio(audio_wav: Path, job_dir: Path, model_size: str = "small") -> dict[str, Any]:
    """Transcribe audio with faster-whisper on CUDA and persist transcript.json."""
    transcript_path = job_dir / "transcript.json"
    if transcript_path.exists():
        print(f"[2/5] Using cached transcript: {transcript_path}")
        return json.loads(transcript_path.read_text(encoding="utf-8"))

    print(f"[2/5] Transcribing with faster-whisper ({model_size}, cuda, int8_float16)...")
    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
    segments_iter, info = model.transcribe(
        str(audio_wav),
        word_timestamps=True,
        vad_filter=True,
    )

    segments_data: list[dict[str, Any]] = []
    words_data: list[dict[str, Any]] = []
    for seg in segments_iter:
        seg_words = []
        for w in seg.words or []:
            item = {
                "word": w.word.strip(),
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
            }
            if item["word"]:
                seg_words.append(item)
                words_data.append(item)
        segments_data.append({
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": seg.text.strip(),
            "words": seg_words,
        })

    result = {
        "language": info.language,
        "duration": round(float(info.duration), 3),
        "segments": segments_data,
        "words": words_data,
    }
    transcript_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    elapsed = time.perf_counter() - t0
    print(
        f"      Transcribed {info.duration:.1f}s ({info.language}) in {elapsed:.2f}s "
        f"| segments={len(segments_data)}, words={len(words_data)}"
    )

    del model
    gc.collect()
    return result


def compute_per_second_rms(audio_wav: Path, total_seconds: int) -> np.ndarray:
    """Compute normalized [0, 1] per-second RMS energy from 16kHz mono PCM WAV."""
    with wave.open(str(audio_wav), "rb") as wf:
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)

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


def compute_per_second_speech(words: list[dict[str, Any]], total_seconds: int) -> np.ndarray:
    """Compute normalized [0, 1] per-second speech density from word timestamps."""
    speech = np.zeros(total_seconds, dtype=np.float32)
    for w in words:
        sec = int(w["start"])
        if 0 <= sec < total_seconds:
            speech[sec] += 1.0
    max_speech = float(np.max(speech)) if len(speech) > 0 else 0.0
    if max_speech > 0:
        speech /= max_speech
    return speech


def snap_boundary(
    target: float,
    boundaries: list[float],
    tolerance: float = 3.0,
) -> float:
    """Snap target timestamp to the nearest segment/word boundary within tolerance."""
    best = target
    best_dist = tolerance + 1e-6
    for b in boundaries:
        dist = abs(b - target)
        if dist <= tolerance and dist < best_dist:
            best = b
            best_dist = dist
    return best


def find_highlights(
    audio_wav: Path,
    transcript: dict[str, Any],
    job_dir: Path,
    num_clips: int = 3,
    target_duration: float = 35.0,
) -> list[dict[str, Any]]:
    """Find top non-overlapping highlight windows using audio energy + speech density."""
    print("[3/5] Analyzing audio energy + speech density for highlights...")
    duration = float(transcript["duration"])
    total_seconds = max(1, int(np.ceil(duration)))
    rms_scores = compute_per_second_rms(audio_wav, total_seconds)
    speech_scores = compute_per_second_speech(transcript["words"], total_seconds)

    win_len = min(int(round(target_duration)), total_seconds)
    candidates: list[tuple[float, float, float]] = []
    max_start = max(0, total_seconds - win_len)
    for start_sec in range(0, max_start + 1):
        end_sec = min(total_seconds, start_sec + win_len)
        rms_avg = float(np.mean(rms_scores[start_sec:end_sec]))
        speech_avg = float(np.mean(speech_scores[start_sec:end_sec]))
        score = 0.65 * rms_avg + 0.35 * speech_avg
        candidates.append((score, float(start_sec), float(end_sec)))

    candidates.sort(key=lambda item: item[0], reverse=True)

    selected: list[tuple[float, float, float]] = []
    min_separation = 25.0
    for score, start_t, end_t in candidates:
        if all(abs(start_t - sel_start) >= min_separation for _, sel_start, _ in selected):
            selected.append((score, start_t, end_t))
        if len(selected) >= num_clips:
            break

    selected.sort(key=lambda item: item[1])

    start_boundaries = [float(seg["start"]) for seg in transcript["segments"]] + [
        float(w["start"]) for w in transcript["words"]
    ]
    end_boundaries = [float(seg["end"]) for seg in transcript["segments"]] + [
        float(w["end"]) for w in transcript["words"]
    ]

    clips_plan: list[dict[str, Any]] = []
    for idx, (score, raw_start, raw_end) in enumerate(selected, start=1):
        snapped_start = snap_boundary(raw_start, start_boundaries, tolerance=3.0)
        snapped_end = snap_boundary(raw_end, end_boundaries, tolerance=3.0)

        snapped_start = max(0.0, snapped_start)
        snapped_end = min(duration, snapped_end)

        clip_len = snapped_end - snapped_start
        if clip_len < 25.0:
            snapped_end = min(duration, snapped_start + 25.0)
            if snapped_end - snapped_start < 25.0:
                snapped_start = max(0.0, snapped_end - 25.0)
        elif clip_len > 45.0:
            snapped_end = snapped_start + 45.0

        clip_words = [
            w["word"]
            for w in transcript["words"]
            if w["end"] > snapped_start and w["start"] < snapped_end
        ]
        clips_plan.append({
            "index": idx,
            "start_time": round(snapped_start, 3),
            "end_time": round(snapped_end, 3),
            "duration": round(snapped_end - snapped_start, 3),
            "score": round(score, 4),
            "text": " ".join(clip_words),
        })

    plan_path = job_dir / "clips_plan.json"
    plan_path.write_text(json.dumps(clips_plan, indent=2, ensure_ascii=False), encoding="utf-8")
    for c in clips_plan:
        print(
            f"      Clip #{c['index']}: {c['start_time']:.2f}s -> {c['end_time']:.2f}s "
            f"({c['duration']:.2f}s, score={c['score']:.3f})"
        )
    return clips_plan


def format_ass_time(seconds: float) -> str:
    """Format seconds into H:MM:SS.cc for ASS subtitles."""
    seconds = max(0.0, seconds)
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_ass_word(text: str) -> str:
    """Escape characters that break ASS override tags."""
    return text.replace("{", "").replace("}", "").replace("\\", "").strip().upper()


def generate_clip_ass(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
) -> None:
    """Generate TikTok/Reels active-word .ass subtitle file for a clip window."""
    clip_words: list[dict[str, Any]] = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        rel_start = max(0.0, w["start"] - clip_start)
        rel_end = min(clip_end - clip_start, max(rel_start + 0.08, w["end"] - clip_start))
        cleaned = clean_ass_word(w["word"])
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

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue_lines: list[str] = []
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
            line_text = " ".join(parts)
            dialogue_lines.append(
                f"Dialogue: 0,{format_ass_time(ev_start)},{format_ass_time(ev_end)},"
                f"Default,,0,0,0,,{line_text}"
            )

    ass_path.write_text(header + "\n".join(dialogue_lines) + "\n", encoding="utf-8")


def render_clip(
    raw_mp4: Path,
    ass_path: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    layout: str = "fullscreen",
) -> float:
    """Render a 1080x1920 clip with h264_nvenc and burned-in .ass subtitles."""
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    t0 = time.perf_counter()

    if layout == "fullscreen":
        vf = f"crop=ih*9/16:ih,scale=1080:1920,ass='{ass_escaped}'"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_time:.3f}",
            "-to", f"{end_time:.3f}",
            "-i", str(raw_mp4),
            "-vf", vf,
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
    elif layout == "splitscreen":
        filter_complex = (
            "[0:v]crop=480:284:40:40,scale=1080:640[cam];"
            "[0:v]crop=911:1080:504:0,scale=1080:1280[game];"
            f"[cam][game]vstack=inputs=2,ass='{ass_escaped}'[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start_time:.3f}",
            "-to", f"{end_time:.3f}",
            "-i", str(raw_mp4),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
    else:
        raise ValueError(f"Unsupported layout: {layout}")

    run_cmd(cmd)
    return time.perf_counter() - t0


def main() -> int:
    parser = argparse.ArgumentParser(description="Clipper Tools End-to-End URL Clipper PoC")
    parser.add_argument("--url", required=True, help="YouTube/Twitch/Kick video URL")
    parser.add_argument("--num-clips", type=int, default=3, help="Number of top clips to generate")
    parser.add_argument("--duration", type=float, default=35.0, help="Target clip duration in seconds")
    parser.add_argument("--model", default="small", help="faster-whisper model size")
    args = parser.parse_args()

    video_id = get_video_id(args.url)
    job_dir = JOBS_DIR / video_id
    print(f"Job directory: {job_dir}")

    raw_mp4, audio_wav = download_and_extract_audio(args.url, job_dir)
    transcript = transcribe_audio(audio_wav, job_dir, model_size=args.model)
    clips_plan = find_highlights(
        audio_wav,
        transcript,
        job_dir,
        num_clips=args.num_clips,
        target_duration=args.duration,
    )

    print("[4/5] Generating .ass subtitle files...")
    ass_paths: list[Path] = []
    for clip in clips_plan:
        ass_path = job_dir / f"clip_{clip['index']:02d}.ass"
        generate_clip_ass(transcript["words"], clip["start_time"], clip["end_time"], ass_path)
        ass_paths.append(ass_path)

    print("[5/5] Rendering clips with FFmpeg h264_nvenc...")
    for clip, ass_path in zip(clips_plan, ass_paths):
        out_full = job_dir / f"clip_{clip['index']:02d}_fullscreen.mp4"
        elapsed = render_clip(
            raw_mp4,
            ass_path,
            out_full,
            clip["start_time"],
            clip["end_time"],
            layout="fullscreen",
        )
        print(f"      Rendered {out_full.name} in {elapsed:.2f}s")

        if clip["index"] == 1:
            out_split = job_dir / "clip_01_splitscreen.mp4"
            split_elapsed = render_clip(
                raw_mp4,
                ass_path,
                out_split,
                clip["start_time"],
                clip["end_time"],
                layout="splitscreen",
            )
            print(f"      Rendered {out_split.name} in {split_elapsed:.2f}s")

    print(f"\nDone! All outputs saved in: {job_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
