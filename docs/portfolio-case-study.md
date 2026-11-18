# Portfolio Case Study — Kaltim Data Trust

## Hero
**Kaltim Data Trust — From Fragmented Public Spreadsheets to Traceable Regional Intelligence**

> Saya merancang dan mengimplementasikan prototipe independen rekayasa kualitas data dan analitik kesehatan publik Kalimantan Timur, berfokus pada audit byte-level, pemadanan kode wilayah Kemendagri–BPS, karantina anomali otomatis, serta perbandingan daerah yang dapat ditelusuri kembali ke sel sumbernya.

## 1. Masalah Nyata yang Ditemukan Saat Audit
Alih-alih langsung membangun grafik di atas file unduhan, eksekusi dimulai dari **Data Discovery Gate** terhadap katalog Dinas Kesehatan pada portal **Satu Data Kalimantan Timur** (18 November 2026). Audit byte dan sel mengungkap enam masalah nyata:
- **Masquerade format:** File `1 TENAGA DOKTER.csv` berlabel `CSV`, tetapi byte aslinya adalah kontainer ZIP/XLSX (`PK\x03\x04`) ber-hash identik dengan `1-dokter.xlsx` (bahkan `openpyxl` menolak membukanya jika dipassing path `.csv` tanpa stream `BytesIO`).
- **Benturan sistem kode wilayah tanpa label:** File Puskesmas memakai kode **BPS** (`6402`=Kutai Barat, `6403`=Kutai Kartanegara, `6404`=Kutai Timur, `6405`=Berau), sedangkan file Dokter dan Rumah Sakit memakai kode **Kemendagri** (`6402`=Kutai Kartanegara, `6403`=Berau, `6407`=Kutai Barat, `6408`=Kutai Timur). Join naif menukar empat kabupaten secara diam-diam.
- **Salah ketik kode kota duplikat:** Baris Kota Bontang pada `1-dokter.xlsx` (`R65`) tertulis `6472.0` (duplikat kode Kota Samarinda).
- **Korupsi serial waktu Excel & pemisah ribuan:** Paket publik `Distribusi Jumlah dan Rasio Tenaga Medis 2024` menyimpan rasio Paser `45,05` sebagai `1,877083` hari dan penduduk `286.991` jiwa sebagai bilangan pecahan `286.99099999999999`.
- **Inkosistensi subkategori tahun 2022:** Pada Berau (`185 < 197`) dan Kutai Barat (`120 < 184`), angka `Dokter Umum` melebihi baris total `Dokter`.

## 2. Solusi Rekayasa
1. **Byte Sniffer & Macro Guard (`sniff.py`):** Verifikasi magic bytes, penolakan makro VBA (`xl/vbaProject.bin`), dan pencatatan checksum `SHA-256`.
2. **Kamus Jembatan Wilayah (`region_aliases.csv` + `RegionResolver`):** Memetakan kode BPS dan Kemendagri ke kode kanonik dengan validasi silang nama wilayah serta mengkarantina wilayah tak dikenal (`UNMAPPED_REGION`).
3. **Mesin 14 Aturan Kualitas (`validate.py`):** Menguji keunikan kunci, keutuhan bilangan bulat, konsistensi subkategori, rekonsiliasi total provinsi, serta memblokir publikasi dataset korup (`SRC-DOC-RATIO-BAD` tertahan `QUARANTINED`).
4. **Skema SQL Terpisah untuk Fakta Sumber vs Turunan (`models.py` & `sql/`):** Menghubungkan setiap angka rasio melalui `derived_fact_inputs` ke fakta pembilang dan penyebutnya.
5. **Hybrid Edge Snapshot (`export_static.py` + Astro 5):** Mengekspor snapshot aktif yang lolos gate ke JSON ber-checksum untuk disajikan cepat dan tahan gangguan, selaras penuh dengan kontrak FastAPI `/api/v1`.
6. **Peta Tematik Choropleth Tervalidasi (`kdt.geo` + Leaflet SVG di `/map`):** Memadankan 10 poligon kabupaten/kota resmi BPS/WFP/UN OCHA (`CC BY 3.0 IGO`, termasuk rekonsiliasi ejaan `Mahakam Hulu` → `64.11`) dengan jaminan 0 poligon tak terpetakan, kompatibilitas penuh di browser mobile & desktop tanpa ketergantungan WebGL, serta bundel geospasial ringan yang terisolasi khusus pada halaman `/map`.

## 3. Hasil Terukur
- **20 skenario uji kritis (`T-001`..`T-020`)** lulus pada `pytest` (mencakup idempotensi, perlindungan snapshot lama saat run baru gagal, tahun tak sepadan, denominator nol, dan injeksi formula).
- **275 observasi terpublikasi** dengan cakupan pelacakan (`lineage`) **100%** hingga koordinat sel sumber.
- **Temuan analitis terverifikasi (2024):** Ketiga kota di Kalimantan Timur berada di atas rata-rata provinsi (`97,43` tenaga medis / 100.000 penduduk), dipimpin Kota Bontang (`156,14`), sedangkan ketujuh kabupaten berada di bawah angka provinsi dengan titik terendah pada Kabupaten Kutai Kartanegara (`46,60`) — sebuah ketimpangan per kapita yang tidak terlihat bila analis hanya melihat jumlah absolut (`368` orang di Kutai Kartanegara vs `294` di Bontang).
