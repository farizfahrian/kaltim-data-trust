# Data Source Audit Report — Kaltim Data Trust

> **Tanggal eksekusi audit:** 18 November 2026 (UTC: `2026-11-18T06:50Z–07:00Z`)  
> **Status Gate:** `GO-A` (Perbandingan absolut 2024) dan `GO-B` (Rasio per 100.000 penduduk 2024) **LULUS DENGAN PENYESUAIAN CAKUPAN TERTULIS** (`ADR-001`).  
> **Prinsip redistribusi:** File mentah di `data/raw/` tidak di-commit ke Git dan tidak disediakan sebagai unduhan langsung karena header CKAN mencatat lisensi kosong (`No License Provided`), meskipun lampiran resmi `Metadata Indikator` menyatakan *"Apakah dapat diakses umum? = Ya"*.

---

## 1. Ringkasan Eksekutif Temuan Audit Nyata

Pemeriksaan byte-level terhadap file asli yang diunduh dari portal **Satu Data Kalimantan Timur** (`data.kaltimprov.go.id`) membuktikan bahwa enam hipotesis risiko kualitas data pada PRD benar-benar terjadi di lapangan:

1. **Label CSV Palsu (Byte XLSX):** Pada `SRC-DOC`, resource `1 TENAGA DOKTER.csv` berlabel format `CSV` di CKAN, tetapi *magic bytes* awalnya adalah `504b0304` (kontainer ZIP/XLSX) dengan nilai `SHA-256` identik (`f427719f...`, 29.159 byte) terhadap `1-dokter.xlsx`, serta `datastore_contains_all_records_of_source_file = false`.
2. **Salah Ketik Kode Wilayah Duplikat:** Pada `SRC-DOC` (`1-dokter.xlsx` baris `R65`), **Kota Bontang** diberi kode `6472.0` — sama persis dengan kode **Kota Samarinda** (`R58`) — padahal kode resmi Kota Bontang adalah `64.74` (`6474`). Join naif memakai kolom kode akan menggandakan Samarinda atau menghilangkan Bontang.
3. **Benturan Sistem Kode Kemendagri vs BPS Antar-File dari Penerbit yang Sama:**
   - `SRC-DOC` dan `SRC-RS` memakai penomoran **Kemendagri** (`6402` = Kutai Kartanegara, `6403` = Berau, `6407` = Kutai Barat, `6408` = Kutai Timur).
   - `SRC-PKM-RI` dan `SRC-PKM-NRI` memakai penomoran **BPS** (`6402` = Kutai Barat, `6403` = Kutai Kartanegara, `6404` = Kutai Timur, `6405` = Berau) tanpa deklarasi sistem kode pada nama kolom `KODE`. Penggabungan tanpa jembatan kode akan **menukar data empat kabupaten secara diam-diam**.
4. **Kontradiksi Catatan Katalog vs Rumus Excel (`=SUM`) & Anomali 2022:** Catatan CKAN `SRC-DOC` menyebut baris *Dokter* tidak menyebut dokter gigi, tetapi formula sel `=SUM(F4:F7)` pada file menjumlahkan `Dokter Umum + Dokter Spesialis + Dokter Gigi + Dokter Gigi Spesialis` (sesuai definisi *Tenaga Medis* Permenkes 18/2022). Selain itu, tahun **2022** mengalami anomali struktural di Berau (`Dokter=185 < Dokter Umum=197`) dan Kutai Barat (`Dokter=120 < Dokter Umum=184`), serta tahun **2024** pada baris `Dokter Spesialis` provinsi (`980`) berselisih `+50` dari jumlah 10 kabupaten/kota (`930`).
5. **Korupsi Serial Waktu Excel & Pemisah Ribuan (`SRC-DOC-RATIO-BAD`):** File publik `Distribusi Jumlah dan Rasio Tenaga Medis Tahun 2024` (`aa5b1aee...`) menyimpan rasio Paser `45,05` sebagai `1.8770833333` hari (akibat teks `45:03` terbaca sebagai jam:menit oleh Excel), mengubah penduduk kabupaten `286.991` menjadi bilangan desimal `286.99099999999999` sementara baris provinsi berupa teks `'4,045,858'`, dan memotong total provinsi tenaga kesehatan dari `27.040` menjadi `'27.04'`. Dataset ini **dikarantina penuh (`BLOCKED`)** dan dijadikan bukti nyata efektivitas *Quality Gate*.
6. **Slug Lama HTTP 404 & Ketidaksinkronan Satuan Persentase:** URL kandidat awal `jumlah-fasilitas-pelayanan-kesehatan-...-2023-2025` mengembalikan `HTTP 404`. Data fasilitas aktif 2024 tersedia terpisah pada `SRC-RS` (Rumah Sakit = 47 unit), `SRC-PKM-RI` (Puskesmas Rawat Inap = 89 unit), dan `SRC-PKM-NRI` (Puskesmas Non Rawat Inap = 99 unit; total Puskesmas `89 + 99 = 188` terverifikasi 100% cocok dengan `SRC-PKM-SPA` dan `SRC-ACC`). Adapun pada `SRC-PKM-SPA`, seluruh baris kabupaten untuk metrik *Persentase* justru berlabel satuan `'buah'`.

---

## 2. Inventaris Sumber Terverifikasi

| Source ID | Judul Paket | Format Katalog vs Aktual | Ukuran (Byte) | SHA-256 | Sistem Kode | Keputusan Gate |
|---|---|---|---|---|---|---|
| `SRC-DOC` | Distribusi Tenaga Dokter di Prov. Kaltim Tahun 2022–2025 | `CSV` / `XLSX` → **`XLSX`** | 29.159 | `f427719f44070b01021ab25e8a5fe7cded0e39de75f3ded705bbd87b62eac8ad` | Kemendagri (typo `6472` di Bontang) | **PASS (2023–2025)**; **BLOCK 2022** |
| `SRC-RS` | Jumlah Rumah Sakit Tahun 2024 | `XLSX` → **`XLSX`** | 9.314 | `0815eaf574d3c16fc6d3da2a3f78ae5f783128d120c9a104ab015bd0601414ef` | Kemendagri (`6400` prov) | **PASS (2024)** |
| `SRC-PKM-RI` | Jumlah Puskesmas Rawat Inap Tahun 2024 | `XLSX` → **`XLSX`** | 23.901 | `f927d32043e6e448d0f0b246b858691515306a96d36fabc8cf8e50a00f743b29` | **BPS** (`6402`=Kubar, `6403`=Kukar) | **PASS (2024)** |
| `SRC-PKM-NRI` | Jumlah Puskesmas Non Rawat Inap Tahun 2024 | `XLSX` → **`XLSX`** | 23.932 | `c6f54da0db4bfde880f5c4db6e79f95e14fd29c9b04711b808d96597e3dd7f68` | **BPS** (`6402`=Kubar, `6403`=Kukar) | **PASS (2024)** |
| `SRC-POP-NAKES` | Rasio Tenaga Kesehatan di Prov. Kaltim Tahun 2024 | `XLSX` → **`XLSX`** | 10.864 | `f0589145f52b99429ff876f0ea4fddf589e725a6d13251be3a38579a0914515c` | Nama Wilayah (10 kab/kota + prov) | **PASS Denominator Penduduk 2024** |
| `SRC-DOC-RATIO-BAD` | Distribusi Jumlah dan Rasio Tenaga Medis Tahun 2024 | `XLSX` → **`XLSX`** | 10.808 | `aa5b1aee1547e95ea7c24342ba542975c6a61c2b63a80664c236fb20c2d0b343` | Nama Wilayah | **QUARANTINED / BLOCKED** (korupsi serial waktu & pemisah ribuan) |

*Catatan Akses BPS (`kaltim.bps.go.id`):* Permintaan HTTP terotomasi langsung ke halaman web tabel BPS dikembalikan sebagai `HTTP 403 Forbidden` (proteksi anti-bot). Sesuai pedoman etika teknis, sistem tidak melakukan bypass proteksi anti-bot dan menggunakan publikasi resmi `SRC-POP-NAKES` pada Satu Data Kaltim yang memuat kolom `JUMLAH PENDUDUK` integer utuh per kabupaten/kota tahun 2024 (total `4.045.858` jiwa, cocok dengan ringkasan publikasi *Kalimantan Timur Dalam Angka 2024*).

---

## 3. Rekonsiliasi Silang Angka Tahun 2024 (10 Kabupaten/Kota)

Tabel di bawah merangkum hasil rekonsiliasi manual sebelum penulisan kode pipeline:

| Kode Kanonik | Kode BPS | Wilayah | Penduduk 2024 (`jiwa`) | Tenaga Medis (`SRC-DOC`) | Dokter Umum (`SRC-DOC` = `SRC-POP-NAKES`) | Dokter Spesialis (`SRC-DOC`) | RS (`SRC-RS`) | PKM RI (`SRC-PKM-RI`) | PKM Non-RI (`SRC-PKM-NRI`) | Total PKM (Cocok `SPA`) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `64.01` | `6401` | Kab. Paser | 286.991 | 223 | 130 | 40 | 2 | 9 | 10 | 19 |
| `64.02` | `6403` | Kab. Kutai Kartanegara | 789.767 | 368 | 242 | 76 | 3 | 23 | 9 | 32 |
| `64.03` | `6405` | Kab. Berau | 261.833 | 224 | 151 | 35 | 2 | 8 | 13 | 21 |
| `64.07` | `6402` | Kab. Kutai Barat | 178.743 | 130 | 90 | 24 | 3 | 14 | 5 | 19 |
| `64.08` | `6404` | Kab. Kutai Timur | 462.993 | 314 | 206 | 62 | 3 | 18 | 3 | 21 |
| `64.09` | `6409` | Kab. Penajam Paser Utara | 267.686 | 128 | 67 | 27 | 4 | 9 | 2 | 11 |
| `64.11` | `6411` | Kab. Mahakam Ulu | 34.246 | 22 | 18 | 0 | 2 | 6 | 0 | 6 |
| `64.71` | `6471` | Kota Balikpapan | 717.231 | 1.063 | 557 | 305 | 12 | 0 | 27 | 27 |
| `64.72` | `6472` | Kota Samarinda | 858.079 | 1.176 | 682 | 297 | 11 | 2 | 24 | 26 |
| `64.74` | `6474` | Kota Bontang | 188.289 | 294 | 176 | 64 | 5 | 0 | 6 | 6 |
| **`64`** | **`6400`** | **Prov. Kalimantan Timur** | **4.045.858** | **3.942** | **2.319** | **930** *(sumber prov: 980)* | **47** | **89** | **99** | **188** |

---

## 4. Keputusan Data Discovery Gate

1. **GO-A (Indikator Absolut Kesehatan 2024):** **GO.** Sepuluh kabupaten/kota lengkap pada tahun referensi 2024 dengan rekonsiliasi 100% pada `doctor_total_count`, `doctor_general_count`, `dentist_total_count`, `hospital_total_count`, `puskesmas_inpatient_count`, dan `puskesmas_non_inpatient_count`. Untuk `doctor_specialist_count`, nilai kabupaten/kota valid (`validated`), sedangkan pembanding provinsi diberi catatan `warning` karena selisih `+50` terhadap baris total sumber.
2. **GO-B (Rasio per 100.000 Penduduk 2024):** **GO untuk tahun 2024 saja.** Denominator penduduk 2024 tersedia lengkap dalam satuan `jiwa` utuh dan kompatibel dengan angka dokter 2024 pada dokumen sumber yang sama (`2.319` dokter umum & `610` dokter gigi cocok 100%). Untuk tahun **2023** dan **2025**, rasio per 100.000 penduduk **WAJIB DIBLOKIR (`not_comparable` / `year_mismatch`)** karena denominator penduduk resmi untuk tahun tersebut belum diaudit.
3. **Kebijakan Lisensi & Publikasi:** Tidak meng-host ulang file `.xlsx`/`.csv` asli dan tidak menyediakan fitur unduh data mentah. Hanya menyajikan indikator agregat terkurasi dengan atribusi penuh dan tautan langsung ke halaman resmi Satu Data Kaltim.
