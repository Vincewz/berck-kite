"""
build_static_audio.py — À lancer UNE SEULE FOIS
Génère tous les morceaux audio fixes via ElevenLabs et les stocke dans podcast/static/
Coût : ~650 chars ElevenLabs (déduction du free tier une fois)
"""

import os, json, time, requests
from pathlib import Path

API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "sk_25898bd3b1f6155c71d424ec263fab609b85a29f3b5b5366")
MODEL_ID = "eleven_multilingual_v2"
STATIC   = Path(__file__).parent.parent / "podcast" / "static"
STATIC.mkdir(parents=True, exist_ok=True)

# ── Choisir une voix française ───────────────────────────────────────────────
def pick_voice():
    r = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": API_KEY}
    )
    voices = r.json().get("voices", [])
    # Préférer une voix avec label "fr" ou des noms connus FR
    fr_hints = ["french", "france", "fr ", "Antoine", "Thomas", "Hugo", "Serena"]
    for v in voices:
        meta = json.dumps(v).lower()
        if any(h.lower() in meta for h in fr_hints):
            print(f"Voix FR trouvée : {v['name']} ({v['voice_id']})")
            return v["voice_id"]
    # Fallback : première voix multilingue
    for v in voices:
        if "multilingual" in json.dumps(v.get("labels", {})).lower():
            print(f"Voix multilingue : {v['name']} ({v['voice_id']})")
            return v["voice_id"]
    # Dernier recours : Adam (polyglotte)
    return "pNInz6obpgDQGcFmaJgB"

VOICE_ID = pick_voice()

# ── Synthèse TTS ─────────────────────────────────────────────────────────────
def tts(text: str, filename: str, pause_after: float = 0.5):
    out = STATIC / filename
    if out.exists():
        print(f"  skip (déjà créé) : {filename}")
        return
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}
        }
    )
    if r.status_code != 200:
        print(f"  ERREUR {r.status_code} pour '{text}': {r.text[:200]}")
        return
    out.write_bytes(r.content)
    print(f"  ✓ {filename}  ({len(r.content)//1024}KB)")
    time.sleep(pause_after)  # Éviter le rate limit

# ── Phrases fixes ─────────────────────────────────────────────────────────────
PHRASES = {
    "intro.mp3":         "Bonjour, kite report à Berck-sur-Mer pour ce",
    "vent.mp3":          "Le vent souffle du",
    "a_noeuds.mp3":      "à",
    "noeuds_rafales.mp3":"nœuds, rafales",
    "noeuds.mp3":        "nœuds.",
    "vagues.mp3":        "Les vagues font",
    "virgule.mp3":       "virgule",
    "metres.mp3":        "mètres.",
    "maree.mp3":         "Coefficient de marée :",
    "outro.mp3":         "Bonne journée à tous les kiteurs de Berck !",
}

# ── Chiffres 0–60 ─────────────────────────────────────────────────────────────
NOMBRES = {
    "0":"zéro","1":"un","2":"deux","3":"trois","4":"quatre","5":"cinq",
    "6":"six","7":"sept","8":"huit","9":"neuf","10":"dix",
    "11":"onze","12":"douze","13":"treize","14":"quatorze","15":"quinze",
    "16":"seize","17":"dix-sept","18":"dix-huit","19":"dix-neuf","20":"vingt",
    "21":"vingt et un","22":"vingt-deux","23":"vingt-trois","24":"vingt-quatre",
    "25":"vingt-cinq","26":"vingt-six","27":"vingt-sept","28":"vingt-huit",
    "29":"vingt-neuf","30":"trente","31":"trente et un","32":"trente-deux",
    "33":"trente-trois","34":"trente-quatre","35":"trente-cinq","36":"trente-six",
    "37":"trente-sept","38":"trente-huit","39":"trente-neuf","40":"quarante",
    "41":"quarante et un","42":"quarante-deux","43":"quarante-trois",
    "44":"quarante-quatre","45":"quarante-cinq","46":"quarante-six",
    "47":"quarante-sept","48":"quarante-huit","49":"quarante-neuf",
    "50":"cinquante","51":"cinquante et un","52":"cinquante-deux",
    "53":"cinquante-trois","54":"cinquante-quatre","55":"cinquante-cinq",
    "56":"cinquante-six","57":"cinquante-sept","58":"cinquante-huit",
    "59":"cinquante-neuf","60":"soixante",
    # Dizaines pour coefficients maréaux (60–120)
    "70":"soixante-dix","80":"quatre-vingts","90":"quatre-vingt-dix",
    "100":"cent","110":"cent dix","120":"cent vingt",
}

# ── Directions 16 points ──────────────────────────────────────────────────────
DIRECTIONS = {
    "N":"nord","NNE":"nord-nord-est","NE":"nord-est","ENE":"est-nord-est",
    "E":"est","ESE":"est-sud-est","SE":"sud-est","SSE":"sud-sud-est",
    "S":"sud","SSO":"sud-sud-ouest","SO":"sud-ouest","OSO":"ouest-sud-ouest",
    "O":"ouest","ONO":"ouest-nord-ouest","NO":"nord-ouest","NNO":"nord-nord-ouest",
}

# ── Génération ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n=== Génération audio statique → {STATIC} ===\n")

    print("── Phrases fixes ──")
    for fname, text in PHRASES.items():
        tts(text, fname)

    print("\n── Nombres ──")
    for num, word in NOMBRES.items():
        tts(word, f"num_{num}.mp3", pause_after=0.3)

    print("\n── Directions ──")
    for code, label in DIRECTIONS.items():
        tts(label, f"dir_{code}.mp3", pause_after=0.3)

    print("\n✅ Génération statique terminée !")
    files = list(STATIC.glob("*.mp3"))
    total_kb = sum(f.stat().st_size for f in files) // 1024
    print(f"   {len(files)} fichiers, {total_kb} KB total")
