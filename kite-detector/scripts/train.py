"""
train.py
Entraîne YOLOv11 sur le dataset kitesurf.

Usage:
  python train.py [--epochs 100] [--model yolo11n.pt] [--imgsz 640]

Prérequis : dataset/data.yaml généré par labelstudio_to_yolo.py
"""

import argparse
from pathlib import Path
from ultralytics import YOLO

BASE    = Path(__file__).parent.parent
YAML    = BASE / "dataset" / "data.yaml"
RUNS    = BASE / "runs"

def train(model_name: str, epochs: int, imgsz: int, batch: int):
    if not YAML.exists():
        raise FileNotFoundError(
            f"data.yaml introuvable : {YAML}\n"
            "Lance d'abord labelstudio_to_yolo.py"
        )

    print(f"Modele : {model_name}")
    print(f"Epochs : {epochs}  |  imgsz : {imgsz}  |  batch : {batch}")
    print(f"Dataset: {YAML}\n")

    model = YOLO(model_name)
    results = model.train(
        data=str(YAML),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(RUNS),
        name="kitesurf",
        exist_ok=True,
        patience=20,        # early stopping
        save=True,
        plots=True,
        verbose=True,
    )

    best = RUNS / "kitesurf" / "weights" / "best.pt"
    print(f"\nEntrainement termine.")
    print(f"Meilleur modele : {best}")
    return results

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model",  default="yolo11n.pt",
                    help="Checkpoint de depart (yolo11n/s/m/l/x.pt)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz",  type=int, default=640)
    ap.add_argument("--batch",  type=int, default=16)
    args = ap.parse_args()
    train(args.model, args.epochs, args.imgsz, args.batch)
