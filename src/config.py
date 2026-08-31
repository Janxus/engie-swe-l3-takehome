"""Shared constants: sites, Open-Meteo variables, and thresholds.

Thresholds here are the numeric defaults documented in the README's
"Cleaning and normalisation" and "Anomaly detection" sections — change them
in one place if the justification changes.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "openmeteo.duckdb"
DEMO_DB_PATH = DATA_DIR / "demo_faulty.duckdb"

# Three sites chosen for contrast: opposite hemispheres (different seasons in
# the same 30-day window), and one site each that is solar-dominant,
# wind-dominant, and tropical-mixed. See README "Site selection".
SITES = [
    {
        "site_id": "atacama_cl",
        "site_name": "Atacama Desert, CL",
        "latitude": -23.4,
        "longitude": -68.0,
        "timezone": "America/Santiago",
    },
    {
        "site_id": "northsea_uk",
        "site_name": "North Sea Coast, UK",
        "latitude": 56.5,
        "longitude": -2.0,
        "timezone": "Europe/London",
    },
    {
        "site_id": "luzon_ph",
        "site_name": "Luzon, PH",
        "latitude": 14.6,
        "longitude": 121.0,
        "timezone": "Asia/Manila",
    },
]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo hourly variable names, confirmed live against the running API
# (not just the docs page) at build time.
VAR_GHI = "shortwave_radiation"       # W/m^2, global horizontal irradiance
VAR_TOA = "terrestrial_radiation"     # W/m^2, top-of-atmosphere radiation
VAR_WIND = "wind_speed_100m"          # m/s (requested via wind_speed_unit=ms), turbine hub height

HOURLY_VARS = [VAR_GHI, VAR_TOA, VAR_WIND]

# Metric identifiers as persisted in the store (long format: one row per
# timestamp x site x metric).
METRIC_SOLAR = "solar_radiation"
METRIC_WIND = "wind_speed"

PAST_DAYS = 30  # "past 30 days" per the brief; window is [today-30, today-1]

# --- Cleaning thresholds ---
# Gaps of this many consecutive hours or fewer are linearly interpolated and
# marked quality_flag='interpolated'. Longer gaps are left null, not
# fabricated. This is the spec's own example threshold.
SHORT_GAP_MAX_HOURS = 2

# --- Tier 1 (physical validity) thresholds ---
# GHI may not exceed top-of-atmosphere radiation by more than this margin;
# a small margin (not 1.0) allows for model/interpolation noise right at the
# TOA boundary without weakening the rule's intent (GHI > TOA is impossible).
GHI_TOA_MARGIN = 1.05

# Generous ceiling for a reanalysis wind product at hub height; well above
# any recorded surface wind speed, so only clearly erroneous values trip it.
WIND_MAX_MS = 60.0

# Flatline / stuck-sensor rule: an identical value repeated for this many
# consecutive *valid* hours (daylight hours for solar via TOA>0, all hours
# for wind) is flagged as a collective anomaly.
FLATLINE_MIN_HOURS = 6

# --- Tier 2 (conditional IQR) fence multipliers ---
# Solar uses the standard Tukey fence. Wind is right-skewed (Weibull-shaped)
# even within an hour-of-day bucket, so it uses the wider "far out" fence:
# a genuine high-wind hour is the most operationally interesting data a wind
# farm produces, not noise to suppress.
IQR_K_SOLAR = 1.5
IQR_K_WIND = 3.0

# Minimum observations required in a (site, hour-of-day) bucket before a
# fence is computed at all; below this, quartiles are too noisy to trust.
# With 30 days of data, a full bucket has ~30 observations, so 4 is a low
# bar that only excludes buckets thinned out by Tier-1 exclusions or nulls.
IQR_MIN_BUCKET_N = 4
