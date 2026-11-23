"""The network: any segmentation_models_pytorch architecture and encoder named in the config."""

from __future__ import annotations

from typing import Any

import segmentation_models_pytorch as smp
import torch


def build(cfg: dict[str, Any], classes: int, pretrained: bool = True) -> torch.nn.Module:
    model = cfg["model"]
    return smp.create_model(
        arch=model["arch"],
        encoder_name=model["encoder"],
        encoder_weights=model.get("encoder_weights") if pretrained else None,
        classes=classes,
    )


def load(checkpoint: str, device: torch.device) -> tuple[torch.nn.Module, dict[str, Any]]:
    """A trained model and the checkpoint it came from (its config, classes and metrics)."""
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model = build(saved["config"], len(saved["classes"]), pretrained=False)
    model.load_state_dict(saved["state_dict"])
    return model.to(device).eval(), saved
