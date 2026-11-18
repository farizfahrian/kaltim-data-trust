from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kdt.registry import RegionResolver, load_region_entries
from kdt.validate import TARGET_REGENCY_CODES


class GeoAlignmentError(ValueError):
    """Raised when GeoJSON features fail 1-to-1 alignment against canonical region codes."""


@dataclass(frozen=True)
class GeoValidationSummary:
    feature_count: int
    matched_codes: list[str]
    unmatched_shapes: list[str]
    missing_target_regions: list[str]
    license: str
    source: str


def load_and_validate_kaltim_geojson(
    data_dir: Path,
    geojson_path: Path | None = None,
) -> tuple[dict[str, Any], GeoValidationSummary]:
    target_path = geojson_path or (data_dir / "geo" / "kaltim_adm2_bps_ocha.geojson")
    if not target_path.exists():
        raise GeoAlignmentError(f"File batas wilayah GeoJSON tidak ditemukan: {target_path.name}")

    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise GeoAlignmentError("Struktur GeoJSON bukan FeatureCollection yang valid.")

    meta = payload.get("metadata") or {}
    license_str = str(meta.get("license") or "").strip()
    source_str = str(meta.get("source") or "").strip()
    if not license_str or not source_str:
        raise GeoAlignmentError("Metadata sumber dan lisensi geometri wajib tercantum pada GeoJSON.")

    entries = load_region_entries(data_dir / "mapping" / "region_aliases.csv")
    resolver = RegionResolver(entries)

    matched_codes: set[str] = set()
    unmatched_shapes: list[str] = []

    for idx, feat in enumerate(payload["features"]):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        if geom.get("type") not in {"Polygon", "MultiPolygon"} or not geom.get("coordinates"):
            unmatched_shapes.append(f"feature[{idx}]: invalid_geometry")
            continue

        raw_code = props.get("region_code")
        raw_name = props.get("source_shape_name") or props.get("region_name")
        res = resolver.resolve(raw_code, raw_name, code_system="kemendagri")
        if res.region_code is None or res.region_code not in TARGET_REGENCY_CODES:
            unmatched_shapes.append(f"{raw_code!r}/{raw_name!r}: {res.status}")
            continue
        if res.region_code in matched_codes:
            unmatched_shapes.append(f"{res.region_code}: duplicate_polygon_feature")
            continue
        matched_codes.add(res.region_code)

    missing_targets = sorted(TARGET_REGENCY_CODES - matched_codes)
    summary = GeoValidationSummary(
        feature_count=len(payload["features"]),
        matched_codes=sorted(matched_codes),
        unmatched_shapes=unmatched_shapes,
        missing_target_regions=missing_targets,
        license=license_str,
        source=source_str,
    )

    if unmatched_shapes or missing_targets:
        raise GeoAlignmentError(
            f"Validasi GIS gagal: unmatched={unmatched_shapes}, missing={missing_targets}"
        )

    return payload, summary
