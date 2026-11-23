"""
Run a trained model on a drawing and write takeoff JSON — the format the
annotator, the backend and the frontend read.

The page is rendered at the training resolution (set by its scale when one is
printed or given). A quick pass of the network at reduced resolution finds the
drawings on the sheet, where it sees building; title blocks, notes, legends
and margins are then left out. Each drawing is predicted in overlapping tiles
blended with a smooth window so tile seams never show, optionally averaged with
a mirrored pass, and turned into walls, doors, windows, rooms, columns and
stairs. Room names and door and window marks come from the page's text, read
by OCR on scans.

    takeoff-predict --checkpoint runs/convnext-v1/best.pt plan.pdf --out plan.json
    takeoff-predict --checkpoint runs/convnext-v1/best.pt plan.pdf --pages 2,3 --scale 100
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from takeoff_model.config import load_config, pick_device
from takeoff_model.data import to_tensor
from takeoff_model.labels import takeoff_result
from takeoff_model.net import load
from takeoff_model.pages import Span, is_image, mm_per_px, page_count, render, render_dpi, stated_scale, text_spans
from takeoff_model.vectorize import vectorize

MARK = re.compile(r"^(D|DR|W|WN|WD|DW|SD|FD|GD)[-\s./]?\d{1,3}[A-Z]?$", re.IGNORECASE)
DIMENSION = re.compile(r"^[\d.,+\-\s]+(mm|m)?$", re.IGNORECASE)


def _window(size: int) -> np.ndarray:
    """2-D blending weight: highest at a tile's centre, low but never zero at its edges."""
    hann = np.hanning(size + 2)[1:-1]
    return np.maximum(np.outer(hann, hann), 0.05).astype(np.float32)


def _starts(length: int, tile: int, stride: int) -> list[int]:
    starts = list(range(0, max(length - tile, 0) + 1, stride))
    return starts if starts[-1] + tile >= length else starts + [length - tile]


def _merge(boxes: list[list[int]]) -> list[tuple[int, int, int, int]]:
    """Overlapping boxes as one, so no part of a drawing is predicted twice."""
    i = 0
    while i < len(boxes):
        p = boxes[i]
        j = next((j for j, q in enumerate(boxes) if j != i and p[0] < q[2] and q[0] < p[2] and p[1] < q[3] and q[1] < p[3]), None)
        if j is None:
            i += 1
            continue
        q = boxes.pop(j)
        boxes[i if j > i else i - 1] = [min(p[0], q[0]), min(p[1], q[1]), max(p[2], q[2]), max(p[3], q[3])]
        i = 0
    return [tuple(b) for b in boxes]


def name_detections(detections: list[dict[str, Any]], spans: list[Span]) -> None:
    """Rooms take the largest name printed inside them; doors and windows the nearest mark."""
    words = [s for s in spans if sum(ch.isalpha() for ch in s.text) >= 2 and not DIMENSION.match(s.text)]
    marks = [s for s in spans if MARK.match(s.text)]
    for d in detections:
        polygon = np.asarray(d["polygon"], np.float32)
        if d["class_name"] == "space":
            inside = [s for s in words if not MARK.match(s.text) and cv2.pointPolygonTest(polygon, s.centre, False) >= 0]
            if inside:
                d["name"] = max(inside, key=lambda s: (s.size, len(s.text))).text
                d["name_source"] = "text"
        elif d["class_name"] in ("door", "window") and marks:
            x0, y0, x1, y1 = d["bbox"]
            centre, reach = ((x0 + x1) / 2, (y0 + y1) / 2), max(3 * max(x1 - x0, y1 - y0), 60)
            nearest = min(marks, key=lambda s: np.hypot(s.centre[0] - centre[0], s.centre[1] - centre[1]))
            if np.hypot(nearest.centre[0] - centre[0], nearest.centre[1] - centre[1]) <= reach:
                d["name"], d["name_source"] = nearest.text, "text"


class Predictor:
    def __init__(self, checkpoint: str, device: str | None = None, config: str | None = None):
        """The network and its rendering come from the checkpoint; how it predicts and post-processes from `config`."""
        self.device = pick_device(device)
        self.model, saved = load(checkpoint, self.device)
        current = load_config(config)
        self.cfg = {**saved["config"], "predict": current["predict"], "vectorize": current["vectorize"]}
        self.classes = saved["classes"]
        self.info = {"checkpoint": Path(checkpoint).name, "epoch": saved.get("epoch"),
                     "arch": self.cfg["model"]["arch"], "encoder": self.cfg["model"]["encoder"]}

    @torch.no_grad()
    def probabilities(self, image: np.ndarray) -> np.ndarray:
        """Per-pixel class probabilities, (height, width, classes) float16."""
        pcfg = self.cfg["predict"]
        tile, stride = pcfg["tile"], pcfg["tile"] - pcfg["overlap"]
        height, width = image.shape[:2]
        padded = np.pad(image, ((0, max(0, tile - height)), (0, max(0, tile - width)), (0, 0)), constant_values=255)
        H, W = padded.shape[:2]
        total = np.zeros((H, W, len(self.classes)), np.float16)
        weight_sum = np.zeros((H, W), np.float32)
        window = _window(tile)
        boxes = [(y, x) for y in _starts(H, tile, stride) for x in _starts(W, tile, stride)]
        for i in range(0, len(boxes), pcfg["batch_size"]):
            batch = boxes[i:i + pcfg["batch_size"]]
            tensor = torch.stack([to_tensor(padded[y:y + tile, x:x + tile]) for y, x in batch]).to(self.device)
            probs = self.model(tensor).softmax(1)
            if pcfg["tta"]:
                probs = (probs + self.model(tensor.flip(-1)).softmax(1).flip(-1)) / 2
            probs = probs.permute(0, 2, 3, 1).float().cpu().numpy()
            for (y, x), p in zip(batch, probs):
                total[y:y + tile, x:x + tile] += (p * window[..., None]).astype(np.float16)
                weight_sum[y:y + tile, x:x + tile] += window
        return (total[:height, :width] / weight_sum[:height, :width, None]).astype(np.float16)

    def drawings(self, image: np.ndarray, px: float) -> list[tuple[int, int, int, int]]:
        """
        Boxes (x0, y0, x1, y1) around each drawing on the sheet, found by the
        network itself at reduced resolution: walls, columns and openings it
        sees, joined across rooms, enclosing a building-sized area. Empty when
        it finds none, and the whole page is then predicted.
        """
        locate = self.cfg["predict"].get("locate")
        if not locate:
            return []
        factor = locate["scale"]
        small = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        label = self.probabilities(small).argmax(-1)
        structure = np.isin(label, [self.classes.index(c) for c in ("wall", "column", "door", "window") if c in self.classes])
        small_px = px / factor
        join = max(3, round(locate["join_mm"] / small_px)) | 1
        joined = cv2.morphologyEx(structure.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((join, join), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
        pad, boxes = locate["pad_mm"] / px, []
        for i in range(1, count):
            x, y, w, h, _ = stats[i]
            enclosed = w * h * small_px**2 / 1e6
            walls = structure[y:y + h, x:x + w][labels[y:y + h, x:x + w] == i].sum() * small_px**2 / 1e6
            if enclosed >= locate["min_plan_m2"] and walls >= locate["min_wall_m2"]:
                boxes.append([max(0, int(x / factor - pad)), max(0, int(y / factor - pad)),
                              min(image.shape[1], int((x + w) / factor + pad)), min(image.shape[0], int((y + h) / factor + pad))])
        return _merge(boxes)

    def predict_page(self, source: str | Path, page_index: int, denominator: float | None = None) -> dict[str, Any]:
        given = denominator
        denominator = denominator or stated_scale(source, page_index)
        dpi = render_dpi(denominator, self.cfg["render"])
        image = render(source, page_index, dpi, self.cfg["render"])
        real = mm_per_px(denominator, dpi)
        px = real or 100 * 25.4 / dpi  # without a scale, sizes are judged as if the sheet were 1:100
        boxes = self.drawings(image, px)
        detections = []
        for x0, y0, x1, y1 in boxes or [(0, 0, image.shape[1], image.shape[0])]:
            for d in vectorize(self.probabilities(image[y0:y1, x0:x1]), self.classes, px, real is not None,
                               self.cfg["vectorize"], page_index):
                d["polygon"] = [[x + x0, y + y0] for x, y in d["polygon"]]
                d["bbox"] = [d["bbox"][0] + x0, d["bbox"][1] + y0, d["bbox"][2] + x0, d["bbox"][3] + y0]
                d["id"] = f"det_{page_index}_{len(detections):03d}"
                detections.append(d)
        px_per_pt = 1.0 if is_image(source) else dpi / 72
        name_detections(detections, text_spans(source, page_index, px_per_pt))
        return {
            "page_index": page_index,
            "image_size": {"width": image.shape[1], "height": image.shape[0]},
            "scale": {"meters_per_pixel": real / 1000 if real else 0.0, "denominator": denominator,
                      "source": "user" if given else "drawing" if denominator else "none"},
            "detections": detections,
            "plans": [{"bbox": list(b)} for b in boxes],
        }

    def predict(self, source: str | Path, pages: list[int] | None = None, denominator: float | None = None,
                document_id: str | None = None) -> dict[str, Any]:
        pages = pages if pages is not None else list(range(page_count(source)))
        result = [self.predict_page(source, i, denominator) for i in pages]
        return takeoff_result(document_id or Path(source).stem, result, self.info)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="PDF or image")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--pages", help="1-based page numbers, e.g. 1,3 (default: every page)")
    parser.add_argument("--scale", type=float, help="drawing scale denominator, e.g. 100 for 1:100")
    parser.add_argument("--out", help="output JSON (default: <source>.takeoff.json)")
    parser.add_argument("--device")
    parser.add_argument("--config", help="prediction and post-processing settings (default: config.yaml)")
    args = parser.parse_args(argv)

    predictor = Predictor(args.checkpoint, args.device, args.config)
    pages = [int(p) - 1 for p in args.pages.split(",")] if args.pages else None
    result = predictor.predict(args.source, pages, args.scale)
    out = Path(args.out or Path(args.source).with_suffix(".takeoff.json"))
    out.write_text(json.dumps(result, indent=1))
    print(f"{args.source} → {out}: {result['metrics']}")


if __name__ == "__main__":
    main()
