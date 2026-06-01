"""Tests for the prompts-038 consolidated re-apply sweep.

Covers:
  * normalizer.db.delete_normalized_for_sources — scoped delete, empty no-op,
    returns the deleted count;
  * smart_runner.reapply_consolidated_to_sources — clears normalized output for
    the named feeds and resets their raw ``normalized`` flag so the next run
    reprocesses them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import backend.db.manager as mgr
import backend.normalizer.db as norm_db_mod
from backend.normalizer import smart_runner as smart_runner_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(norm_db_mod, "_NORM_DB_PATH", tmp_path / "normalized.db")
    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)
    yield


async def _seed_norm(*feeds: str) -> None:
    await norm_db_mod.init_norm_db()
    for i, feed in enumerate(feeds, start=1):
        await norm_db_mod.insert_normalized(
            {"source_entry_id": i, "source_name": feed, "title": "x"}
        )


def _seed_source(tmp_path: Path, feed: str, *, normalized: int, count: int) -> None:
    with sqlite3.connect(tmp_path / f"{feed}.db") as con:
        con.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, normalized INTEGER NOT NULL)"
        )
        for _ in range(count):
            con.execute("INSERT INTO entries (normalized) VALUES (?)", (normalized,))
        con.commit()


# ── delete_normalized_for_sources ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_normalized_scoped_to_named_sources():
    await _seed_norm("feed-a", "feed-b", "feed-c")
    deleted = await norm_db_mod.delete_normalized_for_sources(["feed-a", "feed-b"])
    assert deleted == 2
    remaining = {r["source_name"] for r in await norm_db_mod.query_normalized(limit=100)}
    assert remaining == {"feed-c"}


@pytest.mark.asyncio
async def test_delete_normalized_empty_is_noop():
    await _seed_norm("feed-a")
    assert await norm_db_mod.delete_normalized_for_sources([]) == 0
    assert await norm_db_mod.delete_normalized_for_sources(["", None]) == 0  # type: ignore[list-item]
    remaining = {r["source_name"] for r in await norm_db_mod.query_normalized(limit=100)}
    assert remaining == {"feed-a"}


# ── reapply_consolidated_to_sources ────────────────────────────────────────


@pytest.mark.asyncio
async def test_reapply_clears_output_and_resets_flags(tmp_path: Path):
    _seed_source(tmp_path, "feed-a", normalized=1, count=3)
    _seed_source(tmp_path, "feed-b", normalized=1, count=2)
    _seed_source(tmp_path, "feed-other", normalized=1, count=4)
    await _seed_norm("feed-a", "feed-b", "feed-other")

    reset = await smart_runner_mod.reapply_consolidated_to_sources(["feed-a", "feed-b"])
    # 3 + 2 raw rows reset across the two mapping feeds.
    assert reset == 5

    # Raw flags reset only for the mapping feeds.
    for feed, expected_zero in (("feed-a", 3), ("feed-b", 2), ("feed-other", 0)):
        with sqlite3.connect(tmp_path / f"{feed}.db") as con:
            n = con.execute(
                "SELECT COUNT(*) FROM entries WHERE normalized=0"
            ).fetchone()[0]
            assert n == expected_zero

    # Normalized output cleared only for the mapping feeds.
    remaining = {r["source_name"] for r in await norm_db_mod.query_normalized(limit=100)}
    assert remaining == {"feed-other"}


@pytest.mark.asyncio
async def test_reapply_empty_is_noop():
    assert await smart_runner_mod.reapply_consolidated_to_sources([]) == 0
