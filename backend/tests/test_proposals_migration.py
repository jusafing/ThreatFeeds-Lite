"""Tests for proposals.db schema migrations (v1 → v5).

Originally covered the 021E-3 v1→v2 migration. Extended (021G follow-up)
to cover the v3 ``mapping_version_id`` column, (prompts-032) the v4
consolidated-proposal columns, and (prompts-034) the v5 lifecycle columns
``proposal_name`` + ``archived``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from backend.normalizer import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate_proposals_db(tmp_path: Path, monkeypatch):
    fake = tmp_path / "proposals.db"
    monkeypatch.setattr(proposals_mod, "_PROPOSALS_DB_PATH", fake)
    yield fake


def _build_v1_db(path: Path) -> None:
    """Build a v1-shape proposals.db file directly via sqlite3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name       TEXT    NOT NULL,
            provider_name     TEXT,
            model             TEXT,
            sample_size       INTEGER NOT NULL DEFAULT 0,
            raw_fields_json   TEXT    NOT NULL DEFAULT '[]',
            mapping_json      TEXT    NOT NULL DEFAULT '{}',
            prompt_system     TEXT    NOT NULL DEFAULT '',
            prompt_user       TEXT    NOT NULL DEFAULT '',
            llm_response_raw  TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL,
            decided_at        TEXT,
            decided_by_note   TEXT
        )
        """
    )
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute(
        """
        INSERT INTO proposals (source_name, status, created_at)
        VALUES ('legacy-feed', 'pending', '2024-01-01T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_v1_to_v2_migration_is_idempotent_and_additive(_isolate_proposals_db: Path):
    _build_v1_db(_isolate_proposals_db)

    # First init: should add v2 columns and bump schema_version.
    await proposals_mod.init_proposals_db()

    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
        cur = await db.execute("SELECT version FROM schema_version")
        version_row = await cur.fetchone()
        await cur.close()
        cur = await db.execute(
            "SELECT trigger_reason FROM proposals WHERE source_name = 'legacy-feed'"
        )
        legacy = await cur.fetchone()
        await cur.close()

    assert {
        "trigger_reason", "score", "score_breakdown", "outcome",
        "auto_applied", "mapping_version_id",
        "sources_json", "field_scope", "consolidated_version_id",
        "proposal_name", "archived",
    } <= cols
    assert version_row is not None and int(version_row[0]) == 6
    # Pre-existing row defaults to 'manual' (the column default).
    assert legacy is not None and legacy[0] == "manual"

    # Second init must be a no-op (no exceptions, columns unchanged).
    await proposals_mod.init_proposals_db()
    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols2 = {r[1] for r in await cur.fetchall()}
        await cur.close()
    assert cols == cols2


@pytest.mark.asyncio
async def test_insert_proposal_persists_trigger_reason(_isolate_proposals_db: Path):
    pid = await proposals_mod.insert_proposal(
        source_name="src", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="", trigger_reason="on_new_feed",
    )
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched is not None
    assert fetched["trigger_reason"] == "on_new_feed"


@pytest.mark.asyncio
async def test_insert_proposal_rejects_invalid_trigger_reason(_isolate_proposals_db: Path):
    with pytest.raises(ValueError):
        await proposals_mod.insert_proposal(
            source_name="src", provider_name=None, model=None, sample_size=1,
            raw_fields=[], mapping={}, prompt_system="", prompt_user="",
            llm_response_raw="", trigger_reason="bogus",
        )


@pytest.mark.asyncio
async def test_fresh_v3_db_init_is_noop(_isolate_proposals_db: Path):
    """A brand-new init must produce a v5 DB without running migrations twice."""
    await proposals_mod.init_proposals_db()
    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("SELECT version FROM schema_version")
        version_row = await cur.fetchone()
        await cur.close()
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
    assert version_row is not None and int(version_row[0]) == 6
    assert "mapping_version_id" in cols
    assert "consolidated_version_id" in cols
    assert "proposal_name" in cols
    assert "archived" in cols


@pytest.mark.asyncio
async def test_v2_to_v3_migration_adds_mapping_version_id(_isolate_proposals_db: Path):
    """A v2-shape DB must gain the ``mapping_version_id`` column on init."""
    # Build a v2-shape DB: full v2 columns, schema_version = 2.
    conn = sqlite3.connect(_isolate_proposals_db)
    conn.execute(
        """
        CREATE TABLE proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name       TEXT    NOT NULL,
            provider_name     TEXT,
            model             TEXT,
            sample_size       INTEGER NOT NULL DEFAULT 0,
            raw_fields_json   TEXT    NOT NULL DEFAULT '[]',
            mapping_json      TEXT    NOT NULL DEFAULT '{}',
            prompt_system     TEXT    NOT NULL DEFAULT '',
            prompt_user       TEXT    NOT NULL DEFAULT '',
            llm_response_raw  TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL,
            decided_at        TEXT,
            decided_by_note   TEXT,
            trigger_reason    TEXT    NOT NULL DEFAULT 'manual',
            score             REAL,
            score_breakdown   TEXT,
            outcome           TEXT,
            auto_applied      INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.execute(
        """
        INSERT INTO proposals (source_name, status, created_at, outcome)
        VALUES ('v2-feed', 'approved', '2024-01-01T00:00:00Z', 'approved')
        """
    )
    conn.commit()
    conn.close()

    await proposals_mod.init_proposals_db()

    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
        cur = await db.execute(
            "SELECT mapping_version_id FROM proposals WHERE source_name = 'v2-feed'"
        )
        legacy = await cur.fetchone()
        await cur.close()
        cur = await db.execute("SELECT version FROM schema_version")
        v = await cur.fetchone()
        await cur.close()
    assert "mapping_version_id" in cols
    assert v is not None and int(v[0]) == 6
    # Pre-existing v2 rows get NULL (not retroactively populated).
    assert legacy is not None and legacy[0] is None


@pytest.mark.asyncio
async def test_update_proposal_status_persists_mapping_version_id(_isolate_proposals_db: Path):
    """``update_proposal_status`` must persist mapping_version_id when given."""
    pid = await proposals_mod.insert_proposal(
        source_name="src", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    changed = await proposals_mod.update_proposal_status(
        pid, "approved", note="lgtm", outcome="approved",
        mapping_version_id=42,
    )
    assert changed is True
    row = await proposals_mod.get_proposal(pid)
    assert row is not None
    assert row["mapping_version_id"] == 42
    assert row["outcome"] == "approved"
    # Omitting mapping_version_id on a later update must NOT clobber it.
    await proposals_mod.update_proposal_status(pid, "approved", note="touched")
    row2 = await proposals_mod.get_proposal(pid)
    assert row2 is not None
    assert row2["mapping_version_id"] == 42


# ---------------------------------------------------------------------------
# prompts-032: v3 → v4 consolidated columns
# ---------------------------------------------------------------------------

def _build_v3_db(path: Path) -> None:
    """Build a v3-shape proposals.db file directly via sqlite3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name       TEXT    NOT NULL,
            provider_name     TEXT,
            model             TEXT,
            sample_size       INTEGER NOT NULL DEFAULT 0,
            raw_fields_json   TEXT    NOT NULL DEFAULT '[]',
            mapping_json      TEXT    NOT NULL DEFAULT '{}',
            prompt_system     TEXT    NOT NULL DEFAULT '',
            prompt_user       TEXT    NOT NULL DEFAULT '',
            llm_response_raw  TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL,
            decided_at        TEXT,
            decided_by_note   TEXT,
            trigger_reason    TEXT    NOT NULL DEFAULT 'manual',
            score             REAL,
            score_breakdown   TEXT,
            outcome           TEXT,
            auto_applied      INTEGER NOT NULL DEFAULT 0,
            mapping_version_id INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    conn.execute(
        """
        INSERT INTO proposals (source_name, status, created_at, outcome)
        VALUES ('v3-feed', 'approved', '2024-01-01T00:00:00Z', 'approved')
        """
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_v3_to_v4_migration_adds_consolidated_columns(_isolate_proposals_db: Path):
    """A v3-shape DB must gain the v4 consolidated columns on init."""
    _build_v3_db(_isolate_proposals_db)

    await proposals_mod.init_proposals_db()

    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
        cur = await db.execute(
            "SELECT sources_json, field_scope, consolidated_version_id "
            "FROM proposals WHERE source_name = 'v3-feed'"
        )
        legacy = await cur.fetchone()
        await cur.close()
        cur = await db.execute("SELECT version FROM schema_version")
        v = await cur.fetchone()
        await cur.close()
    assert {"sources_json", "field_scope", "consolidated_version_id"} <= cols
    assert v is not None and int(v[0]) == 6
    # Pre-existing v3 rows: sources_json defaults to '[]', the rest NULL.
    assert legacy is not None
    assert legacy[0] == "[]"
    assert legacy[1] is None
    assert legacy[2] is None


@pytest.mark.asyncio
async def test_insert_consolidated_proposal_roundtrips(_isolate_proposals_db: Path):
    """insert_proposal must persist and parse back the consolidated columns."""
    pid = await proposals_mod.insert_proposal(
        source_name=proposals_mod.CONSOLIDATED_SENTINEL,
        provider_name="openai", model="gpt-x", sample_size=20,
        raw_fields=["a", "b", "c"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
        sources=["feed-a", "feed-b"], field_scope="configured",
    )
    row = await proposals_mod.get_proposal(pid)
    assert row is not None
    assert row["source_name"] == proposals_mod.CONSOLIDATED_SENTINEL
    assert row["sources"] == ["feed-a", "feed-b"]
    assert row["field_scope"] == "configured"
    assert row["consolidated_version_id"] is None


@pytest.mark.asyncio
async def test_insert_proposal_rejects_invalid_field_scope(_isolate_proposals_db: Path):
    with pytest.raises(ValueError):
        await proposals_mod.insert_proposal(
            source_name="src", provider_name=None, model=None, sample_size=1,
            raw_fields=[], mapping={}, prompt_system="", prompt_user="",
            llm_response_raw="", field_scope="bogus",
        )


@pytest.mark.asyncio
async def test_update_proposal_status_persists_consolidated_version_id(_isolate_proposals_db: Path):
    pid = await proposals_mod.insert_proposal(
        source_name=proposals_mod.CONSOLIDATED_SENTINEL,
        provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="", sources=["s1"], field_scope="all",
    )
    changed = await proposals_mod.update_proposal_status(
        pid, "approved", note="ok", outcome="approved",
        consolidated_version_id=99,
    )
    assert changed is True
    row = await proposals_mod.get_proposal(pid)
    assert row is not None and row["consolidated_version_id"] == 99
    # Omitting it on a later update must not clobber it.
    await proposals_mod.update_proposal_status(pid, "approved", note="again")
    row2 = await proposals_mod.get_proposal(pid)
    assert row2 is not None and row2["consolidated_version_id"] == 99


# ---------------------------------------------------------------------------
# prompts-034: v4 → v5 lifecycle columns (proposal_name + archived)
# ---------------------------------------------------------------------------

def _build_v4_db(path: Path) -> None:
    """Build a v4-shape proposals.db file directly via sqlite3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name       TEXT    NOT NULL,
            provider_name     TEXT,
            model             TEXT,
            sample_size       INTEGER NOT NULL DEFAULT 0,
            raw_fields_json   TEXT    NOT NULL DEFAULT '[]',
            mapping_json      TEXT    NOT NULL DEFAULT '{}',
            prompt_system     TEXT    NOT NULL DEFAULT '',
            prompt_user       TEXT    NOT NULL DEFAULT '',
            llm_response_raw  TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL,
            decided_at        TEXT,
            decided_by_note   TEXT,
            trigger_reason    TEXT    NOT NULL DEFAULT 'manual',
            score             REAL,
            score_breakdown   TEXT,
            outcome           TEXT,
            auto_applied      INTEGER NOT NULL DEFAULT 0,
            mapping_version_id INTEGER,
            sources_json            TEXT    NOT NULL DEFAULT '[]',
            field_scope             TEXT,
            consolidated_version_id INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")
    conn.execute(
        """
        INSERT INTO proposals (source_name, status, created_at, outcome)
        VALUES ('v4-feed', 'approved', '2024-01-01T00:00:00Z', 'approved')
        """
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_v4_to_v5_migration_adds_lifecycle_columns(_isolate_proposals_db: Path):
    """A v4-shape DB must gain proposal_name + archived and backfill names."""
    _build_v4_db(_isolate_proposals_db)

    await proposals_mod.init_proposals_db()

    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
        cur = await db.execute(
            "SELECT proposal_name, archived FROM proposals "
            "WHERE source_name = 'v4-feed'"
        )
        legacy = await cur.fetchone()
        await cur.close()
        cur = await db.execute("SELECT version FROM schema_version")
        v = await cur.fetchone()
        await cur.close()
    assert {"proposal_name", "archived"} <= cols
    assert v is not None and int(v[0]) == 6
    # Legacy row: name backfilled from created_at, archived defaults to 0.
    assert legacy is not None
    assert legacy[0] == "Proposal-2024-01-01T00:00:00Z"
    assert legacy[1] == 0

    # Second init is a no-op (idempotent backfill).
    await proposals_mod.init_proposals_db()
    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute(
            "SELECT proposal_name FROM proposals WHERE source_name = 'v4-feed'"
        )
        again = await cur.fetchone()
        await cur.close()
    assert again is not None and again[0] == "Proposal-2024-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_insert_proposal_populates_name_and_defaults_unarchived(_isolate_proposals_db: Path):
    pid = await proposals_mod.insert_proposal(
        source_name="src", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    row = await proposals_mod.get_proposal(pid)
    assert row is not None
    assert row["proposal_name"].startswith("Proposal-")
    assert row["archived"] is False


@pytest.mark.asyncio
async def test_archive_proposal_flips_flag_and_hides_by_default(_isolate_proposals_db: Path):
    pid = await proposals_mod.insert_proposal(
        source_name="src", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    # Visible by default (archived defaults False).
    active = await proposals_mod.list_proposals(outcome="all", archived=False)
    assert any(r["id"] == pid for r in active)

    changed = await proposals_mod.archive_proposal(pid, note="dup")
    assert changed is True
    row = await proposals_mod.get_proposal(pid)
    assert row is not None and row["archived"] is True

    # Excluded from the active view, present in the archived-only view.
    active2 = await proposals_mod.list_proposals(outcome="all", archived=False)
    assert all(r["id"] != pid for r in active2)
    archived_only = await proposals_mod.list_proposals(outcome="all", archived=True)
    assert any(r["id"] == pid for r in archived_only)
    # archived=None returns both.
    both = await proposals_mod.list_proposals(outcome="all", archived=None)
    assert any(r["id"] == pid for r in both)


# ---------------------------------------------------------------------------
# prompts-037: v5 → v6 raw-exchange columns
#   (llm_request_raw + llm_response_json)
# ---------------------------------------------------------------------------

def _build_v5_db(path: Path) -> None:
    """Build a v5-shape proposals.db file directly via sqlite3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name       TEXT    NOT NULL,
            provider_name     TEXT,
            model             TEXT,
            sample_size       INTEGER NOT NULL DEFAULT 0,
            raw_fields_json   TEXT    NOT NULL DEFAULT '[]',
            mapping_json      TEXT    NOT NULL DEFAULT '{}',
            prompt_system     TEXT    NOT NULL DEFAULT '',
            prompt_user       TEXT    NOT NULL DEFAULT '',
            llm_response_raw  TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL,
            decided_at        TEXT,
            decided_by_note   TEXT,
            trigger_reason    TEXT    NOT NULL DEFAULT 'manual',
            score             REAL,
            score_breakdown   TEXT,
            outcome           TEXT,
            auto_applied      INTEGER NOT NULL DEFAULT 0,
            mapping_version_id INTEGER,
            sources_json            TEXT    NOT NULL DEFAULT '[]',
            field_scope             TEXT,
            consolidated_version_id INTEGER,
            proposal_name     TEXT    NOT NULL DEFAULT '',
            archived          INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.execute(
        """
        INSERT INTO proposals (source_name, status, created_at, outcome,
                               proposal_name)
        VALUES ('v5-feed', 'error', '2024-01-01T00:00:00Z', 'error', 'P-1')
        """
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_v5_to_v6_migration_adds_raw_exchange_columns(_isolate_proposals_db: Path):
    """A v5-shape DB must gain llm_request_raw + llm_response_json."""
    _build_v5_db(_isolate_proposals_db)

    await proposals_mod.init_proposals_db()

    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols = {r[1] for r in await cur.fetchall()}
        await cur.close()
        cur = await db.execute(
            "SELECT llm_request_raw, llm_response_json FROM proposals "
            "WHERE source_name = 'v5-feed'"
        )
        legacy = await cur.fetchone()
        await cur.close()
        cur = await db.execute("SELECT version FROM schema_version")
        v = await cur.fetchone()
        await cur.close()
    assert {"llm_request_raw", "llm_response_json"} <= cols
    assert v is not None and int(v[0]) == 6
    # Legacy row backfilled to empty strings (NOT NULL DEFAULT '').
    assert legacy is not None
    assert legacy[0] == ""
    assert legacy[1] == ""

    # Second init is a no-op.
    await proposals_mod.init_proposals_db()
    async with aiosqlite.connect(_isolate_proposals_db) as db:
        cur = await db.execute("PRAGMA table_info(proposals)")
        cols2 = {r[1] for r in await cur.fetchall()}
        await cur.close()
    assert {"llm_request_raw", "llm_response_json"} <= cols2
