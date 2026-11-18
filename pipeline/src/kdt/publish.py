from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from kdt.metrics import derive_metrics
from kdt.models import (
    CuratedFact,
    DerivedFact,
    ImportRun,
    PublishedSnapshot,
    SnapshotDerivedFact,
    SnapshotFact,
)


class PublicationBlockedError(RuntimeError):
    """Raised when a quarantined/failed run is explicitly requested for publication."""


def publish_snapshot(
    session: Session,
    *,
    run_ids: list[str] | None = None,
    release_note: str = "Automated verified snapshot publication",
    demo_data: bool = False,
) -> str:
    """
    Atomically publish a validated snapshot.
    - If `run_ids` contains any QUARANTINED/DRY_RUN/HELD run, raises PublicationBlockedError
      WITHOUT altering the currently active snapshot (T-013).
    - Supersedes any previously active snapshot within the same DB transaction.
    """
    if run_ids is not None:
        runs = (
            session.execute(
                select(ImportRun)
                .where(ImportRun.run_id.in_(run_ids))
                .order_by(ImportRun.started_at.asc(), ImportRun.run_id.asc())
            )
            .scalars()
            .all()
        )
        if len(runs) != len(set(run_ids)):
            raise PublicationBlockedError("Sebagian run_id yang diminta tidak ditemukan.")
        bad_runs = [r for r in runs if r.status not in {"ELIGIBLE", "PUBLISHED"}]
        if bad_runs:
            bad_desc = ", ".join(f"{r.run_id} ({r.source_id}: {r.status})" for r in bad_runs)
            raise PublicationBlockedError(
                f"Publikasi ditolak oleh Quality Gate; run berikut belum lolos: {bad_desc}"
            )
    else:
        runs = (
            session.execute(
                select(ImportRun)
                .where(ImportRun.status.in_(["ELIGIBLE", "PUBLISHED"]))
                .order_by(ImportRun.started_at.asc(), ImportRun.run_id.asc())
            )
            .scalars()
            .all()
        )

    if not runs:
        raise PublicationBlockedError("Tidak ada run ELIGIBLE yang dapat dipublikasikan.")

    # Select one authoritative run per source_id (latest started_at)
    chosen_by_source: dict[str, ImportRun] = {}
    for r in runs:
        chosen_by_source[r.source_id] = r
    chosen_run_ids = sorted(r.run_id for r in chosen_by_source.values())

    drun_id = derive_metrics(session, eligible_run_ids=chosen_run_ids)

    snap_seed = "|".join(chosen_run_ids) + f"|{drun_id}|demo={demo_data}"
    snap_hash = hashlib.sha256(snap_seed.encode("utf-8")).hexdigest()[:16]
    snapshot_id = f"snap-{snap_hash}"

    existing_snap = session.get(PublishedSnapshot, snapshot_id)
    if existing_snap is not None and existing_snap.status == "active":
        return snapshot_id

    # Supersede previous active snapshots atomically
    prev_actives = (
        session.execute(select(PublishedSnapshot).where(PublishedSnapshot.status == "active"))
        .scalars()
        .all()
    )
    for prev in prev_actives:
        prev.status = "superseded"

    now = datetime.now(UTC)
    if existing_snap is not None:
        existing_snap.status = "active"
        existing_snap.published_at = now
        existing_snap.release_note = release_note
    else:
        session.add(
            PublishedSnapshot(
                snapshot_id=snapshot_id,
                published_at=now,
                status="active",
                release_note=release_note,
                demo_data=demo_data,
            )
        )
        session.flush()

        # Attach only non-blocked curated facts (FR-012 / KPI Invalid publication = 0)
        c_facts = (
            session.execute(
                select(CuratedFact).where(
                    CuratedFact.run_id.in_(chosen_run_ids),
                    CuratedFact.quality_status != "blocked",
                )
            )
            .scalars()
            .all()
        )
        for cf in c_facts:
            session.add(SnapshotFact(snapshot_id=snapshot_id, fact_id=cf.fact_id))

        # Attach derived facts (status placeholders with value=None for not_comparable/not_computable are kept for honest UI explanations; blocked numeric values never exist)
        d_facts = (
            session.execute(select(DerivedFact).where(DerivedFact.derivation_run_id == drun_id))
            .scalars()
            .all()
        )
        for df in d_facts:
            session.add(SnapshotDerivedFact(snapshot_id=snapshot_id, derived_fact_id=df.derived_fact_id))

    for r in chosen_by_source.values():
        r.status = "PUBLISHED"

    session.flush()
    return snapshot_id
