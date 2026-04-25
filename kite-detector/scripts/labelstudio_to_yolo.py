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

import json, shutil, random, argparse, os
from pathlib import Path

CLASSES = ["kitesurf"]

# Répertoire media de Label Studio (Windows)
LS_MEDIA = Path(os.environ.get("LOCALAPPDATA", "")) / "label-studio" / "label-studio" / "media"

def resolve_image(img_url: str, base: Path) -> Path | None:
    """Trouve le fichier image depuis l'URL Label Studio."""
    # img_url = "/data/upload/3/uuid-filename.jpg"
    rel = img_url.lstrip("/")  # "data/upload/3/uuid-filename.jpg"

    # 1. Chercher dans le media LS
    candidate = LS_MEDIA / rel
    if candidate.exists():
        return candidate

    # 2. Chercher dans dataset/raw/ par nom exact
    fname = Path(rel).name  # "uuid-filename.jpg"
    raw = base / "raw" / fname
    if raw.exists():
        return raw

    # 3. Chercher dans dataset/raw/ en retirant le préfixe UUID (uuid-filename.jpg -> filename.jpg)
    parts = fname.split("-", 1)
    if len(parts) == 2:
        clean_name = parts[1]
        raw_clean = base / "raw" / clean_name
        if raw_clean.exists():
            return raw_clean

    return None

def convert(export_path: str):
    with open(export_path, encoding="utf-8") as f:
        tasks = json.load(f)

    base = Path(__file__).parent.parent / "dataset"
    for split in ["train", "val"]:
        (base / "images" / split).mkdir(parents=True, exist_ok=True)
        (base / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Inclure toutes les tâches annotées (même sans bbox = négatifs)
    annotated = [t for t in tasks if t.get("annotations")]
    with_bbox  = [t for t in annotated if t["annotations"][0].get("result")]
    negatives  = [t for t in annotated if not t["annotations"][0].get("result")]
    print(f"{len(annotated)} images annotees: {len(with_bbox)} avec kite, {len(negatives)} negatives")

    random.seed(42)
    random.shuffle(annotated)
    split_idx = int(len(annotated) * 0.8)
    splits = {"train": annotated[:split_idx], "val": annotated[split_idx:]}

    missing = 0
    for split, items in splits.items():
        for task in items:
            ann    = task["annotations"][0]
            result = ann["result"]
            img_w  = result[0]["original_width"]  if result else 640
            img_h  = result[0]["original_height"] if result else 640

            img_url = task["data"].get("image", "")
            img_src = resolve_image(img_url, base)

            if img_src is None:
                print(f"  MANQUANT: {img_url}")
                missing += 1
                continue

            # Nom de destination = nom propre sans UUID
            dst_name = img_src.name
            img_dst = base / "images" / split / dst_name
            shutil.copy2(img_src, img_dst)

            # Convertir bbox en YOLO (cx cy w h normalisés)
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

            lbl_dst = base / "labels" / split / (Path(dst_name).stem + ".txt")
            lbl_dst.write_text("\n".join(lines), encoding="utf-8")

        print(f"  {split}: {len(items)} images")

    if missing:
        print(f"\nATTENTION: {missing} images introuvables")

    # data.yaml
    yaml_path = base / "data.yaml"
    yaml_path.write_text(
        f"path: {base.resolve()}\n"
        f"train: images/train\n"
        f"val:   images/val\n\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n",
        encoding="utf-8"
    )
    print(f"\nDataset pret: {base}")
    print(f"data.yaml: {yaml_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    args = ap.parse_args()
    convert(args.export)
