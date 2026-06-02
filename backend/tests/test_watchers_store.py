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
    assert ver == store._WATCHERS_SCHEMA_VERSION
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


@pytest.mark.asyncio
async def test_migration_v2_to_v3_adds_publish_columns(tmp_path, monkeypatch):
    """A v2 DB lacking the publish-target / delivery columns gains them on init
    (issue_local_007), idempotently."""
    db_path = tmp_path / "v2.db"
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    # Minimal v2 tables WITHOUT the v3 columns.
    conn.execute(
        "CREATE TABLE watchers (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE watcher_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "watcher_id TEXT NOT NULL, dataset TEXT NOT NULL, source_entry_id INTEGER, "
        "source_name TEXT, triggered_at TEXT, event_json TEXT)"
    )
    conn.executescript(store.CREATE_SCHEMA_VERSION_TABLE)
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.commit()
    conn.close()

    await store.init_watchers_db()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    wcols = {r[1] for r in conn.execute("PRAGMA table_info(watchers)")}
    assert {"publish_target", "webhook_url", "auth_header", "auth_value"} <= wcols
    ecols = {r[1] for r in conn.execute("PRAGMA table_info(watcher_events)")}
    assert {"delivery_status", "delivery_error", "delivered_at"} <= ecols
    conn.close()

    # Idempotent.
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


# ── Publish target validation (issue_local_007) ─────────────────────────────


def test_validate_defaults_publish_target_to_local():
    out = store.validate_definition(_defn())
    assert out["publish_target"] == "local"
    # A local target carries no remote URL/auth even if supplied.
    assert out["webhook_url"] == ""
    assert out["auth_header"] == ""
    assert out["auth_value"] == ""


def test_validate_local_clears_supplied_url_and_auth():
    out = store.validate_definition(
        _defn(publish_target="local", webhook_url="https://x.example/h", auth_header="X", auth_value="y")
    )
    assert out["webhook_url"] == ""
    assert out["auth_header"] == ""
    assert out["auth_value"] == ""


def test_validate_rejects_unknown_publish_target():
    with pytest.raises(ValueError):
        store.validate_definition(_defn(publish_target="carrier-pigeon"))


def test_validate_webhook_requires_url():
    with pytest.raises(ValueError):
        store.validate_definition(_defn(publish_target="webhook", webhook_url=""))


def test_validate_webhook_rejects_non_http_url():
    with pytest.raises(ValueError):
        store.validate_definition(
            _defn(publish_target="webhook", webhook_url="ftp://host/x")
        )


def test_validate_auth_header_value_must_be_paired():
    with pytest.raises(ValueError):
        store.validate_definition(
            _defn(publish_target="http", webhook_url="https://x.example/in", auth_header="Authorization")
        )
    with pytest.raises(ValueError):
        store.validate_definition(
            _defn(publish_target="http", webhook_url="https://x.example/in", auth_value="Bearer z")
        )


def test_validate_webhook_accepts_http_and_https():
    for url in ("http://internal.lan/hook", "https://hooks.example.com/in"):
        out = store.validate_definition(_defn(publish_target="webhook", webhook_url=url))
        assert out["webhook_url"] == url


@pytest.mark.asyncio
async def test_create_and_get_round_trips_publish_fields():
    created = await store.create_watcher(
        _defn(
            name="Webhook W",
            publish_target="webhook",
            webhook_url="https://hooks.example.com/in",
            auth_header="Authorization",
            auth_value="Bearer secret",
        )
    )
    assert created["publish_target"] == "webhook"
    assert created["webhook_url"] == "https://hooks.example.com/in"
    assert created["auth_header"] == "Authorization"
    assert created["auth_value"] == "Bearer secret"
    # Fresh read reflects the persisted values + zeroed delivery aggregates.
    got = await store.get_watcher(created["id"])
    assert got["publish_target"] == "webhook"
    assert got["delivery_error_count"] == 0
    assert got["last_delivery_error"] is None


@pytest.mark.asyncio
async def test_update_switches_target_and_clears_remote_fields():
    w = await store.create_watcher(
        _defn(name="Switchy", publish_target="http", webhook_url="https://a.example/in")
    )
    updated = await store.update_watcher(
        w["id"], _defn(name="Switchy", publish_target="local")
    )
    assert updated["publish_target"] == "local"
    assert updated["webhook_url"] in ("", None)


@pytest.mark.asyncio
async def test_delivery_status_and_pending_listing():
    w = await store.create_watcher(
        _defn(name="Deliverable", dataset="normalized", publish_target="webhook",
              webhook_url="https://x.example/in")
    )
    wid = w["id"]
    triggers = [
        {"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a",
         "event": {"id": 1, "cve_id": "CVE-2024-1"}},
        {"dataset": "normalized", "source_entry_id": 2, "source_name": "feed-a",
         "event": {"id": 2, "cve_id": "CVE-2024-2"}},
    ]
    await store.record_triggers(wid, triggers, max_events=100)

    # Both events are pending initially.
    pending = await store.list_pending_deliveries(wid)
    assert len(pending) == 2
    ids = sorted(p["id"] for p in pending)

    # Mark one ok, one error.
    await store.update_delivery_status(ids[0], "ok", None)
    await store.update_delivery_status(ids[1], "error", "HTTP 500")

    # Only the errored one remains pending (retried next pass).
    pending2 = await store.list_pending_deliveries(wid)
    assert [p["id"] for p in pending2] == [ids[1]]

    # The watcher aggregate now reports one delivery error.
    got = await store.get_watcher(wid)
    assert got["delivery_error_count"] == 1
    assert got["last_delivery_error"] == "HTTP 500"

    # list_events exposes per-event delivery columns.
    events = {e["id"]: e for e in await store.list_events(wid)}
    assert events[ids[0]]["delivery_status"] == "ok"
    assert events[ids[0]]["delivery_error"] is None
    assert events[ids[1]]["delivery_status"] == "error"
    assert events[ids[1]]["delivery_error"] == "HTTP 500"

