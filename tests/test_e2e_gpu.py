"""End-to-end GPU QA test (faster-whisper CUDA + FFmpeg h264_nvenc Split-Screen)."""

import shutil
from pathlib import Path

import pytest

from backend.app.services.pipeline import (
    find_highlights,
    generate_clip_ass,
    prepare_media,
    probe_video_dimensions,
    render_clip_nvenc,
    transcribe_audio,
)


SAMPLE_MP4 = Path("/home/angger/Clipper-tools/storage/poc/sample_1080p.mp4")


@pytest.mark.skipif(not SAMPLE_MP4.exists(), reason="PoC sample_1080p.mp4 not available")
def test_e2e_gpu_splitscreen_pipeline(tmp_path: Path) -> None:
    job_dir = tmp_path / "e2e_job"
    job_dir.mkdir(parents=True, exist_ok=True)
    local_raw = job_dir / "raw.mp4"
    shutil.copy2(SAMPLE_MP4, local_raw)

    raw_mp4, audio_wav, preview_jpg, _ = prepare_media("local_file", str(local_raw), job_dir)
    assert audio_wav.exists() and audio_wav.stat().st_size > 0
    assert preview_jpg.exists() and preview_jpg.stat().st_size > 0

    transcript = transcribe_audio(audio_wav, job_dir, model_size="small", language="auto")
    assert transcript["duration"] > 5.0

    clips = find_highlights(audio_wav, transcript, num_clips=1, target_duration=8.0)
    assert len(clips) == 1
    clip = clips[0]

    ass_path = job_dir / "clip_01.ass"
    generate_clip_ass(clip["words"], clip["start_time"], clip["end_time"], ass_path)
    assert ass_path.exists()

    out_mp4 = job_dir / "clip_01_splitscreen.mp4"
    render_clip_nvenc(
        raw_mp4=raw_mp4,
        ass_path=ass_path,
        output_path=out_mp4,
        start_time=clip["start_time"],
        end_time=clip["end_time"],
        layout_mode="splitscreen",
    )
    assert out_mp4.exists() and out_mp4.stat().st_size > 0

    width, height, duration = probe_video_dimensions(out_mp4)
    assert (width, height) == (1080, 1920)
    assert duration > 5.0
