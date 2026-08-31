"""Assert-based smoke check against the built store. Not a unit-test suite --
one runnable check that fails loudly if the pipeline's core invariants ever
break (schema drift, a Tier-1 rule stops firing, fence bounds go inconsistent).

Run: python tests/test_invariants.py  (or `make test`)
Requires data/openmeteo.duckdb to already exist (`make pipeline` first).
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import DB_PATH, METRIC_SOLAR, METRIC_WIND, SITES  # noqa: E402

EXPECTED_COLUMNS = {
    "timestamp", "site_id", "site_name", "metric", "value", "unit",
    "quality_flag", "is_anomaly", "anomaly_tier", "anomaly_reason",
    "fence_lower", "fence_upper",
}


def main():
    assert DB_PATH.exists(), f"{DB_PATH} not found -- run `make pipeline` first."
    con = duckdb.connect(str(DB_PATH), read_only=True)

    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='observations'"
    ).fetchall()}
    assert cols == EXPECTED_COLUMNS, f"schema mismatch: {cols.symmetric_difference(EXPECTED_COLUMNS)}"

    n_sites = len(SITES)
    total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert total % (n_sites * 2) == 0, f"expected a multiple of {n_sites * 2} rows (sites x metrics), got {total}"

    per_group = con.execute("SELECT COUNT(*) FROM observations GROUP BY site_id, metric").fetchdf()
    assert per_group["count_star()"].nunique() == 1, "row count differs across site/metric groups"

    for metric, unit in [(METRIC_SOLAR, "W/m²"), (METRIC_WIND, "m/s")]:
        bad_unit = con.execute(
            "SELECT COUNT(*) FROM observations WHERE metric = ? AND unit != ?", [metric, unit]
        ).fetchone()[0]
        assert bad_unit == 0, f"{metric} has {bad_unit} rows with unexpected unit"

    neg_unflagged = con.execute(
        "SELECT COUNT(*) FROM observations WHERE value < 0 AND NOT is_anomaly"
    ).fetchone()[0]
    assert neg_unflagged == 0, f"{neg_unflagged} negative values were not flagged anomalous"

    bad_fence = con.execute(
        "SELECT COUNT(*) FROM observations WHERE fence_lower IS NOT NULL AND fence_lower > fence_upper"
    ).fetchone()[0]
    assert bad_fence == 0, f"{bad_fence} rows have fence_lower > fence_upper"

    tier_without_flag = con.execute(
        "SELECT COUNT(*) FROM observations WHERE anomaly_tier IS NOT NULL AND NOT is_anomaly"
    ).fetchone()[0]
    assert tier_without_flag == 0, f"{tier_without_flag} rows have an anomaly_tier but is_anomaly=False"

    flagged_without_reason = con.execute(
        "SELECT COUNT(*) FROM observations WHERE is_anomaly AND anomaly_reason IS NULL"
    ).fetchone()[0]
    assert flagged_without_reason == 0, f"{flagged_without_reason} anomalous rows have no anomaly_reason"

    con.close()
    print(f"OK -- {total} rows, schema matches, all invariants hold ({DB_PATH}).")


if __name__ == "__main__":
    main()
