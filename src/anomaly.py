"""Two-tier anomaly detection, computed once and persisted as columns.

Tier 1 (physical validity) -- deterministic rules, no statistics. Certainly
wrong: negative values, radiation above the top-of-atmosphere ceiling,
radiation while the sun is below the horizon, and flatline/stuck-sensor runs.

Tier 2 (contextual statistical anomaly) -- IQR fences conditioned on
(site, metric, hour-of-day), solar restricted to daylight hours (TOA > 0).
Solar uses the standard 1.5x Tukey fence; wind uses the wider 3x "far out"
fence because a genuine high-wind hour is operationally interesting data,
not noise to suppress.

Tier 1 takes precedence in the persisted is_anomaly/anomaly_tier/
anomaly_reason columns when both would fire on the same row. Fence bounds
are persisted for every row in a bucket (not just anomalous ones) so the
dashboard can shade the fence band continuously.
"""

import numpy as np
import pandas as pd

from config import (
    FLATLINE_MIN_HOURS,
    GHI_TOA_MARGIN,
    IQR_K_SOLAR,
    IQR_K_WIND,
    IQR_MIN_BUCKET_N,
    METRIC_SOLAR,
    METRIC_WIND,
    WIND_MAX_MS,
)


def _flatline_mask(values: pd.Series, min_hours: int) -> pd.Series:
    """True where a value belongs to a run of >= min_hours identical
    consecutive values. `values` must already be sorted by time."""
    same_as_prev = values.eq(values.shift())
    run_id = (~same_as_prev).cumsum()
    run_len = values.groupby(run_id).transform("size")
    return run_len >= min_hours


def _tier1_solar(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    flag = pd.Series(False, index=df.index)
    reason = pd.Series(pd.NA, index=df.index, dtype="object")

    neg = df["ghi"] < 0
    reason[neg] = "Negative solar radiation reading (physically impossible)."
    flag |= neg

    night_nonzero = (df["toa"] == 0) & (df["ghi"] > 0)
    unset = night_nonzero & ~flag
    reason[unset] = "Non-zero radiation while the sun is below the horizon (TOA=0)."
    flag |= night_nonzero

    ceiling = df["toa"] * GHI_TOA_MARGIN
    above_ceiling = df["ghi"].notna() & (df["ghi"] > ceiling)
    unset = above_ceiling & ~flag
    reason[unset] = (
        "Radiation of " + df.loc[unset, "ghi"].round(0).astype(int).astype(str)
        + " W/m² exceeds the top-of-atmosphere ceiling ("
        + ceiling[unset].round(0).astype(int).astype(str)
        + " W/m²) -- physically impossible."
    )
    flag |= above_ceiling

    flat = pd.Series(False, index=df.index)
    daylight = df[df["toa"] > 0]
    for _, g in daylight.groupby("site_id"):
        g = g.sort_values("timestamp")
        run_flag = _flatline_mask(g["ghi"], FLATLINE_MIN_HOURS)
        flat.loc[g.index[run_flag.to_numpy()]] = True
    unset = flat & ~flag
    reason[unset] = f"Identical reading for >= {FLATLINE_MIN_HOURS} consecutive daylight hours (stuck-sensor pattern)."
    flag |= flat

    return flag, reason


def _tier1_wind(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    flag = pd.Series(False, index=df.index)
    reason = pd.Series(pd.NA, index=df.index, dtype="object")

    neg = df["wind"] < 0
    reason[neg] = "Negative wind speed reading (physically impossible)."
    flag |= neg

    above_ceiling = df["wind"].notna() & (df["wind"] > WIND_MAX_MS)
    unset = above_ceiling & ~flag
    reason[unset] = (
        "Wind speed of " + df.loc[unset, "wind"].round(1).astype(str)
        + f" m/s exceeds the {WIND_MAX_MS:.0f} m/s physical ceiling for this product."
    )
    flag |= above_ceiling

    flat = pd.Series(False, index=df.index)
    for _, g in df.groupby("site_id"):
        g = g.sort_values("timestamp")
        run_flag = _flatline_mask(g["wind"], FLATLINE_MIN_HOURS)
        flat.loc[g.index[run_flag.to_numpy()]] = True
    unset = flat & ~flag
    reason[unset] = f"Identical reading for >= {FLATLINE_MIN_HOURS} consecutive hours (stuck-sensor or calm-day pattern)."
    flag |= flat

    return flag, reason


def _fence_stats(group: pd.DataFrame, value_col: str, k: float) -> pd.Series:
    q1, q3 = group[value_col].quantile([0.25, 0.75])
    iqr = q3 - q1
    return pd.Series(
        {
            "fence_lower": max(q1 - k * iqr, 0.0),
            "fence_upper": q3 + k * iqr,
            "n": len(group),
        }
    )


def _tier2(
    df: pd.DataFrame,
    value_col: str,
    k: float,
    daylight_only: bool,
    tier1_flag: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    hour = df["timestamp"].dt.hour
    in_scope = (df["toa"] > 0) if daylight_only else pd.Series(True, index=df.index)
    valid_for_stats = in_scope & ~tier1_flag & df[value_col].notna()

    keyed = df.assign(hour=hour)
    stats = (
        keyed[valid_for_stats]
        .groupby(["site_id", "hour"])
        .apply(lambda g: _fence_stats(g, value_col, k), include_groups=False)
        .reset_index()
    )
    stats.loc[stats["n"] < IQR_MIN_BUCKET_N, ["fence_lower", "fence_upper"]] = np.nan

    merged = keyed.merge(stats[["site_id", "hour", "fence_lower", "fence_upper"]], on=["site_id", "hour"], how="left")
    fence_lower = merged["fence_lower"]
    fence_upper = merged["fence_upper"]
    fence_lower.index = df.index
    fence_upper.index = df.index

    if daylight_only:
        night = ~in_scope
        fence_lower = fence_lower.where(~night, 0.0)
        fence_upper = fence_upper.where(~night, 0.0)

    out_of_fence = (
        in_scope
        & df[value_col].notna()
        & fence_lower.notna()
        & ((df[value_col] < fence_lower) | (df[value_col] > fence_upper))
    )
    flag = out_of_fence & ~tier1_flag

    unit = "W/m²" if value_col == "ghi" else "m/s"
    reason = pd.Series(pd.NA, index=df.index, dtype="object")
    reason[flag] = (
        df.loc[flag, value_col].round(1).astype(str) + " " + unit
        + " is outside the typical range for this site at hour "
        + hour[flag].astype(str)
        + " (" + fence_lower[flag].round(1).astype(str) + "–" + fence_upper[flag].round(1).astype(str) + " " + unit
        + ")."
    )

    return flag, reason, fence_lower, fence_upper


def _build_metric_frame(
    wide: pd.DataFrame,
    value_col: str,
    quality_col: str,
    metric: str,
    unit: str,
    tier1_fn,
    daylight_only: bool,
    k: float,
) -> pd.DataFrame:
    tier1_flag, tier1_reason = tier1_fn(wide)
    tier2_flag, tier2_reason, fence_lower, fence_upper = _tier2(wide, value_col, k, daylight_only, tier1_flag)

    is_anomaly = tier1_flag | tier2_flag
    anomaly_tier = pd.Series(pd.NA, index=wide.index, dtype="Int64")
    anomaly_tier[tier1_flag] = 1
    anomaly_tier[tier2_flag & ~tier1_flag] = 2

    anomaly_reason = tier1_reason.where(tier1_flag, tier2_reason)

    return pd.DataFrame(
        {
            "timestamp": wide["timestamp"],
            "site_id": wide["site_id"],
            "site_name": wide["site_name"],
            "metric": metric,
            "value": wide[value_col],
            "unit": unit,
            "quality_flag": wide[quality_col],
            "is_anomaly": is_anomaly,
            "anomaly_tier": anomaly_tier,
            "anomaly_reason": anomaly_reason,
            "fence_lower": fence_lower,
            "fence_upper": fence_upper,
        }
    )


def compute_anomalies(wide: pd.DataFrame) -> pd.DataFrame:
    solar = _build_metric_frame(
        wide, "ghi", "ghi_quality_flag", METRIC_SOLAR, "W/m²", _tier1_solar, daylight_only=True, k=IQR_K_SOLAR
    )
    wind = _build_metric_frame(
        wide, "wind", "wind_quality_flag", METRIC_WIND, "m/s", _tier1_wind, daylight_only=False, k=IQR_K_WIND
    )
    long_df = pd.concat([solar, wind], ignore_index=True)
    return long_df.sort_values(["site_id", "metric", "timestamp"]).reset_index(drop=True)


if __name__ == "__main__":
    from clean import build_wide_frame, clean
    from fetch import fetch_all

    wide = clean(build_wide_frame(fetch_all()))
    long_df = compute_anomalies(wide)
    print(long_df.shape)
    print(long_df["metric"].value_counts())
    print(long_df.groupby(["metric", "anomaly_tier"], dropna=False).size())
    print(long_df[long_df["is_anomaly"]].head(10).to_string())
