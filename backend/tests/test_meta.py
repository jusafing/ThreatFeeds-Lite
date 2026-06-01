"""Tests for backend.db.meta — per-source last-ingest persistence."""
from __future__ import annotations

import pytest

from backend.db import meta as meta_mod


@pytest.fixture
def isolated_meta(tmp_path, monkeypatch):
    """Redirect meta.db to a tmp path so tests don't touch real data/."""
    fake_path = tmp_path / "meta.db"
    monkeypatch.setattr(meta_mod, "META_DB", fake_path)
    monkeypatch.setattr(meta_mod, "DATA_DIR", tmp_path)
    return fake_path


@pytest.mark.asyncio
async def test_record_and_get_single_source(isolated_meta):
    counters = {"total_read": 50, "inserted": 40, "duplicates": 5, "discarded": 5}
    await meta_mod.record_ingest("src1", counters, state="done", kind="local_feed")

    rows = await meta_mod.get_meta(["src1"])
    assert "src1" in rows
    r = rows["src1"]
    assert r["last_total_read"] == 50
    assert r["last_inserted"] == 40
    assert r["last_duplicates"] == 5
    assert r["last_discarded"] == 5
    assert r["last_job_state"] == "done"
    assert r["last_job_kind"] == "local_feed"
    assert r["last_ingested_at"] is not None


@pytest.mark.asyncio
async def test_record_upserts_same_source(isolated_meta):
    await meta_mod.record_ingest("srcA", {"total_read": 1, "inserted": 1, "duplicates": 0, "discarded": 0})
    await meta_mod.record_ingest("srcA", {"total_read": 9, "inserted": 7, "duplicates": 1, "discarded": 1}, kind="push")

    rows = await meta_mod.get_meta(["srcA"])
    assert rows["srcA"]["last_total_read"] == 9
    assert rows["srcA"]["last_inserted"] == 7
    assert rows["srcA"]["last_job_kind"] == "push"


@pytest.mark.asyncio
async def test_get_meta_missing_source_returns_empty(isolated_meta):
    # Empty DB: nothing recorded
    rows = await meta_mod.get_meta(["never_existed"])
    assert rows == {}


@pytest.mark.asyncio
async def test_get_meta_no_filter_returns_all(isolated_meta):
    await meta_mod.record_ingest("a", {"total_read": 1, "inserted": 1, "duplicates": 0, "discarded": 0})
    await meta_mod.record_ingest("b", {"total_read": 2, "inserted": 2, "duplicates": 0, "discarded": 0})
    rows = await meta_mod.get_meta(None)
    assert set(rows.keys()) == {"a", "b"}
