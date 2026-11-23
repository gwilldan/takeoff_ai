"""
Score predicted takeoff JSON against hand-checked labels.

For every class: pixel IoU (how much of the drawn area agrees) and, for
countable things (rooms, doors, windows, columns, stairs), precision, recall
and F1 with a match at polygon IoU ≥ 0.5. Walls are also compared by total
length where both sides know the scale. Works on any takeoff JSON, so the
same numbers compare this model, QuantTakeoff and the old rule-based pipeline.

    takeoff-evaluate --truth data/labels/plan.json --pred plan.takeoff.json
    takeoff-evaluate --truth data/labels --pred predictions/      # every file by name
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from takeoff_model.config import class_name, load_config
from takeoff_model.labels import rasterize, read_labels

COUNTED = ["space", "door", "window", "column", "stair"]


def _wall_length_m(page: dict) -> float | None:
    mpp = (page.get("scale") or {}).get("meters_per_pixel")
    if not mpp:
        return None
    total = 0.0
    for d in page["detections"]:
        if class_name(d["class_name"]) == "wall" and len(d["polygon"]) >= 3:
            (_, _), (w, h), _ = cv2.minAreaRect(np.asarray(d["polygon"], np.float32))
            total += max(w, h)
    return total * mpp


def _polygons(page: dict, name: str, size: tuple[int, int]) -> list[np.ndarray]:
    """A class's polygons, scaled to the truth page's pixels."""
    sx, sy = size[1] / page["image_size"]["width"], size[0] / page["image_size"]["height"]
    return [np.asarray(d["polygon"], np.float64) * [sx, sy] for d in page["detections"]
            if class_name(d["class_name"]) == name and len(d["polygon"]) >= 3]


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    """Polygon IoU, rasterized only over the two polygons' joint box."""
    lo = np.floor(np.minimum(a.min(0), b.min(0))).astype(int)
    hi = np.ceil(np.maximum(a.max(0), b.max(0))).astype(int) + 1
    masks = []
    for poly in (a, b):
        mask = np.zeros((hi[1] - lo[1] + 1, hi[0] - lo[0] + 1), np.uint8)
        cv2.fillPoly(mask, [np.round(poly - lo).astype(np.int32)], 1)
        masks.append(mask.astype(bool))
    union = np.logical_or(*masks).sum()
    return float(np.logical_and(*masks).sum() / union) if union else 0.0


def _match(truth: list[np.ndarray], pred: list[np.ndarray], threshold: float = 0.5) -> int:
    """Matches at IoU ≥ threshold, each object used once, best pairs first."""
    pairs = []
    for i, t in enumerate(truth):
        for j, p in enumerate(pred):
            if (t.min(0) <= p.max(0)).all() and (p.min(0) <= t.max(0)).all():
                pairs.append((_iou(t, p), i, j))
    used_t, used_p, matched = set(), set(), 0
    for score, i, j in sorted(pairs, reverse=True):
        if score >= threshold and i not in used_t and j not in used_p:
            used_t.add(i), used_p.add(j)
            matched += 1
    return matched


def score_page(truth: dict, pred: dict, classes: list[str]) -> dict:
    size = (truth["image_size"]["height"], truth["image_size"]["width"])
    t_mask, p_mask = rasterize(truth, size, classes), rasterize(pred, size, classes)
    scores = {"pixels": {}, "objects": {}, "wall_length_m": (_wall_length_m(truth), _wall_length_m(pred))}
    for i, name in enumerate(classes[1:], 1):
        scores["pixels"][name] = (int(np.logical_and(t_mask == i, p_mask == i).sum()), int(np.logical_or(t_mask == i, p_mask == i).sum()))
    for name in (c for c in COUNTED if c in classes):
        t, p = _polygons(truth, name, size), _polygons(pred, name, size)
        scores["objects"][name] = (_match(t, p), len(t), len(p))
    return scores


def report(totals: dict) -> str:
    lines = [f"{'class':8} {'pixel IoU':>9} {'truth':>6} {'pred':>6} {'precision':>9} {'recall':>7} {'F1':>6}"]
    for name, (inter, union) in totals["pixels"].items():
        row = f"{name:8} {inter / union if union else float('nan'):9.3f}"
        if name in totals["objects"]:
            matched, n_truth, n_pred = totals["objects"][name]
            p = matched / n_pred if n_pred else float("nan")
            r = matched / n_truth if n_truth else float("nan")
            f = 2 * p * r / (p + r) if p + r else 0.0
            row += f" {n_truth:6d} {n_pred:6d} {p:9.3f} {r:7.3f} {f:6.3f}"
        lines.append(row)
    truth_m, pred_m = totals["wall_length_m"]
    if truth_m:
        lines.append(f"wall length: truth {truth_m:.1f} m, predicted {pred_m:.1f} m ({(pred_m / truth_m - 1) * 100:+.1f}%)")
    return "\n".join(lines)


def evaluate(pairs: list[tuple[Path, Path]], classes: list[str]) -> dict:
    totals = {"pixels": {}, "objects": {}, "wall_length_m": [0.0, 0.0]}
    for truth_path, pred_path in pairs:
        truth, pred = read_labels(truth_path), read_labels(pred_path)
        for index, truth_page in truth.items():
            if index not in pred:
                print(f"{pred_path.name}: page {index + 1} missing; counted as empty")
            pred_page = pred.get(index) or {**truth_page, "detections": [], "scale": {}}
            page = score_page(truth_page, pred_page, classes)
            for name, (a, b) in page["pixels"].items():
                x, y = totals["pixels"].get(name, (0, 0))
                totals["pixels"][name] = (x + a, y + b)
            for name, (a, b, c) in page["objects"].items():
                x, y, z = totals["objects"].get(name, (0, 0, 0))
                totals["objects"][name] = (x + a, y + b, z + c)
            t_len, p_len = page["wall_length_m"]
            if t_len is not None and p_len is not None:
                totals["wall_length_m"][0] += t_len
                totals["wall_length_m"][1] += p_len
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--truth", required=True, help="labels file, or a folder of them")
    parser.add_argument("--pred", required=True, help="prediction file, or a folder holding <name>.json or <name>.takeoff.json")
    args = parser.parse_args(argv)
    truth, pred = Path(args.truth), Path(args.pred)
    if truth.is_dir():
        pairs = []
        for t in sorted(truth.glob("*.json")):
            match = next((p for p in (pred / f"{t.stem}.takeoff.json", pred / t.name) if p.exists()), None)
            if match:
                pairs.append((t, match))
    else:
        pairs = [(truth, pred)]
    if not pairs:
        raise SystemExit("Nothing to compare: no prediction shares a name with a labels file.")
    print(f"{len(pairs)} document(s)\n" + report(evaluate(pairs, load_config(args.config)["classes"])))


if __name__ == "__main__":
    main()
