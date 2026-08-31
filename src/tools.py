"""The 4 tools the AI agent is allowed to call, plus their Anthropic tool
schemas. Every function is a typed, deterministic Python query against the
persisted observations table -- the model never computes, it only picks a
function and fills in parameters (part2-handoff.md section 5).

Rows flagged anomaly_tier=1 (physically impossible) are excluded from every
aggregate computed here; anomaly_tier=2 rows (real meteorological events,
see README section 6) are kept. get_anomalies is the exception -- it reads
the persisted flag columns directly and never recomputes or filters them.
"""

import duckdb

from config import METRIC_SOLAR, METRIC_WIND, SITES
from dates import RANGE_KEYS, RANGE_LAST_7_DAYS, RANGE_LAST_30_DAYS, resolve_range

SITE_IDS = [s["site_id"] for s in SITES]
METRICS = [METRIC_SOLAR, METRIC_WIND]

_SITE_DESC = "; ".join(f"{s['site_id']} = {s['site_name']}" for s in SITES)
_NOT_TIER1 = "anomaly_tier IS DISTINCT FROM 1"  # exclude physically-impossible rows from aggregates

GENERATION_POTENTIAL_PROXY = (
    "Reporting relative irradiance/wind-speed as a proxy for generation potential; a true "
    "estimate requires turbine power curves and panel capacity/efficiency data, which are out "
    "of scope here."
)


def _site_filter(site: str | None) -> tuple[str, list]:
    if site is None:
        return "", []
    return " AND site_id = ?", [site]


def _metric_filter(metric: str | None) -> tuple[str, list]:
    if metric is None:
        return "", []
    return " AND metric = ?", [metric]


def get_site_ranking(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    aggregation: str = "mean",
    date_range: str = RANGE_LAST_7_DAYS,
    sort_direction: str = "desc",
) -> dict:
    """Rank all 3 sites by an aggregate of one metric over a date range."""
    agg_sql = {"mean": "AVG(value)", "min": "MIN(value)", "max": "MAX(value)", "count": "COUNT(*)"}[aggregation]
    start, end = resolve_range(date_range, con)
    order = "DESC" if sort_direction == "desc" else "ASC"

    rows = con.execute(
        f"""
        SELECT site_id, site_name, {agg_sql} AS value
        FROM observations
        WHERE metric = ? AND timestamp BETWEEN ? AND ? AND {_NOT_TIER1} AND value IS NOT NULL
        GROUP BY site_id, site_name
        ORDER BY value {order}
        """,
        [metric, start, end],
    ).fetchall()

    if not rows:
        return {
            "no_data": True,
            "message": f"No {metric} data in the {date_range.replace('_', ' ')} window.",
            "data": [],
        }
    return {
        "no_data": False,
        "date_range": {"start": str(start), "end": str(end)},
        "metric": metric,
        "aggregation": aggregation,
        "data": [{"site_id": r[0], "site_name": r[1], "value": round(r[2], 2)} for r in rows],
    }


def get_anomalies(
    con: duckdb.DuckDBPyConnection,
    site: str | None = None,
    metric: str | None = None,
    date_range: str = RANGE_LAST_7_DAYS,
) -> dict:
    """List anomalies (both tiers) for a site/metric/date range, reading the
    persisted is_anomaly/anomaly_tier/anomaly_reason columns directly."""
    start, end = resolve_range(date_range, con)
    site_sql, site_params = _site_filter(site)
    metric_sql, metric_params = _metric_filter(metric)

    rows = con.execute(
        f"""
        SELECT timestamp, site_id, site_name, metric, value, unit, anomaly_tier, anomaly_reason
        FROM observations
        WHERE is_anomaly AND timestamp BETWEEN ? AND ? {site_sql} {metric_sql}
        ORDER BY timestamp
        """,
        [start, end] + site_params + metric_params,
    ).fetchall()

    if not rows:
        scope = f" at {site}" if site else ""
        return {
            "no_data": True,
            "message": f"No anomalies were flagged{scope} in the {date_range.replace('_', ' ')} window.",
            "data": [],
        }
    return {
        "no_data": False,
        "date_range": {"start": str(start), "end": str(end)},
        "count": len(rows),
        "data": [
            {
                "timestamp": str(r[0]), "site_id": r[1], "site_name": r[2], "metric": r[3],
                "value": r[4], "unit": r[5], "anomaly_tier": r[6], "anomaly_reason": r[7],
            }
            for r in rows
        ],
    }


def compare_sites(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    date_range: str = RANGE_LAST_30_DAYS,
    site_list: list[str] | None = None,
) -> dict:
    """Compare a metric's average across sites as a documented proxy for
    generation potential (see part2-handoff.md section 5.2a -- raw
    irradiance/wind-speed aggregates, not a power estimate)."""
    sites = site_list or SITE_IDS
    start, end = resolve_range(date_range, con)
    placeholders = ",".join("?" for _ in sites)

    rows = con.execute(
        f"""
        SELECT site_id, site_name, AVG(value) AS mean_value, COUNT(*) AS n
        FROM observations
        WHERE metric = ? AND site_id IN ({placeholders}) AND timestamp BETWEEN ? AND ?
              AND {_NOT_TIER1} AND value IS NOT NULL
        GROUP BY site_id, site_name
        ORDER BY mean_value DESC
        """,
        [metric] + sites + [start, end],
    ).fetchall()

    if not rows:
        return {"no_data": True, "message": f"No {metric} data for the requested sites/range.", "data": []}
    return {
        "no_data": False,
        "date_range": {"start": str(start), "end": str(end)},
        "metric": metric,
        "proxy_disclaimer": GENERATION_POTENTIAL_PROXY,
        "data": [{"site_id": r[0], "site_name": r[1], "mean_value": round(r[2], 2), "n": r[3]} for r in rows],
    }


def get_summary_stats(
    con: duckdb.DuckDBPyConnection,
    site: str,
    metric: str,
    date_range: str = RANGE_LAST_7_DAYS,
) -> dict:
    """Min/max/mean/count for one site, one metric, one window -- returned
    together rather than gated behind an aggregation choice."""
    start, end = resolve_range(date_range, con)
    row = con.execute(
        f"""
        SELECT COUNT(*), MIN(value), MAX(value), AVG(value)
        FROM observations
        WHERE site_id = ? AND metric = ? AND timestamp BETWEEN ? AND ?
              AND {_NOT_TIER1} AND value IS NOT NULL
        """,
        [site, metric, start, end],
    ).fetchone()

    count, min_v, max_v, mean_v = row
    if not count:
        return {"no_data": True, "message": f"No {metric} data for {site} in that window.", "data": {}}
    return {
        "no_data": False,
        "date_range": {"start": str(start), "end": str(end)},
        "site": site,
        "metric": metric,
        "data": {"count": count, "min": round(min_v, 2), "max": round(max_v, 2), "mean": round(mean_v, 2)},
    }


TOOL_DISPATCH = {
    "get_site_ranking": get_site_ranking,
    "get_anomalies": get_anomalies,
    "compare_sites": compare_sites,
    "get_summary_stats": get_summary_stats,
}

TOOL_SCHEMAS = [
    {
        "name": "get_site_ranking",
        "description": "Rank all sites by an aggregate of one metric over a date range, e.g. "
        "'which site had the highest average solar radiation last week?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": METRICS},
                "aggregation": {"type": "string", "enum": ["mean", "min", "max", "count"], "default": "mean"},
                "date_range": {"type": "string", "enum": RANGE_KEYS, "default": RANGE_LAST_7_DAYS},
                "sort_direction": {
                    "type": "string", "enum": ["desc", "asc"], "default": "desc",
                    "description": "desc = highest first, asc = lowest first",
                },
            },
            "required": ["metric"],
        },
    },
    {
        "name": "get_anomalies",
        "description": "List anomalies flagged for a site and/or metric over a date range, e.g. "
        "'were there anomalous wind readings at Site B in the last 7 days?'. Omit site/metric "
        "to check across all sites/metrics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": SITE_IDS, "description": _SITE_DESC},
                "metric": {"type": "string", "enum": METRICS},
                "date_range": {"type": "string", "enum": RANGE_KEYS, "default": RANGE_LAST_7_DAYS},
            },
            "required": [],
        },
    },
    {
        "name": "compare_sites",
        "description": "Compare generation potential across sites for one metric over a date range, e.g. "
        "'compare generation potential across all three sites for the past month'. Reports raw "
        "metric averages as a documented proxy, not a power estimate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": METRICS},
                "date_range": {"type": "string", "enum": RANGE_KEYS, "default": RANGE_LAST_30_DAYS},
                "site_list": {
                    "type": "array", "items": {"type": "string", "enum": SITE_IDS},
                    "description": f"Defaults to all 3 sites if omitted. {_SITE_DESC}",
                },
            },
            "required": ["metric"],
        },
    },
    {
        "name": "get_summary_stats",
        "description": "Min/max/mean/count for one site and one metric over a date range -- the "
        "catch-all for a direct statistics question about a single site.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": SITE_IDS, "description": _SITE_DESC},
                "metric": {"type": "string", "enum": METRICS},
                "date_range": {"type": "string", "enum": RANGE_KEYS, "default": RANGE_LAST_7_DAYS},
            },
            "required": ["site", "metric"],
        },
    },
]

# Question text + the fixed (tool, params) it resolves to without a model --
# used both as demo buttons and as the keyless fallback path.
PRESET_QUESTIONS = [
    {
        "question": "Which site had the highest average solar radiation last week?",
        "tool": "get_site_ranking",
        "params": {"metric": METRIC_SOLAR, "aggregation": "mean", "date_range": RANGE_LAST_7_DAYS, "sort_direction": "desc"},
    },
    {
        "question": "Were there anomalous wind readings at the North Sea Coast site in the last 7 days?",
        "tool": "get_anomalies",
        "params": {"site": "northsea_uk", "metric": METRIC_WIND, "date_range": RANGE_LAST_7_DAYS},
    },
    {
        "question": "Compare generation potential across all three sites for the past month.",
        "tool": "compare_sites",
        "params": {"metric": METRIC_SOLAR, "date_range": RANGE_LAST_30_DAYS},
    },
    {
        "question": "What's the min, max, mean and count of wind speed at the Atacama Desert site this week?",
        "tool": "get_summary_stats",
        "params": {"site": "atacama_cl", "metric": METRIC_WIND, "date_range": RANGE_LAST_7_DAYS},
    },
]


def demo():
    from config import DB_PATH

    con = duckdb.connect(str(DB_PATH), read_only=True)

    ranking = get_site_ranking(con, METRIC_SOLAR, date_range=RANGE_LAST_7_DAYS)
    assert not ranking["no_data"] and len(ranking["data"]) == 3, "expected all 3 sites ranked"

    anomalies = get_anomalies(con, site="northsea_uk", metric=METRIC_WIND, date_range="all")
    assert anomalies["no_data"], "North Sea wind has zero recorded anomalies -- expected the no-data signal"

    anomalies_present = get_anomalies(con, metric=METRIC_SOLAR, date_range="all")
    assert not anomalies_present["no_data"] and anomalies_present["count"] > 0, "solar has known Tier-2 anomalies"

    cmp = compare_sites(con, METRIC_WIND, date_range=RANGE_LAST_30_DAYS)
    assert len(cmp["data"]) == 3 and GENERATION_POTENTIAL_PROXY == cmp["proxy_disclaimer"]

    stats = get_summary_stats(con, "atacama_cl", METRIC_SOLAR, date_range="all")
    assert stats["data"]["min"] <= stats["data"]["mean"] <= stats["data"]["max"]

    empty_stats = get_summary_stats(con, "atacama_cl", METRIC_SOLAR, date_range="last_7_days")
    con.close()
    assert isinstance(empty_stats["no_data"], bool)

    assert {t["name"] for t in TOOL_SCHEMAS} == set(TOOL_DISPATCH), "schema/dispatch name mismatch"
    print("OK -- all 4 tools behave correctly against", DB_PATH)


if __name__ == "__main__":
    demo()
