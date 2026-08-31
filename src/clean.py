"""Parse raw Open-Meteo JSON into a wide per-site DataFrame and interpolate
short null gaps.

Resolution to the brief's "clean nulls" vs "flag anomalies" tension: never
delete a row. Short gaps (<= SHORT_GAP_MAX_HOURS consecutive) are linearly
interpolated and marked quality_flag='interpolated'; longer gaps are left
null (quality_flag='missing') rather than fabricated.
"""

import pandas as pd

from config import SHORT_GAP_MAX_HOURS, SITES, VAR_GHI, VAR_TOA, VAR_WIND


def raw_to_site_frame(site: dict, raw: dict) -> pd.DataFrame:
    h = raw["hourly"]
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(h["time"]),
            "site_id": site["site_id"],
            "site_name": site["site_name"],
            "ghi": h[VAR_GHI],
            "toa": h[VAR_TOA],
            "wind": h[VAR_WIND],
        }
    )


def build_wide_frame(raw_by_site: dict[str, dict]) -> pd.DataFrame:
    frames = [raw_to_site_frame(site, raw_by_site[site["site_id"]]) for site in SITES]
    return pd.concat(frames, ignore_index=True).sort_values(["site_id", "timestamp"]).reset_index(drop=True)


def interpolate_short_gaps(series: pd.Series, max_gap: int) -> tuple[pd.Series, pd.Series]:
    """Linearly interpolate runs of <= max_gap consecutive nulls; leave longer
    runs (and un-bounded edge runs) as null. Returns (values, quality_flag)."""
    is_null = series.isna()
    run_id = (is_null != is_null.shift()).cumsum()
    run_lengths = is_null.groupby(run_id).transform("sum")
    short_gap_mask = is_null & (run_lengths <= max_gap)

    interpolated = series.interpolate(method="linear", limit_area="inside")
    filled = short_gap_mask & interpolated.notna()

    values = series.copy()
    values[filled] = interpolated[filled]

    quality_flag = pd.Series("ok", index=series.index)
    quality_flag[filled] = "interpolated"
    quality_flag[is_null & ~filled] = "missing"
    return values, quality_flag


def clean(wide: pd.DataFrame) -> pd.DataFrame:
    """Interpolate ghi and wind per site (toa is a deterministic astronomical
    quantity with no nulls, so it is left as-is)."""
    wide = wide.copy()
    wide["ghi_quality_flag"] = "ok"
    wide["wind_quality_flag"] = "ok"

    for site_id, idx in wide.groupby("site_id").groups.items():
        idx = idx.sort_values()
        ghi_vals, ghi_flags = interpolate_short_gaps(wide.loc[idx, "ghi"], SHORT_GAP_MAX_HOURS)
        wind_vals, wind_flags = interpolate_short_gaps(wide.loc[idx, "wind"], SHORT_GAP_MAX_HOURS)
        wide.loc[idx, "ghi"] = ghi_vals
        wide.loc[idx, "ghi_quality_flag"] = ghi_flags
        wide.loc[idx, "wind"] = wind_vals
        wide.loc[idx, "wind_quality_flag"] = wind_flags

    return wide


if __name__ == "__main__":
    from fetch import fetch_all

    raw = fetch_all()
    wide = clean(build_wide_frame(raw))
    print(wide.describe(include="all"))
    print(wide["ghi_quality_flag"].value_counts())
    print(wide["wind_quality_flag"].value_counts())
