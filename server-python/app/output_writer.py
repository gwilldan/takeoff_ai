"""Write extraction results to the shared output volume and log a summary."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def save_extraction_output(job_id: str, result: dict[str, Any]) -> Path:
    """Persist result JSON to `<EXTRACTION_OUTPUT_DIR>/<jobId>.json`."""
    out_dir = Path(settings.extraction_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{job_id}.json"
    output_path.write_text(
        json.dumps({"jobId": job_id, **result}, indent=2, default=str),
        encoding="utf-8",
    )
    _log_summary(job_id, result, output_path)
    return output_path


def _log_summary(job_id: str, result: dict[str, Any], output_path: Path) -> None:
    document = result.get("document") or {}
    tokens = (result.get("tokenUsage") or {}).get("totals") or {}

    log.info("Job %s completed", job_id)

    if not result.get("accepted", True):
        rejection = result.get("rejection") or {}
        log.info("Rejected: %s — %s", rejection.get("reason"), rejection.get("message"))
    else:
        log.info(
            "Plan pages: %s of %s", document.get("planPageCount", 0), document.get("pageCount", 0)
        )
        if totals := result.get("totals"):
            log.info("Components — %s", " | ".join(f"{k}: {v}" for k, v in sorted(totals.items())))

        for page in result.get("pages") or []:
            if page.get("isPlan"):
                scale = (page.get("scale") or {}).get("text", "unknown")
                log.info(
                    "  p%s %s scale=%s %s",
                    page.get("pageNumber"), page.get("planType", "unknown"), scale, page.get("counts") or {},
                )
            else:
                log.info("  p%s skipped — %s", page.get("pageNumber"), page.get("skippedReason"))

    if tokens:
        log.info(
            "Tokens — prompt: %s, completion: %s, total: %s",
            tokens.get("prompt_tokens", 0), tokens.get("completion_tokens", 0), tokens.get("total_tokens", 0),
        )

    log.info("Saved to: %s", output_path)
