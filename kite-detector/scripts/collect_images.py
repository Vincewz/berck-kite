"""
Collect qualifying webcam images for the YOLO dataset.

The weather/daylight rules are shared with inference through kite_conditions.py:
wind >= 8 kt, no east component, rain < 0.3 mm/h, outside festival,
and from sunrise to sunset + 30 minutes.
"""
import time
from datetime import timedelta
from pathlib import Path

import requests

from kite_conditions import (
    MAX_RAIN_MM,
    MIN_WIND_KT,
    archive_weather_payload,
    has_east_component,
    is_daylight_window,
    is_festival,
    now_paris,
    parse_local_dt,
    sun_by_date_from_payload,
    to_kt,
)

DAYS_BACK = 30
S3_BASE = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer"
OUT_DIR = Path(__file__).parent.parent / "dataset" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CAMERAS = [
    {"name": "eole", "prefix": "", "paths": ["large/{hour}-00.jpg"]},
    {"name": "maritime", "prefix": "maritime_", "paths": ["{hour}-00.jpg", "{hour}-15.jpg", "{hour}-30.jpg", "{hour}-45.jpg"]},
    {"name": "mer", "prefix": "mer_", "paths": ["{hour}-00.jpg", "{hour}-15.jpg", "{hour}-30.jpg", "{hour}-45.jpg"]},
]


def candidate_urls(camera, dt):
    day = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    hour = f"{dt.hour:02d}"
    return [
        f"{S3_BASE}/{camera['name']}/{day}/{path.format(hour=hour)}"
        for path in camera["paths"]
    ]


def filename(camera, dt, kt, deg):
    return f"{camera['prefix']}{dt.strftime('%Y%m%d_%H00')}_w{int(kt)}kt_d{int(deg)}deg.jpg"


def save_first_available(camera, dt, kt, deg):
    fpath = OUT_DIR / filename(camera, dt, kt, deg)
    if fpath.exists():
        return "skipped"

    for url in candidate_urls(camera, dt):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 5000:
                fpath.write_bytes(resp.content)
                print(f"  OK  {fpath.name}  ({len(resp.content) // 1024}KB)")
                return "downloaded"
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.03)
    return "missing"


now = now_paris()
start = (now - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
end = now.strftime("%Y-%m-%d")

print(f"Fetch meteo {start} -> {end}...")
payload = archive_weather_payload(start, end)
sun_by_date = sun_by_date_from_payload(payload)

times = payload["hourly"]["time"]
winds = payload["hourly"]["wind_speed_10m"]
dirs = payload["hourly"]["wind_direction_10m"]
rains = payload["hourly"]["precipitation"]

candidates = []
rejected = {"festival": 0, "east": 0, "wind": 0, "rain": 0, "night": 0}

for i, value in enumerate(times):
    dt = parse_local_dt(value)
    kt = to_kt(winds[i] or 0)
    deg = dirs[i] or 0
    rain = rains[i] or 0
    sun = sun_by_date.get(dt.date().isoformat())

    if not sun or not is_daylight_window(dt, sun[0], sun[1]):
        rejected["night"] += 1
        continue
    if is_festival(dt):
        rejected["festival"] += 1
        continue
    if has_east_component(deg):
        rejected["east"] += 1
        continue
    if rain >= MAX_RAIN_MM:
        rejected["rain"] += 1
        continue
    if kt < MIN_WIND_KT:
        rejected["wind"] += 1
        continue
    candidates.append((dt, kt, deg))

print(f"  {len(candidates)} creneaux valides | rejetes: {rejected}")

stats = {camera["name"]: {"downloaded": 0, "skipped": 0, "missing": 0} for camera in CAMERAS}

for camera in CAMERAS:
    print(f"\n=== {camera['name']} ===")
    for dt, kt, deg in candidates:
        result = save_first_available(camera, dt, kt, deg)
        stats[camera["name"]][result] += 1

total = len(list(OUT_DIR.glob("*.jpg")))
print("\nTermine")
for camera in CAMERAS:
    values = stats[camera["name"]]
    print(
        f"  {camera['name']}: {values['downloaded']} telecharges, "
        f"{values['skipped']} deja la, {values['missing']} manquants"
    )
print(f"Total dataset/raw: {total} images")
print(f"Objectif 1000: encore {max(0, 1000 - total)} images a collecter")
