"""
Train the segmentation model on the prepared tiles.

Loss is weighted cross-entropy plus Dice, so thin, rare classes (walls,
windows) pull as hard as large ones (rooms). Each epoch draws
`samples_per_epoch` random crops, rare classes oversampled, then scores the
validation tiles by intersection over union per class. The best checkpoint by
mean IoU over the classes that matter is kept as best.pt, with its config and
class list, so prediction needs nothing else.

    takeoff-train --name convnext-v1
    takeoff-train --name convnext-v1 --resume runs/convnext-v1/last.pt
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch
from torch.utils.data import DataLoader, WeightedRandomSampler
from tqdm import tqdm

from takeoff_model.config import load_config, pick_device
from takeoff_model.data import Tiles
from takeoff_model.net import build


def confusion(pred: torch.Tensor, truth: torch.Tensor, classes: int) -> np.ndarray:
    index = (truth.flatten() * classes + pred.flatten()).cpu()
    return torch.bincount(index, minlength=classes * classes).reshape(classes, classes).numpy()


def iou(matrix: np.ndarray) -> np.ndarray:
    inter = np.diag(matrix)
    union = matrix.sum(0) + matrix.sum(1) - inter
    return np.where(union > 0, inter / np.maximum(union, 1), np.nan)


@torch.no_grad()
def validate(model, loader, classes: int, device, autocast) -> np.ndarray:
    model.eval()
    matrix = np.zeros((classes, classes), np.int64)
    for images, masks in loader:
        with autocast():
            logits = model(images.to(device))
        matrix += confusion(logits.argmax(1), masks.to(device), classes)
    model.train()
    return iou(matrix)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--name", default=time.strftime("run-%Y%m%d-%H%M"))
    parser.add_argument("--resume", help="checkpoint to continue from (or to fine-tune from, with --fine-tune)")
    parser.add_argument("--fine-tune", action="store_true", help="start from --resume's weights with a fresh schedule")
    parser.add_argument("--device")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    tcfg, classes = cfg["train"], cfg["classes"]
    device = pick_device(args.device)
    out = Path(tcfg["out"]) / args.name
    out.mkdir(parents=True, exist_ok=True)

    train_set = Tiles(cfg["data"]["root"], "train", crop=tcfg["crop"], scale=tcfg.get("scale", (0.45, 1.6)))
    val_set = Tiles(cfg["data"]["root"], "val")
    if not len(train_set):
        raise SystemExit("No training tiles: run takeoff-prepare first.")
    sampler = WeightedRandomSampler(train_set.sample_weights(), tcfg["samples_per_epoch"], replacement=True)
    workers = tcfg["workers"]
    train_loader = DataLoader(train_set, tcfg["batch_size"], sampler=sampler, num_workers=workers,
                              pin_memory=device.type == "cuda", drop_last=True, persistent_workers=workers > 0)
    val_loader = DataLoader(val_set, max(1, tcfg["batch_size"] // 4), num_workers=workers)

    model = build(cfg, len(classes)).to(device)
    cross_entropy = torch.nn.CrossEntropyLoss(weight=train_set.class_weights().to(device))
    dice = smp.losses.DiceLoss("multiclass", from_logits=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=tcfg["lr"], weight_decay=tcfg["weight_decay"])
    steps_per_epoch = len(train_loader)
    total, warmup = tcfg["epochs"] * steps_per_epoch, tcfg["warmup_epochs"] * steps_per_epoch
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda s: (s + 1) / warmup if s < warmup else 0.5 * (1 + math.cos(math.pi * (s - warmup) / max(1, total - warmup))))

    # Mixed precision on CUDA: bfloat16 where the GPU has it, else float16 with loss scaling.
    use_amp = tcfg["amp"] and device.type == "cuda"
    dtype = torch.bfloat16 if use_amp and torch.cuda.is_bf16_supported() else torch.float16
    scaler = torch.amp.GradScaler(enabled=use_amp and dtype == torch.float16)
    autocast = lambda: torch.autocast(device.type, dtype=dtype, enabled=use_amp)

    start, best = 0, -1.0
    if args.resume:
        saved = torch.load(args.resume, map_location="cpu", weights_only=False)
        model.load_state_dict(saved["state_dict"])
        if not args.fine_tune:
            optimizer.load_state_dict(saved["optimizer"])
            schedule.load_state_dict(saved["schedule"])
            start, best = saved["epoch"] + 1, saved.get("best", -1.0)

    # Rooms, walls, windows and doors decide a takeoff; background does not.
    scored = [classes.index(c) for c in ("space", "wall", "window", "door") if c in classes]
    log_path = out / "log.csv"
    with open(log_path, "a", newline="") as log_file:
        log = csv.writer(log_file)
        if start == 0:
            log.writerow(["epoch", "loss", "lr", "score", *[f"iou_{c}" for c in classes], "seconds"])
        print(f"Training on {device}: {len(train_set)} train tiles, {len(val_set)} validation tiles → {out}")

        for epoch in range(start, tcfg["epochs"]):
            began, losses = time.time(), []
            model.train()
            for images, masks in tqdm(train_loader, desc=f"epoch {epoch + 1}/{tcfg['epochs']}", leave=False):
                images, masks = images.to(device, non_blocking=True), masks.to(device, non_blocking=True)
                with autocast():
                    logits = model(images)
                    loss = cross_entropy(logits.float(), masks) + dice(logits.float(), masks)
                optimizer.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                schedule.step()
                losses.append(loss.item())

            per_class = validate(model, val_loader, len(classes), device, autocast) if len(val_set) else np.full(len(classes), np.nan)
            score = float(np.nanmean(per_class[scored])) if len(val_set) else -float(np.mean(losses))
            seconds = round(time.time() - began)
            log.writerow([epoch + 1, round(float(np.mean(losses)), 4), f"{schedule.get_last_lr()[0]:.2e}",
                          round(score, 4), *[round(float(v), 4) for v in per_class], seconds])
            log_file.flush()
            print(f"epoch {epoch + 1}: loss {np.mean(losses):.4f}, score {score:.4f}, "
                  + ", ".join(f"{c} {v:.3f}" for c, v in zip(classes, per_class) if c != "background" and not np.isnan(v))
                  + f" ({seconds} s)")

            state = {"state_dict": model.state_dict(), "optimizer": optimizer.state_dict(), "schedule": schedule.state_dict(),
                     "config": cfg, "classes": classes, "epoch": epoch, "best": max(best, score),
                     "metrics": dict(zip(classes, map(float, per_class)))}
            torch.save(state, out / "last.pt")
            if score > best:
                best = score
                torch.save({k: v for k, v in state.items() if k not in ("optimizer", "schedule")}, out / "best.pt")
                print(f"  new best → {out / 'best.pt'}")


if __name__ == "__main__":
    main()
