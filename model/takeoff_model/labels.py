"""
Labels are takeoff JSON — the format QuantTakeoff returns, the backend
publishes and the annotator draws: pages of detections with a class name and a
polygon in the page pixels of an image of `image_size`. One file per source
document, named like it (`labels/plan.json` labels `sources/plan.pdf`). Every
page listed in a file is labelled; a page with no detections teaches the model
that it holds nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from takeoff_model.config import PAINT_ORDER, class_name
from takeoff_model.pages import SOURCE_SUFFIXES


def read_labels(path: str | Path) -> dict[int, dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    return {p["page_index"]: p for p in data.get("pages", []) if p.get("image_size")}


def find_source(labels_path: Path, sources: Path) -> Path | None:
    for suffix in SOURCE_SUFFIXES:
        for candidate in (sources / f"{labels_path.stem}{suffix}", sources / f"{labels_path.stem}{suffix.upper()}"):
            if candidate.exists():
                return candidate
    return None


def label_denominator(page: dict[str, Any]) -> float | None:
    scale = page.get("scale") or {}
    return scale.get("denominator") or None


def _points(polygon, sx: float, sy: float) -> np.ndarray:
    return np.round(np.asarray(polygon, dtype=np.float64) * [sx, sy]).astype(np.int32)


def rasterize(page: dict[str, Any], size: tuple[int, int], classes: list[str]) -> np.ndarray:
    """
    A class-index mask of `size` (height, width) from a labelled page. Polygons
    are scaled from the label's image size; where classes overlap the later
    one in PAINT_ORDER wins, and a room's holes are left to what fills them.
    """
    height, width = size
    sx, sy = width / page["image_size"]["width"], height / page["image_size"]["height"]
    mask = np.zeros((height, width), dtype=np.uint8)
    detections = [d for d in page.get("detections", []) if class_name(d["class_name"]) in classes]
    for name in [c for c in PAINT_ORDER if c in classes]:
        index = classes.index(name)
        for d in (d for d in detections if class_name(d["class_name"]) == name):
            parts = d.get("polygon_parts") or [d["polygon"]]
            cv2.fillPoly(mask, [_points(p, sx, sy) for p in parts if len(p) >= 3], index)
            for hole in d.get("polygon_holes") or []:
                if len(hole) >= 3:
                    cv2.fillPoly(mask, [_points(hole, sx, sy)], 0)
    return mask


def takeoff_result(document_id: str, pages: list[dict[str, Any]], model: dict[str, Any] | None = None) -> dict[str, Any]:
    """A whole takeoff result around predicted or labelled pages."""
    counts: dict[str, int] = {}
    for d in (d for p in pages for d in p["detections"]):
        counts[d["class_name"]] = counts.get(d["class_name"], 0) + 1
    result = {
        "id": document_id, "status": "succeeded", "schema_version": "takeoff-model-1",
        "pages": pages, "metrics": {f"{name} count": n for name, n in sorted(counts.items())},
        "assets": [], "error": None,
    }
    if model:
        result["model"] = model
    return result
