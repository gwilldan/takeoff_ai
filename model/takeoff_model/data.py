"""
Training tiles, augmented so the model meets every kind of drawing it will be
given: any orientation, a range of scales around the nominal one, heavier and
lighter pens, blur, scan noise, JPEG artefacts, colour and greyscale.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")  # no network check on import

import albumentations as A  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch.utils.data import Dataset  # noqa: E402

MEAN = np.array([0.485, 0.456, 0.406], np.float32) * 255
STD = np.array([0.229, 0.224, 0.225], np.float32) * 255


def to_tensor(image: np.ndarray) -> torch.Tensor:
    """RGB uint8 (H, W, 3) → normalised float tensor (3, H, W)."""
    return torch.from_numpy(((image.astype(np.float32) - MEAN) / STD).transpose(2, 0, 1).copy())


def ink_weight(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Thicken or thin every line by a pixel each side, as a heavier or finer pen would (odd kernel: no shift)."""
    kernel = np.ones((3, 3), np.uint8)
    return cv2.erode(image, kernel) if rng.random() < 0.5 else cv2.dilate(image, kernel)


def augmentation(crop: int, scale: tuple[float, float] = (0.45, 1.6)) -> A.Compose:
    white = dict(border_mode=cv2.BORDER_CONSTANT, fill=255, fill_mask=0)
    return A.Compose([
        A.RandomRotate90(),
        A.HorizontalFlip(),
        A.VerticalFlip(),
        A.Affine(scale=tuple(scale), rotate=(-2, 2), p=0.7, **white),
        A.PadIfNeeded(min_height=crop, min_width=crop, **white),
        A.RandomCrop(crop, crop),
        A.OneOf([A.GaussianBlur(blur_limit=(3, 5)), A.MotionBlur(blur_limit=(3, 5))], p=0.2),
        A.ImageCompression(quality_range=(30, 95), p=0.3),
        A.GaussNoise(std_range=(0.01, 0.06), p=0.2),
        A.RandomBrightnessContrast(p=0.3),
        A.HueSaturationValue(p=0.2),
        A.ToGray(p=0.2),
    ])


class Tiles(Dataset):
    def __init__(self, root: str | Path, split: str, crop: int | None = None, scale: tuple[float, float] = (0.45, 1.6)):
        self.root = Path(root) / "tiles"
        index = json.loads((self.root / "index.json").read_text())
        self.classes = index["classes"]
        self.entries = [e for e in index["tiles"] if e["split"] == split]
        self.transform = augmentation(crop, scale) if crop else None
        self.rng = np.random.default_rng()

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, i: int):
        name = self.entries[i]["name"]
        image = cv2.cvtColor(cv2.imread(str(self.root / f"{name}.png")), cv2.COLOR_BGR2RGB)
        mask = cv2.imread(str(self.root / f"{name}_mask.png"), cv2.IMREAD_GRAYSCALE)
        if self.transform:
            if self.rng.random() < 0.3:
                image = ink_weight(image, self.rng)
            out = self.transform(image=image, mask=mask)
            image, mask = out["image"], out["mask"]
        return to_tensor(image), torch.from_numpy(mask.astype(np.int64))

    def sample_weights(self) -> list[float]:
        """Tiles with the rare classes are drawn more often: doors and windows, columns and stairs."""
        rare = [self.classes.index(c) for c in ("door", "window") if c in self.classes]
        rarer = [self.classes.index(c) for c in ("column", "stair") if c in self.classes]
        return [1 + 3 * any(e["counts"][i] for i in rare) + 2 * any(e["counts"][i] for i in rarer) for e in self.entries]

    def class_weights(self) -> torch.Tensor:
        """Loss weight per class, the square root of inverse frequency, so thin walls count as much as big rooms."""
        totals = np.array([e["counts"] for e in self.entries], np.float64).sum(0) + 1
        weights = np.sqrt(np.median(totals) / totals)
        return torch.tensor(np.clip(weights, 0.1, 10), dtype=torch.float32)
