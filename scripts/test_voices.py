"""
Test rapide de toutes les voix ElevenLabs avec une phrase française
pour trouver la plus naturelle
"""
import os, requests, time
from pathlib import Path

API_KEY = os.environ.get("ELEVENLABS_API_KEY", "sk_25898bd3b1f6155c71d424ec263fab609b85a29f3b5b5366")
OUT_DIR = Path(__file__).parent.parent / "podcast" / "voice_tests"
OUT_DIR.mkdir(exist_ok=True)

# Phrase de test typique d'un bulletin kite
TEST_TEXT = (
    "Bonjour à tous depuis Berck-sur-Mer. Ce vendredi matin, le vent souffle du nord-est "
    "à une dizaine de nœuds avec des rafales à seize. Les vagues sont faibles, "
    "une trentaine de centimètres. Ciel partiellement nuageux, treize degrés. "
    "Bonne journée sur la plage."
)

# Voix à tester — toutes censées fonctionner en multilingual
VOICES = [
    ("N2lVS1w4EtoT3dr4eOWO", "Callum"),       # actuel
    ("CwhRBWXHgEKkTim9LvJX", "Roger"),         # professionnel, mature
    ("onwK4e9ZLuTAKqWW03F9", "Daniel"),        # deep, autoritaire
    ("IKne3meq5aSn9XLyUdCD", "Charlie"),       # conversationnel
    ("SAz9YHcvj6GT2YYXdXww", "River"),         # calme, neutre
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam"),          # décontracté
    ("pqHfZKP75CvOlQylNhV4", "Bill"),          # grave
    ("nPczCjzI2devNBz1zQrb", "Brian"),         # américain mature
    ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),         # féminine douce
    ("9BWtsMINqrJLrRacOk9x", "Aria"),          # féminine naturelle
]

print(f"Test de {len(VOICES)} voix avec phrase FR...\n")
results = []

for vid, vname in VOICES:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
        json={
            "text": TEST_TEXT,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.2,
                "use_speaker_boost": True
            }
        },
        timeout=30
    )
    if r.status_code == 200:
        out = OUT_DIR / f"{vname}.mp3"
        out.write_bytes(r.content)
        print(f"  OK  {vname:<12} → {out.name}")
        results.append(vname)
    else:
        print(f"  --  {vname:<12} → {r.status_code}: {r.text[:60]}")
    time.sleep(0.3)

print(f"\n{len(results)}/{len(VOICES)} voix générées dans {OUT_DIR}")
print("Écoute les fichiers et dis-moi laquelle est la meilleure !")
