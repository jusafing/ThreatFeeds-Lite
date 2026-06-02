"""Tests for backend.db.watchers store (issue_local_006).

Covers: slugify, definition validation, CRUD round-trip, enable toggle,
trigger dedup + retention prune, high-water advance, and event reads.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.db import watchers as store


@pytest.fixture(autouse=True)
def _isolate_watchers_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", tmp_path / "watchers.db")
    yield


def _defn(**over):
    base = {
        "name": "Critical CVEs",
        "severity": "critical",
        "dataset": "normalized",
        "feeds": ["feed-a"],
        "conditions": [{"field": "cve_id", "value": "CVE-2024-*", "match_type": "wildcard"}],
        "mode": "realtime",
        "interval_sec": 120,
        "format": "json",
        "max_feed_events": 10,
        "enabled": True,
    }
    base.update(over)
    return base


def test_slugify_basic():
    assert store.slugify("Critical CVEs") == "critical-cves"
    assert store.slugify("  My  Watcher!! ") == "my-watcher"


def test_slugify_rejects_empty():
    with pytest.raises(ValueError):
        store.slugify("!!!")


def test_validate_rejects_bad_enum():
    with pytest.raises(ValueError):
        store.validate_definition(_defn(severity="bogus"))
    with pytest.raises(ValueError):
        store.validate_definition(_defn(dataset="nope"))
    with pytest.raises(ValueError):
        store.validate_definition(_defn(mode="cron"))
    with pytest.raises(ValueError):
        store.validate_definition(_defn(format="yaml"))


def test_validate_rejects_invalid_regex_condition():
    with pytest.raises(ValueError) as exc:
        store.validate_definition(
            _defn(conditions=[{"field": "title", "value": "(", "match_type": "regex"}])
        )
    assert "regex" in str(exc.value)


def test_validate_rejects_empty_condition_value():
    with pytest.raises(ValueError):
        store.validate_definition(
            _defn(conditions=[{"field": "title", "value": "", "match_type": "exact"}])
        )


@pytest.mark.asyncio
async def test_create_and_get_round_trip():
    w = await store.create_watcher(_defn())
    assert w["id"] == "critical-cves"
    assert w["dataset"] == "normalized"
    assert w["feeds"] == ["feed-a"]
    assert w["conditions"][0]["match_type"] == "wildcard"
    assert w["enabled"] is True
    assert w["trigger_count"] == 0
    got = await store.get_watcher("critical-cves")
    assert got["name"] == "Critical CVEs"


@pytest.mark.asyncio
async def test_create_duplicate_id_rejected():
    await store.create_watcher(_defn())
    with pytest.raises(ValueError):
        await store.create_watcher(_defn())


@pytest.mark.asyncio
async def test_update_and_toggle():
    await store.create_watcher(_defn())
    upd = await store.update_watcher("critical-cves", _defn(severity="high", enabled=False))
    assert upd["severity"] == "high"
    assert upd["enabled"] is False
    toggled = await store.set_enabled("critical-cves", True)
    assert toggled["enabled"] is True
    assert await store.update_watcher("missing", _defn()) is None
    assert await store.set_enabled("missing", True) is None


@pytest.mark.asyncio
async def test_delete_removes_watcher_and_events():
    await store.create_watcher(_defn())
    await store.record_triggers(
        "critical-cves",
        [{"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a", "event": {"id": 1}}],
        max_events=100,
    )
    assert await store.count_events("critical-cves") == 1
    assert await store.delete_watcher("critical-cves") is True
    assert await store.get_watcher("critical-cves") is None
    assert await store.count_events("critical-cves") == 0
    assert await store.delete_watcher("critical-cves") is False


@pytest.mark.asyncio
async def test_record_triggers_dedups_and_counts():
    await store.create_watcher(_defn())
    rows = [
        {"dataset": "normalized", "source_entry_id": 1, "source_name": "a", "event": {"id": 1}},
        {"dataset": "normalized", "source_entry_id": 2, "source_name": "a", "event": {"id": 2}},
    ]
    n1 = await store.record_triggers("critical-cves", rows, max_events=100)
    assert n1 == 2
    # Re-record same rows: deduped, zero new.
    n2 = await store.record_triggers("critical-cves", rows, max_events=100)
    assert n2 == 0
    w = await store.get_watcher("critical-cves")
    assert w["trigger_count"] == 2


@pytest.mark.asyncio
async def test_record_triggers_prunes_to_max_events():
    await store.create_watcher(_defn())
    rows = [
        {"dataset": "raw", "source_entry_id": i, "source_name": "a", "event": {"id": i}}
        for i in range(1, 11)
    ]
    await store.record_triggers("critical-cves", rows, max_events=5)
    assert await store.count_events("critical-cves") == 5
    events = await store.list_events("critical-cves", limit=10)
    # Newest (highest source_entry_id) survive.
    kept = sorted(e["source_entry_id"] for e in events)
    assert kept == [6, 7, 8, 9, 10]


@pytest.mark.asyncio
async def test_high_water_only_advances():
    await store.create_watcher(_defn())
    await store.update_high_water("critical-cves", raw_id=5, norm_id=9)
    w = await store.get_watcher("critical-cves")
    assert w["last_eval_raw_id"] == 5
    assert w["last_eval_norm_id"] == 9
    # Lower values must not move the mark backwards.
    await store.update_high_water("critical-cves", raw_id=2, norm_id=3)
    w = await store.get_watcher("critical-cves")
    assert w["last_eval_raw_id"] == 5
    assert w["last_eval_norm_id"] == 9


def test_list_scheduled_watchers_sync_missing_db_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", tmp_path / "nope.db")
    assert store.list_scheduled_watchers_sync() == []


@pytest.mark.asyncio
async def test_list_scheduled_watchers_sync_returns_scheduled_only():
    await store.create_watcher(_defn(name="Sched One", mode="scheduled", interval_sec=30))
    await store.create_watcher(_defn(name="Realtime One", mode="realtime"))
    await store.create_watcher(_defn(name="Sched Off", mode="scheduled", enabled=False))
    rows = store.list_scheduled_watchers_sync()
    ids = {r["id"]: r["interval_sec"] for r in rows}
    assert ids == {"sched-one": 30}
