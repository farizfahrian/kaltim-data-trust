from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kdt.db import PROJECT_ROOT, create_kdt_engine, init_db, session_scope
from kdt.export_static import export_static_snapshot
from kdt.ingest import ingest_file
from kdt.publish import PublicationBlockedError, publish_snapshot
from kdt.registry import load_sources_manifest
from kdt.service import get_quality_run_report
from kdt.sniff import sniff_file


def _resolve_dirs(root: Path | None = None) -> tuple[Path, Path]:
    base = root or PROJECT_ROOT
    return base, base / "data"


def cmd_audit(args: argparse.Namespace) -> int:
    _, data_dir = _resolve_dirs()
    sources = load_sources_manifest(data_dir / "manifests" / "sources.yml")
    cfg = sources.get(args.source)
    reported = cfg.get("reported_format") if cfg else None
    res = sniff_file(Path(args.file), reported_format=reported)
    payload = {
        "source_id": args.source,
        "file": str(res.path),
        "size_bytes": res.size_bytes,
        "sha256": res.sha256,
        "reported_format": res.reported_format,
        "actual_format": res.actual_format,
        "warnings": res.warnings,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    _, data_dir = _resolve_dirs()
    engine = create_kdt_engine(args.db_url)
    init_db(engine)
    with session_scope(engine) as session:
        outcome = ingest_file(
            session,
            data_dir=data_dir,
            source_id=args.source,
            file_path=Path(args.file),
            override_year=args.year,
            dry_run=args.dry_run,
        )
        summary = {
            "run_id": outcome.run_id,
            "source_id": outcome.source_id,
            "sha256": outcome.sha256,
            "actual_format": outcome.actual_format,
            "status": outcome.status,
            "is_idempotent_reuse": outcome.is_idempotent_reuse,
            "is_dry_run": outcome.is_dry_run,
            "input_row_count": outcome.input_row_count,
            "output_row_count": outcome.output_row_count,
            "quarantined_row_count": outcome.quarantined_row_count,
            "failed_rules": [o.rule_code for o in outcome.outcomes if o.status == "fail"],
            "warning_rules": [o.rule_code for o in outcome.outcomes if o.status == "warn"],
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    engine = create_kdt_engine(args.db_url)
    init_db(engine)
    with session_scope(engine) as session:
        rep = get_quality_run_report(session, args.run_id)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        has_critical_fail = any(
            r["status"] == "fail" and r["severity"] == "critical" for r in rep["rules"]
        )
        return 1 if has_critical_fail else 0


def cmd_publish(args: argparse.Namespace) -> int:
    engine = create_kdt_engine(args.db_url)
    init_db(engine)
    run_ids = [args.run_id] if args.run_id else None
    try:
        with session_scope(engine) as session:
            snap_id = publish_snapshot(
                session,
                run_ids=run_ids,
                release_note=args.note or "CLI snapshot publication",
                demo_data=args.demo,
            )
            print(json.dumps({"snapshot_id": snap_id, "status": "active"}, indent=2))
        return 0
    except PublicationBlockedError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


def cmd_report(args: argparse.Namespace) -> int:
    engine = create_kdt_engine(args.db_url)
    init_db(engine)
    with session_scope(engine) as session:
        rep = get_quality_run_report(session, args.run_id)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote quality report to {out}")
    return 0


def cmd_build_official(args: argparse.Namespace) -> int:
    """Ingest all local audited sources (including quarantined audit target), publish eligible ones, and export static snapshot."""
    base_dir, data_dir = _resolve_dirs()
    sources = load_sources_manifest(data_dir / "manifests" / "sources.yml")
    engine = create_kdt_engine(args.db_url)
    init_db(engine)

    with session_scope(engine) as session:
        eligible_runs: list[str] = []
        for src_id, cfg in sources.items():
            raw_fname = cfg.get("local_raw_filename")
            if not raw_fname:
                continue
            fpath = data_dir / "raw" / raw_fname
            if not fpath.exists():
                raise FileNotFoundError(f"File sumber lokal belum tersedia: {fpath}")
            outcome = ingest_file(
                session,
                data_dir=data_dir,
                source_id=src_id,
                file_path=fpath,
            )
            print(
                f"[{src_id}] run={outcome.run_id} status={outcome.status} "
                f"out={outcome.output_row_count} quarantined={outcome.quarantined_row_count}"
            )
            if outcome.status in {"ELIGIBLE", "PUBLISHED"}:
                eligible_runs.append(outcome.run_id)

        snap_id = publish_snapshot(
            session,
            run_ids=eligible_runs,
            release_note="Snapshot terverifikasi data kesehatan publik Kalimantan Timur (baseline 2024 & tren dokter 2023–2025)",
            demo_data=False,
        )
        manifest = export_static_snapshot(
            session,
            public_api_dir=base_dir / "web" / "public" / "api" / "v1",
            astro_data_file=base_dir / "web" / "src" / "generated" / "snapshot.json",
            quality_artifacts_dir=base_dir / "artifacts" / "quality",
        )
        print(json.dumps({"published_snapshot": snap_id, "manifest": manifest}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kdt", description="Kaltim Data Trust pipeline CLI")
    parser.add_argument("--db-url", default=None, help="SQLAlchemy database URL override")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Sniff file bytes and compute SHA-256")
    p_audit.add_argument("--source", required=True)
    p_audit.add_argument("--file", required=True)
    p_audit.set_defaults(func=cmd_audit)

    p_ing = sub.add_parser("ingest", help="Ingest and validate a verified file")
    p_ing.add_argument("--source", required=True)
    p_ing.add_argument("--file", required=True)
    p_ing.add_argument("--year", type=int, default=None)
    p_ing.add_argument("--dry-run", action="store_true")
    p_ing.set_defaults(func=cmd_ingest)

    p_val = sub.add_parser("validate", help="Inspect validation outcomes for a run")
    p_val.add_argument("--run-id", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_pub = sub.add_parser("publish", help="Atomically publish eligible runs into active snapshot")
    p_pub.add_argument("--run-id", default=None)
    p_pub.add_argument("--note", default=None)
    p_pub.add_argument("--demo", action="store_true")
    p_pub.set_defaults(func=cmd_publish)

    p_rep = sub.add_parser("report", help="Export sanitized JSON quality report for a run")
    p_rep.add_argument("--run-id", required=True)
    p_rep.add_argument("--output", required=True)
    p_rep.set_defaults(func=cmd_report)

    p_build = sub.add_parser(
        "build-official",
        help="Run end-to-end ingestion, quality gate, snapshot publication, and static API export",
    )
    p_build.set_defaults(func=cmd_build_official)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
