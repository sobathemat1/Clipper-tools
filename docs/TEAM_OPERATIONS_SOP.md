# SOP Operasional Tim & Panduan Maintenance (Tahap 9–10)

Dokumen ini menjadi panduan kerja harian tim *clipper* menggunakan **Clipper Tools v1.0** saat menjalankan *campaign* (misalnya GTA VI, livestream gaming, atau VOD panjang).

---

## 1. Alur Kerja Harian Tim Clipper

1. **Set Nama Anggota Tim Aktif:**
   * Buka dashboard (`http://localhost:8000` atau `http://<IP-SERVER>:8000`).
   * Isi kolom **Anggota Tim Aktif** di kiri atas (misal: `Angger`, `Budi`, `Sari`). Nama ini tersimpan otomatis di browser masing-masing dan menempel pada setiap job baru.
2. **Submit Link VOD / Upload Video:**
   * Jika link video sudah pernah diproses anggota tim lain, sistem akan memunculkan peringatan duplikat (`HTTP 409`) lengkap dengan nama pemilik sebelumnya agar tim tidak membuang waktu memotong video yang sama.
3. **Gunakan Preset Streamer untuk Split-Screen:**
   * Jika sering mengambil konten dari streamer yang sama, atur kotak kuning **Facecam** & kotak hijau **Gameplay** satu kali, lalu simpan lewat **+ Simpan Preset**. Di video berikutnya cukup pilih preset → **Terapkan**.
4. **Kurasi, Edit Subtitle & Teks Hook:**
   * Periksa kandidat klip, gunakan **Cari & Ganti Cepat** untuk merapikan nama tokoh/istilah yang salah ketik, isi **Teks Hook Atas** bila perlu, lalu klik **Simpan Subtitle & Waktu**.
   * Jika ingin mencoba kombinasi deteksi momen terbaru pada transkrip yang sudah ada, klik **Analisis Ulang Momen**.
5. **Render & Distribusi:**
   * Klik **Render Klip** (atau **Render Semua Klip**). Setelah status `DONE`, putar preview untuk cek sinkronisasi lalu klik **Download MP4** atau **Download Semua (.zip)**.
6. **Tandai Checklist Upload (`TikTok`, `IG`, `YT`, `FB`):**
   * Setiap kali klip di-upload ke platform tertentu, klik pill platform pada kartu klip hingga berubah menjadi hijau (`✓ TikTok`, `✓ YT`, dst.) supaya seluruh tim tahu status distribusi tiap klip.
7. **Bersihkan File Mentah secara Berkala:**
   * Setelah semua klip selesai di-render, klik **Bersihkan Raw Job Selesai** di **Storage Monitor** (atau aktifkan `CLIPPER_AUTO_CLEANUP_RAW=true` di `.env`) agar SSD tidak cepat penuh oleh file `raw.mp4`.

---

## 2. Cara Akses Bersama 1 Tim (LAN / Wi-Fi)

* **Server Host (PC ber-GPU NVIDIA):** Jalankan `./run.sh` (Linux) atau `run.bat` (Windows), atau `docker compose --profile gpu up -d`. Server otomatis berjalan di `0.0.0.0:8000`.
* **Laptop Anggota Tim Lain (Windows/Mac/Linux tanpa GPU):** Tidak perlu install apa-apa—cukup buka browser dan akses:
  ```text
  http://<IP-LOKAL-SERVER>:8000
  ```
* **Mode Mandiri di Laptop Sendiri:** Jika bekerja di luar jaringan yang sama, jalankan di laptop masing-masing via Docker `--profile cpu` (otomatis memakai `CPU int8` + `libx264`).

---

## 3. Panduan Maintenance Rutin (Tahap 10)

* **Saat `yt-dlp` Gagal Download (Perubahan YouTube/Twitch/Kick):**
  Platform sering mengubah format ekstraksi video. Jalankan skrip pembaruan di folder proyek:
  ```bash
  ./update_deps.sh
  ```
* **Aktifkan AI Semantic Highlight (Opsional):**
  Isi `GEMINI_API_KEY=...` di file `.env` dan restart server jika ingin judul, hook text, dan pemilihan momen menggunakan penalaran konteks LLM Gemini.
* **Backup Data Tim:**
  Seluruh riwayat job, preset streamer, dan checklist upload tersimpan di `storage/clipper.db`. Cukup salin folder `storage/` saat memindahkan server ke perangkat baru.
