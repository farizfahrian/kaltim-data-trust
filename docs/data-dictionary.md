# Data Dictionary — Kaltim Data Trust

## 1. Kebijakan Grain & Kunci Bisnis

- **Grain fakta terkurasi (`curated_facts`):** Satu nilai numerik (atau status hilang eksplisit) untuk kombinasi `(run_id, region_code, reference_year, metric_code)` yang berasal dari **satu sumber tunggal**.
- **Grain fakta turunan (`derived_facts`):** Satu nilai terhitung untuk kombinasi `(derivation_run_id, region_code, reference_year, metric_code)` yang menautkan seluruh fakta pembentuknya melalui tabel `derived_fact_inputs` (`numerator`, `denominator`, `supporting`).
- **Kunci publikasi aktif:** Dalam satu `published_snapshot` berstatus `active`, maksimal hanya ada **satu** observasi untuk setiap `(metric_code, region_code, reference_year)`.

---

## 2. Master Wilayah (`regions`)

Sistem menggunakan **Kode Wilayah Administrasi Pemerintahan (Kepmendagri 100.1.1-6117/2022)** sebagai `region_code` kanonik dan menyimpan `bps_numeric` secara eksplisit untuk menjembatani dataset yang memakai penomoran BPS.

| `region_code` | `kemendagri_numeric` | `bps_numeric` | `region_name` | `region_level` | `parent_region_code` |
|---|---|---|---|---|---|
| `64` | `64` (`6400`) | `6400` | Provinsi Kalimantan Timur | `province` | `NULL` |
| `64.01` | `6401` | `6401` | Kabupaten Paser | `regency` | `64` |
| `64.02` | `6402` | `6403` | Kabupaten Kutai Kartanegara | `regency` | `64` |
| `64.03` | `6403` | `6405` | Kabupaten Berau | `regency` | `64` |
| `64.07` | `6407` | `6402` | Kabupaten Kutai Barat | `regency` | `64` |
| `64.08` | `6408` | `6404` | Kabupaten Kutai Timur | `regency` | `64` |
| `64.09` | `6409` | `6409` | Kabupaten Penajam Paser Utara | `regency` | `64` |
| `64.11` | `6411` | `6411` | Kabupaten Mahakam Ulu | `regency` | `64` |
| `64.71` | `6471` | `6471` | Kota Balikpapan | `city` | `64` |
| `64.72` | `6472` | `6472` | Kota Samarinda | `city` | `64` |
| `64.74` | `6474` | `6474` | Kota Bontang | `city` | `64` |

> **Aturan Keras (`FR-004`):** Nama atau kode wilayah yang tidak terdaftar di `data/mapping/region_aliases.csv` wajib diberi penanda `UNMAPPED_REGION` dan dikarantina (`DQ-003`), tidak pernah ditebak memakai *fuzzy matching*.

---

## 3. Semantik Nilai Nol, Hilang, dan Diblokir

| Kondisi | `value_numeric` | `missing_reason` | `quality_status` | Tampilan UI |
|---|---|---|---|---|
| Nilai nol faktual terverifikasi (mis. Puskesmas Rawat Inap Kota Balikpapan = `0`) | `0` | `NULL` | `validated` | `0 unit` |
| Kosong atau sentinel `-` / `TAD` di sumber (mis. Dokter Gigi Spesialis Mahakam Ulu 2025) | `NULL` | `missing_in_source` | `warning` | `—` *(Tidak tercatat di sumber)* |
| Tahun pembanding tidak sepadan (mis. `doctor_per_100k` tahun 2025 tanpa penduduk 2025) | `NULL` | `year_mismatch` | `not_comparable` | `—` *(Belum sepadan)* |
| Denominator bernilai `<= 0` atau `NULL` | `NULL` | `denominator_not_computable` | `not_computable` | `—` *(Tidak dapat dihitung)* |
| Gagal aturan kualitas kritis (mis. `Dokter` 2022 Berau/Kubar atau `SRC-DOC-RATIO-BAD`) | `NULL` | `blocked_quality` | `blocked` | `Diblokir` *(dengan alasan rule)* |

---

## 4. Skema Kolom Inti

### 4.1 `curated_facts`
- `fact_id` (`TEXT` UUID): Identitas unik fakta sumber.
- `run_id` (`TEXT` UUID): Referensi proses impor (`import_runs`).
- `source_id` (`TEXT`): ID sumber (`SRC-DOC`, `SRC-RS`, dll.).
- `region_code` (`TEXT`): Kode wilayah kanonik Kemendagri.
- `reference_year` (`INTEGER`): Tahun observasi data (bukan tahun unggah file).
- `metric_code` (`TEXT`): Kode indikator sesuai `metric_map.yml`.
- `value_numeric` (`NUMERIC` nullable): Angka terstandarisasi; `NULL` bila hilang/diblokir.
- `unit` (`TEXT`): Satuan baku (`orang`, `unit`, `jiwa`).
- `missing_reason` (`TEXT` nullable): Alasan wajib bila `value_numeric IS NULL`.
- `quality_status` (`TEXT`): `validated | warning | blocked | not_checked`.
- `source_locator` (`TEXT`): Koordinat bukti pada file sumber (contoh: `sheet:1-dokter!R3C5`).
- `transformation_version` (`TEXT`): Versi aturan normalisasi.

### 4.2 `derived_facts` & `derived_fact_inputs`
- `derived_fact_id` (`TEXT` UUID): Identitas unik indikator hasil perhitungan lintas sumber.
- `derivation_run_id` (`TEXT` UUID): Referensi eksekusi mesin metrik.
- `region_code`, `reference_year`, `metric_code`, `value_numeric`, `unit`, `missing_reason`, `quality_status`, `formula_applied`, `methodology_version`.
- `derived_fact_inputs`: Menyimpan relasi `(derived_fact_id, source_fact_id, input_role)` dengan `input_role IN ('numerator', 'denominator', 'supporting')` sehingga setiap angka rasio dapat ditelusuri balik hingga sel Excel asalnya.
