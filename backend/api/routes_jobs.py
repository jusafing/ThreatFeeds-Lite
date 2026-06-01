"""
Job status routes — let the frontend poll background ingest progress.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.ingestion.jobs import job_store

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(active: bool = Query(False, description="Only running/queued jobs")) -> list[dict[str, Any]]:
    if active:
        return [j.to_dict() for j in job_store.list_active()]
    # Default: return all (including recently-finished within TTL)
    return [j.to_dict() for j in job_store._jobs.values()]  # type: ignore[attr-defined]


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found or expired")
    return job.to_dict()
