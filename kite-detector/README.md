# Kite Detector — YOLOv11

## Pipeline complet

### 1. Collecter les images
```bash
python scripts/collect_images.py
```
Télécharge les images Éole (large) des 30 derniers jours
pour les créneaux 10h–18h, ≥10kt, pas de pluie.
→ `dataset/raw/`

### 2. Annoter avec Label Studio
```bash
start_labelstudio.bat     # Windows
# puis ouvrir http://localhost:8080
```

**Setup du projet Label Studio :**
1. Créer un nouveau projet → "Object Detection with Bounding Boxes"
2. Remplacer le config XML par le contenu de `labelstudio_config.xml`
3. Importer les images depuis `dataset/raw/`
4. Annoter : dessiner des bbox autour de chaque kitesurf visible
5. Exporter → JSON → sauvegarder sous `project-export.json`

### 3. Convertir en dataset YOLO
```bash
python scripts/labelstudio_to_yolo.py --export project-export.json
```
→ `dataset/images/train|val/` + `dataset/labels/train|val/` + `dataset/data.yaml`

### 4. Entraîner
```bash
# Modèle nano (rapide, bon point de départ)
python scripts/train.py --model yolo11n.pt --epochs 100

# Modèle small (meilleure précision si assez de VRAM)
python scripts/train.py --model yolo11s.pt --epochs 150 --batch 8
```
→ Meilleur modèle : `runs/kitesurf/weights/best.pt`

## Structure
```
kite-detector/
  dataset/
    raw/          ← images collectées (non annotées)
    images/
      train/      ← après conversion
      val/
    labels/
      train/
      val/
    data.yaml
  scripts/
    collect_images.py
    labelstudio_to_yolo.py
    train.py
  runs/           ← résultats d'entraînement YOLO
  ls-env/         ← venv Label Studio
  labelstudio_config.xml
  start_labelstudio.bat
```

## Notes
- Images : format `large` Skaping (≈640×480), archives 30j max
- Classe unique : `kitesurf` (kite + rider comme une seule bbox)
- YOLOv11n : ~2.6M params, tourne sur CPU si pas de GPU
- Pour de meilleurs résultats : 200+ images annotées minimum
