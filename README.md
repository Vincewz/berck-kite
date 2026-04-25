# KiteBerck

Tableau de bord météo kite en temps réel pour Berck-sur-Mer, avec détection automatique de kites par vision par ordinateur et bulletin audio quotidien.

**[kiteberck.fr](https://kiteberck.fr)**

---

## Ce que ça fait

**Météo en direct** — vent (force, direction, rafales), température, vagues, marées, webcams live. Données Open-Meteo + marine API, rafraîchies toutes les 10 min.

**Détection kites par IA** — un modèle YOLOv11 surveille la webcam Éole 3 fois par jour (10h30, 14h30, 17h30). Il ne tourne que si les conditions sont réunies : vent ≥ 15 kt, pas de composante Est, température ≥ 3°C. Le résultat s'affiche en badge sur le site et la dernière détection est visible avec les boîtes de détection.

**Podcast quotidien** — bulletin radio généré chaque matin à 6h : prévisions de la journée (fenêtre 10h–18h), marées, vagues, aperçu du lendemain. Script Mistral + voix OpenAI TTS + mastering ffmpeg. Si des kites ont été détectés la veille, le bulletin le mentionne.

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
| Podcast | Mistral API + OpenAI TTS + ffmpeg |
| Automatisation | GitHub Actions (3 workflows) |

---

## Workflows GitHub Actions

```
collect_images.yml   →  21h00  — collecte les images du jour pour le dataset
infer_kite.yml       →  10h30 / 14h30 / 17h30  — détection kites (si conditions OK)
podcast.yml          →  06h00  — génère le bulletin audio du jour
```

Le workflow de détection est en deux jobs : `check` (conditions météo, ~1s) → `detect` (YOLO, ~3 min) uniquement si conditions réunies. Le cache pip évite de réinstaller torch à chaque run.

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

# Générer un podcast manuellement
MISTRAL_API_KEY=... OPENAI_API_KEY=... python scripts/generate_daily_podcast.py

# Lancer une détection manuellement
python kite-detector/scripts/infer_kite.py
```
