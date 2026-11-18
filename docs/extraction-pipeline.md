# Extraction pipeline

How Takeoff AI turns an uploaded drawing set into walls, rooms, doors and windows that the frontend draws, a person checks and edits, and the takeoff prices. This covers the backend in `server-python`, from `POST /extract` to the job record reading `completed`, and how the frontend uses the result.

Code paths are relative to `server-python/app`.

---

## 1. Overview

```
POST /extract ──► job record (Redis) ──► extract-document
                                              │  one task per page
                                              ▼
                                        extract-page × N        parse, classify, check scale, read grid
                                              │
                         not a floor plan ────┤──── floor plan: post it to QuantTakeoff as a one-page PDF
                         published at once    │
                                              ▼
                                       collect-takeoff         by callback, or polling every 10 s
                                              │
                                              ▼
                                  page published; the last page closes the job ──► output JSON + webhook
```

The work is split by who does it best:

- **QuantTakeoff measures.** Walls, doors, windows and rooms come from its trained models, as polygons in page pixels with confidence scores, wall types, room areas, and the names and marks printed in the PDF's text.
- **We decide what to send and check the scale.** Qwen classifies each page, and only floor plans are sent, so sections, schedules, site plans and cover sheets cost nothing. Our scale check runs on every floor plan, and where QuantTakeoff could only estimate a scale, a scale we checked against the dimensions replaces it.
- **We add what the editor needs.** Grid axes for snapping, a length for every wall, and our scale beside theirs.

Qwen is used for classification only. It never places or names anything.

---

## 2. Intake (`routes/extract.py`)

Multipart form with a `document` file and an optional `notificationUrl`.

1. A UUID becomes the job id.
2. The upload streams to `<DOCUMENT_STORAGE_DIR>/<jobId>/source.pdf`. The first kilobyte must contain `%PDF-`, or the request fails with `400 Document must be a PDF`. Over `MAX_UPLOAD_BYTES` (50 MB) fails with `413`.
3. `notificationUrl`, when given, must be an HTTP URL or the request fails with `400`.
4. A `JobRecord` is written to Redis with status `queued`, and `extract-document` is enqueued.
5. The response is `202` with `jobId`, `statusUrl`, `documentPath` and the queue name.

The job record (`models.py`) is camelCase on the wire: `id, status, createdAt, updatedAt, documentPath, notificationUrl, attempts, fileId, result, error, completedAt`. `result` is the takeoff result of §6, in QuantTakeoff's snake_case. Updates go through `update_job`, a Redis `WATCH` transaction, so page tasks on several workers never overwrite each other.

---

## 3. Tasks (`tasks.py`)

Celery with Redis as the broker. There is no result backend and no chord: a takeoff finishes minutes after the task that started it, so whichever page is published last closes the job.

### 3.1 `extract-document`

Runs once per job. It resolves the stored PDF, counts its pages, opens an empty result, and queues one `extract-page` per page. A missing file or job record fails the job at once; anything else retries with backoff (`QUEUE_ATTEMPTS`, `QUEUE_BACKOFF_DELAY_MS`) and then fails the job.

### 3.2 `extract-page`

Runs once per page, `WORKER_CONCURRENCY` at a time.

1. `pipeline.read_page` parses, classifies, and for a floor plan checks the scale and reads the grid (§4).
2. A page that is not a floor plan is published straight away with `is_plan: false` and a `skipped_reason`.
3. A floor plan is cut into a one-page PDF and posted to `POST /v1.1/takeoffs` as multipart form data (`takeoff_api.create_takeoff`), with an `Idempotency-Key` of `<jobId>:<pageIndex>` so a retry never starts or bills a second one. The response is the takeoff's id. The page as read is saved beside the job, keyed by that id, and `collect-takeoff` is scheduled.

The multipart form takes no options: page choice, scale and units are ignored, and the takeoff always measures the first page of the file. That is why each page goes as its own PDF, and why our scale cannot be sent in advance. A page on which QuantTakeoff finds no scale text, readable dimensions or enough doors fails with that reason. The JSON form (`source.file_id` from `POST /v1.1/uploads`) does take `options.scale`, but on 2026-11-18 their upload endpoint answered every call with a 500.

A page that cannot be read after its retries is published as skipped. A page that was read but whose takeoff could not be started is published as a plan with an `error`.

### 3.3 `collect-takeoff`

Runs when QuantTakeoff calls back, and every `TAKEOFF_POLL_INTERVAL_S` (10 s) until then.

1. Nothing to do if the takeoff is no longer pending: the callback or an earlier poll already published it.
2. While the takeoff is `queued` or `running`, check again later. After `TAKEOFF_TIMEOUT_S` (30 min) the page is published with an error.
3. Once it `succeeded`, fetch the results, merge their only page with the page as read (§5), and publish. `takeoff_s` is the time from posting to seeing it succeed, so it is accurate to the poll interval.
4. A `failed` or `canceled` takeoff publishes the page with QuantTakeoff's reason; a 4xx from the API with its message. A 5xx, 429 or network error is retried until the timeout.

Publishing adds the page to the result and rolls up the document's counts, metrics, wall types, review counts and token usage. The page that completes the document sets `status: succeeded`, `accepted` (true when any page is a floor plan, else a `rejection`), and the elapsed time; it then writes `<EXTRACTION_OUTPUT_DIR>/<jobId>.json` and calls the job's webhook (`notifier.py`).

### 3.4 The callback (`routes/webhooks.py`)

`POST /webhooks/quanttakeoff`. QuantTakeoff does not document how its callbacks are signed, so the payload is not trusted: it only names a takeoff, and only a takeoff we started and are still waiting on is acted on, by queueing `collect-takeoff` now. The result itself is always fetched with our key.

A callback is only requested when `PUBLIC_API_URL` is set to an address QuantTakeoff can reach. Without it, polling alone completes every page; on a laptop that is the normal mode.

---

## 4. Reading a page (`extractor/pipeline.py: read_page`)

### 4.1 Parse (`extractor/cache.py`)

PyMuPDF opens the page once and collects:

- **Text spans**, with bounding box, centre and font size, in PDF points with a top-left origin.
- **OCR for scans.** A page with fewer than 20 native spans and images covering at least 20% of it is a scan. Tesseract reads it (`OCR_LANGUAGE`, `OCR_DPI`) and its words become the spans; the page is marked `text_source: "ocr"`.
- **Line segments**: every stroked straight line, for the prefilter and the scale check.
- **Image coverage**, so an image-backed drawing is not taken for an empty page.

### 4.2 Classify (`extractor/classify.py`)

**Prefilter, free.** Skipped without a model call: fewer than 40 line segments and images under 20% of the page; a schedule, specification or register title with no plan vocabulary; a section or elevation title, or three or more datum level marks (`AGL`, `DPC`, `FFL` …).

**Vision call.** The page at 150 dpi and a short text summary go to `LLM_VISION_MODEL`, which returns `is_plan, plan_type, not_plan_reason, scale, scale_ratio, confidence, notes`.

Only `ground_floor_plan`, `upper_floor_plan`, `floor_plan` and `unknown` are sent for takeoff. Site and roof plans are skipped: QuantTakeoff measures floor plans. If the model is unreachable or its reply is not JSON, the page is kept as `unknown` and sent: a page is never dropped because of a bad response.

### 4.3 Check the scale (`extractor/calibrate.py`)

A stated scale is only true for the sheet it was printed on: a 1:100 drawing exported on a larger sheet measures 1:50, and a shrunk scan can measure 1:195. Two independent sources of readings are judged one at a time, and the first that is conclusive decides:

1. **Dimension lines.** Each dimension string labels a line beside it; `value ÷ line length` is one reading. On vector pages the line is found among the vector segments, on scans in the rendered page.
2. **Dimension chains.** A string is centred on its own span, so two neighbours in a chain sit half their values' sum apart. This needs only the text, so it works on scans whose lines are too faint to measure. A pair counts only when the next pair along agrees with it and the labels are not all equal, which drops grid numbers, mixed chains and runs of identical riser heights.

Readings within 3% form a cluster, and the largest cluster is the measurement, in millimetres, centimetres or metres, whichever agrees with the stated scale.

| Outcome | Needs | `source` | Confidence |
|---|---|---|---|
| Stated scale confirmed (within 6%) | 4 readings and 15% of all | `stated` | high |
| Stated scale overruled | 6 readings and 40% of all | `dimensions` | medium |
| No scale stated; measured | 5 readings and 30% of all | `dimensions` | medium |
| Stated scale kept unchecked | anything less | `stated` | from the classifier |
| Nothing stated, nothing measured | — | `assumed` (1:100) | low |

A scale confirmed or measured against the dimensions (`dimensionsChecked` above zero) is a checked scale. §5 says when it replaces QuantTakeoff's.

### 4.4 Grid (`extractor/grid.py`)

Setting-out marks, letters across the top and numbers down the side, are one- or two-character spans in bands of at least three. Their positions become snap lines in the editor.

---

## 5. Merging the takeoff (`extractor/pipeline.py: complete_page`)

QuantTakeoff's page result is kept whole: every field keeps its name and value, except `page_index`, which becomes the page's index in the set (theirs is always 0, the only page of the PDF it was sent). Door and window marks are theirs, in `name`. Added beside it:

**Which scale measures.** QuantTakeoff's when it read it from the drawing (`source: drawing`, `dimensions` or `user`). Otherwise ours when it is checked, since theirs is then at best an estimate from door widths (on the Emma ground floor they estimated 1:200; the dimensions say 1:194.7, 5% in length and 11% in area). Otherwise whichever exists.

| Field | Where | What |
|---|---|---|
| `length_m` | wall detection | Long side of the wall's tightest rotated rectangle × metres per pixel. |
| `area_m2_checked` | room detection | Their `area_m2` at our scale, when ours replaced theirs. |
| `metres_per_px`, `scale_used` | page | The scale everything is measured at, and whose it is (`quanttakeoff` or `ours`). |
| `metrics` | page | That takeoff's metrics, as QuantTakeoff wrote them. |
| `grid` | page | Our grid axes, converted to page pixels. |
| `is_plan`, `plan_type`, `classified_by`, `text_source` | page | Our classification. |
| `page_size_pt` | page | The PDF page size, so page pixels map onto the PDF. |
| `scale_check` | page | Our scale check (§4.3). |
| `takeoff_id`, `timings`, `notes`, `token_usage` | page | The takeoff, `read_s` and `takeoff_s`, notes, and Qwen's tokens. |

---

## 6. Output shape

`result` is QuantTakeoff's takeoff result for the whole document:

```
id, status (queued | running | succeeded | failed), schema_version, pages[], metrics, assets, error, wall_types, review
```

plus ours: `document { page_count, pages_processed, plan_page_count }`, `accepted`, `rejection`, `token_usage`, `timings { started_at, completed_at, elapsed_s }`.

Each page is QuantTakeoff's `page_index, image_size, scale, detections, plans` plus the fields of §5. A page that is not a floor plan has `is_plan: false`, a `skipped_reason` and no detections. A page whose takeoff failed has `is_plan: true` and an `error`.

`metrics` is ours, over every page: `counts` per class, `wall_length_m`, `room_area_m2` (checked areas where they exist), and `seconds { read_mean, takeoff_mean, takeoff_max }`, which is the response-time benchmark. Each page keeps its own takeoff's metrics. `wall_types` is QuantTakeoff's per takeoff, each entry tagged with its `page_index`. `review` sums their doors and windows needing review.

---

## 7. What the frontend does with it

`frontend/lib/annotations/extraction-import.ts` maps the result onto layers, the way `annotate-pdf-from-json/annotator.py` draws it: every point is scaled by `page_size_pt / image_size`; rooms, walls, windows and doors take its colours and fill opacities and are painted in that order; doors, windows and rooms carry its `class score` label. Detections flagged `needs_review` are outlined dashed. Walls total their length, doors and windows their count, rooms their area. The calibration comes from the first plan page's `metres_per_px`, and the grid axes become snap lines.

The import runs again as each page lands, and a stream of page imports counts as one entry in the editor's history.

---

## 8. Settings

| Setting | Default | Effect |
|---|---|---|
| `QUANTTAKEOFF_API_KEY` | — | Required. Bearer key for QuantTakeoff. |
| `QUANTTAKEOFF_BASE_URL` | `https://app.quanttakeoff.com/v1.1` | API base. |
| `PUBLIC_API_URL` | empty | Where QuantTakeoff can reach this API for callbacks. Empty means poll only. |
| `TAKEOFF_POLL_INTERVAL_S` | 10 | How often a pending takeoff is checked. |
| `TAKEOFF_TIMEOUT_S` | 1800 | When a takeoff that has not finished is given up. |
| `LLM_VISION_MODEL`, `LLM_THINKING` | `qwen3.7-plus`, false | The classifier. |
| `WORKER_CONCURRENCY` | 2 | Pages read in parallel. Takeoffs run on QuantTakeoff and hold no worker. |

---

## 9. Testing

```bash
cd server-python
uv run pytest
```

QuantTakeoff and Qwen are never called by the tests. `tests/test_extraction.py` reads real drawings: page reading and classification, the scale confirmed, overruled and measured, dimension chains, the one-page PDF each plan is sent as, and the merge of a takeoff page (their fields kept, ours added, wall lengths, which scale wins, checked areas, grid in pixels). `tests/test_workflow.py` runs the job lifecycle against a fake QuantTakeoff: only floor plans are sent, pages publish as they land, the last page closes the job, failures land on their page, and the callback acts only on takeoffs we are waiting on. The OCR test needs Tesseract, which the Docker image installs.

---

## 10. Results

Run on 2026-11-18 through the dev stack and the live QuantTakeoff API.

| PDF | Pages sent | Result | Time |
|---|---|---|---|
| `CCVE GROUND FLOOR PLAN-main.pdf` | 1 of 1 | 50 walls (208.6 m), 19 doors, 23 windows, 21 named rooms (235.4 m²); scale 1:100 read by both. Marks such as Dr-02 and Wn-04 from the PDF text. | 55 s in all; takeoff 42 s |
| `arch-emma-storey-building.pdf` (phone scan, letter size) | 3 of 17 | First floor: 53 walls, 22 doors, 18 windows, 19 rooms at their 1:200 estimate. Ground floor: 46 walls, 22 doors, 18 windows, 20 rooms at our measured 1:194.7. The sheet with two plans and a stair detail failed: no readable scale. No names or marks: the scan has no text layer. 14 pages skipped by classification. | 70 s in all; takeoffs 21–23 s |
