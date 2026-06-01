"""Tests for GET /api/viewer/summary?include_active=true."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db.manager import insert_entry
from backend.ingestion.jobs import job_store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_jobs():
    job_store._jobs.clear()
    yield
    job_store._jobs.clear()


def test_summary_default_has_no_active_jobs_field(client):
    resp = client.get("/api/viewer/summary")
    assert resp.status_code == 200
    rows = resp.json()
    for r in rows:
        assert "active_jobs" not in r


@pytest.mark.asyncio
async def test_summary_include_active_attaches_running_jobs(tmp_path, monkeypatch):
    # Redirect DATA_DIR so the source DB lands in tmp
    from backend.db import manager as mgr
    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)

    # Seed an entry so the source shows up in the summary listing
    await insert_entry("active_src", {
        "source": "active_src", "indicator": "1.1.1.1",
        "published_at": "2024-01-01", "ingest_mode": "push",
    })

    job = job_store.create(source="active_src", kind="local_feed")
    job_store.update_step(job.id, "inserting", total=100)
    job_store.update_progress(job.id, 42)

    with TestClient(app) as client:
        resp = client.get("/api/viewer/summary?include_active=true")
    assert resp.status_code == 200
    rows = resp.json()
    matching = [r for r in rows if r.get("source") == "active_src"]
    assert matching, f"active_src missing from summary: {rows}"
    assert matching[0]["active_jobs"]
    aj = matching[0]["active_jobs"][0]
    assert aj["kind"] == "local_feed"
    assert aj["step"] == "inserting"
    assert aj["processed"] == 42
    assert aj["total"] == 100


def test_summary_include_active_done_jobs_excluded(client):
    job = job_store.create(source="finished_src", kind="push")
    job_store.complete(job.id, {"total_read": 1, "inserted": 1, "duplicates": 0, "discarded": 0})

    resp = client.get("/api/viewer/summary?include_active=true")
    assert resp.status_code == 200
    rows = resp.json()
    for r in rows:
        if r.get("source") == "__total__":
            continue
        for aj in r.get("active_jobs", []):
            assert aj["job_id"] != job.id
