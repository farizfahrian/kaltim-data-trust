# ADR-001: Penyesuaian Sumber Fasilitas Kesehatan 2024 & Jembatan Kode Wilayah Kemendagri–BPS

- **Date:** 2026-11-18
- **Status:** Accepted

## Context
Pada tahap **Data Discovery Gate**, kandidat URL awal `jumlah-fasilitas-pelayanan-kesehatan-di-provinsi-kalimantan-timur-tahun-2023-2025` pada Satu Data Kaltim mengembalikan `HTTP 404`. Audit menyeluruh terhadap 206 paket Dinas Kesehatan Provinsi Kalimantan Timur menemukan bahwa data fasilitas tahun 2024 dipublikasikan dalam tiga dataset terpisah yang aktif dan dilengkapi `Metadata Variabel` serta `Metadata Indikator`:
- `jumlah-rumah-sakit-tahun-2024` (`SRC-RS`) — memakai kode wilayah **Kemendagri**.
- `jumlah-puskesmas-rawat-inap-tahun-2024` (`SRC-PKM-RI`) — memakai kode wilayah **BPS**.
- `jumlah-puskesmas-non-rawat-inap-tahun-2024` (`SRC-PKM-NRI`) — memakai kode wilayah **BPS**.

Selain itu, pada `SRC-DOC` (`1-dokter.xlsx`), baris Kota Bontang tertulis berkode `6472.0` (duplikat kode Kota Samarinda).

## Options Considered
1. Menggunakan fixture sintetis penuh karena satu URL kandidat awal mengembalikan `404`.
2. Mengintegrasikan ketiga dataset fasilitas resmi tahun 2024 (`SRC-RS`, `SRC-PKM-RI`, `SRC-PKM-NRI`) dengan mendefinisikan cakupan metrik secara jujur sebagai **Fasilitas Kesehatan Utama (Rumah Sakit + Puskesmas Induk)** serta membangun tabel master pemadanan eksplisit Kemendagri ↔ BPS + validasi silang nama wilayah.

## Decision
Memilih **Opsi 2**.

## Trade-offs & Consequences
- **Positif:** Menggunakan 100% data publik resmi tahun 2024 yang nyata dan terverifikasi silang (`89 + 99 = 188` Puskesmas cocok dengan dataset SPA & Akreditasi; `47` Rumah Sakit cocok dengan total provinsi), sekaligus mendemonstrasikan penyelesaian masalah interoperabilitas nyata di sektor publik Indonesia (sinkronisasi kode Kemendagri vs BPS dan deteksi salah ketik kode wilayah).
- **Batasan yang didokumentasikan:** Indikator `facility_total_count` diberi label tegas **Fasilitas Kesehatan Utama (RS & Puskesmas)** agar pembaca tidak mengira klinik pratama swasta atau apotek ikut dihitung.
