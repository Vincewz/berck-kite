"""
generate_daily_podcast.py — Lancé chaque matin par GitHub Actions
1. Fetch conditions Open-Meteo
2. Mistral génère UNE phrase descriptive (~80 chars)
3. ElevenLabs TTS seulement pour: date + phrase descriptive
4. ffmpeg assemble tout avec la musique de fond
5. Écrit podcast/today.mp3
"""

import os, json, subprocess, tempfile, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
import math

# ── Config ────────────────────────────────────────────────────────────────────
MISTRAL_KEY  = os.environ["MISTRAL_API_KEY"]
ELEVEN_KEY   = os.environ["ELEVENLABS_API_KEY"]
MODEL_ID     = "eleven_multilingual_v2"
BERCK_LAT    = 50.4
BERCK_LON    = 1.6

BASE      = Path(__file__).parent.parent
STATIC    = BASE / "podcast" / "static"
JINGLE_IN = BASE / "podcast" / "jingle_intro.mp3"
JINGLE_BG = BASE / "podcast" / "jingle_bg.mp3"
OUT_FILE  = BASE / "podcast" / "today.mp3"

# ── Helpers ───────────────────────────────────────────────────────────────────
def to_kt(kmh):
    return max(0, round(float(kmh) / 1.852))

def dir_label(deg):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
    return dirs[round(float(deg) / 22.5) % 16]

def wmo_label(code):
    code = int(code)
    if code == 0:  return "ciel dégagé"
    if code <= 2:  return "peu nuageux"
    if code <= 3:  return "couvert"
    if code <= 48: return "brumeux"
    if code <= 57: return "bruine"
    if code <= 67: return "pluvieux"
    if code <= 77: return "neigeux"
    if code <= 82: return "averses"
    return "orageux"

def date_fr(dt: datetime) -> str:
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]
    return f"{jours[dt.weekday()]} {dt.day} {mois[dt.month-1]}"

def num_files(n: int):
    """Renvoie les fichiers statiques qui forment le nombre n (0-120)."""
    n = max(0, min(120, int(n)))
    # Cas simples
    if n <= 60 or n in (70, 80, 90, 100, 110, 120):
        return [STATIC / f"num_{n}.mp3"]
    # 61-120 non prévus : approximation à la dizaine la plus proche
    rounded = round(n / 10) * 10
    return [STATIC / f"num_{rounded}.mp3"]

def wave_files(h: float):
    """Décompose 1.4 → [num_1, virgule, num_4]"""
    h = round(h, 1)
    integer = int(h)
    decimal = round((h - integer) * 10)
    files = [STATIC / f"num_{integer}.mp3"]
    if decimal > 0:
        files += [STATIC / "virgule.mp3", STATIC / f"num_{decimal}.mp3"]
    return files

# ── 1. Fetch Open-Meteo ───────────────────────────────────────────────────────
def fetch_conditions():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
        f"temperature_2m,weather_code"
        f"&hourly=wind_speed_10m"  # juste pour vérifier que ça marche
        f"&daily=wind_speed_10m_max"
        f"&timezone=Europe/Paris"
    )
    marine_url = (
        f"https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wave_height,wave_period"
    )
    w = requests.get(url, timeout=10).json()
    m = requests.get(marine_url, timeout=10).json()

    c = w["current"]
    mc = m.get("current", {})
    return {
        "kt":    to_kt(c["wind_speed_10m"]),
        "gkt":   to_kt(c["wind_gusts_10m"]),
        "dir":   dir_label(c["wind_direction_10m"]),
        "temp":  round(float(c["temperature_2m"])),
        "weather": wmo_label(c["weather_code"]),
        "wave_h": round(float(mc.get("wave_height", 0)), 1),
        "wave_p": round(float(mc.get("wave_period", 0))),
    }

# ── 2. Mistral → phrase descriptive ──────────────────────────────────────────
def generate_description(cond: dict, date_str: str) -> str:
    prompt = (
        f"Tu es un présentateur radio sympa pour un bulletin kite à Berck-sur-Mer. "
        f"Conditions du {date_str} : vent {cond['dir']} {cond['kt']} nœuds, "
        f"rafales {cond['gkt']} nœuds, vagues {cond['wave_h']}m, "
        f"météo {cond['weather']}, température {cond['temp']}°C. "
        f"Écris UNE SEULE phrase courte (max 20 mots), style radio décontracté, "
        f"en français. Pas de ponctuation au début. Pas de guillemets."
    )
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
        json={
            "model": "mistral-small-latest",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80,
            "temperature": 0.8,
        },
        timeout=15,
    )
    text = r.json()["choices"][0]["message"]["content"].strip()
    # Supprimer éventuelles guillemets
    text = text.strip('"\'«»')
    return text

# ── 3. ElevenLabs TTS (seulement le variable) ─────────────────────────────────
def tts(text: str, out_path: Path):
    # Sélectionner la voix (chercher une voix FR)
    voices_r = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": ELEVEN_KEY}
    ).json().get("voices", [])

    voice_id = "pNInz6obpgDQGcFmaJgB"  # Adam (multilingual fallback)
    for v in voices_r:
        meta = json.dumps(v).lower()
        if any(h in meta for h in ["french","france","antoine","hugo","thomas"]):
            voice_id = v["voice_id"]
            break

    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {"stability": 0.6, "similarity_boost": 0.75}
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
    out_path.write_bytes(r.content)
    print(f"  TTS OK : {out_path.name} ({len(r.content)//1024}KB)")

# ── 4. Assemblage ffmpeg ──────────────────────────────────────────────────────
def assemble(speech_parts: list, output: Path):
    """
    speech_parts : liste de Path vers les .mp3 dans l'ordre
    Pipeline :
      1. Concat tous les morceaux de parole → speech_full.mp3
      2. Préfixer avec jingle_intro (3s) → with_intro.mp3
      3. Mixer avec jingle_bg (volume bas) → today.mp3
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Filtrer les fichiers manquants
        valid_parts = [p for p in speech_parts if p.exists()]
        if not valid_parts:
            raise RuntimeError("Aucun fichier audio de parole trouvé !")

        # Concat liste → filelist.txt
        filelist = tmp / "filelist.txt"
        with open(filelist, "w", encoding="utf-8") as f:
            for p in valid_parts:
                f.write(f"file '{p.as_posix()}'\n")

        speech_full = tmp / "speech_full.mp3"
        subprocess.run([
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", str(filelist),
            "-ar", "44100", "-ac", "1",
            "-y", str(speech_full)
        ], check=True, capture_output=True)

        # Durée de la parole
        probe = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(speech_full)
        ], capture_output=True, text=True)
        speech_dur = float(json.loads(probe.stdout)["format"]["duration"])
        total_dur  = speech_dur + 3.0  # 3s d'intro

        # Assemblage final : intro + parole + fond musical
        subprocess.run([
            "ffmpeg",
            "-i", str(JINGLE_IN),    # intro jingle
            "-i", str(speech_full),  # parole
            "-i", str(JINGLE_BG),   # fond musical
            "-filter_complex",
            (
                # Intro : 3 premières secondes du jingle
                f"[0:a]atrim=0:3,asetpts=PTS-STARTPTS[intro];"
                # Parole
                f"[1:a]asetpts=PTS-STARTPTS[speech];"
                # Fond musical : durée totale, volume 0.18
                f"[2:a]atrim=0:{total_dur:.2f},asetpts=PTS-STARTPTS,volume=0.18[bg];"
                # Concat intro + parole
                f"[intro][speech]concat=n=2:v=0:a=1[voice];"
                # Mix voix + fond
                f"[voice][bg]amix=inputs=2:duration=first:normalize=0[out]"
            ),
            "-map", "[out]",
            "-ar", "44100", "-b:a", "128k",
            "-y", str(output)
        ], check=True, capture_output=True)

    print(f"  ✅ Podcast généré : {output} ({output.stat().st_size//1024}KB)")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    paris_tz  = timezone(timedelta(hours=2))  # CEST
    now       = datetime.now(paris_tz)
    date_str  = date_fr(now)

    print(f"\n=== Génération podcast {date_str} ===\n")

    # 1. Conditions
    print("Fetching conditions Open-Meteo...")
    cond = fetch_conditions()
    print(f"  → {cond['kt']}kt {cond['dir']}, vagues {cond['wave_h']}m, {cond['weather']}")

    # 2. Description Mistral
    print("Génération texte Mistral...")
    description = generate_description(cond, date_str)
    print(f"  → \"{description}\"")

    # 3. TTS variable (date + description uniquement)
    tts_dir = BASE / "podcast" / "tts"
    tts_dir.mkdir(exist_ok=True)

    print("TTS ElevenLabs (variable uniquement)...")
    date_audio = tts_dir / "date.mp3"
    desc_audio = tts_dir / "desc.mp3"
    tts(date_str, date_audio)
    tts(description, desc_audio)

    # 4. Assembler la liste des morceaux dans l'ordre
    print("Assemblage ffmpeg...")
    dir_code = cond["dir"]

    speech_parts = [
        STATIC / "intro.mp3",         # "Bonjour, kite report à Berck pour ce"
        date_audio,                    # "vendredi 25 avril"  ← TTS généré
        STATIC / "vent.mp3",           # "Le vent souffle du"
        STATIC / f"dir_{dir_code}.mp3",# "nord-est"
        STATIC / "a_noeuds.mp3",       # "à"
        *num_files(cond["kt"]),        # "15"
        STATIC / "noeuds_rafales.mp3", # "nœuds, rafales"
        *num_files(cond["gkt"]),       # "22"
        STATIC / "noeuds.mp3",         # "nœuds."
        STATIC / "vagues.mp3",         # "Les vagues font"
        *wave_files(cond["wave_h"]),   # "1" "virgule" "4"
        STATIC / "metres.mp3",         # "mètres."
        desc_audio,                    # phrase descriptive ← TTS généré
        STATIC / "outro.mp3",          # "Bonne journée à tous les kiteurs !"
    ]

    assemble(speech_parts, OUT_FILE)

    print(f"\n🎙️ Podcast du jour prêt : {OUT_FILE}")
