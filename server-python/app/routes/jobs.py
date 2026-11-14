from fastapi import APIRouter, HTTPException, status

from app.jobs import get_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def read_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
