"""
Shared weather and daylight rules for scraping and YOLO inference.
"""
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
PARIS_TZ = ZoneInfo("Europe/Paris")


def now_paris():
    return datetime.now(PARIS_TZ)


def to_kt(kmh):
    return float(kmh) / 1.852


def has_east_component(deg):
    return 0 < float(deg) % 360 < 180


def is_festival(dt):
    return dt.month == FESTIVAL_MONTH and FESTIVAL_START <= dt.day <= FESTIVAL_END


def parse_local_dt(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=PARIS_TZ) if dt.tzinfo is None else dt.astimezone(PARIS_TZ)


def daylight_limit(sunset):
    return sunset + timedelta(minutes=SUNSET_GRACE_MIN)


def is_daylight_window(dt, sunrise, sunset):
    return sunrise <= dt <= daylight_limit(sunset)


def daylight_reason(sunrise, sunset):
    return f"hors lumiere ({sunrise.strftime('%H:%M')}-{daylight_limit(sunset).strftime('%H:%M')})"


def retry_json(url, timeout=20, attempts=3, pause=10):
    last_exc = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print(f"  Tentative {attempt + 1}/{attempts} echouee : {exc}")
            if attempt < attempts - 1:
                time.sleep(pause)
    raise last_exc


def current_weather_payload():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        "&current=wind_speed_10m,wind_direction_10m,temperature_2m,precipitation"
        "&daily=sunrise,sunset&forecast_days=1"
        "&wind_speed_unit=kmh&timezone=Europe/Paris"
    )
    return retry_json(url, timeout=20)


def archive_weather_payload(start, end):
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&start_date={start}&end_date={end}"
        "&hourly=wind_speed_10m,wind_direction_10m,precipitation"
        "&daily=sunrise,sunset"
        "&wind_speed_unit=kmh&timezone=Europe/Paris"
    )
    return retry_json(url, timeout=30, pause=5)


def sun_window_from_payload(payload, index=0):
    sunrise = parse_local_dt(payload["daily"]["sunrise"][index])
    sunset = parse_local_dt(payload["daily"]["sunset"][index])
    return sunrise, sunset


def sun_by_date_from_payload(payload):
    return {
        date: (parse_local_dt(sunrise), parse_local_dt(sunset))
        for date, sunrise, sunset in zip(
            payload["daily"]["time"],
            payload["daily"]["sunrise"],
            payload["daily"]["sunset"],
        )
    }


def weather_values(weather):
    wind_kt = to_kt(weather["wind_speed_10m"])
    wind_dir = weather["wind_direction_10m"]
    temp_c = weather.get("temperature_2m")
    rain_mm = weather.get("precipitation") or 0
    return wind_kt, wind_dir, temp_c, rain_mm


def weather_reasons(wind_kt, wind_dir, temp_c, rain_mm, require_temp=True):
    reasons = []
    if wind_kt < MIN_WIND_KT:
        reasons.append(f"vent {wind_kt:.0f}kt < {MIN_WIND_KT}kt")
    if has_east_component(wind_dir):
        reasons.append(f"composante Est ({wind_dir:.0f}deg)")
    if rain_mm >= MAX_RAIN_MM:
        reasons.append(f"pluie {rain_mm:.1f}mm >= {MAX_RAIN_MM}mm")
    if require_temp and temp_c is not None and temp_c < MIN_TEMP_C:
        reasons.append(f"temp {temp_c}C < {MIN_TEMP_C}C")
    return reasons


def current_conditions(now=None):
    now = now or now_paris()
    payload = current_weather_payload()
    sunrise, sunset = sun_window_from_payload(payload)
    weather = payload["current"]
    wind_kt, wind_dir, temp_c, rain_mm = weather_values(weather)
    reasons = []
    if is_festival(now):
        reasons.append("festival de cerfs-volants")
    if not is_daylight_window(now, sunrise, sunset):
        reasons.append(daylight_reason(sunrise, sunset))
    reasons.extend(weather_reasons(wind_kt, wind_dir, temp_c, rain_mm, require_temp=True))
    return {
        "ok": not reasons,
        "reason": ", ".join(reasons),
        "reasons": reasons,
        "timestamp": now,
        "sunrise": sunrise,
        "sunset": sunset,
        "wind_kt": wind_kt,
        "wind_dir": wind_dir,
        "temp_c": temp_c,
        "rain_mm": rain_mm,
        "payload": payload,
    }
