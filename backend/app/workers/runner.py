"""Sequential background worker thread for GPU-heavy video tasks."""

import json
import queue
import threading
from pathlib import Path
from typing import Any

from backend.app.config import AUTO_CLEANUP_RAW, DEFAULT_SPLIT_CONFIG, GEMINI_API_KEY, JOBS_DIR
from backend.app.database import (
    create_clip,
    create_job,
    delete_clips_for_job,
    get_clip,
    get_job,
    list_clips_for_job,
    update_clip,
    update_job,
)
from backend.app.services.llm_scorer import enhance_highlights_with_llm, select_highlights_with_llm
from backend.app.services.pipeline import (
    extract_preview_frame,
    find_highlights,
    generate_clip_ass,
    prepare_media,
    render_clip_nvenc,
    transcribe_audio,
)


def cleanup_job_raw_files(job_id: str, jobs_dir: Path | None = None) -> int:
    """Delete raw video files and extracted WAV audio for a job; return bytes freed."""
    job = get_job(job_id)
    if not job:
        return 0
    base_dir = jobs_dir or JOBS_DIR
    job_dir = base_dir / job_id
    freed_bytes = 0
    targets: list[Path] = [job_dir / "audio_16k.wav"]
    if job.get("raw_video_path"):
        targets.append(Path(job["raw_video_path"]))
    for pattern in ("raw.mp4", "raw.mkv", "raw.mov", "raw.webm"):
        targets.append(job_dir / pattern)

    seen: set[Path] = set()
    for path in targets:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.exists() and path.is_file():
            freed_bytes += path.stat().st_size
            path.unlink()

    update_job(job_id, raw_video_path=None)
    return freed_bytes


_task_queue: queue.Queue[tuple[str, str]] = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def enqueue_job(job_id: str) -> None:
    """Queue a full download -> transcribe -> analyze job."""
    _task_queue.put(("process_job", job_id))


def enqueue_render_clip(clip_id: str) -> None:
    """Queue rendering for a single clip."""
    _task_queue.put(("render_clip", clip_id))


def _process_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    job_dir = JOBS_DIR / job_id
    try:
        update_job(job_id, status="downloading", progress=20, error_message=None)
        raw_mp4, audio_wav, preview_jpg, detected_title = prepare_media(
            job["source_type"],
            job["source_value"],
            job_dir,
        )
        final_title = job["title"]
        if not final_title or final_title in ("YouTube / Stream Clip Job", "New Clip Job"):
            final_title = detected_title

        update_job(
            job_id,
            title=final_title,
            raw_video_path=str(raw_mp4),
            preview_frame_path=str(preview_jpg),
            status="transcribing",
            progress=55,
        )

        transcript = transcribe_audio(
            audio_wav,
            job_dir,
            model_size=job["whisper_model"],
            language=job["language"],
        )

        update_job(job_id, status="analyzing", progress=85)
        highlights = select_highlights_with_llm(
            transcript,
            num_clips=int(job["num_clips"]),
            target_duration=float(job["target_duration"]),
            api_key=GEMINI_API_KEY,
        )
        if highlights is None:
            highlights = find_highlights(
                audio_wav,
                transcript,
                num_clips=int(job["num_clips"]),
                target_duration=float(job["target_duration"]),
            )
            highlights = enhance_highlights_with_llm(
                transcript,
                highlights,
                api_key=GEMINI_API_KEY,
            )

        delete_clips_for_job(job_id)
        for item in highlights:
            create_clip({
                "id": f"{job_id}_c{item['clip_index']:02d}",
                "job_id": job_id,
                "clip_index": item["clip_index"],
                "title": item["title"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "score": item["score"],
                "layout_mode": job["layout_mode"],
                "hook_text": item.get("hook_text", ""),
                "words": item["words"],
                "status": "draft",
            })

        update_job(job_id, status="ready", progress=100, error_message=None)
    except Exception as exc:
        update_job(job_id, status="failed", error_message=str(exc))


def _render_clip(clip_id: str) -> None:
    clip = get_clip(clip_id)
    if not clip:
        return
    job = get_job(clip["job_id"])
    if not job:
        return

    raw_path_str = job.get("raw_video_path")
    if not raw_path_str or not Path(raw_path_str).exists():
        update_clip(
            clip_id,
            status="failed",
            error_message="File video mentah (raw.mp4) tidak ditemukan atau sudah dihapus.",
        )
        return

    raw_mp4 = Path(raw_path_str)
    job_dir = JOBS_DIR / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    ass_path = job_dir / f"clip_{clip['clip_index']:02d}.ass"
    output_path = job_dir / f"clip_{clip['clip_index']:02d}_{clip['layout_mode']}.mp4"

    try:
        update_clip(clip_id, status="rendering", error_message=None)
        update_job(job["id"], status="rendering")

        generate_clip_ass(
            clip["words"],
            float(clip["start_time"]),
            float(clip["end_time"]),
            ass_path,
            hook_text=str(clip.get("hook_text") or ""),
        )
        render_clip_nvenc(
            raw_mp4=raw_mp4,
            ass_path=ass_path,
            output_path=output_path,
            start_time=float(clip["start_time"]),
            end_time=float(clip["end_time"]),
            layout_mode=clip["layout_mode"],
            split_config=job.get("split_config") or DEFAULT_SPLIT_CONFIG,
        )

        update_clip(clip_id, status="done", output_path=str(output_path), error_message=None)

        sibling_clips = list_clips_for_job(job["id"])
        if sibling_clips and all(c["status"] == "done" for c in sibling_clips):
            update_job(job["id"], status="completed", progress=100)
            if AUTO_CLEANUP_RAW:
                cleanup_job_raw_files(job["id"])
        elif any(c["status"] == "rendering" for c in sibling_clips):
            update_job(job["id"], status="rendering")
        else:
            update_job(job["id"], status="ready")
    except Exception as exc:
        update_clip(clip_id, status="failed", error_message=str(exc))
        update_job(job["id"], status="ready")


def _worker_loop() -> None:
    while True:
        task_type, target_id = _task_queue.get()
        try:
            if task_type == "process_job":
                _process_job(target_id)
            elif task_type == "render_clip":
                _render_clip(target_id)
        finally:
            _task_queue.task_done()


def start_worker() -> None:
    """Start the single sequential background worker thread once."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="clipper-gpu-worker", daemon=True)
        thread.start()
        _worker_started = True


def seed_existing_jobs() -> None:
    """Import existing Stage 2 GTA VI PoC job if present and not yet in SQLite."""
    poc_id = "diZ6-kQW_Ok"
    if get_job(poc_id) is not None:
        return

    job_dir = JOBS_DIR / poc_id
    raw_mp4 = job_dir / "raw.mp4"
    audio_wav = job_dir / "audio_16k.wav"
    transcript_path = job_dir / "transcript.json"
    if not (raw_mp4.exists() and audio_wav.exists() and transcript_path.exists()):
        return

    preview_jpg = job_dir / "preview.jpg"
    if not preview_jpg.exists():
        try:
            extract_preview_frame(raw_mp4, preview_jpg)
        except Exception:
            preview_jpg = None  # type: ignore[assignment]

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    highlights = find_highlights(audio_wav, transcript, num_clips=3, target_duration=35.0)

    create_job({
        "id": poc_id,
        "title": "GTA VI 100% AKU SIAP!",
        "source_type": "url",
        "source_value": f"https://youtu.be/{poc_id}",
        "language": transcript.get("language", "id"),
        "whisper_model": "small",
        "layout_mode": "fullscreen",
        "split_config": dict(DEFAULT_SPLIT_CONFIG),
        "target_duration": 35.0,
        "num_clips": 3,
        "status": "ready",
        "progress": 100,
        "raw_video_path": str(raw_mp4),
        "preview_frame_path": str(preview_jpg) if preview_jpg and preview_jpg.exists() else None,
    })

    for item in highlights:
        existing_mp4 = job_dir / f"clip_{item['clip_index']:02d}_fullscreen.mp4"
        create_clip({
            "id": f"{poc_id}_c{item['clip_index']:02d}",
            "job_id": poc_id,
            "clip_index": item["clip_index"],
            "title": item["title"],
            "start_time": item["start_time"],
            "end_time": item["end_time"],
            "score": item["score"],
            "layout_mode": "fullscreen",
            "words": item["words"],
            "status": "done" if existing_mp4.exists() else "draft",
            "output_path": str(existing_mp4) if existing_mp4.exists() else None,
        })
