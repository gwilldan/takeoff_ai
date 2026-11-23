"""
Plans kept for training the takeoff model, in the layout its tools read
(`model/data`):

    sources/<jobId>.pdf     the uploaded drawing set
    drafts/<jobId>.json     what the takeoff engine returned for its plan pages
    labels/<jobId>.json     a person's corrections: what the model trains on

Every finished job leaves a draft. Corrections sent from the editor become
labels. A draft is never trained on as it is: correct it in the editor or in
CVAT first (`takeoff-coco export --drafts`). Nothing is kept unless
TRAINING_DATA_DIR is set.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.models import JobRecord

PAPER_MM_PER_PT = 25.4 / 72


def _denominator(page: dict[str, Any]) -> float | None:
    """The scale the page was measured at, from its metres per pixel and pixels per PDF point."""
    mpp, image, paper = page.get("metres_per_px"), page.get("image_size"), page.get("page_size_pt")
    if not (mpp and image and paper):
        return None
    real_mm_per_pt = mpp * 1000 * image["width"] / paper["width"]
    return round(real_mm_per_pt / PAPER_MM_PER_PT, 2)


def _keep_source(job: JobRecord, root: Path) -> None:
    target = root / "sources" / f"{job.id}.pdf"
    if not target.exists() and job.document_path:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job.document_path, target)


def _write(path: Path, job: JobRecord, pages: list[dict[str, Any]], **extra: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": job.id, "status": "succeeded", "pages": pages, **extra}, indent=1))
    return path


def plan_pages(job: JobRecord) -> dict[int, dict[str, Any]]:
    """The job's measured plan pages, by page index."""
    pages = job.result.pages if job.result else []
    return {p["page_index"]: p for p in pages if p.get("is_plan") and p.get("image_size") and not p.get("error")}


def save_draft(job: JobRecord) -> Path | None:
    """The engine's detections on every plan page, in its own pixels, as a starting point for labelling."""
    if not settings.training_data_dir or not job.result:
        return None
    root = Path(settings.training_data_dir)
    pages = [{"page_index": i, "image_size": p["image_size"],
              "scale": {"denominator": _denominator(p) or (p.get("scale") or {}).get("denominator")},
              "detections": [{"class_name": d["class_name"], "polygon": d["polygon"], **({"name": d["name"]} if d.get("name") else {})}
                             for d in p.get("detections") or [] if len(d.get("polygon") or []) >= 3]}
             for i, p in sorted(plan_pages(job).items())]
    if not pages:
        return None
    _keep_source(job, root)
    return _write(root / "drafts" / f"{job.id}.json", job, pages, engine=urlparse(settings.quanttakeoff_base_url).hostname)


def save_labels(job: JobRecord, pages: list[dict[str, Any]]) -> Path:
    """
    A person's corrected detections for some plan pages, with polygons in PDF
    points from the top-left: the page's own size in points is the label's image
    size, so the model renders the page at any resolution and the labels follow.
    """
    root = Path(settings.training_data_dir)
    measured = plan_pages(job)
    labelled = []
    for page in pages:
        source = measured[page["page_index"]]
        size = source["page_size_pt"]
        labelled.append({"page_index": page["page_index"], "image_size": {"width": size["width"], "height": size["height"]},
                         "scale": {"denominator": _denominator(source)}, "detections": page["detections"]})
    _keep_source(job, root)
    return _write(root / "labels" / f"{job.id}.json", job, labelled, reviewed=True)
