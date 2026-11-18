"""FastAPI application entrypoint and REST endpoints for Clipper Tools."""

import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.config import (
    DEFAULT_SPLIT_CONFIG,
    GEMINI_API_KEY,
    JOBS_DIR,
    MAX_UPLOAD_MB,
    STATIC_DIR,
    STORAGE_DIR,
    YTDLP_BIN,
    ensure_dirs,
)
from backend.app.database import (
    backup_database_to,
    create_clip,
    create_job,
    create_preset,
    delete_clips_for_job,
    delete_preset,
    find_duplicate_jobs,
    get_clip,
    get_job,
    get_system_metrics,
    init_db,
    list_clips_for_job,
    list_jobs,
    list_presets,
    update_clip,
    update_job,
)
from backend.app.services.llm_scorer import enhance_highlights_with_llm, select_highlights_with_llm
from backend.app.services.pipeline import (
    detect_compute_capabilities,
    find_highlights,
    slice_words_for_window,
)
from backend.app.workers.runner import (
    cleanup_job_raw_files,
    enqueue_job,
    enqueue_render_clip,
    seed_existing_jobs,
    start_worker,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize directories, SQLite schema, seed data, and GPU worker."""
    ensure_dirs()
    init_db()
    seed_existing_jobs()
    start_worker()
    yield


app = FastAPI(title="Clipper Tools API", version="1.1.0", lifespan=lifespan)


class UrlJobCreate(BaseModel):
    url: str = Field(..., min_length=5)
    owner: str = "Tim"
    force_duplicate: bool = False
    language: Literal["auto", "id", "en"] = "auto"
    whisper_model: Literal["tiny", "base", "small", "medium"] = "small"
    layout_mode: Literal["fullscreen", "splitscreen"] = "fullscreen"
    target_duration: float = Field(default=35.0, ge=10.0, le=120.0)
    num_clips: int = Field(default=3, ge=1, le=15)


class SplitConfigModel(BaseModel):
    cam_x: int = Field(ge=0)
    cam_y: int = Field(ge=0)
    cam_w: int = Field(ge=16)
    cam_h: int = Field(ge=16)
    game_x: int = Field(ge=0)
    game_y: int = Field(ge=0)
    game_w: int = Field(ge=16)
    game_h: int = Field(ge=16)


class JobLayoutUpdate(BaseModel):
    layout_mode: Literal["fullscreen", "splitscreen"] | None = None
    split_config: SplitConfigModel | None = None


class WordToken(BaseModel):
    word: str
    start: float
    end: float


class ClipUpdate(BaseModel):
    title: str | None = None
    start_time: float | None = Field(default=None, ge=0.0)
    end_time: float | None = Field(default=None, ge=0.1)
    layout_mode: Literal["fullscreen", "splitscreen"] | None = None
    hook_text: str | None = None
    published_platforms: list[Literal["tiktok", "ig", "yt", "fb"]] | None = None
    words: list[WordToken] | None = None
    reslice_from_transcript: bool = False


class PresetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    layout_mode: Literal["fullscreen", "splitscreen"] = "splitscreen"
    language: Literal["auto", "id", "en"] = "auto"
    target_duration: float = Field(default=35.0, ge=10.0, le=120.0)
    split_config: SplitConfigModel


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
    return cleaned or "clip"


def _attach_job_flags(job: dict[str, Any]) -> dict[str, Any]:
    raw_path = job.get("raw_video_path")
    job["raw_exists"] = bool(raw_path and Path(raw_path).exists())
    preview_path = job.get("preview_frame_path")
    job["has_preview"] = bool(preview_path and Path(preview_path).exists())
    return job


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness and detected hardware/engine capability check."""
    caps = {k: v for k, v in detect_compute_capabilities().items() if k != "encoder_args"}
    return {
        "status": "ok",
        "version": "1.1.0",
        **caps,
        "llm_enabled": bool(GEMINI_API_KEY),
    }


@app.get("/api/jobs")
def api_list_jobs() -> list[dict[str, Any]]:
    """List all jobs ordered newest first."""
    return [_attach_job_flags(j) for j in list_jobs()]


@app.post("/api/jobs/url")
def api_create_url_job(payload: UrlJobCreate) -> dict[str, Any]:
    """Create and enqueue a new clipping job from a video URL."""
    url = payload.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL harus diawali http:// atau https://")

    if not payload.force_duplicate:
        dups = find_duplicate_jobs(url)
        if dups:
            first = dups[0]
            raise HTTPException(
                status_code=409,
                detail=f"Link video ini sudah pernah di-clip oleh {first.get('owner', 'Tim')} ({first.get('title', first['id'])})",
            )

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job({
        "id": job_id,
        "title": "New Clip Job",
        "owner": payload.owner.strip() or "Tim",
        "source_type": "url",
        "source_value": url,
        "language": payload.language,
        "whisper_model": payload.whisper_model,
        "layout_mode": payload.layout_mode,
        "split_config": dict(DEFAULT_SPLIT_CONFIG),
        "target_duration": payload.target_duration,
        "num_clips": payload.num_clips,
        "status": "queued",
        "progress": 5,
    })
    enqueue_job(job_id)
    return _attach_job_flags(job)


@app.post("/api/jobs/upload")
def api_create_upload_job(
    file: UploadFile = File(...),
    owner: str = Form("Tim"),
    language: str = Form("auto"),
    whisper_model: str = Form("small"),
    layout_mode: str = Form("fullscreen"),
    target_duration: float = Form(35.0),
    num_clips: int = Form(3),
) -> dict[str, Any]:
    """Upload a local video file and enqueue a new clipping job."""
    if language not in {"auto", "id", "en"}:
        raise HTTPException(status_code=400, detail="Bahasa tidak valid")
    if whisper_model not in {"tiny", "base", "small", "medium"}:
        raise HTTPException(status_code=400, detail="Model Whisper tidak valid")
    if layout_mode not in {"fullscreen", "splitscreen"}:
        raise HTTPException(status_code=400, detail="Layout mode tidak valid")

    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4", ".mkv", ".mov", ".webm"}:
        raise HTTPException(status_code=400, detail="Format video harus .mp4, .mkv, .mov, atau .webm")

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest_path = job_dir / f"raw{ext}"

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    written_bytes = 0
    try:
        with dest_path.open("wb") as out_f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written_bytes += len(chunk)
                if written_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Ukuran file melebihi batas maksimal {MAX_UPLOAD_MB} MB",
                    )
                out_f.write(chunk)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    title = Path(file.filename or "Local Video").stem
    job = create_job({
        "id": job_id,
        "title": title,
        "owner": owner.strip() or "Tim",
        "source_type": "local_file",
        "source_value": str(dest_path),
        "language": language,
        "whisper_model": whisper_model,
        "layout_mode": layout_mode,
        "split_config": dict(DEFAULT_SPLIT_CONFIG),
        "target_duration": max(10.0, min(120.0, float(target_duration))),
        "num_clips": max(1, min(15, int(num_clips))),
        "status": "queued",
        "progress": 5,
        "raw_video_path": str(dest_path),
    })
    enqueue_job(job_id)
    return _attach_job_flags(job)


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    """Fetch job details along with its candidate clips."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    clips = list_clips_for_job(job_id)
    for clip in clips:
        out_path = clip.get("output_path")
        clip["output_exists"] = bool(out_path and Path(out_path).exists())
    return {
        **_attach_job_flags(job),
        "clips": clips,
    }


@app.post("/api/jobs/{job_id}/reanalyze")
def api_reanalyze_job(job_id: str) -> dict[str, Any]:
    """Re-run 4-signal (and optional LLM) highlight detection from saved transcript.json."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    job_dir = JOBS_DIR / job_id
    transcript_path = job_dir / "transcript.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Transkrip job belum tersedia")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    audio_wav = job_dir / "audio_16k.wav"

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

    existing = list_clips_for_job(job_id)
    if len(existing) == len(highlights):
        for old_clip, item in zip(existing, highlights):
            unchanged = (
                abs(float(old_clip["start_time"]) - float(item["start_time"])) < 0.01
                and abs(float(old_clip["end_time"]) - float(item["end_time"])) < 0.01
            )
            updates: dict[str, Any] = {
                "title": item["title"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "score": item["score"],
                "words": item["words"],
            }
            if item.get("hook_text"):
                updates["hook_text"] = item["hook_text"]
            if not unchanged:
                updates["status"] = "draft"
                updates["output_path"] = None
            update_clip(old_clip["id"], **updates)
    else:
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

    return api_get_job(job_id)


@app.patch("/api/jobs/{job_id}/layout")
def api_update_job_layout(job_id: str, payload: JobLayoutUpdate) -> dict[str, Any]:
    """Update job-level default layout and Split-Screen coordinates."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    updates: dict[str, Any] = {}
    if payload.layout_mode is not None:
        updates["layout_mode"] = payload.layout_mode
    if payload.split_config is not None:
        updates["split_config"] = payload.split_config.model_dump()

    updated = update_job(job_id, **updates)
    return _attach_job_flags(updated or job)


@app.get("/api/jobs/{job_id}/frame")
def api_get_preview_frame(job_id: str) -> FileResponse:
    """Serve the extracted preview JPEG frame for Split-Screen box placement."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    frame_path = job.get("preview_frame_path")
    if not frame_path or not Path(frame_path).exists():
        raise HTTPException(status_code=404, detail="Preview frame belum tersedia")
    return FileResponse(frame_path, media_type="image/jpeg")


@app.patch("/api/clips/{clip_id}")
def api_update_clip(clip_id: str, payload: ClipUpdate) -> dict[str, Any]:
    """Save edited clip timestamps, layout, title, or subtitle words."""
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip tidak ditemukan")

    new_start = payload.start_time if payload.start_time is not None else float(clip["start_time"])
    new_end = payload.end_time if payload.end_time is not None else float(clip["end_time"])
    if new_end <= new_start + 1.0:
        raise HTTPException(status_code=400, detail="End time harus lebih besar minimal 1 detik dari Start time")

    updates: dict[str, Any] = {
        "start_time": round(new_start, 3),
        "end_time": round(new_end, 3),
    }
    if payload.title is not None:
        updates["title"] = payload.title.strip() or clip["title"]
    if payload.layout_mode is not None:
        updates["layout_mode"] = payload.layout_mode
    if payload.hook_text is not None:
        updates["hook_text"] = payload.hook_text.strip()
    if payload.published_platforms is not None:
        updates["published_platforms"] = list(dict.fromkeys(payload.published_platforms))

    if payload.reslice_from_transcript:
        transcript_path = JOBS_DIR / clip["job_id"] / "transcript.json"
        if not transcript_path.exists():
            raise HTTPException(status_code=404, detail="Transcript job tidak ditemukan")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        updates["words"] = slice_words_for_window(transcript.get("words", []), new_start, new_end)
    elif payload.words is not None:
        updates["words"] = [
            {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
            for w in payload.words
            if w.word.strip()
        ]

    updated = update_clip(clip_id, **updates)
    assert updated is not None
    out_path = updated.get("output_path")
    updated["output_exists"] = bool(out_path and Path(out_path).exists())
    return updated


@app.post("/api/clips/{clip_id}/render")
def api_render_clip(clip_id: str) -> dict[str, Any]:
    """Queue a single clip for NVENC rendering."""
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip tidak ditemukan")
    job = get_job(clip["job_id"])
    if not job or not job.get("raw_video_path") or not Path(job["raw_video_path"]).exists():
        raise HTTPException(status_code=400, detail="File video mentah sudah dihapus atau tidak ada")

    updated = update_clip(clip_id, status="queued", error_message=None)
    enqueue_render_clip(clip_id)
    return updated or clip


@app.post("/api/jobs/{job_id}/render-all")
def api_render_all_clips(job_id: str) -> dict[str, Any]:
    """Queue all clips in a job for NVENC rendering."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    if not job.get("raw_video_path") or not Path(job["raw_video_path"]).exists():
        raise HTTPException(status_code=400, detail="File video mentah sudah dihapus atau tidak ada")

    clips = list_clips_for_job(job_id)
    if not clips:
        raise HTTPException(status_code=400, detail="Belum ada kandidat klip untuk di-render")

    for clip in clips:
        update_clip(clip["id"], status="queued", error_message=None)
        enqueue_render_clip(clip["id"])

    update_job(job_id, status="rendering")
    return {"queued": len(clips), "job_id": job_id}


@app.get("/api/clips/{clip_id}/video")
def api_stream_clip_video(clip_id: str) -> FileResponse:
    """Serve rendered MP4 video for in-browser preview."""
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip tidak ditemukan")
    out_path = clip.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise HTTPException(status_code=404, detail="File hasil render belum tersedia")
    return FileResponse(out_path, media_type="video/mp4")


@app.get("/api/clips/{clip_id}/download")
def api_download_clip_video(clip_id: str) -> FileResponse:
    """Download rendered MP4 file as an attachment."""
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip tidak ditemukan")
    out_path = clip.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise HTTPException(status_code=404, detail="File hasil render belum tersedia")
    dl_name = f"{_sanitize_filename(clip['title'])}_{clip['layout_mode']}.mp4"
    return FileResponse(out_path, media_type="video/mp4", filename=dl_name)


@app.get("/api/jobs/{job_id}/download-zip")
def api_download_job_zip(job_id: str) -> FileResponse:
    """Bundle all rendered clips of a job into a single .zip download."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    rendered: list[tuple[dict[str, Any], Path]] = []
    for clip in list_clips_for_job(job_id):
        out_path = clip.get("output_path")
        if out_path and Path(out_path).exists():
            rendered.append((clip, Path(out_path)))

    if not rendered:
        raise HTTPException(status_code=400, detail="Belum ada klip yang selesai di-render untuk diunduh")

    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"{_sanitize_filename(job['title'])}_clips.zip"
    zip_path = job_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for clip, file_path in rendered:
            arcname = f"clip_{int(clip['clip_index']):02d}_{_sanitize_filename(clip['title'])}_{clip['layout_mode']}.mp4"
            zf.write(file_path, arcname=arcname)

    return FileResponse(zip_path, media_type="application/zip", filename=zip_name)


@app.get("/api/presets")
def api_list_presets() -> list[dict[str, Any]]:
    """List all saved streamer presets."""
    return list_presets()


@app.post("/api/presets")
def api_create_preset(payload: PresetCreate) -> dict[str, Any]:
    """Save a reusable streamer layout & split-screen preset."""
    preset_id = f"preset_{uuid.uuid4().hex[:8]}"
    return create_preset({
        "id": preset_id,
        "name": payload.name.strip(),
        "layout_mode": payload.layout_mode,
        "language": payload.language,
        "target_duration": payload.target_duration,
        "split_config": payload.split_config.model_dump(),
    })


@app.delete("/api/presets/{preset_id}")
def api_delete_preset(preset_id: str) -> dict[str, bool]:
    """Delete a saved streamer preset."""
    if not delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset tidak ditemukan")
    return {"deleted": True}


@app.delete("/api/jobs/{job_id}/raw")
def api_delete_raw_files(job_id: str) -> dict[str, Any]:
    """Delete raw video and extracted WAV audio to reclaim SSD space."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    freed_bytes = cleanup_job_raw_files(job_id, jobs_dir=JOBS_DIR)
    return {
        "job_id": job_id,
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
    }


@app.get("/api/storage")
def api_get_storage_stats() -> dict[str, Any]:
    """Report disk usage for raw files vs rendered clips and free SSD space."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    raw_bytes = 0
    clips_bytes = 0
    for path in JOBS_DIR.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name == "audio_16k.wav" or name.startswith("raw."):
            raw_bytes += path.stat().st_size
        elif name.startswith("clip_") and name.endswith(".mp4"):
            clips_bytes += path.stat().st_size

    usage = shutil.disk_usage(JOBS_DIR)
    return {
        "raw_mb": round(raw_bytes / (1024 * 1024), 2),
        "clips_mb": round(clips_bytes / (1024 * 1024), 2),
        "free_disk_gb": round(usage.free / (1024**3), 2),
    }


@app.post("/api/storage/cleanup")
def api_cleanup_completed_raw() -> dict[str, Any]:
    """Delete raw video & WAV audio for all completed jobs."""
    freed_bytes = 0
    jobs_cleaned = 0
    for job in list_jobs():
        clips = list_clips_for_job(job["id"])
        is_completed = job["status"] == "completed" or (
            bool(clips) and all(c["status"] == "done" for c in clips)
        )
        if not is_completed:
            continue
        freed = cleanup_job_raw_files(job["id"], jobs_dir=JOBS_DIR)
        if freed > 0:
            freed_bytes += freed
            jobs_cleaned += 1

    return {
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "jobs_cleaned": jobs_cleaned,
    }


def _get_ytdlp_version() -> str:
    try:
        proc = subprocess.run(
            [YTDLP_BIN, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[-1].strip()
    except Exception:
        pass
    return "unknown"


@app.get("/api/metrics")
def api_get_metrics() -> dict[str, Any]:
    """Return live team productivity, platform upload distribution, and yt-dlp version."""
    return {
        **get_system_metrics(),
        "ytdlp_version": _get_ytdlp_version(),
    }


@app.post("/api/maintenance/update-ytdlp")
def api_update_ytdlp() -> dict[str, Any]:
    """Upgrade yt-dlp in the active Python environment."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Gagal update yt-dlp: {proc.stderr.strip() or 'unknown error'}",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal update yt-dlp: {exc}") from exc

    return {
        "status": "ok",
        "ytdlp_version": _get_ytdlp_version(),
    }


@app.get("/api/maintenance/backup-db")
def api_backup_db() -> FileResponse:
    """Create a consistent WAL-checkpointed SQLite backup and return as download."""
    backup_path = backup_database_to(STORAGE_DIR / "clipper_backup.db")
    return FileResponse(
        backup_path,
        media_type="application/octet-stream",
        filename="clipper_backup.db",
    )


ensure_dirs()
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
