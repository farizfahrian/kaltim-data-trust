# Methodology — Kaltim Data Trust

## 1. Alur Data-to-Decision (10 Tahap Deterministik)

1. **Register (`sources.yml`):** Mencatat URL resmi paket, URL resource, penerbit, status lisensi, dan sistem kode wilayah.
2. **Fingerprint (`sniff.py`):** Menghitung sidik jari `SHA-256` dari byte mentah, memverifikasi struktur ZIP/XLSX (`PK\x03\x04`) vs CSV UTF-8, serta menolak file korup atau bermakro VBA (`xl/vbaProject.bin`).
3. **Extract (`parse.py`):** Membaca lembar kerja menggunakan stream `BytesIO` agar file `.csv` ber-byte XLSX (`1 TENAGA DOKTER.csv`) tetap terurai deterministik tanpa mengeksekusi ulang formula.
4. **Normalize (`normalize.py` & `registry.py`):** Memetakan kode wilayah BPS (`6402`=Kutai Barat, `6403`=Kutai Kartanegara, `6404`=Kutai Timur, `6405`=Berau) ke kode kanonik Kemendagri (`64.07`, `64.02`, `64.08`, `64.03`) dengan validasi silang nama wilayah.
5. **Validate (`validate.py`):** Menjalankan skema Pandera dan 14 aturan kualitas (`DQ-001` s/d `DQ-014`).
6. **Quarantine:** Menyimpan setiap baris gagal beserta koordinat sel sumber (`sheet!R..C..`) pada tabel `quarantined_records`.
7. **Derive (`metrics.py`):** Menghitung penjumlahan fasilitas saling lepas (`RS + Puskesmas RI + Puskesmas Non-RI`) dan rasio per 100.000 penduduk hanya bila `region_code` dan `reference_year` cocok, serta menyimpan relasi input di `derived_fact_inputs`.
8. **Publish (`publish.py`):** Transaksi atomik yang mengaktifkan satu `published_snapshot` baru tanpa pernah menimpa snapshot sah bila proses impor baru gagal.
9. **Serve / Export (`api.py` & `export_static.py`):** Menyajikan kontrak `/api/v1/*` secara identik melalui FastAPI maupun snapshot statis ber-checksum SHA-256.
10. **Trace:** Memungkinkan pembaca melacak setiap angka di layar kembali ke sel lembar kerja, hash file, dan URL penerbitnya.

## 2. Rumus & Aturan Rasio

$$\text{Rasio per } 100.000 = \frac{\text{Pembilang Tervalidasi}}{\text{Jumlah Penduduk Tervalidasi}} \times 100.000$$

- Pembulatan menggunakan `ROUND_HALF_UP` hingga 2 angka desimal.
- Apabila `population_count <= 0` atau `NULL`, hasil ditetapkan `NULL` dengan status `not_computable` (`denominator_not_computable`).
- Apabila tahun referensi pembilang dan penyebut berbeda (contoh: data dokter 2025 sementara denominator penduduk teraudit hanya tersedia untuk 2024), hasil ditetapkan `NULL` dengan status `not_comparable` (`year_mismatch`).
