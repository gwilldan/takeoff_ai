"""
Write extraction results to the shared output volume and log a summary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_extraction_output(job_id: str, result: dict[str, Any], output_dir: str) -> Path:
    """Persist result JSON to {output_dir}/{job_id}.json and print a summary."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{job_id}.json"
    output_path.write_text(
        json.dumps({"jobId": job_id, **result}, indent=2, default=str),
        encoding="utf-8",
    )

    _print_summary(job_id, result, output_path)

    return output_path


def _print_summary(job_id: str, result: dict[str, Any], output_path: Path) -> None:
    document = result.get("document") or {}
    totals = result.get("totals") or {}
    tokens = (result.get("tokenUsage") or {}).get("totals", {})

    print("=" * 60)
    print(f"[output] Job {job_id} completed")

    if not result.get("accepted", True):
        rejection = result.get("rejection") or {}
        print(f"[output] Rejected: {rejection.get('reason')}")
        print(f"[output] {rejection.get('message')}")
    else:
        print(
            f"[output] Plan pages: {document.get('planPageCount', 0)}"
            f" of {document.get('pageCount', 0)}"
        )
        if totals:
            breakdown = " | ".join(f"{layer}: {count}" for layer, count in sorted(totals.items()))
            print(f"[output] Components — {breakdown}")

        for page in result.get("pages") or []:
            if page.get("isPlan"):
                scale = (page.get("scale") or {}).get("text", "unknown")
                print(
                    f"[output]   p{page.get('pageNumber')} "
                    f"{page.get('planType', 'unknown')} scale={scale} "
                    f"{page.get('counts') or {}}"
                )
            else:
                print(
                    f"[output]   p{page.get('pageNumber')} skipped — "
                    f"{page.get('skippedReason')}"
                )

    if tokens:
        print(
            f"[output] Tokens — prompt: {tokens.get('prompt_tokens', 0)}, "
            f"completion: {tokens.get('completion_tokens', 0)}, "
            f"total: {tokens.get('total_tokens', 0)}"
        )

    print(f"[output] Saved to: {output_path}")
    print("=" * 60)
