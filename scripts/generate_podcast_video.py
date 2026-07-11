"""
Build a short daily video from the podcast audio and sunrise webcam archives.

The script is intentionally standalone so it can run locally and in GitHub
Actions right after scripts/generate_daily_podcast.py.
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


BASE = Path(__file__).parent.parent
PODCAST_AUDIO = BASE / "podcast" / "today.mp3"
OUT_VIDEO = BASE / "podcast" / "today.mp4"
FRAME_DIR = BASE / "podcast" / "video_frames"
MANIFEST = FRAME_DIR / "manifest.json"
S3_BASE = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer"
TZ = ZoneInfo("Europe/Paris")
BERCK_LAT = 50.4113
BERCK_LON = 1.5676

CAMERAS = [
    {"slug": "baie-d-authie", "label": "Baie d'Authie"},
    {"slug": "entonnoir", "label": "Entonnoir"},
    {"slug": "maritime", "label": "Maritime"},
    {"slug": "mer", "label": "La Mer"},
    {"slug": "poste-de-secours", "label": "Poste de Secours"},
    {"slug": "eole", "label": "Eole"},
]


def run_json(cmd: list[str]) -> dict:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def media_duration(path: Path) -> float:
    payload = run_json([
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ])
    return float(payload["format"]["duration"])


def fetch_sunrise(now: datetime) -> datetime:
    today = now.date().isoformat()
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        "&daily=sunrise"
        "&timezone=Europe/Paris"
        f"&start_date={today}&end_date={today}"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    sunrise = response.json()["daily"]["sunrise"][0]
    return datetime.fromisoformat(sunrise).replace(tzinfo=TZ)


def round_to_quarter(dt: datetime) -> datetime:
    minute = int(round(dt.minute / 15) * 15)
    rounded = dt.replace(minute=0, second=0, microsecond=0)
    if minute == 60:
        return rounded + timedelta(hours=1)
    return rounded.replace(minute=minute)


def candidate_times(sunrise: datetime) -> list[datetime]:
    anchor = round_to_quarter(sunrise)
    offsets = [0, -15, 15, -30, 30, -45, 45, -60, 60, -90, 90]
    seen = set()
    candidates = []
    for minutes in offsets:
        dt = anchor + timedelta(minutes=minutes)
        key = dt.strftime("%Y%m%d%H%M")
        if key not in seen:
            seen.add(key)
            candidates.append(dt)
    return candidates


def image_urls(slug: str, dt: datetime) -> list[str]:
    day = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    hour = f"{dt.hour:02d}"
    minute = f"{(dt.minute // 15) * 15:02d}"
    base = f"{S3_BASE}/{slug}/{day}"
    if slug == "eole":
        return [
            f"{base}/large/{hour}-00.jpg",
            f"{base}/small/{hour}-00.jpg",
            f"{base}/{hour}-00.jpg",
        ]
    return [
        f"{base}/large/{hour}-{minute}.jpg",
        f"{base}/small/{hour}-{minute}.jpg",
        f"{base}/{hour}-{minute}.jpg",
        f"{base}/small/{hour}-00.jpg",
        f"{base}/{hour}-00.jpg",
    ]


def valid_image(content: bytes) -> bool:
    return len(content) > 5_000 and content[:2] == b"\xff\xd8"


def normalize_image(path: Path) -> None:
    tmp = path.with_suffix(".normalized.jpg")
    try:
        subprocess.run([
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1",
            "-frames:v",
            "1",
            str(tmp),
        ], check=True)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def fetch_frame(camera: dict, sunrise: datetime) -> dict | None:
    slug = camera["slug"]
    out = FRAME_DIR / f"{slug}.jpg"
    for dt in candidate_times(sunrise):
        for url in image_urls(slug, dt):
            try:
                response = requests.get(url, timeout=12)
                if response.status_code == 200 and valid_image(response.content):
                    out.write_bytes(response.content)
                    normalize_image(out)
                    return {
                        "camera": slug,
                        "label": camera["label"],
                        "source": "archive",
                        "url": url,
                        "captured_at": dt.isoformat(),
                        "path": str(out.relative_to(BASE)).replace("\\", "/"),
                    }
            except requests.RequestException:
                pass
            time.sleep(0.03)

    fallback = BASE / "cams" / f"{slug}.jpg"
    if fallback.exists() and fallback.stat().st_size > 5_000:
        out.write_bytes(fallback.read_bytes())
        normalize_image(out)
        return {
            "camera": slug,
            "label": camera["label"],
            "source": "local_fallback",
            "url": str(fallback.relative_to(BASE)).replace("\\", "/"),
            "captured_at": None,
            "path": str(out.relative_to(BASE)).replace("\\", "/"),
        }
    return None


def ffmpeg_escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def build_video(frames: list[dict], audio_duration: float) -> None:
    if not frames:
        raise RuntimeError("No webcam image available for podcast video")

    per_frame = max(4.0, audio_duration / len(frames))
    concat_lines = []
    for frame in frames:
        concat_lines.append(f"file '{ffmpeg_escape(BASE / frame['path'])}'")
        concat_lines.append(f"duration {per_frame:.3f}")
    concat_lines.append(f"file '{ffmpeg_escape(BASE / frames[-1]['path'])}'")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write("\n".join(concat_lines))
        concat_file = Path(handle.name)

    fade_start = max(0, math.floor(audio_duration - 1.0))
    vf = f"fade=t=out:st={fade_start}:d=1,format=yuv420p"
    try:
        subprocess.run([
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-i",
            str(PODCAST_AUDIO),
            "-vf",
            vf,
            "-r",
            "25",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-t",
            f"{audio_duration:.3f}",
            "-movflags",
            "+faststart",
            str(OUT_VIDEO),
        ], check=True)
    finally:
        concat_file.unlink(missing_ok=True)


def main() -> None:
    if not PODCAST_AUDIO.exists():
        raise FileNotFoundError(f"Missing podcast audio: {PODCAST_AUDIO}")

    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    sunrise = fetch_sunrise(now)
    frames = []

    print(f"Sunrise reference: {sunrise.strftime('%Y-%m-%d %H:%M %Z')}")
    for camera in CAMERAS:
        frame = fetch_frame(camera, sunrise)
        if frame:
            frames.append(frame)
            print(f"  OK {camera['slug']}: {frame['source']} {frame['captured_at'] or frame['url']}")
        else:
            print(f"  -- {camera['slug']}: no image available")

    audio_duration = media_duration(PODCAST_AUDIO)
    build_video(frames, audio_duration)

    manifest = {
        "generated_at": now.isoformat(),
        "sunrise": sunrise.isoformat(),
        "audio": "podcast/today.mp3",
        "video": "podcast/today.mp4",
        "audio_duration_seconds": round(audio_duration, 3),
        "frames": frames,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Video ready: {OUT_VIDEO.relative_to(BASE)} ({OUT_VIDEO.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
