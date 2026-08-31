"""Deterministic date-range resolution for the AI agent's tools.

The model never computes dates -- it only classifies a phrase onto one of a
fixed set of range keys, and this module resolves that key against
MAX(timestamp) in the store (never datetime.now(), see part2-handoff.md
section 5.3). This keeps date arithmetic out of the LLM's hands entirely.
"""

from datetime import datetime, timedelta

import duckdb

from config import DB_PATH

RANGE_LAST_7_DAYS = "last_7_days"
RANGE_LAST_14_DAYS = "last_14_days"
RANGE_LAST_30_DAYS = "last_30_days"
RANGE_ALL = "all"

RANGE_DAYS = {RANGE_LAST_7_DAYS: 7, RANGE_LAST_14_DAYS: 14, RANGE_LAST_30_DAYS: 30}
RANGE_KEYS = [RANGE_LAST_7_DAYS, RANGE_LAST_14_DAYS, RANGE_LAST_30_DAYS, RANGE_ALL]


def resolve_range(range_key: str, con: duckdb.DuckDBPyConnection) -> tuple[datetime, datetime]:
    """Resolve a range key to a [start, end] timestamp window, inclusive,
    anchored on the store's own MAX(timestamp) rather than the system clock."""
    min_ts, max_ts = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM observations").fetchone()
    if max_ts is None:
        raise ValueError("observations table is empty")

    if range_key == RANGE_ALL:
        return min_ts, max_ts
    if range_key not in RANGE_DAYS:
        raise ValueError(f"unknown range_key {range_key!r}, expected one of {RANGE_KEYS}")

    days = RANGE_DAYS[range_key]
    start = max_ts.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return start, max_ts


def demo():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    max_ts = con.execute("SELECT MAX(timestamp) FROM observations").fetchone()[0]

    for key, days in RANGE_DAYS.items():
        start, end = resolve_range(key, con)
        assert end == max_ts, f"{key}: end should be MAX(timestamp)"
        assert (end.date() - start.date()).days == days - 1, f"{key}: expected a {days}-day span"
        assert start.hour == 0 and start.minute == 0, f"{key}: start should be midnight"

    start, end = resolve_range(RANGE_ALL, con)
    min_ts = con.execute("SELECT MIN(timestamp) FROM observations").fetchone()[0]
    assert start == min_ts and end == max_ts, "all: expected the full store range"

    try:
        resolve_range("yesterday", con)
        raise AssertionError("expected ValueError for an unknown range key")
    except ValueError:
        pass

    con.close()
    print("OK -- all range keys resolve correctly against", DB_PATH)


if __name__ == "__main__":
    demo()
