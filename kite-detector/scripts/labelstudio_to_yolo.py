"""
labelstudio_to_yolo.py
Convertit l'export JSON de Label Studio en dataset YOLO
et split train/val (80/20).

Usage:
  python labelstudio_to_yolo.py --export project-export.json

Sortie:
  dataset/
    images/train/   images/val/
    labels/train/   labels/val/
    data.yaml
"""

import json, shutil, random, argparse
from pathlib import Path

CLASSES = ["kitesurf"]   # une seule classe pour l'instant

def convert(export_path: str):
    with open(export_path, encoding="utf-8") as f:
        tasks = json.load(f)

    base = Path(__file__).parent.parent / "dataset"
    for split in ["train", "val"]:
        (base / "images" / split).mkdir(parents=True, exist_ok=True)
        (base / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Filtrer les tâches annotées
    annotated = [t for t in tasks if t.get("annotations") and
                 t["annotations"][0].get("result")]
    print(f"{len(annotated)} images annotees sur {len(tasks)}")

    random.seed(42)
    random.shuffle(annotated)
    split_idx = int(len(annotated) * 0.8)
    splits = {"train": annotated[:split_idx], "val": annotated[split_idx:]}

    for split, items in splits.items():
        for task in items:
            ann    = task["annotations"][0]
            result = ann["result"]
            img_w  = result[0]["original_width"]  if result else 640
            img_h  = result[0]["original_height"] if result else 640

            # Chemin image source
            img_src = Path(task["data"].get("image", "").lstrip("/"))
            if not img_src.exists():
                # Chercher dans dataset/raw/ par nom de fichier
                raw = base / "raw" / img_src.name
                if raw.exists():
                    img_src = raw

            img_dst = base / "images" / split / img_src.name
            if img_src.exists():
                shutil.copy2(img_src, img_dst)

            # Convertir les bbox en format YOLO (cx cy w h normalisés)
            lines = []
            for r in result:
                if r.get("type") != "rectanglelabels":
                    continue
                val   = r["value"]
                label = val["rectanglelabels"][0]
                if label not in CLASSES:
                    CLASSES.append(label)
                cls_id = CLASSES.index(label)

                x  = val["x"] / 100
                y  = val["y"] / 100
                w  = val["width"] / 100
                h  = val["height"] / 100
                cx = x + w / 2
                cy = y + h / 2
                lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

            lbl_dst = base / "labels" / split / (img_src.stem + ".txt")
            lbl_dst.write_text("\n".join(lines), encoding="utf-8")

        print(f"  {split}: {len(items)} images")

    # data.yaml
    yaml = base / "data.yaml"
    yaml.write_text(
        f"path: {base.resolve()}\n"
        f"train: images/train\n"
        f"val:   images/val\n\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n",
        encoding="utf-8"
    )
    print(f"\nDataset pret: {base}")
    print(f"data.yaml: {yaml}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="Chemin vers l'export JSON Label Studio")
    args = ap.parse_args()
    convert(args.export)
