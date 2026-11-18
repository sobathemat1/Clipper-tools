# Stage 8 — Beta Testing Report (Clipper Tools)

**Tanggal Uji:** 2026-11-18  
**Environment Uji:** `http://localhost:8000` (Ubuntu 24.04, NVIDIA RTX 2050 4 GB VRAM, `CUDA + NVENC Studio`)  
**Metode Uji:** Browser UAT interaktif + verifikasi API backend + regression suite `pytest`

---

## 1. Skenario & Hasil Pengujian Beta

| Skenario Uji | Detail yang Diuji | Hasil |
| :--- | :--- | :--- |
| **Load Dashboard & Hardware Badge** | Cek `#hw-badge`, Storage Monitor, preview frame, overlay kotak `FACECAM` & `GAMEPLAY`, dan player video `<video>` | **PASS** — `CUDA + NVENC Studio`, `Hasil Klip: 145.35 MB`, `Sisa SSD: 84.83 GB`, 3 kartu klip & player tampil tanpa JS console error |
| **Journey 1 — Streamer Preset & Split-Screen** | Simpan preset `"Windah Basudara"`, terapkan preset, dan simpan koordinat Split-Screen | **PASS** — `POST /api/presets` (`200`), `PATCH /api/jobs/diZ6-kQW_Ok/layout` (`200`), preset tersimpan di SQLite (`preset_94a60254`) |
| **Journey 2 — Hook Banner & Find/Replace Subtitle** | Isi `"GTA VI 100% SIAP!"` di Hook Atas, cari `"Lu Siat Aminos"` → ganti `"Lucia Caminos"`, simpan & reload halaman | **PASS (setelah perbaikan)** — Awalnya gagal karena token `"Aminos."` bertanda baca titik; setelah diperbaiki di `app.js`, ganti kata berhasil dan persist setelah reload (`hook_text`, `Lucia`, `Caminos.`) |
| **Journey 3 — Checklist Upload & ZIP Download** | Toggle pill `TikTok` dan `YT` pada Clip #1, cek tombol `Download Semua (.zip)` | **PASS** — Status berubah jadi `✓ TikTok` & `✓ YT` (`published_platforms: ['tiktok', 'yt']`), link ZIP mengarah ke `/api/jobs/diZ6-kQW_Ok/download-zip` |
| **Journey 4 — Identitas Tim & Deteksi Duplikat** | Ubah Anggota Tim Aktif menjadi `"Budi"`, uji dropdown filter `#filter-owner`, dan cek penolakan URL duplikat | **PASS** — Filter berjalan tanpa error; submit ulang `https://youtu.be/diZ6-kQW_Ok` mengembalikan HTTP `409` dengan info pemilik sebelumnya (`Tim`) |
| **Regression Suite (`pytest`)** | Jalankan seluruh unit test, API test, portabilitas Docker/CI, dan E2E GPU Split-Screen test | **PASS** — **19 passed in 6.77s** |

---

## 2. Bug & Perbaikan Selama Beta Testing

1. **Bug Find & Replace Multi-Kata dengan Tanda Baca (`backend/static/app.js`):**
   * *Masalah:* Pencarian frasa seperti `"Lu Siat Aminos"` gagal mencocokkan token `["Lu", "Siat", "Aminos."]` karena token terakhir mengandung titik (`.`).
   * *Perbaikan:* Pencocokan token kini menormalisasi tanda baca di awal/akhir kata saat perbandingan dan tetap mempertahankan tanda baca akhir pada token hasil penggantian (`"Lucia"`, `"Caminos."`).
2. **UX Tombol Render Nonaktif Saat Video Mentah Dihapus (`backend/static/index.html`, `styles.css`, `app.js`):**
   * *Masalah:* Saat `raw.mp4` sudah dibersihkan (`Raw Video: 0 MB`), tombol `Render Klip (NVENC)` & `Render Semua Klip` nonaktif tanpa penjelasan langsung di layar.
   * *Perbaikan:* Menambahkan banner `#raw-missing-notice` dan tooltip agar anggota tim langsung paham bahwa video mentah sudah dihapus untuk menghemat SSD.

---

## 3. Catatan untuk Pengujian Mandiri oleh User

* Karena file video mentah (`raw.mp4`) pada job `diZ6-kQW_Ok` sudah dibersihkan sebelumnya, uji tombol **Render Klip (NVENC)** secara interaktif belum dijalankan ulang pada job lama tersebut (meski pipeline NVENC Split-Screen tetap teruji lulus di `tests/test_e2e_gpu.py`).
* Untuk mencoba pengalaman lengkap dari awal sampai render ulang di **`http://localhost:8000`**, coba submit 1 link YouTube baru (atau upload video lokal) dengan nama Anggota Tim yang berbeda (mis. `"Angger"`), lalu tes:
  1. Edit subtitle + isi **Teks Hook Atas**.
  2. Ubah layout ke **Split-Screen** lalu klik **Render Klip (NVENC)**.
  3. Putar hasil videonya langsung di browser dan coba **Download Semua (.zip)**.

---

## 4. Round 2 Beta QA Testing (Upgrade Detektor Momen & Status Hardware)

**Target Uji:** Job nyata user *"SEDIKIT LAGI PERJALANAN SAMPAI! TAPI KOK.... Euro Truck Simulator 2 GAMEPLAY #4"* (`job_146bcdac00`, durasi `1710.1s`, `Raw Video: 905.09 MB`, pemilik `Angger`).

### 4.1. Verifikasi Panel Status Perangkat & Engine
* **Mode Badge:** `GPU AKTIF (CUDA + NVENC)`
* **GPU:** `NVIDIA GeForce RTX 2050 (4.0 GB VRAM)`
* **CPU & RAM:** `AMD Ryzen 7 7435HS • 16 Core • 7.6 GB RAM`
* **Whisper STT:** `CUDA (int8_float16)`
* **Video Encoder:** `NVIDIA h264_nvenc`
* **Deteksi Momen:** `4-Sinyal Lokal (Audio Spike + Reaksi)`
* **Label Tombol Render:** `Render Klip (GPU NVENC)`

### 4.2. Perbandingan Penangkapan Momen (`Analisis Ulang Momen`)
* **Sebelum (Detektor 2-Sinyal Lama):**
  * Clip #1 mengambil intro livestream (`0.05s → 34.46s`, *"Selamat sore teman-teman sekalian, balik..."*).
* **Sesudah (Detektor 4-Sinyal + Action Keywords + Intro Penalty):**
  * **Clip #1:** `118.12s → 153.21s` (*"GG gak? GG gak? GG gak... ngebut banget deh"*) — intro `0.05s` tereliminasi.
  * **Clip #2:** `200.26s → 238.76s` (*"saya mesti kalah..."*)
  * **Clip #3:** `315.15s → 349.61s` (*"supir balas kanan lanjut lanjut guys..."*)
  * **Clip #4:** `719.11s → 749.96s` (*"lihat-lihat guys, di panelnya guys..."*)
  * **Clip #5:** `911.37s → 946.00s` (*"oke lurusin setir dulu challenge mulai..."* — momen oleng/challenge)
  * **Clip #6:** `999.28s → 1035.69s` (*"ikat kerusakannya udah tanpa parah guys... challenge tanpa rem... nabrak"*)
  * **Clip #7:** `1082.99s → 1116.69s` (*"wiper kalo ujan kita nyalain ya..."*)
  * **Clip #8:** `1206.85s → 1237.29s` (*"aduh susah coh, mundur coh... putar balik"*)

### 4.3. Uji Render GPU NVENC Langsung di Browser & Regression Suite
* **Render Clip #1 (`job_146bcdac00_c01`):** Klik `Render Klip (GPU NVENC)` → selesai menjadi `done` tanpa error JS console, player `<video>` menampilkan subtitle `"GG GAK? GG GAK?"`, dan tombol `Download MP4` muncul (`/api/clips/job_146bcdac00_c01/download`).
* **`pytest -v /home/angger/Clipper-tools/tests`:** **22 passed in 6.64s** (semua unit test, API test, portabilitas Docker/CI, deteksi 4-sinyal, dan E2E GPU test lulus).

