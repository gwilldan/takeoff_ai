"""
Data models shared across the entire worker pipeline.
All stages pass these typed objects between each other.
"""

from dataclasses import dataclass, field
from typing import Optional
from typing import Any, List


@dataclass
class Room:
    name: str
    area_m2: Optional[float]
    position_pts: tuple


@dataclass
class Wall:
    start_pts: tuple
    end_pts: tuple
    length_mm: float
    color: Optional[tuple]
    stroke_width: float
    is_structural: bool = False


@dataclass
class Opening:
    kind: str            # "door" | "window"
    reference: str       # "Dr-01", "Wn-02" etc
    position_pts: tuple


@dataclass
class BOQItem:
    item_code: str
    description: str
    unit: str            # m | m2 | m3 | nr | kg
    quantity: float
    category: str
    confidence: str      # high | medium | low
    approved: bool = False


@dataclass
class BOQResult:
    project_id: str
    scale: str
    rooms: list[Room]
    walls: list[Wall]
    openings: list[Opening]
    boq_items: list[BOQItem]
    raw_extraction: dict
    notes: list[str] = field(default_factory=list)


@dataclass
class ExtractedTable:
    rows: List[List[str]]


@dataclass
class ExtractedPage:
    pageNumber: int
    text: str
    tables: List[ExtractedTable]


@dataclass
class PdfExtractionResult:
    pageCount: int
    text: str
    pages: List[ExtractedPage]
    extractedAt: str