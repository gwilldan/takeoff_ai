from fastapi import APIRouter, HTTPException, status

from app.jobs import get_job
from app.models import JobRecord

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRecord, response_model_by_alias=True, response_model_exclude_none=True)
def read_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
