"""Write extraction results to the shared output volume and log a summary."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import TakeoffResult

log = logging.getLogger(__name__)


def save_extraction_output(job_id: str, extraction: TakeoffResult) -> Path:
    """Persist result JSON to `<EXTRACTION_OUTPUT_DIR>/<jobId>.json`."""
    result = extraction.to_wire()
    out_dir = Path(settings.extraction_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{job_id}.json"
    output_path.write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
    _log_summary(job_id, result, output_path)
    return output_path


def _log_summary(job_id: str, result: dict[str, Any], output_path: Path) -> None:
    document, metrics = result["document"], result.get("metrics") or {}
    log.info("Job %s completed in %ss — %s of %s pages are floor plans", job_id,
             result.get("timings", {}).get("elapsed_s"), document["plan_page_count"], document["page_count"])
    log.info("Detections — %s | walls %s m | rooms %s m² | seconds %s", metrics.get("counts"),
             metrics.get("wall_length_m"), metrics.get("room_area_m2"), metrics.get("seconds"))
    for page in result.get("pages") or []:
        if page.get("error"):
            log.info("  p%s takeoff failed — %s", page["page_index"] + 1, page["error"])
        elif not page.get("is_plan"):
            log.info("  p%s skipped — %s", page["page_index"] + 1, page.get("skipped_reason"))
        else:
            log.info("  p%s %s, %s detections, read %ss, takeoff %ss", page["page_index"] + 1, page.get("plan_type"),
                     len(page.get("detections") or []), page["timings"].get("read_s"), page["timings"].get("takeoff_s"))
    log.info("Saved to: %s", output_path)
