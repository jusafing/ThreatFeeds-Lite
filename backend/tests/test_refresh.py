"""Tests for the shared single-source refresh helpers (issue #1).

Covers `refresh_source` dispatch and `run_tracked_pull`, the job-wrapped
background pull that drives the per-feed "pulling…" / "ready" / "error"
status markers.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.ingestion.refresh as refresh
from backend.ingestion.jobs import job_store


@pytest.fixture(autouse=True)
def _isolate_jobs():
    """Each test starts and ends with an empty job_store."""
    job_store.reset()
    yield
    job_store.reset()


@pytest.fixture(autouse=True)
def _no_meta_writes(monkeypatch):
    """Prevent job completion from scheduling real data/meta.db writes."""
    import backend.db.meta as meta

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(meta, "record_ingest", _noop)


@pytest.mark.asyncio
async def test_refresh_source_dispatches_api_pull(monkeypatch):
    seen: list = []

    async def fake_api(src):
        seen.append(src["name"])
        return {"inserted": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(refresh, "pull_api_source", fake_api)
    result = await refresh.refresh_source("api_pull", {"name": "a", "url": "http://x"})

    assert result == {"inserted": 1, "skipped": 0, "errors": []}
    assert seen == ["a"]


@pytest.mark.asyncio
async def test_refresh_source_dispatches_rss_pull(monkeypatch):
    async def fake_rss(src):
        return {"inserted": 2, "skipped": 0, "errors": []}

    monkeypatch.setattr(refresh, "pull_rss_source", fake_rss)
    result = await refresh.refresh_source("rss_pull", {"name": "r", "url": "http://x"})
    assert result["inserted"] == 2


@pytest.mark.asyncio
async def test_refresh_source_dispatches_remote_json(monkeypatch):
    captured: dict = {}

    async def fake_remote(url, name, source_fields=None):
        captured.update(url=url, name=name, fields=source_fields)
        return {"inserted": 3, "skipped": 0, "errors": [], "format": "json"}

    monkeypatch.setattr(refresh, "ingest_remote_feed", fake_remote)
    result = await refresh.refresh_source(
        "remote_json_pull",
        {"name": "rj", "url": "http://x", "fields": {"id": "cve"}},
    )

    assert result["inserted"] == 3
    assert captured == {"url": "http://x", "name": "rj", "fields": {"id": "cve"}}


@pytest.mark.asyncio
async def test_refresh_source_unknown_kind_raises():
    with pytest.raises(ValueError):
        await refresh.refresh_source("bogus_kind", {"name": "x"})


@pytest.mark.asyncio
async def test_run_tracked_pull_completes_job(monkeypatch):
    async def fake_api(src):
        return {"inserted": 5, "skipped": 1, "errors": []}

    monkeypatch.setattr(refresh, "pull_api_source", fake_api)

    await refresh.run_tracked_pull("api_pull", {"name": "src", "url": "http://x"})

    jobs = list(job_store._jobs.values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "src"
    assert job.kind == "api_pull"
    assert job.state == "done"
    assert job.counters["inserted"] == 5


@pytest.mark.asyncio
async def test_run_tracked_pull_maps_remote_json_kind(monkeypatch):
    async def fake_remote(url, name, source_fields=None):
        return {"inserted": 0, "skipped": 0, "errors": [], "format": "json"}

    monkeypatch.setattr(refresh, "ingest_remote_feed", fake_remote)

    await refresh.run_tracked_pull("remote_json_pull", {"name": "rj", "url": "http://x"})

    job = next(iter(job_store._jobs.values()))
    assert job.kind == "remote_json"
    assert job.state == "done"


@pytest.mark.asyncio
async def test_run_tracked_pull_records_failure(monkeypatch):
    async def boom(src):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "pull_api_source", boom)

    # Must not raise out of the background coroutine.
    await refresh.run_tracked_pull("api_pull", {"name": "src", "url": "http://x"})

    job = next(iter(job_store._jobs.values()))
    assert job.state == "error"
    assert "network down" in (job.error_msg or "")
