"""
collect_images.py
Collecte les images Éole (format large) des 30 derniers jours.

Filtres :
  - 10h–18h heure Paris
  - Vent >= MIN_WIND_KT noeuds
  - Aucune composante Est dans le vent (dir 180°–360° uniquement)
  - Précipitations < MAX_RAIN_MM mm/h
  - Hors festival de cerfs-volants de Berck (17-27 avril chaque année)

Pour accumuler 1000 images, ce script est conçu pour être lancé
quotidiennement via GitHub Actions — les images déjà téléchargées
sont skippées automatiquement.
"""

import os, requests, time, math
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ── Config ────────────────────────────────────────────────────────────────────
BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT   = 8           # seuil vent (nœuds) — bas pour maximiser les images
MAX_RAIN_MM   = 0.3         # seuil pluie mm/h
HOUR_START    = 10
HOUR_END      = 18
DAYS_BACK     = 30

# Festival de cerfs-volants de Berck : 17-27 avril chaque année
FESTIVAL_MONTH = 4
FESTIVAL_START = 17
FESTIVAL_END   = 27

BASE_URL = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer/eole"
OUT_DIR  = Path(__file__).parent.parent / "dataset" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def to_kt(kmh): return float(kmh) / 1.852

def has_east_component(deg):
    """Retourne True si le vent a une composante Est (à exclure)."""
    d = float(deg) % 360
    # Composante Est = sin(angle) > 0 → direction entre 1° et 179°
    return 0 < d < 180

def is_festival(dt):
    """Retourne True si la date tombe dans le festival de cerfs-volants."""
    return (dt.month == FESTIVAL_MONTH and
            FESTIVAL_START <= dt.day <= FESTIVAL_END)

# ── 1. Données météo historiques ──────────────────────────────────────────────
paris_tz = timezone(timedelta(hours=2))
now   = datetime.now(paris_tz)
start = (now - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
end   = now.strftime("%Y-%m-%d")

print(f"Fetch meteo {start} -> {end}...")
r = requests.get(
    "https://archive-api.open-meteo.com/v1/archive"
    f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
    f"&start_date={start}&end_date={end}"
    "&hourly=wind_speed_10m,wind_direction_10m,precipitation"
    "&wind_speed_unit=kmh&timezone=Europe/Paris",
    timeout=30
)
data   = r.json()
times  = data["hourly"]["time"]
winds  = data["hourly"]["wind_speed_10m"]
dirs   = data["hourly"]["wind_direction_10m"]
rains  = data["hourly"]["precipitation"]

# ── 2. Filtrage ───────────────────────────────────────────────────────────────
candidates, rejected = [], {"festival": 0, "east": 0, "wind": 0, "rain": 0, "night": 0}
for i, t in enumerate(times):
    dt   = datetime.fromisoformat(t)
    hour = dt.hour
    kt   = to_kt(winds[i] or 0)
    deg  = dirs[i] or 0
    rain = rains[i] or 0

    if not (HOUR_START <= hour < HOUR_END):
        rejected["night"] += 1; continue
    if is_festival(dt):
        rejected["festival"] += 1; continue
    if has_east_component(deg):
        rejected["east"] += 1; continue
    if rain >= MAX_RAIN_MM:
        rejected["rain"] += 1; continue
    if kt < MIN_WIND_KT:
        rejected["wind"] += 1; continue
    candidates.append((dt, kt, deg, rain))

print(f"  {len(candidates)} creneaux valides | rejetes: {rejected}")

# ── 3. Téléchargement ─────────────────────────────────────────────────────────
downloaded, skipped, errors = 0, 0, 0

for dt, kt, deg, rain in candidates:
    fname = f"{dt.strftime('%Y%m%d_%H00')}_w{int(kt)}kt_d{int(deg)}deg.jpg"
    fpath = OUT_DIR / fname

    if fpath.exists():
        skipped += 1
        continue

    url = f"{BASE_URL}/{dt.year}/{dt.month:02d}/{dt.day:02d}/large/{dt.hour:02d}-00.jpg"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200 and len(resp.content) > 5000:
            fpath.write_bytes(resp.content)
            downloaded += 1
            print(f"  OK  {fname}  ({len(resp.content)//1024}KB)")
        else:
            errors += 1
    except Exception:
        errors += 1
    time.sleep(0.05)

total = len(list(OUT_DIR.glob("*.jpg")))
print(f"\nTermine: {downloaded} telecharges, {skipped} deja la, {errors} erreurs")
print(f"Total dataset/raw: {total} images")
print(f"Objectif 1000: encore {max(0, 1000 - total)} images a collecter")
