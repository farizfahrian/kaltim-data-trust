# Metric Feasibility & Operational Specification

Dokumen ini menetapkan kelayakan setiap kandidat indikator berdasarkan hasil **Data Discovery Gate** tanggal 18 November 2026.

## Matriks Kelayakan Indikator

| Metric Code | Status Gate | Tahun Aktif | Keputusan Teknis & Alasan |
|---|---|---|---|
| `doctor_total_count` | **GO (P0)** | 2023, 2024, 2025 | Tersedia lengkap di `SRC-DOC`. Dinamai **Jumlah Tenaga Medis Kedokteran** agar jujur terhadap rumus Excel `=SUM` yang menyertakan dokter gigi. Tahun **2022 DIBLOKIR** karena gagal konsistensi subkategori pada Berau & Kutai Barat. |
| `doctor_general_count` | **GO (P0)** | 2023, 2024, 2025 | Tersedia lengkap di `SRC-DOC` dan terverifikasi 100% cocok terhadap `SRC-POP-NAKES` pada tahun 2024 (`2.319` orang). |
| `doctor_specialist_count` | **GO dengan Warning Provinsi (P0)** | 2023, 2024, 2025 | Nilai 10 kab/kota valid; pada 2024 jumlah 10 kab/kota (`930`) berbeda dari baris provinsi (`980`), sehingga baris provinsi diberi status `warning` (`DQ-011`). |
| `dentist_total_count` | **GO (P1)** | 2023, 2024, 2025 | Tersedia lengkap di `SRC-DOC` (`610` orang pada 2024, 100% cocok dengan `SRC-POP-NAKES`). |
| `hospital_total_count` | **GO (P0)** | 2024 | Tersedia lengkap di `SRC-RS` (`47` unit, rekonsiliasi 10 kab/kota = provinsi 100%). |
| `puskesmas_inpatient_count` | **GO (P0)** | 2024 | Tersedia lengkap di `SRC-PKM-RI` (`89` unit) setelah jembatan kode BPS → Kemendagri. |
| `puskesmas_non_inpatient_count` | **GO (P0)** | 2024 | Tersedia lengkap di `SRC-PKM-NRI` (`99` unit) setelah jembatan kode BPS → Kemendagri. |
| `puskesmas_total_count` | **GO (P0 Derived)** | 2024 | Penjumlahan saling lepas `puskesmas_inpatient_count + puskesmas_non_inpatient_count` (`188` unit), cocok 100% per kab/kota dengan `SRC-PKM-SPA`. |
| `facility_total_count` | **GO Scoped (P0 Derived)** | 2024 | Karena slug fasyankes gabungan 2023–2025 mengalami `HTTP 404`, indikator ini didefinisikan secara transparan sebagai **Fasilitas Kesehatan Utama (Rumah Sakit + Puskesmas Induk)** (`47 + 188 = 235` fasilitas). Klinik swasta/apotek tidak diklaim termasuk di dalamnya. |
| `population_count` | **GO (P0 Denominator)** | 2024 | Tersedia dalam satuan `jiwa` utuh di `SRC-POP-NAKES` (`4.045.858` jiwa). |
| `doctor_per_100k` | **GO Conditional (P0)** | **2024 saja** | Aktif pada 2024; otomatis `not_comparable` (`year_mismatch`) pada 2023 & 2025 karena belum ada denominator penduduk teraudit untuk kedua tahun tersebut. |
| `doctor_general_per_100k` | **GO Conditional (P0)** | **2024 saja** | Menghindari bias komposisi dokter gigi apabila analis ingin menilai khusus ketersediaan dokter layanan primer per 100.000 penduduk. |
| `facility_per_100k` | **GO Conditional (P1)** | **2024 saja** | Rasio Rumah Sakit + Puskesmas Induk per 100.000 penduduk tahun 2024; wajib disertai catatan geografis kepadatan penduduk rendah (contoh: Mahakam Ulu). |
| `puskesmas_accredited_share` | **DEFERRED (P1)** | — | File `SRC-ACC` hanya memuat ringkasan level provinsi (`188` puskesmas induk), belum memecah per kabupaten/kota. Tidak dipaksakan ke perbandingan daerah. |

## Pertanyaan Analitis Inti yang Dapat Dijawab Secara Sah

1. **Disparitas Kepadatan Tenaga Medis vs Beban Populasi (2024):** Bagaimana perbandingan ketersediaan tenaga medis kedokteran dan dokter umum per 100.000 penduduk antar 10 kabupaten/kota di Kalimantan Timur setelah perbedaan skala populasi dinormalisasi?
2. **Struktur Layanan Primer vs Rujukan Daerah (2024):** Bagaimana komposisi fasilitas kesehatan utama (Rumah Sakit vs Puskesmas Rawat Inap vs Puskesmas Non Rawat Inap) di wilayah perkotaan padat (Samarinda, Balikpapan, Bontang) dibandingkan wilayah pedalaman/perbatasan (Mahakam Ulu, Kutai Barat, Berau)?
3. **Audit Ketahanan Kualitas Data Publik:** Mengapa suatu angka tersedia, memiliki catatan rekonsiliasi (`warning`), tidak sepadan antar-tahun (`not_comparable`), atau diblokir penuh oleh pemeriksaan kualitas (`blocked`)?
