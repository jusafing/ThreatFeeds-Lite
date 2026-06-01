"""Tests for the 4-counter ingest summary (total_read / inserted / duplicates / discarded)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_local_feed_returns_four_counter_shape():
    """Every ingest result dict must include total_read, duplicates, discarded."""
    from backend.ingestion.local_feed import ingest_local_feed

    async def fake_insert(source_name, entry):
        return "inserted"

    data = json.dumps([{"indicator": "1.1.1.1"}, {"indicator": "2.2.2.2"}]).encode()

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(data, "summary_src")

    assert result["total_read"] == 2
    assert result["inserted"] == 2
    assert result["duplicates"] == 0
    assert result["discarded"] == 0
    # Back-compat: skipped == duplicates + discarded
    assert result["skipped"] == result["duplicates"] + result["discarded"]


@pytest.mark.anyio
async def test_local_feed_distinguishes_duplicates_from_discarded():
    """Duplicates (insert returns 'duplicate') and discards (insert returns 'error'
    or non-dict input) must be counted separately."""
    from backend.ingestion.local_feed import ingest_local_feed

    calls = {"n": 0}

    async def fake_insert(source_name, entry):
        calls["n"] += 1
        # 1st → inserted, 2nd → duplicate, 3rd → error
        return ["inserted", "duplicate", "error"][calls["n"] - 1]

    data = json.dumps([
        {"indicator": "a"},
        {"indicator": "b"},
        {"indicator": "c"},
    ]).encode()

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(data, "split_src")

    assert result["total_read"] == 3
    assert result["inserted"] == 1
    assert result["duplicates"] == 1
    assert result["discarded"] == 1
    assert result["skipped"] == 2
