# Changelog — Clipper Tools

## v1.1.0 (2026-11-18) — Stage 10 Monitoring & Maintenance
* **Team Productivity & Distribution Metrics (`GET /api/metrics`):** Ringkasan jumlah klip ter-render, klip ter-upload, distribusi platform (`TikTok`, `IG`, `YT`, `FB`), kontribusi per anggota tim, dan versi `yt-dlp` aktif di panel **Monitoring & Maintenance**.
* **1-Click Online SQLite Backup (`GET /api/maintenance/backup-db`):** Backup database `clipper.db` secara aman dari mode WAL langsung melalui browser.
* **In-App `yt-dlp` Updater (`POST /api/maintenance/update-ytdlp`):** Tombol pembaruan `yt-dlp` langsung dari dashboard tanpa membuka terminal.

## v1.0.0 (2026-11-18) — Production Launch

### Fitur Utama
* **Input Multi-Source:** Download video URL (YouTube, Twitch, Kick via `yt-dlp`) dan upload video lokal (`.mp4`, `.mkv`, `.mov`, `.webm`) dengan batas ukuran upload yang dapat dikonfigurasi (`CLIPPER_MAX_UPLOAD_MB`).
* **Auto-Detect Hardware:** Deteksi otomatis GPU NVIDIA (`CUDA` + `h264_nvenc`) maupun fallback ke CPU/AMD Radeon (`CPU int8` + `libx264`), lengkap dengan panel **Status Perangkat & Engine** di dashboard (GPU, CPU & RAM, Whisper, Encoder, Detektor Momen).
* **Deteksi Momen 4-Sinyal + AI Opsional:**
  * Kombinasi energi audio (RMS), lonjakan kontras (*audio spike*), kata kunci reaksi ID/EN + tanda baca, kepadatan bicara, peredam intro livestream, bonus pembuka 6 detik pertama, dan *non-overlapping intervals*.
  * Dukungan *semantic highlight selection*, judul, dan *hook text* otomatis via Gemini API (`GEMINI_API_KEY` opsional).
  * Tombol **Analisis Ulang Momen** (`POST /api/jobs/{id}/reanalyze`) dari transkrip tersimpan.
* **Editor & Rendering 9:16:** Layout **Fullscreen 9:16** dan **Split-Screen** dengan preview kotak visual, **Editor Subtitle** per kata + *Cari & Ganti Cepat* toleran tanda baca, *trim* detik mulai/selesai, **Teks Hook Banner Atas**, dan preview video langsung di browser.
* **Kolaborasi Tim:** Identitas anggota tim aktif (`localStorage`) + filter job per orang, deteksi link duplikat (`HTTP 409`), checklist status upload (`TikTok`, `IG`, `YT`, `FB`), preset streamer tersimpan, dan 1-klik **Download Semua (.zip)**.
* **Storage Monitor & Docker Portability:** Pantau pemakaian SSD, bersihkan file mentah job selesai (`POST /api/storage/cleanup` atau `CLIPPER_AUTO_CLEANUP_RAW`), `Dockerfile` + `docker-compose.yml` (profil `cpu` & `gpu`), launcher Linux (`run.sh`) & Windows (`run.bat`), serta CI workflow GitHub Actions.

### Hasil Pengujian Rilis
* **Automated Test Suite (`pytest`):** 22/22 test lulus (unit, API integration, Docker/CI config check, dan E2E GPU Split-Screen).
* **Beta Testing (2 Putaran):** Teruji pada video nyata GTA VI dan Euro Truck Simulator 2 (28.5 menit) termasuk render live GPU NVENC di browser.
