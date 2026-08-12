"""
Write extraction results to the shared output volume and log a summary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_extraction_output(job_id: str, result: dict[str, Any], output_dir: str) -> Path:
    """
    Persist result JSON to {output_dir}/{job_id}.json and print a summary.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{job_id}.json"
    payload = {
        "jobId": job_id,
        **result,
    }
    output_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )

    walls = len(result.get("walls") or [])
    rooms = len(result.get("rooms") or [])
    dimensions = len(result.get("dimensions") or [])
    openings = len(result.get("openings") or [])
    scale = result.get("scale", "unknown")
    tokens = (result.get("token_usage") or {}).get("totals", {})

    print("=" * 60)
    print(f"[output] Job {job_id} completed")
    print(f"[output] Scale: {scale}")
    print(f"[output] Walls: {walls} | Rooms: {rooms} | Dimensions: {dimensions} | Openings: {openings}")
    if tokens:
        print(
            f"[output] Tokens — prompt: {tokens.get('prompt_tokens', 0)}, "
            f"completion: {tokens.get('completion_tokens', 0)}, "
            f"total: {tokens.get('total_tokens', 0)}"
        )
    print(f"[output] Saved to: {output_path}")
    print("=" * 60)

    return output_path
