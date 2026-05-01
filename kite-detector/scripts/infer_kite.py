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
import sys, json, time, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT  = 15
MIN_TEMP_C   = 3
HOUR_START   = 10
HOUR_END     = 18

BASE_DIR    = Path(__file__).parent.parent
MODEL_PATH  = BASE_DIR / "models" / "kitesurf_v2.pt"
MODEL_V1    = BASE_DIR / "models" / "kitesurf_v1.pt"
STATUS_FILE = BASE_DIR.parent / "berck-kite" / "kite_status.json"
WEBCAM_URL  = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer/eole"

paris_tz = timezone(timedelta(hours=2))
now = datetime.now(paris_tz)

def to_kt(kmh): return float(kmh) / 1.852

def has_east_component(deg):
    return 0 < float(deg) % 360 < 180

HISTORY_FILE = BASE_DIR.parent / "berck-kite" / "detection_history.json"

def load_last_kite():
    """Lit le last_kite existant pour le conserver si pas de détection."""
    try:
        prev = json.loads(STATUS_FILE.read_text())
        return prev.get("last_kite")
    except Exception:
        return None

def append_history(entry: dict):
    """Ajoute une détection positive à l'historique."""
    try:
        history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        history = []
    history.append(entry)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"Historique: {len(history)} entrée(s)")

def save_status(data):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Status sauvegarde: {STATUS_FILE}")

# ── 1. Heure de la journee ────────────────────────────────────────────────────
last_kite = load_last_kite()

if not (HOUR_START <= now.hour < HOUR_END):
    print(f"Hors plage horaire ({now.hour}h Paris) — skip")
    sys.exit(0)

# ── 2. Fetch meteo actuelle ───────────────────────────────────────────────────
print(f"Fetch meteo Berck ({now.strftime('%H:%M')})...")
for attempt in range(3):
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
            "&current=wind_speed_10m,wind_direction_10m,temperature_2m"
            "&wind_speed_unit=kmh&timezone=Europe/Paris",
            timeout=20
        )
        r.raise_for_status()
        break
    except requests.exceptions.RequestException as e:
        print(f"  Tentative {attempt+1}/3 échouée : {e}")
        if attempt < 2:
            time.sleep(10)
        else:
            print("API météo indisponible — skip")
            sys.exit(0)
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
        "last_kite":      last_kite,
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
print("Inference YOLO (ensemble v1+v2 conf=0.15)...")
from ultralytics import YOLO  # import ici pour ne pas bloquer si conditions KO

def _predict(model_path):
    m = YOLO(str(model_path))
    res = m(str(img_path), conf=0.15, iou=0.5, verbose=False)[0]
    out = []
    for box in res.boxes:
        x1, y1, x2, y2 = box.xyxyn[0].tolist()
        out.append((x1, y1, x2, y2, float(box.conf[0])))
    return out

def _nms(detections, iou_thr=0.5):
    detections = sorted(detections, key=lambda b: b[4], reverse=True)
    kept = []
    for d in detections:
        def _iou(a, b):
            ix1 = max(a[0],b[0]); iy1 = max(a[1],b[1])
            ix2 = min(a[2],b[2]); iy2 = min(a[3],b[3])
            inter = max(0,ix2-ix1)*max(0,iy2-iy1)
            ua = (a[2]-a[0])*(a[3]-a[1]); ub = (b[2]-b[0])*(b[3]-b[1])
            return inter/(ua+ub-inter) if ua+ub-inter>0 else 0
        if not any(_iou(d[:4], k[:4]) > iou_thr for k in kept):
            kept.append(d)
    return kept

raw = _predict(MODEL_PATH) + (_predict(MODEL_V1) if MODEL_V1.exists() else [])
merged = _nms(raw, iou_thr=0.4)

boxes = []
for x1, y1, x2, y2, conf in merged:
    boxes.append({
        "x1": round(x1, 4), "y1": round(y1, 4),
        "x2": round(x2, 4), "y2": round(y2, 4),
        "conf": round(conf, 3),
    })

print(f"  → {len(boxes)} kite(s) detecte(s)")

if len(boxes) > 0:
    last_kite = {
        "timestamp":      now.isoformat(),
        "image_url":      img_url,
        "kites_detected": len(boxes),
        "boxes":          boxes,
    }
    max_conf = max(b["conf"] for b in boxes)
    append_history({
        "timestamp":      now.isoformat(),
        "wind_kt":        round(wind_kt, 1),
        "wind_dir":       round(wind_dir),
        "temp_c":         temp_c,
        "kites_detected": len(boxes),
        "max_conf":       round(max_conf, 3),
        "image_url":      img_url,
    })

save_status({
    "timestamp":      now.isoformat(),
    "conditions_ok":  True,
    "wind_kt":        round(wind_kt, 1),
    "wind_dir":       round(wind_dir),
    "temp_c":         temp_c,
    "kites_detected": len(boxes),
    "boxes":          boxes,
    "image_url":      img_url,
    "last_kite":      last_kite,
})
