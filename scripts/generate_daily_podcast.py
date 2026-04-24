"""
generate_daily_podcast.py — Lancé chaque matin par GitHub Actions
1. Fetch conditions Open-Meteo
2. Mistral génère le SCRIPT COMPLET librement (pas de template rigide)
3. ElevenLabs TTS — script entier en UN seul appel, voix FR naturelle
4. ffmpeg mix avec musique de fond libre de droit
5. Écrit podcast/today.mp3
"""

import os, json, subprocess, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
MISTRAL_KEY  = os.environ["MISTRAL_API_KEY"]
ELEVEN_KEY   = os.environ["ELEVENLABS_API_KEY"]
BERCK_LAT, BERCK_LON = 50.4, 1.6

BASE     = Path(__file__).parent.parent
JINGLE   = BASE / "podcast" / "jingle_bg.mp3"
OUT_FILE = BASE / "podcast" / "today.mp3"
TTS_RAW  = BASE / "podcast" / "tts" / "voice_raw.mp3"
TTS_RAW.parent.mkdir(exist_ok=True)

# ── Voix ElevenLabs — meilleures options pour le français ─────────────────────
# Classées par naturalité en français (eleven_multilingual_v2)
FRENCH_VOICES = [
    ("XB0fDUnXU5powFXDhCwa", "Charlotte"),   # Voix féminine chaleureuse, excellent FR
    ("N2lVS1w4EtoT3dr4eOWO", "Callum"),      # Voix masculine naturelle, bon FR
    ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),       # Voix féminine douce, bon FR
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam"),        # Voix masculine décontractée
    ("pNInz6obpgDQGcFmaJgB", "Adam"),        # Fallback
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def to_kt(kmh): return max(0, round(float(kmh) / 1.852))

def dir_label(deg):
    dirs = ["Nord","Nord-Nord-Est","Nord-Est","Est-Nord-Est","Est","Est-Sud-Est",
            "Sud-Est","Sud-Sud-Est","Sud","Sud-Sud-Ouest","Sud-Ouest","Ouest-Sud-Ouest",
            "Ouest","Ouest-Nord-Ouest","Nord-Ouest","Nord-Nord-Ouest"]
    return dirs[round(float(deg) / 22.5) % 16]

def wmo_label(code):
    code = int(code)
    if code == 0:  return "ciel dégagé, grand soleil"
    if code <= 2:  return "peu nuageux"
    if code <= 3:  return "couvert"
    if code <= 48: return "brumeux ou brouillard"
    if code <= 57: return "bruine légère"
    if code <= 67: return "pluie"
    if code <= 77: return "neige"
    if code <= 82: return "averses"
    return "orage"

def date_fr(dt):
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]
    return f"{jours[dt.weekday()]} {dt.day} {mois[dt.month-1]}"

# ── 1. Conditions Open-Meteo ──────────────────────────────────────────────────
def fetch_conditions():
    w = requests.get(
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,weather_code"
        f"&timezone=Europe/Paris", timeout=10
    ).json()
    m = requests.get(
        f"https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wave_height,wave_period", timeout=10
    ).json()
    c  = w["current"]
    mc = m.get("current", {})
    return {
        "kt":      to_kt(c["wind_speed_10m"]),
        "gkt":     to_kt(c["wind_gusts_10m"]),
        "dir":     dir_label(c["wind_direction_10m"]),
        "deg":     round(float(c["wind_direction_10m"])),
        "temp":    round(float(c["temperature_2m"])),
        "weather": wmo_label(c["weather_code"]),
        "wave_h":  round(float(mc.get("wave_height", 0)), 1),
        "wave_p":  round(float(mc.get("wave_period", 0))),
    }

# ── 2. Mistral — script radio sobre et pro ───────────────────────────────────
def generate_script(cond, date_str):
    prompt = f"""Tu rédiges le bulletin kite quotidien de Berck-sur-Mer pour une radio locale.
Style : journaliste météo professionnel — informatif, fluide, chaleureux sans être familier.
Pas d'humour forcé, pas d'exclamations excessives. Comme un vrai présentateur radio.

Conditions du {date_str} :
Vent {cond['dir']}, {cond['kt']} nœuds, rafales {cond['gkt']} nœuds.
Vagues {cond['wave_h']}m ({cond['wave_p']}s). {cond['weather'].capitalize()}, {cond['temp']}°C.

Rédige un bulletin oral de 45 à 55 secondes (environ 110 mots).
Commence par situer le contexte du jour (météo, ambiance), puis décris les conditions.
Conclus simplement sur ce que ça implique pour la plage.
Texte brut uniquement, sans ponctuation de scène ni tirets."""

    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
        json={
            "model": "mistral-small-latest",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 250,
            "temperature": 0.9,
        },
        timeout=20,
    )
    text = r.json()["choices"][0]["message"]["content"].strip()
    return text

# ── 3. ElevenLabs TTS — script entier, meilleure voix FR ─────────────────────
def text_to_speech(script: str, out_path: Path):
    for voice_id, voice_name in FRENCH_VOICES:
        print(f"  Tentative voix : {voice_name} ({voice_id})")
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={
                "text": script,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.45,          # Moins stable = plus expressif
                    "similarity_boost": 0.80,
                    "style": 0.35,              # Style speaking (radio feel)
                    "use_speaker_boost": True,
                }
            },
            timeout=60,
        )
        if r.status_code == 200:
            out_path.write_bytes(r.content)
            print(f"  Voix {voice_name} OK — {len(r.content)//1024}KB")
            return voice_name
        else:
            print(f"  {voice_name} echec {r.status_code}: {r.text[:80]}")
    raise RuntimeError("Aucune voix ElevenLabs disponible !")

# ── 4. ffmpeg — mix voix + musique de fond ───────────────────────────────────
def mix_audio(voice_path: Path, music_path: Path, output: Path):
    import json as _json

    # Durée de la voix
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(voice_path)],
        capture_output=True, text=True
    )
    voice_dur = float(_json.loads(probe.stdout)["format"]["duration"])
    total_dur = voice_dur + 1.0  # 1s de marge après la voix

    print(f"  Durée voix : {voice_dur:.1f}s — Podcast total : {total_dur:.1f}s")

    INTRO_DUR = 3.0   # secondes de musique seule avant la voix

    subprocess.run([
        "ffmpeg",
        "-i", str(voice_path),
        "-i", str(music_path),
        "-filter_complex",
        (
            # Voix : décaler de 3s pour laisser l'intro musicale
            f"[0:a]adelay={int(INTRO_DUR*1000)}|{int(INTRO_DUR*1000)},asetpts=PTS-STARTPTS[voice];"
            # Musique : volume bas pendant la voix, plein pendant l'intro
            # Durée totale = intro + voix + 1s
            f"[1:a]aloop=loop=-1:size=2e+09,"
            f"atrim=0:{total_dur + INTRO_DUR + 1:.1f},"
            f"afade=t=in:st=0:d=1.5,"                         # fade-in au début
            f"afade=t=out:st={total_dur + INTRO_DUR - 1.5:.1f}:d=2,"  # fade-out à la fin
            f"volume=0.14,asetpts=PTS-STARTPTS[music];"
            # Mix : voix + musique de fond
            f"[voice][music]amix=inputs=2:duration=first:normalize=0[out]"
        ),
        "-map", "[out]",
        "-ar", "44100", "-b:a", "128k",
        "-y", str(output)
    ], check=True, capture_output=True)

    print(f"  Podcast final : {output.stat().st_size//1024}KB")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    paris_tz = timezone(timedelta(hours=2))
    now      = datetime.now(paris_tz)
    date_str = date_fr(now)

    print(f"\n=== Kite Podcast — {date_str} ===\n")

    # 1. Conditions
    print("Conditions Open-Meteo...")
    cond = fetch_conditions()
    print(f"  {cond['kt']}kt {cond['dir']}, rafales {cond['gkt']}kt, vagues {cond['wave_h']}m, {cond['weather']}, {cond['temp']}C")

    # 2. Script Mistral
    print("\nScript Mistral...")
    script = generate_script(cond, date_str)
    print(f"\n--- SCRIPT ---\n{script}\n--- FIN ---\n")
    # Sauvegarder le script pour debug
    (BASE / "podcast" / "tts" / "script.txt").write_text(script, encoding="utf-8")

    # 3. TTS
    print("TTS ElevenLabs...")
    voice_used = text_to_speech(script, TTS_RAW)

    # 4. Mix
    print("\nMix audio ffmpeg...")
    mix_audio(TTS_RAW, JINGLE, OUT_FILE)

    print(f"\n Podcast pret : {OUT_FILE}")
    print(f"   Voix : {voice_used}")
    print(f"   Script : {len(script.split())} mots")
