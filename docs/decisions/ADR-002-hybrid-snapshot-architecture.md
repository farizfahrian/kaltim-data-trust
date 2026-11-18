# ADR-002: Arsitektur Hybrid Verified SQL Pipeline + Immutable Edge Snapshot

- **Date:** 2026-11-18
- **Status:** Accepted

## Context
Proyek ini membutuhkan dua kualitas sekaligus:
1. **Kedalaman rekayasa data & basis data relasional:** Bukti skema SQL (`sources`, `import_runs`, `curated_facts`, `derived_facts`, `derived_fact_inputs`, `quality_results`, `published_snapshots`), aturan validasi deterministik (`DQ-001`..`DQ-014`), transaksi publikasi atomik, dan kontrak REST API (`FastAPI` + OpenAPI).
2. **Keandalan demo publik 24/7 bagi perekrut/penilai teknis:** Layanan *free-tier* database/container sering mengalami *cold start* 30–50 detik atau kedaluwarsa saat ditinjau perekrut, sedangkan domain utama pemilik proyek (`farizfahrian.com`) berjalan di jaringan edge Cloudflare dengan biaya nol dan latensi rendah.

## Options Considered
1. Menyematkan seluruh proyek ke dalam repositori portofolio utama (`portfolio-nextjs`).
2. Menjalankan backend FastAPI + PostgreSQL berbayar/free-tier sebagai satu-satunya jalur pembacaan UI demo.
3. **Arsitektur Hybrid ("Verified ETL & SQL API + Signed Static Snapshot"):**
   - Repositori mandiri (`kaltim-data-trust`) yang berisi pipeline Python (`kdt`), skema SQLAlchemy 2.0 / PostgreSQL 16+, endpoint `FastAPI` (`/api/v1/*`), dan pengujian otomatis `pytest` (termasuk job PostgreSQL asli pada GitHub Actions CI dan `docker-compose.yml`).
   - Perintah CLI `kdt export-snapshot` yang mengekspor *Published Snapshot* yang lolos *Quality Gate* dari database SQL menjadi artefak JSON read-only ber-checksum SHA-256 dengan struktur identik terhadap respons `/api/v1/*`, disajikan oleh antarmuka **Astro + TypeScript** di Cloudflare Workers Static Assets.

## Decision
Memilih **Opsi 3**.

## Trade-offs & Consequences
- **Positif:** Repositori portofolio utama tetap ramping tanpa pembengkakan dependensi; repositori `kaltim-data-trust` menampilkan disiplin *data engineering* dan pengujian SQL secara utuh; demo langsung merespons instan (`<100ms`) tanpa risiko *cold start* atau serangan SSRF/SQLi di publik, selaras penuh dengan aturan PRD Section 11.1 & 12.3 (*API selalu menyajikan published snapshot*).
- **Konsekuensi:** Pembaruan data dilakukan secara terukur melalui eksekusi ulang pipeline `kdt` dan publikasi snapshot baru (sesuai desain MVP non-realtime pada PRD Section 1.4).
