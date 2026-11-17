"""
forecast_kite.py
Analyse les prévisions horaires 10h-18h et écrit kite_forecast.json.
Lancé à 7h chaque matin — avant les runs de détection horaires.
"""
import json, requests, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT  = 13
MIN_TEMP_C   = 3
HOUR_START   = 10
HOUR_END     = 18

BASE_DIR      = Path(__file__).parent.parent
FORECAST_FILE = BASE_DIR.parent / "berck-kite" / "kite_forecast.json"

paris_tz = ZoneInfo("Europe/Paris")
now      = datetime.now(paris_tz)
today    = now.strftime("%Y-%m-%d")

def to_kt(kmh): return float(kmh) / 1.852
def has_east(deg): return 0 < float(deg) % 360 < 180

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)))
query = (
    f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
    "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m"
    "&wind_speed_unit=kmh&timezone=Europe/Paris&forecast_days=2"
)
for endpoint in ("forecast", "meteofrance"):
    try:
        r = session.get(f"https://api.open-meteo.com/v1/{endpoint}{query}", timeout=(10, 30))
        r.raise_for_status()
        hr = r.json()["hourly"]
        break
    except requests.RequestException:
        if endpoint == "meteofrance":
            raise
        print("::warning::Open-Meteo best-match indisponible ; essai du modèle Météo-France.")

slots = []
for i, t in enumerate(hr["time"]):
    if not t.startswith(today): continue
    hour = int(t[11:13])
    if not (HOUR_START <= hour < HOUR_END): continue
    kt   = to_kt(hr["wind_speed_10m"][i])
    gkt  = to_kt(hr["wind_gusts_10m"][i])
    deg  = float(hr["wind_direction_10m"][i])
    temp = float(hr["temperature_2m"][i])
    ok   = kt >= MIN_WIND_KT and not has_east(deg) and temp >= MIN_TEMP_C
    slots.append({
        "hour": f"{hour:02d}:00",
        "kt":   round(kt, 1),
        "gkt":  round(gkt, 1),
        "dir":  round(deg),
        "temp": round(temp, 1),
        "favorable": ok,
    })

favorable_hours = [s["hour"] for s in slots if s["favorable"]]
peak = max(slots, key=lambda s: s["kt"]) if slots else None

# Détecte les fenêtres continues de bonnes conditions
windows = []
window = []
for s in slots:
    if s["favorable"]:
        window.append(s["hour"])
    else:
        if len(window) >= 2:
            windows.append({"start": window[0], "end": window[-1], "hours": len(window)})
        window = []
if len(window) >= 2:
    windows.append({"start": window[0], "end": window[-1], "hours": len(window)})

forecast = {
    "date":            today,
    "generated_at":    now.isoformat(),
    "favorable_hours": favorable_hours,
    "kite_likely":     len(favorable_hours) >= 2,
    "windows":         windows,  # fenêtres continues >= 2h
    "peak":            peak,
    "slots":           slots,
    # Timing webcam : snapshot à XX:00, dispo sur S3 ~XX:10, fetch à XX:20
    "webcam_fetch_offset_min": 20,
}

FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps(forecast, indent=2, ensure_ascii=False)
FORECAST_FILE.write_text(payload)
(BASE_DIR.parent / "kite_forecast.json").write_text(payload)

print(f"Forecast {today}: {len(favorable_hours)}/{len(slots)} créneaux favorables")
for s in slots:
    flag = "✓" if s["favorable"] else "✗"
    print(f"  {flag} {s['hour']}  {s['kt']:.0f}kt  {s['dir']}°  {s['temp']}°C")
if windows:
    for w in windows:
        print(f"  → Fenêtre continue : {w['start']}–{w['end']} ({w['hours']}h)")
if peak:
    print(f"  Pic : {peak['hour']} → {peak['kt']:.0f}kt rafales {peak['gkt']:.0f}kt")
