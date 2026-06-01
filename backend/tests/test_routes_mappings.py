"""Tests for backend.api.routes_mappings (021F step 5).

Covers list/get/activate/diff routes + isolation via tmp_path-pointed
mapping_versions.db and DATA_DIR.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.normalizer import config as norm_cfg_mod
from backend.normalizer import mappings as mappings_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings_mod, "_MAPPINGS_DB_PATH", tmp_path / "mapping_versions.db")
    monkeypatch.setattr(norm_cfg_mod, "_NORMALIZER_CONFIG_PATH", tmp_path / "normalizer-config.yaml")
    import backend.db.manager as mgr
    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)
    yield


# ── list ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_versions_empty():
    client = TestClient(app)
    r = client.get("/api/normalizer/mappings/versions")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_versions_filters_by_source():
    v1 = await mappings_mod.create_version(
        source_name="src-a", mapping={"a": "title"}, origin="manual",
    )
    v2 = await mappings_mod.create_version(
        source_name="src-b", mapping={"b": "title"}, origin="manual",
    )
    client = TestClient(app)
    r = client.get("/api/normalizer/mappings/versions?source=src-a")
    assert r.status_code == 200
    rows = r.json()
    assert {row["id"] for row in rows} == {v1}
    assert rows[0]["mapping"] == {"a": "title"}


# ── get ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_version_404():
    client = TestClient(app)
    r = client.get("/api/normalizer/mappings/versions/424242")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_version_returns_diff_vs_active():
    v1 = await mappings_mod.create_version(
        source_name="src-a", mapping={"a": "title"}, origin="manual",
    )
    await mappings_mod.activate_version(v1)
    v2 = await mappings_mod.create_version(
        source_name="src-a",
        mapping={"a": "title", "b": "indicator"},
        origin="proposal",
    )
    client = TestClient(app)
    r = client.get(f"/api/normalizer/mappings/versions/{v2}")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]["id"] == v2
    assert body["active"]["id"] == v1
    # Diff from v1 -> v2 adds 'b'.
    assert body["diff"]["added"] == [{"raw_field": "b", "canonical": "indicator"}]
    assert body["diff"]["removed"] == []
    assert body["diff"]["changed"] == []


# ── activate ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_promotes_and_resets_source(tmp_path):
    v1 = await mappings_mod.create_version(
        source_name="src-a", mapping={"a": "title"}, origin="manual",
    )
    v2 = await mappings_mod.create_version(
        source_name="src-a", mapping={"b": "title"}, origin="manual",
    )
    await mappings_mod.activate_version(v1)

    # Seed a fake source DB with two normalized=1 rows so reset_rows > 0.
    src_db = tmp_path / "src-a.db"
    with sqlite3.connect(src_db) as con:
        con.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, normalized INTEGER NOT NULL)"
        )
        con.execute("INSERT INTO entries (normalized) VALUES (1)")
        con.execute("INSERT INTO entries (normalized) VALUES (1)")
        con.commit()

    client = TestClient(app)
    r = client.post(f"/api/normalizer/mappings/versions/{v2}/activate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_id"] == v2
    assert body["source"] == "src-a"
    assert body["reset_rows"] == 2

    # v2 is now active and yaml snapshot reflects it.
    active = await mappings_mod.get_active_version("src-a")
    assert active["id"] == v2
    on_disk = norm_cfg_mod.load_normalizer_config()
    assert on_disk["manual_mappings"]["src-a"] == {"b": "title"}

    # Source DB normalized flags reset.
    with sqlite3.connect(src_db) as con:
        cur = con.execute("SELECT COUNT(*) FROM entries WHERE normalized=1")
        assert cur.fetchone()[0] == 0


@pytest.mark.asyncio
async def test_activate_404():
    client = TestClient(app)
    r = client.post("/api/normalizer/mappings/versions/424242/activate")
    assert r.status_code == 404


# ── diff ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_three_bucket():
    v1 = await mappings_mod.create_version(
        source_name="s",
        mapping={"keep": "title", "drop": "summary", "mut": "title"},
        origin="manual",
    )
    v2 = await mappings_mod.create_version(
        source_name="s",
        mapping={"keep": "title", "mut": "subject", "new": "published_at"},
        origin="manual",
    )
    client = TestClient(app)
    r = client.get(f"/api/normalizer/mappings/diff?from={v1}&to={v2}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from"]["id"] == v1
    assert body["to"]["id"] == v2
    assert body["diff"]["added"] == [{"raw_field": "new", "canonical": "published_at"}]
    assert body["diff"]["removed"] == [{"raw_field": "drop", "canonical": "summary"}]
    assert body["diff"]["changed"] == [
        {"raw_field": "mut", "from": "title", "to": "subject"}
    ]


@pytest.mark.asyncio
async def test_diff_404_when_either_missing():
    v1 = await mappings_mod.create_version(
        source_name="s", mapping={"a": "title"}, origin="manual",
    )
    client = TestClient(app)
    r = client.get(f"/api/normalizer/mappings/diff?from={v1}&to=99999")
    assert r.status_code == 404
    r = client.get(f"/api/normalizer/mappings/diff?from=99999&to={v1}")
    assert r.status_code == 404


# ── viewer entries mapping_version_id filter ─────────────────────────────────

@pytest.mark.asyncio
async def test_normalizer_entries_accepts_mapping_version_id(tmp_path, monkeypatch):
    """The /api/normalizer/entries endpoint accepts and applies the new
    mapping_version_id query param."""
    import backend.normalizer.db as ndb

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    await ndb.insert_normalized({
        "source_entry_id": 1, "source_name": "s", "mapping_version_id": 7,
    })
    await ndb.insert_normalized({
        "source_entry_id": 2, "source_name": "s", "mapping_version_id": 8,
    })

    client = TestClient(app)
    r = client.get("/api/normalizer/entries?source=s&mapping_version_id=7")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["source_entry_id"] == 1
