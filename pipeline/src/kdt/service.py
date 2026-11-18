from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kdt.models import (
    CuratedFact,
    DerivedFact,
    DerivedFactInput,
    ImportRun,
    Metric,
    PublishedSnapshot,
    QualityResult,
    QuarantinedRecord,
    Region,
    SnapshotDerivedFact,
    SnapshotFact,
    Source,
)


def _num_to_float_or_int(val: Decimal | None, unit: str) -> int | float | None:
    if val is None:
        return None
    if val == val.to_integral_value() and "/" not in unit and "%" not in unit:
        return int(val)
    return float(val)


class UnknownEntityError(KeyError):
    pass


def get_active_snapshot(session: Session) -> PublishedSnapshot | None:
    return session.execute(
        select(PublishedSnapshot)
        .where(PublishedSnapshot.status == "active")
        .order_by(PublishedSnapshot.published_at.desc())
    ).scalar_one_or_none()


def list_datasets(session: Session) -> list[dict[str, Any]]:
    sources = session.execute(select(Source).order_by(Source.source_id.asc())).scalars().all()
    runs = (
        session.execute(select(ImportRun).order_by(ImportRun.started_at.desc())).scalars().all()
    )
    latest_run_by_src: dict[str, ImportRun] = {}
    for r in runs:
        if r.source_id not in latest_run_by_src:
            latest_run_by_src[r.source_id] = r

    out = []
    for s in sources:
        r = latest_run_by_src.get(s.source_id)
        q_status = "not_checked"
        pass_cnt = warn_cnt = fail_cnt = 0
        if r is not None:
            q_rows = (
                session.execute(select(QualityResult).where(QualityResult.run_id == r.run_id))
                .scalars()
                .all()
            )
            pass_cnt = sum(1 for q in q_rows if q.status == "pass")
            warn_cnt = sum(1 for q in q_rows if q.status == "warn")
            fail_cnt = sum(1 for q in q_rows if q.status == "fail")
            if r.status == "QUARANTINED" or fail_cnt > 0:
                q_status = "blocked"
            elif warn_cnt > 0:
                q_status = "warning"
            else:
                q_status = "validated"

        out.append(
            {
                "source_id": s.source_id,
                "title": s.title,
                "publisher": s.publisher,
                "dataset_url": s.dataset_url,
                "resource_url": s.resource_url,
                "reported_format": s.reported_format,
                "actual_format": s.actual_format,
                "region_code_system": s.region_code_system,
                "license_status": s.license_status,
                "publication_allowed": s.publication_allowed,
                "quality_status": q_status,
                "latest_run": (
                    {
                        "run_id": r.run_id,
                        "status": r.status,
                        "source_sha256": r.source_sha256,
                        "file_size_bytes": r.file_size_bytes,
                        "retrieved_at": r.retrieved_at.isoformat(),
                        "input_row_count": r.input_row_count,
                        "output_row_count": r.output_row_count,
                        "quarantined_row_count": r.quarantined_row_count,
                        "rules_summary": {
                            "pass": pass_cnt,
                            "warn": warn_cnt,
                            "fail": fail_cnt,
                        },
                    }
                    if r
                    else None
                ),
                "known_limitations": json.loads(s.known_limitations_json),
            }
        )
    return out


def get_dataset_detail(session: Session, source_id: str) -> dict[str, Any]:
    src = session.get(Source, source_id)
    if src is None:
        raise UnknownEntityError(f"Dataset '{source_id}' tidak ditemukan.")

    datasets = list_datasets(session)
    base = next(d for d in datasets if d["source_id"] == source_id)
    runs = (
        session.execute(
            select(ImportRun)
            .where(ImportRun.source_id == source_id)
            .order_by(ImportRun.started_at.desc())
        )
        .scalars()
        .all()
    )
    latest_run = runs[0] if runs else None
    quality_rules = []
    quarantined_samples = []
    if latest_run:
        q_rows = (
            session.execute(
                select(QualityResult)
                .where(QualityResult.run_id == latest_run.run_id)
                .order_by(QualityResult.rule_code.asc())
            )
            .scalars()
            .all()
        )
        quality_rules = [
            {
                "rule_code": q.rule_code,
                "dimension": q.dimension,
                "status": q.status,
                "severity": q.severity,
                "affected_count": q.affected_count,
                "message": q.message,
                "details": json.loads(q.detail_json),
            }
            for q in q_rows
        ]
        q_recs = (
            session.execute(
                select(QuarantinedRecord)
                .where(QuarantinedRecord.run_id == latest_run.run_id)
                .limit(20)
            )
            .scalars()
            .all()
        )
        quarantined_samples = [
            {
                "source_locator": qr.source_locator,
                "raw_region": qr.raw_region,
                "raw_year": qr.raw_year,
                "raw_metric": qr.raw_metric,
                "rule_code": qr.rule_code,
                "reason_code": qr.reason_code,
                "detail_message": qr.detail_message,
            }
            for qr in q_recs
        ]

    base["quality_rules"] = quality_rules
    base["quarantined_samples"] = quarantined_samples
    base["run_history"] = [
        {
            "run_id": r.run_id,
            "status": r.status,
            "source_sha256": r.source_sha256,
            "started_at": r.started_at.isoformat(),
            "output_row_count": r.output_row_count,
            "quarantined_row_count": r.quarantined_row_count,
        }
        for r in runs
    ]
    return base


def list_metrics(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(select(Metric).order_by(Metric.metric_code.asc())).scalars().all()
    return [_serialize_metric(m) for m in rows]


def get_metric_detail(session: Session, metric_code: str) -> dict[str, Any]:
    m = session.get(Metric, metric_code)
    if m is None:
        raise UnknownEntityError(f"Metric '{metric_code}' tidak dikenal.")
    return _serialize_metric(m)


def _serialize_metric(m: Metric) -> dict[str, Any]:
    sources = []
    for part in (m.numerator_source, m.denominator_source):
        if part:
            for item in part.split(","):
                s_clean = item.strip()
                if s_clean and s_clean not in sources:
                    sources.append(s_clean)
    return {
        "metric_code": m.metric_code,
        "label": m.label,
        "short_label": m.short_label,
        "unit": m.unit,
        "kind": m.kind,
        "priority": m.priority,
        "operational_definition": m.operational_definition,
        "formula": m.formula,
        "numerator_source": m.numerator_source,
        "denominator_source": m.denominator_source,
        "sources": sources,
        "aggregation_rule": m.aggregation_rule,
        "valid_periods": json.loads(m.valid_periods_json),
        "limitations": m.limitations,
        "methodology_version": m.methodology_version,
    }


def list_regions(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(select(Region).order_by(Region.region_code.asc())).scalars().all()
    return [
        {
            "region_code": r.region_code,
            "region_name": r.region_name,
            "short_name": r.short_name,
            "region_level": r.region_level,
            "parent_region_code": r.parent_region_code,
            "kemendagri_numeric": r.kemendagri_numeric,
            "bps_numeric": r.bps_numeric,
            "code_system": r.code_system,
            "code_valid_from": r.code_valid_from,
        }
        for r in rows
    ]


def get_snapshot_observations(
    session: Session,
    *,
    metric_code: str | None = None,
    year: int | None = None,
    region_code: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    if metric_code is not None and session.get(Metric, metric_code) is None:
        raise UnknownEntityError(f"Metric '{metric_code}' tidak dikenal.")
    if region_code is not None and session.get(Region, region_code) is None:
        raise UnknownEntityError(f"Region '{region_code}' tidak dikenal.")

    snap = get_active_snapshot(session)
    if snap is None:
        return {
            "meta": {"snapshot_id": None, "available_years": [], "total": 0},
            "data": [],
        }

    regions_by_code = {
        r.region_code: r for r in session.execute(select(Region)).scalars().all()
    }
    metrics_by_code = {
        m.metric_code: m for m in session.execute(select(Metric)).scalars().all()
    }

    c_facts = (
        session.execute(
            select(CuratedFact)
            .join(SnapshotFact, SnapshotFact.fact_id == CuratedFact.fact_id)
            .where(SnapshotFact.snapshot_id == snap.snapshot_id)
        )
        .scalars()
        .all()
    )
    d_facts = (
        session.execute(
            select(DerivedFact)
            .join(SnapshotDerivedFact, SnapshotDerivedFact.derived_fact_id == DerivedFact.derived_fact_id)
            .where(SnapshotDerivedFact.snapshot_id == snap.snapshot_id)
        )
        .scalars()
        .all()
    )

    unified: list[dict[str, Any]] = []
    available_years_for_metric: set[int] = set()

    for cf in c_facts:
        if metric_code and cf.metric_code != metric_code:
            continue
        if cf.value_numeric is not None and cf.quality_status in {"validated", "warning"}:
            available_years_for_metric.add(cf.reference_year)
        if year is not None and cf.reference_year != year:
            continue
        if region_code and cf.region_code != region_code:
            continue
        reg = regions_by_code[cf.region_code]
        m = metrics_by_code[cf.metric_code]
        unified.append(
            {
                "observation_id": cf.fact_id,
                "region_code": cf.region_code,
                "region_name": reg.region_name,
                "short_name": reg.short_name,
                "region_level": reg.region_level,
                "year": cf.reference_year,
                "metric_code": cf.metric_code,
                "metric_label": m.label,
                "value": _num_to_float_or_int(cf.value_numeric, cf.unit),
                "unit": cf.unit,
                "quality_status": cf.quality_status,
                "missing_reason": cf.missing_reason,
                "sources": [cf.source_id],
                "source_locator": cf.source_locator,
                "lineage_url": f"/api/v1/lineage/{cf.fact_id}",
            }
        )

    for df in d_facts:
        if metric_code and df.metric_code != metric_code:
            continue
        if df.value_numeric is not None and df.quality_status in {"validated", "warning"}:
            available_years_for_metric.add(df.reference_year)
        if year is not None and df.reference_year != year:
            continue
        if region_code and df.region_code != region_code:
            continue
        reg = regions_by_code[df.region_code]
        m = _serialize_metric(metrics_by_code[df.metric_code])
        unified.append(
            {
                "observation_id": df.derived_fact_id,
                "region_code": df.region_code,
                "region_name": reg.region_name,
                "short_name": reg.short_name,
                "region_level": reg.region_level,
                "year": df.reference_year,
                "metric_code": df.metric_code,
                "metric_label": m["label"],
                "value": _num_to_float_or_int(df.value_numeric, df.unit),
                "unit": df.unit,
                "quality_status": df.quality_status,
                "missing_reason": df.missing_reason,
                "sources": m["sources"],
                "source_locator": "derived",
                "lineage_url": f"/api/v1/lineage/{df.derived_fact_id}",
            }
        )

    unified.sort(key=lambda x: (x["year"], x["metric_code"], x["region_code"]))
    safe_limit = max(1, min(limit, 500))
    paged = unified[offset : offset + safe_limit]

    return {
        "meta": {
            "snapshot_id": snap.snapshot_id,
            "published_at": snap.published_at.isoformat(),
            "demo_data": snap.demo_data,
            "available_years": sorted(available_years_for_metric),
            "total": len(unified),
            "limit": safe_limit,
            "offset": offset,
        },
        "data": paged,
    }


def get_overview(
    session: Session,
    *,
    metric_code: str = "doctor_per_100k",
    year: int = 2024,
) -> dict[str, Any]:
    m_obj = session.get(Metric, metric_code)
    if m_obj is None:
        raise UnknownEntityError(f"Metric '{metric_code}' tidak dikenal.")
    m_meta = _serialize_metric(m_obj)

    obs_res = get_snapshot_observations(session, metric_code=metric_code, year=year, limit=500)
    snap = get_active_snapshot(session)

    all_regions = (
        session.execute(
            select(Region)
            .where(Region.region_level.in_(["regency", "city"]))
            .order_by(Region.region_code.asc())
        )
        .scalars()
        .all()
    )

    by_reg = {item["region_code"]: item for item in obs_res["data"]}
    prov_obs = by_reg.get("64")

    regency_rows: list[dict[str, Any]] = []
    for r in all_regions:
        existing = by_reg.get(r.region_code)
        if existing is not None:
            regency_rows.append(existing)
        else:
            # Explicit missing region representation (FR-008, T-014)
            reason = (
                "year_mismatch"
                if (m_meta["kind"] == "derived" and year not in m_meta["valid_periods"])
                else "missing_in_source"
            )
            status = "not_comparable" if reason == "year_mismatch" else "warning"
            regency_rows.append(
                {
                    "observation_id": None,
                    "region_code": r.region_code,
                    "region_name": r.region_name,
                    "short_name": r.short_name,
                    "region_level": r.region_level,
                    "year": year,
                    "metric_code": metric_code,
                    "metric_label": m_meta["label"],
                    "value": None,
                    "unit": m_meta["unit"],
                    "quality_status": status,
                    "missing_reason": reason,
                    "sources": m_meta["sources"],
                    "source_locator": None,
                    "lineage_url": None,
                }
            )

    eligible_vals = [
        row["value"]
        for row in regency_rows
        if row["value"] is not None and row["quality_status"] in {"validated", "warning"}
    ]
    min_row = min(
        (r for r in regency_rows if r["value"] is not None and r["quality_status"] in {"validated", "warning"}),
        key=lambda x: x["value"],
        default=None,
    )
    max_row = max(
        (r for r in regency_rows if r["value"] is not None and r["quality_status"] in {"validated", "warning"}),
        key=lambda x: x["value"],
        default=None,
    )

    return {
        "meta": {
            "metric_code": m_meta["metric_code"],
            "metric_label": m_meta["label"],
            "short_label": m_meta["short_label"],
            "unit": m_meta["unit"],
            "kind": m_meta["kind"],
            "formula": m_meta["formula"],
            "operational_definition": m_meta["operational_definition"],
            "limitations": m_meta["limitations"],
            "year": year,
            "available_years": obs_res["meta"]["available_years"],
            "snapshot_id": snap.snapshot_id if snap else None,
            "sources": m_meta["sources"],
            "methodology_version": m_meta["methodology_version"],
            "published_at": snap.published_at.isoformat() if snap else None,
            "demo_data": snap.demo_data if snap else False,
        },
        "summary": {
            "total_target_regions": len(all_regions),
            "valid_region_count": len(eligible_vals),
            "missing_or_blocked_count": len(all_regions) - len(eligible_vals),
            "province_observation": prov_obs,
            "min_region": (
                {
                    "region_code": min_row["region_code"],
                    "short_name": min_row["short_name"],
                    "value": min_row["value"],
                }
                if min_row
                else None
            ),
            "max_region": (
                {
                    "region_code": max_row["region_code"],
                    "short_name": max_row["short_name"],
                    "value": max_row["value"],
                }
                if max_row
                else None
            ),
        },
        "data": regency_rows,
    }


def get_region_detail(session: Session, region_code: str, year: int = 2024) -> dict[str, Any]:
    reg = session.get(Region, region_code)
    if reg is None:
        raise UnknownEntityError(f"Region '{region_code}' tidak ditemukan.")

    obs_all_years = get_snapshot_observations(session, region_code=region_code, limit=500)
    prov_obs_yr = get_snapshot_observations(session, region_code="64", year=year, limit=500)
    prov_by_metric = {o["metric_code"]: o for o in prov_obs_yr["data"]}

    metrics = list_metrics(session)
    by_m_yr = {(o["metric_code"], o["year"]): o for o in obs_all_years["data"]}

    year_metrics = []
    for m in metrics:
        obs = by_m_yr.get((m["metric_code"], year))
        p_obs = prov_by_metric.get(m["metric_code"])
        year_metrics.append(
            {
                "metric_code": m["metric_code"],
                "label": m["label"],
                "short_label": m["short_label"],
                "unit": m["unit"],
                "kind": m["kind"],
                "value": obs["value"] if obs else None,
                "quality_status": obs["quality_status"] if obs else "not_comparable",
                "missing_reason": obs["missing_reason"] if obs else "missing_in_source",
                "observation_id": obs["observation_id"] if obs else None,
                "lineage_url": obs["lineage_url"] if obs else None,
                "province_value": p_obs["value"] if p_obs else None,
                "province_quality_status": p_obs["quality_status"] if p_obs else None,
                "sources": m["sources"],
                "formula": m["formula"],
            }
        )

    return {
        "region": {
            "region_code": reg.region_code,
            "region_name": reg.region_name,
            "short_name": reg.short_name,
            "region_level": reg.region_level,
            "kemendagri_numeric": reg.kemendagri_numeric,
            "bps_numeric": reg.bps_numeric,
            "code_system": reg.code_system,
        },
        "year": year,
        "metrics": year_metrics,
        "series": obs_all_years["data"],
    }


def get_quality_run_report(session: Session, run_id: str) -> dict[str, Any]:
    run = session.get(ImportRun, run_id)
    if run is None:
        raise UnknownEntityError(f"Run '{run_id}' tidak ditemukan.")
    src = session.get(Source, run.source_id)
    rules = (
        session.execute(
            select(QualityResult)
            .where(QualityResult.run_id == run_id)
            .order_by(QualityResult.rule_code.asc())
        )
        .scalars()
        .all()
    )
    q_recs = (
        session.execute(select(QuarantinedRecord).where(QuarantinedRecord.run_id == run_id).limit(25))
        .scalars()
        .all()
    )
    return {
        "run_id": run.run_id,
        "source_id": run.source_id,
        "source_title": src.title if src else run.source_id,
        "status": run.status,
        "source_sha256": run.source_sha256,
        "actual_format": run.actual_format,
        "file_size_bytes": run.file_size_bytes,
        "mapping_version": run.mapping_version,
        "transformation_version": run.transformation_version,
        "retrieved_at": run.retrieved_at.isoformat(),
        "started_at": run.started_at.isoformat(),
        "input_row_count": run.input_row_count,
        "output_row_count": run.output_row_count,
        "quarantined_row_count": run.quarantined_row_count,
        "is_synthetic": run.is_synthetic,
        "rules": [
            {
                "rule_code": r.rule_code,
                "dimension": r.dimension,
                "status": r.status,
                "severity": r.severity,
                "affected_count": r.affected_count,
                "message": r.message,
                "details": json.loads(r.detail_json),
            }
            for r in rules
        ],
        "quarantined_samples": [
            {
                "source_locator": q.source_locator,
                "raw_region": q.raw_region,
                "raw_year": q.raw_year,
                "raw_metric": q.raw_metric,
                "rule_code": q.rule_code,
                "reason_code": q.reason_code,
                "detail_message": q.detail_message,
            }
            for q in q_recs
        ],
    }


def get_observation_lineage(session: Session, observation_id: str) -> dict[str, Any]:
    if observation_id.startswith("srcf-"):
        cf = session.get(CuratedFact, observation_id)
        if cf is None:
            raise UnknownEntityError(f"Observasi '{observation_id}' tidak ditemukan.")
        run = session.get(ImportRun, cf.run_id)
        src = session.get(Source, cf.source_id)
        reg = session.get(Region, cf.region_code)
        m = session.get(Metric, cf.metric_code)
        return {
            "observation_id": cf.fact_id,
            "observation_kind": "source",
            "region_code": cf.region_code,
            "region_name": reg.region_name if reg else cf.region_code,
            "year": cf.reference_year,
            "metric_code": cf.metric_code,
            "metric_label": m.label if m else cf.metric_code,
            "value": _num_to_float_or_int(cf.value_numeric, cf.unit),
            "unit": cf.unit,
            "quality_status": cf.quality_status,
            "missing_reason": cf.missing_reason,
            "formula": m.formula if m else None,
            "transformation_version": cf.transformation_version,
            "upstream_inputs": [
                {
                    "role": "direct_source",
                    "fact_id": cf.fact_id,
                    "metric_code": cf.metric_code,
                    "value": _num_to_float_or_int(cf.value_numeric, cf.unit),
                    "unit": cf.unit,
                    "source_id": cf.source_id,
                    "source_title": src.title if src else cf.source_id,
                    "publisher": src.publisher if src else None,
                    "dataset_url": src.dataset_url if src else None,
                    "resource_url": src.resource_url if src else None,
                    "source_locator": cf.source_locator,
                    "run_id": cf.run_id,
                    "source_sha256": run.source_sha256 if run else None,
                    "retrieved_at": run.retrieved_at.isoformat() if run else None,
                }
            ],
        }

    if observation_id.startswith("derf-"):
        df = session.get(DerivedFact, observation_id)
        if df is None:
            raise UnknownEntityError(f"Observasi '{observation_id}' tidak ditemukan.")
        reg = session.get(Region, df.region_code)
        m = session.get(Metric, df.metric_code)
        edges = (
            session.execute(
                select(DerivedFactInput).where(DerivedFactInput.derived_fact_id == observation_id)
            )
            .scalars()
            .all()
        )
        upstream = []
        for edge in edges:
            cf = session.get(CuratedFact, edge.source_fact_id)
            if cf is None:
                continue
            run = session.get(ImportRun, cf.run_id)
            src = session.get(Source, cf.source_id)
            upstream.append(
                {
                    "role": edge.input_role,
                    "fact_id": cf.fact_id,
                    "metric_code": cf.metric_code,
                    "value": _num_to_float_or_int(cf.value_numeric, cf.unit),
                    "unit": cf.unit,
                    "source_id": cf.source_id,
                    "source_title": src.title if src else cf.source_id,
                    "publisher": src.publisher if src else None,
                    "dataset_url": src.dataset_url if src else None,
                    "resource_url": src.resource_url if src else None,
                    "source_locator": cf.source_locator,
                    "run_id": cf.run_id,
                    "source_sha256": run.source_sha256 if run else None,
                    "retrieved_at": run.retrieved_at.isoformat() if run else None,
                }
            )
        return {
            "observation_id": df.derived_fact_id,
            "observation_kind": "derived",
            "region_code": df.region_code,
            "region_name": reg.region_name if reg else df.region_code,
            "year": df.reference_year,
            "metric_code": df.metric_code,
            "metric_label": m.label if m else df.metric_code,
            "value": _num_to_float_or_int(df.value_numeric, df.unit),
            "unit": df.unit,
            "quality_status": df.quality_status,
            "missing_reason": df.missing_reason,
            "formula": m.formula if m else None,
            "methodology_version": df.methodology_version,
            "derivation_run_id": df.derivation_run_id,
            "upstream_inputs": upstream,
        }

    raise UnknownEntityError(f"Format observation_id '{observation_id}' tidak dikenal.")
