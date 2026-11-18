"""SQLite database initialization and CRUD helpers."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.app.config import DB_PATH, DEFAULT_SPLIT_CONFIG, ensure_dirs
from backend.app.services.pipeline import normalize_video_url


def utc_now_iso() -> str:
    """Return current UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a WAL-enabled SQLite connection with Row factory."""
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create jobs and clips tables if they do not exist."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT,
                owner TEXT DEFAULT 'Tim',
                source_type TEXT,
                source_value TEXT,
                language TEXT,
                whisper_model TEXT,
                layout_mode TEXT,
                split_config_json TEXT,
                target_duration REAL,
                num_clips INTEGER,
                status TEXT,
                progress INTEGER,
                error_message TEXT,
                raw_video_path TEXT,
                preview_frame_path TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                clip_index INTEGER,
                title TEXT,
                start_time REAL,
                end_time REAL,
                score REAL,
                layout_mode TEXT,
                hook_text TEXT,
                published_platforms_json TEXT DEFAULT '[]',
                words_json TEXT,
                status TEXT,
                output_path TEXT,
                error_message TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                layout_mode TEXT NOT NULL,
                language TEXT NOT NULL,
                target_duration REAL NOT NULL,
                split_config_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        job_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "owner" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN owner TEXT DEFAULT 'Tim'")

        clip_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(clips)").fetchall()
        }
        if "hook_text" not in clip_cols:
            conn.execute("ALTER TABLE clips ADD COLUMN hook_text TEXT")
        if "published_platforms_json" not in clip_cols:
            conn.execute("ALTER TABLE clips ADD COLUMN published_platforms_json TEXT DEFAULT '[]'")


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["owner"] = data.get("owner") or "Tim"
    raw_split = data.pop("split_config_json", None)
    try:
        data["split_config"] = json.loads(raw_split) if raw_split else dict(DEFAULT_SPLIT_CONFIG)
    except json.JSONDecodeError:
        data["split_config"] = dict(DEFAULT_SPLIT_CONFIG)
    return data


def _row_to_clip(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["hook_text"] = data.get("hook_text") or ""
    raw_platforms = data.pop("published_platforms_json", None)
    try:
        parsed_platforms = json.loads(raw_platforms) if raw_platforms else []
        data["published_platforms"] = parsed_platforms if isinstance(parsed_platforms, list) else []
    except json.JSONDecodeError:
        data["published_platforms"] = []
    raw_words = data.pop("words_json", None)
    try:
        data["words"] = json.loads(raw_words) if raw_words else []
    except json.JSONDecodeError:
        data["words"] = []
    return data


def create_job(job_data: dict[str, Any]) -> dict[str, Any]:
    """Insert a new job row and return it."""
    now = utc_now_iso()
    split_cfg = job_data.get("split_config") or DEFAULT_SPLIT_CONFIG
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, title, owner, source_type, source_value, language, whisper_model,
                layout_mode, split_config_json, target_duration, num_clips,
                status, progress, error_message, raw_video_path, preview_frame_path,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_data["id"],
                job_data.get("title", "Untitled Job"),
                (job_data.get("owner") or "Tim").strip() or "Tim",
                job_data["source_type"],
                job_data["source_value"],
                job_data.get("language", "auto"),
                job_data.get("whisper_model", "small"),
                job_data.get("layout_mode", "fullscreen"),
                json.dumps(split_cfg),
                float(job_data.get("target_duration", 35.0)),
                int(job_data.get("num_clips", 3)),
                job_data.get("status", "queued"),
                int(job_data.get("progress", 0)),
                job_data.get("error_message"),
                job_data.get("raw_video_path"),
                job_data.get("preview_frame_path"),
                job_data.get("created_at", now),
                job_data.get("updated_at", now),
            ),
        )
    return get_job(job_data["id"])  # type: ignore[return-value]


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update selected columns on a job."""
    if not fields:
        return get_job(job_id)
    mapped: dict[str, Any] = {}
    for key, val in fields.items():
        if key == "split_config":
            mapped["split_config_json"] = json.dumps(val)
        else:
            mapped[key] = val
    mapped["updated_at"] = utc_now_iso()

    assignments = ", ".join(f"{col} = ?" for col in mapped)
    values = list(mapped.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
    return get_job(job_id)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch a single job by ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs() -> list[dict[str, Any]]:
    """Return all jobs ordered newest first, with clip counts."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.*,
                   (SELECT COUNT(*) FROM clips c WHERE c.job_id = j.id) AS clip_count,
                   (SELECT COUNT(*) FROM clips c WHERE c.job_id = j.id AND c.status = 'done') AS done_count
            FROM jobs j
            ORDER BY j.created_at DESC
            """
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def create_clip(clip_data: dict[str, Any]) -> dict[str, Any]:
    """Insert a new clip row and return it."""
    words = clip_data.get("words") or []
    published = clip_data.get("published_platforms") or []
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO clips (
                id, job_id, clip_index, title, start_time, end_time,
                score, layout_mode, hook_text, published_platforms_json,
                words_json, status, output_path, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_data["id"],
                clip_data["job_id"],
                int(clip_data["clip_index"]),
                clip_data.get("title", f"Clip #{clip_data['clip_index']}"),
                float(clip_data["start_time"]),
                float(clip_data["end_time"]),
                float(clip_data.get("score", 0.0)),
                clip_data.get("layout_mode", "fullscreen"),
                clip_data.get("hook_text", ""),
                json.dumps(published),
                json.dumps(words, ensure_ascii=False),
                clip_data.get("status", "draft"),
                clip_data.get("output_path"),
                clip_data.get("error_message"),
            ),
        )
    return get_clip(clip_data["id"])  # type: ignore[return-value]


def update_clip(clip_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update selected columns on a clip."""
    if not fields:
        return get_clip(clip_id)
    mapped: dict[str, Any] = {}
    for key, val in fields.items():
        if key == "words":
            mapped["words_json"] = json.dumps(val, ensure_ascii=False)
        elif key == "published_platforms":
            mapped["published_platforms_json"] = json.dumps(val)
        else:
            mapped[key] = val
    assignments = ", ".join(f"{col} = ?" for col in mapped)
    values = list(mapped.values()) + [clip_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE clips SET {assignments} WHERE id = ?", values)
    return get_clip(clip_id)


def get_clip(clip_id: str) -> dict[str, Any] | None:
    """Fetch a single clip by ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_clip(row) if row else None


def list_clips_for_job(job_id: str) -> list[dict[str, Any]]:
    """Fetch all clips belonging to a job ordered by clip_index."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY clip_index ASC",
            (job_id,),
        ).fetchall()
    return [_row_to_clip(r) for r in rows]


def delete_clips_for_job(job_id: str) -> None:
    """Delete existing clip records for a job (used before regenerating candidates)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))


def find_duplicate_jobs(url: str) -> list[dict[str, Any]]:
    """Return existing URL jobs matching the normalized URL."""
    target = normalize_video_url(url)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE source_type = 'url' ORDER BY created_at DESC"
        ).fetchall()
    return [
        _row_to_job(r)
        for r in rows
        if normalize_video_url(str(r["source_value"])) == target
    ]


def _row_to_preset(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw_split = data.pop("split_config_json", None)
    try:
        data["split_config"] = json.loads(raw_split) if raw_split else dict(DEFAULT_SPLIT_CONFIG)
    except json.JSONDecodeError:
        data["split_config"] = dict(DEFAULT_SPLIT_CONFIG)
    return data


def list_presets() -> list[dict[str, Any]]:
    """Return all saved streamer presets ordered by name."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY name COLLATE NOCASE ASC").fetchall()
    return [_row_to_preset(r) for r in rows]


def create_preset(data: dict[str, Any]) -> dict[str, Any]:
    """Insert a new streamer preset and return it."""
    now = utc_now_iso()
    split_cfg = data.get("split_config") or DEFAULT_SPLIT_CONFIG
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO presets (
                id, name, layout_mode, language, target_duration, split_config_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["name"].strip(),
                data.get("layout_mode", "splitscreen"),
                data.get("language", "auto"),
                float(data.get("target_duration", 35.0)),
                json.dumps(split_cfg),
                data.get("created_at", now),
            ),
        )
        row = conn.execute("SELECT * FROM presets WHERE id = ?", (data["id"],)).fetchone()
    assert row is not None
    return _row_to_preset(row)


def delete_preset(preset_id: str) -> bool:
    """Delete a streamer preset by ID; returns True if deleted."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    return cur.rowcount > 0


def get_system_metrics() -> dict[str, Any]:
    """Aggregate job, clip, platform distribution, and per-member team metrics."""
    active_statuses = {"queued", "downloading", "transcribing", "analyzing", "rendering"}
    platform_counts = {"tiktok": 0, "ig": 0, "yt": 0, "fb": 0}
    members: dict[str, dict[str, Any]] = {}

    with get_conn() as conn:
        job_rows = conn.execute("SELECT id, owner, status FROM jobs").fetchall()
        clip_rows = conn.execute(
            "SELECT job_id, status, published_platforms_json FROM clips"
        ).fetchall()

    job_owner_map: dict[str, str] = {}
    total_jobs = len(job_rows)
    completed_jobs = 0
    failed_jobs = 0
    active_jobs = 0

    for j in job_rows:
        owner = (j["owner"] or "Tim").strip() or "Tim"
        job_owner_map[j["id"]] = owner
        if owner not in members:
            members[owner] = {
                "owner": owner,
                "jobs": 0,
                "clips_rendered": 0,
                "clips_published": 0,
            }
        members[owner]["jobs"] += 1

        st = j["status"]
        if st == "completed":
            completed_jobs += 1
        elif st == "failed":
            failed_jobs += 1
        elif st in active_statuses:
            active_jobs += 1

    total_clips = len(clip_rows)
    rendered_clips = 0
    published_clips = 0

    for c in clip_rows:
        owner = job_owner_map.get(c["job_id"], "Tim")
        if owner not in members:
            members[owner] = {
                "owner": owner,
                "jobs": 0,
                "clips_rendered": 0,
                "clips_published": 0,
            }
        if c["status"] == "done":
            rendered_clips += 1
            members[owner]["clips_rendered"] += 1

        try:
            plats = json.loads(c["published_platforms_json"] or "[]")
        except json.JSONDecodeError:
            plats = []
        if isinstance(plats, list) and plats:
            published_clips += 1
            members[owner]["clips_published"] += 1
            for p in plats:
                if p in platform_counts:
                    platform_counts[p] += 1

    member_stats = sorted(
        members.values(),
        key=lambda m: (-m["clips_rendered"], -m["jobs"], m["owner"].lower()),
    )

    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "active_jobs": active_jobs,
        "total_clips": total_clips,
        "rendered_clips": rendered_clips,
        "published_clips": published_clips,
        "platform_counts": platform_counts,
        "member_stats": member_stats,
    }


def backup_database_to(dest_path: Path) -> Path:
    """Safely checkpoint and copy the live WAL SQLite database into dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    with get_conn() as src_conn:
        dst_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    return dest_path
