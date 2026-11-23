"""
Render every labelled page at its training resolution, rasterize its labels,
and cut both into overlapping tiles in data/tiles, with an index of how many
pixels of each class every tile holds. Documents are split into train and
validation by name, so pages of one building never sit on both sides.

    takeoff-prepare
"""

from __future__ import annotations

import argparse
import json
import shutil
import zlib
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

from takeoff_model.config import load_config
from takeoff_model.labels import find_source, label_denominator, rasterize, read_labels
from takeoff_model.pages import render, render_dpi, stated_scale

# Keep this share of tiles that are blank paper with no labels.
BLANK_KEEP = 0.1


def is_validation(name: str, fraction: float) -> bool:
    return zlib.crc32(name.encode()) % 1000 < fraction * 1000


def positions(length: int, tile: int, stride: int) -> list[int]:
    if length <= tile:
        return [0]
    starts = list(range(0, length - tile, stride))
    return starts + [length - tile]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    classes, data = cfg["classes"], cfg["data"]
    root = Path(data["root"])
    out = root / "tiles"
    shutil.rmtree(out, ignore_errors=True)
    (out / "train").mkdir(parents=True)
    (out / "val").mkdir(parents=True)
    tile, stride = data["tile"], data["tile"] - data["overlap"]
    rng = np.random.default_rng(0)
    entries = []

    for labels_path in tqdm(sorted((root / "labels").glob("*.json")), desc="pages"):
        source = find_source(labels_path, root / "sources")
        if source is None:
            print(f"skipping {labels_path.name}: no source of that name in {root / 'sources'}")
            continue
        split = "val" if is_validation(labels_path.stem, data["val_fraction"]) else "train"
        for page_index, page in read_labels(labels_path).items():
            denominator = label_denominator(page) or stated_scale(source, page_index)
            image = render(source, page_index, render_dpi(denominator, cfg["render"]), cfg["render"])
            mask = rasterize(page, image.shape[:2], classes)
            if image.shape[0] < tile or image.shape[1] < tile:
                pad = ((0, max(0, tile - image.shape[0])), (0, max(0, tile - image.shape[1])))
                image = np.pad(image, (*pad, (0, 0)), constant_values=255)
                mask = np.pad(mask, pad)
            for y in positions(image.shape[0], tile, stride):
                for x in positions(image.shape[1], tile, stride):
                    tile_mask = mask[y:y + tile, x:x + tile]
                    counts = np.bincount(tile_mask.ravel(), minlength=len(classes))
                    tile_image = image[y:y + tile, x:x + tile]
                    if counts[0] == tile_mask.size and tile_image.min() > 200 and rng.random() > BLANK_KEEP:
                        continue
                    name = f"{split}/{labels_path.stem}_p{page_index}_{y}_{x}"
                    cv2.imwrite(str(out / f"{name}.png"), cv2.cvtColor(tile_image, cv2.COLOR_RGB2BGR))
                    cv2.imwrite(str(out / f"{name}_mask.png"), tile_mask)
                    entries.append({"name": name, "split": split, "counts": counts.tolist()})

    (out / "index.json").write_text(json.dumps({"classes": classes, "tiles": entries}))
    by_split = {s: sum(e["split"] == s for e in entries) for s in ("train", "val")}
    print(f"{len(entries)} tiles in {out}: {by_split['train']} train, {by_split['val']} validation")


if __name__ == "__main__":
    main()
