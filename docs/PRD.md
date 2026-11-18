# Product Requirements Document (PRD) — Clipper Tools

## 1. Ringkasan Produk
**Clipper Tools** adalah aplikasi web lokal untuk mengotomasi proses pemotongan video berdurasi panjang (gameplay, livestream, podcast/reaction seperti konten GTA VI) menjadi klip vertikal `9:16` siap upload untuk **TikTok, Instagram Reels, Facebook Reels, dan YouTube Shorts**.

Tools ini dirancang khusus untuk penggunaan **internal / tim clipper** dengan fokus pada **kecepatan render (memanfaatkan GPU NVIDIA lokal)**, **efisiensi biaya** (transkripsi & render 100% lokal), dan **kualitas klip yang layak FYP**.

---

## 2. Target Pengguna & Tujuan
* **Target User:** Pembuat konten / tim *clipper* internal.
* **Masalah yang Diselesaikan:** Proses manual mencari momen menarik di VOD berdurasi berjam-jam, memotong rasio 16:9 ke 9:16 (termasuk *split-screen* facecam + gameplay), dan membuat subtitle dinamis memakan waktu sangat lama.
* **Tujuan Utama:** Mengubah 1 video panjang menjadi beberapa klip vertikal bersubtitle siap upload dalam hitungan menit.

---

## 3. Ruang Lingkup (Scope)

### 3.1. Masuk MVP (Fase 1)
1. **Multi-Source Input:**
   * Input via URL (YouTube, Twitch, Kick) menggunakan downloader otomatis.
   * Upload file video lokal (`.mp4`, `.mkv`, `.mov`).
2. **Deteksi Momen Otomatis (Hybrid):**
   * Analisis energi audio (*decibel spike* / momen heboh) + transkrip suara lokal.
   * Opsional: integrasi API LLM berbiaya rendah untuk menilai skor kelayakan momen (*virality score*) dan menghasilkan rekomendasi judul/hook.
   * Fallback mode 100% lokal (tanpa API key) tetap berfungsi menggunakan skor audio + heuristik kata kunci.
3. **Layout & Reframing 9:16:**
   * **Mode Fullscreen 9:16:** Crop tengah/fokus gameplay.
   * **Mode Split-Screen (Facecam + Gameplay):** User dapat menentukan area kotak facecam dan area gameplay pada preview awal, lalu sistem merender facecam di atas dan gameplay di bawah dalam kanvas `1080x1920`.
4. **Auto-Subtitle Dinamis (ID & EN):**
   * Transkripsi otomatis mendukung Bahasa Indonesia, Bahasa Inggris, dan campuran (*code-switching*).
   * Burn-in subtitle gaya TikTok/Reels (*word-level highlight* / per frasa pendek, font tebal dengan outline agar mudah dibaca).
5. **Web Dashboard Lokal:**
   * Form submit job baru (URL/file, pilihan template, target durasi klip, jumlah klip).
   * Monitor status & progres antrean (*downloading, transcribing, analyzing, rendering, done*).
   * Preview video hasil klip langsung di browser.
   * **Editor Ringan:** Koreksi teks subtitle yang typo dan penyesuaian *trim* awal/akhir klip sebelum render final.
   * Download klip satuan atau sekaligus (`.zip`).

### 3.2. Di Luar MVP (Fase 2 / Future)
* Auto-post & scheduling langsung ke API TikTok, IG, FB, dan YouTube Shorts.
* Multi-user authentication & billing (SaaS mode).
* Auto-face tracking dinamis berbasis AI untuk multi-pembicara.
* Auto-insert teks hook / watermark tim secara massal.

---

## 4. Alur Pengguna (User Flow)
1. User membuka **Web Dashboard** di browser lokal (`http://localhost:xxxx`).
2. User memilih **New Clip Job**:
   * Paste URL video atau pilih file video lokal.
   * Pilih bahasa utama audio (`Auto`, `ID`, `EN`).
   * Pilih template layout (`Fullscreen 9:16` atau `Split-Screen Facecam + Gameplay`). Jika pilih *Split-Screen*, user menandai posisi kotak facecam pada frame sampel.
   * Tentukan target durasi klip (misal `30–60 detik`) dan jumlah kandidat klip (misal `5 klip`).
3. Sistem memproses video di *background* dan menampilkan progres real-time.
4. Setelah kandidat klip selesai dianalisis, user bisa:
   * Melihat daftar kandidat momen beserta skor/ringkasan.
   * Mengedit teks subtitle atau menggeser *timestamp* mulai/selesai bila perlu.
   * Klik **Render Final** dan mengunduh klip `.mp4` (`1080x1920`) siap upload.

---

## 5. Kebutuhan Non-Fungsional & Target Performa
* **Hardware Target:** PC/Laptop lokal dengan **GPU NVIDIA (CUDA & NVENC)**.
* **Kecepatan Proses:** Video berdurasi 1 jam dapat dianalisis dan menghasilkan 5 klip siap upload dalam waktu **< 10 menit**.
* **Kualitas Output:** Resolusi `1080x1920` (9:16), 30/60 fps, H.264, audio AAC stereo, sinkronisasi subtitle akurat (< 200ms drift).
* **Manajemen Disk:** File video mentah sementara dan cache audio memiliki tombol/aturan *auto-cleanup* agar kapasitas SSD lokal tidak cepat penuh.

---

## 6. Kriteria Keberhasilan (Success Metrics)
* Minimal **80% klip** yang dihasilkan hanya butuh sedikit atau tanpa edit manual sebelum di-upload.
* Pipeline berjalan stabil untuk video berdurasi panjang (1–3 jam) tanpa crash karena kehabisan memori.
* Nol biaya wajib per video jika dijalankan dalam mode 100% lokal.
