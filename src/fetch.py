"""Fetch hourly solar radiation and wind speed from Open-Meteo for all sites.

Uses the /v1/forecast endpoint with an explicit start_date/end_date window
rather than past_days, which pulls in today's partial forecast day. Each
site's raw JSON response is cached under data/raw/ so a clean clone can
rebuild the store offline (see pipeline.py) without needing network access
or an API key.
"""

import argparse
import datetime as dt
import json

import requests

from config import FORECAST_URL, HOURLY_VARS, PAST_DAYS, RAW_DIR, SITES


def date_window(today: dt.date | None = None) -> tuple[str, str]:
    """[today - PAST_DAYS, today - 1], i.e. exactly PAST_DAYS full days."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    start = today - dt.timedelta(days=PAST_DAYS)
    return start.isoformat(), end.isoformat()


def fetch_site(site: dict, start_date: str, end_date: str) -> dict:
    params = {
        "latitude": site["latitude"],
        "longitude": site["longitude"],
        "hourly": ",".join(HOURLY_VARS),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": site["timezone"],
        "wind_speed_unit": "ms",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "hourly" not in data:
        raise RuntimeError(f"Open-Meteo error for {site['site_id']}: {data}")
    return data


def fetch_all(force_refresh: bool = False) -> dict[str, dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    start_date, end_date = date_window()
    results = {}
    for site in SITES:
        cache_path = RAW_DIR / f"{site['site_id']}.json"
        if cache_path.exists() and not force_refresh:
            results[site["site_id"]] = json.loads(cache_path.read_text())
            continue
        data = fetch_site(site, start_date, end_date)
        cache_path.write_text(json.dumps(data, indent=2))
        results[site["site_id"]] = data
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="Re-fetch from the API even if a cached raw JSON snapshot exists."
    )
    args = parser.parse_args()

    raw = fetch_all(force_refresh=args.refresh)
    for site_id, data in raw.items():
        n = len(data["hourly"]["time"])
        print(f"{site_id}: {n} hourly rows, {data['hourly']['time'][0]} -> {data['hourly']['time'][-1]}")
