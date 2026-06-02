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


def test_validate_rejects_zero_conditions():
    with pytest.raises(ValueError) as exc:
        store.validate_definition(_defn(conditions=[]))
    assert "condition" in str(exc.value).lower()


def test_validate_accepts_numeric_match_types():
    out = store.validate_definition(
        _defn(conditions=[{"field": "confidence", "value": "80", "match_type": "gte"}])
    )
    assert out["conditions"][0]["match_type"] == "gte"
    out2 = store.validate_definition(
        _defn(conditions=[{"field": "confidence", "value": "20", "match_type": "lte"}])
    )
    assert out2["conditions"][0]["match_type"] == "lte"


def test_validate_rejects_non_numeric_value_for_numeric_match_type():
    with pytest.raises(ValueError) as exc:
        store.validate_definition(
            _defn(conditions=[{"field": "confidence", "value": "high", "match_type": "gte"}])
        )
    assert "numeric" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_last_triggered_at_reflects_latest_event():
    await store.create_watcher(_defn())
    got = await store.get_watcher("critical-cves")
    assert got["last_triggered_at"] is None
    await store.record_triggers(
        "critical-cves",
        [{"dataset": "normalized", "source_entry_id": 1, "source_name": "a", "event": {"id": 1}}],
        max_events=100,
    )
    got = await store.get_watcher("critical-cves")
    assert got["last_triggered_at"] is not None
    listed = await store.list_watchers()
    assert listed[0]["last_triggered_at"] == got["last_triggered_at"]


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


@pytest.mark.asyncio
async def test_high_water_map_is_per_source_and_only_advances():
    await store.create_watcher(_defn())
    assert await store.get_high_water_map("critical-cves", "raw") == {}
    await store.update_high_water_map("critical-cves", "raw", {"feed-a": 10, "feed-b": 3})
    assert await store.get_high_water_map("critical-cves", "raw") == {"feed-a": 10, "feed-b": 3}
    # Independent per source; lower values never move a mark backwards.
    await store.update_high_water_map("critical-cves", "raw", {"feed-a": 4, "feed-b": 7})
    assert await store.get_high_water_map("critical-cves", "raw") == {"feed-a": 10, "feed-b": 7}
    # Datasets are tracked separately.
    assert await store.get_high_water_map("critical-cves", "normalized") == {}


@pytest.mark.asyncio
async def test_record_triggers_dedup_is_per_source():
    """Same source_entry_id in different feeds are distinct events; same id in
    the same feed dedups (review_02b: dedup key includes source_name)."""
    await store.create_watcher(_defn())
    rows = [
        {"dataset": "raw", "source_entry_id": 1, "source_name": "feed-a", "event": {"id": 1}},
        {"dataset": "raw", "source_entry_id": 1, "source_name": "feed-b", "event": {"id": 1}},
    ]
    n1 = await store.record_triggers("critical-cves", rows, max_events=100)
    assert n1 == 2  # not collapsed despite identical id
    n2 = await store.record_triggers("critical-cves", rows, max_events=100)
    assert n2 == 0  # now deduped per (watcher, dataset, id, source)
    assert await store.count_events("critical-cves") == 2


@pytest.mark.asyncio
async def test_delete_removes_high_water(monkeypatch):
    await store.create_watcher(_defn())
    await store.update_high_water_map("critical-cves", "raw", {"feed-a": 5})
    assert await store.delete_watcher("critical-cves") is True
    assert await store.get_high_water_map("critical-cves", "raw") == {}


@pytest.mark.asyncio
async def test_migration_v1_to_v2(tmp_path, monkeypatch):
    """A pre-existing v1 DB (global dedup index, no high_water table) is migrated
    forward idempotently to v2."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", db_path)
    # Build a minimal v1 schema by hand.
    conn = sqlite3.connect(db_path)
    conn.executescript(store.CREATE_WATCHERS_TABLE)
    conn.executescript(store.CREATE_WATCHER_EVENTS_TABLE)
    conn.execute(
        "CREATE UNIQUE INDEX idx_watcher_events_dedup "
        "ON watcher_events (watcher_id, dataset, source_entry_id)"
    )
    conn.executescript(store.CREATE_SCHEMA_VERSION_TABLE)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    # init runs the migration.
    await store.init_watchers_db()

    conn = sqlite3.connect(db_path)
    ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver == 2
    # high_water table now exists.
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "watcher_high_water" in tables
    # dedup index now includes source_name.
    idx_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_watcher_events_dedup'"
    ).fetchone()[0]
    assert "source_name" in idx_sql
    conn.close()

    # Idempotent: a second init is a no-op.
    await store.init_watchers_db()


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
