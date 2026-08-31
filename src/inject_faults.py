"""Opt-in fault injection for demonstration purposes only.

Open-Meteo serves model/reanalysis output, not raw sensor telemetry -- it is
already quality-controlled, so real data over any 30-day window has ~zero
null gaps and ~zero Tier-1 physical-validity violations (verified: see
ai-artifacts/build-log.md). That means the interpolation path and the
Tier-1 detector are real but practically undemonstrable on real data.

This script corrupts a handful of values in a COPY of the cleaned wide
frame -- never the real pipeline's data -- and writes the result to a
separate, clearly-named database (data/demo_faulty.duckdb). It never runs
as part of `pipeline.py` and never touches data/openmeteo.duckdb. The
dashboard must show an unmistakable banner whenever this file is loaded.

Run explicitly: `python src/inject_faults.py`
"""

import pandas as pd

from anomaly import compute_anomalies
from clean import build_wide_frame, clean
from config import DEMO_DB_PATH, SITES
from fetch import fetch_all
from store import load


def _site_df(wide: pd.DataFrame, site_id: str) -> pd.DataFrame:
    return wide[wide["site_id"] == site_id].sort_values("timestamp")


def _daylight_indices(wide: pd.DataFrame, site_id: str, day_offset: int, length: int) -> pd.Index:
    """Original-frame index labels for the first `length` daylight (toa>0)
    hours on the day `day_offset` days after this site's first date."""
    site_df = _site_df(wide, site_id)
    dates = sorted(site_df["timestamp"].dt.date.unique())
    target_date = dates[day_offset]
    day_df = site_df[(site_df["timestamp"].dt.date == target_date) & (site_df["toa"] > 0)]
    return day_df.sort_values("timestamp").index[:length]


def _day_indices(wide: pd.DataFrame, site_id: str, day_offset: int) -> pd.Index:
    """Original-frame index labels for all 24 hours of the day `day_offset`
    days after this site's first date."""
    site_df = _site_df(wide, site_id)
    dates = sorted(site_df["timestamp"].dt.date.unique())
    target_date = dates[day_offset]
    return site_df[site_df["timestamp"].dt.date == target_date].index


def inject(wide: pd.DataFrame) -> pd.DataFrame:
    wide = wide.copy()
    site_a, site_b, site_c = (s["site_id"] for s in SITES)

    # 1. Short gap (2h, <= SHORT_GAP_MAX_HOURS): interpolation path.
    idx = _daylight_indices(wide, site_a, day_offset=4, length=2)
    wide.loc[idx, "ghi"] = float("nan")

    # 2. Long gap (5h, > SHORT_GAP_MAX_HOURS): left null, "missing" path.
    idx = _day_indices(wide, site_a, day_offset=5)[:5]
    wide.loc[idx, "wind"] = float("nan")

    # 3. Negative wind: Tier 1 physical rule.
    idx = _day_indices(wide, site_b, day_offset=6)[10:11]
    wide.loc[idx, "wind"] = -3.0

    # 4. GHI > TOA ceiling breach: Tier 1 physical rule.
    idx = _daylight_indices(wide, site_b, day_offset=7, length=1)
    wide.loc[idx, "ghi"] = wide.loc[idx, "toa"] * 1.5

    # 5. Solar flatline: identical value across a run of daylight hours, Tier 1 collective rule.
    idx = _daylight_indices(wide, site_c, day_offset=8, length=8)
    wide.loc[idx, "ghi"] = 400.0

    # 6. All-zero wind day: Tier 1 collective rule (explicitly named in the brief).
    idx = _day_indices(wide, site_c, day_offset=9)
    wide.loc[idx, "wind"] = 0.0

    return wide


def run() -> None:
    # Inject BEFORE clean() so the short-gap/long-gap nulls actually flow
    # through interpolation -- injecting after clean() would leave the
    # quality_flag columns stuck at "ok" for the newly-nulled cells.
    raw_wide = build_wide_frame(fetch_all())
    faulty_raw = inject(raw_wide)
    faulty = clean(faulty_raw)
    long_df = compute_anomalies(faulty)
    load(long_df, db_path=DEMO_DB_PATH)

    n_anomalies = int(long_df["is_anomaly"].sum())
    n_tier1 = int((long_df["anomaly_tier"] == 1).sum())
    print(f"Wrote {DEMO_DB_PATH} -- {len(long_df)} rows, {n_anomalies} anomalies ({n_tier1} Tier 1).")
    print("This file contains SYNTHETIC, deliberately corrupted data. Never treat it as real.")


if __name__ == "__main__":
    run()
