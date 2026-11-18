from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

from kdt.normalize import parse_year


@dataclass
class ExtractedRecord:
    source_locator: str
    raw_code: Any
    raw_region: Any
    raw_year: Any
    metric_code: str
    raw_value: Any
    raw_unit: str | None
    locale_policy: str = "strict"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    records: list[ExtractedRecord]
    raw_row_count: int
    sheet_names: list[str]
    parser_notes: list[str] = field(default_factory=list)


DOCTOR_CATEGORY_MAP = {
    "dokter": "doctor_total_count",
    "dokter umum": "doctor_general_count",
    "dokter spesialis": "doctor_specialist_count",
    "dokter gigi": "dentist_total_count",
}


def extract_source_records(
    file_path: Path,
    actual_format: str,
    source_cfg: dict[str, Any],
    override_year: int | None = None,
) -> ExtractionResult:
    adapter = source_cfg.get("parser_adapter", "generic_tabular")
    if adapter == "dinkes_doctor_blocks_xlsx":
        return _parse_dinkes_doctor_blocks(file_path, source_cfg)
    if adapter == "dinkes_single_year_table_xlsx":
        return _parse_dinkes_single_year_table(file_path, source_cfg, override_year)
    if adapter == "dinkes_population_nakes_2024_xlsx":
        return _parse_dinkes_population_nakes_2024(file_path, override_year or 2024)
    if adapter == "dinkes_corrupted_ratio_2024_xlsx":
        return _parse_dinkes_corrupted_ratio_2024(file_path, override_year or 2024)
    if adapter == "generic_tabular":
        return _parse_generic_tabular(file_path, actual_format, source_cfg, override_year)
    raise ValueError(f"Parser adapter tidak dikenal: {adapter}")


def _load_xlsx_dual(file_path: Path) -> tuple[openpyxl.Workbook, openpyxl.Workbook]:
    raw_bytes = file_path.read_bytes()
    wb_formula = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False, read_only=False)
    wb_values = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=False)
    return wb_formula, wb_values


def _cell_value_safe(ws_f, ws_v, row: int, col: int) -> Any:
    raw_f = ws_f.cell(row, col).value
    val = ws_v.cell(row, col).value
    if isinstance(raw_f, str) and raw_f.startswith("="):
        # Accept cached numeric result from openpyxl data_only=True; if uncached None, return raw formula string so normalizer rejects it safely
        if val is None:
            return raw_f
        return val
    return val


def _parse_dinkes_doctor_blocks(file_path: Path, source_cfg: dict[str, Any]) -> ExtractionResult:
    wb_f, wb_v = _load_xlsx_dual(file_path)
    sheet_name = wb_v.sheetnames[0]
    ws_f, ws_v = wb_f[sheet_name], wb_v[sheet_name]

    # Header on row 1: [None, 'URAIAN', 'TH 2022', 'TH 2023', 'TH 2024', 'TH 2025', 'TH 2026', 'Satuan']
    year_cols: list[tuple[int, int]] = []
    unit_col: int | None = None
    allowed_years = set(source_cfg.get("reference_years", [2022, 2023, 2024, 2025]))

    for col_idx in range(1, ws_v.max_column + 1):
        hdr = ws_v.cell(1, col_idx).value
        if hdr is None:
            continue
        hdr_str = str(hdr).strip()
        if hdr_str.lower() == "satuan":
            unit_col = col_idx
            continue
        yr = parse_year(hdr_str)
        if yr is not None and yr in allowed_years:
            year_cols.append((col_idx, yr))

    records: list[ExtractedRecord] = []
    current_code: Any = None
    current_region: Any = None
    nonempty_rows = 0

    for r_idx in range(2, ws_v.max_row + 1):
        c1 = _cell_value_safe(ws_f, ws_v, r_idx, 1)
        c2 = _cell_value_safe(ws_f, ws_v, r_idx, 2)
        row_any = any(ws_v.cell(r_idx, c).value is not None for c in range(1, 9))
        if not row_any:
            continue
        nonempty_rows += 1

        if c1 is not None and c2 is not None:
            current_code = c1
            current_region = str(c2).strip()
            continue

        if c2 is not None and current_region is not None:
            cat_norm = str(c2).strip().lower()
            metric_code = DOCTOR_CATEGORY_MAP.get(cat_norm)
            if not metric_code:
                continue
            raw_unit = str(ws_v.cell(r_idx, unit_col).value).strip() if unit_col else "orang"
            for col_idx, yr in year_cols:
                val = _cell_value_safe(ws_f, ws_v, r_idx, col_idx)
                raw_formula = ws_f.cell(r_idx, col_idx).value
                locator = f"{sheet_name}!R{r_idx}C{col_idx}"
                records.append(
                    ExtractedRecord(
                        source_locator=locator,
                        raw_code=current_code,
                        raw_region=current_region,
                        raw_year=yr,
                        metric_code=metric_code,
                        raw_value=val,
                        raw_unit=raw_unit,
                        extra={"formula": raw_formula if isinstance(raw_formula, str) and raw_formula.startswith("=") else None},
                    )
                )

    return ExtractionResult(
        records=records,
        raw_row_count=nonempty_rows,
        sheet_names=wb_v.sheetnames,
        parser_notes=["Hierarki blok wilayah diurai deterministik; kolom TH 2026 dikecualikan sesuai manifest."],
    )


def _parse_dinkes_single_year_table(
    file_path: Path, source_cfg: dict[str, Any], override_year: int | None
) -> ExtractionResult:
    wb_f, wb_v = _load_xlsx_dual(file_path)
    sheet_name = wb_v.sheetnames[0]
    ws_f, ws_v = wb_f[sheet_name], wb_v[sheet_name]

    # Row 1 headers: ['NO', 'KODE'/'KODE REFERENSI', 'KABUPATEN/KOTA'/'RUMAH SAKIT UMUM', 2024]
    hdr_year = parse_year(ws_v.cell(1, 4).value) or override_year
    metric_code = source_cfg["metric_code"]
    unit = source_cfg.get("unit", "unit")

    records: list[ExtractedRecord] = []
    nonempty_rows = 0
    for r_idx in range(2, ws_v.max_row + 1):
        code_val = _cell_value_safe(ws_f, ws_v, r_idx, 2)
        region_val = _cell_value_safe(ws_f, ws_v, r_idx, 3)
        num_val = _cell_value_safe(ws_f, ws_v, r_idx, 4)
        if code_val is None and region_val is None and num_val is None:
            continue
        nonempty_rows += 1
        records.append(
            ExtractedRecord(
                source_locator=f"{sheet_name.strip()}!R{r_idx}C4",
                raw_code=code_val,
                raw_region=region_val,
                raw_year=hdr_year,
                metric_code=metric_code,
                raw_value=num_val,
                raw_unit=unit,
            )
        )

    return ExtractionResult(
        records=records,
        raw_row_count=nonempty_rows,
        sheet_names=wb_v.sheetnames,
    )


def _parse_dinkes_population_nakes_2024(file_path: Path, year: int) -> ExtractionResult:
    wb_f, wb_v = _load_xlsx_dual(file_path)
    sheet_name = wb_v.sheetnames[0]
    ws_f, ws_v = wb_f[sheet_name], wb_v[sheet_name]

    records: list[ExtractedRecord] = []
    nonempty_rows = 0
    # Columns: 1=NO, 2=KAB/KOTA, 3=JUMLAH PENDUDUK
    for r_idx in range(2, ws_v.max_row + 1):
        region_val = _cell_value_safe(ws_f, ws_v, r_idx, 2)
        pop_val = _cell_value_safe(ws_f, ws_v, r_idx, 3)
        if region_val is None and pop_val is None:
            continue
        nonempty_rows += 1
        records.append(
            ExtractedRecord(
                source_locator=f"{sheet_name}!R{r_idx}C3",
                raw_code=None,
                raw_region=region_val,
                raw_year=year,
                metric_code="population_count",
                raw_value=pop_val,
                raw_unit="jiwa",
            )
        )
    return ExtractionResult(
        records=records,
        raw_row_count=nonempty_rows,
        sheet_names=wb_v.sheetnames,
    )


def _parse_dinkes_corrupted_ratio_2024(file_path: Path, year: int) -> ExtractionResult:
    """Adapter for SRC-DOC-RATIO-BAD to expose real-world corruption to the DQ gate."""
    wb_f, wb_v = _load_xlsx_dual(file_path)
    sheet_name = wb_v.sheetnames[0]
    ws_f, ws_v = wb_f[sheet_name], wb_v[sheet_name]

    records: list[ExtractedRecord] = []
    nonempty_rows = 0
    for r_idx in range(2, ws_v.max_row + 1):
        region_val = _cell_value_safe(ws_f, ws_v, r_idx, 2)
        pop_val = _cell_value_safe(ws_f, ws_v, r_idx, 3)
        doc_val = _cell_value_safe(ws_f, ws_v, r_idx, 4)
        ratio_val = _cell_value_safe(ws_f, ws_v, r_idx, 5)
        if region_val is None:
            continue
        nonempty_rows += 1
        records.append(
            ExtractedRecord(
                source_locator=f"{sheet_name}!R{r_idx}C3",
                raw_code=None,
                raw_region=region_val,
                raw_year=year,
                metric_code="population_count",
                raw_value=pop_val,
                raw_unit="jiwa",
                extra={"reported_doctor_general": doc_val, "reported_ratio_per_100k": ratio_val},
            )
        )
    return ExtractionResult(
        records=records,
        raw_row_count=nonempty_rows,
        sheet_names=wb_v.sheetnames,
    )


def _parse_generic_tabular(
    file_path: Path,
    actual_format: str,
    source_cfg: dict[str, Any],
    override_year: int | None,
) -> ExtractionResult:
    locale_policy = source_cfg.get("locale_policy", "strict")
    default_metric = source_cfg.get("metric_code")
    default_unit = source_cfg.get("unit")

    rows: list[dict[str, Any]] = []
    sheet_names: list[str] = []

    if actual_format == "CSV":
        text = file_path.read_text(encoding="utf-8-sig")
        delimiter = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        sheet_names = ["csv"]
        for row in reader:
            rows.append({(k or "").strip().lower(): v for k, v in row.items()})
    else:
        wb_f, wb_v = _load_xlsx_dual(file_path)
        sname = wb_v.sheetnames[0]
        sheet_names = wb_v.sheetnames
        ws_f, ws_v = wb_f[sname], wb_v[sname]
        headers = [
            str(ws_v.cell(1, c).value or "").strip().lower() for c in range(1, ws_v.max_column + 1)
        ]
        for r_idx in range(2, ws_v.max_row + 1):
            vals = [_cell_value_safe(ws_f, ws_v, r_idx, c) for c in range(1, ws_v.max_column + 1)]
            if not any(v is not None and str(v).strip() != "" for v in vals):
                continue
            rows.append(dict(zip(headers, vals, strict=False)))

    required_any_region = {"region", "region_name", "kab/kota", "kabupaten/kota", "region_code", "kode"}
    if rows and not (set(rows[0].keys()) & required_any_region):
        return ExtractionResult(
            records=[],
            raw_row_count=len(rows),
            sheet_names=sheet_names,
            parser_notes=["MISSING_REQUIRED_COLUMNS"],
        )

    records: list[ExtractedRecord] = []
    for idx, row in enumerate(rows, start=2):
        raw_code = row.get("region_code") or row.get("kode")
        raw_region = (
            row.get("region")
            or row.get("region_name")
            or row.get("kab/kota")
            or row.get("kabupaten/kota")
        )
        raw_year = row.get("year") or row.get("tahun") or override_year
        metric_code = str(row.get("metric_code") or default_metric or "").strip()
        raw_val = row.get("value") if "value" in row else row.get("nilai")
        raw_unit = str(row.get("unit") or row.get("satuan") or default_unit or "").strip() or None
        records.append(
            ExtractedRecord(
                source_locator=f"{sheet_names[0]}!R{idx}",
                raw_code=raw_code,
                raw_region=raw_region,
                raw_year=raw_year,
                metric_code=metric_code,
                raw_value=raw_val,
                raw_unit=raw_unit,
                locale_policy=locale_policy,
            )
        )

    return ExtractionResult(
        records=records,
        raw_row_count=len(rows),
        sheet_names=sheet_names,
    )
