"""Config loading, class lists, and the choice of device."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

# Classes painted later win where labels overlap: a door sits on top of its
# wall, a wall on top of the room it bounds.
PAINT_ORDER = ["space", "stair", "wall", "column", "window", "door"]

# Takeoff class names that mean the same thing as ours.
ALIASES = {"room": "space"}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_CONFIG) as f:
        return yaml.safe_load(f)


def class_name(name: str) -> str:
    return ALIASES.get(name, name)


def pick_device(requested: str | None = None):
    import torch

    if requested:
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")
