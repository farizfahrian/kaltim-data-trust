-- Kaltim Data Trust — PostgreSQL 16+ Target Relational Schema
-- Separates single-source curated facts from multi-source derived facts with full input lineage.

CREATE TABLE IF NOT EXISTS sources (
    source_id VARCHAR(64) PRIMARY KEY,
    title TEXT NOT NULL,
    publisher TEXT NOT NULL,
    dataset_url TEXT NOT NULL,
    resource_url TEXT,
    reported_format VARCHAR(32) NOT NULL DEFAULT 'Not verified',
    actual_format VARCHAR(32) NOT NULL DEFAULT 'Not verified',
    expected_sha256 CHAR(64),
    license_status TEXT NOT NULL DEFAULT 'undetermined',
    publication_allowed VARCHAR(64) NOT NULL DEFAULT 'undetermined',
    region_code_system VARCHAR(64) NOT NULL DEFAULT 'kemendagri',
    known_limitations_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS regions (
    region_code VARCHAR(16) PRIMARY KEY,
    region_name VARCHAR(128) NOT NULL,
    short_name VARCHAR(128) NOT NULL,
    region_level VARCHAR(32) NOT NULL CHECK (region_level IN ('province', 'regency', 'city')),
    parent_region_code VARCHAR(16) REFERENCES regions(region_code),
    kemendagri_numeric VARCHAR(16) NOT NULL,
    bps_numeric VARCHAR(16) NOT NULL,
    code_system VARCHAR(64) NOT NULL,
    code_valid_from VARCHAR(32)
);

CREATE TABLE IF NOT EXISTS metrics (
    metric_code VARCHAR(64) PRIMARY KEY,
    label TEXT NOT NULL,
    short_label VARCHAR(128) NOT NULL,
    unit VARCHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('source', 'derived')),
    priority VARCHAR(8) NOT NULL DEFAULT 'P0',
    operational_definition TEXT NOT NULL,
    formula TEXT,
    numerator_source VARCHAR(128),
    denominator_source VARCHAR(128),
    aggregation_rule VARCHAR(64) NOT NULL,
    valid_periods_json TEXT NOT NULL DEFAULT '[]',
    limitations TEXT NOT NULL DEFAULT '',
    methodology_version VARCHAR(32) NOT NULL DEFAULT '1.0.0'
);

CREATE TABLE IF NOT EXISTS import_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id),
    source_sha256 CHAR(64) NOT NULL,
    actual_format VARCHAR(32) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    mapping_version VARCHAR(32) NOT NULL,
    transformation_version VARCHAR(32) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL CHECK (
        status IN ('RECEIVED', 'PROFILED', 'NORMALIZED', 'VALIDATED', 'ELIGIBLE', 'PUBLISHED', 'QUARANTINED', 'HELD', 'DRY_RUN')
    ),
    input_row_count INTEGER NOT NULL DEFAULT 0,
    output_row_count INTEGER NOT NULL DEFAULT 0,
    quarantined_row_count INTEGER NOT NULL DEFAULT 0,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_import_run_deterministic UNIQUE (source_id, source_sha256, mapping_version, transformation_version)
);

CREATE TABLE IF NOT EXISTS curated_facts (
    fact_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES import_runs(run_id),
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id),
    region_code VARCHAR(16) NOT NULL REFERENCES regions(region_code),
    reference_year INTEGER NOT NULL,
    metric_code VARCHAR(64) NOT NULL REFERENCES metrics(metric_code),
    value_numeric NUMERIC(18, 4),
    unit VARCHAR(64) NOT NULL,
    missing_reason VARCHAR(64),
    quality_status VARCHAR(32) NOT NULL CHECK (
        quality_status IN ('validated', 'warning', 'blocked', 'not_checked')
    ),
    source_locator VARCHAR(255) NOT NULL,
    transformation_version VARCHAR(32) NOT NULL,
    CONSTRAINT uq_curated_fact_key UNIQUE (run_id, region_code, reference_year, metric_code),
    CONSTRAINT ck_curated_null_has_reason CHECK (value_numeric IS NOT NULL OR missing_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_curated_lookup ON curated_facts (region_code, reference_year, metric_code);

CREATE TABLE IF NOT EXISTS quarantined_records (
    quarantine_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES import_runs(run_id),
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id),
    source_locator VARCHAR(255) NOT NULL,
    raw_region VARCHAR(255),
    raw_year VARCHAR(64),
    raw_metric VARCHAR(255),
    raw_value VARCHAR(255),
    rule_code VARCHAR(32) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    detail_message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS derivation_runs (
    derivation_run_id VARCHAR(64) PRIMARY KEY,
    input_signature CHAR(64) NOT NULL,
    methodology_version VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL,
    CONSTRAINT uq_derivation_signature UNIQUE (input_signature, methodology_version)
);

CREATE TABLE IF NOT EXISTS derived_facts (
    derived_fact_id VARCHAR(64) PRIMARY KEY,
    derivation_run_id VARCHAR(64) NOT NULL REFERENCES derivation_runs(derivation_run_id),
    region_code VARCHAR(16) NOT NULL REFERENCES regions(region_code),
    reference_year INTEGER NOT NULL,
    metric_code VARCHAR(64) NOT NULL REFERENCES metrics(metric_code),
    value_numeric NUMERIC(18, 4),
    unit VARCHAR(64) NOT NULL,
    missing_reason VARCHAR(64),
    quality_status VARCHAR(32) NOT NULL CHECK (
        quality_status IN ('validated', 'warning', 'blocked', 'not_computable', 'not_comparable')
    ),
    methodology_version VARCHAR(32) NOT NULL,
    CONSTRAINT uq_derived_fact_key UNIQUE (derivation_run_id, region_code, reference_year, metric_code),
    CONSTRAINT ck_derived_null_has_reason CHECK (value_numeric IS NOT NULL OR missing_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_derived_lookup ON derived_facts (region_code, reference_year, metric_code);

CREATE TABLE IF NOT EXISTS derived_fact_inputs (
    derived_fact_id VARCHAR(64) NOT NULL REFERENCES derived_facts(derived_fact_id),
    source_fact_id VARCHAR(64) NOT NULL REFERENCES curated_facts(fact_id),
    input_role VARCHAR(32) NOT NULL CHECK (input_role IN ('numerator', 'denominator', 'supporting')),
    PRIMARY KEY (derived_fact_id, source_fact_id, input_role)
);

CREATE TABLE IF NOT EXISTS quality_results (
    quality_result_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES import_runs(run_id),
    rule_code VARCHAR(32) NOT NULL,
    dimension VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    affected_count INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL,
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS published_snapshots (
    snapshot_id VARCHAR(64) PRIMARY KEY,
    published_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('active', 'superseded', 'rolled_back')),
    release_note TEXT,
    demo_data BOOLEAN NOT NULL DEFAULT FALSE
);

-- Enforce invariant: at most ONE active snapshot at any point in time
CREATE UNIQUE INDEX IF NOT EXISTS uq_single_active_snapshot
    ON published_snapshots ((status))
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS snapshot_facts (
    snapshot_id VARCHAR(64) NOT NULL REFERENCES published_snapshots(snapshot_id),
    fact_id VARCHAR(64) NOT NULL REFERENCES curated_facts(fact_id),
    PRIMARY KEY (snapshot_id, fact_id)
);

CREATE TABLE IF NOT EXISTS snapshot_derived_facts (
    snapshot_id VARCHAR(64) NOT NULL REFERENCES published_snapshots(snapshot_id),
    derived_fact_id VARCHAR(64) NOT NULL REFERENCES derived_facts(derived_fact_id),
    PRIMARY KEY (snapshot_id, derived_fact_id)
);
