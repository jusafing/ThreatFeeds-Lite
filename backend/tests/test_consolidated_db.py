"""Tests for backend.normalizer.consolidated (prompts-032 Phase B).

Covers: init idempotency, CRUD, single-global-active invariant (incl. partial
unique index race protection), activate atomicity, field_scope validation,
and the diff wrapper.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from backend.normalizer import consolidated as consolidated_mod
from backend.normalizer.consolidated import (
    activate_consolidated_version,
    create_consolidated_version,
    diff_consolidated,
    get_active_consolidated,
    get_consolidated_version,
    init_consolidated_db,
    list_consolidated_versions,
)


@pytest.fixture(autouse=True)
def _isolate_consolidated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "mapping_versions.db"
    monkeypatch.setattr(consolidated_mod, "_CONSOLIDATED_DB_PATH", fake_db)
    yield


# ---------------------------------------------------------------------------
# init / schema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_creates_schema_and_is_idempotent():
    await init_consolidated_db()
    await init_consolidated_db()  # second call must not error
    with sqlite3.connect(consolidated_mod._CONSOLIDATED_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_cv_active_global'"
        ).fetchall()
    assert rows, "partial unique index must exist after init"


# ---------------------------------------------------------------------------
# create / list / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_roundtrip():
    vid = await create_consolidated_version(
        mapping={"raw_title": "title", "raw_ts": "published_at"},
        sources=["feed-a", "feed-b"],
        field_scope="configured",
        proposal_id=7,
        note="first consolidated",
    )
    fetched = await get_consolidated_version(vid)
    assert fetched is not None
    assert fetched["mapping"] == {"raw_title": "title", "raw_ts": "published_at"}
    assert fetched["sources"] == ["feed-a", "feed-b"]
    assert fetched["field_scope"] == "configured"
    assert fetched["proposal_id"] == 7
    assert fetched["active"] is False
    assert fetched["note"] == "first consolidated"


@pytest.mark.asyncio
async def test_get_version_missing_returns_none():
    await init_consolidated_db()
    assert await get_consolidated_version(99999) is None


@pytest.mark.asyncio
async def test_list_versions_newest_first():
    v1 = await create_consolidated_version(mapping={"a": "title"}, sources=["s1"])
    v2 = await create_consolidated_version(mapping={"b": "title"}, sources=["s2"])
    v3 = await create_consolidated_version(mapping={"c": "title"}, sources=["s1", "s2"])
    rows = await list_consolidated_versions()
    assert [r["id"] for r in rows] == [v3, v2, v1]


@pytest.mark.asyncio
async def test_create_rejects_non_dict_mapping():
    with pytest.raises(ValueError):
        await create_consolidated_version(mapping=["nope"], sources=[])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_rejects_non_list_sources():
    with pytest.raises(ValueError):
        await create_consolidated_version(mapping={}, sources="feed-a")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_rejects_invalid_field_scope():
    with pytest.raises(ValueError):
        await create_consolidated_version(
            mapping={}, sources=[], field_scope="bogus"
        )


# ---------------------------------------------------------------------------
# activate — single global active invariant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_promotes_and_demotes_globally():
    v1 = await create_consolidated_version(mapping={"a": "title"}, sources=["s1"])
    v2 = await create_consolidated_version(mapping={"b": "title"}, sources=["s2"])
    await activate_consolidated_version(v1)
    assert (await get_active_consolidated())["id"] == v1
    await activate_consolidated_version(v2)
    active = await get_active_consolidated()
    assert active["id"] == v2
    # v1 must now be demoted.
    v1_after = await get_consolidated_version(v1)
    assert v1_after["active"] is False


@pytest.mark.asyncio
async def test_get_active_returns_none_when_no_active():
    await create_consolidated_version(mapping={"a": "title"}, sources=["s1"])
    assert await get_active_consolidated() is None


@pytest.mark.asyncio
async def test_activate_unknown_id_raises_lookup_error():
    await init_consolidated_db()
    with pytest.raises(LookupError):
        await activate_consolidated_version(424242)


@pytest.mark.asyncio
async def test_partial_unique_index_prevents_two_active():
    """The partial unique index is the safety net under concurrent activate."""
    v1 = await create_consolidated_version(mapping={"a": "title"}, sources=["s1"])
    v2 = await create_consolidated_version(mapping={"b": "title"}, sources=["s2"])
    with sqlite3.connect(consolidated_mod._CONSOLIDATED_DB_PATH) as conn:
        conn.execute(
            "UPDATE consolidated_versions SET active=1 WHERE id=?", (v1,)
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE consolidated_versions SET active=1 WHERE id=?", (v2,)
            )
            conn.commit()


@pytest.mark.asyncio
async def test_concurrent_activate_serialises():
    """Two simultaneous activate calls must leave exactly one active row."""
    v1 = await create_consolidated_version(mapping={"a": "title"}, sources=["s1"])
    v2 = await create_consolidated_version(mapping={"b": "title"}, sources=["s2"])
    await asyncio.gather(
        activate_consolidated_version(v1),
        activate_consolidated_version(v2),
    )
    rows = await list_consolidated_versions()
    active = [r for r in rows if r["active"]]
    assert len(active) == 1


# ---------------------------------------------------------------------------
# diff wrapper
# ---------------------------------------------------------------------------

def test_diff_consolidated_matches_mappings_diff():
    out = diff_consolidated(
        {"keep": "title", "drop": "summary", "mut": "title"},
        {"keep": "title", "mut": "subject", "new": "published_at"},
    )
    assert out == {
        "added":   [{"raw_field": "new",  "canonical": "published_at"}],
        "removed": [{"raw_field": "drop", "canonical": "summary"}],
        "changed": [{"raw_field": "mut",  "from": "title", "to": "subject"}],
    }
