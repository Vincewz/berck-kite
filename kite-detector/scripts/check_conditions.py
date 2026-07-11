"""
check_conditions.py
Verifie les conditions meteo et ecrit le resultat dans $GITHUB_OUTPUT.
N'installe que requests, avant l'installation torch/ultralytics.
"""
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT = 8
MAX_RAIN_MM = 0.3
MIN_TEMP_C = 3
SUNSET_GRACE_MIN = 30
FESTIVAL_MONTH = 4
FESTIVAL_START = 17
FESTIVAL_END = 27

paris_tz = ZoneInfo("Europe/Paris")
now = datetime.now(paris_tz)


def to_kt(kmh):
    return float(kmh) / 1.852


def has_east_component(deg):
    return 0 < float(deg) % 360 < 180


def is_festival(dt):
    return dt.month == FESTIVAL_MONTH and FESTIVAL_START <= dt.day <= FESTIVAL_END


def parse_local_dt(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=paris_tz) if dt.tzinfo is None else dt.astimezone(paris_tz)


def is_daylight_window(dt, sunrise, sunset):
    return sunrise <= dt <= sunset + timedelta(minutes=SUNSET_GRACE_MIN)


def set_output(key, value):
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
    print(f"  output: {key}={value}")


if is_festival(now):
    print("Festival de cerfs-volants de Berck - pas de detection")
    set_output("conditions_ok", "false")
    set_output("reason", "festival de cerfs-volants")
    sys.exit(0)

print(f"Verification conditions ({now.strftime('%H:%M')} Paris)...")
for attempt in range(3):
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
            "&current=wind_speed_10m,wind_direction_10m,temperature_2m,precipitation"
            "&daily=sunrise,sunset&forecast_days=1"
            "&wind_speed_unit=kmh&timezone=Europe/Paris",
            timeout=20,
        )
        response.raise_for_status()
        break
    except requests.exceptions.RequestException as exc:
        print(f"  Tentative {attempt + 1}/3 echouee : {exc}")
        if attempt < 2:
            time.sleep(10)
        else:
            print("API meteo indisponible - skip")
            set_output("conditions_ok", "false")
            set_output("reason", "API meteo indisponible")
            sys.exit(0)

payload = response.json()
sunrise = parse_local_dt(payload["daily"]["sunrise"][0])
sunset = parse_local_dt(payload["daily"]["sunset"][0])
sunset_limit = sunset + timedelta(minutes=SUNSET_GRACE_MIN)

if not is_daylight_window(now, sunrise, sunset):
    reason = f"hors lumiere ({sunrise.strftime('%H:%M')}-{sunset_limit.strftime('%H:%M')})"
    print(f"{reason} - pas de detection")
    set_output("conditions_ok", "false")
    set_output("reason", reason)
    sys.exit(0)

weather = payload["current"]
wind_kt = to_kt(weather["wind_speed_10m"])
wind_dir = weather["wind_direction_10m"]
temp_c = weather["temperature_2m"]
rain_mm = weather.get("precipitation") or 0
print(
    f"  Vent : {wind_kt:.1f}kt  Dir : {wind_dir}deg  "
    f"Temp : {temp_c}C  Pluie : {rain_mm}mm"
)

reasons = []
if wind_kt < MIN_WIND_KT:
    reasons.append(f"vent {wind_kt:.0f}kt < {MIN_WIND_KT}kt")
if has_east_component(wind_dir):
    reasons.append(f"composante Est ({wind_dir:.0f}deg)")
if rain_mm >= MAX_RAIN_MM:
    reasons.append(f"pluie {rain_mm:.1f}mm >= {MAX_RAIN_MM}mm")
if temp_c < MIN_TEMP_C:
    reasons.append(f"temp {temp_c}C < {MIN_TEMP_C}C")

if reasons:
    reason = ", ".join(reasons)
    print(f"Conditions KO : {reason}")
    set_output("conditions_ok", "false")
    set_output("reason", reason)
else:
    print("Conditions OK - lancement YOLO")
    set_output("conditions_ok", "true")
    set_output("reason", "ok")
