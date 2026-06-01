"""Integration tests for background ingest paths (?background=true)."""
from __future__ import annotations

import asyncio
import io
import json

import pytest
from fastapi import BackgroundTasks

import backend.api.routes_ingest as ri
import backend.ingestion.preview as pv
from backend.ingestion.jobs import job_store


@pytest.fixture(autouse=True)
def _reset_jobs():
    job_store.reset()
    yield
    job_store.reset()


@pytest.mark.asyncio
async def test_push_background_returns_job_id(monkeypatch):
    """POST /api/ingest/push/{src}?background=true returns {job_id} and ingests asynchronously."""
    captured: list = []

    async def fake_process_push(payload, source_name, job_id=None):
        # Simulate the ingest writing progress + counters
        if job_id:
            job_store.update_step(job_id, "inserting", total=1)
            job_store.update_progress(job_id, 1)
        captured.append((source_name, payload, job_id))
        return {
            "inserted": 1, "skipped": 0, "total_read": 1,
            "duplicates": 0, "discarded": 0,
        }

    monkeypatch.setattr(ri, "process_push", fake_process_push)

    async def fake_first_ingest(name):
        return True

    monkeypatch.setattr(ri, "_first_ingest", fake_first_ingest)

    bg = BackgroundTasks()
    payload = {"indicator": "1.2.3.4"}
    result = await ri.push_ingest("srcA", payload, bg, background=True)

    assert "job_id" in result
    job = job_store.get(result["job_id"])
    assert job is not None
    assert job.source == "srcA"
    assert job.kind == "push"
    assert job.first_ingest is True

    # Run the deferred task and confirm completion is reached via the runner
    await bg()
    job = job_store.get(result["job_id"])
    assert job.state == "done"
    assert job.counters["inserted"] == 1
    assert captured == [("srcA", payload, result["job_id"])]


@pytest.mark.asyncio
async def test_push_sync_still_returns_ingest_response(monkeypatch):
    """Without ?background=true the response is the synchronous IngestResponse."""

    async def fake_process_push(payload, source_name, job_id=None):
        return {
            "inserted": 2, "skipped": 0, "total_read": 2,
            "duplicates": 0, "discarded": 0,
        }

    monkeypatch.setattr(ri, "process_push", fake_process_push)

    bg = BackgroundTasks()
    result = await ri.push_ingest("srcA", {"indicator": "x"}, bg, background=False)
    # Result is an IngestResponse instance, not a job_id dict
    assert getattr(result, "inserted", None) == 2
    assert job_store.list_active() == []


@pytest.mark.asyncio
async def test_confirm_local_preview_background(monkeypatch):
    """POST /api/ingest/preview/confirm/{id}?background=true returns job_id and ingests in background."""

    # Seed an entry in the preview cache directly
    import time as _t
    import uuid as _uuid
    pid = str(_uuid.uuid4())
    pv._store[pid] = {
        "entries": [{"indicator": "x", "source": "s1"}],
        "source_name": "s1",
        "format": "json",
        "expires": _t.monotonic() + 60,
    }

    async def fake_insert(name, entry):
        return "inserted"

    monkeypatch.setattr(pv, "insert_entry", fake_insert)

    async def fake_first_ingest(name):
        return True
    monkeypatch.setattr(ri, "_first_ingest", fake_first_ingest)

    bg = BackgroundTasks()
    result = await ri.confirm_local_preview(pid, bg, background=True)
    assert "job_id" in result
    await bg()
    job = job_store.get(result["job_id"])
    assert job.state == "done"
    assert job.counters["inserted"] == 1
    assert job.source == "s1"
    assert job.first_ingest is True


@pytest.mark.asyncio
async def test_confirm_local_preview_background_404_for_unknown_id():
    from fastapi import HTTPException
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await ri.confirm_local_preview("does-not-exist", bg, background=True)
    assert exc.value.status_code == 404


# ── prompts-021B: compressed-preview integration ───────────────────────────────

class _FakeUpload:
    """Minimal UploadFile stand-in: only filename + read() are used."""
    def __init__(self, filename: str, body: bytes) -> None:
        self.filename = filename
        self._body = body

    async def read(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_preview_local_feed_accepts_gz_upload(monkeypatch):
    """A .gz upload to /preview/local/{src} returns a preview whose records
    were produced from the decompressed payload."""
    import gzip
    inner = json.dumps([{"indicator": "9.9.9.9"}, {"indicator": "8.8.8.8"}]).encode()
    body = gzip.compress(inner)

    monkeypatch.setattr(pv, "normalise", lambda raw, **kw: raw)

    upload = _FakeUpload("feed.json.gz", body)
    resp = await ri.preview_local_feed("gz_src", upload)

    assert resp.format == "json"
    assert resp.total == 2
    assert any(s.get("indicator") == "9.9.9.9" for s in resp.sample)


@pytest.mark.asyncio
async def test_preview_local_feed_accepts_zip_upload(monkeypatch):
    """A single-member .zip upload to /preview/local/{src} returns a preview."""
    import io
    import zipfile

    csv_bytes = b"indicator,severity\n10.0.0.1,high\n10.0.0.2,low\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.csv", csv_bytes)
    body = buf.getvalue()

    monkeypatch.setattr(pv, "normalise", lambda raw, **kw: raw)

    upload = _FakeUpload("bundle.zip", body)
    resp = await ri.preview_local_feed("zip_src", upload)

    assert resp.format == "csv"
    assert resp.total == 2
