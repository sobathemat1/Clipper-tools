"""PoC benchmark script for Clipper Tools Stage 2.

Tests:
1. Synthetic 1080p test video generation (or custom input).
2. FFmpeg h264_nvenc rendering for Fullscreen 9:16 and Split-Screen layouts with .ass subtitles.
3. faster-whisper CUDA transcription speed & word-level timestamps.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = ROOT_DIR / "storage" / "poc"


def run_cmd(cmd: list[str]) -> float:
    """Run a command and return elapsed seconds."""
    start = time.perf_counter()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}):\n{proc.stderr}")
    return elapsed


def ensure_sample_video(output_path: Path, duration: int = 10) -> None:
    """Generate a 1080p 60fps synthetic test video with audio."""
    if output_path.exists():
        return
    print(f"[1/4] Generating {duration}s 1080p@60fps synthetic test video...")
    elapsed = run_cmd([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=60:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "h264_nvenc", "-preset", "p4", "-c:a", "aac",
        str(output_path),
    ])
    print(f"      Generated in {elapsed:.2f}s -> {output_path}")


def write_sample_ass(ass_path: Path) -> None:
    """Write a TikTok-style .ass subtitle file."""
    content = """[Script Info]
Title: Clipper PoC Subtitle
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,68,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:03.00,Default,,0,0,0,,{\\c&H0000FFFF&}INI\\N{\\c&H00FFFFFF&}TES SUBTITLE CLIPPER
Dialogue: 0,0:00:03.20,0:00:06.50,Default,,0,0,0,,RENDER PAKAI {\\c&H0000FF00&}NVENC\\N{\\c&H00FFFFFF&}SUPER CEPAT!
Dialogue: 0,0:00:06.80,0:00:09.50,Default,,0,0,0,,SIAP BUAT {\\c&H0000FFFF&}GTA VI\\N{\\c&H00FFFFFF&}CAMPAIGN!
"""
    ass_path.write_text(content, encoding="utf-8")


def benchmark_nvenc_fullscreen(input_video: Path, ass_path: Path, output_video: Path) -> None:
    """Render 1080x1920 Fullscreen 9:16 with burned-in .ass subtitles."""
    print("[2/4] Benchmarking NVENC Fullscreen 9:16 + .ass subtitle burn-in...")
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    vf = f"crop=ih*9/16:ih,scale=1080:1920,ass='{ass_escaped}'"
    elapsed = run_cmd([
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vf", vf,
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(output_video),
    ])
    print(f"      Fullscreen 9:16 done in {elapsed:.2f}s -> {output_video}")


def benchmark_nvenc_splitscreen(input_video: Path, ass_path: Path, output_video: Path) -> None:
    """Render 1080x1920 Split-Screen (facecam top 1080x640 + gameplay bottom 1080x1280)."""
    print("[3/4] Benchmarking NVENC Split-Screen + .ass subtitle burn-in...")
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    filter_complex = (
        "[0:v]crop=480:284:40:40,scale=1080:640[cam];"
        "[0:v]crop=911:1080:504:0,scale=1080:1280[game];"
        f"[cam][game]vstack=inputs=2,ass='{ass_escaped}'[out]"
    )
    elapsed = run_cmd([
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(output_video),
    ])
    print(f"      Split-Screen done in {elapsed:.2f}s -> {output_video}")


def benchmark_whisper(input_video: Path, model_size: str = "small") -> None:
    """Test faster-whisper on CUDA with int8_float16."""
    print(f"[4/4] Testing faster-whisper ({model_size}, cuda, int8_float16)...")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("      [SKIP] faster-whisper not installed in current Python environment.")
        return

    audio_wav = TEMP_DIR / "sample_16k.wav"
    run_cmd([
        "ffmpeg", "-y", "-i", str(input_video),
        "-vn", "-ac", "1", "-ar", "16000",
        str(audio_wav),
    ])

    t0 = time.perf_counter()
    model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
    load_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    segments, info = model.transcribe(str(audio_wav), word_timestamps=True)
    segs = list(segments)
    transcribe_time = time.perf_counter() - t1

    print(
        f"      Model loaded in {load_time:.2f}s | "
        f"Transcribed {info.duration:.1f}s audio in {transcribe_time:.2f}s "
        f"(lang={info.language}, segments={len(segs)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Clipper Tools Stage 2 PoC Benchmark")
    parser.add_argument("--input", type=Path, help="Optional local video path to test")
    parser.add_argument("--model", default="small", help="Whisper model size (default: small)")
    args = parser.parse_args()

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    input_video = args.input if args.input else (TEMP_DIR / "sample_1080p.mp4")

    if args.input and not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    if not args.input:
        ensure_sample_video(input_video, duration=10)

    ass_path = TEMP_DIR / "sample.ass"
    write_sample_ass(ass_path)

    benchmark_nvenc_fullscreen(input_video, ass_path, TEMP_DIR / "out_fullscreen.mp4")
    benchmark_nvenc_splitscreen(input_video, ass_path, TEMP_DIR / "out_splitscreen.mp4")
    benchmark_whisper(input_video, model_size=args.model)

    print("\nPoC benchmark completed successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
