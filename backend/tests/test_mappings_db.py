"""Tests for backend.normalizer.mappings (021F Step 1).

Covers: init idempotency, CRUD, activate atomicity (incl. partial unique
index race protection), idempotent yaml migration, yaml snapshot
regeneration, diff helper.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from backend.normalizer import config as config_mod
from backend.normalizer import mappings as mappings_mod
from backend.normalizer.mappings import (
    activate_version,
    create_version,
    diff_mappings,
    get_active_version,
    get_all_active_mappings,
    get_version,
    init_mappings_db,
    list_versions,
    migrate_yaml_manual_mappings_once,
    regenerate_yaml_snapshot,
)


@pytest.fixture(autouse=True)
def _isolate_mappings_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "mapping_versions.db"
    monkeypatch.setattr(mappings_mod, "_MAPPINGS_DB_PATH", fake_db)
    yield


@pytest.fixture
def _isolate_yaml(tmp_path, monkeypatch):
    """Point config.py at a per-test yaml file."""
    fake_yaml = tmp_path / "normalizer-config.yaml"
    monkeypatch.setattr(config_mod, "_NORMALIZER_CONFIG_PATH", fake_yaml)
    return fake_yaml


# ---------------------------------------------------------------------------
# init / schema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_creates_schema_and_is_idempotent():
    await init_mappings_db()
    await init_mappings_db()  # second call must not error
    # Verify partial unique index exists (the race-safety net).
    with sqlite3.connect(mappings_mod._MAPPINGS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_mv_active_per_source'"
        ).fetchall()
    assert rows, "partial unique index must exist after init"


# ---------------------------------------------------------------------------
# create / list / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_roundtrip():
    vid = await create_version(
        source_name="src-a",
        mapping={"raw_title": "title", "raw_ts": "published_at"},
        origin="manual",
        note="first one",
    )
    fetched = await get_version(vid)
    assert fetched is not None
    assert fetched["source_name"] == "src-a"
    assert fetched["mapping"] == {"raw_title": "title", "raw_ts": "published_at"}
    assert fetched["origin"] == "manual"
    assert fetched["active"] is False
    assert fetched["note"] == "first one"


@pytest.mark.asyncio
async def test_get_version_missing_returns_none():
    await init_mappings_db()
    assert await get_version(99999) is None


@pytest.mark.asyncio
async def test_list_versions_newest_first_and_filtered():
    v1 = await create_version(source_name="s1", mapping={"a": "title"}, origin="migration")
    v2 = await create_version(source_name="s2", mapping={"b": "title"}, origin="manual")
    v3 = await create_version(source_name="s1", mapping={"c": "title"}, origin="manual")

    s1_rows = await list_versions("s1")
    assert [r["id"] for r in s1_rows] == [v3, v1]

    all_rows = await list_versions()
    assert [r["id"] for r in all_rows] == [v3, v2, v1]


@pytest.mark.asyncio
async def test_create_rejects_invalid_origin():
    with pytest.raises(ValueError):
        await create_version(source_name="s", mapping={}, origin="bogus")


@pytest.mark.asyncio
async def test_create_rejects_non_dict_mapping():
    with pytest.raises(ValueError):
        await create_version(source_name="s", mapping=["not", "a", "dict"], origin="manual")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_promotes_and_demotes():
    v1 = await create_version(source_name="s", mapping={"a": "title"}, origin="manual")
    v2 = await create_version(source_name="s", mapping={"b": "title"}, origin="manual")
    await activate_version(v1)
    assert (await get_active_version("s"))["id"] == v1
    await activate_version(v2)
    active = await get_active_version("s")
    assert active["id"] == v2
    # v1 must now be demoted.
    v1_after = await get_version(v1)
    assert v1_after["active"] is False


@pytest.mark.asyncio
async def test_activate_unknown_id_raises_lookup_error():
    await init_mappings_db()
    with pytest.raises(LookupError):
        await activate_version(424242)


@pytest.mark.asyncio
async def test_activate_does_not_touch_other_sources():
    a1 = await create_version(source_name="src-a", mapping={"a": "title"}, origin="manual")
    b1 = await create_version(source_name="src-b", mapping={"b": "title"}, origin="manual")
    await activate_version(a1)
    await activate_version(b1)
    assert (await get_active_version("src-a"))["id"] == a1
    assert (await get_active_version("src-b"))["id"] == b1


@pytest.mark.asyncio
async def test_partial_unique_index_prevents_two_active():
    """The partial unique index is the safety net under concurrent activate.

    We bypass activate_version and force two active=1 rows via raw SQL to
    prove the index rejects the second.
    """
    v1 = await create_version(source_name="s", mapping={"a": "title"}, origin="manual")
    v2 = await create_version(source_name="s", mapping={"b": "title"}, origin="manual")
    with sqlite3.connect(mappings_mod._MAPPINGS_DB_PATH) as conn:
        conn.execute("UPDATE mapping_versions SET active=1 WHERE id=?", (v1,))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE mapping_versions SET active=1 WHERE id=?", (v2,))
            conn.commit()


@pytest.mark.asyncio
async def test_concurrent_activate_serialises():
    """Two simultaneous activate calls for the same source: both must
    succeed sequentially (one wins last) without leaving a duplicate-active
    state."""
    v1 = await create_version(source_name="s", mapping={"a": "title"}, origin="manual")
    v2 = await create_version(source_name="s", mapping={"b": "title"}, origin="manual")
    await asyncio.gather(activate_version(v1), activate_version(v2))
    rows = await list_versions("s")
    active = [r for r in rows if r["active"]]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_get_all_active_mappings_returns_active_only():
    v1 = await create_version(source_name="s1", mapping={"a": "title"}, origin="manual")
    v2 = await create_version(source_name="s1", mapping={"b": "title"}, origin="manual")
    v3 = await create_version(source_name="s2", mapping={"c": "title"}, origin="manual")
    await activate_version(v2)
    await activate_version(v3)
    all_active = await get_all_active_mappings()
    assert all_active == {"s1": {"b": "title"}, "s2": {"c": "title"}}


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_yaml_seeds_one_active_per_source(_isolate_yaml):
    _isolate_yaml.write_text(
        "manual_mappings:\n"
        "  src-a:\n"
        "    raw_title: title\n"
        "  src-b:\n"
        "    raw_ts: published_at\n",
        encoding="utf-8",
    )
    created = await migrate_yaml_manual_mappings_once()
    assert created == 2
    a = await get_active_version("src-a")
    b = await get_active_version("src-b")
    assert a is not None and a["mapping"] == {"raw_title": "title"}
    assert a["origin"] == "migration"
    assert b is not None and b["mapping"] == {"raw_ts": "published_at"}


@pytest.mark.asyncio
async def test_migrate_yaml_is_idempotent(_isolate_yaml):
    _isolate_yaml.write_text(
        "manual_mappings:\n  src-a:\n    raw_title: title\n",
        encoding="utf-8",
    )
    first = await migrate_yaml_manual_mappings_once()
    second = await migrate_yaml_manual_mappings_once()
    assert first == 1
    assert second == 0
    rows = await list_versions("src-a")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_migrate_yaml_no_op_when_empty(_isolate_yaml):
    _isolate_yaml.write_text("manual_mappings: {}\n", encoding="utf-8")
    created = await migrate_yaml_manual_mappings_once()
    assert created == 0
    assert await list_versions() == []


# ---------------------------------------------------------------------------
# snapshot regeneration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_yaml_snapshot_writes_active_mappings(_isolate_yaml):
    _isolate_yaml.write_text(
        "manual_mappings: {}\nenabled: true\n", encoding="utf-8"
    )
    v1 = await create_version(source_name="src-a", mapping={"a": "title"}, origin="manual")
    await activate_version(v1)
    v2 = await create_version(source_name="src-b", mapping={"b": "title"}, origin="proposal")
    await activate_version(v2)
    await regenerate_yaml_snapshot()
    cfg = config_mod.load_normalizer_config()
    assert cfg["manual_mappings"] == {
        "src-a": {"a": "title"},
        "src-b": {"b": "title"},
    }
    # Pre-existing keys must survive the write.
    assert cfg["enabled"] is True


# ---------------------------------------------------------------------------
# diff helper
# ---------------------------------------------------------------------------

def test_diff_added_removed_changed():
    out = diff_mappings(
        {"keep": "title", "drop": "summary", "mut": "title"},
        {"keep": "title", "mut": "subject", "new": "published_at"},
    )
    assert out == {
        "added":   [{"raw_field": "new",  "canonical": "published_at"}],
        "removed": [{"raw_field": "drop", "canonical": "summary"}],
        "changed": [{"raw_field": "mut",  "from": "title", "to": "subject"}],
    }


def test_diff_empty_inputs():
    assert diff_mappings({}, {}) == {"added": [], "removed": [], "changed": []}


def test_diff_is_deterministic_sorted():
    out = diff_mappings({}, {"z": "title", "a": "summary", "m": "subject"})
    assert [e["raw_field"] for e in out["added"]] == ["a", "m", "z"]
