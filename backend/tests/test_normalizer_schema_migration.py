"""Tests for prompts-021E-pre schema reconciliation.

Covers:
* normalized.db is dropped & recreated when its stored schema_version is below
  the engine's required version.
* normalizer-config.yaml manual_mappings auto-translate legacy canonical names
  (ip_address/domain/hash/cve/timestamp/source_name_norm) to yaml canonicals
  and the file is rewritten in place.
* reset_normalized_flag_for_all_sources updates rows across multiple source
  DBs (synthetic).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest
import yaml


# ── schema_version drop+recreate ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_schema_bump_drops_and_recreates_normalized_db(tmp_path, monkeypatch):
    """When stored schema_version < _NORM_SCHEMA_VERSION the DB is rebuilt."""
    import backend.normalizer.db as ndb

    fake_path = tmp_path / "normalized.db"
    monkeypatch.setattr(ndb, "_NORM_DB_PATH", fake_path)

    # Seed an "old" normalized.db at version 1 with a sentinel row.
    fake_path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(fake_path) as con:
        con.execute("CREATE TABLE normalized_entries (id INTEGER PRIMARY KEY, junk TEXT)")
        con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        con.execute("INSERT INTO schema_version (version) VALUES (1)")
        con.execute("INSERT INTO normalized_entries (junk) VALUES ('stale')")
        con.commit()
    assert fake_path.exists()

    # Stub the source-flag-reset to avoid touching real source DBs.
    async def _noop_reset() -> int:
        return 0
    monkeypatch.setattr(
        "backend.db.manager.reset_normalized_flag_for_all_sources",
        _noop_reset,
    )

    bumped = await ndb.check_and_handle_schema_bump()
    assert bumped is True

    # New DB exists with version stamp == required and no stale row.
    async with aiosqlite.connect(fake_path) as db:
        cur = await db.execute("SELECT version FROM schema_version")
        row = await cur.fetchone()
        assert row[0] == ndb._NORM_SCHEMA_VERSION

        cur = await db.execute("SELECT COUNT(*) FROM normalized_entries")
        count = (await cur.fetchone())[0]
        assert count == 0

    # Idempotent: a second call does not bump again.
    bumped_again = await ndb.check_and_handle_schema_bump()
    assert bumped_again is False


# ── manual_mappings auto-migration ─────────────────────────────────────────────

def test_manual_mappings_migrates_ip_address_canonical(tmp_path, monkeypatch):
    """Legacy `ip_address` canonical is rewritten to `indicator` and persisted."""
    import backend.normalizer.config as cfg_mod

    cfg_path = tmp_path / "normalizer-config.yaml"
    cfg_path.write_text(
        "mode: manual\n"
        "manual_mappings:\n"
        "  feed_a:\n"
        "    src_ip: ip_address\n"
        "    cve: cve\n"
    )
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", cfg_path)

    result = cfg_mod.load_normalizer_config()

    assert result["manual_mappings"]["feed_a"]["src_ip"] == "indicator"
    assert result["manual_mappings"]["feed_a"]["cve"] == "cve_id"

    # File is rewritten with translated canonicals.
    persisted = yaml.safe_load(cfg_path.read_text())
    assert persisted["manual_mappings"]["feed_a"]["src_ip"] == "indicator"
    assert persisted["manual_mappings"]["feed_a"]["cve"] == "cve_id"


def test_manual_mappings_migrates_timestamp_and_source_name_norm(tmp_path, monkeypatch):
    """Legacy `timestamp` and `source_name_norm` canonicals are rewritten."""
    import backend.normalizer.config as cfg_mod

    cfg_path = tmp_path / "normalizer-config.yaml"
    cfg_path.write_text(
        "mode: manual\n"
        "manual_mappings:\n"
        "  feed_b:\n"
        "    published: timestamp\n"
        "    feed: source_name_norm\n"
    )
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", cfg_path)

    result = cfg_mod.load_normalizer_config()

    assert result["manual_mappings"]["feed_b"]["published"] == "published_at"
    assert result["manual_mappings"]["feed_b"]["feed"] == "source"

    persisted = yaml.safe_load(cfg_path.read_text())
    assert persisted["manual_mappings"]["feed_b"]["published"] == "published_at"
    assert persisted["manual_mappings"]["feed_b"]["feed"] == "source"


def test_manual_mappings_no_change_when_already_current(tmp_path, monkeypatch):
    """When the file already uses yaml canonicals, it is NOT rewritten."""
    import backend.normalizer.config as cfg_mod

    cfg_path = tmp_path / "normalizer-config.yaml"
    original_text = (
        "mode: manual\n"
        "manual_mappings:\n"
        "  feed_c:\n"
        "    src_ip: indicator\n"
        "    cve_id_field: cve_id\n"
    )
    cfg_path.write_text(original_text)
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", cfg_path)
    mtime_before = cfg_path.stat().st_mtime_ns

    cfg_mod.load_normalizer_config()

    # File was not rewritten.
    assert cfg_path.stat().st_mtime_ns == mtime_before


# ── reset_normalized_flag_for_all_sources ──────────────────────────────────────

@pytest.mark.anyio
async def test_reset_normalized_flag_clears_flag_across_sources(tmp_path, monkeypatch):
    """Synthetic source DBs have their normalized flag reset to 0."""
    import backend.db.manager as mgr

    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)

    # Create two synthetic source DBs each with one normalized=1 row.
    for name in ("alpha", "beta"):
        path = tmp_path / f"{name}.db"
        with sqlite3.connect(path) as con:
            con.execute(
                "CREATE TABLE entries (id INTEGER PRIMARY KEY, normalized INTEGER NOT NULL)"
            )
            con.execute("INSERT INTO entries (normalized) VALUES (1)")
            con.execute("INSERT INTO entries (normalized) VALUES (1)")
            con.execute("INSERT INTO entries (normalized) VALUES (0)")
            con.commit()

    # System DBs (normalized/meta) must be skipped if they look like sources.
    (tmp_path / "normalized.db").write_bytes(b"")  # would crash if iterated
    (tmp_path / "meta.db").write_bytes(b"")

    touched = await mgr.reset_normalized_flag_for_all_sources()
    # 2 rows per source, 2 sources = 4 updates
    assert touched == 4

    for name in ("alpha", "beta"):
        with sqlite3.connect(tmp_path / f"{name}.db") as con:
            cur = con.execute("SELECT COUNT(*) FROM entries WHERE normalized=1")
            assert cur.fetchone()[0] == 0


# ── prompts-021F: mapping_version_id housekeeping column ──────────────────────

@pytest.mark.anyio
async def test_normalized_entries_has_mapping_version_id_column(tmp_path, monkeypatch):
    """Schema bump v3 adds the mapping_version_id housekeeping column."""
    import backend.normalizer.db as ndb

    fake_path = tmp_path / "normalized.db"
    monkeypatch.setattr(ndb, "_NORM_DB_PATH", fake_path)
    await ndb.init_norm_db()

    with sqlite3.connect(fake_path) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(normalized_entries)")}
    assert "mapping_version_id" in cols


@pytest.mark.anyio
async def test_query_normalized_filters_by_mapping_version_id(tmp_path, monkeypatch):
    """query_normalized(mapping_version_id=N) returns only rows produced under N."""
    import backend.normalizer.db as ndb

    fake_path = tmp_path / "normalized.db"
    monkeypatch.setattr(ndb, "_NORM_DB_PATH", fake_path)

    await ndb.insert_normalized({
        "source_entry_id": 1, "source_name": "s", "mapping_version_id": 1,
    })
    await ndb.insert_normalized({
        "source_entry_id": 2, "source_name": "s", "mapping_version_id": 2,
    })
    await ndb.insert_normalized({
        "source_entry_id": 3, "source_name": "s", "mapping_version_id": 2,
    })

    v2_rows = await ndb.query_normalized(source_name="s", mapping_version_id=2)
    assert {r["source_entry_id"] for r in v2_rows} == {2, 3}

    v1_rows = await ndb.query_normalized(source_name="s", mapping_version_id=1)
    assert {r["source_entry_id"] for r in v1_rows} == {1}

    all_rows = await ndb.query_normalized(source_name="s")
    assert len(all_rows) == 3
