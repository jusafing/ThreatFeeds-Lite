"""Tests for routes_jobs — job status polling endpoint."""
from __future__ import annotations

import pytest

import backend.api.routes_jobs as rj
from backend.ingestion.jobs import job_store


@pytest.fixture(autouse=True)
def _reset_jobs():
    job_store.reset()
    yield
    job_store.reset()


@pytest.mark.asyncio
async def test_get_job_returns_state():
    job = job_store.create("src1", "local_feed")
    job_store.update_step(job.id, "inserting", total=10)
    job_store.update_progress(job.id, 7)

    result = await rj.get_job(job.id)
    assert result["id"] == job.id
    assert result["source"] == "src1"
    assert result["kind"] == "local_feed"
    assert result["state"] == "running"
    assert result["step"] == "inserting"
    assert result["total"] == 10
    assert result["processed"] == 7


@pytest.mark.asyncio
async def test_get_job_returns_404_for_unknown_id():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await rj.get_job("non-existent")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_active_filters_terminal_jobs():
    a = job_store.create("a", "local_feed")
    b = job_store.create("b", "local_feed")
    job_store.complete(a.id, {"total_read": 5, "inserted": 5, "duplicates": 0, "discarded": 0})

    active = await rj.list_jobs(active=True)
    assert len(active) == 1
    assert active[0]["id"] == b.id

    all_jobs = await rj.list_jobs(active=False)
    assert len(all_jobs) == 2
