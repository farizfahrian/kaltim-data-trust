from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kdt.models import ImportRun
from kdt.service import (
    get_active_snapshot,
    get_dataset_detail,
    get_observation_lineage,
    get_overview,
    get_quality_run_report,
    get_region_detail,
    get_snapshot_observations,
    list_datasets,
    list_metrics,
    list_regions,
)

EXPORT_YEARS = [2023, 2024, 2025]


def export_static_snapshot(
    session: Session,
    *,
    public_api_dir: Path,
    astro_data_file: Path,
    quality_artifacts_dir: Path,
) -> dict[str, Any]:
    snap = get_active_snapshot(session)
    if snap is None:
        raise ValueError("Tidak ada published snapshot aktif untuk diekspor.")

    public_api_dir.mkdir(parents=True, exist_ok=True)
    quality_artifacts_dir.mkdir(parents=True, exist_ok=True)
    astro_data_file.parent.mkdir(parents=True, exist_ok=True)

    def _write_json(path: Path, payload: Any) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, indent=2, ensure_ascii=False)
        path.write_text(raw, encoding="utf-8")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    datasets = list_datasets(session)
    datasets_detail = {d["source_id"]: get_dataset_detail(session, d["source_id"]) for d in datasets}
    metrics = list_metrics(session)
    regions = list_regions(session)
    all_obs = get_snapshot_observations(session, limit=500)

    # Export /api/v1 static JSON files
    _write_json(
        public_api_dir / "health.json",
        {
            "status": "ok",
            "active_snapshot_id": snap.snapshot_id,
            "published_at": snap.published_at.isoformat(),
            "demo_data": snap.demo_data,
        },
    )
    _write_json(public_api_dir / "datasets.json", {"data": datasets})
    for src_id, d_detail in datasets_detail.items():
        _write_json(public_api_dir / "datasets" / f"{src_id}.json", d_detail)

    _write_json(public_api_dir / "metrics.json", {"data": metrics})
    for m in metrics:
        _write_json(public_api_dir / "metrics" / f"{m['metric_code']}.json", m)

    _write_json(public_api_dir / "regions.json", {"data": regions})
    region_details_2024: dict[str, Any] = {}
    for r in regions:
        r_det = get_region_detail(session, r["region_code"], year=2024)
        region_details_2024[r["region_code"]] = r_det
        _write_json(public_api_dir / "regions" / f"{r['region_code']}.json", r_det)

    overviews: dict[str, dict[str, Any]] = {}
    for m in metrics:
        m_code = m["metric_code"]
        overviews[m_code] = {}
        for yr in EXPORT_YEARS:
            ov = get_overview(session, metric_code=m_code, year=yr)
            overviews[m_code][str(yr)] = ov
            _write_json(public_api_dir / "overview" / f"{m_code}-{yr}.json", ov)

    # Export quality run JSON artifacts (FR-014)
    runs = session.execute(select(ImportRun)).scalars().all()
    quality_reports: dict[str, Any] = {}
    for r in runs:
        rep = get_quality_run_report(session, r.run_id)
        quality_reports[r.run_id] = rep
        _write_json(quality_artifacts_dir / f"{r.run_id}.json", rep)
        _write_json(public_api_dir / "quality" / "runs" / f"{r.run_id}.json", rep)

    # Export lineage JSON per observation
    lineages: dict[str, Any] = {}
    for obs in all_obs["data"]:
        obs_id = obs["observation_id"]
        if obs_id:
            lin = get_observation_lineage(session, obs_id)
            lineages[obs_id] = lin
            _write_json(public_api_dir / "lineage" / f"{obs_id}.json", lin)

    bundle = {
        "snapshot": {
            "snapshot_id": snap.snapshot_id,
            "published_at": snap.published_at.isoformat(),
            "release_note": snap.release_note,
            "demo_data": snap.demo_data,
            "years": EXPORT_YEARS,
            "default_year": 2024,
            "default_metric": "doctor_per_100k",
        },
        "datasets": datasets,
        "datasets_detail": datasets_detail,
        "metrics": metrics,
        "regions": regions,
        "region_details_2024": region_details_2024,
        "overviews": overviews,
        "observations": all_obs["data"],
        "lineages": lineages,
        "quality_reports": quality_reports,
    }
    bundle_sha256 = _write_json(astro_data_file, bundle)
    manifest = {
        "snapshot_id": snap.snapshot_id,
        "published_at": snap.published_at.isoformat(),
        "bundle_sha256": bundle_sha256,
        "observation_count": len(all_obs["data"]),
        "lineage_count": len(lineages),
        "dataset_count": len(datasets),
    }
    _write_json(public_api_dir / "manifest.json", manifest)
    return manifest
