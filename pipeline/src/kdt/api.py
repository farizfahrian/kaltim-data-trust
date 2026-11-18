from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from kdt.db import create_kdt_engine, init_db
from kdt.service import (
    UnknownEntityError,
    get_active_snapshot,
    get_dataset_detail,
    get_metric_detail,
    get_observation_lineage,
    get_overview,
    get_quality_run_report,
    get_region_detail,
    get_snapshot_observations,
    list_datasets,
    list_metrics,
    list_regions,
)


def create_app(engine: Engine | None = None) -> FastAPI:
    db_engine = engine or create_kdt_engine()
    init_db(db_engine)
    SessionLocal = sessionmaker(bind=db_engine, expire_on_commit=False, future=True)

    app = FastAPI(
        title="Kaltim Data Trust API",
        version="1.0.0",
        description=(
            "Read-only public analytics & data provenance API for Kalimantan Timur health indicators. "
            "Every observation is served strictly from an audited published snapshot."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4321", "https://farizfahrian.com", "https://data-trust.farizfahrian.com"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def get_db() -> Iterator[Session]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @app.get("/api/v1/health")
    def health(db: Session = Depends(get_db)):
        snap = get_active_snapshot(db)
        return {
            "status": "ok",
            "active_snapshot_id": snap.snapshot_id if snap else None,
            "published_at": snap.published_at.isoformat() if snap else None,
        }

    @app.get("/api/v1/datasets")
    def datasets_endpoint(db: Session = Depends(get_db)):
        return {"data": list_datasets(db)}

    @app.get("/api/v1/datasets/{source_id}")
    def dataset_detail_endpoint(source_id: str, db: Session = Depends(get_db)):
        try:
            return get_dataset_detail(db, source_id)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/metrics")
    def metrics_endpoint(db: Session = Depends(get_db)):
        return {"data": list_metrics(db)}

    @app.get("/api/v1/metrics/{metric_code}")
    def metric_detail_endpoint(metric_code: str, db: Session = Depends(get_db)):
        try:
            return get_metric_detail(db, metric_code)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/regions")
    def regions_endpoint(db: Session = Depends(get_db)):
        return {"data": list_regions(db)}

    @app.get("/api/v1/regions/{region_code}")
    def region_detail_endpoint(
        region_code: str,
        year: int = Query(default=2024, ge=1990, le=2100),
        db: Session = Depends(get_db),
    ):
        try:
            return get_region_detail(db, region_code, year=year)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/observations")
    def observations_endpoint(
        metric: str | None = Query(default=None),
        year: int | None = Query(default=None, ge=1990, le=2100),
        region: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ):
        try:
            return get_snapshot_observations(
                db, metric_code=metric, year=year, region_code=region, limit=limit, offset=offset
            )
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/overview")
    def overview_endpoint(
        metric: str = Query(default="doctor_per_100k"),
        year: int = Query(default=2024, ge=1990, le=2100),
        db: Session = Depends(get_db),
    ):
        try:
            return get_overview(db, metric_code=metric, year=year)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/quality/runs/{run_id}")
    def quality_run_endpoint(run_id: str, db: Session = Depends(get_db)):
        try:
            return get_quality_run_report(db, run_id)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/lineage/{observation_id}")
    def lineage_endpoint(observation_id: str, db: Session = Depends(get_db)):
        try:
            return get_observation_lineage(db, observation_id)
        except UnknownEntityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()
