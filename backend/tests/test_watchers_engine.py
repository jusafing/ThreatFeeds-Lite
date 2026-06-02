"""Tests for backend.watchers.engine (issue_local_006).

Covers match semantics (exact/wildcard/regex, severity gate, feed gate, AND of
conditions, any-field token), dataset selection (raw/normalized/all), high-water
skipping, and the run_watchers mode/trigger gating. Candidate fetching is
monkeypatched so the engine logic is tested in isolation from the real DBs.
"""
from __future__ import annotations

import pytest

from backend.db import watchers as store
from backend.watchers import engine


@pytest.fixture(autouse=True)
def _isolate_watchers_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", tmp_path / "watchers.db")
    # Keep the candidate window small + deterministic.
    monkeypatch.setattr(engine, "load_watcher_max_events", lambda: 1000)
    yield


def _w(**over):
    base = {
        "id": "w1",
        "name": "W1",
        "severity": "high",
        "dataset": "normalized",
        "feeds": [],
        "conditions": [],
        "mode": "realtime",
        "max_feed_events": 10,
        "enabled": True,
        "last_eval_raw_id": 0,
        "last_eval_norm_id": 0,
    }
    base.update(over)
    return base


# ── pure match helpers ──────────────────────────────────────────────────────

def test_row_matches_severity_gate():
    row = {"id": 1, "severity": "low", "source_name": "a"}
    assert engine.row_matches(row, _w(severity="high")) is False
    row2 = {"id": 1, "severity": "HIGH", "source_name": "a"}
    assert engine.row_matches(row2, _w(severity="high")) is True


def test_row_matches_feed_gate():
    row = {"id": 1, "severity": "high", "source_name": "feed-b"}
    assert engine.row_matches(row, _w(feeds=["feed-a"])) is False
    assert engine.row_matches(row, _w(feeds=["feed-b"])) is True
    assert engine.row_matches(row, _w(feeds=[])) is True  # empty == all


def test_exact_match_case_insensitive():
    row = {"id": 1, "severity": "high", "country": "Germany"}
    w = _w(conditions=[{"field": "country", "value": "germany", "match_type": "exact"}])
    assert engine.row_matches(row, w) is True


def test_wildcard_match():
    row = {"id": 1, "severity": "high", "cve_id": "CVE-2024-1234"}
    w = _w(conditions=[{"field": "cve_id", "value": "CVE-2024-*", "match_type": "wildcard"}])
    assert engine.row_matches(row, w) is True
    w2 = _w(conditions=[{"field": "cve_id", "value": "CVE-2023-*", "match_type": "wildcard"}])
    assert engine.row_matches(row, w2) is False


def test_regex_match():
    row = {"id": 1, "severity": "high", "indicator": "10.0.0.5"}
    w = _w(conditions=[{"field": "indicator", "value": r"^10\.0\.0\.\d+$", "match_type": "regex"}])
    assert engine.row_matches(row, w) is True


def test_any_field_token_matches_any_value():
    row = {"id": 1, "severity": "high", "title": "evil campaign", "actor": "APT99"}
    w = _w(conditions=[{"field": "*", "value": "APT99", "match_type": "exact"}])
    assert engine.row_matches(row, w) is True
    w2 = _w(conditions=[{"field": "", "value": "nomatch", "match_type": "exact"}])
    assert engine.row_matches(row, w2) is False


def test_multiple_conditions_are_anded():
    row = {"id": 1, "severity": "high", "actor": "APT99", "country": "DE"}
    w = _w(conditions=[
        {"field": "actor", "value": "APT99", "match_type": "exact"},
        {"field": "country", "value": "DE", "match_type": "exact"},
    ])
    assert engine.row_matches(row, w) is True
    w2 = _w(conditions=[
        {"field": "actor", "value": "APT99", "match_type": "exact"},
        {"field": "country", "value": "US", "match_type": "exact"},
    ])
    assert engine.row_matches(row, w2) is False


def test_missing_field_does_not_match():
    row = {"id": 1, "severity": "high"}
    w = _w(conditions=[{"field": "actor", "value": "APT99", "match_type": "exact"}])
    assert engine.row_matches(row, w) is False


# ── evaluate_watcher integration (monkeypatched candidate fetch) ─────────────

@pytest.mark.asyncio
async def test_evaluate_normalized_records_matches(monkeypatch):
    watcher = await store.create_watcher({
        "name": "High DE", "severity": "high", "dataset": "normalized",
        "feeds": [], "conditions": [{"field": "country", "value": "DE", "match_type": "exact"}],
        "mode": "realtime", "format": "json", "enabled": True,
    })
    norm_rows = [
        {"id": 1, "severity": "high", "country": "DE", "source_name": "feed-a"},
        {"id": 2, "severity": "high", "country": "US", "source_name": "feed-a"},
        {"id": 3, "severity": "low", "country": "DE", "source_name": "feed-a"},
    ]
    monkeypatch.setattr(engine, "query_normalized", _const(norm_rows))
    monkeypatch.setattr(engine, "query_entries", _const([]))

    n = await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"normalized"})
    assert n == 1
    events = await store.list_events(watcher["id"])
    assert len(events) == 1
    assert events[0]["source_entry_id"] == 1
    assert events[0]["dataset"] == "normalized"
    # High-water advanced to max id seen.
    w = await store.get_watcher(watcher["id"])
    assert w["last_eval_norm_id"] == 3


@pytest.mark.asyncio
async def test_evaluate_high_water_skips_seen_rows(monkeypatch):
    watcher = await store.create_watcher({
        "name": "All High", "severity": "high", "dataset": "normalized",
        "feeds": [], "conditions": [], "mode": "realtime", "enabled": True,
    })
    rows = [{"id": 5, "severity": "high", "source_name": "a"}]
    monkeypatch.setattr(engine, "query_normalized", _const(rows))
    monkeypatch.setattr(engine, "query_entries", _const([]))
    assert await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"normalized"}) == 1
    # Same row again: id 5 not > high-water 5 -> skipped.
    assert await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"normalized"}) == 0


@pytest.mark.asyncio
async def test_evaluate_dataset_all_scans_both(monkeypatch):
    watcher = await store.create_watcher({
        "name": "Both", "severity": "high", "dataset": "all",
        "feeds": [], "conditions": [], "mode": "realtime", "enabled": True,
    })
    monkeypatch.setattr(engine, "query_entries", _const([{"id": 1, "severity": "high", "source": "raw-a"}]))
    monkeypatch.setattr(engine, "query_normalized", _const([{"id": 1, "severity": "high", "source_name": "norm-a"}]))
    n = await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"raw", "normalized"})
    assert n == 2
    datasets = {e["dataset"] for e in await store.list_events(watcher["id"])}
    assert datasets == {"raw", "normalized"}


@pytest.mark.asyncio
async def test_evaluate_raw_watcher_ignores_normalized_scan(monkeypatch):
    watcher = await store.create_watcher({
        "name": "Raw Only", "severity": "high", "dataset": "raw",
        "feeds": [], "conditions": [], "mode": "realtime", "enabled": True,
    })
    monkeypatch.setattr(engine, "query_entries", _const([{"id": 1, "severity": "high", "source": "a"}]))
    monkeypatch.setattr(engine, "query_normalized", _const([{"id": 9, "severity": "high", "source_name": "a"}]))
    # Only a normalized scan requested: a raw-dataset watcher does nothing.
    assert await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"normalized"}) == 0


@pytest.mark.asyncio
async def test_run_watchers_gates_by_mode(monkeypatch):
    await store.create_watcher({
        "name": "RT", "severity": "high", "dataset": "normalized", "feeds": [],
        "conditions": [], "mode": "realtime", "enabled": True,
    })
    await store.create_watcher({
        "name": "Sch", "severity": "high", "dataset": "normalized", "feeds": [],
        "conditions": [], "mode": "scheduled", "enabled": True,
    })
    monkeypatch.setattr(engine, "query_normalized", _const([{"id": 1, "severity": "high", "source_name": "a"}]))
    monkeypatch.setattr(engine, "query_entries", _const([]))
    # normalize trigger only evaluates realtime watchers.
    res = await engine.run_watchers("normalize", {"normalized"})
    assert res["evaluated"] == 1


def _const(rows):
    async def _fn(*args, **kwargs):
        return list(rows)
    return _fn
