# takeoff-model

Your own floor-plan model. It reads a PDF or image and returns walls, doors, windows, rooms (`space`), columns and stairs as **takeoff JSON**. That's the format QuantTakeoff returns, the backend publishes, the frontend imports and `annotate-pdf-from-json` draws. It also serves the API the backend already calls on QuantTakeoff, so switching engines is a change of URL.

It is a vision model: a neural network reads the page image, so vector PDFs, scans and photos are handled the same way. Everything after the network builds on what the network predicts.

## How it works

1. **Render at a real-world resolution.** Each page is rendered so one pixel covers 15 real millimetres whenever its scale is known: from the label, the `1:100` printed on the page, or `--scale`. A 225 mm wall is then about 15 px thick on every drawing, whatever its paper size or scale. Pages with no known scale render at 150 dpi.
2. **Find the drawings on the sheet.** The network makes a quick pass at half resolution. Where it sees walls, columns and openings enclosing a building-sized area, that area is a drawing. Only drawings go on to step 3, so title blocks, notes, legends, schedules and empty margins are never predicted. Two plans on one sheet are two drawings.
3. **Label every pixel.** A U-Net with a ConvNeXt encoder (`segmentation_models_pytorch` and `timm`, ImageNet-pretrained) gives every pixel a class: background, space, wall, window, door, column or stair. Large drawings are predicted in overlapping 1024 px tiles, blended so seams never show, and optionally averaged with a mirrored pass.
4. **Build the takeoff from the prediction** (`vectorize.py`), the way a person reads a plan:
   - **Walls.** The predicted wall pixels are split into straight runs, one rectangle per run.
   - **Openings are gaps in those walls.** From each free end of a wall run, the wall's band is followed until wall starts again. A gap of door-to-window width is a candidate opening, as wide as the gap and as thick as the wall. The network decides what fills it:
     - pixels it labels window in the gap make it a window;
     - pixels it labels door in or beside the gap make it a door, since the swing is drawn beside it;
     - anything else is a plain opening, which is not reported.
     A door is therefore always in a wall, never on its swing.
   - **Rooms are what the walls enclose.** Walls, columns and openings form a barrier. Each region it closes off from the edge of the drawing is a candidate room, and it is kept when the network calls most of it room. A room stops at wall faces, so it can't run out through a gap into the margin.
   - **Columns** are rectangles and **stairs** polygons. Each object's score comes from the network's probabilities.
5. **Name things from the page text.** Rooms take the largest name printed inside them, and doors and windows the nearest mark, such as `D2` or `Wn-04`. Scans are read with Tesseract when it is installed.

Training uses weighted cross-entropy plus Dice loss, so thin, rare classes count as much as big rooms. Tiles containing doors, windows, columns and stairs are oversampled. Augmentations cover rotation, mirroring, zoom from 0.45× to 1.6×, pen weight, blur, scan noise, JPEG artefacts, colour and greyscale.

## Layout

```
config.yaml              every setting: classes, resolution, model, training, prediction, post-processing
data/sources/            drawings: <name>.pdf or <name>.png/.jpg/.tif
data/labels/             takeoff JSON, one file per drawing, same name: <name>.json (what training reads)
data/drafts/             engine output kept by the backend, to correct into labels (never trained on as it is)
data/tiles/              made by takeoff-prepare
runs/<name>/             checkpoints (best.pt, last.pt) and log.csv
takeoff_model/
  synth.py               synthetic drawing sheets with exact labels
  prepare.py             render, rasterize labels, cut tiles, split by document
  data.py                tile dataset and augmentation
  net.py                 the network
  train.py               training loop
  predict.py             drawing finder, tiled prediction, naming, takeoff JSON out
  vectorize.py           pixels → walls, openings, rooms, columns, stairs
  evaluate.py            precision, recall, F1 and IoU against labels
  coco.py                labels ↔ COCO, for CVAT / Label Studio / Roboflow
  serve.py               QuantTakeoff-compatible API
```

## Setup

```bash
cd model
uv sync                     # Python 3.15, PyTorch, CUDA on Linux, Metal on Apple silicon
uv run pytest               # six pipeline tests, no GPU needed
```

Install Tesseract (`brew install tesseract`, or `apt install tesseract-ocr`) to name rooms on scanned drawings.

## The labels

A label file is takeoff JSON:

```json
{"pages": [{
  "page_index": 0,
  "image_size": {"width": 4961, "height": 3509},
  "scale": {"denominator": 100},
  "detections": [
    {"class_name": "wall",  "polygon": [[2017, 2022], [2034, 2022], [2034, 2921], [2017, 2921]]},
    {"class_name": "door",  "polygon": [[2086, 1097], [2190, 1097], [2190, 1113], [2086, 1113]]},
    {"class_name": "space", "polygon": [[2166, 2801], [2273, 2801], [2273, 3045], [2166, 3045]], "name": "TOILET"}
  ]
}]}
```

Polygons are in the pixels of an image of `image_size`; the page is rendered at any size and the labels follow. Labels saved from the Takeoff AI editor are in PDF points, with the page's size in points as `image_size`. Every listed page is used. A page listed with no detections teaches the model that the page holds nothing, which is useful for sections and schedules. `scale.denominator` sets the training resolution; leave it out and the printed scale is read from the PDF.

**What to draw.** Be consistent: consistency matters more than anything else.

| Class | Draw | Do not draw |
|---|---|---|
| `wall` | Each straight run of cut wall, as a rectangle face to face. Stop at openings. | Openings, dimension lines, hatching outside the wall, legend swatches. |
| `door` | The opening in the wall: its width by the wall's thickness. | The swing arc or leaf. Archways with no door. |
| `window` | The opening in the wall, as for doors. | Sills or frames drawn outside the wall. |
| `space` | Each room's floor, wall face to wall face, stopping at its openings. | Wall thickness. Courtyards, porches and paving with no walls. Furniture and text inside are part of the room. |
| `column` | Each column's outline. | |
| `stair` | The stair's outline, treads included. | |

The post-processing assumes these rules: rooms end at walls and openings, and openings are gaps in walls. Labels drawn another way will fight it.

## Getting training data

Training data comes from three places.

**1. Synthetic sheets** (`takeoff-synth`) have exact labels and cost nothing, so they teach the basics before you have real data. They're built to look like real sheets:
- one or two buildings per sheet, with L- and U-shaped footprints;
- solid, hatched, cross-hatched, outlined, grey and coloured walls, with thin partitions;
- swing, double, sliding and archway openings, and windows with sills;
- tinted and tiled rooms, with furniture by room type;
- grids, dimension chains, tags, section marks, dashed overhead lines and paving;
- title blocks, notes, legends and schedules.

They are still simpler than real drawings, so they get the model started; they are not enough on their own.

**2. Your own uploads.** With `TRAINING_DATA_DIR` set, as both compose files do, the backend keeps training data in the model's data folder:

| When | What it keeps | Where |
|---|---|---|
| A job finishes | The uploaded PDF | `data/sources/<jobId>.pdf` |
| A job finishes | What the engine returned for each plan page | `data/drafts/<jobId>.json` |
| You press **Save corrections for training** | Your corrected layers: classes from the extracted layers, polygons in PDF points | `data/labels/<jobId>.json` |

The button is in the extraction panel, after you've imported the layers and corrected them. Saving again replaces the label. Drafts are never trained on; only what you correct and save becomes a label.

In development, `compose.dev.yaml` mounts `model/data` directly. In production, `compose.yaml` keeps a `training-data` volume; copy it out before training.

**3. CVAT**, for labelling in bulk. It's free, and Label Studio and Roboflow work the same way. Export the pages that have no label yet, pre-filled with the engine's drafts:

```bash
uv run takeoff-coco export --out data/coco --unlabelled --drafts
```

- Create a CVAT project with the six classes.
- Upload `data/coco/images`, then import `data/coco/annotations.json` as COCO 1.0.
- Correct everything, and export as COCO 1.0.

Then bring the corrections back:

```bash
uv run takeoff-coco import path/to/exported/annotations.json      # writes data/labels/<name>.json
```

Images are rendered at the training resolution, and their names (`<document>__p<page>.png`) link them back to their labels. Don't rename them.

## How to train it well

Accuracy comes from the data far more than the network:
- **Many real pages,** from many offices and drawing styles.
- **Labelled consistently** to the rules above.
- **At the right scale.**

A bigger model or more epochs won't fix missing or inconsistent labels. Follow these stages in order.

### 0. Set aside a test set first

Before training on anything real, pick **20 to 30 plan pages** that the model will never train on:
- from offices and styles that are not in your training data;
- including a few scans or photos if your users upload them.

Label them carefully and keep them in `data/test/` and `data/test-labels/`, outside `data/sources` and `data/labels`. These pages are how you know whether anything you do helps. Never train on them, and never tune settings on anything else.

### 1. Pretrain on synthetic sheets

```bash
uv run takeoff-synth --count 3000          # about 1 s a sheet
uv run takeoff-prepare
uv run takeoff-train --name synth-v1
```

On synthetic validation the score should pass 0.85 mean IoU within the run. If it doesn't, something is wrong with the setup, not the data.

### 2. Collect real pages

Aim for these amounts of labelled plan pages:

| Pages | What to expect |
|---|---|
| 50 | The first clear gains over synthetic-only. Good for checking that everything works. |
| 300 | A useful model on drawing styles like the ones it has seen. |
| 1,000 or more, from many offices | Close to commercial quality across styles. |

Variety matters as much as count: different offices, CAD packages, wall styles, scales, scans and phone photos. Ten pages from each of 30 offices beat 300 pages from one office.

Include some **non-plan pages** (sections, elevations, schedules) labelled with no detections. They teach the model to leave those pages empty. About 1 in 10 pages is enough.

### 3. Label

- **Start from drafts, never from scratch.** Predict with your best model so far, or use the drafts the backend kept, then correct them in the editor or in CVAT. Correcting a draft typically takes 15 to 45 minutes a page, depending on its size. Drawing from scratch takes several times longer.
- **Check consistency.** Before training, look over a sample of pages labelled by each person against the rules table. Inconsistent labels cost more accuracy than missing ones.
- **Label whole pages.** Anything left unlabelled on a labelled page teaches the model that it is background.

### 4. Fine-tune on real data

Mix about one synthetic sheet for every four real pages:

```bash
uv run takeoff-synth --count 250     # or keep a share of the first set
uv run takeoff-prepare
```

Set `train.lr: 1.0e-4` in `config.yaml` (a third of pretraining), and `train.epochs: 40`. Then:

```bash
uv run takeoff-train --name real-v1 --resume runs/synth-v1/best.pt --fine-tune
```

### 5. Read the training log

`runs/<name>/log.csv` has one row per epoch: the loss, the validation `score` (mean IoU over space, wall, window and door) and each class's IoU. `best.pt` is kept at the best score.

| What you see | What it means | What to do |
|---|---|---|
| Score still climbing at the last epoch | Training too short | Raise `train.epochs` and resume from `last.pt`. |
| Score flat well before the end, training loss still falling | Not enough data, or labels the model can't agree with | More pages, or check label consistency. More epochs won't help. |
| One class far below the rest (often door or window) | Too few examples, or drawn inconsistently | Label more pages rich in that class. Check that openings are labelled as gaps in walls. |
| Score swings a lot from epoch to epoch | Validation set too small | Label more pages. The validation set is chosen by document name (`data.val_fraction`). |
| Out of memory | Card too small | Use the settings for smaller GPUs below. |

### 6. Evaluate on the test set

```bash
for f in data/test/*.pdf; do
  uv run takeoff-predict --checkpoint runs/real-v1/best.pt "$f" --out "preds/$(basename "${f%.*}").takeoff.json"
done
uv run takeoff-evaluate --truth data/test-labels --pred preds
```

This reports:
- pixel IoU per class;
- precision, recall and F1 per countable class, matched at IoU 0.5;
- total wall length against the truth.

Score QuantTakeoff's output for the same pages the same way. Your model has caught up when it matches theirs on your test set; until then, the gap tells you which classes need more labels.

The post-processing settings (`vectorize.*` and `predict.locate`) can be tuned against the test set without retraining. Tune them only if you keep a second labelled set for the final check, or you'll be tuning to the test set.

### 7. Repeat

1. Find the test-set and production pages where the model does worst.
2. Label more pages like them; the drafts the backend keeps are ready to correct.
3. Fine-tune again from the last `best.pt`.

Three or four rounds close most of the gap. Each round needs fewer new pages than the last.

### Settings by GPU

| GPU memory | Settings |
|---|---|
| 24 GB (RTX 4090, A5000, L4, A10G) | The defaults: `tu-convnext_small`, `batch_size: 8`, `crop: 768`. |
| 8 to 12 GB | `model.encoder: tu-convnext_tiny`, `train.batch_size: 4`, `train.crop: 640`. |
| 40 GB or more, with over 1,000 labelled pages | `model.encoder: tu-convnext_base`. |

Changing the encoder means training from synthetic again; fine-tuning works only between checkpoints with the same encoder.

### Training on a rented GPU

There's no need to buy a training GPU. Rent a 24 GB card by the hour from RunPod, Lambda, Vast.ai or a cloud provider; it is usually under a dollar an hour, but check current prices.

```bash
# on your machine: send the code and data (data/tiles can be rebuilt there instead)
rsync -av --exclude .venv --exclude data/tiles --exclude runs model/ gpu-box:model/

# on the GPU machine
cd model && curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync
uv run takeoff-prepare
nohup uv run takeoff-train --name real-v1 --resume runs/synth-v1/best.pt --fine-tune > train.log 2>&1 &
tail -f train.log

# back on your machine when it's done
rsync -av gpu-box:model/runs/real-v1/ model/runs/real-v1/
```

Measure the first epoch's time (it's in `log.csv`) and multiply by the epoch count before leaving it running. A 60-epoch synthetic run of the default model should take a few hours on a 24 GB card. A Mac is fine for tests and small experiments, not for the real model.

## Using the model

**On a file:**

```bash
uv run takeoff-predict --checkpoint runs/real-v1/best.pt plan.pdf --pages 1,3 --scale 100 --out plan.takeoff.json
cd ../annotate-pdf-from-json && uv run annotator.py ../model/plan.takeoff.json ../model/plan.pdf plan.annotated.pdf
```

Each page's `plans` lists the drawings found on it, as boxes.

**From Python:**

```python
from takeoff_model.predict import Predictor
result = Predictor("runs/real-v1/best.pt").predict("plan.pdf", pages=[0], denominator=100)
```

**In Takeoff AI, in place of QuantTakeoff.** Start the server:

```bash
TAKEOFF_MODEL_API_KEY=choose-a-key uv run takeoff-serve --checkpoint runs/real-v1/best.pt --port 8000
```

Then point the backend at it in `server-python/.env`, and restart the worker:

```
QUANTTAKEOFF_BASE_URL=http://host.docker.internal:8000/v1.1
QUANTTAKEOFF_API_KEY=choose-a-key
```

Nothing else changes. The backend still:
1. classifies pages;
2. sends each floor plan as a one-page PDF;
3. polls for the result;
4. merges in its checked scale;
5. keeps a training draft of every job.

The server runs one job at a time on its GPU; to scale, run one server per GPU behind a load balancer.

The network and its rendering come from the checkpoint. How it predicts and post-processes (`predict` and `vectorize` in `config.yaml`, or `--config`) is read when the server starts, so those settings change without retraining.

## Settings worth changing

All are in `config.yaml`.

| Setting | When |
|---|---|
| `render.mm_per_px: 10` | Thin windows are missed. It renders finer and costs memory; retrain after changing it. |
| `predict.tta: false` | Halves prediction time, for a little accuracy. |
| `predict.locate` | Remove it to predict whole pages, if drawings are being missed. Lower `min_plan_m2` for small plans. |
| `vectorize.opening_evidence` | Lower it if real openings are dropped; raise it if gaps are wrongly called doors. |
| `vectorize.seal_gap_mm` | Raise it if rooms leak through wide openings; lower it if open-plan spaces are cut in two. |
| `vectorize.room_min_probability` | Lower it if enclosed rooms are dropped. |
| `vectorize.min_*` | The smallest wall run, opening, room, column and stair kept. |

## Machines

| Job | Recommended | Minimum |
|---|---|---|
| Labelling, data prep, tests | Any laptop | Any laptop |
| Training | One NVIDIA GPU with 24 GB (RTX 4090, A5000, L4, A10G), 32 GB RAM, 8+ CPU cores for augmentation, 200 GB SSD | 8 to 12 GB GPU with `tu-convnext_tiny` and a smaller batch |
| Serving, GPU | NVIDIA T4 or L4, 4 vCPU, 16 GB RAM: a page in seconds | |
| Serving, CPU | 8 vCPU, 16 GB RAM: tens of seconds to minutes per large page | 4 vCPU, 8 GB |

Prediction holds the class probabilities of one drawing at a time, about 0.3 GB for an A1 sheet, plus the model, so allow 2 to 4 GB of RAM per concurrent job. An Apple-silicon Mac runs everything on its GPU (`mps`); a small ResNet-18 model predicts a page there in seconds.

## Licences and data

- **Libraries.** `segmentation_models_pytorch` (MIT), `timm` (Apache 2.0), Albumentations (MIT), PyTorch (BSD) and PyMuPDF (AGPL, or commercial). PyMuPDF is already in the backend: check that its licence fits how you ship.
- **ImageNet encoder weights.** Their licences vary by model; ConvNeXt's are MIT.
- **Public datasets.** CubiCasa5K and most public floor-plan datasets are, as far as known, licensed for non-commercial use only. Use them for experiments, not a shipped model, unless their licence allows it.
- **QuantTakeoff output.** While the backend uses QuantTakeoff, the drafts it keeps are QuantTakeoff's output; each draft records its `engine`. Don't train on them, or use them as a starting point for labels, without checking QuantTakeoff's terms. They would also teach the model QuantTakeoff's mistakes. Drafts from your own model are yours.
- **Customer drawings.** Uploaded drawings kept for training belong to your customers. Make sure your terms allow keeping them for training, or turn it off by leaving `TRAINING_DATA_DIR` unset.
