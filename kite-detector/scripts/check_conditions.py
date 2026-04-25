"""
check_conditions.py
Vérifie les conditions météo et écrit le résultat dans $GITHUB_OUTPUT.
N'installe que 'requests' — appelé AVANT l'install de torch/ultralytics.
"""
import os, sys, requests
from datetime import datetime, timezone, timedelta

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT = 15
MIN_TEMP_C  = 3
HOUR_START  = 10
HOUR_END    = 18

paris_tz = timezone(timedelta(hours=2))
now = datetime.now(paris_tz)

def to_kt(kmh): return float(kmh) / 1.852

def has_east_component(deg):
    return 0 < float(deg) % 360 < 180

def set_output(key, value):
    gho = os.environ.get("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a") as f:
            f.write(f"{key}={value}\n")
    print(f"  output: {key}={value}")

# ── Heure ────────────────────────────────────────────────────────────────────
if not (HOUR_START <= now.hour < HOUR_END):
    print(f"Hors plage horaire ({now.hour}h Paris) — pas de détection")
    set_output("conditions_ok", "false")
    set_output("reason", f"hors plage horaire ({now.hour}h)")
    sys.exit(0)

# ── Météo ────────────────────────────────────────────────────────────────────
print(f"Vérification conditions ({now.strftime('%H:%M')} Paris)...")
r = requests.get(
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
    "&current=wind_speed_10m,wind_direction_10m,temperature_2m"
    "&wind_speed_unit=kmh&timezone=Europe/Paris",
    timeout=20
)
r.raise_for_status()
w = r.json()["current"]
wind_kt  = to_kt(w["wind_speed_10m"])
wind_dir = w["wind_direction_10m"]
temp_c   = w["temperature_2m"]
print(f"  Vent : {wind_kt:.1f}kt  Dir : {wind_dir}°  Temp : {temp_c}°C")

# ── Conditions ───────────────────────────────────────────────────────────────
reasons = []
if wind_kt < MIN_WIND_KT:
    reasons.append(f"vent {wind_kt:.0f}kt < {MIN_WIND_KT}kt")
if has_east_component(wind_dir):
    reasons.append(f"composante Est ({wind_dir:.0f}°)")
if temp_c < MIN_TEMP_C:
    reasons.append(f"temp {temp_c}°C < {MIN_TEMP_C}°C")

if reasons:
    reason = ", ".join(reasons)
    print(f"Conditions KO : {reason}")
    set_output("conditions_ok", "false")
    set_output("reason", reason)
else:
    print("Conditions OK — lancement YOLO")
    set_output("conditions_ok", "true")
    set_output("reason", "ok")
