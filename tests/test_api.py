"""API and edge-case integration tests using isolated temporary SQLite & storage."""

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.config as app_config
import backend.app.database as app_db
import backend.app.main as app_main


@pytest.fixture()
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    jobs_dir = storage_dir / "jobs"
    db_path = storage_dir / "clipper_test.db"

    monkeypatch.setattr(app_config, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(app_config, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(app_config, "DB_PATH", db_path)
    monkeypatch.setattr(app_db, "DB_PATH", db_path)
    monkeypatch.setattr(app_main, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(app_main, "JOBS_DIR", jobs_dir)

    queued_jobs: list[str] = []
    queued_renders: list[str] = []
    monkeypatch.setattr(app_main, "seed_existing_jobs", lambda: None)
    monkeypatch.setattr(app_main, "start_worker", lambda: None)
    monkeypatch.setattr(app_main, "enqueue_job", queued_jobs.append)
    monkeypatch.setattr(app_main, "enqueue_render_clip", queued_renders.append)

    with TestClient(app_main.app) as client:
        yield client, jobs_dir, queued_jobs, queued_renders


def test_health_and_dashboard(isolated_client) -> None:
    client, _, _, _ = isolated_client
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    health_data = r_health.json()
    assert health_data["status"] == "ok"
    assert health_data["version"] == "1.1.0"
    assert health_data["whisper_device"] in {"cuda", "cpu"}
    assert health_data["video_encoder"] in {"h264_nvenc", "libx264"}
    assert "mode" in health_data
    assert "cpu_name" in health_data and health_data["cpu_cores"] >= 1
    assert "ram_total_gb" in health_data
    assert "gpu_name" in health_data and "gpu_vram_mb" in health_data
    assert isinstance(health_data["llm_enabled"], bool)

    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "Clipper Tools" in r_root.text
    assert "v1.1" in r_root.text


def test_url_job_validation(isolated_client) -> None:
    client, _, queued_jobs, _ = isolated_client
    bad = client.post("/api/jobs/url", json={"url": "ftp://bad"})
    assert bad.status_code == 400

    good = client.post(
        "/api/jobs/url",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "num_clips": 2, "target_duration": 30},
    )
    assert good.status_code == 200
    body = good.json()
    assert body["status"] == "queued"
    assert body["id"] in queued_jobs


def test_upload_job_validation(isolated_client) -> None:
    client, _, queued_jobs, _ = isolated_client
    bad = client.post(
        "/api/jobs/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad.status_code == 400

    good = client.post(
        "/api/jobs/upload",
        files={"file": ("sample.mp4", b"fake-mp4-bytes", "video/mp4")},
        data={"language": "id", "whisper_model": "small", "layout_mode": "fullscreen"},
    )
    assert good.status_code == 200
    body = good.json()
    assert body["source_type"] == "local_file"
    assert body["id"] in queued_jobs


def _seed_job_and_clip(jobs_dir: Path) -> tuple[str, str]:
    job_id = "job_test01"
    clip_id = "job_test01_c01"
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4 = job_dir / "raw.mp4"
    wav_file = job_dir / "audio_16k.wav"
    raw_mp4.write_bytes(b"fake-video-data")
    wav_file.write_bytes(b"fake-wav-data")

    transcript = {
        "language": "id",
        "duration": 40.0,
        "segments": [],
        "words": [
            {"word": "satu", "start": 2.0, "end": 2.5},
            {"word": "dua", "start": 6.0, "end": 6.5},
            {"word": "tiga", "start": 10.0, "end": 10.5},
            {"word": "empat", "start": 18.0, "end": 18.5},
        ],
    }
    (job_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    app_db.create_job({
        "id": job_id,
        "title": "Test Job",
        "source_type": "local_file",
        "source_value": str(raw_mp4),
        "language": "id",
        "whisper_model": "small",
        "layout_mode": "fullscreen",
        "status": "ready",
        "progress": 100,
        "raw_video_path": str(raw_mp4),
    })
    app_db.create_clip({
        "id": clip_id,
        "job_id": job_id,
        "clip_index": 1,
        "title": "Clip #1",
        "start_time": 1.0,
        "end_time": 20.0,
        "score": 0.8,
        "layout_mode": "fullscreen",
        "words": transcript["words"],
        "status": "draft",
    })
    return job_id, clip_id


def test_clip_update_reslice_and_validation(isolated_client) -> None:
    client, jobs_dir, _, _ = isolated_client
    _, clip_id = _seed_job_and_clip(jobs_dir)

    bad_time = client.patch(
        f"/api/clips/{clip_id}",
        json={"start_time": 20.0, "end_time": 20.5},
    )
    assert bad_time.status_code == 400

    edited = client.patch(
        f"/api/clips/{clip_id}",
        json={
            "words": [
                {"word": "Lucia", "start": 2.0, "end": 2.5},
                {"word": "Caminos", "start": 2.6, "end": 3.0},
            ]
        },
    )
    assert edited.status_code == 200
    assert [w["word"] for w in edited.json()["words"]] == ["Lucia", "Caminos"]

    resliced = client.patch(
        f"/api/clips/{clip_id}",
        json={"start_time": 5.0, "end_time": 15.0, "reslice_from_transcript": True},
    )
    assert resliced.status_code == 200
    assert [w["word"] for w in resliced.json()["words"]] == ["dua", "tiga"]


def test_split_layout_and_raw_cleanup(isolated_client) -> None:
    client, jobs_dir, _, _ = isolated_client
    job_id, clip_id = _seed_job_and_clip(jobs_dir)

    split_cfg = {
        "cam_x": 100, "cam_y": 50, "cam_w": 400, "cam_h": 300,
        "game_x": 500, "game_y": 0, "game_w": 900, "game_h": 1080,
    }
    r_layout = client.patch(
        f"/api/jobs/{job_id}/layout",
        json={"layout_mode": "splitscreen", "split_config": split_cfg},
    )
    assert r_layout.status_code == 200
    assert r_layout.json()["layout_mode"] == "splitscreen"
    assert r_layout.json()["split_config"] == split_cfg

    r_del = client.delete(f"/api/jobs/{job_id}/raw")
    assert r_del.status_code == 200
    assert r_del.json()["freed_bytes"] > 0

    r_job = client.get(f"/api/jobs/{job_id}")
    assert r_job.status_code == 200
    assert r_job.json()["raw_exists"] is False

    r_render = client.post(f"/api/clips/{clip_id}/render")
    assert r_render.status_code == 400


def test_storage_monitor_cleanup_and_hook_text(isolated_client) -> None:
    client, jobs_dir, _, _ = isolated_client
    job_id, clip_id = _seed_job_and_clip(jobs_dir)

    r_hook = client.patch(
        f"/api/clips/{clip_id}",
        json={"hook_text": "GTA VI 100% TAMAT"},
    )
    assert r_hook.status_code == 200
    assert r_hook.json()["hook_text"] == "GTA VI 100% TAMAT"

    out_clip = jobs_dir / job_id / "clip_01_fullscreen.mp4"
    out_clip.write_bytes(b"0" * (1024 * 64))
    app_db.update_clip(clip_id, status="done", output_path=str(out_clip))
    app_db.update_job(job_id, status="completed")

    r_stats = client.get("/api/storage")
    assert r_stats.status_code == 200
    stats = r_stats.json()
    assert "raw_mb" in stats and "clips_mb" in stats and stats["free_disk_gb"] > 0

    r_cleanup = client.post("/api/storage/cleanup")
    assert r_cleanup.status_code == 200
    cleaned = r_cleanup.json()
    assert cleaned["jobs_cleaned"] == 1
    assert cleaned["freed_bytes"] > 0
    assert out_clip.exists()


def test_duplicate_url_detection_and_owner(isolated_client) -> None:
    client, _, queued_jobs, _ = isolated_client
    first = client.post(
        "/api/jobs/url",
        json={"url": "https://youtu.be/diZ6-kQW_Ok?si=foo", "owner": "Budi"},
    )
    assert first.status_code == 200
    assert first.json()["owner"] == "Budi"

    dup = client.post(
        "/api/jobs/url",
        json={"url": "https://www.youtube.com/watch?v=diZ6-kQW_Ok", "owner": "Sari"},
    )
    assert dup.status_code == 409
    assert "Budi" in dup.json()["detail"]

    forced = client.post(
        "/api/jobs/url",
        json={
            "url": "https://www.youtube.com/watch?v=diZ6-kQW_Ok",
            "owner": "Sari",
            "force_duplicate": True,
        },
    )
    assert forced.status_code == 200
    assert forced.json()["owner"] == "Sari"
    assert len(queued_jobs) == 2


def test_clip_published_platforms_and_zip_download(isolated_client) -> None:
    client, jobs_dir, _, _ = isolated_client
    job_id, clip_id = _seed_job_and_clip(jobs_dir)

    r_pub = client.patch(
        f"/api/clips/{clip_id}",
        json={"published_platforms": ["tiktok", "yt", "tiktok"]},
    )
    assert r_pub.status_code == 200
    assert r_pub.json()["published_platforms"] == ["tiktok", "yt"]

    r_empty_zip = client.get(f"/api/jobs/{job_id}/download-zip")
    assert r_empty_zip.status_code == 400

    out_clip = jobs_dir / job_id / "clip_01_fullscreen.mp4"
    out_clip.write_bytes(b"fake-mp4-content")
    app_db.update_clip(clip_id, status="done", output_path=str(out_clip))

    r_zip = client.get(f"/api/jobs/{job_id}/download-zip")
    assert r_zip.status_code == 200
    assert "application/zip" in r_zip.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(r_zip.content), "r") as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert zf.read(names[0]) == b"fake-mp4-content"


def test_streamer_presets_crud(isolated_client) -> None:
    client, _, _, _ = isolated_client
    split_cfg = {
        "cam_x": 20, "cam_y": 20, "cam_w": 400, "cam_h": 240,
        "game_x": 450, "game_y": 0, "game_w": 1000, "game_h": 1080,
    }
    created = client.post(
        "/api/presets",
        json={
            "name": "Windah Basudara",
            "layout_mode": "splitscreen",
            "language": "id",
            "target_duration": 40.0,
            "split_config": split_cfg,
        },
    )
    assert created.status_code == 200
    preset = created.json()
    assert preset["name"] == "Windah Basudara"
    assert preset["split_config"] == split_cfg

    listed = client.get("/api/presets")
    assert listed.status_code == 200
    assert any(p["id"] == preset["id"] for p in listed.json())

    deleted = client.delete(f"/api/presets/{preset['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    listed_after = client.get("/api/presets")
    assert all(p["id"] != preset["id"] for p in listed_after.json())


def test_reanalyze_job_endpoint(isolated_client) -> None:
    client, jobs_dir, _, _ = isolated_client
    job_id, _ = _seed_job_and_clip(jobs_dir)
    # Remove raw audio to ensure transcript-only reanalyze works cleanly too
    (jobs_dir / job_id / "audio_16k.wav").unlink()

    res = client.post(f"/api/jobs/{job_id}/reanalyze")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == job_id
    assert len(body["clips"]) >= 1


def test_stage10_metrics_backup_and_update_ytdlp(isolated_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, jobs_dir, _, _ = isolated_client
    _, clip_id = _seed_job_and_clip(jobs_dir)
    app_db.update_clip(clip_id, status="done", published_platforms=["tiktok", "yt"])

    r_metrics = client.get("/api/metrics")
    assert r_metrics.status_code == 200
    metrics = r_metrics.json()
    assert metrics["total_jobs"] == 1
    assert metrics["rendered_clips"] == 1
    assert metrics["published_clips"] == 1
    assert metrics["platform_counts"]["tiktok"] == 1
    assert metrics["platform_counts"]["yt"] == 1
    assert len(metrics["member_stats"]) == 1
    assert "ytdlp_version" in metrics

    r_backup = client.get("/api/maintenance/backup-db")
    assert r_backup.status_code == 200
    assert r_backup.content.startswith(b"SQLite format 3\x00")

    monkeypatch.setattr(
        app_main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="2026.11.18\n", stderr=""),
    )
    r_upd = client.post("/api/maintenance/update-ytdlp")
    assert r_upd.status_code == 200
    assert r_upd.json()["status"] == "ok"
    assert r_upd.json()["ytdlp_version"] == "2026.11.18"
