from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from kdt.models import (
    CuratedFact,
    DerivationRun,
    DerivedFact,
    DerivedFactInput,
    ImportRun,
    Metric,
    Region,
)

TWO_PLACES = Decimal("0.01")


def derive_metrics(
    session: Session,
    *,
    eligible_run_ids: list[str] | None = None,
    methodology_version: str = "1.0.0",
) -> str:
    """
    Deterministically compute derived indicators from ELIGIBLE/PUBLISHED source runs.
    Enforces exact (region_code, reference_year) compatibility and records full input lineage.
    """
    if eligible_run_ids is None:
        runs = (
            session.execute(
                select(ImportRun)
                .where(ImportRun.status.in_(["ELIGIBLE", "PUBLISHED"]))
                .order_by(ImportRun.started_at.asc(), ImportRun.run_id.asc())
            )
            .scalars()
            .all()
        )
    else:
        runs = (
            session.execute(
                select(ImportRun)
                .where(ImportRun.run_id.in_(eligible_run_ids))
                .order_by(ImportRun.started_at.asc(), ImportRun.run_id.asc())
            )
            .scalars()
            .all()
        )

    if not runs:
        raise ValueError("Tidak ada run berstatus ELIGIBLE/PUBLISHED untuk perhitungan metrik turunan.")

    # Latest eligible run per source_id (explicit source selection, never blind union)
    selected_run_by_source: dict[str, ImportRun] = {}
    for r in runs:
        selected_run_by_source[r.source_id] = r

    selected_run_ids = sorted(r.run_id for r in selected_run_by_source.values())
    sig_raw = "|".join(f"{r_id}" for r_id in selected_run_ids) + f"|v={methodology_version}"
    input_signature = hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()

    existing_drun = session.execute(
        select(DerivationRun).where(
            DerivationRun.input_signature == input_signature,
            DerivationRun.methodology_version == methodology_version,
        )
    ).scalar_one_or_none()

    if existing_drun is not None:
        return existing_drun.derivation_run_id

    drun_id = f"drun-{uuid.uuid5(uuid.NAMESPACE_URL, input_signature).hex[:16]}"
    now = datetime.now(UTC)
    session.add(
        DerivationRun(
            derivation_run_id=drun_id,
            input_signature=input_signature,
            methodology_version=methodology_version,
            started_at=now,
            finished_at=now,
            status="VALIDATED",
        )
    )
    session.flush()

    facts = (
        session.execute(select(CuratedFact).where(CuratedFact.run_id.in_(selected_run_ids)))
        .scalars()
        .all()
    )
    metrics_meta = {
        m.metric_code: m for m in session.execute(select(Metric)).scalars().all()
    }
    all_regions = [r.region_code for r in session.execute(select(Region)).scalars().all()]

    # Index curated facts by (metric_code, region_code, reference_year)
    fact_idx: dict[tuple[str, str, int], CuratedFact] = {}
    years_by_metric: dict[str, set[int]] = {}
    all_years: set[int] = set()

    for f in facts:
        fact_idx[(f.metric_code, f.region_code, f.reference_year)] = f
        if f.value_numeric is not None and f.quality_status in {"validated", "warning"}:
            years_by_metric.setdefault(f.metric_code, set()).add(f.reference_year)
        all_years.add(f.reference_year)

    def _save_derived(
        region_code: str,
        year: int,
        metric_code: str,
        value: Decimal | None,
        status: str,
        missing_reason: str | None,
        inputs: list[tuple[CuratedFact, str]],
    ) -> None:
        if metric_code not in metrics_meta:
            return
        df_seed = f"{drun_id}:{region_code}:{year}:{metric_code}"
        df_id = f"derf-{uuid.uuid5(uuid.NAMESPACE_URL, df_seed).hex[:16]}"
        session.add(
            DerivedFact(
                derived_fact_id=df_id,
                derivation_run_id=drun_id,
                region_code=region_code,
                reference_year=year,
                metric_code=metric_code,
                value_numeric=value,
                unit=metrics_meta[metric_code].unit,
                missing_reason=missing_reason,
                quality_status=status,
                methodology_version=methodology_version,
            )
        )
        session.flush()
        for src_fact, role in inputs:
            session.add(
                DerivedFactInput(
                    derived_fact_id=df_id,
                    source_fact_id=src_fact.fact_id,
                    input_role=role,
                )
            )

    # 1. Additive disjoint facility sums: puskesmas_total_count & facility_total_count
    pkm_tot_cache: dict[tuple[str, int], tuple[Decimal | None, str, list[CuratedFact]]] = {}
    fac_tot_cache: dict[tuple[str, int], tuple[Decimal | None, str, list[CuratedFact]]] = {}

    for yr in sorted(all_years):
        has_any_pkm_yr = (
            yr in years_by_metric.get("puskesmas_inpatient_count", set())
            or yr in years_by_metric.get("puskesmas_non_inpatient_count", set())
        )
        has_any_rs_yr = yr in years_by_metric.get("hospital_total_count", set())

        for reg in all_regions:
            pkm_ri = fact_idx.get(("puskesmas_inpatient_count", reg, yr))
            pkm_nri = fact_idx.get(("puskesmas_non_inpatient_count", reg, yr))

            if has_any_pkm_yr and (pkm_ri is not None or pkm_nri is not None):
                inputs_pkm = []
                if pkm_ri:
                    inputs_pkm.append((pkm_ri, "numerator"))
                if pkm_nri:
                    inputs_pkm.append((pkm_nri, "supporting"))

                if pkm_ri is None or pkm_nri is None:
                    val_pkm, st_pkm, mr_pkm = None, "not_comparable", "year_mismatch"
                elif pkm_ri.quality_status == "blocked" or pkm_nri.quality_status == "blocked":
                    val_pkm, st_pkm, mr_pkm = None, "blocked", "blocked_quality"
                elif pkm_ri.value_numeric is None or pkm_nri.value_numeric is None:
                    val_pkm, st_pkm, mr_pkm = None, "not_computable", "component_missing"
                else:
                    val_pkm = pkm_ri.value_numeric + pkm_nri.value_numeric
                    st_pkm = (
                        "warning"
                        if "warning" in {pkm_ri.quality_status, pkm_nri.quality_status}
                        else "validated"
                    )
                    mr_pkm = None

                _save_derived(reg, yr, "puskesmas_total_count", val_pkm, st_pkm, mr_pkm, inputs_pkm)
                pkm_tot_cache[(reg, yr)] = (val_pkm, st_pkm, [f for f, _ in inputs_pkm])

            rs = fact_idx.get(("hospital_total_count", reg, yr))
            if (has_any_pkm_yr or has_any_rs_yr) and (rs is not None or pkm_ri is not None or pkm_nri is not None):
                inputs_fac = []
                if rs:
                    inputs_fac.append((rs, "numerator"))
                if pkm_ri:
                    inputs_fac.append((pkm_ri, "supporting"))
                if pkm_nri:
                    inputs_fac.append((pkm_nri, "supporting"))

                if rs is None or pkm_ri is None or pkm_nri is None:
                    val_fac, st_fac, mr_fac = None, "not_comparable", "year_mismatch"
                elif "blocked" in {rs.quality_status, pkm_ri.quality_status, pkm_nri.quality_status}:
                    val_fac, st_fac, mr_fac = None, "blocked", "blocked_quality"
                elif rs.value_numeric is None or pkm_ri.value_numeric is None or pkm_nri.value_numeric is None:
                    val_fac, st_fac, mr_fac = None, "not_computable", "component_missing"
                else:
                    val_fac = rs.value_numeric + pkm_ri.value_numeric + pkm_nri.value_numeric
                    st_fac = (
                        "warning"
                        if "warning" in {rs.quality_status, pkm_ri.quality_status, pkm_nri.quality_status}
                        else "validated"
                    )
                    mr_fac = None

                _save_derived(reg, yr, "facility_total_count", val_fac, st_fac, mr_fac, inputs_fac)
                fac_tot_cache[(reg, yr)] = (val_fac, st_fac, [f for f, _ in inputs_fac])

    # 2. Cross-source per-100,000 population ratios (FR-011, T-007, T-008, T-009)
    pop_years_anywhere = years_by_metric.get("population_count", set())

    ratio_specs = [
        ("doctor_per_100k", "doctor_total_count"),
        ("doctor_general_per_100k", "doctor_general_count"),
    ]

    for derived_code, num_code in ratio_specs:
        for yr in sorted(all_years):
            for reg in all_regions:
                num_fact = fact_idx.get((num_code, reg, yr))
                pop_fact = fact_idx.get(("population_count", reg, yr))

                if num_fact is None and pop_fact is None:
                    continue

                inputs_r: list[tuple[CuratedFact, str]] = []
                if num_fact is not None:
                    inputs_r.append((num_fact, "numerator"))
                if pop_fact is not None:
                    inputs_r.append((pop_fact, "denominator"))

                if num_fact is None or pop_fact is None:
                    # One side exists in this year while the other only exists in another year (or not at all)
                    reason = "year_mismatch" if pop_years_anywhere or years_by_metric.get(num_code) else "denominator_not_computable"
                    _save_derived(reg, yr, derived_code, None, "not_comparable", reason, inputs_r)
                    continue

                if num_fact.quality_status == "blocked" or pop_fact.quality_status == "blocked":
                    _save_derived(reg, yr, derived_code, None, "blocked", "blocked_quality", inputs_r)
                    continue

                if (
                    num_fact.value_numeric is None
                    or pop_fact.value_numeric is None
                    or pop_fact.value_numeric <= 0
                ):
                    _save_derived(
                        reg,
                        yr,
                        derived_code,
                        None,
                        "not_computable",
                        "denominator_not_computable",
                        inputs_r,
                    )
                    continue

                ratio_val = (
                    (num_fact.value_numeric / pop_fact.value_numeric) * Decimal("100000")
                ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                st = (
                    "warning"
                    if "warning" in {num_fact.quality_status, pop_fact.quality_status}
                    else "validated"
                )
                _save_derived(reg, yr, derived_code, ratio_val, st, None, inputs_r)

    # 3. facility_per_100k from fac_tot_cache + population_count
    for yr in sorted(all_years):
        for reg in all_regions:
            fac_entry = fac_tot_cache.get((reg, yr))
            pop_fact = fact_idx.get(("population_count", reg, yr))
            if fac_entry is None and pop_fact is None:
                continue

            inputs_fr: list[tuple[CuratedFact, str]] = []
            if fac_entry is not None:
                for idx_f, sf in enumerate(fac_entry[2]):
                    inputs_fr.append((sf, "numerator" if idx_f == 0 else "supporting"))
            if pop_fact is not None:
                inputs_fr.append((pop_fact, "denominator"))

            if fac_entry is None or pop_fact is None:
                _save_derived(reg, yr, "facility_per_100k", None, "not_comparable", "year_mismatch", inputs_fr)
                continue

            fac_val, fac_st, _ = fac_entry
            if fac_st == "blocked" or pop_fact.quality_status == "blocked":
                _save_derived(reg, yr, "facility_per_100k", None, "blocked", "blocked_quality", inputs_fr)
            elif fac_val is None or pop_fact.value_numeric is None or pop_fact.value_numeric <= 0:
                _save_derived(
                    reg, yr, "facility_per_100k", None, "not_computable", "denominator_not_computable", inputs_fr
                )
            else:
                ratio_val = ((fac_val / pop_fact.value_numeric) * Decimal("100000")).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
                st = "warning" if "warning" in {fac_st, pop_fact.quality_status} else "validated"
                _save_derived(reg, yr, "facility_per_100k", ratio_val, st, None, inputs_fr)

    session.flush()
    return drun_id
