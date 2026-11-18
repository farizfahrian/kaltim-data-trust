from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_format: Mapped[str] = mapped_column(String(32), nullable=False, default="Not verified")
    actual_format: Mapped[str] = mapped_column(String(32), nullable=False, default="Not verified")
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license_status: Mapped[str] = mapped_column(Text, nullable=False, default="undetermined")
    publication_allowed: Mapped[str] = mapped_column(String(64), nullable=False, default="undetermined")
    region_code_system: Mapped[str] = mapped_column(String(64), nullable=False, default="kemendagri")
    known_limitations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class Region(Base):
    __tablename__ = "regions"

    region_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    region_name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str] = mapped_column(String(128), nullable=False)
    region_level: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_region_code: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("regions.region_code"), nullable=True
    )
    kemendagri_numeric: Mapped[str] = mapped_column(String(16), nullable=False)
    bps_numeric: Mapped[str] = mapped_column(String(16), nullable=False)
    code_system: Mapped[str] = mapped_column(String(64), nullable=False)
    code_valid_from: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("region_level IN ('province', 'regency', 'city')", name="ck_region_level"),
    )


class Metric(Base):
    __tablename__ = "metrics"

    metric_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    short_label: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="P0")
    operational_definition: Mapped[str] = mapped_column(Text, nullable=False)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    numerator_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    denominator_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aggregation_rule: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_periods_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    limitations: Mapped[str] = mapped_column(Text, nullable=False, default="")
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")

    __table_args__ = (
        CheckConstraint("kind IN ('source', 'derived')", name="ck_metric_kind"),
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.source_id"), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_format: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False)
    transformation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "source_sha256",
            "mapping_version",
            "transformation_version",
            name="uq_import_run_deterministic",
        ),
        CheckConstraint(
            "status IN ('RECEIVED', 'PROFILED', 'NORMALIZED', 'VALIDATED', 'ELIGIBLE', 'PUBLISHED', 'QUARANTINED', 'HELD', 'DRY_RUN')",
            name="ck_import_run_status",
        ),
    )


class CuratedFact(Base):
    __tablename__ = "curated_facts"

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("import_runs.run_id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.source_id"), nullable=False)
    region_code: Mapped[str] = mapped_column(String(16), ForeignKey("regions.region_code"), nullable=False)
    reference_year: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_code: Mapped[str] = mapped_column(String(64), ForeignKey("metrics.metric_code"), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    missing_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)
    transformation_version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "region_code", "reference_year", "metric_code", name="uq_curated_fact_key"),
        CheckConstraint(
            "quality_status IN ('validated', 'warning', 'blocked', 'not_checked')",
            name="ck_curated_quality_status",
        ),
        CheckConstraint(
            "(value_numeric IS NOT NULL) OR (missing_reason IS NOT NULL)",
            name="ck_curated_null_has_reason",
        ),
        Index("ix_curated_lookup", "region_code", "reference_year", "metric_code"),
    )


class QuarantinedRecord(Base):
    __tablename__ = "quarantined_records"

    quarantine_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("import_runs.run_id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.source_id"), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_year: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_metric: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_message: Mapped[str] = mapped_column(Text, nullable=False)


class DerivationRun(Base):
    __tablename__ = "derivation_runs"

    derivation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("input_signature", "methodology_version", name="uq_derivation_signature"),
    )


class DerivedFact(Base):
    __tablename__ = "derived_facts"

    derived_fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    derivation_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("derivation_runs.derivation_run_id"), nullable=False
    )
    region_code: Mapped[str] = mapped_column(String(16), ForeignKey("regions.region_code"), nullable=False)
    reference_year: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_code: Mapped[str] = mapped_column(String(64), ForeignKey("metrics.metric_code"), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    missing_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "derivation_run_id", "region_code", "reference_year", "metric_code", name="uq_derived_fact_key"
        ),
        CheckConstraint(
            "quality_status IN ('validated', 'warning', 'blocked', 'not_computable', 'not_comparable')",
            name="ck_derived_quality_status",
        ),
        CheckConstraint(
            "(value_numeric IS NOT NULL) OR (missing_reason IS NOT NULL)",
            name="ck_derived_null_has_reason",
        ),
        Index("ix_derived_lookup", "region_code", "reference_year", "metric_code"),
    )


class DerivedFactInput(Base):
    __tablename__ = "derived_fact_inputs"

    derived_fact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("derived_facts.derived_fact_id"), primary_key=True
    )
    source_fact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("curated_facts.fact_id"), primary_key=True
    )
    input_role: Mapped[str] = mapped_column(String(32), primary_key=True)

    __table_args__ = (
        CheckConstraint("input_role IN ('numerator', 'denominator', 'supporting')", name="ck_input_role"),
    )


class QualityResult(Base):
    __tablename__ = "quality_results"

    quality_result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("import_runs.run_id"), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    affected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        CheckConstraint("status IN ('pass', 'warn', 'fail')", name="ck_quality_status"),
        CheckConstraint("severity IN ('critical', 'warning', 'info')", name="ck_quality_severity"),
    )


class PublishedSnapshot(Base):
    __tablename__ = "published_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    release_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'superseded', 'rolled_back')", name="ck_snapshot_status"),
    )


class SnapshotFact(Base):
    __tablename__ = "snapshot_facts"

    snapshot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("published_snapshots.snapshot_id"), primary_key=True
    )
    fact_id: Mapped[str] = mapped_column(String(64), ForeignKey("curated_facts.fact_id"), primary_key=True)


class SnapshotDerivedFact(Base):
    __tablename__ = "snapshot_derived_facts"

    snapshot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("published_snapshots.snapshot_id"), primary_key=True
    )
    derived_fact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("derived_facts.derived_fact_id"), primary_key=True
    )
