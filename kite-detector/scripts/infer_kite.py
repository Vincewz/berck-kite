"""
infer_kite.py
Vérifie les conditions météo et lance l'inférence YOLO si conditions favorables.
Appelé quotidiennement par GitHub Actions.

Conditions requises :
  - Vent >= 15 noeuds
  - Pas de composante Est (direction 180°–360° uniquement)
  - Température >= 3°C (raisonnable en grosse combinaison)
  - Heure Paris entre 10h et 18h
"""
import sys, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT  = 15
MIN_TEMP_C   = 3
HOUR_START   = 10
HOUR_END     = 18

BASE_DIR    = Path(__file__).parent.parent
MODEL_PATH  = BASE_DIR / "models" / "kitesurf_v1.pt"
STATUS_FILE = BASE_DIR.parent / "berck-kite" / "kite_status.json"
WEBCAM_URL  = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer/eole"

paris_tz = timezone(timedelta(hours=2))
now = datetime.now(paris_tz)

def to_kt(kmh): return float(kmh) / 1.852

def has_east_component(deg):
    return 0 < float(deg) % 360 < 180

def save_status(data):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Status sauvegarde: {STATUS_FILE}")

# ── 1. Heure de la journee ────────────────────────────────────────────────────
if not (HOUR_START <= now.hour < HOUR_END):
    print(f"Hors plage horaire ({now.hour}h Paris) — skip")
    sys.exit(0)

# ── 2. Fetch meteo actuelle ───────────────────────────────────────────────────
print(f"Fetch meteo Berck ({now.strftime('%H:%M')})...")
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
print(f"  Vent: {wind_kt:.1f}kt  Dir: {wind_dir}°  Temp: {temp_c}°C")

# ── 3. Check conditions ───────────────────────────────────────────────────────
reasons = []
if wind_kt < MIN_WIND_KT:
    reasons.append(f"vent insuffisant ({wind_kt:.0f}kt < {MIN_WIND_KT}kt)")
if has_east_component(wind_dir):
    reasons.append(f"vent d'Est ({wind_dir:.0f}°)")
if temp_c < MIN_TEMP_C:
    reasons.append(f"trop froid ({temp_c}°C)")

if reasons:
    print(f"Conditions non favorables: {', '.join(reasons)}")
    save_status({
        "timestamp":      now.isoformat(),
        "conditions_ok":  False,
        "reason":         ", ".join(reasons),
        "wind_kt":        round(wind_kt, 1),
        "wind_dir":       round(wind_dir),
        "temp_c":         temp_c,
        "kites_detected": 0,
        "boxes":          [],
    })
    sys.exit(0)

# ── 4. Fetch image webcam ─────────────────────────────────────────────────────
img_url = f"{WEBCAM_URL}/{now.year}/{now.month:02d}/{now.day:02d}/large/{now.hour:02d}-00.jpg"
print(f"Fetch image: {img_url}")
resp = requests.get(img_url, timeout=15)
if resp.status_code != 200 or len(resp.content) < 5000:
    # Essayer heure precedente
    prev = now - timedelta(hours=1)
    img_url = f"{WEBCAM_URL}/{prev.year}/{prev.month:02d}/{prev.day:02d}/large/{prev.hour:02d}-00.jpg"
    print(f"Image indispo, essai: {img_url}")
    resp = requests.get(img_url, timeout=15)
    if resp.status_code != 200 or len(resp.content) < 5000:
        print("Image toujours indisponible — abort")
        sys.exit(1)

img_path = Path("/tmp/webcam_kite.jpg")
img_path.write_bytes(resp.content)
print(f"  Image: {len(resp.content)//1024}KB")

# ── 5. Inference YOLO ─────────────────────────────────────────────────────────
print(f"Inference YOLO ({MODEL_PATH.name})...")
from ultralytics import YOLO  # import ici pour ne pas bloquer si conditions KO

model   = YOLO(str(MODEL_PATH))
results = model(str(img_path), conf=0.45, iou=0.5, verbose=False)
result  = results[0]

boxes = []
for box in result.boxes:
    x1, y1, x2, y2 = box.xyxy[0].tolist()
    conf = float(box.conf[0])
    boxes.append({
        "x1": round(x1), "y1": round(y1),
        "x2": round(x2), "y2": round(y2),
        "conf": round(conf, 3),
    })

print(f"  → {len(boxes)} kite(s) detecte(s)")

save_status({
    "timestamp":      now.isoformat(),
    "conditions_ok":  True,
    "wind_kt":        round(wind_kt, 1),
    "wind_dir":       round(wind_dir),
    "temp_c":         temp_c,
    "kites_detected": len(boxes),
    "boxes":          boxes,
    "image_url":      img_url,
})
