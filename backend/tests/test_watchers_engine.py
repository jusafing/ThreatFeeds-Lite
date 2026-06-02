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

def test_row_matches_ignores_watcher_severity():
    # review_02: watcher severity is a label only — it must NOT gate matching.
    # A condition is what decides the trigger, regardless of event severity.
    w = _w(severity="critical", conditions=[{"field": "country", "value": "DE", "match_type": "exact"}])
    assert engine.row_matches({"id": 1, "severity": "low", "country": "DE", "source_name": "a"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "critical", "country": "DE", "source_name": "a"}, w) is True
    # Non-matching condition still blocks regardless of severity.
    assert engine.row_matches({"id": 1, "severity": "critical", "country": "US", "source_name": "a"}, w) is False


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


def test_numeric_gte_match():
    w = _w(conditions=[{"field": "confidence", "value": "80", "match_type": "gte"}])
    assert engine.row_matches({"id": 1, "severity": "high", "confidence": "90"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "high", "confidence": "80"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "high", "confidence": "79"}, w) is False
    # Non-numeric target never matches a numeric condition.
    assert engine.row_matches({"id": 1, "severity": "high", "confidence": "n/a"}, w) is False


def test_numeric_lte_match():
    w = _w(conditions=[{"field": "score", "value": "5", "match_type": "lte"}])
    assert engine.row_matches({"id": 1, "severity": "high", "score": "3"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "high", "score": "5"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "high", "score": "6"}, w) is False


def test_numeric_match_handles_floats():
    w = _w(conditions=[{"field": "score", "value": "2.5", "match_type": "gte"}])
    assert engine.row_matches({"id": 1, "severity": "high", "score": "2.50"}, w) is True
    assert engine.row_matches({"id": 1, "severity": "high", "score": "2.4"}, w) is False


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
    # review_02: severity is not a gate, so BOTH DE rows match (id 1 high + id 3
    # low); only the US row (id 2) is filtered by the country condition.
    assert n == 2
    events = await store.list_events(watcher["id"])
    assert len(events) == 2
    assert {e["source_entry_id"] for e in events} == {1, 3}
    assert all(e["dataset"] == "normalized" for e in events)
    # High-water advanced per-source to the max id seen for that source.
    hw = await store.get_high_water_map(watcher["id"], "normalized")
    assert hw == {"feed-a": 3}


@pytest.mark.asyncio
async def test_evaluate_high_water_skips_seen_rows(monkeypatch):
    watcher = await store.create_watcher({
        "name": "All High", "severity": "high", "dataset": "normalized",
        "feeds": [], "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
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
        "feeds": [], "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
    })
    monkeypatch.setattr(engine, "query_entries", _const([{"id": 1, "severity": "high", "source": "raw-a"}]))
    monkeypatch.setattr(engine, "query_normalized", _const([{"id": 1, "severity": "high", "source_name": "norm-a"}]))
    n = await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"raw", "normalized"})
    assert n == 2
    datasets = {e["dataset"] for e in await store.list_events(watcher["id"])}
    assert datasets == {"raw", "normalized"}


@pytest.mark.asyncio
async def test_evaluate_two_sources_with_overlapping_ids_both_trigger(monkeypatch):
    """review_02b: raw `entries` ids are per-source sequences. Two feeds each
    emitting id 1 must BOTH trigger (no global high-water collision) and the
    high-water mark advances independently per source."""
    watcher = await store.create_watcher({
        "name": "Raw Both", "severity": "high", "dataset": "raw",
        "feeds": [], "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
    })
    raw_rows = [
        {"id": 1, "severity": "high", "source": "feed-a"},
        {"id": 1, "severity": "high", "source": "feed-b"},
    ]
    monkeypatch.setattr(engine, "query_entries", _const(raw_rows))
    monkeypatch.setattr(engine, "query_normalized", _const([]))

    n = await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"raw"})
    assert n == 2  # both id-1 rows trigger despite identical id
    assert await store.get_high_water_map(watcher["id"], "raw") == {"feed-a": 1, "feed-b": 1}
    # Re-scan: each source's id 1 is not > its own mark of 1 -> nothing new.
    assert await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"raw"}) == 0


@pytest.mark.asyncio
async def test_any_field_match_excludes_internal_columns(monkeypatch):
    """review_02b: the any-field ('*') token searches real fields but must skip
    internal serialization columns (raw blob, dedup_key, etc.)."""
    watcher = await store.create_watcher({
        "name": "Any Hit", "severity": "high", "dataset": "raw",
        "feeds": [], "conditions": [{"field": "*", "value": "needle", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
    })
    rows = [
        # 'needle' only appears inside the internal raw blob -> must NOT match.
        {"id": 1, "severity": "high", "source": "feed-a", "raw": "needle in raw", "actor": "APT1"},
        # 'needle' in a real custom field -> matches.
        {"id": 2, "severity": "high", "source": "feed-a", "actor": "needle"},
    ]
    monkeypatch.setattr(engine, "query_entries", _const(rows))
    monkeypatch.setattr(engine, "query_normalized", _const([]))

    n = await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"raw"})
    assert n == 1
    assert {e["source_entry_id"] for e in await store.list_events(watcher["id"])} == {2}


@pytest.mark.asyncio
async def test_evaluate_raw_watcher_ignores_normalized_scan(monkeypatch):
    watcher = await store.create_watcher({
        "name": "Raw Only", "severity": "high", "dataset": "raw",
        "feeds": [], "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
    })
    monkeypatch.setattr(engine, "query_entries", _const([{"id": 1, "severity": "high", "source": "a"}]))
    monkeypatch.setattr(engine, "query_normalized", _const([{"id": 9, "severity": "high", "source_name": "a"}]))
    # Only a normalized scan requested: a raw-dataset watcher does nothing.
    assert await engine.evaluate_watcher(await store.get_watcher(watcher["id"]), {"normalized"}) == 0


@pytest.mark.asyncio
async def test_run_watchers_gates_by_mode(monkeypatch):
    await store.create_watcher({
        "name": "RT", "severity": "high", "dataset": "normalized", "feeds": [],
        "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "realtime", "enabled": True,
    })
    await store.create_watcher({
        "name": "Sch", "severity": "high", "dataset": "normalized", "feeds": [],
        "conditions": [{"field": "severity", "value": "high", "match_type": "exact"}],
        "mode": "scheduled", "enabled": True,
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


# ── schedule_realtime_ingest_eval (review_02 sync-ingest hook) ───────────────

@pytest.mark.asyncio
async def test_schedule_realtime_ingest_eval_runs_on_insert(monkeypatch):
    import asyncio

    calls: list[tuple] = []

    async def _fake_run(trigger, datasets=None):
        calls.append((trigger, datasets))
        return {"evaluated": 0, "triggered": 0}

    monkeypatch.setattr(engine, "run_watchers", _fake_run)
    engine.schedule_realtime_ingest_eval(2)
    # Task is scheduled on the running loop; let it run.
    await asyncio.sleep(0)
    assert calls == [("ingest", {"raw"})]


@pytest.mark.asyncio
async def test_schedule_realtime_ingest_eval_noop_when_zero(monkeypatch):
    import asyncio

    calls: list[tuple] = []

    async def _fake_run(trigger, datasets=None):
        calls.append((trigger, datasets))

    monkeypatch.setattr(engine, "run_watchers", _fake_run)
    engine.schedule_realtime_ingest_eval(0)
    engine.schedule_realtime_ingest_eval(None)
    await asyncio.sleep(0)
    assert calls == []


def test_schedule_realtime_ingest_eval_no_loop_is_safe():
    # Called outside any event loop (sync context): must not raise.
    engine.schedule_realtime_ingest_eval(5)
