# Extraction pipeline

How Takeoff AI turns an uploaded drawing set into rooms, walls, doors and windows that the frontend can draw, a person can check and edit, and the takeoff can price. This covers the backend in `server-python`, from the moment a PDF lands at `POST /extract` to the moment the job record says `completed`, and how the frontend uses the result.

Code paths are relative to `server-python/app`.

---

## 1. Overview

```
POST /extract ──► job record (Redis) ──► extract-document
                                              │  one task per page
                                              ▼
                                        extract-page × N      (2 in parallel)
                                              │  each page published as it finishes
                                              ▼
                                      finalize-extraction ──► output JSON + webhook
```

Every page runs the same stages, independently of every other page:

| Stage | Module | Model? | What it produces |
|---|---|---|---|
| Parse | `extractor/cache.py` | no | Text spans (native or OCR), vector lines, vector cut regions, image coverage |
| Classify | `extractor/classify.py` | vision | Is this a plan? Which part of the sheet is the plan? What scale does it state? |
| Check scale | `extractor/calibrate.py` | no | The stated scale confirmed or overruled by the printed dimensions, or measured when none is stated |
| Rasterise | `extractor/raster.py` | no | The page as ink masks, at a resolution set in real millimetres |
| Walls | `extractor/walls.py` | no | A wall mask, the bridges across its openings, the enclosed pockets |
| Rooms | `extractor/rooms.py` | no | Room polygons from the pockets, named and checked by the drawing's labels |
| Openings | `extractor/openings.py` | no | Doors and windows with measured widths and the rooms either side |
| Wall segments | `extractor/walls.py` | no | Each wall as a rectangle with length and thickness |
| Label | `extractor/labelling.py` | vision | Names for unlabelled rooms; non-rooms and non-openings rejected; door or window confirmed |
| Nodes | `extractor/rooms.py` | no | Shared corner nodes with grid references |

### The approach, and why

The goal is a takeoff that works on plans from any office, whatever its drawing conventions. Three things make that hard: offices draw walls differently (solid black, hatched between two lines, two hollow lines, an embedded raster image), a PDF's stated scale is often wrong for the sheet it is on, and labels are inconsistent or absent.

The pipeline splits the work by what each tool is good at:

- **Geometry is deterministic.** Every point the frontend draws comes from pixel or vector measurement, so it lands on the drawing and can be checked. A vision model is poor at placing points precisely and is never asked for a coordinate.
- **Meaning comes from the vision model.** Which part of the sheet is the plan, whether a pocket is a bedroom or a stair flight, whether a gap is a door or a window. The model answers about numbered marks drawn over the page ("set-of-marks" prompting), and its answers can rename or reject but never move anything.
- **The drawing's own annotations are the referee.** Printed room areas decide between wall readings and between outlines; printed dimensions check the scale; door and window tags name openings; grid marks name the nodes.

Three design rules keep it general:

- **Everything geometric is measured in millimetres and converted through the sheet's scale**, so a 1:50 and a 1:200 drawing get the same thresholds.
- **Walls are read from pixels, whatever drew them**, with vector cut regions as an extra reading when the PDF has them. Vector CAD exports, hatched BIM output, image-backed sheets and scans all end up as the same kind of mask.
- **Rooms come from walls, not labels.** An unlabelled room, or a scan whose text cannot be read, still yields rooms. Labels name and check them.

The wall mask is the single seam where a trained segmentation model would plug in later (see §11). Everything downstream of it would stay the same.

Every model step degrades to the deterministic result when the model is unreachable or no key is set.

---

## 2. Intake

### 2.1 `POST /extract` (`routes/extract.py`)

Multipart form with a `document` file and an optional `notificationUrl`.

1. A UUID becomes the job id.
2. The upload streams to `<DOCUMENT_STORAGE_DIR>/<jobId>/source.pdf` (`storage.py`). The first kilobyte must contain `%PDF-`; the PDF spec allows the header anywhere in that range. Anything else is rejected with `400 Document must be a PDF`. Anything over `MAX_UPLOAD_BYTES` (50 MB) is rejected with `413`.
3. `notificationUrl`, when given, must parse as an HTTP URL or the request fails with `400`.
4. A `JobRecord` is written to Redis with status `queued` (`jobs.py`, `models.py`).
5. `extract-document` is enqueued on Celery with the job id as its task id.
6. The response is `202` with `jobId`, `statusUrl`, `documentPath` and the queue name.

### 2.2 The job record (`models.py`)

Stored at `<REDIS_JOB_PREFIX><jobId>` as JSON, with a TTL of `JOB_TTL_SECONDS` (7 days by default) that is refreshed on every write. Field names are snake_case in Python and camelCase on the wire.

```
JobRecord
  id, status, createdAt, updatedAt, documentPath, notificationUrl?, attempts
  result?: ExtractionResult
  error?, completedAt?

ExtractionResult
  partial            true until finalize runs
  accepted?          true when at least one page is a plan
  document           { pageCount, pagesProcessed, planPageCount, pdfPath }
  pages[]            one entry per page, in page order (see §6)
  totals             { rooms, walls, doors, windows } summed over plan pages
  rejection?         { reason, message } when accepted is false
  tokenUsage?        LLM calls and token totals, set at finalize
  extractedAt?
```

Updates go through `update_job`, which runs a Redis `WATCH` transaction: read, mutate, write, retry on conflict. This is what lets several page tasks on several workers append to the same record without overwriting one another.

---

## 3. Task orchestration (`tasks.py`)

Celery with Redis as broker and result backend. The result backend exists only so the chord can count finished pages; results expire after an hour. Job state the frontend reads lives in the job record, never in the Celery backend.

### 3.1 `extract-document`

Runs once per job.

1. Loads the job record. A missing record is `UnrecoverableError` and fails without retry.
2. Resolves `documentPath` (`storage.resolve_document`): it must sit under `DOCUMENT_STORAGE_DIR` and exist. Either failure is unrecoverable.
3. Reads the page count with PyMuPDF.
4. Sets the job to `processing`, increments `attempts`, and writes an empty `ExtractionResult` with `pageCount` filled in. The frontend starts showing "Reading page 0 of N" from this moment.
5. Builds a chord: a header of `extract-page` signatures, one per page, and `finalize-extraction` as the body.

Any other exception marks the job `failed` and re-raises so Celery retries with exponential backoff: `QUEUE_ATTEMPTS` attempts, starting at `QUEUE_BACKOFF_DELAY_MS`.

### 3.2 `extract-page`

Runs once per page, possibly in parallel across worker processes (`WORKER_CONCURRENCY`).

1. Calls `pipeline.process_page` with a fresh `TokenTracker`.
2. On exception, retries up to `QUEUE_ATTEMPTS - 1` times with doubling delay. When retries are exhausted the page is recorded as skipped with the error in `skippedReason`, so one bad sheet never fails the document.
3. Publishes the page into the job record via `ExtractionResult.add_page`, which keeps pages sorted by number and recomputes `pagesProcessed`, `planPageCount` and `totals`.
4. Returns the page and its token usage for the chord body.

### 3.3 `finalize-extraction`

Runs once every page task has returned.

1. Merges the per-page token usage into one `tokenUsage` block.
2. Adds any page that somehow was not published (defensive; normally none).
3. Sets `partial: false`, `accepted` (true when `planPageCount > 0`), `extractedAt`, and a `rejection` when nothing was a plan.
4. Marks the job `completed`.
5. Writes `<EXTRACTION_OUTPUT_DIR>/<jobId>.json` (`output_writer.py`) and logs a summary.
6. POSTs the job to the webhook, if one was given or `NOTIFICATION_WEBHOOK_URL` is set (`notifier.py`). Three attempts, 15 second timeout, failures logged and never raised.

Queueing: a second upload while one is running is simply queued. Two worker processes run by default (`WORKER_CONCURRENCY=2`), so two pages from any mix of documents are processed at once and the rest wait their turn. A dense A1 page peaks near 1 GB, so the setting is bounded by the memory Docker is given: two fits the default 4 GB, three needs about 6 GB. A process that grows past 1.5 GB is recycled after its task (`worker_max_memory_per_child`). Tasks are acknowledged after completion (`task_acks_late`), so a worker crash mid-page puts the page back on the queue.

---

## 4. Per-page pipeline (`extractor/pipeline.py`)

`process_page(pdf_path, page_index, tracker)` returns one page dict. The stages in order:

### 4.1 Parse (`extractor/cache.py`)

PyMuPDF opens the page once and collects everything downstream needs into an `ExtractionCache`:

- **Text spans.** Every non-empty span from `page.get_text("dict")`, with its bounding box, centre (`cx`, `cy`) and font size. Coordinates are PDF points with a top-left origin, the same space pdf.js reports at scale 1, so the frontend overlay needs no transform.
- **OCR for scans.** A page with fewer than 20 native spans and images covering at least 20% of it is a scan. Tesseract runs on it through PyMuPDF (`OCR_LANGUAGE`, `OCR_DPI`, default `eng` at 300 dpi) and its words become the text spans, in the same coordinates. The page is marked `textSource: "ocr"`. When Tesseract is not installed the page is read without text and a warning is logged; walls and rooms are still found, unnamed.
- **Vectors, in one pass** (`get_drawings` re-walks the content stream on every call):
  - **Line segments**: every stroked `l` item, with endpoints, length, stroke colour and width. Used for hairline erasure and for measuring dimensions.
  - **Cut regions**: closed regions a CAD export uses for cut elements. A clip path that hatching is drawn inside is how ArchiCAD and Revit draw a cut wall; a path filled darker than light grey is how AutoCAD draws a solid one. Clip paths larger than a twentieth of the page are viewports and are ignored. Cut regions are one of the wall readings in §4.6.
- **Image coverage.** The share of the page covered by embedded images. Used by the prefilter so an image-backed drawing is not mistaken for an empty page.
- **Full text** and a `title_block_text` view of its first 1200 characters.

`parse_pdf_to_cache` raises `PdfPageError` for a missing file, an unopenable or password-protected PDF, or an out-of-range page.

### 4.2 Classify (`extractor/classify.py`)

Two steps. The first is free; the second costs one vision call.

**Prefilter.** A page is skipped without a model call when:
- it has fewer than 40 line segments **and** images cover less than 20% of it (nothing drawn), or
- the first 400 characters of its text match a non-plan title (`SCHEDULE`, `SPECIFICATION`, `BILL OF QUANTITIES`, `REVISION HISTORY`, `DRAWING LIST/REGISTER`, `CONTENTS`, `NOTES SHEET`, `LEGEND SHEET`) and contain no plan vocabulary, or
- it reads as a section or elevation: the title says `SECTION`, `ELEVATION` or `DETAIL` without `PLAN`, or the text carries three or more datum level marks (`AGL`, `DPC`, `FFL`, `SSL`, `SFL`, `RL`, `NGL`).

A skipped page gets `classifiedBy: "prefilter"` and a `skippedReason`.

**Scale from text.** The whole page text is searched for `1 : N`.

**Vision call.** The page is rendered at 150 dpi and sent with a short summary to `LLM_VISION_MODEL`. The model returns JSON:

```
is_plan, plan_type, not_plan_reason, scale, scale_ratio, plan_regions, confidence, notes
```

`plan_regions` lists a box around each floor-plan drawing on the sheet, on a 0–1000 scale of the page, excluding the title block, notes, legends, tables and any section or detail drawn beside the plan. A sheet with two plans side by side returns two boxes. `plan_region_mask` widens each box by 2% of the page on every side, so a box drawn tight against an outer wall never cuts it off, and the wall reading (§4.6) is limited to those boxes. Without boxes the whole page is read. This is what keeps title-block cells and legend swatches from becoming rooms.

`is_plan` only stands when `plan_type` is a measurable plan type (`ground_floor_plan`, `upper_floor_plan`, `floor_plan`, `site_plan`, `roof_plan`, `setting_out_plan`, `unknown`). A reply of `is_plan: true, plan_type: section` is treated as not a plan.

**Fallback.** If the model is unreachable, the key is missing, or the reply is not JSON, the page is kept as a plan of `plan_type: "unknown"` with `confidence: "low"`, read over the whole page. A page is never dropped because of a bad response.

All model calls are made with Qwen's step-by-step reasoning off (`LLM_THINKING=false`). Reading a page and naming numbered marks does not need it, and it made each call 4–10× slower.

### 4.3 Check the scale (`extractor/calibrate.py`)

A stated scale is only true for the sheet it was printed on. A drawing set up at 1:100 on A3 and exported on an A1 sheet still says "1:100" but measures 1:50. A scan of a print shrinks everything. Some sheets state no scale at all. One wrong scale silently corrupts every quantity, so it is measured, not trusted.

**Readings.** Every dimension string (`2400`, `7,500`, `3.25`) labels a dimension line drawn beside it. For each one:

- **On vector pages** (as many line segments as dimension strings), the dimension line is the nearest horizontal segment within two text heights of the text, off its centre line (a text box includes descender space, so the line may sit inside it), running under the text's centre. Its ends are where another segment crosses it (a tick, arrow or witness line) or where a collinear piece of the chain stops, nearest each side of the text. Vertical text is handled by swapping x and y.
- **On scans**, the same search runs on the rendered page: the longest inked row under the text, ended where ink leaves the line sideways.

`value ÷ length` gives one reading of millimetres per point, in the drawing's own unit.

**Clustering.** Chained dimensions, grid lines crossing a chain and odd placements make single readings noisy. Readings within 3% of each other form a cluster, and the largest cluster is the measurement. Drawings are dimensioned in millimetres, centimetres or metres, so the unit is whichever makes the measurement agree with the stated scale; with no stated scale, millimetres unless another unit lands clearly nearer a scale draughtsmen use.

**Decision.** How much agreement is needed depends on what the measurement is used for:

| Outcome | Needs | `scale.source` | Confidence |
|---|---|---|---|
| Stated scale confirmed (within 6%) | 4 readings and 15% of all | `stated` | high |
| Stated scale overruled | 6 readings and 40% of all | `dimensions` | medium |
| No scale stated; measured | 5 readings and 30% of all | `dimensions` | medium |
| Stated scale kept unchecked | anything less | `stated` | from the classifier |
| Nothing stated, nothing measured | — | `assumed` (1:100) | low |

Every outcome adds a note to the page saying what was found, for example *"The sheet says 1:100 but 32 of 75 printed dimensions measure 1:50.1; the sheet appears to have been resized, so the measured scale is used."*

### 4.4 Grid (`extractor/grid.py`)

Deterministic. Construction drawings carry setting-out marks: letters across the top, numbers down the side. The detector takes every one- or two-character alphanumeric span, clusters them into horizontal and vertical bands within 4 points, merges repeated labels, and keeps the band with the most distinct marks per axis, provided it has at least three. `grid_ref_for_point(grid, x, y)` describes a point as `C/4`, `C~D/4~5` between axes, or the one axis that exists.

### 4.5 Rasterise (`extractor/raster.py`)

`PageRaster.from_cache(cache, scale_ratio)` renders the page once:

1. **Resolution** is set so one pixel is about 15 mm of real world (`MM_PER_PX`), never finer than 3 px/pt and capped at 20 megapixels. An A3 sheet at 1:100 renders at 2.35 px/pt; an A1 sheet at 1:150 hits the cap at about 2.7 px/pt.
2. **No anti-aliasing.** A 0.25 pt grey hatch line rendered with smoothing fades into the paper; rendered crisp it keeps its colour.
3. **Two masks**, both with every text span's box (padded by a point) erased, so a label never becomes a wall or seals a room:
   - `ink`: every channel below 200. Grey hatching counts; pale room fills (a light yellow) do not. Walls are read from this.
   - `barrier`: every channel below 150, with hairline vectors (≤ 0.5 pt) also erased when the sheet draws its walls some other way. Used to trace a room around its own label and to look inside openings.

`raster.mm(x)` converts real millimetres to points, `raster.px(x)` to pixels, `raster.m2(pixels)` a pixel count to square metres.

`polygon(region)` turns a pocket into an outline: open and close with a 300 mm kernel to remove slivers and fill notches, take the largest external contour, simplify with Douglas–Peucker at 2 points, then orthogonalise. **Orthogonalise** drops edges under 100 mm (diagonals under 1000 mm), closes the jog between a horizontal and a vertical run into a corner, merges collinear runs, and gives every horizontal edge a single `y` and every vertical edge a single `x`. Long diagonals are kept: a splayed room keeps its splay.

### 4.6 Walls (`extractor/walls.py`)

A wall on a plan is a band of ink 80–600 mm thick that runs straight for a while. Everything else drawn is thinner (dimension lines, furniture outlines, tile grids), shorter or curved (chairs, door swings, fixtures), or fatter (stair flights, hatched voids, desks). Walls are read in four steps, all in real millimetres:

1. **Close** the ink so a hatched or hollow wall becomes a solid band.
2. **Open** with an 80 mm disk so anything thinner than a wall disappears.
3. **Drop the fat.** Anything a 600 mm disk fits inside is not a wall. Fat regions of 4 m² or more are kept aside as **solids**. To one office a large hatched area is a stair core, void or shaft that bounds the rooms around it; to another it is a tiled floor that *is* a room. So every reading is scored twice, with solids as room boundaries and without, and the better fit wins.
4. **Keep the straight.** Only ink that forms a straight run of at least 600 mm, horizontally, vertically or on either diagonal, survives.

**Readings.** How far to close in step 1 depends on the office: hatching is a few hundred millimetres apart, a hollow wall needs more. So several readings are built:

| Reading | Suits |
|---|---|
| ink closed at 120 mm | Solid and finely hatched walls |
| ink closed at 260 mm | Coarsely hatched and hollow double-line walls |
| vector cut regions | CAD exports that mark cut walls with hatch clips or fills (only when the PDF has them) |
| vector cut regions + ink at 120 mm | Exports whose thin partitions are not hatched |

Each reading (in both solids variants) is limited to the plan regions from §4.2, sealed, and scored, and the best one wins. The page notes say which, for example `Walls read as: ink closed at 120 mm, hatched areas solid`.

**Sealing.** A door or window is a gap in a wall line. For every free pixel, the nearest wall pixel to each side along its row is measured (a running maximum, so it is one vectorised pass), and the same along its column. The pixel is bridged when the gap is short and at least one side is a horizontal (or vertical) run of wall at least 700 mm long, longer than any wall is thick, so the side of a thick cross wall never counts as a run. Collinear pieces of one wall line are bridged up to 3.2 m apart; a wall end and a cross wall or solid up to 1.6 m. The bridges are then opened slightly to drop single-pixel strays.

**Pockets.** The free space left inside walls, solids and bridges is labelled into connected pockets. A pocket is a candidate room when it does not touch the page edge, is between 1.5 and 400 m², and fills at least 30% of its bounding box (a sliver between hatch strokes does not).

**Score.** Each pocket adds its area, capped at 40 m² so one merged hall cannot outscore the rooms it swallowed. Each printed room area that lands in a pocket within 15% adds 30. So on a labelled drawing the labels decide; on an unlabelled one, the reading that resolves into the most room-sized space wins.

**Wall segments.** For the walls layer the chosen mask is split into horizontal runs, vertical runs and whatever is left (splayed walls). Each connected run becomes its minimum-area rectangle: four corners, `lengthMm` and `thicknessMm`. Runs shorter than 300 mm or thinner than 60 mm are dropped. Corners where two walls meet are counted in both.

### 4.7 Rooms (`extractor/rooms.py`)

Rooms are the pockets of the chosen wall reading. Labels then name and check them.

**Tags.** A room tag is a name with its printed area beside it. `pair_room_tags`:

- collects area spans matching `12 m²`, `12.5m2`, `12,5 sqm`, `12 sq m`, or `12 M` whose superscript ² is a separate span,
- collects name-shaped spans of 2–31 characters, upper or mixed case (`BEDROOM 2`, `W.C.`, `Pool Office`). A short code (`P1`, `D5`, `C D`) is a mark, not a name, unless it is a known room word like `WC`; otherwise a finish tag beside an area label would steal it,
- pairs them nearest-first within 30 points or 3.5 lines of the name's font size, whichever is larger (large lettering sits further from its area), each span used once,
- accepts a name with no area only from a known vocabulary.

**Per pocket:**

| Tags inside | Result |
|---|---|
| none | A room named by the most prominent name-like text inside it (largest font, not a dimension, grid mark or opening tag), or `ROOM` for the model to name. `confidence: medium`. |
| one | The tag's name. When the pocket's outline is within tolerance of the printed area (12%, or the 0.5 m² the figure was rounded to), `high` within 5%, else `medium`. Otherwise the tag is re-traced (below). |
| several | An open plan when the outline matches the sum of their printed areas: one room named `KITCHEN / DINING`. Otherwise a wall the reading missed: each tag is re-traced. |

Tags that fall in no pocket are re-traced too.

**Re-tracing around a label.** The barrier mask is closed with radii of 0, 150, 300, 450 and 600 mm in turn, the pocket around the tag is filled at each, and the first outline within 5% of the printed area wins; the best within tolerance is kept otherwise. This is the previous label-driven method, kept as the second opinion: it rescues rooms where the wall reading merged two rooms or lost a wall. These rooms carry `source: "label"`; pocket rooms carry `source: "walls"`.

**Fallback.** A tag that traces neither way becomes a square of its printed area with `confidence: low`. Open porches end up here because they have no fourth wall.

**Nodes.** After labelling (§4.9), `share_nodes` clusters all room corners within 2 points into page-level nodes `n1, n2, …` with grid references, so adjoining rooms reference the same node at a shared corner.

### 4.8 Doors and windows (`extractor/openings.py`)

Two routes, because drawings mark openings in two ways.

**Marked openings.** Most plans tag every opening: `Dr-02`, `D1`, `D/3`, `DOOR 2`, `Wn-01`, `W3`, `WIN-2`, `WINDOW 1`. The tag says what it is; the wall says where and how wide. The nearest axis-aligned edge of a traced room within 1200 mm is the host wall. A 300 mm band across that edge is sampled every half point along it: a position is wall when the band holds at least 100 mm of dark ink. A door shows nothing, a window only its sill lines. Working outwards from the tag up to 1500 mm either side, the first non-wall run 400–3000 mm long is the opening. This route matters for windows: many offices draw the frame dark enough that a window never appears as a gap in the wall reading, only as a thinning of the wall band.

**Unmarked openings.** Every bridge from §4.6 is a candidate, its minimum-area rectangle giving width and direction. A bridge also closes gaps that are not openings (a wall stopping short, a corridor end), so a candidate is reported only with evidence:

- **window**: two or more glazing lines running at least 70% of the gap's width;
- **door**: the leaf and its swing drawn beside it. From either jamb, on either face, for one leaf or two, the open leaf (a straight line from the hinge, at least 70% inked), the quarter circle it sweeps (at least 60% inked at 0.85, 1.0 or 1.1 of the leaf) and clear floor inside the arc (at most 25% inked at 0.55 of the leaf). A round table or a chair has the circle but not the leaf.

An unmarked opening within 1 m of a marked one is the same opening and is dropped.

**Fallback.** A tag with no gap found is placed at the tag's projection on its wall, at 900 mm (door) or 1200 mm (window), with `confidence: low`.

Each opening carries `polyline` (two points along the wall), `widthMm`, `reference` (when tagged), `roomIds` (the rooms either side, read from the pockets 400 mm beyond each wall face), `exterior` (one side is not a room), `gridRef`, `confidence` and `notes`.

### 4.9 Label and verify (`extractor/labelling.py`)

The geometry is fixed by now. What pixels cannot tell is meaning. The plan is cropped to the components, rendered at 2400 px on its long side, and every room is outlined and numbered `R1…` in blue and every opening `O1…` in magenta. The model is asked, per number, for a room's name and whether it is a room at all, and whether an opening is a door, a window or not an opening. Sheets with more than 90 marks are split into spatial tiles and labelled in several calls, so every image stays legible.

What the answers may change:

- An unnamed room (`ROOM`) takes the model's name, with the note *"Named by the vision model"*. A room named from the drawing's text keeps it.
- A room the model says is not a room is dropped, unless the drawing tags it with a printed area. Then it is kept with a note: the drawing outranks the model.
- An untagged opening takes the model's door or window call, or is dropped when the model says it is not an opening. A tagged opening is never changed.

Without a reachable model everything passes through unchanged.

---

## 5. Units and coordinates

- All geometry is in **PDF points**, top-left origin, matching pdf.js at scale 1. The frontend copies points straight into its overlay.
- **Scale** is the checked scale of §4.3. One point is 25.4 / 72 mm of paper, covering `ratio` mm of real world. The frontend derives its millimetre calibration from the first plan page with a usable ratio.
- `areaM2` is the figure printed on the drawing. `measuredAreaM2` is the shoelace area of the returned polygon at that scale.

---

## 6. Output shape

One entry in `result.pages` per page. A skipped page:

```json
{
  "pageNumber": 6,
  "isPlan": false,
  "planType": "schedule",
  "classifiedBy": "vision",
  "textSource": "native",
  "pageSize": { "width": 1190.6, "height": 841.9 },
  "skippedReason": "door and window schedule",
  "components": [],
  "tokenUsage": { "calls": [ … ], "totals": { … } }
}
```

A plan page. Components are ordered back to front: rooms, walls, then doors and windows.

```json
{
  "pageNumber": 1,
  "isPlan": true,
  "planType": "ground_floor_plan",
  "classifiedBy": "vision",
  "textSource": "native",
  "pageSize": { "width": 1190.5, "height": 841.9 },
  "scale": { "text": "1:100", "ratio": 100.0, "confidence": "high", "source": "stated", "dimensionsChecked": 57 },
  "grid": { "columns": [{ "ref": "A", "x": 320.2 }], "rows": [{ "ref": "1", "y": 125.8 }] },
  "nodes": [{ "id": "n1", "point": [321.4, 266.1], "gridRef": "A/4" }],
  "components": [
    {
      "id": "room-002", "layer": "rooms", "type": "room", "name": "BEDROOM", "source": "walls",
      "polyline": [[327.2, 162.4], [384.1, 162.4], [384.1, 218.6], [327.2, 218.6]],
      "nodes": ["n5", "n6", "n7", "n8"], "areaM2": 13.0, "measuredAreaM2": 12.84,
      "gridRef": "A~B/2~3", "confidence": "high",
      "notes": ["Enclosed by walls; area within 1% of the printed 13.0 m²"]
    },
    {
      "id": "wall-012", "layer": "walls", "type": "wall",
      "polyline": [[320.1, 158.2], [452.3, 158.2], [452.3, 164.6], [320.1, 164.6]],
      "lengthMm": 4664, "thicknessMm": 225, "confidence": "medium",
      "notes": ["Wall band read from the drawing"]
    },
    {
      "id": "door-003", "layer": "doors", "type": "door", "reference": "Dr-02",
      "polyline": [[497.0, 266.3], [519.5, 266.3]], "widthMm": 794,
      "roomIds": ["room-007", "room-011"], "exterior": false,
      "gridRef": "E~F/4", "confidence": "high",
      "notes": ["Width measured from the gap in the wall beside the mark Dr-02"]
    }
  ],
  "counts": { "rooms": 31, "walls": 74, "doors": 16, "windows": 16 },
  "notes": ["Stated scale confirmed by 57 of 189 printed dimensions", "Walls read as: ink closed at 120 mm"],
  "tokenUsage": { "calls": [ … ], "totals": { "prompt_tokens": 6900, "completion_tokens": 800, "total_tokens": 7700 } }
}
```

The final document wraps the pages:

```json
{
  "partial": false,
  "accepted": true,
  "document": { "pageCount": 8, "pagesProcessed": 8, "planPageCount": 2, "pdfPath": "…" },
  "pages": [ … ],
  "totals": { "rooms": 33, "walls": 111, "doors": 32, "windows": 16 },
  "tokenUsage": { "calls": [ … ], "totals": { … } },
  "extractedAt": "2026-11-17T09:12:44+00:00"
}
```

While pages are still arriving, `partial` is `true`, and `pagesProcessed`, `totals` and `tokenUsage` grow with each page.

---

## 7. What the frontend does with it

`frontend/lib/annotations/extraction-import.ts` turns each plan page's components into annotation layers, one per component class across all pages, painted back to front: **Rooms**, **Walls**, then **Doors** and **Windows** on top, so an opening is always visible and is what a click picks when it overlaps a room or wall. Rooms and walls are area shapes; doors and windows are line shapes whose length is the opening width. The workspace imports rooms, walls, doors and windows on every poll that brings a new page and once more on completion; user-drawn layers are untouched, extraction layers are replaced.

The import also records **snap guides** per page: the grid axes and the extracted corner nodes. Every extracted shape is editable with the select tool: drag a vertex (snapping to nodes, other vertices, grid lines and neighbouring vertices' x or y; Alt to drag freely), drag the body to move the shape, double-click a vertex to remove it or an edge to add one, Delete to remove the shape. Edited shapes are what the takeoff totals.

The extraction panel lists every **plan page** with its type, scale and component counts. Clicking one jumps the viewer to that page; the page on screen is highlighted.

---

## 8. Tuning knobs

Values in millimetres are real-world and converted through the sheet scale; values in points are text-layout or raster artefacts and stay fixed.

| Constant | Module | Default | Effect |
|---|---|---|---|
| `MM_PER_PX` / `PX_PER_PT` / `MAX_PIXELS` | raster | 15 / 3.0 / 20 M | Render resolution in real mm per pixel, its ceiling, and its cap. |
| `DARK_MAX_CHANNEL` / `INK_MAX_CHANNEL` | raster | 150 / 200 | What counts as dark (barrier) and as ink (walls). |
| `CLEANUP_MM` / `MIN_EDGE_MM` / `MIN_DIAGONAL_EDGE_MM` | raster | 300 / 100 / 1000 | Outline cleanup and squaring. |
| `MIN_THICKNESS_MM` / `MAX_THICKNESS_MM` | walls | 80 / 600 | What thickness of ink band is a wall. |
| `FILL_MM` | walls | 120, 260 | Closing radii of the ink readings. |
| `MIN_RUN_MM` | walls | 600 | Straight run a wall must make. |
| `AXIS_RUN_MM` | walls | 700 | Run that marks a wall as horizontal or vertical when sealing. |
| `SAME_LINE_GAP_MM` / `CROSS_GAP_MM` | walls | 3200 / 1600 | Widest gap bridged along one wall line / to a cross wall. |
| `MIN_SOLID_M2` | walls | 4 | Fat ink at least this big is a solid that bounds rooms. |
| `MIN_ROOM_M2` / `MAX_ROOM_M2` / `MIN_BOX_FILL` | walls | 1.5 / 400 / 0.3 | Which pockets are candidate rooms. |
| `ROOM_SCORE_CAP_M2` / `MATCHED_TAG_SCORE` / `TAG_TOLERANCE` | walls | 40 / 30 / 0.15 | How wall readings are scored. |
| `TAG_PAIR_RADIUS_PT` / `TAG_PAIR_FONT_LINES` | rooms | 30 / 3.5 | How far a name may sit from its area. |
| `CLOSING_RADII_MM` | rooms | 0…600 | Radii tried when re-tracing around a label. |
| `AREA_TOLERANCE` / `AREA_ROUNDING_M2` / `GOOD_ENOUGH` | rooms | 0.12 / 0.5 / 0.05 | Acceptance against the printed area, and the `high` threshold. |
| `MAX_TAG_TO_EDGE_MM` / `SEARCH_ALONG_MM` | openings | 1200 / 1500 | How far a tag may be from its wall, and how far along it the gap is sought. |
| `BAND_MM` / `WALL_DARK_MM` | openings | 300 / 100 | Scan band across the wall, and the ink that still counts as wall. |
| `MIN_WIDTH_MM` / `MAX_WIDTH_MM` | openings | 400 / 3000 | Plausible opening widths. |
| `GLAZING_COVER` / `MIN_GLAZING_LINES` | openings | 0.7 / 2 | Window evidence. |
| `ARC_COVER` / `LEAF_COVER` / `ARC_CLEAR` | openings | 0.6 / 0.7 / 0.25 | Door-swing evidence. |
| `CLUSTER_TOLERANCE` / `CONFIRM` / `SUPPLY` / `OVERRULE` | calibrate | 0.03 / (4, 15%) / (5, 30%) / (6, 40%) | How readings cluster and how much agreement each decision needs. |
| `REGION_MARGIN` | classify | 0.02 | Padding added to each plan region. |
| `MAX_MARKS` / `IMAGE_LONG_SIDE_PX` | labelling | 90 / 2400 | Marks per labelling image, and its size. |
| `LLM_THINKING` | settings | false | Qwen's step-by-step reasoning on model calls. |
| `WORKER_CONCURRENCY` | settings | 2 | Pages processed in parallel; each can peak near 1 GB on an A1 sheet. |

---

## 9. Scanned and rasterised drawings

| Stage | On a scan |
|---|---|
| Parse | No native text. OCR supplies words with positions; tiny area tags and superscripts are often missed. No vectors, so no cut regions. |
| Classify | Unchanged: the model sees the same rendered page and reports the plan regions. |
| Scale | Dimensions are measured on the rendered page instead of from vectors. Noisier, so a resized scan's scale is only overruled when the dimensions agree strongly. |
| Walls, rooms | Unchanged: the mask was always pixels. Unnamed pockets are named by the model. |
| Openings | Tagged when OCR read the tags; otherwise from bridges with a drawn swing or glazing. |
| Frontend | Snapping to extracted nodes and grid works; there is no vector line-work to snap to. |

Scan resolution is the real limit. A letter-size scan of an A3 plan leaves a 225 mm wall about 6 pixels thick and a 2 mm label below what Tesseract reads.

---

## 10. Known limits

- **Furniture drawn as heavily as walls** (some "new works" layers, bold office desks) passes the thickness and straightness tests and splits rooms. The labels and re-tracing rescue labelled rooms; unlabelled ones split. A trained wall segmenter is the real fix (§11).
- **Hatched floor zones** (tiled corridors, wet areas) can read as solids at the coarse closing radius, so the room in them is lost unless it is labelled and re-traced.
- **Windows drawn solid** are found only through their tags; untagged, they read as wall.
- **Unmarked openings need a drawn swing or glazing.** Sliding doors, archways and openings without either are not reported unless tagged.
- **Wall segments double-count corners** and break at every opening; total wall length is an upper bound, not a centreline measure.
- **Diagonal and curved walls** are kept as walls but not sealed across their openings, and curved walls are lost.
- **One scale per page.** A sheet with two views at different scales is read at one scale.
- **Rooms on scans** are only as good as the scan; see §9.

---

## 11. Toward a trained model

The wall mask is a single swappable seam: `walls._interpretations` yields candidate masks and everything after it (sealing, scoring, pockets, rooms, openings, segments, labelling) only needs a mask. A segmentation network trained on floor plans would plug in as one more reading, scored against the others by the same rules, and would win where the hand-built readings fail (furniture drawn like walls, unusual hatching).

What it needs is labelled data in this product's own drawing styles. Every edit a user makes to an extracted room, wall or opening in the workspace is exactly that label. Storing the corrected layers against the page image builds the training set as the product is used; a few hundred corrected pages is a realistic point to train a first model and compare it on the benchmark below.

---

## 12. Testing

```bash
cd server-python
uv run pytest tests/test_extraction.py   # extraction on real drawings, no API key needed
uv run pytest                            # everything, including the API and task lifecycle
```

The test environment blanks `DASHSCOPE_API_KEY`, so every model step takes its fallback and the tests are deterministic. `tests/test_extraction.py` covers:

- **The reference sheet** (page 1 of `PROJECT_CCVE_without_first_two_pages.pdf`): walls alone, with no labels, enclose at least 20 rooms totalling 150–300 m²; all 27 tags are accounted for; at least 22 outlines land within tolerance of the printed area; at least 18 rooms come from walls and are four-corner rectangles; nodes are shared; wall segments are wall-thick; all 16 door and 16 window tags are placed, at least 24 with a measured width; the page result is ordered back to front.
- **Generality**: the ArchiCAD export's stated 1:100 is overruled to 1:50 by its centimetre dimensions; the unscaled, unlabelled sheet's scale is measured at about 1:50 and it still yields wall-bounded rooms and door swings.
- **Model steps**: plan regions become a padded mask; labelling names rooms, rejects non-rooms and non-openings, and never overrules a tagged room or opening.
- **Helpers**: prefilter, orthogonalise, tag pairing (including mixed case and split superscripts) and OCR (skipped without Tesseract, so it runs in the Docker image).

The full test set in `tests/files` is the benchmark for generality. §13 records the latest run through the real API.

---

## 13. Results on the test set

Run on 2026-11-17 through the real API (Docker, two workers, `qwen3.7-plus` with thinking off): 6 PDFs, 51 pages, no failed page, 624 seconds in all, about 227,000 tokens.

| PDF | What it is | Pages: plans / skipped | Result |
|---|---|---|---|
| `PROJECT_CCVE_without_first_two_pages.pdf` | Raster wall tiles under vector text, labelled | 3 / 5 | Ground floor: 31 rooms, 22 of 27 printed areas matched, 16 doors and 16 windows from their tags (27 measured), scale confirmed by 57 dimensions. Sections, schedules skipped. |
| `CCVE GROUND FLOOR PLAN-main.pdf` | Same office, single sheet | 1 / 0 | Same quality: 32 rooms, 22 of 27 printed areas matched, scale confirmed. |
| `1725445830689-annex-ii-drawings.pdf` | ArchiCAD, A1 at 1:150, office building, no printed areas | 4 / 5 | 61–103 rooms per floor named from the drawing and the model, 20–58 unmarked doors found by their swings, scale confirmed by 34–36 dimensions. All elevations and sections skipped by the model. Rooms are `medium`: no printed areas to check. |
| `Eng copy.pdf` | No scale stated, no labels, solid black walls | 2 / 0 | Page 1: scale measured at 1:49.5 from 8 dimensions; three bedrooms and five door swings. Page 2: no scale measurable, 1:100 assumed and flagged; walls only. |
| `36472-1._architectural_final.pdf` | ArchiCAD, A1, labelled, dimensions in cm, open-plan offices | 9 / 5 | Stated 1:100 correctly overruled to 1:50 on every floor-plan sheet. Rooms weak: 0–5 printed areas matched per sheet, most rooms low-confidence placeholders, because offices are divided by notional lines and furniture drawn in the wall pen. 41–56 opening tags placed per sheet, a quarter measured. Two detail sheets were classified as plans by the model. |
| `arch-emma-storey-building.pdf` | Phone-app scan, letter size, no text layer | 6 / 11 | OCR ran. Every schedule, section, elevation and 3D page skipped. Plans weak: 4 rooms per floor, leaky. The stated 1:100 is really about 1:195 (the print was shrunk) and the scan's dimensions are too small to measure reliably, so the scale could not be corrected. |

**Reading the table.** The pipeline is strong where walls are drawn as walls (solid, raster, or cleanly hatched) and labels or dimensions exist to check against. It is weak on open-plan offices whose furniture is drawn as heavily as walls, and on small, resized scans. In both weak cases the output is flagged `low` rather than presented as measured, and the engineer corrects it in the editor. Those corrections are the data a trained wall segmenter (§11) would learn from.
