"""
Draw detections from a takeoff JSON onto the PDF they were made from.

    uv run annotator.py                                  # input/*.json + input/*.pdf -> output/
    uv run annotator.py input/outside.json input/plan.pdf output/plan.annotated.pdf

The JSON holds pages of detections (class_name, score, polygon) in page-pixel
coordinates of an image of `image_size`; each point is scaled onto the PDF
page. Rooms are drawn at the back, then walls, then windows and doors on top.
A legend with the counts and the file's metrics goes in the top-left corner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent

# Back to front: class -> (colour, fill opacity, label size or None for no label).
STYLES = {
    "space": ((0.09, 0.64, 0.29), 0.25, 4.0),
    "wall": ((0.15, 0.39, 0.92), 0.45, None),
    "window": ((0.03, 0.57, 0.70), 0.70, 3.0),
    "door": ((0.92, 0.35, 0.05), 0.70, 3.0),
}
OTHER = ((0.49, 0.23, 0.93), 0.45, 3.0)


def annotate(json_path: Path, pdf_path: Path, out_path: Path) -> None:
    data = json.loads(json_path.read_text())
    doc = pymupdf.open(pdf_path)

    for page_data in data.get("pages", []):
        if "image_size" not in page_data:
            continue  # a page that was not measured: skipped, or its takeoff failed
        page = doc[page_data.get("page_index", 0)]
        size = page_data["image_size"]
        sx, sy = page.rect.width / size["width"], page.rect.height / size["height"]
        detections = page_data.get("detections", [])
        classes = list(STYLES) + sorted({d["class_name"] for d in detections} - set(STYLES))

        for name in classes:
            colour, opacity, label_size = STYLES.get(name, OTHER)
            for det in (d for d in detections if d["class_name"] == name):
                points = [pymupdf.Point(x * sx, y * sy) for x, y in det["polygon"]]
                if len(points) < 2:
                    continue
                shape = page.new_shape()
                shape.draw_polyline(points + [points[0]])
                shape.finish(color=colour, fill=colour, fill_opacity=opacity, width=0.8, closePath=True)
                shape.commit()
                if label_size:
                    x0, y0 = det["bbox"][0] * sx, det["bbox"][1] * sy
                    page.insert_text((x0 + 1, y0 + label_size + 1), f"{name} {det.get('score', 0):.2f}",
                                     fontsize=label_size, color=colour)

        y = 30
        page.insert_text((30, y), f"Annotated from {json_path.name}", fontsize=9)
        for name in classes:
            count = sum(d["class_name"] == name for d in detections)
            if not count:
                continue
            y += 11
            colour = STYLES.get(name, OTHER)[0]
            page.draw_rect(pymupdf.Rect(30, y - 7, 38, y + 1), color=colour, fill=colour)
            page.insert_text((42, y), f"{name}: {count}", fontsize=8)
        for key, value in (data.get("metrics") or {}).items():
            y += 10
            page.insert_text((30, y), f"{key}: {value}", fontsize=6.5, color=(0.2, 0.2, 0.2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    doc.close()
    print(f"{json_path.name} + {pdf_path.name} -> {out_path}")


def _only(folder: Path, pattern: str) -> Path:
    found = sorted(folder.glob(pattern))
    if len(found) != 1:
        sys.exit(f"Expected exactly one {pattern} in {folder}, found {len(found)}; pass the paths instead.")
    return found[0]


if __name__ == "__main__":
    if len(sys.argv) == 4:
        json_path, pdf_path, out_path = map(Path, sys.argv[1:])
    elif len(sys.argv) == 1:
        json_path, pdf_path = _only(HERE / "input", "*.json"), _only(HERE / "input", "*.pdf")
        out_path = HERE / "output" / f"{pdf_path.stem}.annotated.pdf"
    else:
        sys.exit("Usage: annotator.py [input.json input.pdf output.pdf]")
    annotate(json_path, pdf_path, out_path)
