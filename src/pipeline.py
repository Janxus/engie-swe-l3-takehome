"""CLI entry point: fetch -> clean -> anomaly -> store.

Run with `python src/pipeline.py` from the repo root's venv. Re-running is
idempotent (store.load replaces the table). Uses cached raw JSON under
data/raw/ if present; pass --refresh to force a live re-fetch from Open-Meteo.
"""

import argparse

from anomaly import compute_anomalies
from clean import build_wide_frame, clean
from fetch import fetch_all
from store import load


def run(force_refresh: bool = False) -> None:
    raw = fetch_all(force_refresh=force_refresh)
    wide = clean(build_wide_frame(raw))
    long_df = compute_anomalies(wide)
    load(long_df)
    n_anomalies = int(long_df["is_anomaly"].sum())
    print(f"Loaded {len(long_df)} rows ({long_df['timestamp'].min()} -> {long_df['timestamp'].max()}).")
    print(f"Flagged {n_anomalies} anomalies ({n_anomalies / len(long_df):.1%}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Force a live re-fetch instead of using cached raw JSON.")
    args = parser.parse_args()
    run(force_refresh=args.refresh)
