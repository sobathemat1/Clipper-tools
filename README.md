# Clipper Tools

Aplikasi web lokal untuk mengotomasi pemotongan video panjang (gameplay, livestream, VOD YouTube/Twitch/Kick, atau file lokal) menjadi klip vertikal `9:16` (`1080x1920`) dengan subtitle dinamis dan opsional *hook banner* atas, siap upload ke **TikTok, Instagram Reels, Facebook Reels, dan YouTube Shorts**.

## Kebutuhan Sistem
- **OS:** Linux (diuji di Ubuntu 24.04) atau Windows 10/11
- **Hardware:** Mendukung **GPU NVIDIA (CUDA + NVENC)** untuk proses tercepat (diuji di RTX 2050 4 GB VRAM) maupun **CPU-only / AMD Radeon** (otomatis fallback ke `CPU int8` + `libx264`)
- **Tools:** `ffmpeg`, Python 3.11+ (atau cukup **Docker** / **Docker Desktop**)

## Menjalankan Aplikasi (Native Tanpa Docker)

*(Opsional)* Salin `.env.example` ke `.env` jika ingin mengisi `GEMINI_API_KEY` (semantic highlight & hook AI) atau mengaktifkan `CLIPPER_AUTO_CLEANUP_RAW=true`.

### A. Linux
1. Pastikan `ffmpeg` sudah terpasang (`sudo apt install -y ffmpeg`) dan environment Python 3.11 + `requirements.txt` sudah siap.
2. Jalankan server lokal:
   ```bash
   cp .env.example .env   # opsional
   ./run.sh
   ```
3. Buka **`http://localhost:8000`** di browser.

### B. Windows (PowerShell / CMD)
1. Install Python 3.11+ dan `ffmpeg`:
   ```powershell
   winget install Gyan.FFmpeg
   ```
2. Buat virtual environment & install dependencies:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Salin `.env.example` menjadi `.env` (opsional), lalu jalankan server:
   ```powershell
   copy .env.example .env
   uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
   ```
4. Buka **`http://localhost:8000`** di browser.

## Deployment dengan Docker (Linux & Windows — Disarankan untuk Tim)
Cara paling praktis agar tidak perlu setup `ffmpeg` atau CUDA secara manual. Di Windows cukup install **Docker Desktop (WSL2)**. Aplikasi otomatis menyesuaikan engine (`CUDA + NVENC` vs `CPU + libx264`):

1. **Laptop tanpa NVIDIA (CPU-only / AMD Radeon):**
   ```bash
   docker compose --profile cpu up -d --build
   ```
2. **Laptop / Server dengan GPU NVIDIA** *(butuh Docker + NVIDIA Container Toolkit)*:
   ```bash
   docker compose --profile gpu up -d --build
   ```
3. **Akses Bareng 1 Tim lewat Wi-Fi / LAN:**
   Jalankan server di 1 PC/laptop ber-GPU, lalu anggota tim lain cukup buka browser ke:
   ```text
   http://<IP-LOKAL-SERVER>:8000
   ```
   Semua data SQLite, preset streamer, dan klip tersimpan di folder `./storage` (ter-mount ke `/app/storage`).

## Fitur Utama
- **Multi-Source Input:** Paste URL (YouTube, Twitch, Kick via `yt-dlp`) atau upload file lokal (`.mp4`, `.mkv`, `.mov`, `.webm`).
- **Auto-Detect Hardware:** Otomatis mendeteksi mode **GPU (`CUDA + NVENC`)** atau **CPU-Only (`CPU int8 + libx264`)** dan menampilkannya di panel *Status Perangkat & Engine*.
- **Highlight Detection 4-Sinyal + AI:** Kombinasi energi audio (RMS), lonjakan kontras (*audio spike*), kata kunci reaksi ID/EN, kepadatan bicara, peredam intro livestream, serta opsional *semantic highlight* via `GEMINI_API_KEY`.
- **Kolaborasi Tim:** Identitas anggota tim aktif + filter job per orang, deteksi link duplikat (`HTTP 409`), checklist status upload (`TikTok`, `IG`, `YT`, `FB`), simpan preset streamer, dan 1-klik **Download Semua (.zip)**.
- **Layout 9:16 & Editor Ringan:** Fullscreen 9:16 dan Split-Screen (Facecam + Gameplay), koreksi typo subtitle (*Cari & Ganti Cepat*), *trim* detik mulai/selesai, teks hook atas, dan **Storage Monitor** untuk hemat SSD.

## Ringkasan API Endpoint
- `GET /api/health` — Cek status server
- `GET /api/jobs` / `GET /api/jobs/{job_id}` — Daftar & detail job beserta kandidat klip
- `POST /api/jobs/url` / `POST /api/jobs/upload` — Buat job baru
- `PATCH /api/jobs/{job_id}/layout` — Simpan koordinat Split-Screen
- `PATCH /api/clips/{clip_id}` — Edit waktu, layout, judul, *hook text*, dan kata subtitle
- `POST /api/clips/{clip_id}/render` / `POST /api/jobs/{job_id}/render-all` — Render klip via NVENC
- `GET /api/clips/{clip_id}/download` — Download MP4 hasil render
- `GET /api/storage` / `POST /api/storage/cleanup` / `DELETE /api/jobs/{job_id}/raw` — Monitor & bersihkan storage mentah

## Menjalankan Automated Test (`pytest`)
```bash
SITE_PKG="/home/angger/anaconda3/envs/clipper/lib/python3.11/site-packages"
export LD_LIBRARY_PATH="${SITE_PKG}/nvidia/cublas/lib:${SITE_PKG}/nvidia/cudnn/lib:${LD_LIBRARY_PATH}"
/home/angger/anaconda3/envs/clipper/bin/pytest -v /home/angger/Clipper-tools/tests
```
