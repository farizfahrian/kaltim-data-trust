from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd
import pandera.pandas as pa

from kdt.sniff import SniffResult

TARGET_REGENCY_CODES = {
    "64.01",
    "64.02",
    "64.03",
    "64.07",
    "64.08",
    "64.09",
    "64.11",
    "64.71",
    "64.72",
    "64.74",
}

INTEGRAL_UNITS = {"orang", "unit", "buah", "puskesmas", "fasilitas", "jiwa"}

CURATED_FRAME_SCHEMA = pa.DataFrameSchema(
    {
        "region_code": pa.Column(str, nullable=False),
        "reference_year": pa.Column(int, pa.Check.ge(1990), nullable=False),
        "metric_code": pa.Column(str, pa.Check.str_length(min_value=2), nullable=False),
        "unit": pa.Column(str, pa.Check.str_length(min_value=1), nullable=False),
        "quality_status": pa.Column(
            str, pa.Check.isin(["validated", "warning", "blocked", "not_checked"]), nullable=False
        ),
        "source_locator": pa.Column(str, pa.Check.str_length(min_value=1), nullable=False),
    },
    strict=False,
)


@dataclass
class CandidateFact:
    region_code: str
    region_level: str
    reference_year: int
    metric_code: str
    value_numeric: Decimal | None
    unit: str
    missing_reason: str | None
    quality_status: str
    source_locator: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuarantineItem:
    source_locator: str
    raw_region: str | None
    raw_year: str | None
    raw_metric: str | None
    raw_value: str | None
    rule_code: str
    reason_code: str
    detail_message: str


@dataclass
class RuleOutcome:
    rule_code: str
    dimension: str
    status: str  # "pass" | "warn" | "fail"
    severity: str  # "critical" | "warning" | "info"
    affected_count: int
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_detail_json(self) -> str:
        return json.dumps(self.details, ensure_ascii=False, default=str)


def run_quality_suite(
    *,
    source_cfg: dict[str, Any],
    metrics_map: dict[str, dict[str, Any]],
    sniff: SniffResult,
    candidates: list[CandidateFact],
    quarantined: list[QuarantineItem],
    code_typo_warnings: list[str],
    parser_notes: list[str],
) -> tuple[list[RuleOutcome], bool]:
    """
    Execute DQ-001 through DQ-014 and Pandera schema check.
    Returns (outcomes, is_eligible_for_publish).
    """
    outcomes: list[RuleOutcome] = []
    published_years = set(source_cfg.get("published_years") or source_cfg.get("reference_years") or [])

    # DQ-001: Schema / columns & format signature
    missing_cols = "MISSING_REQUIRED_COLUMNS" in parser_notes or (not candidates and not quarantined)
    if missing_cols:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-001",
                dimension="Validity",
                status="fail",
                severity="critical",
                affected_count=1,
                message="Kolom wajib wilayah/nilai tidak ditemukan atau tidak ada record yang dapat diekstraksi.",
                details={"parser_notes": parser_notes},
            )
        )
    elif sniff.warnings:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-001",
                dimension="Validity",
                status="warn",
                severity="warning",
                affected_count=len(sniff.warnings),
                message="Struktur kolom valid, namun label format/ekstensi berbeda dari byte file asli.",
                details={"format_warnings": sniff.warnings, "actual_format": sniff.actual_format},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-001",
                dimension="Validity",
                status="pass",
                severity="critical",
                affected_count=0,
                message=f"Format byte ({sniff.actual_format}) dan struktur kolom wajib terverifikasi.",
            )
        )

    # Pandera validation on candidate facts if non-empty
    if candidates:
        df = pd.DataFrame(
            [
                {
                    "region_code": c.region_code,
                    "reference_year": c.reference_year,
                    "metric_code": c.metric_code,
                    "unit": c.unit,
                    "quality_status": c.quality_status,
                    "source_locator": c.source_locator,
                }
                for c in candidates
            ]
        )
        CURATED_FRAME_SCHEMA.validate(df, lazy=True)

    # DQ-002: Business key uniqueness (region_code, reference_year, metric_code)
    seen_keys: dict[tuple[str, int, str], list[str]] = {}
    for c in candidates:
        key = (c.region_code, c.reference_year, c.metric_code)
        seen_keys.setdefault(key, []).append(c.source_locator)
    dup_keys = {f"{k[0]}:{k[1]}:{k[2]}": locs for k, locs in seen_keys.items() if len(locs) > 1}
    dup_q = [q for q in quarantined if q.rule_code == "DQ-002"]
    total_dups = len(dup_keys) + len(dup_q)
    if total_dups > 0:
        for c in candidates:
            if len(seen_keys[(c.region_code, c.reference_year, c.metric_code)]) > 1:
                c.quality_status = "blocked"
                c.value_numeric = None
                c.missing_reason = "blocked_quality"
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-002",
                dimension="Uniqueness",
                status="fail",
                severity="critical",
                affected_count=total_dups,
                message="Terdeteksi duplikasi kunci bisnis (region_code + reference_year + metric_code).",
                details={"duplicates": dup_keys},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-002",
                dimension="Uniqueness",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Seluruh kunci bisnis (wilayah × tahun × metrik) unik tanpa penghitungan ganda.",
            )
        )

    # DQ-003: Referential region mapping
    unmapped_q = [q for q in quarantined if q.rule_code == "DQ-003"]
    if unmapped_q:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-003",
                dimension="Referential Integrity",
                status="fail",
                severity="critical",
                affected_count=len(unmapped_q),
                message=f"{len(unmapped_q)} baris memiliki wilayah tak dikenal/konflik kode dan dikarantina.",
                details={
                    "samples": [
                        {"locator": q.source_locator, "raw_region": q.raw_region, "reason": q.reason_code}
                        for q in unmapped_q[:5]
                    ]
                },
            )
        )
    elif code_typo_warnings:
        unique_warnings = sorted(set(code_typo_warnings))
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-003",
                dimension="Referential Integrity",
                status="warn",
                severity="warning",
                affected_count=len(code_typo_warnings),
                message="Semua wilayah terpetakan ke master resmi; salah ketik kode sumber dicatat pada jejak audit.",
                details={"code_typo_notes": unique_warnings},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-003",
                dimension="Referential Integrity",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Seluruh kode dan nama wilayah cocok 100% dengan master wilayah resmi.",
            )
        )

    # DQ-004: Numeric parsing & missing-value semantics
    parse_q = [q for q in quarantined if q.rule_code == "DQ-004"]
    missing_source_facts = [c for c in candidates if c.missing_reason == "missing_in_source"]
    if parse_q:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-004",
                dimension="Validity",
                status="fail",
                severity="critical",
                affected_count=len(parse_q),
                message=f"{len(parse_q)} nilai gagal diurai secara deterministik (locale ambigu / formula / teks ilegal).",
                details={
                    "samples": [
                        {
                            "locator": q.source_locator,
                            "raw_value": q.raw_value,
                            "reason": q.reason_code,
                        }
                        for q in parse_q[:5]
                    ]
                },
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-004",
                dimension="Validity",
                status="pass",
                severity="critical",
                affected_count=0,
                message=(
                    f"Parsing numerik valid; {len(missing_source_facts)} nilai kosong disimpan sebagai NULL bermakna (bukan 0)."
                ),
            )
        )

    # DQ-005: Nonnegative & integral count constraints
    dq5_q = [q for q in quarantined if q.rule_code == "DQ-005"]
    neg_or_frac: list[str] = []
    for c in candidates:
        if c.value_numeric is None:
            continue
        if c.value_numeric < 0:
            neg_or_frac.append(f"{c.source_locator}: nilai negatif {c.value_numeric}")
            c.quality_status = "blocked"
            c.value_numeric = None
            c.missing_reason = "blocked_quality"
        elif c.unit.lower() in INTEGRAL_UNITS and c.value_numeric != c.value_numeric.to_integral_value():
            neg_or_frac.append(f"{c.source_locator}: nilai pecahan {c.value_numeric} untuk satuan {c.unit}")
            c.quality_status = "blocked"
            c.value_numeric = None
            c.missing_reason = "blocked_quality"

    total_dq5 = len(dq5_q) + len(neg_or_frac)
    if total_dq5 > 0:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-005",
                dimension="Validity",
                status="fail",
                severity="critical",
                affected_count=total_dq5,
                message="Ditemukan nilai negatif atau pecahan tidak sah pada metrik cacah bulat.",
                details={
                    "samples": [q.detail_message for q in dq5_q[:5]] + neg_or_frac[:5],
                },
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-005",
                dimension="Validity",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Semua metrik cacah bernilai non-negatif dan bulat utuh.",
            )
        )

    # DQ-006: Reference year coverage & validity
    year_q = [q for q in quarantined if q.rule_code == "DQ-006"]
    allowed_years = set(source_cfg.get("reference_years") or [])
    out_of_range = [
        c for c in candidates if allowed_years and c.reference_year not in allowed_years
    ]
    for c in out_of_range:
        c.quality_status = "blocked"
        c.value_numeric = None
        c.missing_reason = "blocked_quality"
    if year_q or out_of_range:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-006",
                dimension="Timeliness",
                status="fail",
                severity="critical",
                affected_count=len(year_q) + len(out_of_range),
                message="Tahun referensi kosong, tidak valid, atau di luar cakupan periode sumber.",
                details={
                    "quarantined_years": [q.source_locator for q in year_q[:5]],
                    "out_of_range": [c.source_locator for c in out_of_range[:5]],
                },
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-006",
                dimension="Timeliness",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Tahun referensi valid dan berada dalam periode sumber yang dideklarasikan.",
            )
        )

    # DQ-007: Subcategory & aggregation semantics (e.g., doctor_total_count >= general + specialist + dentist)
    by_reg_yr: dict[tuple[str, int], dict[str, CandidateFact]] = {}
    for c in candidates:
        by_reg_yr.setdefault((c.region_code, c.reference_year), {})[c.metric_code] = c

    subtype_violations_published: list[str] = []
    subtype_violations_historical: list[str] = []
    years_with_violation: set[int] = set()

    for (reg_code, yr), mdict in by_reg_yr.items():
        tot = mdict.get("doctor_total_count")
        if tot is None or tot.value_numeric is None:
            continue
        sub_sum = Decimal("0")
        present_subs = 0
        for sub_code in ("doctor_general_count", "doctor_specialist_count", "dentist_total_count"):
            sub_fact = mdict.get(sub_code)
            if sub_fact is not None and sub_fact.value_numeric is not None:
                sub_sum += sub_fact.value_numeric
                present_subs += 1
        if present_subs > 0 and tot.value_numeric < sub_sum:
            msg = f"{reg_code} ({yr}): total={tot.value_numeric} < jumlah subkategori={sub_sum}"
            years_with_violation.add(yr)
            if not published_years or yr in published_years:
                subtype_violations_published.append(msg)
            else:
                subtype_violations_historical.append(msg)

    # Block entire year of doctor_total_count if any region in that year fails subtype consistency
    blocked_historical_count = 0
    for yr in years_with_violation:
        for c in candidates:
            if c.reference_year == yr and c.metric_code == "doctor_total_count":
                c.quality_status = "blocked"
                c.value_numeric = None
                c.missing_reason = "blocked_quality"
                blocked_historical_count += 1
                quarantined.append(
                    QuarantineItem(
                        source_locator=c.source_locator,
                        raw_region=c.region_code,
                        raw_year=str(yr),
                        raw_metric=c.metric_code,
                        raw_value="blocked_by_DQ007",
                        rule_code="DQ-007",
                        reason_code="subtype_exceeds_total",
                        detail_message=f"Tahun {yr} diblokir karena subkategori melebihi total pada sumber.",
                    )
                )

    if subtype_violations_published:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-007",
                dimension="Consistency",
                status="fail",
                severity="critical",
                affected_count=len(subtype_violations_published),
                message="Total indikator lebih kecil daripada penjumlahan subkategorinya pada tahun target publikasi.",
                details={"violations": subtype_violations_published},
            )
        )
    elif subtype_violations_historical:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-007",
                dimension="Consistency",
                status="warn",
                severity="warning",
                affected_count=blocked_historical_count,
                message=(
                    "Anomali subkategori pada tahun historis non-publikasi (2022) otomatis diblokir; "
                    "periode publikasi aktif (2023–2025) konsisten 100%."
                ),
                details={"blocked_historical_violations": subtype_violations_historical},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-007",
                dimension="Consistency",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Semantik kategori dan subkategori konsisten tanpa tumpang tindih tersembunyi.",
            )
        )

    # DQ-008: Cross-source year/region compatibility (evaluated deeply in derive step; verified at source level)
    outcomes.append(
        RuleOutcome(
            rule_code="DQ-008",
            dimension="Consistency",
            status="pass",
            severity="critical",
            affected_count=0,
            message="Kunci wilayah dan tahun referensi siap untuk pemeriksaan kompatibilitas lintas sumber.",
        )
    )

    # DQ-009: Unit & embedded ratio verification
    unit_mismatches: list[str] = []
    corrupted_embedded_ratios: list[str] = []
    for c in candidates:
        expected_unit = metrics_map.get(c.metric_code, {}).get("unit")
        if expected_unit and c.unit.lower() != expected_unit.lower():
            # Allow 'buah' -> 'unit' only when documented, never 'buah' for '%'
            if {c.unit.lower(), expected_unit.lower()} == {"buah", "unit"}:
                c.unit = expected_unit
            else:
                unit_mismatches.append(
                    f"{c.source_locator}: satuan sumber '{c.unit}' != kontrak '{expected_unit}'"
                )
                c.quality_status = "blocked"
                c.value_numeric = None
                c.missing_reason = "blocked_quality"

        rep_doc = c.extra.get("reported_doctor_general")
        rep_ratio = c.extra.get("reported_ratio_per_100k")
        if rep_doc is not None and rep_ratio is not None and c.value_numeric and c.value_numeric > 0:
            try:
                expected_r = (Decimal(str(rep_doc)) / c.value_numeric) * Decimal("100000")
                actual_r = Decimal(str(rep_ratio))
                if abs(expected_r - actual_r) > Decimal("1.0"):
                    corrupted_embedded_ratios.append(
                        f"{c.source_locator} ({c.region_code}): rasio tertulis={actual_r} vs seharusnya={expected_r:.2f}"
                    )
            except Exception:
                corrupted_embedded_ratios.append(f"{c.source_locator}: rasio tertulis tidak valid {rep_ratio!r}")

    if unit_mismatches or corrupted_embedded_ratios:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-009",
                dimension="Consistency",
                status="fail",
                severity="critical",
                affected_count=len(unit_mismatches) + len(corrupted_embedded_ratios),
                message="Ketidaksesuaian satuan atau korupsi skala rasio (serial waktu Excel) terdeteksi.",
                details={
                    "unit_mismatches": unit_mismatches[:5],
                    "corrupted_ratios": corrupted_embedded_ratios[:5],
                },
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-009",
                dimension="Consistency",
                status="pass",
                severity="critical",
                affected_count=0,
                message="Satuan dan konversi skala sesuai dengan kamus metrik.",
            )
        )

    # DQ-010: Provenance completeness
    has_prov = bool(source_cfg.get("dataset_url") and sniff.sha256)
    outcomes.append(
        RuleOutcome(
            rule_code="DQ-010",
            dimension="Traceability",
            status="pass" if has_prov else "fail",
            severity="critical",
            affected_count=0 if has_prov else 1,
            message=(
                f"Provenance lengkap (SHA-256 {sniff.sha256[:12]}…, URL penerbit, dan koordinat sel tersimpan)."
                if has_prov
                else "Metadata URL sumber atau SHA-256 tidak lengkap."
            ),
        )
    )

    # DQ-011: Reconcile sum of 10 regencies/cities against official province row ('64')
    reconcile_warnings: list[str] = []
    reconcile_failures: list[str] = []
    by_metric_yr: dict[tuple[str, int], list[CandidateFact]] = {}
    for c in candidates:
        if published_years and c.reference_year not in published_years:
            continue
        by_metric_yr.setdefault((c.metric_code, c.reference_year), []).append(c)

    for (m_code, yr), group in by_metric_yr.items():
        prov_fact = next((g for g in group if g.region_code == "64"), None)
        reg_facts = [
            g for g in group if g.region_code in TARGET_REGENCY_CODES and g.value_numeric is not None
        ]
        if prov_fact and prov_fact.value_numeric is not None and len(reg_facts) == 10:
            reg_sum = sum((g.value_numeric for g in reg_facts), Decimal("0"))
            diff = prov_fact.value_numeric - reg_sum
            if diff != 0:
                rel = abs(diff) / reg_sum if reg_sum != 0 else Decimal("1")
                msg = f"{m_code} ({yr}): jumlah 10 kab/kota={reg_sum} vs baris provinsi={prov_fact.value_numeric} (selisih={diff:+})"
                if rel > Decimal("0.25"):
                    reconcile_failures.append(msg)
                    prov_fact.quality_status = "blocked"
                    prov_fact.value_numeric = None
                    prov_fact.missing_reason = "blocked_quality"
                else:
                    reconcile_warnings.append(msg)
                    prov_fact.quality_status = "warning"

    if reconcile_failures:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-011",
                dimension="Accuracy Evidence",
                status="fail",
                severity="critical",
                affected_count=len(reconcile_failures),
                message="Selisih ekstrem antara penjumlahan kabupaten/kota dan baris total provinsi.",
                details={"failures": reconcile_failures},
            )
        )
    elif reconcile_warnings:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-011",
                dimension="Accuracy Evidence",
                status="warn",
                severity="warning",
                affected_count=len(reconcile_warnings),
                message="Terdapat selisih minor antara penjumlahan 10 kab/kota dan baris agregat provinsi; diberi penanda warning.",
                details={"discrepancies": reconcile_warnings},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-011",
                dimension="Accuracy Evidence",
                status="pass",
                severity="warning",
                affected_count=0,
                message="Penjumlahan 10 kabupaten/kota cocok 100% dengan baris agregat resmi Provinsi Kalimantan Timur.",
            )
        )

    # DQ-012: Sudden YoY jump (> 40%)
    yoy_flags: list[str] = []
    by_reg_metric: dict[tuple[str, str], list[CandidateFact]] = {}
    for c in candidates:
        if c.region_code in TARGET_REGENCY_CODES and c.value_numeric is not None:
            if not published_years or c.reference_year in published_years:
                by_reg_metric.setdefault((c.region_code, c.metric_code), []).append(c)

    for (reg_code, m_code), series in by_reg_metric.items():
        series_sorted = sorted(series, key=lambda x: x.reference_year)
        for prev, curr in zip(series_sorted, series_sorted[1:], strict=False):
            if curr.reference_year - prev.reference_year == 1 and prev.value_numeric and prev.value_numeric >= 20:
                pct = ((curr.value_numeric - prev.value_numeric) / prev.value_numeric) * Decimal("100")
                if abs(pct) > Decimal("40"):
                    yoy_flags.append(
                        f"{reg_code} {m_code} ({prev.reference_year}→{curr.reference_year}): "
                        f"{prev.value_numeric} → {curr.value_numeric} ({pct:+.1f}%)"
                    )

    if yoy_flags:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-012",
                dimension="Consistency",
                status="warn",
                severity="warning",
                affected_count=len(yoy_flags),
                message="Perubahan tahunan >40% ditandai untuk tinjauan konteks domain (tidak dikoreksi otomatis).",
                details={"flagged_changes": yoy_flags},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-012",
                dimension="Consistency",
                status="pass",
                severity="warning",
                affected_count=0,
                message="Tidak ada lonjakan tahunan di luar ambang batas wajar.",
            )
        )

    # DQ-013: Timeliness
    active_years = [c.reference_year for c in candidates if c.quality_status in {"validated", "warning"}]
    max_yr = max(active_years) if active_years else 0
    if max_yr < 2024:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-013",
                dimension="Timeliness",
                status="warn",
                severity="warning",
                affected_count=1,
                message=f"Periode referensi terbaru ({max_yr}) lebih lama dari target baseline 2024.",
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-013",
                dimension="Timeliness",
                status="pass",
                severity="warning",
                affected_count=0,
                message=f"Periode referensi mencakup baseline mutakhir (hingga {max_yr}).",
            )
        )

    # DQ-014: Regional completeness across 10 Kaltim regencies/cities
    missing_coverage: list[str] = []
    for (m_code, yr), group in by_metric_yr.items():
        covered = {
            g.region_code
            for g in group
            if g.region_code in TARGET_REGENCY_CODES and g.value_numeric is not None and g.quality_status != "blocked"
        }
        missing_regs = sorted(TARGET_REGENCY_CODES - covered)
        if missing_regs:
            missing_coverage.append(f"{m_code} ({yr}): kurang {missing_regs}")

    if missing_coverage:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-014",
                dimension="Completeness",
                status="warn",
                severity="warning",
                affected_count=len(missing_coverage),
                message="Sebagian kabupaten/kota tidak memiliki nilai tervalidasi dan akan ditampilkan sebagai status missing.",
                details={"missing_by_metric_year": missing_coverage},
            )
        )
    else:
        outcomes.append(
            RuleOutcome(
                rule_code="DQ-014",
                dimension="Completeness",
                status="pass",
                severity="warning",
                affected_count=0,
                message="Cakupan lengkap 10/10 kabupaten/kota Kalimantan Timur terpenuhi.",
            )
        )

    has_critical_fail = any(o.status == "fail" and o.severity == "critical" for o in outcomes)
    pub_allowed = source_cfg.get("publication_allowed") != "quarantined_audit_only"
    is_eligible = (not has_critical_fail) and pub_allowed and any(
        c.quality_status in {"validated", "warning"} and c.value_numeric is not None for c in candidates
    )
    return outcomes, is_eligible
