# Extraction pipeline

How Takeoff AI turns an uploaded drawing set into room outlines the frontend can draw and price. This covers the backend in `server-python` only: from the moment a PDF lands at `POST /extract` to the moment the job record says `completed`.

Code paths are relative to `server-python/app`.

---

## 1. Overview

```
POST /extract ──► job record (Redis) ──► extract-document
                                              │  one task per page
                                              ▼
                                        extract-page × N
                                              │  each page published as it finishes
                                              ▼
                                      finalize-extraction ──► output JSON + webhook
```

Every page runs the same five stages, independently of every other page:

| Stage | Module | What it produces |
|---|---|---|
| Parse | `extractor/cache.py` | Text spans (native or OCR), vector lines, image coverage, page size |
| Classify | `extractor/classify.py` | Is this a plan? At what scale? |
| Rasterise | `extractor/raster.py` | A wall mask in pixel space |
| Rooms | `extractor/rooms.py` | Room polygons, shared corner nodes, grid references |
| Openings | `extractor/openings.py` | Door and window spans with measured widths |

The result the frontend reads is one JSON document per job: a list of pages, each with its components and nodes. Rooms, doors and windows are extracted today. Walls and columns will follow on the same raster foundation.

The pipeline is written to work across drawing offices and output styles, not for one sample. Three design rules carry that:

- **Everything geometric is measured in millimetres and converted through the sheet's own scale**, so a 1:50 and a 1:200 drawing get the same door-bridging, cleanup and wall-thickness thresholds.
- **Walls are read from pixels, whatever drew them.** Vector CAD exports, hatched BIM output and scanned paper all end up as the same mask.
- **The drawing's own annotations are the oracle.** Printed room areas pick the door-bridging radius; door and window tags say what an opening is; grid marks name the nodes. No thresholds are tuned to a particular office's conventions.

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
  totals             { rooms: n } summed over plan pages
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

Queueing: a second upload while one is running is simply queued. With concurrency 1 its pages wait behind the first document's pages. Tasks are acknowledged after completion (`task_acks_late`), so a worker crash mid-page puts the page back on the queue.

---

## 4. Per-page pipeline (`extractor/pipeline.py`)

`process_page(pdf_path, page_index, tracker)` returns one page dict. The stages in order:

### 4.1 Parse (`extractor/cache.py`)

PyMuPDF opens the page once and collects everything downstream needs into an `ExtractionCache`:

- **Text spans.** Every non-empty span from `page.get_text("dict")`, with its bounding box, centre (`cx`, `cy`) and font size. Coordinates are PDF points with a top-left origin, the same space pdf.js reports at scale 1, so the frontend overlay needs no transform.
- **OCR for scans.** A page with fewer than 20 native spans and images covering at least 20% of it is a scan. Tesseract runs on it through PyMuPDF (`OCR_LANGUAGE`, `OCR_DPI`, default `eng` at 300 dpi) and its words become the text spans, in the same coordinates. The page is marked `textSource: "ocr"`. Room names, dimensions and the `1:N` scale survive OCR; the tiny printed area tags often do not, so rooms on scans are traced at a fixed bridging radius with `confidence: medium`. When Tesseract is not installed the page is read without text and a warning is logged.
- **Line segments.** Every `l` item from `page.get_drawings()`, with endpoints, length, stroke colour and stroke width. One pass, because `get_drawings` re-walks the content stream on every call.
- **Image coverage.** The share of the page covered by embedded images, clipped to the page rectangle. Used by the prefilter so an image-backed drawing is not mistaken for an empty page.
- **Full text** and a `title_block_text` view of its first 1200 characters.

`parse_pdf_to_cache` raises `PdfPageError` for a missing file, an unopenable or password-protected PDF, or an out-of-range page. PyMuPDF returns `None` for a bad page index instead of raising; `pdf_utils.load_page` turns that into an error with a message.

### 4.2 Classify (`extractor/classify.py`)

Two steps. The first is free; the second costs one vision call.

**Prefilter.** A page is skipped without a model call when:
- it has fewer than 40 line segments **and** images cover less than 20% of it (nothing drawn), or
- the first 400 characters of its text match a non-plan title (`SCHEDULE`, `SPECIFICATION`, `BILL OF QUANTITIES`, `REVISION HISTORY`, `DRAWING LIST/REGISTER`, `CONTENTS`, `NOTES SHEET`, `LEGEND SHEET`) and contain no plan vocabulary (`PLAN`, `FLOOR`, `BEDROOM`, `SCALE`, and so on), or
- it reads as a section or elevation: the title says `SECTION`, `ELEVATION` or `DETAIL` without `PLAN`, or the text carries three or more datum level marks (`AGL`, `DPC`, `FFL`, `SSL`, `SFL`, `RL`, `NGL`). Plans do not print a column of levels; sections and elevations always do.

A skipped page gets `classifiedBy: "prefilter"` and a `skippedReason` saying why.

**Scale from text.** The whole page text is searched for `1 : N`. The view title often sits last in the text stream, so searching only the title block missed scales that were printed plainly.

**Vision call.** The page is rendered at 150 dpi and sent with a short summary (title block sample, span and line counts, image coverage, sample labels, detected scale) to `LLM_VISION_MODEL`. The model returns JSON:

```
is_plan, plan_type, not_plan_reason, scale, scale_ratio, confidence, notes
```

Sections, elevations and details are drawings but cannot be measured in plan, so the prompt tells the model to mark them `is_plan: false`. The answer is also checked: `is_plan` only stands when `plan_type` is one of `ground_floor_plan`, `upper_floor_plan`, `floor_plan`, `site_plan`, `roof_plan`, `setting_out_plan` or `unknown`. A reply of `is_plan: true, plan_type: section` is treated as not a plan.

**Fallback.** If the model is unreachable, the key is missing, or the reply is not JSON, the page is kept as a plan of `plan_type: "unknown"` with `confidence: "low"` and a note explaining the fallback. A page is never dropped because of a bad response. The scale found in the text fills in whatever the model left as `unknown`.

In fallback mode every page with geometry becomes a plan, including elevation sheets, and those often carry room tags. Set `DASHSCOPE_API_KEY` to get real classification.

### 4.3 Grid (`extractor/grid.py`)

Deterministic, no model. Construction drawings carry setting-out marks: letters across the top, numbers down the side. The detector:

1. Takes every text span whose text is one or two alphanumeric characters (`GRID_LABEL`). Two characters is long enough for `12`, too short for a dimension like `3600`.
2. Clusters those spans by `cy` within 4 points to find horizontal bands, and by `cx` to find vertical bands.
3. Within a band, merges repeated labels (a mark usually appears at both edges of the sheet) by averaging their positions.
4. Keeps the band with the most distinct marks for each axis, provided it has at least three. A single band cannot be both axes; whichever has more marks wins.

`grid_ref_for_point(grid, x, y)` describes a point as `C/4` when it sits within 10 points of both axes, `C~D/4~5` when it falls between them, or just the one axis that exists.

### 4.4 Rasterise (`extractor/raster.py`)

Walls on a drawing may be vectors, hatching between face pairs, or pixels in an embedded image. The sample drawing in `tests/files` is the last kind: its walls are a raster tiled 80 times under the vector text and dimensions. Working in pixel space handles all three the same way.

`PageRaster.from_cache(cache, scale_ratio)` builds the wall mask:

1. **Render** the page at `PX_PER_PT = 3` pixels per point (216 dpi), reduced for large sheets so the mask never exceeds 30 megapixels. An A3 landscape sheet becomes about 3572 × 2526 pixels; an A0 sheet renders at roughly 1.9 px/pt instead.
2. **Threshold.** A pixel is a barrier when every channel is below 150. Black and dark-red walls count; bright red dimension lines, light grey ghosted layers and white paper do not.
3. **Erase text.** Each text span's bounding box, padded by one point, is cleared. A room name can never split its own room or seal a doorway.
4. **Erase hairlines, when safe.** Vector lines with stroke width at or below 0.5 points are drawn over in white, three pixels wide, so dimension lines, grid lines and leaders cannot cut rooms in half. This only happens when the sheet draws its walls some other way: either an embedded image covers at least 20% of the page, or the heaviest common stroke on the page is at least 1.5× the hairline width. A CAD export drawn entirely in 0.25 pt lines keeps every line, because its walls are hairlines too, and the colour threshold alone separates walls from coloured dimension lines.

The raster also knows the sheet's scale. `raster.mm(x)` converts real-world millimetres to points on this page, and every geometric constant below is stated in millimetres.

`closed(radius)` returns the mask with gaps narrower than twice the radius bridged, using a morphological close with a square kernel. Results are cached per radius since every room on the page reuses them.

`region_around(seed, radius)` flood-fills the free space from a point. If the seed itself lands on a barrier (a label glyph the padding missed, say), it searches up to 6 points around it for the nearest free pixel. The result is a binary mask of the pocket the seed sits in. A seed outside the building fills the whole sheet; the caller catches that by area.

`polygon(region)` turns a pocket into an outline:

1. **Open then close** with a 300 mm square kernel (8.5 points at 1:100). The open removes slivers narrower than that, such as the fill poking into a doorway or along a door leaf. The close fills notches of the same size. No real room or corridor is narrower than 300 mm, so rooms survive at any scale.
2. **Contour.** The largest external contour, so islands inside the room (fixtures, text that was not a span) are ignored.
3. **Simplify** with Douglas–Peucker at 2 points.
4. **Orthogonalise** (`orthogonalize`), described next.

**Orthogonalise.** A traced outline is nearly square but not exactly: corners are a pixel off, door swings leave stair-steps. The routine makes it exact without inventing geometry:

1. Repeatedly drop short edges, merging their endpoints to the midpoint. Axis-aligned edges under 100 mm go; diagonal edges under 1000 mm go, since a short diagonal is a stair-step left by a door swing and a real splayed wall is longer than a door's radius.
2. Repeatedly drop a vertex whose two edges run in the same direction (both horizontal or both vertical).
3. Give every horizontal edge a single `y` (the mean of its ends) and every vertical edge a single `x`. A corner takes `y` from its horizontal side and `x` from its vertical side, so the two never fight.
4. Long diagonals are left alone. A splayed room keeps its splay.

### 4.5 Rooms (`extractor/rooms.py`)

**Tags.** A room tag is a name with its printed area beside it. `pair_room_tags`:

- collects area spans matching `12 m²`, `12.5m2`, `12,5 sqm` or `12 sq m` (whole span, so `3600` never matches),
- collects name-shaped spans: an uppercase label of 2 to 31 characters, allowing digits, dots, slashes, apostrophes and hyphens (`BEDROOM 2`, `W.C.`, `VISITORS' TOILET`),
- pairs them nearest-first within 30 points, each span used once. Nearest-first matters: matching every name to its closest area independently attaches one room's area to its neighbour. The name is whatever sits beside the area, so `SCULLERY` reads as readily as `BEDROOM`,
- accepts a name with no area only from the known vocabulary: residential (`BEDROOM`, `KITCHEN`, `LOUNGE`, `ENSUITE`, `VERANDA`…), circulation (`LOBBY`, `CORRIDOR`, `STAIRS`, `LIFT`…) and institutional (`OFFICE`, `WARD`, `CLASSROOM`, `LABORATORY`, `WORKSHOP`, `PLANT ROOM`…), with an optional `MASTER`, `GUEST`, `MAIN`, `FAMILY` or `VISITORS'` prefix. A bare uppercase label with no area is as likely to be a note as a room.

**Tracing.** For each tag, `_trace` tries closing radii of 0, 150, 300, 450 and 600 mm in order (a radius bridges a gap of twice its size, so 600 mm closes a 1.2 m double door). At each radius it fills the pocket around the tag and converts the pixel count to square metres using the page scale:

```
m² = pixels / PX_PER_PT² × (25.4 / 72 × scale_ratio)² / 1 000 000
```

A pocket over 120 m² means the fill leaked outside the building and is discarded. Otherwise the pocket's error against the printed area is computed, the best so far is kept, and the loop stops early once the error is at or under 5%. This is why no door-width threshold has to be guessed: the drawing's own area annotation picks the radius.

The error is judged on the final squared-up polygon, not the raw fill, so what is reported is what was checked. A traced outline is accepted when its error is within 12% of the printed figure, or within the half square metre the figure was rounded to, whichever is looser. Printed areas are whole numbers, so a 2 m² toilet measured at 1.6 m² is within rounding. A tag with no printed area is traced at a 300 mm radius and accepted whenever the pocket is bounded.

**Fallback.** When nothing traces, the room becomes a square of the printed area centred on the tag (or a 3 m nominal square when there is no area), with `confidence: "low"` and a note saying the outline could not be traced. Open porches end up here because they have no fourth wall.

**Confidence.**

| Confidence | Meaning |
|---|---|
| `high` | Traced; within 5% of the printed area |
| `medium` | Traced; within tolerance but further than 5%, or no printed area to check against |
| `low` | Could not be traced; placeholder square |

**Nodes.** After every room is traced, `_share_nodes` clusters all corners within 2 points of each other into page-level nodes `n1, n2, …`. Adjoining rooms therefore reference the same node at a shared corner. Each node carries a grid reference. Each room lists its node ids in polyline order.

### 4.6 Doors and windows (`extractor/openings.py`)

Every opening on a plan carries a reference tag beside it. The tag says what the opening is; the wall says where and how wide.

**Tags.** Spans matching `Dr-02`, `D1`, `D/3`, `DOOR 2`, `Wn-01`, `W3`, `WIN-2`, `WINDOW 1`, with an optional trailing letter. A leading `D` means door, `W` means window.

**Host wall.** The nearest axis-aligned edge of any traced room, within 1200 mm of the tag. Rooms that fell back to a placeholder square are not used as hosts. The tag's projection onto that edge is where the search starts.

**Gap scan.** The wall mask is sampled across a 300 mm band centred on the edge, perpendicular to it, at every half point along the edge. A position is wall when the dark run across the band is at least 100 mm; a door shows nothing, a window shows only its sill lines (30–60 mm). Starting from the projection and working outwards up to 1500 mm either side, the first non-wall run between 400 and 3000 mm wide is the opening. Its ends are the opening's span, and its length is the width.

**Fallback.** When no gap is found, the opening is placed at the tag's projection with a nominal width (900 mm door, 1200 mm window) and `confidence: low`, so an assumed width is never mistaken for a measured one. A tag with no room wall within reach is placed at the tag itself.

Each opening carries `polyline` (two points on the wall line), `widthMm`, `reference`, `hostRoomId`, `gridRef`, `confidence` and `notes`. Openings are not yet given shared nodes.

Openings are only sought on pages with at least one traced room, so a door schedule that lists `Dr-01`… produces nothing.

On the sample drawing all 16 door and 16 window tags are found, 27 with a measured width. The unmeasured ones sit where a door swing has left the room edge diagonal, so no axis-aligned host edge runs past them.

---

## 5. Units and coordinates

- All geometry is in **PDF points**, top-left origin, matching pdf.js at scale 1. The frontend copies points straight into its overlay.
- **Scale** is the drawing's own `1:N`. One point is 25.4 / 72 mm of paper, covering `N` mm of real world. The frontend derives its millimetre calibration from the first plan page with a usable ratio.
- `areaM2` is the figure printed on the drawing. `measuredAreaM2` is the shoelace area of the returned polygon at that scale. They differ by the printed rounding plus whatever the outline missed.

---

## 6. Output shape

One entry in `result.pages` per page. A skipped page:

```json
{
  "pageNumber": 6,
  "isPlan": false,
  "planType": "schedule",
  "classifiedBy": "vision",
  "pageSize": { "width": 1190.6, "height": 841.9 },
  "skippedReason": "door and window schedule",
  "components": []
}
```

A plan page:

```json
{
  "pageNumber": 1,
  "isPlan": true,
  "planType": "ground_floor_plan",
  "classifiedBy": "vision",
  "pageSize": { "width": 1190.5, "height": 841.9 },
  "scale": { "text": "1:100", "ratio": 100.0, "confidence": "high" },
  "grid": {
    "columns": [{ "ref": "A", "x": 320.2 }, { "ref": "B", "x": 391.1 }],
    "rows":    [{ "ref": "1", "y": 125.8 }, { "ref": "2", "y": 165.5 }]
  },
  "nodes": [
    { "id": "n1", "point": [321.4, 266.1], "gridRef": "A/4" }
  ],
  "tokenUsage": { "calls": [ … ], "totals": { "prompt_tokens": 1830, "completion_tokens": 120, "total_tokens": 1950 } },
  "components": [
    {
      "id": "door-003",
      "layer": "doors",
      "type": "door",
      "reference": "Dr-02",
      "polyline": [[497.0, 266.3], [519.5, 266.3]],
      "widthMm": 794,
      "hostRoomId": "room-021",
      "gridRef": "E~F/4",
      "confidence": "high",
      "notes": ["Width measured from the gap in the wall"]
    },
    {
      "id": "room-001",
      "layer": "rooms",
      "type": "room",
      "name": "KITCHEN",
      "polyline": [[585.5, 167.8], [640.0, 167.8], [640.0, 210.3], [585.5, 210.3]],
      "nodes": ["n5", "n6", "n7", "n8"],
      "areaM2": 6.0,
      "measuredAreaM2": 5.73,
      "gridRef": "G~H/2~3",
      "confidence": "high",
      "notes": ["Outline traced from the drawing; area within 4% of the printed 6.0 m²"]
    }
  ],
  "counts": { "rooms": 27, "doors": 16, "windows": 16 },
  "notes": []
}
```

Every page also carries `textSource` (`native` or `ocr`) and its own `tokenUsage`. The document-level `tokenUsage` is the sum of the pages' and is updated every time a page lands, not only at the end.

The final document wraps the pages:

```json
{
  "partial": false,
  "accepted": true,
  "document": { "pageCount": 8, "pagesProcessed": 8, "planPageCount": 1, "pdfPath": "…" },
  "pages": [ … ],
  "totals": { "rooms": 27, "doors": 16, "windows": 16 },
  "tokenUsage": { "calls": [ … ], "totals": { "prompt_tokens": 14640, "completion_tokens": 960, "total_tokens": 15600 } },
  "extractedAt": "2026-11-15T09:12:44+00:00"
}
```

While pages are still arriving, `partial` is `true`, `accepted` and `extractedAt` are absent, and `document.pagesProcessed`, `totals` and `tokenUsage` grow with each page. The frontend re-imports its extraction layers every time that count moves.

---

## 7. What the frontend does with it

`frontend/lib/annotations/extraction-import.ts` turns each plan page's components into annotation layers, one layer per component class across all pages. Each shape keeps its page number, its polyline, its name, its reference and its grid reference. Rooms are area shapes; doors and windows are line shapes whose length is the opening width. The workspace imports on every poll that brings a new page and once more on completion; user-drawn layers are untouched, extraction layers are replaced.

The import also records **snap guides** per page: the grid axes and the extracted corner nodes. Every extracted shape is editable with the select tool:

- drag a vertex handle to move that corner; it snaps to extracted nodes, other shapes' vertices, grid lines, and the neighbouring vertices' x or y, so a corrected wall stays straight and lands on the drawing's own geometry. Hold Alt to drag freely;
- drag the shape body to move the whole shape;
- double-click a vertex to remove it, or an edge to add one;
- Delete removes the shape.

Snapping works on scanned drawings too, because the targets are the extraction's nodes and grid, not the PDF's vectors. What is not available on a scan is snapping to arbitrary line-work; a future step is to have the backend return wall-face candidates from the raster so the frontend can snap to any wall.

---

## 8. Tuning knobs

Values in millimetres are real-world and converted through the sheet scale; values in points are raster or text-layout artefacts and stay fixed.

| Constant | Module | Default | Effect |
|---|---|---|---|
| `PX_PER_PT` / `MAX_PIXELS` | raster | 3.0 / 30 M | Render resolution and its cap for large sheets. |
| `DARK_MAX_CHANNEL` | raster | 150 | What counts as a wall pixel. Lower excludes more. |
| `HAIRLINE_PT` / `HEAVY_STROKE_RATIO` | raster | 0.5 / 1.5 | Hairline erasure threshold, and the heavier stroke that must exist for erasure to be safe. |
| `CLEANUP_MM` | raster | 300 | Open/close kernel. Must stay below the narrowest real room. |
| `MIN_EDGE_MM` / `MIN_DIAGONAL_EDGE_MM` | raster | 100 / 1000 | Edges shorter than these are merged away. |
| `SIMPLIFY_PT` | raster | 2.0 | Contour simplification tolerance. |
| `AXIS_TOLERANCE_DEG` | raster | 12 | How far from axis an edge can lean and still be squared. |
| `TAG_PAIR_RADIUS_PT` | rooms | 30 | How far a name may sit from its area (a text-layout distance). |
| `CLOSING_RADII_MM` / `RADIUS_WITHOUT_AREA_MM` | rooms | 0…600 / 300 | Door-bridging radii tried per room. |
| `AREA_TOLERANCE` / `AREA_ROUNDING_M2` | rooms | 0.12 / 0.5 | Acceptance against the printed area. |
| `MAX_ROOM_M2` | rooms | 120 | Above this the fill is assumed to have leaked outside. |
| `NODE_TOLERANCE_PT` | rooms | 2.0 | Corners closer than this share a node. |
| `MAX_TAG_TO_EDGE_MM` / `SEARCH_ALONG_MM` | openings | 1200 / 1500 | How far a tag may be from its wall, and how far along it the gap is sought. |
| `BAND_MM` / `WALL_DARK_MM` | openings | 300 / 100 | Scan band across the wall, and the dark run that still counts as wall. |
| `MIN_WIDTH_MM` / `MAX_WIDTH_MM` | openings | 400 / 3000 | Plausible opening widths. |
| `MIN_LINE_SEGMENTS` / `MIN_IMAGE_COVERAGE` | classify | 40 / 0.2 | Prefilter thresholds. |
| `MIN_LEVEL_MARKS` | classify | 3 | Datum marks that make a page read as a section. |
| `MIN_NATIVE_SPANS` / `MIN_SCAN_IMAGE_COVERAGE` | cache | 20 / 0.2 | When a page is treated as a scan and OCR runs. |
| `OCR_LANGUAGE` / `OCR_DPI` | settings | eng / 300 | Tesseract language and resolution. |

---

## 9. Scanned and rasterised drawings

The pipeline runs on scans. What changes:

| Stage | On a scan |
|---|---|
| Parse | No native text. OCR supplies words with positions. Room names, dimensions, door/window tags and the `1:N` scale come through; the tiny printed area tags usually do not. |
| Prefilter | Passes on image coverage, since there are no vectors. |
| Classify | Unchanged: the vision model sees the same rendered page. |
| Rasterise | Unchanged: the mask was always pixels. Hairline erasure is skipped (no vectors), so thin dimension lines drawn on the scan become barriers. The 300 mm cleanup and door-bridging tolerate most of them, but a dimension line running the full length of a room can split it. |
| Rooms | Traced at the fixed 300 mm radius when the area tag was not read, with `confidence: medium`. |
| Openings | Unchanged when the tags were OCR'd. |
| Frontend | Snapping to extracted nodes and grid works; there is no vector line-work to snap to beyond that. |

Scan quality is the real limit. A 200 dpi scan of an A3 sheet renders a 3.5 pt label as about 10 pixels tall, which Tesseract reads for words and misses for superscripts. Scans at 300 dpi or better behave like the sample.

What would make scans first-class: a second OCR pass at higher resolution on the strip under each room name to recover the area tag; using the model to read the scale when no `1:N` is found; and inferring the scale from a dimension string against the measured pixel distance between its extension lines.

---

## 10. Known limits

- **Small rooms read small.** Closing the wall mask to bridge a doorway also fills any pocket of the room narrower than twice the radius: the slot between a door leaf and the wall, the corner behind a swing arc. In a 3 m² toilet those pockets are a fifth of the room, so the outline comes out 15–25% under the printed area and the room falls back to a placeholder square. The fix is to grow the chosen pocket back out to the raw walls (dilate by the radius, intersect with the un-closed mask) before tracing; it is the next change to make here.
- **Door swings baked into an image** leave the occasional stair-step on a room edge, and an opening whose host edge went diagonal is placed at its nominal width.
- **Open porches and verandas** have no closing wall, so the fill leaks and the room falls back to a square.
- **Rooms with no printed area** are only read when their name is in the vocabulary, and are traced at a fixed radius with nothing to check against.
- **Openings without a tag** are not found yet. The gap scan can find untagged openings along every room edge; that is the next step, with door versus window decided by the sill-line thickness.
- **Fallback classification** treats every drawn page as a plan, except pages the section heuristic catches. Set the API key.
- **One scale per page.** A sheet with two views at different scales is read at whichever `1:N` appears first in its text.
- **Rotated pages** are read in displayed orientation, which matches pdf.js. Pages whose media box does not start at the origin have not been tested.

---

## 11. Testing

```bash
cd server-python
uv run pytest tests/test_rooms.py      # room detection on the sample drawing, no API key needed
uv run pytest                          # everything, including the API and task lifecycle
```

`tests/test_rooms.py` runs the detector on page 1 of `tests/files/PROJECT_CCVE_without_first_two_pages.pdf` and asserts: all 27 tags become rooms, at least 23 trace, at least 22 land within tolerance of the printed area, at least 15 are clean four-corner rectangles, nodes are shared and carry grid references, all 32 opening tags are placed with at least 24 measured widths, the elevation sheet is skipped by the prefilter, and the page result has the shape above. The classifier is stubbed so no model call is made. The OCR test builds a scanned copy of page 1 on the fly and is skipped when Tesseract is not installed, so it runs in the Docker image rather than on a bare laptop.
