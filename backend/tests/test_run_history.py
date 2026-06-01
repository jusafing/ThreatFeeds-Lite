"""Tests for backend.normalizer.run_history (prompts-039).

Covers: init idempotency, record/list round-trip, newest-first ordering,
sources JSON round-trip, and the 500-row retention cap.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.normalizer import run_history as run_history_mod
from backend.normalizer.run_history import (
    _MAX_ROWS,
    init_run_history_db,
    list_runs,
    record_run,
)


@pytest.fixture(autouse=True)
def _isolate_run_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "run_history.db"
    monkeypatch.setattr(run_history_mod, "_RUN_DB_PATH", fake_db)
    yield


@pytest.mark.asyncio
async def test_init_creates_schema_and_is_idempotent():
    await init_run_history_db()
    await init_run_history_db()  # second call must not error
    with sqlite3.connect(run_history_mod._RUN_DB_PATH) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='run_history'"
        ).fetchall()
        ver = conn.execute("SELECT version FROM schema_version").fetchone()
    assert tables, "run_history table must exist after init"
    assert ver[0] == run_history_mod._RUN_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_record_and_list_round_trip():
    await record_run(
        trigger="manual",
        mode="smart",
        status="ok",
        processed=10,
        inserted=8,
        errors=1,
        proposal_id=42,
        proposal_name="Proposal-2026",
        sources=["feed-a", "feed-b"],
        warning=None,
    )
    rows = await list_runs()
    assert len(rows) == 1
    row = rows[0]
    assert row["trigger"] == "manual"
    assert row["mode"] == "smart"
    assert row["status"] == "ok"
    assert row["processed"] == 10
    assert row["inserted"] == 8
    assert row["errors"] == 1
    assert row["proposal_id"] == 42
    assert row["proposal_name"] == "Proposal-2026"
    assert row["sources"] == ["feed-a", "feed-b"]


@pytest.mark.asyncio
async def test_list_is_newest_first():
    await record_run(trigger="schedule", mode="auto", status="ok")
    await record_run(trigger="manual", mode="smart", status="ok")
    rows = await list_runs()
    assert [r["trigger"] for r in rows] == ["manual", "schedule"]


@pytest.mark.asyncio
async def test_auto_run_has_empty_sources_and_no_proposal():
    await record_run(trigger="schedule", mode="auto", status="ok", processed=3)
    rows = await list_runs()
    assert rows[0]["sources"] == []
    assert rows[0]["proposal_id"] is None
    assert rows[0]["proposal_name"] is None


@pytest.mark.asyncio
async def test_retention_caps_at_max_rows():
    for i in range(_MAX_ROWS + 25):
        await record_run(trigger="schedule", mode="auto", status="ok", processed=i)
    rows = await list_runs(limit=_MAX_ROWS + 100)
    assert len(rows) == _MAX_ROWS
    # The newest row (highest processed) must survive; the oldest must be gone.
    assert rows[0]["processed"] == _MAX_ROWS + 24


@pytest.mark.asyncio
async def test_list_respects_limit():
    for _ in range(5):
        await record_run(trigger="manual", mode="auto", status="ok")
    rows = await list_runs(limit=2)
    assert len(rows) == 2
