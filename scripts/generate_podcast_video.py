"""
Build a short daily video from the podcast audio and sunrise webcam archives.

The script is intentionally standalone so it can run locally and in GitHub
Actions right after scripts/generate_daily_podcast.py.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


BASE = Path(__file__).parent.parent
PODCAST_AUDIO = BASE / "podcast" / "today.mp3"
PODCAST_SCRIPT = BASE / "podcast" / "tts" / "script.txt"
OUT_VIDEO = BASE / "podcast" / "today.mp4"
FRAME_DIR = BASE / "podcast" / "video_frames"
MANIFEST = FRAME_DIR / "manifest.json"
SUBTITLE_ASS = FRAME_DIR / "subtitles.ass"
S3_BASE = "https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer"
TZ = ZoneInfo("Europe/Paris")
BERCK_LAT = 50.4113
BERCK_LON = 1.5676
ENHANCE_FRAMES = os.getenv("ENHANCE_PODCAST_FRAMES", "").lower() in {"1", "true", "yes"}
REALESRGAN_BIN = os.getenv("REALESRGAN_BIN", "realesrgan-ncnn-vulkan")

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


def enhance_image(path: Path) -> bool:
    if not ENHANCE_FRAMES:
        return False

    exe = shutil.which(REALESRGAN_BIN) or (REALESRGAN_BIN if Path(REALESRGAN_BIN).exists() else None)
    if not exe:
        print("  Real-ESRGAN unavailable, keeping original frame")
        return False

    small = path.with_suffix(".realesrgan.input.jpg")
    upscaled = path.with_suffix(".realesrgan.x4.png")
    enhanced = path.with_suffix(".realesrgan.enhanced.jpg")
    try:
        subprocess.run([
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "scale=640:360:flags=lanczos",
            str(small),
        ], check=True)
        start = time.perf_counter()
        subprocess.run([
            str(exe),
            "-i",
            str(small),
            "-o",
            str(upscaled),
            "-n",
            "realesrgan-x4plus",
            "-f",
            "png",
        ], check=True, timeout=180)
        subprocess.run([
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(upscaled),
            "-vf",
            "scale=1280:720:flags=lanczos",
            str(enhanced),
        ], check=True)
        enhanced.replace(path)
        print(f"  Real-ESRGAN enhanced {path.name} in {time.perf_counter() - start:.1f}s")
        return True
    except Exception as exc:
        print(f"  Real-ESRGAN skipped for {path.name}: {exc}")
        return False
    finally:
        small.unlink(missing_ok=True)
        upscaled.unlink(missing_ok=True)
        enhanced.unlink(missing_ok=True)


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
                    enhanced = enhance_image(out)
                    return {
                        "camera": slug,
                        "label": camera["label"],
                        "source": "archive",
                        "enhanced": enhanced,
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
        enhanced = enhance_image(out)
        return {
            "camera": slug,
            "label": camera["label"],
            "source": "local_fallback",
            "enhanced": enhanced,
            "url": str(fallback.relative_to(BASE)).replace("\\", "/"),
            "captured_at": None,
            "path": str(out.relative_to(BASE)).replace("\\", "/"),
        }
    return None


def ass_time(seconds: float) -> str:
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def subtitle_chunks(script: str) -> list[str]:
    script = re.sub(r"\s+", " ", script).strip()
    if not script:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    chunks = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= 11:
            chunks.append(sentence)
            continue
        for i in range(0, len(words), 9):
            chunks.append(" ".join(words[i:i + 9]))
    return chunks


def write_ass_subtitles(audio_duration: float) -> list[dict]:
    if not PODCAST_SCRIPT.exists():
        return []

    chunks = subtitle_chunks(PODCAST_SCRIPT.read_text(encoding="utf-8"))
    if not chunks:
        return []

    voice_start = min(3.0, max(0, audio_duration - 5))
    voice_end = max(voice_start + 1, audio_duration - 0.8)
    total_words = sum(max(1, len(chunk.split())) for chunk in chunks)
    cursor = voice_start
    events = []

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,34,&H00FFFFFF,&H00FFFFFF,&H99000000,&H77000000,-1,0,0,0,100,100,0,0,1,2.6,0,2,92,92,44,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip()]
    usable_duration = voice_end - voice_start
    for chunk in chunks:
        weight = max(1, len(chunk.split()))
        duration = max(1.6, usable_duration * weight / total_words)
        end = min(voice_end, cursor + duration)
        wrapped = r"\N".join(textwrap.wrap(ass_escape(chunk), width=42))
        lines.append(f"Dialogue: 0,{ass_time(cursor)},{ass_time(end)},Default,,0,0,0,,{wrapped}")
        events.append({"start": round(cursor, 2), "end": round(end, 2), "text": chunk})
        cursor = end
        if cursor >= voice_end - 0.05:
            break

    SUBTITLE_ASS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return events


def build_video(frames: list[dict], audio_duration: float) -> list[dict]:
    if not frames:
        raise RuntimeError("No webcam image available for podcast video")

    subtitle_events = write_ass_subtitles(audio_duration)
    transition = min(0.9, max(0.35, audio_duration / len(frames) * 0.12))
    clip_duration = (audio_duration + transition * (len(frames) - 1)) / len(frames)
    fade_start = max(0, math.floor(audio_duration - 1.0))

    inputs = []
    filters = []
    for idx, frame in enumerate(frames):
        inputs.extend(["-loop", "1", "-t", f"{clip_duration + 0.1:.3f}", "-i", str(BASE / frame["path"])])
        direction = "t" if idx % 2 == 0 else f"{clip_duration:.3f}-t"
        x_expr = f"min(iw-ow,max(0,(iw-ow)*({direction})/{clip_duration:.3f}))"
        y_expr = f"min(ih-oh,max(0,(ih-oh)*(0.35+0.30*t/{clip_duration:.3f})))"
        filters.append(
            f"[{idx}:v]scale=1408:792,setsar=1,"
            f"crop=1280:720:x='{x_expr}':y='{y_expr}',"
            f"trim=duration={clip_duration:.3f},setpts=PTS-STARTPTS,fps=25,format=yuv420p[v{idx}]"
        )

    if len(frames) == 1:
        last_label = "v0"
    else:
        last_label = "vx1"
        filters.append(
            f"[v0][v1]xfade=transition=fade:duration={transition:.3f}:"
            f"offset={clip_duration - transition:.3f}[{last_label}]"
        )
        for idx in range(2, len(frames)):
            next_label = f"vx{idx}"
            offset = idx * (clip_duration - transition)
            filters.append(
                f"[{last_label}][v{idx}]xfade=transition=fade:duration={transition:.3f}:"
                f"offset={offset:.3f}[{next_label}]"
            )
            last_label = next_label

    final_vf = f"[{last_label}]fade=t=out:st={fade_start}:d=1"
    if subtitle_events:
        final_vf += ",ass='podcast/video_frames/subtitles.ass'"
    final_vf += ",format=yuv420p[vout]"
    filters.append(final_vf)

    subprocess.run([
        "ffmpeg",
        "-y",
        *inputs,
        "-i",
        str(PODCAST_AUDIO),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        f"{len(frames)}:a",
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
    ], check=True, cwd=BASE)

    return subtitle_events


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
    subtitle_events = build_video(frames, audio_duration)

    manifest = {
        "generated_at": now.isoformat(),
        "sunrise": sunrise.isoformat(),
        "audio": "podcast/today.mp3",
        "video": "podcast/today.mp4",
        "audio_duration_seconds": round(audio_duration, 3),
        "frames": frames,
        "subtitles": {
            "source": str(PODCAST_SCRIPT.relative_to(BASE)).replace("\\", "/") if PODCAST_SCRIPT.exists() else None,
            "events": len(subtitle_events),
            "burned_in": bool(subtitle_events),
        },
        "motion": {
            "type": "bounded_pan",
            "transition": "xfade",
        },
        "enhancement": {
            "enabled": ENHANCE_FRAMES,
            "engine": "Real-ESRGAN ncnn Vulkan" if ENHANCE_FRAMES else None,
            "model": "realesrgan-x4plus" if ENHANCE_FRAMES else None,
            "pre_scale": "640x360 -> x4 -> 1280x720" if ENHANCE_FRAMES else None,
            "frames_enhanced": sum(1 for frame in frames if frame.get("enhanced")),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Video ready: {OUT_VIDEO.relative_to(BASE)} ({OUT_VIDEO.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
