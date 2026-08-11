"""
Measurement skill — converts raw PDF geometry into real-world measurements.

This is purely deterministic arithmetic.
The LLM never touches this — your code calls it after agent extraction.
"""

import math
from worker.models import Wall
from worker.config import (
    PDF_POINTS_PER_INCH,
    MM_PER_INCH,
    MIN_WALL_LENGTH_MM,
    DEFAULT_SCALE_RATIO,
)


def pts_to_mm(pts: float, scale_ratio: int) -> float:
    """Convert PDF points to real-world millimetres at the given scale."""
    return pts * (MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio


def line_length_pts(start: tuple, end: tuple) -> float:
    """Euclidean distance between two PDF coordinate points."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return math.sqrt(dx**2 + dy**2)


def is_red(color_rgb: list | None) -> bool:
    """
    Heuristic: is this color the red used for grid/dimension lines?
    Red = high red channel, low green and blue.
    """
    if not color_rgb or len(color_rgb) < 3:
        return False
    r, g, b = color_rgb[0], color_rgb[1], color_rgb[2]
    return r > 0.7 and g < 0.4 and b < 0.4


def compute_measurements(
    structural_lines: list,
    scale_ratio: int = DEFAULT_SCALE_RATIO
) -> list[Wall]:
    """
    Convert a list of structural line dicts (from agent output) into
    typed Wall objects with real-world mm measurements.

    Filters out:
    - Lines shorter than MIN_WALL_LENGTH_MM (annotation artifacts)
    - Red lines (grid lines, not walls)

    Args:
        structural_lines: list of line dicts from the agent's raw output
        scale_ratio: drawing scale denominator (100 for 1:100)

    Returns:
        List of Wall dataclass instances with length_mm computed
    """
    walls: list[Wall] = []

    for line in structural_lines:
        color = line.get("color_rgb")

        # Skip red lines — these are grid/dimension lines, not walls
        if is_red(color):
            continue

        start = tuple(line["start"])
        end   = tuple(line["end"])

        length_pts = line.get("length_pts") or line_length_pts(start, end)
        length_mm  = round(pts_to_mm(length_pts, scale_ratio), 1)

        # Skip very short lines (dimension tick marks, annotation artifacts)
        if length_mm < MIN_WALL_LENGTH_MM:
            continue

        walls.append(Wall(
            start_pts=start,
            end_pts=end,
            length_mm=length_mm,
            color=tuple(color) if color else None,
            stroke_width=line.get("stroke_width", 0),
            is_structural=True
        ))

    return walls