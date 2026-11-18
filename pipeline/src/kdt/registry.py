from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from kdt.models import Metric, Region, Source


@dataclass(frozen=True)
class RegionEntry:
    region_code: str
    region_name: str
    short_name: str
    region_level: str
    parent_region_code: str | None
    kemendagri_numeric: str
    bps_numeric: str
    code_system: str
    code_valid_from: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class RegionResolution:
    region_code: str | None
    region_level: str | None
    status: str  # "matched" | "matched_with_code_typo" | "unmapped" | "code_name_conflict"
    warning: str | None = None


class RegionResolver:
    def __init__(self, entries: list[RegionEntry]) -> None:
        self.entries = entries
        self.by_code: dict[str, RegionEntry] = {e.region_code: e for e in entries}
        self.by_kemendagri_num: dict[str, RegionEntry] = {}
        self.by_bps_num: dict[str, RegionEntry] = {}
        self.by_alias: dict[str, RegionEntry] = {}

        for entry in entries:
            self.by_kemendagri_num[entry.kemendagri_numeric] = entry
            if entry.region_level == "province":
                self.by_kemendagri_num["6400"] = entry
                self.by_kemendagri_num["64"] = entry
            self.by_bps_num[entry.bps_numeric] = entry
            for alias in entry.aliases:
                norm = self._norm_name(alias)
                self.by_alias[norm] = entry
            self.by_alias[self._norm_name(entry.region_name)] = entry
            self.by_alias[self._norm_name(entry.short_name)] = entry

    @staticmethod
    def _norm_name(val: str) -> str:
        return " ".join(val.strip().lower().replace(".", ". ").split()).replace(". ", ".")

    @staticmethod
    def _norm_code(raw_code: Any) -> str | None:
        if raw_code is None:
            return None
        if isinstance(raw_code, float):
            if raw_code.is_integer():
                return str(int(raw_code))
            return str(raw_code).strip()
        text = str(raw_code).strip()
        if not text:
            return None
        if text.endswith(".0") and text[:-2].replace(".", "").isdigit():
            text = text[:-2]
        return text

    def resolve(
        self,
        raw_code: Any,
        raw_name: Any,
        code_system: str = "kemendagri",
    ) -> RegionResolution:
        code_str = self._norm_code(raw_code)
        name_str = str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else None

        name_entry = self.by_alias.get(self._norm_name(name_str)) if name_str else None
        code_entry: RegionEntry | None = None

        if code_str:
            if code_str in self.by_code:
                code_entry = self.by_code[code_str]
            elif code_system in {"kemendagri", "kemendagri_with_name_authority"}:
                code_entry = self.by_kemendagri_num.get(code_str)
            elif code_system == "bps":
                code_entry = self.by_bps_num.get(code_str)

        if code_system == "name_only":
            if name_entry:
                return RegionResolution(name_entry.region_code, name_entry.region_level, "matched")
            return RegionResolution(None, None, "unmapped", f"Nama wilayah tidak dikenal: {name_str!r}")

        if name_entry and code_entry:
            if name_entry.region_code == code_entry.region_code:
                return RegionResolution(name_entry.region_code, name_entry.region_level, "matched")
            if code_system == "kemendagri_with_name_authority":
                return RegionResolution(
                    name_entry.region_code,
                    name_entry.region_level,
                    "matched_with_code_typo",
                    warning=(
                        f"Kode sumber '{code_str}' merujuk ke {code_entry.short_name} ({code_entry.region_code}), "
                        f"tetapi nama wilayah tertulis '{name_str}' ({name_entry.region_code}); dipetakan ke "
                        f"{name_entry.region_code} sesuai kebijakan otoritas nama dan dicatat pada audit."
                    ),
                )
            return RegionResolution(
                None,
                None,
                "code_name_conflict",
                warning=(
                    f"Konflik kode '{code_str}' ({code_entry.short_name}) vs nama '{name_str}' ({name_entry.short_name})."
                ),
            )

        if name_entry and not code_str:
            return RegionResolution(name_entry.region_code, name_entry.region_level, "matched")

        if code_entry and not name_str:
            return RegionResolution(code_entry.region_code, code_entry.region_level, "matched")

        return RegionResolution(
            None,
            None,
            "unmapped",
            warning=f"Wilayah tidak terdaftar di kamus: code={code_str!r}, name={name_str!r}",
        )


def load_region_entries(csv_path: Path) -> list[RegionEntry]:
    entries: list[RegionEntry] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aliases = tuple(a.strip() for a in row["aliases"].split("|") if a.strip())
            entries.append(
                RegionEntry(
                    region_code=row["region_code"].strip(),
                    region_name=row["region_name"].strip(),
                    short_name=row["short_name"].strip(),
                    region_level=row["region_level"].strip(),
                    parent_region_code=row["parent_region_code"].strip() or None,
                    kemendagri_numeric=row["kemendagri_numeric"].strip(),
                    bps_numeric=row["bps_numeric"].strip(),
                    code_system=row["code_system"].strip(),
                    code_valid_from=row["code_valid_from"].strip() or None,
                    aliases=aliases,
                )
            )
    return entries


def load_sources_manifest(yml_path: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
    return {item["source_id"]: item for item in data["sources"]}


def load_metrics_catalog(yml_path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
    version = str(data.get("version", "1.0.0"))
    return version, {item["metric_code"]: item for item in data["metrics"]}


def sync_reference_tables(
    session: Session,
    data_dir: Path,
) -> tuple[RegionResolver, dict[str, dict[str, Any]], dict[str, dict[str, Any]], str]:
    region_entries = load_region_entries(data_dir / "mapping" / "region_aliases.csv")
    sources_map = load_sources_manifest(data_dir / "manifests" / "sources.yml")
    methodology_version, metrics_map = load_metrics_catalog(data_dir / "mapping" / "metric_map.yml")

    # Upsert regions (province first for self-FK)
    ordered_regions = sorted(region_entries, key=lambda r: 0 if r.parent_region_code is None else 1)
    for r in ordered_regions:
        existing = session.get(Region, r.region_code)
        if existing is None:
            session.add(
                Region(
                    region_code=r.region_code,
                    region_name=r.region_name,
                    short_name=r.short_name,
                    region_level=r.region_level,
                    parent_region_code=r.parent_region_code,
                    kemendagri_numeric=r.kemendagri_numeric,
                    bps_numeric=r.bps_numeric,
                    code_system=r.code_system,
                    code_valid_from=r.code_valid_from,
                )
            )
        else:
            existing.region_name = r.region_name
            existing.short_name = r.short_name
            existing.kemendagri_numeric = r.kemendagri_numeric
            existing.bps_numeric = r.bps_numeric

    for src_id, s in sources_map.items():
        existing = session.get(Source, src_id)
        limitations_json = json.dumps(s.get("known_limitations", []), ensure_ascii=False)
        if existing is None:
            session.add(
                Source(
                    source_id=src_id,
                    title=s["title"],
                    publisher=s["publisher"],
                    dataset_url=s["dataset_url"],
                    resource_url=s.get("resource_url"),
                    reported_format=s.get("reported_format", "Not verified"),
                    actual_format=s.get("actual_format", "Not verified"),
                    expected_sha256=s.get("expected_sha256"),
                    license_status=s.get("license_statement", "undetermined"),
                    publication_allowed=s.get("publication_allowed", "undetermined"),
                    region_code_system=s.get("region_code_system", "kemendagri"),
                    known_limitations_json=limitations_json,
                )
            )
        else:
            existing.title = s["title"]
            existing.publisher = s["publisher"]
            existing.dataset_url = s["dataset_url"]
            existing.resource_url = s.get("resource_url")
            existing.reported_format = s.get("reported_format", "Not verified")
            existing.actual_format = s.get("actual_format", "Not verified")
            existing.expected_sha256 = s.get("expected_sha256")
            existing.license_status = s.get("license_statement", "undetermined")
            existing.publication_allowed = s.get("publication_allowed", "undetermined")
            existing.region_code_system = s.get("region_code_system", "kemendagri")
            existing.known_limitations_json = limitations_json

    for m_code, m in metrics_map.items():
        existing = session.get(Metric, m_code)
        valid_periods_json = json.dumps(m.get("valid_periods", []))
        if existing is None:
            session.add(
                Metric(
                    metric_code=m_code,
                    label=m["label"],
                    short_label=m["short_label"],
                    unit=m["unit"],
                    kind=m["kind"],
                    priority=m.get("priority", "P0"),
                    operational_definition=m["operational_definition"],
                    formula=m.get("formula"),
                    numerator_source=m.get("numerator_source"),
                    denominator_source=m.get("denominator_source"),
                    aggregation_rule=m["aggregation_rule"],
                    valid_periods_json=valid_periods_json,
                    limitations=m.get("limitations", ""),
                    methodology_version=methodology_version,
                )
            )
        else:
            existing.label = m["label"]
            existing.short_label = m["short_label"]
            existing.unit = m["unit"]
            existing.operational_definition = m["operational_definition"]
            existing.formula = m.get("formula")
            existing.valid_periods_json = valid_periods_json
            existing.limitations = m.get("limitations", "")
            existing.methodology_version = methodology_version

    session.flush()
    return RegionResolver(region_entries), sources_map, metrics_map, methodology_version
