# KiteBerck

Tableau de bord météo kite en temps réel pour Berck-sur-Mer, avec détection automatique de kites par vision par ordinateur et bulletin audio quotidien.

**[berck.info](http://berck.info)**

---

## Ce que ça fait

**Météo en direct** — vent (force, direction, rafales), température, vagues, marées, webcams live. Données Open-Meteo + marine API, rafraîchies toutes les 10 min.

**Détection kites par IA** — un modèle YOLOv11 surveille la webcam Éole 3 fois par jour (10h30, 14h30, 17h30). Il ne tourne que si les conditions sont réunies : vent ≥ 15 kt, pas de composante Est, température ≥ 3°C. Le résultat s'affiche en badge sur le site et la dernière détection est visible avec les boîtes de détection.

**Podcast quotidien** — bulletin radio planifié à 08h15 UTC (09h15 hiver / 10h15 été), avec retards possibles de GitHub Actions : prévisions du jour (10h–18h), marées estimées, vagues, aperçu du lendemain. Script Gemini + voix Gemini TTS + mastering ffmpeg. En cas de clé absente, crédits épuisés ou panne Gemini : texte factuel construit depuis les mêmes données et voix française locale eSpeak NG (moins naturelle), sans API payante. Si des kites ont été détectés la veille, le bulletin le mentionne.

---

## Stack

| Composant | Techno |
|---|---|
| Frontend | Vue 3 (CDN), vanilla CSS |
| Déploiement | Vercel |
| Météo | [Open-Meteo](https://open-meteo.com) |
| Webcams | [Skaping](https://www.skaping.com/berck-sur-mer) |
| Modèle IA | YOLOv11n (ultralytics) — entraîné sur images Éole annotées |
| Annotation | Label Studio |
| Podcast | Gemini + Gemini TTS, secours local eSpeak NG + ffmpeg |
| Automatisation | GitHub Actions (collecte, détection, prévisions, podcast, maintenance) |

---

## Workflows GitHub Actions

```
collect_images.yml   →  21h00  — collecte les images du jour pour le dataset
infer_kite.yml       →  10h30 / 14h30 / 17h30  — détection kites (si conditions OK)
podcast.yml          →  06h00  — génère le bulletin audio du jour
```

Le workflow de détection est en deux jobs : `check` (conditions météo) → `detect` (YOLO) uniquement si conditions réunies. Le cache pip évite de réinstaller torch à chaque run. Après échec répété de l'API best-match, les prévisions et contrôles météo essaient le point d'accès Open-Meteo Météo-France (modèle différent, mêmes unités). Si les deux échouent, l'Action échoue explicitement : ce n'est pas une observation négative de kites.

---

## Modèle kite

- **Architecture** : YOLOv11n (2.6M paramètres, CPU)
- **Dataset** : ~100 images annotées sur Label Studio (webcam Éole, résolution large)
- **Métriques** : Precision 0.91 — Recall 0.33 — mAP@50 0.50
- **Parti pris** : précision > recall — pas de fausse alarme

Le dataset grossit automatiquement chaque jour via `collect_images.py` (filtre vent/direction/pluie/festival). Objectif : 1 000 images pour réentraîner.

---

## Fichiers de données

| Fichier | Contenu |
|---|---|
| `kite_status.json` | Résultat de la dernière détection + `last_kite` (persistant) |
| `detection_history.json` | Historique de toutes les détections positives |
| `podcast/today.mp3` | Bulletin du jour |
| `podcast/tts/script.txt` | Script texte du bulletin |

---

## Dev local

```bash
# Servir le site
npx serve .

# Générer un podcast (ffmpeg et espeak-ng requis)
GEMINI_API_KEY=... python scripts/generate_daily_podcast.py

# Mode sans API payante (aussi variable GitHub Actions PODCAST_OFFLINE=1)
PODCAST_OFFLINE=1 python scripts/generate_daily_podcast.py

# Tester le secours sans réseau ni clé
python -m unittest discover -s scripts -p 'test_podcast_fallback.py' -v

# Lancer une détection manuellement
python kite-detector/scripts/infer_kite.py
```
