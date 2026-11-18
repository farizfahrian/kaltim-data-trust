from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kdt.models import CuratedFact, ImportRun, QualityResult, QuarantinedRecord
from kdt.normalize import parse_numeric_value, parse_year
from kdt.parse import extract_source_records
from kdt.registry import sync_reference_tables
from kdt.sniff import SniffResult, sniff_file
from kdt.validate import INTEGRAL_UNITS, CandidateFact, QuarantineItem, RuleOutcome, run_quality_suite

MAPPING_VERSION = "1.0.0"
TRANSFORMATION_VERSION = "1.0.0"


@dataclass
class IngestOutcome:
    run_id: str
    source_id: str
    sha256: str
    actual_format: str
    status: str
    is_idempotent_reuse: bool
    is_dry_run: bool
    input_row_count: int
    output_row_count: int
    quarantined_row_count: int
    outcomes: list[RuleOutcome]


def ingest_file(
    session: Session,
    *,
    data_dir: Path,
    source_id: str,
    file_path: Path,
    override_year: int | None = None,
    dry_run: bool = False,
    custom_source_cfg: dict[str, Any] | None = None,
    is_synthetic: bool = False,
) -> IngestOutcome:
    resolver, sources_map, metrics_map, _ = sync_reference_tables(session, data_dir)

    if custom_source_cfg is not None:
        source_cfg = custom_source_cfg
    elif source_id in sources_map:
        source_cfg = sources_map[source_id]
    else:
        raise ValueError(f"Source ID tidak terdaftar di manifest: {source_id}")

    sniff: SniffResult = sniff_file(file_path, reported_format=source_cfg.get("reported_format"))

    # Idempotency check (FR-003, T-011)
    if not dry_run:
        existing_run = session.execute(
            select(ImportRun).where(
                ImportRun.source_id == source_id,
                ImportRun.source_sha256 == sniff.sha256,
                ImportRun.mapping_version == MAPPING_VERSION,
                ImportRun.transformation_version == TRANSFORMATION_VERSION,
            )
        ).scalar_one_or_none()

        if existing_run is not None:
            existing_q = (
                session.execute(select(QualityResult).where(QualityResult.run_id == existing_run.run_id))
                .scalars()
                .all()
            )
            outcomes = [
                RuleOutcome(
                    rule_code=qr.rule_code,
                    dimension=qr.dimension,
                    status=qr.status,
                    severity=qr.severity,
                    affected_count=qr.affected_count,
                    message=qr.message,
                )
                for qr in existing_q
            ]
            return IngestOutcome(
                run_id=existing_run.run_id,
                source_id=source_id,
                sha256=sniff.sha256,
                actual_format=existing_run.actual_format,
                status=existing_run.status,
                is_idempotent_reuse=True,
                is_dry_run=False,
                input_row_count=existing_run.input_row_count,
                output_row_count=existing_run.output_row_count,
                quarantined_row_count=existing_run.quarantined_row_count,
                outcomes=outcomes,
            )

    extraction = extract_source_records(
        file_path=file_path,
        actual_format=sniff.actual_format,
        source_cfg=source_cfg,
        override_year=override_year,
    )

    candidates: list[CandidateFact] = []
    quarantined: list[QuarantineItem] = []
    code_typo_warnings: list[str] = []
    code_system = source_cfg.get("region_code_system", "kemendagri")
    published_years = set(source_cfg.get("published_years") or [])

    for rec in extraction.records:
        # 1. Year check
        yr = parse_year(rec.raw_year)
        if yr is None:
            quarantined.append(
                QuarantineItem(
                    source_locator=rec.source_locator,
                    raw_region=str(rec.raw_region) if rec.raw_region is not None else None,
                    raw_year=str(rec.raw_year) if rec.raw_year is not None else None,
                    raw_metric=rec.metric_code,
                    raw_value=str(rec.raw_value) if rec.raw_value is not None else None,
                    rule_code="DQ-006",
                    reason_code="missing_or_invalid_year",
                    detail_message=f"Tahun referensi tidak valid atau kosong: {rec.raw_year!r}",
                )
            )
            continue

        # 2. Metric check
        if not rec.metric_code or rec.metric_code not in metrics_map:
            quarantined.append(
                QuarantineItem(
                    source_locator=rec.source_locator,
                    raw_region=str(rec.raw_region) if rec.raw_region is not None else None,
                    raw_year=str(yr),
                    raw_metric=rec.metric_code,
                    raw_value=str(rec.raw_value) if rec.raw_value is not None else None,
                    rule_code="DQ-001",
                    reason_code="unknown_metric_code",
                    detail_message=f"Metric code tidak dikenal: {rec.metric_code!r}",
                )
            )
            continue

        # 3. Region resolution (FR-004)
        res = resolver.resolve(rec.raw_code, rec.raw_region, code_system=code_system)
        if res.region_code is None or res.region_level is None:
            quarantined.append(
                QuarantineItem(
                    source_locator=rec.source_locator,
                    raw_region=f"{rec.raw_code or ''}:{rec.raw_region or ''}",
                    raw_year=str(yr),
                    raw_metric=rec.metric_code,
                    raw_value=str(rec.raw_value) if rec.raw_value is not None else None,
                    rule_code="DQ-003",
                    reason_code="UNMAPPED_REGION" if res.status == "unmapped" else "REGION_CODE_NAME_CONFLICT",
                    detail_message=res.warning or "Wilayah tidak dapat dipetakan.",
                )
            )
            continue

        if res.status == "matched_with_code_typo" and res.warning:
            code_typo_warnings.append(res.warning)

        # 4. Unit & multiplier check (FR-005)
        expected_unit = metrics_map[rec.metric_code]["unit"]
        raw_unit_clean = (rec.raw_unit or expected_unit).strip()
        unit_multiplier = Decimal("1")
        if raw_unit_clean.lower() == "ribu jiwa" and expected_unit.lower() == "jiwa":
            unit_multiplier = Decimal("1000")
            raw_unit_clean = "jiwa"

        require_int = expected_unit.lower() in INTEGRAL_UNITS
        parsed_num = parse_numeric_value(
            rec.raw_value,
            locale_policy=rec.locale_policy,
            require_integer=require_int,
            unit_multiplier=unit_multiplier,
        )

        if parsed_num.error_code is not None:
            rule = "DQ-005" if parsed_num.error_code == "non_integer_for_integral_metric" else "DQ-004"
            quarantined.append(
                QuarantineItem(
                    source_locator=rec.source_locator,
                    raw_region=res.region_code,
                    raw_year=str(yr),
                    raw_metric=rec.metric_code,
                    raw_value=str(rec.raw_value),
                    rule_code=rule,
                    reason_code=parsed_num.error_code,
                    detail_message=parsed_num.error_detail or "Kesalahan parsing nilai.",
                )
            )
            continue

        q_status = "validated" if parsed_num.value is not None else "warning"
        candidates.append(
            CandidateFact(
                region_code=res.region_code,
                region_level=res.region_level,
                reference_year=yr,
                metric_code=rec.metric_code,
                value_numeric=parsed_num.value,
                unit=raw_unit_clean,
                missing_reason=parsed_num.missing_reason,
                quality_status=q_status,
                source_locator=rec.source_locator,
                extra=rec.extra,
            )
        )

    outcomes, is_eligible = run_quality_suite(
        source_cfg=source_cfg,
        metrics_map=metrics_map,
        sniff=sniff,
        candidates=candidates,
        quarantined=quarantined,
        code_typo_warnings=code_typo_warnings,
        parser_notes=extraction.parser_notes,
    )

    # Filter out historical non-published years that were blocked or excluded from published curated output
    persisted_candidates = [
        c
        for c in candidates
        if (not published_years or c.reference_year in published_years)
    ]

    # Deterministic UUID5 from source + sha256 + mapping + transformation
    deterministic_seed = f"{source_id}:{sniff.sha256}:{MAPPING_VERSION}:{TRANSFORMATION_VERSION}"
    run_id = f"run-{uuid.uuid5(uuid.NAMESPACE_URL, deterministic_seed).hex[:16]}"
    final_status = "DRY_RUN" if dry_run else ("ELIGIBLE" if is_eligible else "QUARANTINED")

    if dry_run:
        return IngestOutcome(
            run_id=run_id,
            source_id=source_id,
            sha256=sniff.sha256,
            actual_format=sniff.actual_format,
            status=final_status,
            is_idempotent_reuse=False,
            is_dry_run=True,
            input_row_count=extraction.raw_row_count,
            output_row_count=len([c for c in persisted_candidates if c.quality_status != "blocked"]),
            quarantined_row_count=len(quarantined),
            outcomes=outcomes,
        )

    now = datetime.now(UTC)
    retrieved_str = source_cfg.get("retrieved_at_utc")
    retrieved_dt = (
        datetime.fromisoformat(retrieved_str.replace("Z", "+00:00")) if retrieved_str else now
    )

    run_row = ImportRun(
        run_id=run_id,
        source_id=source_id,
        source_sha256=sniff.sha256,
        actual_format=sniff.actual_format,
        file_size_bytes=sniff.size_bytes,
        mapping_version=MAPPING_VERSION,
        transformation_version=TRANSFORMATION_VERSION,
        retrieved_at=retrieved_dt,
        started_at=now,
        finished_at=now,
        status=final_status,
        input_row_count=extraction.raw_row_count,
        output_row_count=len([c for c in persisted_candidates if c.quality_status != "blocked"]),
        quarantined_row_count=len(quarantined),
        is_synthetic=is_synthetic,
    )
    session.add(run_row)
    session.flush()

    # Deduplicate persisted_candidates on business key before DB insert if DQ-002 caught duplicates
    inserted_keys: set[tuple[str, int, str]] = set()
    for c in persisted_candidates:
        bkey = (c.region_code, c.reference_year, c.metric_code)
        if bkey in inserted_keys:
            continue
        inserted_keys.add(bkey)
        fact_seed = f"{run_id}:{c.region_code}:{c.reference_year}:{c.metric_code}"
        fact_id = f"srcf-{uuid.uuid5(uuid.NAMESPACE_URL, fact_seed).hex[:16]}"
        session.add(
            CuratedFact(
                fact_id=fact_id,
                run_id=run_id,
                source_id=source_id,
                region_code=c.region_code,
                reference_year=c.reference_year,
                metric_code=c.metric_code,
                value_numeric=c.value_numeric,
                unit=c.unit,
                missing_reason=c.missing_reason,
                quality_status=c.quality_status,
                source_locator=c.source_locator,
                transformation_version=TRANSFORMATION_VERSION,
            )
        )

    for idx, q in enumerate(quarantined):
        q_seed = f"{run_id}:q:{idx}:{q.source_locator}:{q.reason_code}"
        session.add(
            QuarantinedRecord(
                quarantine_id=f"quar-{uuid.uuid5(uuid.NAMESPACE_URL, q_seed).hex[:16]}",
                run_id=run_id,
                source_id=source_id,
                source_locator=q.source_locator,
                raw_region=q.raw_region,
                raw_year=q.raw_year,
                raw_metric=q.raw_metric,
                raw_value=q.raw_value,
                rule_code=q.rule_code,
                reason_code=q.reason_code,
                detail_message=q.detail_message,
            )
        )

    for qr in outcomes:
        qr_seed = f"{run_id}:{qr.rule_code}"
        session.add(
            QualityResult(
                quality_result_id=f"qr-{uuid.uuid5(uuid.NAMESPACE_URL, qr_seed).hex[:16]}",
                run_id=run_id,
                rule_code=qr.rule_code,
                dimension=qr.dimension,
                status=qr.status,
                severity=qr.severity,
                affected_count=qr.affected_count,
                message=qr.message,
                detail_json=qr.to_detail_json(),
            )
        )

    session.flush()
    return IngestOutcome(
        run_id=run_id,
        source_id=source_id,
        sha256=sniff.sha256,
        actual_format=sniff.actual_format,
        status=final_status,
        is_idempotent_reuse=False,
        is_dry_run=False,
        input_row_count=extraction.raw_row_count,
        output_row_count=run_row.output_row_count,
        quarantined_row_count=len(quarantined),
        outcomes=outcomes,
    )
