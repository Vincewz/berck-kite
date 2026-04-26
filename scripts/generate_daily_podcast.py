"""
generate_daily_podcast.py — Lancé chaque matin par GitHub Actions
Données riches → Mistral contexte maximal → OpenAI TTS onyx → ffmpeg mastering
"""

import os, json, subprocess, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

MISTRAL_KEY = os.environ["MISTRAL_API_KEY"]
OPENAI_KEY  = os.environ["OPENAI_API_KEY"]
BERCK_LAT, BERCK_LON = 50.4, 1.6

BASE          = Path(__file__).parent.parent
JINGLE        = BASE / "podcast" / "jingle_bg.mp3"
OUT_FILE      = BASE / "podcast" / "today.mp3"
TTS_RAW       = BASE / "podcast" / "tts" / "voice_raw.mp3"
HISTORY_FILE  = BASE / "detection_history.json"
TTS_RAW.parent.mkdir(exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def to_kt(kmh): return max(0, round(float(kmh) / 1.852))

def dir_label(deg):
    dirs = ["Nord","Nord-Nord-Est","Nord-Est","Est-Nord-Est","Est","Est-Sud-Est",
            "Sud-Est","Sud-Sud-Est","Sud","Sud-Sud-Ouest","Sud-Ouest","Ouest-Sud-Ouest",
            "Ouest","Ouest-Nord-Ouest","Nord-Ouest","Nord-Nord-Ouest"]
    return dirs[round(float(deg) / 22.5) % 16]

def wmo_label(code):
    code = int(code)
    if code == 0:  return "ensoleillé"
    if code <= 2:  return "partiellement nuageux"
    if code <= 3:  return "couvert"
    if code <= 48: return "brumeux"
    if code <= 57: return "bruine"
    if code <= 67: return "pluvieux"
    if code <= 77: return "neigeux"
    if code <= 82: return "averses"
    return "orageux"

def date_fr(dt):
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]
    return f"{jours[dt.weekday()]} {dt.day} {mois[dt.month-1]}"

def offshore_warning(deg):
    """Vent de terre = onshore = dangereux pour kite"""
    # Berck : côte orientée SO-NE, vent de terre = Est à Sud
    d = float(deg) % 360
    if 80 <= d <= 200:
        return "⚠ vent de terre (offshore) — prudence"
    return None

# ── Modèle harmonique marées (porté depuis app.js) ───────────────────────────
import math as _math

_TIDE_REF_HW_MS = 1745398800000  # 2026-04-23T06:00:00Z en ms
_M2_MS   = 44712000              # période M2 : 12h25min12s
_MSF_MS  = 14.77 * 86400000      # cycle vives/mortes-eaux

def _tide_coeff(t_ms):
    phase = (t_ms - _TIDE_REF_HW_MS) % _MSF_MS
    if phase < 0: phase += _MSF_MS
    return 0.5 + 0.5 * _math.cos(2 * _math.pi * phase / _MSF_MS)

def _tide_height(t_ms):
    coeff = _tide_coeff(t_ms)
    hw    = 6.5 + coeff * 2.5
    lw    = 3.5 - coeff * 2.5
    mid   = (hw + lw) / 2
    rang  = (hw - lw) / 2
    phase = (t_ms - _TIDE_REF_HW_MS) % _M2_MS
    if phase < 0: phase += _M2_MS
    return mid + rang * _math.cos((phase / _M2_MS) * 2 * _math.pi)

def next_tides(from_dt: datetime, count=4):
    """Retourne les prochains PM/BM sous forme de dicts {type, h, timeStr}"""
    step = 5 * 60 * 1000  # 5 min en ms
    from_ms = int(from_dt.timestamp() * 1000)
    extremes = []
    prev = _tide_height(from_ms - step)
    curr = _tide_height(from_ms)
    t = from_ms
    while t < from_ms + 3 * _M2_MS and len(extremes) < count:
        nxt = _tide_height(t + step)
        if curr > prev and curr > nxt:
            dt = datetime.fromtimestamp(t / 1000, tz=from_dt.tzinfo)
            extremes.append({"type": "PM", "h": round(curr, 1), "timeStr": dt.strftime("%H:%M")})
        elif curr < prev and curr < nxt:
            dt = datetime.fromtimestamp(t / 1000, tz=from_dt.tzinfo)
            extremes.append({"type": "BM", "h": round(curr, 1), "timeStr": dt.strftime("%H:%M")})
        prev = curr
        curr = nxt
        t += step
    return extremes

# ── 1. Fetch — données riches Open-Meteo ─────────────────────────────────────
def fetch_all(now: datetime):
    paris_str = now.strftime("%Y-%m-%dT%H:00")
    today_str = now.strftime("%Y-%m-%d")
    tomorrow  = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # Météo + vent horaire sur 48h
    w = requests.get(
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,weather_code,apparent_temperature"
        f"&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,weather_code,precipitation_probability"
        f"&daily=wind_speed_10m_max,wind_gusts_10m_max,weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset"
        f"&timezone=Europe/Paris&forecast_days=3", timeout=10
    ).json()

    # Marine horaire
    m = requests.get(
        f"https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={BERCK_LAT}&longitude={BERCK_LON}"
        f"&current=wave_height,wave_period,wave_direction,sea_surface_temperature"
        f"&hourly=wave_height,wave_period"
        f"&timezone=Europe/Paris&forecast_days=3", timeout=10
    ).json()

    c  = w["current"]
    mc = m.get("current", {})
    hr = w["hourly"]
    mr = m.get("hourly", {})
    dl = w["daily"]

    # Index heure courante dans le horaire
    try:
        cur_idx = hr["time"].index(paris_str)
    except ValueError:
        cur_idx = 6  # fallback 6h matin

    # Toutes les heures d'aujourd'hui (jusqu'à 22h max)
    end_idx = min(cur_idx + 18, len(hr["time"]))
    hours_today = []
    for i in range(cur_idx, end_idx):
        h = hr["time"][i][11:16]
        wv_h = float(mr["wave_height"][i]) if mr.get("wave_height") and i < len(mr["wave_height"]) else 0
        hours_today.append({
            "h": h,
            "kt": to_kt(hr["wind_speed_10m"][i]),
            "gkt": to_kt(hr["wind_gusts_10m"][i]),
            "dir": dir_label(hr["wind_direction_10m"][i]),
            "deg": round(float(hr["wind_direction_10m"][i])),
            "temp": round(float(hr["temperature_2m"][i])),
            "weather": wmo_label(hr["weather_code"][i]),
            "rain_pct": round(float(hr["precipitation_probability"][i])),
            "wave_h": round(wv_h, 1),
        })

    # Fenêtre kite 10h–18h (conditions de la journée)
    kite_window = [h for h in hours_today if "10:00" <= h["h"] <= "18:00"]
    peak = max(kite_window, key=lambda x: x["kt"]) if kite_window else (
           max(hours_today, key=lambda x: x["kt"]) if hours_today else None)

    # Demain résumé
    tomorrow_idx = 1  # daily[1] = demain
    # Direction dominante demain : moyenne des heures 24-36
    tom_dirs = [float(hr["wind_direction_10m"][i]) for i in range(24, min(36, len(hr["wind_direction_10m"])))]
    tom_dir_avg = sum(tom_dirs) / len(tom_dirs) if tom_dirs else 0
    tomorrow_data = {
        "weather": wmo_label(dl["weather_code"][tomorrow_idx]),
        "kt_max":  to_kt(dl["wind_speed_10m_max"][tomorrow_idx]),
        "gkt_max": to_kt(dl["wind_gusts_10m_max"][tomorrow_idx]),
        "temp_max": round(float(dl["temperature_2m_max"][tomorrow_idx])),
        "temp_min": round(float(dl["temperature_2m_min"][tomorrow_idx])),
        "dir":      dir_label(tom_dir_avg),
    }

    # Lever/coucher soleil
    sunrise = dl["sunrise"][0][11:16] if dl.get("sunrise") else "06:30"
    sunset  = dl["sunset"][0][11:16]  if dl.get("sunset")  else "21:00"

    tides = next_tides(now, count=4)

    return {
        "now": {   # snapshot 6h — utile pour mer/soleil/température eau
            "kt":      to_kt(c["wind_speed_10m"]),
            "gkt":     to_kt(c["wind_gusts_10m"]),
            "dir":     dir_label(c["wind_direction_10m"]),
            "deg":     round(float(c["wind_direction_10m"])),
            "temp":    round(float(c["temperature_2m"])),
            "feels":   round(float(c.get("apparent_temperature", c["temperature_2m"]))),
            "weather": wmo_label(c["weather_code"]),
            "wave_h":  round(float(mc.get("wave_height", 0)), 1),
            "wave_p":  round(float(mc.get("wave_period", 0))),
            "wave_dir":dir_label(mc.get("wave_direction", 0)),
            "sea_temp":round(float(mc["sea_surface_temperature"])) if mc.get("sea_surface_temperature") is not None else None,
            "offshore":offshore_warning(c["wind_direction_10m"]),
        },
        "kite_window": kite_window,   # heures 10h–18h — conditions de la journée
        "hours": hours_today[:10],
        "peak": peak,
        "tomorrow": tomorrow_data,
        "sunrise": sunrise,
        "sunset": sunset,
        "tides": tides,
    }

# ── 2b. Historique détections kite ───────────────────────────────────────────
MIN_CONF_PODCAST = 0.5  # confiance minimale pour mentionner dans le podcast

def load_kite_yesterday(today: datetime) -> dict | None:
    """Retourne la dernière détection confiante du jour précédent, ou None."""
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except Exception:
        return None
    yesterday = (today - timedelta(days=1)).date()
    for entry in reversed(history):
        ts = datetime.fromisoformat(entry["timestamp"]).date()
        if ts == yesterday and entry.get("max_conf", 0) >= MIN_CONF_PODCAST:
            return entry
    return None

# ── 2. Mistral — contexte maximal, script naturel ────────────────────────────
SYSTEM_PROMPT = """Tu es la voix du bulletin météo kite de Berck-sur-Mer, diffusé chaque matin sur une web radio locale.
Ton rôle : énoncer les conditions météo et kite de la journée de façon claire, précise et agréable à écouter.

Règles absolues :
- Texte brut uniquement : zéro titre, zéro gras, zéro tiret, zéro astérisque, zéro liste
- Commence directement par la première phrase parlée
- Durée cible : 55 à 70 secondes à l'oral (130 à 160 mots)
- Langue : français courant, naturel, sans jargon technique excessif
- Ton : celui d'un présentateur météo radio — factuel, neutre, posé

CONTENU — uniquement des faits :
- Commencer par la date du jour
- Conditions prévues pour la journée (fenêtre 10h–18h) : vent (force ET orientation cardinale), rafales, température, météo
- Vagues et température de l'eau (données marines)
- Prochaine pleine mer et prochaine basse mer (heure + hauteur)
- Évolution heure par heure si notable (accélération, changement de direction)
- Si des kites ont été observés hier : dire simplement "quelques kitesurfs à l'eau hier", sans mentionner de système, de caméra ou de détection automatique. Si aucun kite n'a été observé : ne rien dire sur les kites, ne pas signaler l'absence.
- Aperçu de demain : météo, température, vent avec son orientation cardinale
- Si vent de terre (offshore) : le mentionner factuellement, sans dramatiser
- Terminer obligatoirement par : "Bonne journée les Berckois."

INTERDIT — ne jamais inclure :
- Recommandations ("prévoyez", "pensez à", "vérifiez votre matériel")
- Conseils de sécurité ou de pratique ("kitez en sécurité", "idéal pour les débutants")
- Jugements de valeur sur les conditions ("parfait pour", "agréable pour")
- Toute phrase qui dit à l'auditeur quoi faire ou ressentir"""

def generate_script(data: dict, date_str: str, kite_yesterday: dict | None = None) -> str:
    n = data["now"]
    p = data["peak"]
    t = data["tomorrow"]
    kw = data.get("kite_window", [])

    # Résumé fenêtre kite 10h–18h heure par heure
    kite_slots = " | ".join(
        f"{h['h']} : {h['kt']}kt {h['dir']}, {h['weather']}, {h['temp']}°C"
        for h in kw
    ) if kw else "données indisponibles"

    # Marées
    tides = data.get("tides", [])
    next_pm = next((td for td in tides if td["type"] == "PM"), None)
    next_bm = next((td for td in tides if td["type"] == "BM"), None)
    tides_str = ""
    if next_pm: tides_str += f"  Pleine mer : {next_pm['timeStr']} — {next_pm['h']}m\n"
    if next_bm: tides_str += f"  Basse mer  : {next_bm['timeStr']} — {next_bm['h']}m\n"

    offshore_note = f"\n⚠ ALERTE OFFSHORE : {n['offshore']}" if n['offshore'] else ""

    # Kites détectés hier (mentionner seulement si détection confiante)
    kite_note = ""
    if kite_yesterday:
        kite_note = "\nKITES HIER : quelques kitesurfs à l'eau hier.\n"

    user_msg = f"""Bulletin du {date_str} — généré à 6h00

DONNÉES MARINES (actuelles) :
  Mer : vagues {n['wave_h']}m, période {n['wave_p']}s, houle de {n['wave_dir']}{f", température eau {n['sea_temp']}°C" if n.get('sea_temp') is not None else ""}
  Lever soleil : {data['sunrise']} — Coucher : {data['sunset']}{offshore_note}

MARÉES (prochains PM/BM) :
{tides_str}
PRÉVISIONS JOURNÉE (10h–18h) :
  {kite_slots}

  Pic de vent : {p['h'] if p else '?'} — {p['kt'] if p else '?'} nœuds {p['dir'] if p else ''}, rafales {p['gkt'] if p else '?'} nœuds
{kite_note}
DEMAIN :
  {t['weather'].capitalize()}, {t['temp_min']}–{t['temp_max']}°C
  Vent : {t['dir']} — max {t['kt_max']} nœuds, rafales {t['gkt_max']} nœuds

Rédige le bulletin maintenant :"""

    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
        json={
            "model": "mistral-small-latest",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "max_tokens": 350,
            "temperature": 0.75,
        },
        timeout=25,
    )
    text = r.json()["choices"][0]["message"]["content"].strip()
    # Nettoyer les éventuels artefacts markdown
    import re
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'^#+\s.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

# ── 3. OpenAI TTS ─────────────────────────────────────────────────────────────
def text_to_speech(script: str, out_path: Path) -> str:
    # Essai 1 : gpt-4o-mini-tts avec instructions accent français
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini-tts",
            "voice": "onyx",
            "input": script,
            "instructions": "Tu parles français avec un accent français natif, parfaitement naturel. Prononce tous les mots en français. Débit posé, ton neutre de présentateur radio.",
        },
        timeout=60,
    )
    if r.status_code == 200:
        out_path.write_bytes(r.content)
        print(f"  gpt-4o-mini-tts onyx OK — {len(r.content)//1024}KB")
        return "onyx"
    # Fallback : tts-1-hd
    print(f"  gpt-4o-mini-tts echec {r.status_code}, fallback tts-1-hd")
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={"model": "tts-1-hd", "voice": "onyx", "input": script},
        timeout=60,
    )
    if r.status_code == 200:
        out_path.write_bytes(r.content)
        print(f"  tts-1-hd onyx OK — {len(r.content)//1024}KB")
        return "onyx"
    raise RuntimeError(f"OpenAI TTS echec {r.status_code}: {r.text[:100]}")

# ── 4. ffmpeg — mastering radio + mix musique ────────────────────────────────
def mix_audio(voice_path: Path, music_path: Path, output: Path):
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(voice_path)],
        capture_output=True, text=True
    )
    voice_dur = float(json.loads(probe.stdout)["format"]["duration"])
    INTRO = 3.0
    total = voice_dur + INTRO + 1.0

    print(f"  Voix {voice_dur:.1f}s → podcast {total:.1f}s")

    subprocess.run([
        "ffmpeg",
        "-i", str(voice_path),
        "-i", str(music_path),
        "-filter_complex", (
            # ── Voix : mastering radio pro ──
            "[0:a]"
            "highpass=f=90,"                          # coupe les basses
            "lowpass=f=14000,"                        # coupe le hiss
            "compand=attacks=0.01:decays=0.3:"        # compression légère
              "points=-70/-70|-20/-15|0/-10:gain=3,"
            "loudnorm=I=-16:LRA=7:TP=-1.5,"           # normalisation EBU R128 podcast
            f"adelay={int(INTRO*1000)}|{int(INTRO*1000)},"
            "asetpts=PTS-STARTPTS[voice];"
            # ── Musique : fond discret ──
            "[1:a]"
            f"aloop=loop=-1:size=2e+09,atrim=0:{total:.1f},"
            "afade=t=in:st=0:d=2,"
            f"afade=t=out:st={total-2:.1f}:d=2,"
            "volume=0.12,asetpts=PTS-STARTPTS[music];"
            # ── Mix final ──
            "[voice][music]amix=inputs=2:duration=first:normalize=0[out]"
        ),
        "-map", "[out]",
        "-ar", "44100", "-b:a", "128k",
        "-y", str(output)
    ], check=True, capture_output=True)

    print(f"  Podcast : {output.stat().st_size//1024}KB")

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    paris_tz = timezone(timedelta(hours=2))
    now      = datetime.now(paris_tz)
    date_str = date_fr(now)

    print(f"\n=== Kite Podcast — {date_str} ===\n")

    print("Fetch données...")
    data = fetch_all(now)
    n = data["now"]
    print(f"  Maintenant : {n['kt']}kt {n['dir']}, rafales {n['gkt']}kt, "
          f"vagues {n['wave_h']}m, {n['weather']}, {n['temp']}°C")
    if data["peak"]:
        print(f"  Pic : {data['peak']['h']} → {data['peak']['kt']}kt")

    kite_yesterday = load_kite_yesterday(now)
    if kite_yesterday:
        print(f"  Kites hier : {kite_yesterday['kites_detected']} détecté(s) — conf {kite_yesterday['max_conf']:.0%}")
    else:
        print("  Pas de détection kite confiante hier")

    print("\nScript Mistral...")
    script = generate_script(data, date_str, kite_yesterday)
    print(f"\n--- SCRIPT ({len(script.split())} mots) ---\n{script}\n---\n")
    (BASE / "podcast" / "tts" / "script.txt").write_text(script, encoding="utf-8")

    print("TTS OpenAI...")
    voice = text_to_speech(script, TTS_RAW)

    print("\nMix ffmpeg + mastering...")
    mix_audio(TTS_RAW, JINGLE, OUT_FILE)

    print(f"\nPodcast prêt — voix {voice}, {len(script.split())} mots")
