from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from kdt.api import create_app
from kdt.db import PROJECT_ROOT, create_kdt_engine, init_db, session_scope
from kdt.ingest import ingest_file
from kdt.publish import PublicationBlockedError, publish_snapshot
from kdt.sniff import FileSecurityOrFormatError, sniff_file

DATA_DIR = PROJECT_ROOT / "data"


def _make_source_cfg(
    source_id: str = "SYN-TEST",
    *,
    reported_format: str = "CSV",
    reference_years: list[int] | None = None,
    code_system: str = "kemendagri",
    locale_policy: str = "strict",
) -> dict[str, Any]:
    yrs = reference_years or [2024, 2025]
    return {
        "source_id": source_id,
        "title": f"Synthetic Fixture {source_id}",
        "publisher": "Synthetic QA Suite",
        "dataset_url": "https://data.kaltimprov.go.id/dataset/example",
        "resource_url": "https://data.kaltimprov.go.id/dataset/example/resource",
        "reported_format": reported_format,
        "actual_format": reported_format,
        "license_statement": "Synthetic test fixture",
        "publication_allowed": "synthetic_test",
        "reference_years": yrs,
        "published_years": yrs,
        "region_code_system": code_system,
        "parser_adapter": "generic_tabular",
        "locale_policy": locale_policy,
    }


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "test_kdt.db"
    engine = create_kdt_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return engine


def _register_custom_source(session, cfg: dict[str, Any]) -> None:
    from kdt.models import Source

    if session.get(Source, cfg["source_id"]) is None:
        session.add(
            Source(
                source_id=cfg["source_id"],
                title=cfg["title"],
                publisher=cfg["publisher"],
                dataset_url=cfg["dataset_url"],
                resource_url=cfg["resource_url"],
                reported_format=cfg["reported_format"],
                actual_format=cfg["actual_format"],
                license_status=cfg["license_statement"],
                publication_allowed=cfg["publication_allowed"],
                region_code_system=cfg["region_code_system"],
            )
        )
        session.flush()


# T-001: Approved alias normalizes to canonical Kemendagri region code + BPS bridge check
def test_t001_approved_region_alias_and_bps_bridge(db_engine, tmp_path: Path):
    csv_file = tmp_path / "aliases.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,500,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T001")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T001",
            file_path=csv_file,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.status == "ELIGIBLE"
        assert out.output_row_count == 1
        snap = publish_snapshot(session, run_ids=[out.run_id], demo_data=True)
        assert snap.startswith("snap-")

    client = TestClient(create_app(db_engine))
    res = client.get("/api/v1/observations?metric=doctor_total_count&year=2024&region=64.72")
    assert res.status_code == 200
    assert res.json()["data"][0]["value"] == 500


# T-002: Unknown region name is quarantined, never fuzzy-matched to similar region
def test_t002_unknown_region_quarantined(db_engine, tmp_path: Path):
    csv_file = tmp_path / "unknown_region.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarindaa Baru,2024,doctor_total_count,500,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T002")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T002",
            file_path=csv_file,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.status == "QUARANTINED"
        assert out.quarantined_row_count == 1
        assert any(o.rule_code == "DQ-003" and o.status == "fail" for o in out.outcomes)


# T-003: Missing year triggers critical DQ-006 failure and blocks publication
def test_t003_missing_year_blocks_publish(db_engine, tmp_path: Path):
    csv_file = tmp_path / "missing_year.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,,doctor_total_count,500,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T003")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T003",
            file_path=csv_file,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.status == "QUARANTINED"
        assert any(o.rule_code == "DQ-006" and o.status == "fail" for o in out.outcomes)
        with pytest.raises(PublicationBlockedError):
            publish_snapshot(session, run_ids=[out.run_id], demo_data=True)


# T-004: Duplicate business key fails DQ-002 critically without double counting
def test_t004_duplicate_key_blocked(db_engine, tmp_path: Path):
    csv_file = tmp_path / "dup.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,500,orang\n"
        "Samarinda,2024,doctor_total_count,510,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T004")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T004",
            file_path=csv_file,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.status == "QUARANTINED"
        assert any(o.rule_code == "DQ-002" and o.status == "fail" for o in out.outcomes)


# T-005: Ambiguous locale string '1.250' is rejected in strict mode, parsed only under explicit rule
def test_t005_ambiguous_locale_number_policy(db_engine, tmp_path: Path):
    csv_file = tmp_path / "locale_num.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,1.250,orang\n",
        encoding="utf-8",
    )
    strict_cfg = _make_source_cfg("SYN-T005-STRICT", locale_policy="strict")
    id_cfg = _make_source_cfg("SYN-T005-ID", locale_policy="id_thousands")
    with session_scope(db_engine) as session:
        _register_custom_source(session, strict_cfg)
        _register_custom_source(session, id_cfg)
        out_strict = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T005-STRICT",
            file_path=csv_file,
            custom_source_cfg=strict_cfg,
            is_synthetic=True,
        )
        assert out_strict.status == "QUARANTINED"
        assert any(o.rule_code == "DQ-004" and o.status == "fail" for o in out_strict.outcomes)

        out_id = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T005-ID",
            file_path=csv_file,
            custom_source_cfg=id_cfg,
            is_synthetic=True,
        )
        assert out_id.status == "ELIGIBLE"


# T-006: Dash '-' or 'TAD' becomes NULL with missing_in_source, never 0
def test_t006_dash_sentinel_becomes_null_not_zero(db_engine, tmp_path: Path):
    csv_file = tmp_path / "dash.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,1000,orang\n"
        "Kabupaten Mahakam Ulu,2024,doctor_total_count,-,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T006")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T006",
            file_path=csv_file,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.status == "ELIGIBLE"
        publish_snapshot(session, run_ids=[out.run_id], demo_data=True)

    client = TestClient(create_app(db_engine))
    res = client.get("/api/v1/observations?metric=doctor_total_count&year=2024&region=64.11")
    obs = res.json()["data"][0]
    assert obs["value"] is None
    assert obs["missing_reason"] == "missing_in_source"


# T-007, T-008, T-009, T-014, T-015, T-016, T-017: Ratios, year mismatch, zero denominator, missing regions, API & lineage
def test_t007_t008_t009_t014_t015_t016_t017_ratios_and_api_contracts(db_engine, tmp_path: Path):
    doc_csv = tmp_path / "docs_2024.csv"
    doc_csv.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,1000,orang\n"
        "Kabupaten Mahakam Ulu,2024,doctor_total_count,20,orang\n"
        "Kota Samarinda,2025,doctor_total_count,1100,orang\n",
        encoding="utf-8",
    )
    pop_csv = tmp_path / "pop_2024.csv"
    pop_csv.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,population_count,800000,jiwa\n"
        "Kabupaten Mahakam Ulu,2024,population_count,0,jiwa\n",
        encoding="utf-8",
    )
    cfg_doc = _make_source_cfg("SYN-DOC-R", reference_years=[2024, 2025])
    cfg_pop = _make_source_cfg("SYN-POP-R", reference_years=[2024])

    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg_doc)
        _register_custom_source(session, cfg_pop)
        r_doc = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-DOC-R", file_path=doc_csv, custom_source_cfg=cfg_doc, is_synthetic=True
        )
        r_pop = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-POP-R", file_path=pop_csv, custom_source_cfg=cfg_pop, is_synthetic=True
        )
        publish_snapshot(session, run_ids=[r_doc.run_id, r_pop.run_id], demo_data=True)

    client = TestClient(create_app(db_engine))

    # T-009: Valid ratio (1000 / 800000 * 100000 = 125.0)
    ov_2024 = client.get("/api/v1/overview?metric=doctor_per_100k&year=2024").json()
    by_reg_24 = {r["region_code"]: r for r in ov_2024["data"]}
    assert by_reg_24["64.72"]["value"] == 125.0
    assert by_reg_24["64.72"]["quality_status"] == "validated"

    # T-008: Denominator 0 -> not_computable, value=None
    assert by_reg_24["64.11"]["value"] is None
    assert by_reg_24["64.11"]["quality_status"] == "not_computable"
    assert by_reg_24["64.11"]["missing_reason"] == "denominator_not_computable"

    # T-014: Missing region (e.g. Paser 64.01) explicitly present in overview with missing status
    assert "64.01" in by_reg_24
    assert by_reg_24["64.01"]["value"] is None

    # T-007: Year mismatch (doctors 2025, population only 2024) -> not_comparable / year_mismatch
    ov_2025 = client.get("/api/v1/overview?metric=doctor_per_100k&year=2025").json()
    by_reg_25 = {r["region_code"]: r for r in ov_2025["data"]}
    assert by_reg_25["64.72"]["value"] is None
    assert by_reg_25["64.72"]["quality_status"] == "not_comparable"
    assert by_reg_25["64.72"]["missing_reason"] == "year_mismatch"

    # T-015 & T-017: Validated observation has period, unit, sources, and traceable lineage with official URL
    obs_id = by_reg_24["64.72"]["observation_id"]
    lin = client.get(f"/api/v1/lineage/{obs_id}")
    assert lin.status_code == 200
    lin_data = lin.json()
    assert lin_data["year"] == 2024
    assert lin_data["unit"] == "orang/100.000 penduduk"
    roles = {u["role"]: u for u in lin_data["upstream_inputs"]}
    assert "numerator" in roles and "denominator" in roles
    assert roles["numerator"]["dataset_url"].startswith("https://data.kaltimprov.go.id/")
    assert len(roles["numerator"]["source_sha256"]) == 64

    # T-016: Unknown metric returns structured 404
    bad_metric = client.get("/api/v1/overview?metric=invented_metric&year=2024")
    assert bad_metric.status_code == 404


# T-010: Subcategory exceeding reported total fails DQ-007 and never sums overlapping subtypes
def test_t010_overlapping_or_inconsistent_subtypes_blocked(db_engine, tmp_path: Path):
    csv_file = tmp_path / "subtypes_bad.csv"
    csv_file.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Berau,2024,doctor_total_count,185,orang\n"
        "Berau,2024,doctor_general_count,197,orang\n"
        "Berau,2024,doctor_specialist_count,66,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T010", reference_years=[2024])
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-T010", file_path=csv_file, custom_source_cfg=cfg, is_synthetic=True
        )
        assert out.status == "QUARANTINED"
        assert any(o.rule_code == "DQ-007" and o.status == "fail" for o in out.outcomes)


# T-011 & T-020: Re-importing identical file is idempotent; editing bytes creates new versioned run
def test_t011_and_t020_idempotency_and_byte_change_versioning(db_engine, tmp_path: Path):
    f1 = tmp_path / "doctors.csv"
    f1.write_text(
        "region_name,year,metric_code,value,unit\nKota Samarinda,2024,doctor_total_count,1000,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-IDEM")
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        run1 = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-IDEM", file_path=f1, custom_source_cfg=cfg, is_synthetic=True
        )
        run2 = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-IDEM", file_path=f1, custom_source_cfg=cfg, is_synthetic=True
        )
        assert run1.run_id == run2.run_id
        assert run2.is_idempotent_reuse is True

        # T-020: Edit bytes -> new hash & new run_id
        f1.write_text(
            "region_name,year,metric_code,value,unit\nKota Samarinda,2024,doctor_total_count,1050,orang\n",
            encoding="utf-8",
        )
        run3 = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-IDEM", file_path=f1, custom_source_cfg=cfg, is_synthetic=True
        )
        assert run3.run_id != run1.run_id
        assert run3.sha256 != run1.sha256


# T-012: File named .csv / labeled CSV containing XLSX bytes is sniffed as XLSX and parsed with warning
def test_t012_mislabeled_csv_with_xlsx_bytes(db_engine, tmp_path: Path):
    fake_csv_path = tmp_path / "1_TENAGA_DOKTER.csv"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["region_name", "year", "metric_code", "value", "unit"])
    ws.append(["Kota Balikpapan", 2024, "doctor_total_count", 1063, "orang"])
    real_xlsx = tmp_path / "temp.xlsx"
    wb.save(real_xlsx)
    fake_csv_path.write_bytes(real_xlsx.read_bytes())

    cfg = _make_source_cfg("SYN-T012", reported_format="CSV", reference_years=[2024])
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session,
            data_dir=DATA_DIR,
            source_id="SYN-T012",
            file_path=fake_csv_path,
            custom_source_cfg=cfg,
            is_synthetic=True,
        )
        assert out.actual_format == "XLSX"
        assert out.status == "ELIGIBLE"
        assert any(o.rule_code == "DQ-001" and o.status == "warn" for o in out.outcomes)


# T-013: Critical failure in new import leaves previously active published snapshot intact
def test_t013_failed_run_preserves_active_snapshot(db_engine, tmp_path: Path):
    good_csv = tmp_path / "good.csv"
    good_csv.write_text(
        "region_name,year,metric_code,value,unit\nKota Samarinda,2024,doctor_total_count,1176,orang\n",
        encoding="utf-8",
    )
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "region_name,year,metric_code,value,unit\nWilayah Fiktif,2024,doctor_total_count,-50,orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T013", reference_years=[2024])
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        good_run = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-T013", file_path=good_csv, custom_source_cfg=cfg, is_synthetic=True
        )
        snap1 = publish_snapshot(session, run_ids=[good_run.run_id], demo_data=True)

        bad_run = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-T013", file_path=bad_csv, custom_source_cfg=cfg, is_synthetic=True
        )
        assert bad_run.status == "QUARANTINED"
        with pytest.raises(PublicationBlockedError):
            publish_snapshot(session, run_ids=[bad_run.run_id], demo_data=True)

    client = TestClient(create_app(db_engine))
    health = client.get("/api/v1/health").json()
    assert health["active_snapshot_id"] == snap1
    obs = client.get("/api/v1/observations?metric=doctor_total_count&year=2024&region=64.72").json()
    assert obs["data"][0]["value"] == 1176


# T-019: Malicious VBA macro XLSX or formula injection string fails safely without execution
def test_t019_malicious_macro_and_formula_injection_rejected(db_engine, tmp_path: Path):
    # 1. XLSX containing xl/vbaProject.bin
    wb = openpyxl.Workbook()
    clean_xlsx = tmp_path / "base.xlsx"
    wb.save(clean_xlsx)
    macro_xlsx = tmp_path / "evil_macro.xlsx"
    with zipfile.ZipFile(clean_xlsx, "r") as zin, zipfile.ZipFile(macro_xlsx, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("xl/vbaProject.bin", b"FAKE_VBA_MACRO_PAYLOAD")

    with pytest.raises(FileSecurityOrFormatError, match="VBA"):
        sniff_file(macro_xlsx)

    # 2. CSV formula injection string (=CMD|' /C calc'!A0)
    evil_csv = tmp_path / "formula.csv"
    evil_csv.write_text(
        "region_name,year,metric_code,value,unit\n"
        "Kota Samarinda,2024,doctor_total_count,\"=CMD|' /C calc'!A0\",orang\n",
        encoding="utf-8",
    )
    cfg = _make_source_cfg("SYN-T019", reference_years=[2024])
    with session_scope(db_engine) as session:
        _register_custom_source(session, cfg)
        out = ingest_file(
            session, data_dir=DATA_DIR, source_id="SYN-T019", file_path=evil_csv, custom_source_cfg=cfg, is_synthetic=True
        )
        assert out.status == "QUARANTINED"
        assert any(o.rule_code == "DQ-004" and o.status == "fail" for o in out.outcomes)


# GIS-01: Verify 10/10 Kalimantan Timur GeoJSON boundary alignment, zero unmatched shapes, and /api/v1/geo/regions
def test_gis01_geojson_alignment_and_api_endpoint(db_engine, tmp_path: Path):
    import json

    from kdt.geo import GeoAlignmentError, load_and_validate_kaltim_geojson

    payload, summary = load_and_validate_kaltim_geojson(DATA_DIR)
    assert summary.feature_count == 10
    assert summary.unmatched_shapes == []
    assert summary.missing_target_regions == []
    assert "CC BY 3.0 IGO" in summary.license

    # Inject an unmatched shape (e.g. Bulungan from North Kalimantan) -> must fail GIS-01 gate
    tampered = json.loads(json.dumps(payload))
    tampered["features"].append(
        {
            "type": "Feature",
            "properties": {"region_code": "65.01", "source_shape_name": "Bulungan"},
            "geometry": {"type": "Polygon", "coordinates": [[[117.0, 2.5], [117.2, 2.5], [117.2, 2.7], [117.0, 2.5]]]},
        }
    )
    bad_geo = tmp_path / "tampered.geojson"
    bad_geo.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(GeoAlignmentError, match="unmatched"):
        load_and_validate_kaltim_geojson(DATA_DIR, geojson_path=bad_geo)

    client = TestClient(create_app(db_engine))
    res = client.get("/api/v1/geo/regions")
    assert res.status_code == 200
    assert len(res.json()["features"]) == 10

