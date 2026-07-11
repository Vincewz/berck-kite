"""
infer_kite.py
Checks weather conditions and runs YOLO inference on the Berck webcams.
Called by GitHub Actions.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BERCK_LAT, BERCK_LON = 50.4, 1.6
MIN_WIND_KT = 8
MAX_RAIN_MM = 0.3
MIN_TEMP_C = 3
HOUR_START = 10
HOUR_END = 18
FESTIVAL_MONTH = 4
FESTIVAL_START = 17
FESTIVAL_END = 27

BASE_DIR = Path(__file__).parent.parent
MODEL_PATH = BASE_DIR / "models" / "kitesurf_v5.pt"
MODEL_V1 = BASE_DIR / "models" / "kitesurf_v1.pt"
SITE_DIR = BASE_DIR.parent
PUBLIC_DATA_DIR = SITE_DIR / "berck-kite"
STATUS_FILES = [SITE_DIR / "kite_status.json", PUBLIC_DATA_DIR / "kite_status.json"]
HISTORY_FILES = [SITE_DIR / "detection_history.json", PUBLIC_DATA_DIR / "detection_history.json"]
S3_BASE = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer"
RAW_DIR = BASE_DIR / "dataset" / "raw"

CAMERAS = [
    {"name": "eole", "label": "Eole", "large": True, "minutes": ["00"]},
    {"name": "maritime", "label": "Maritime", "large": False, "minutes": ["00", "15", "30", "45"]},
    {"name": "mer", "label": "La Mer", "large": False, "minutes": ["00", "15", "30", "45"]},
]

paris_tz = timezone(timedelta(hours=2))
now = datetime.now(paris_tz)


def to_kt(kmh):
    return float(kmh) / 1.852


def has_east_component(deg):
    return 0 < float(deg) % 360 < 180


def is_festival(dt):
    return dt.month == FESTIVAL_MONTH and FESTIVAL_START <= dt.day <= FESTIVAL_END


def load_previous_status():
    statuses = []
    for status_file in STATUS_FILES:
        try:
            statuses.append(json.loads(status_file.read_text()))
        except Exception:
            pass
    if statuses:
        statuses.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return statuses[0]
    return {}


def load_history():
    histories = []
    for history_file in HISTORY_FILES:
        try:
            data = json.loads(history_file.read_text()) if history_file.exists() else []
            if isinstance(data, list):
                histories.append(data)
        except Exception:
            pass
    if not histories:
        return []
    return max(histories, key=len)


def write_json_all(files, data):
    for file_path in files:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def append_history(entry):
    history = load_history()
    history.append(entry)
    write_json_all(HISTORY_FILES, history)
    print(f"Historique: {len(history)} entree(s)")


def save_status(data):
    write_json_all(STATUS_FILES, data)
    print("Status sauvegarde: " + ", ".join(str(path) for path in STATUS_FILES))


def status_base(previous_status, conditions_ok, **extra):
    last_kites = previous_status.get("last_kites") or []
    if not last_kites and previous_status.get("last_kite"):
        last_kites = [previous_status["last_kite"]]
    data = {
        "timestamp": now.isoformat(),
        "conditions_ok": conditions_ok,
        "kites_detected": 0,
        "boxes": [],
        "last_kite": previous_status.get("last_kite"),
        "last_kites": last_kites,
    }
    data.update(extra)
    return data


def candidate_urls(camera, dt):
    day = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    urls = []
    for minute in camera["minutes"]:
        if camera["large"]:
            urls.append(f"{S3_BASE}/{camera['name']}/{day}/large/{dt.hour:02d}-{minute}.jpg")
        urls.append(f"{S3_BASE}/{camera['name']}/{day}/{dt.hour:02d}-{minute}.jpg")
    return urls


def fetch_camera_image(camera):
    for dt in (now, now - timedelta(hours=1)):
        for url in candidate_urls(camera, dt):
            print(f"Fetch image {camera['name']}: {url}")
            try:
                resp = requests.get(url, timeout=15)
            except requests.exceptions.RequestException as exc:
                print(f"  Skip {camera['name']}: {exc}")
                continue
            if resp.status_code == 200 and len(resp.content) > 5000:
                img_path = Path(f"/tmp/webcam_kite_{camera['name']}.jpg")
                img_path.write_bytes(resp.content)
                print(f"  Image {camera['name']}: {len(resp.content) // 1024}KB")
                save_raw_image(camera, resp.content, dt)
                return img_path, url
    return None, None


def save_raw_image(camera, content, dt):
    if camera["name"] not in {"maritime", "mer"}:
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"{dt.strftime('%Y%m%d_%H%M')}_w{int(wind_kt)}kt_d{int(wind_dir)}deg"
    fpath = RAW_DIR / f"{camera['name']}_{slug}.jpg"
    if not fpath.exists():
        fpath.write_bytes(content)
        print(f"  Saved {fpath.name} ({len(content) // 1024}KB)")


def nms(detections, iou_thr=0.4):
    detections = sorted(detections, key=lambda b: b[4], reverse=True)
    kept = []
    for detection in detections:
        if not any(iou(detection[:4], kept_box[:4]) > iou_thr for kept_box in kept):
            kept.append(detection)
    return kept


def iou(a, b):
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0


def detect_boxes(img_path, models):
    detections = []
    for model in models:
        res = model(str(img_path), conf=0.15, iou=0.5, verbose=False)[0]
        for box in res.boxes:
            x1, y1, x2, y2 = box.xyxyn[0].tolist()
            detections.append((x1, y1, x2, y2, float(box.conf[0])))
    boxes = []
    for x1, y1, x2, y2, conf in nms(detections):
        boxes.append({
            "x1": round(x1, 4),
            "y1": round(y1, 4),
            "x2": round(x2, 4),
            "y2": round(y2, 4),
            "conf": round(conf, 3),
        })
    return boxes


previous_status = load_previous_status()

if not (HOUR_START <= now.hour < HOUR_END):
    print(f"Hors plage horaire ({now.hour}h Paris) - skip")
    sys.exit(0)

if is_festival(now):
    print("Festival de cerfs-volants de Berck - skip")
    save_status(status_base(previous_status, False, reason="festival de cerfs-volants"))
    sys.exit(0)

print(f"Fetch meteo Berck ({now.strftime('%H:%M')})...")
for attempt in range(3):
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
            "&current=wind_speed_10m,wind_direction_10m,temperature_2m,precipitation"
            "&wind_speed_unit=kmh&timezone=Europe/Paris",
            timeout=20,
        )
        r.raise_for_status()
        break
    except requests.exceptions.RequestException as exc:
        print(f"  Tentative {attempt + 1}/3 echouee : {exc}")
        if attempt < 2:
            time.sleep(10)
        else:
            print("API meteo indisponible - skip")
            sys.exit(0)

w = r.json()["current"]
wind_kt = to_kt(w["wind_speed_10m"])
wind_dir = w["wind_direction_10m"]
temp_c = w["temperature_2m"]
rain_mm = w.get("precipitation") or 0
print(f"  Vent: {wind_kt:.1f}kt  Dir: {wind_dir}deg  Temp: {temp_c}C  Pluie: {rain_mm}mm")

common_weather = {
    "wind_kt": round(wind_kt, 1),
    "wind_dir": round(wind_dir),
    "temp_c": temp_c,
    "rain_mm": rain_mm,
}

reasons = []
if wind_kt < MIN_WIND_KT:
    reasons.append(f"vent insuffisant ({wind_kt:.0f}kt < {MIN_WIND_KT}kt)")
if has_east_component(wind_dir):
    reasons.append(f"vent d'Est ({wind_dir:.0f}deg)")
if rain_mm >= MAX_RAIN_MM:
    reasons.append(f"pluie {rain_mm:.1f}mm >= {MAX_RAIN_MM}mm")
if temp_c < MIN_TEMP_C:
    reasons.append(f"trop froid ({temp_c}C)")

if reasons:
    print(f"Conditions non favorables: {', '.join(reasons)}")
    save_status(status_base(
        previous_status,
        False,
        reason=", ".join(reasons),
        **common_weather,
    ))
    sys.exit(0)

print("Inference YOLO (ensemble v1+v5 conf=0.15) sur les webcams...")
from ultralytics import YOLO  # imported only when conditions allow inference

models = [YOLO(str(MODEL_PATH))]
if MODEL_V1.exists():
    models.append(YOLO(str(MODEL_V1)))

current_kites = []
for camera in CAMERAS:
    img_path, img_url = fetch_camera_image(camera)
    if not img_path:
        print(f"  {camera['name']}: webcam indisponible")
        continue

    boxes = detect_boxes(img_path, models)
    print(f"  {camera['name']}: {len(boxes)} kite(s) detecte(s)")
    if not boxes:
        continue

    kite_entry = {
        "camera": camera["name"],
        "camera_label": camera["label"],
        "timestamp": now.isoformat(),
        "image_url": img_url,
        "kites_detected": len(boxes),
        "boxes": boxes,
    }
    current_kites.append(kite_entry)
    append_history({
        **common_weather,
        "timestamp": now.isoformat(),
        "camera": camera["name"],
        "camera_label": camera["label"],
        "kites_detected": len(boxes),
        "max_conf": round(max(box["conf"] for box in boxes), 3),
        "image_url": img_url,
    })

last_kites = current_kites or previous_status.get("last_kites") or []
if not last_kites and previous_status.get("last_kite"):
    last_kites = [previous_status["last_kite"]]

last_kite = None
if current_kites:
    last_kite = max(current_kites, key=lambda entry: max(box["conf"] for box in entry["boxes"]))
else:
    last_kite = previous_status.get("last_kite")

status_boxes = last_kite.get("boxes", []) if last_kite else []
save_status({
    "timestamp": now.isoformat(),
    "conditions_ok": True,
    **common_weather,
    "kites_detected": sum(entry["kites_detected"] for entry in current_kites),
    "boxes": status_boxes,
    "image_url": last_kite.get("image_url") if last_kite else None,
    "last_kite": last_kite,
    "last_kites": last_kites,
})
