"""Unit tests for core video/audio processing helpers in pipeline.py."""

import wave
from pathlib import Path

import numpy as np

from backend.app.services.llm_scorer import enhance_highlights_with_llm
from backend.app.services.pipeline import (
    _clamp_box,
    detect_compute_capabilities,
    find_highlights,
    generate_clip_ass,
    normalize_video_url,
)


def _write_pcm_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.astype(np.int16).tobytes())


def test_find_highlights_strict_non_overlap(tmp_path: Path) -> None:
    sample_rate = 16000
    duration_sec = 180
    total_samples = sample_rate * duration_sec
    t = np.arange(total_samples, dtype=np.float32) / sample_rate
    audio = np.zeros(total_samples, dtype=np.float32)

    for start_s, end_s in [(10, 40), (70, 100), (130, 160)]:
        s_idx, e_idx = start_s * sample_rate, end_s * sample_rate
        audio[s_idx:e_idx] = 20000.0 * np.sin(2 * np.pi * 440.0 * t[s_idx:e_idx])

    audio_wav = tmp_path / "audio_16k.wav"
    _write_pcm_wav(audio_wav, audio, sample_rate)

    words = [
        {"word": f"w{sec}", "start": float(sec), "end": float(sec) + 0.8}
        for sec in range(duration_sec)
    ]
    segments = [
        {"start": float(sec), "end": float(sec + 5), "text": "sample", "words": words[sec : sec + 5]}
        for sec in range(0, duration_sec, 5)
    ]
    transcript = {
        "language": "id",
        "duration": float(duration_sec),
        "segments": segments,
        "words": words,
    }

    clips = find_highlights(audio_wav, transcript, num_clips=3, target_duration=30.0)
    assert len(clips) == 3
    for i in range(len(clips) - 1):
        assert clips[i]["start_time"] < clips[i + 1]["start_time"]
        assert clips[i]["end_time"] <= clips[i + 1]["start_time"]


def test_find_highlights_short_silent_video(tmp_path: Path) -> None:
    sample_rate = 16000
    duration_sec = 8
    audio_wav = tmp_path / "silent.wav"
    _write_pcm_wav(audio_wav, np.zeros(sample_rate * duration_sec, dtype=np.int16), sample_rate)

    transcript = {
        "language": "id",
        "duration": 8.0,
        "segments": [],
        "words": [],
    }

    clips = find_highlights(audio_wav, transcript, num_clips=2, target_duration=10.0)
    assert len(clips) >= 1
    for clip in clips:
        assert 0.0 <= clip["start_time"] < clip["end_time"] <= 8.0


def test_generate_clip_ass_escaping_and_zero_duration(tmp_path: Path) -> None:
    ass_path = tmp_path / "out.ass"
    words = [
        {"word": "{bad}", "start": 5.0, "end": 5.0},
        {"word": "\\backslash", "start": 5.2, "end": 5.5},
        {"word": "normal", "start": 5.6, "end": 6.0},
    ]

    generate_clip_ass(words, clip_start=4.0, clip_end=10.0, ass_path=ass_path)
    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue_lines) == 3
    for line in dialogue_lines:
        payload = line.split(",,", 1)[1]
        assert "BAD" in payload
        assert "BACKSLASH" in payload
        assert "{bad}" not in payload
        assert "\\backslash" not in payload
        parts = line.split(",")
        assert parts[1] != parts[2]


def test_clamp_box_bounds() -> None:
    assert _clamp_box(-50, -20, 3000, 2000, 1920, 1080) == (0, 0, 1920, 1080)
    x, y, w, h = _clamp_box(1910, 1070, 5, 5, 1920, 1080)
    assert w >= 16 and h >= 16
    assert x + w <= 1920 and y + h <= 1080


def test_generate_clip_ass_with_hook_banner(tmp_path: Path) -> None:
    ass_path = tmp_path / "hook.ass"
    words = [{"word": "mantap", "start": 1.0, "end": 1.4}]
    generate_clip_ass(
        words,
        clip_start=0.0,
        clip_end=10.0,
        ass_path=ass_path,
        hook_text="GTA VI 100% TAMAT",
    )
    content = ass_path.read_text(encoding="utf-8")
    assert "Style: HookBanner" in content
    assert "Dialogue: 1,0:00:00.00,0:00:10.00,HookBanner,,0,0,0,,GTA VI 100% TAMAT" in content


def test_enhance_highlights_with_llm_fallback(monkeypatch) -> None:
    base_clips = [
        {
            "clip_index": 1,
            "title": "Clip #1",
            "start_time": 0.0,
            "end_time": 10.0,
            "words": [{"word": "halo", "start": 0.5, "end": 1.0}],
        }
    ]
    transcript = {"language": "id"}

    assert enhance_highlights_with_llm(transcript, base_clips, api_key="") == base_clips

    def _boom(*args, **kwargs):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert enhance_highlights_with_llm(transcript, base_clips, api_key="fake-key") == base_clips


def test_normalize_video_url() -> None:
    assert normalize_video_url("https://youtu.be/diZ6-kQW_Ok?si=abc123&t=15") == "youtube:diZ6-kQW_Ok"
    assert normalize_video_url("https://www.youtube.com/watch?v=diZ6-kQW_Ok&feature=share") == "youtube:diZ6-kQW_Ok"
    assert normalize_video_url("https://youtube.com/shorts/abcDEF123?si=xyz") == "youtube:abcDEF123"
    assert normalize_video_url("https://www.youtube.com/live/liveXYZ99") == "youtube:liveXYZ99"
    assert normalize_video_url("https://twitch.tv/streamer/clip/cool/?t=5") == "https://twitch.tv/streamer/clip/cool"


def test_detect_compute_capabilities_cpu_fallback(monkeypatch) -> None:
    detect_compute_capabilities.cache_clear()
    try:
        monkeypatch.setenv("CLIPPER_DEVICE", "cpu")
        monkeypatch.setenv("CLIPPER_VIDEO_ENCODER", "libx264")
        caps = detect_compute_capabilities()
        assert caps["whisper_device"] == "cpu"
        assert caps["whisper_compute_type"] == "int8"
        assert caps["video_encoder"] == "libx264"
        assert "-c:v" in caps["encoder_args"] and "libx264" in caps["encoder_args"]
    finally:
        detect_compute_capabilities.cache_clear()


def test_dynamic_ytdlp_resolution_and_deployment_files(monkeypatch) -> None:
    from backend.app.config import ROOT_DIR, YTDLP_BIN, _resolve_ytdlp_bin

    assert Path(YTDLP_BIN).exists() or YTDLP_BIN == "yt-dlp"
    monkeypatch.setenv("CLIPPER_YTDLP_BIN", "/custom/yt-dlp")
    assert _resolve_ytdlp_bin() == "/custom/yt-dlp"

    dockerfile = ROOT_DIR / "Dockerfile"
    compose = ROOT_DIR / "docker-compose.yml"
    dockerignore = ROOT_DIR / ".dockerignore"
    ci_yml = ROOT_DIR / ".github" / "workflows" / "ci.yml"

    assert dockerfile.exists()
    assert compose.exists()
    assert dockerignore.exists()
    assert ci_yml.exists()

    compose_text = compose.read_text(encoding="utf-8")
    assert "clipper-cpu:" in compose_text
    assert "clipper-gpu:" in compose_text
    assert 'profiles: ["cpu"]' in compose_text
    assert 'profiles: ["gpu"]' in compose_text
    assert "./storage:/app/storage" in compose_text


def test_4_signal_highlight_prefers_spike_and_reaction(tmp_path: Path) -> None:
    sample_rate = 16000
    duration_sec = 90
    total_samples = sample_rate * duration_sec
    t = np.arange(total_samples, dtype=np.float32) / sample_rate
    audio = np.zeros(total_samples, dtype=np.float32)

    # Flat moderate background noise on 10..35s
    audio[10 * sample_rate : 35 * sample_rate] = 6000.0 * np.sin(
        2 * np.pi * 220.0 * t[10 * sample_rate : 35 * sample_rate]
    )
    # Sudden loud spike on 55..75s following silence
    audio[55 * sample_rate : 75 * sample_rate] = 24000.0 * np.sin(
        2 * np.pi * 440.0 * t[55 * sample_rate : 75 * sample_rate]
    )

    audio_wav = tmp_path / "spike.wav"
    _write_pcm_wav(audio_wav, audio, sample_rate)

    words = []
    for sec in range(10, 35):
        words.append({"word": "biasa", "start": float(sec), "end": float(sec) + 0.5})
    for sec in range(55, 75):
        tok = "Anjir!" if sec % 2 == 0 else "Gila!"
        words.append({"word": tok, "start": float(sec), "end": float(sec) + 0.5})

    segments = [
        {"start": 10.0, "end": 35.0, "text": "ngobrol biasa", "words": words[:25]},
        {"start": 55.0, "end": 75.0, "text": "Anjir! Gila! WTF!", "words": words[25:]},
    ]
    transcript = {"language": "id", "duration": float(duration_sec), "segments": segments, "words": words}

    clips = find_highlights(audio_wav, transcript, num_clips=1, target_duration=20.0)
    assert len(clips) == 1
    assert clips[0]["start_time"] >= 50.0 and clips[0]["end_time"] <= 80.0


def test_find_highlights_transcript_only_when_audio_missing(tmp_path: Path) -> None:
    missing_wav = tmp_path / "does_not_exist.wav"
    words = [
        {"word": "biasa", "start": 2.0, "end": 2.5},
        {"word": "Anjir!", "start": 32.0, "end": 32.5},
        {"word": "Gila!", "start": 33.0, "end": 33.5},
        {"word": "Wkwk", "start": 34.0, "end": 34.5},
        {"word": "WTF!", "start": 35.0, "end": 35.5},
    ]
    segments = [
        {"start": 1.0, "end": 10.0, "text": "biasa", "words": words[:1]},
        {"start": 31.0, "end": 48.0, "text": "Anjir! Gila! Wkwk WTF!", "words": words[1:]},
    ]
    transcript = {"language": "id", "duration": 60.0, "segments": segments, "words": words}

    clips = find_highlights(missing_wav, transcript, num_clips=1, target_duration=20.0)
    assert len(clips) == 1
    assert clips[0]["start_time"] >= 25.0
