from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.jobs import get_job
from app.models import JobRecord
from app.training import plan_pages, save_labels

router = APIRouter(prefix="/jobs", tags=["jobs"])

TRAINING_CLASSES = {"space", "room", "wall", "door", "window", "column", "stair"}


@router.get("/{job_id}", response_model=JobRecord, response_model_by_alias=True, response_model_exclude_none=True)
def read_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


class LabelledDetection(BaseModel):
    class_name: str
    polygon: list[list[float]] = Field(min_length=3)
    name: str | None = None


class LabelledPage(BaseModel):
    page_index: int
    detections: list[LabelledDetection]


class TrainingLabels(BaseModel):
    """Corrected detections for some plan pages, polygons in PDF points from the page's top-left."""
    pages: list[LabelledPage] = Field(min_length=1)


@router.post("/{job_id}/training", status_code=status.HTTP_201_CREATED)
def save_training_labels(job_id: str, body: TrainingLabels) -> dict[str, Any]:
    """Keep a person's corrections to a finished job as labels for training the takeoff model."""
    if not settings.training_data_dir:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Training data is not being kept: set TRAINING_DATA_DIR")
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    measured = plan_pages(job)
    if job.status != "completed" or not measured:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a finished job with measured floor plans can be saved for training")
    unknown = [p.page_index + 1 for p in body.pages if p.page_index not in measured or not measured[p.page_index].get("page_size_pt")]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a measured floor plan page: {unknown}")
    pages = [{"page_index": p.page_index,
              "detections": [d.model_dump(exclude_none=True) for d in p.detections if d.class_name in TRAINING_CLASSES]}
             for p in body.pages]
    path = save_labels(job, pages)
    return {"saved": path.name, "pages": len(pages), "detections": sum(len(p["detections"]) for p in pages)}
