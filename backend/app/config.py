"""Configuration paths, environment settings, and defaults for Clipper Tools."""

import os
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(env_path: Path) -> None:
    """Load KEY=VALUE pairs from .env into os.environ without overwriting existing vars."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(ROOT_DIR / ".env")

def _resolve_ytdlp_bin() -> str:
    """Resolve yt-dlp binary across Conda, Docker, and system PATH environments."""
    override = os.getenv("CLIPPER_YTDLP_BIN", "").strip()
    if override:
        return override
    sibling = Path(sys.executable).resolve().parent / "yt-dlp"
    if sibling.exists():
        return str(sibling)
    on_path = shutil.which("yt-dlp")
    return on_path or "yt-dlp"


STORAGE_DIR = ROOT_DIR / "storage"
JOBS_DIR = STORAGE_DIR / "jobs"
DB_PATH = STORAGE_DIR / "clipper.db"
STATIC_DIR = ROOT_DIR / "backend" / "static"
YTDLP_BIN = _resolve_ytdlp_bin()

AUTO_CLEANUP_RAW: bool = os.getenv("CLIPPER_AUTO_CLEANUP_RAW", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
MAX_UPLOAD_MB: int = max(1, int(os.getenv("CLIPPER_MAX_UPLOAD_MB", "2048") or "2048"))
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

DEFAULT_SPLIT_CONFIG: dict[str, int] = {
    "cam_x": 40,
    "cam_y": 40,
    "cam_w": 480,
    "cam_h": 284,
    "game_x": 504,
    "game_y": 0,
    "game_w": 911,
    "game_h": 1080,
}


def ensure_dirs() -> None:
    """Create required storage and static directories if missing."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
