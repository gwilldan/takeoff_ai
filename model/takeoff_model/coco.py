"""
Round-trip labels through COCO, the format CVAT, Label Studio and Roboflow
import and export, so drawings are labelled in a real annotation tool.

    takeoff-coco export --out data/coco                  # every source page, with labels where they exist
    takeoff-coco export --out data/coco --unlabelled     # only pages with no labels yet
    takeoff-coco export --out data/coco --unlabelled --drafts   # those pages, pre-filled with the engine's drafts
    takeoff-coco import data/coco/annotations.json       # corrected labels back into data/labels

Images are named `<document>__p<page>.png` and rendered at the training
resolution; polygons are scaled to them from whatever size their labels were
in (page pixels, or PDF points for labels saved from the editor). Import that
name back; a tool that renames images breaks the link.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2
import numpy as np

from takeoff_model.config import class_name, load_config
from takeoff_model.labels import read_labels
from takeoff_model.pages import SOURCE_SUFFIXES, page_count, render, render_dpi, stated_scale

FILE_NAME = re.compile(r"^(?P<doc>.+)__p(?P<page>\d+)\.png$")


def export(root: Path, out: Path, classes: list[str], render_cfg: dict, unlabelled_only: bool, drafts: bool = False) -> None:
    (out / "images").mkdir(parents=True, exist_ok=True)
    categories = [{"id": i, "name": c} for i, c in enumerate(classes) if i]
    category_id = {c["name"]: c["id"] for c in categories}
    images, annotations = [], []
    for source in sorted(p for p in (root / "sources").iterdir() if p.suffix.lower() in SOURCE_SUFFIXES):
        labels_path, drafts_path = root / "labels" / f"{source.stem}.json", root / "drafts" / f"{source.stem}.json"
        labels = read_labels(labels_path) if labels_path.exists() else {}
        draft = read_labels(drafts_path) if drafts and drafts_path.exists() else {}
        for index in range(page_count(source)):
            if unlabelled_only and index in labels:
                continue
            page = labels.get(index) or draft.get(index)
            denominator = ((page or {}).get("scale") or {}).get("denominator") or stated_scale(source, index)
            image = render(source, index, render_dpi(denominator, render_cfg), render_cfg)
            sx = image.shape[1] / page["image_size"]["width"] if page else 1.0
            sy = image.shape[0] / page["image_size"]["height"] if page else 1.0
            name = f"{source.stem}__p{index}.png"
            cv2.imwrite(str(out / "images" / name), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            image_id = len(images) + 1
            images.append({"id": image_id, "file_name": name, "width": image.shape[1], "height": image.shape[0],
                           "denominator": denominator})
            for d in (page or {}).get("detections", []):
                cls = class_name(d["class_name"])
                if cls not in category_id or len(d["polygon"]) < 3:
                    continue
                poly = np.asarray(d["polygon"], np.float64) * [sx, sy]
                x0, y0 = poly.min(0)
                x1, y1 = poly.max(0)
                annotations.append({
                    "id": len(annotations) + 1, "image_id": image_id, "category_id": category_id[cls],
                    "segmentation": [poly.ravel().round(1).tolist()], "bbox": [x0, y0, x1 - x0, y1 - y0],
                    "area": float(cv2.contourArea(poly.astype(np.float32))), "iscrowd": 0,
                })
    (out / "annotations.json").write_text(json.dumps({"images": images, "annotations": annotations, "categories": categories}))
    print(f"{len(images)} page images and {len(annotations)} polygons in {out}")


def import_(coco_path: Path, labels_dir: Path) -> None:
    coco = json.loads(coco_path.read_text())
    names = {c["id"]: c["name"] for c in coco["categories"]}
    by_image: dict[int, list] = {}
    for a in coco["annotations"]:
        by_image.setdefault(a["image_id"], []).append(a)
    documents: dict[str, list] = {}
    for image in coco["images"]:
        match = FILE_NAME.match(Path(image["file_name"]).name)
        if not match:
            print(f"skipping {image['file_name']}: not named <document>__p<page>.png")
            continue
        page_index = int(match["page"])
        detections = []
        for a in by_image.get(image["id"], []):
            for part in a["segmentation"] if isinstance(a["segmentation"], list) else []:
                polygon = np.asarray(part, np.float64).reshape(-1, 2)
                if len(polygon) < 3:
                    continue
                x0, y0 = polygon.min(0)
                x1, y1 = polygon.max(0)
                detections.append({"id": f"det_{page_index}_{len(detections):03d}", "class_name": names[a["category_id"]],
                                   "score": 1.0, "bbox": [x0, y0, x1, y1], "polygon": polygon.round(1).tolist(),
                                   "coordinate_space": "page_pixels"})
        denominator = image.get("denominator")
        documents.setdefault(match["doc"], []).append({
            "page_index": page_index, "image_size": {"width": image["width"], "height": image["height"]},
            "scale": {"denominator": denominator, "source": "drawing" if denominator else "none"},
            "detections": detections,
        })
    labels_dir.mkdir(parents=True, exist_ok=True)
    for doc, pages in documents.items():
        path = labels_dir / f"{doc}.json"
        existing = {p["page_index"]: p for p in json.loads(path.read_text())["pages"]} if path.exists() else {}
        existing.update({p["page_index"]: p for p in pages})
        path.write_text(json.dumps({"id": doc, "status": "succeeded", "pages": sorted(existing.values(), key=lambda p: p["page_index"])}))
    print(f"labels for {len(documents)} document(s) written to {labels_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    out = commands.add_parser("export")
    out.add_argument("--out", required=True)
    out.add_argument("--unlabelled", action="store_true", help="only pages with no labels yet")
    out.add_argument("--drafts", action="store_true", help="pre-fill unlabelled pages with the engine's drafts (data/drafts)")
    into = commands.add_parser("import")
    into.add_argument("coco")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    root = Path(cfg["data"]["root"])
    if args.command == "export":
        export(root, Path(args.out), cfg["classes"], cfg["render"], args.unlabelled, args.drafts)
    else:
        import_(Path(args.coco), root / "labels")


if __name__ == "__main__":
    main()
