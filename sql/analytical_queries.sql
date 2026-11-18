-- Kaltim Data Trust — Analytical & Provenance SQL Queries

-- 1. Regional Comparison with Full Numerator/Denominator Lineage (2024 doctor_per_100k)
SELECT
    r.region_code,
    r.short_name AS wilayah,
    df.reference_year AS tahun,
    df.value_numeric AS dokter_per_100rb,
    df.quality_status,
    num_cf.value_numeric AS jumlah_tenaga_medis,
    num_cf.source_locator AS bukti_sel_dokter,
    num_run.source_sha256 AS sha256_dokter,
    den_cf.value_numeric AS jumlah_penduduk,
    den_cf.source_locator AS bukti_sel_penduduk,
    den_run.source_sha256 AS sha256_penduduk
FROM published_snapshots ps
JOIN snapshot_derived_facts sdf ON sdf.snapshot_id = ps.snapshot_id
JOIN derived_facts df ON df.derived_fact_id = sdf.derived_fact_id
JOIN regions r ON r.region_code = df.region_code
LEFT JOIN derived_fact_inputs num_edge
    ON num_edge.derived_fact_id = df.derived_fact_id AND num_edge.input_role = 'numerator'
LEFT JOIN curated_facts num_cf ON num_cf.fact_id = num_edge.source_fact_id
LEFT JOIN import_runs num_run ON num_run.run_id = num_cf.run_id
LEFT JOIN derived_fact_inputs den_edge
    ON den_edge.derived_fact_id = df.derived_fact_id AND den_edge.input_role = 'denominator'
LEFT JOIN curated_facts den_cf ON den_cf.fact_id = den_edge.source_fact_id
LEFT JOIN import_runs den_run ON den_run.run_id = den_cf.run_id
WHERE ps.status = 'active'
  AND df.metric_code = 'doctor_per_100k'
  AND df.reference_year = 2024
  AND r.region_level IN ('regency', 'city')
ORDER BY df.value_numeric DESC NULLS LAST;

-- 2. Reconciliation Assertion: Sum of 10 Regencies/Cities vs Official Province Row (DQ-011)
WITH active_curated AS (
    SELECT cf.*
    FROM published_snapshots ps
    JOIN snapshot_facts sf ON sf.snapshot_id = ps.snapshot_id
    JOIN curated_facts cf ON cf.fact_id = sf.fact_id
    WHERE ps.status = 'active'
),
regency_sums AS (
    SELECT
        ac.metric_code,
        ac.reference_year,
        COUNT(*) AS covered_regions,
        SUM(ac.value_numeric) AS sum_10_kab_kota
    FROM active_curated ac
    JOIN regions r ON r.region_code = ac.region_code
    WHERE r.region_level IN ('regency', 'city')
      AND ac.value_numeric IS NOT NULL
    GROUP BY ac.metric_code, ac.reference_year
),
province_official AS (
    SELECT
        ac.metric_code,
        ac.reference_year,
        ac.value_numeric AS nilai_baris_provinsi,
        ac.quality_status AS status_provinsi
    FROM active_curated ac
    WHERE ac.region_code = '64'
)
SELECT
    rs.metric_code,
    rs.reference_year,
    rs.covered_regions,
    rs.sum_10_kab_kota,
    po.nilai_baris_provinsi,
    (po.nilai_baris_provinsi - rs.sum_10_kab_kota) AS selisih,
    po.status_provinsi
FROM regency_sums rs
LEFT JOIN province_official po
    ON po.metric_code = rs.metric_code AND po.reference_year = rs.reference_year
ORDER BY rs.reference_year DESC, rs.metric_code ASC;
