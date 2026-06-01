"""
Consolidated (global) mapping versioning (prompts-032 Phase B).

Companion to ``mappings.py``'s *per-source* versioning. Where the per-source
store keeps one active ``{raw_field: canonical}`` mapping **per feed**, this
store keeps a single **global** consolidated mapping that spans a set of
selected feeds. Because normalization maps by raw-field name, one global dict
can be applied to every entry regardless of its source.

Stored in the same SQLite file as the per-source registry
(``data/mapping_versions.db``) but in a separate table so the two concerns
never collide. The per-source ``mapping_versions`` table, the
``manual_mappings`` yaml snapshot, and manual mode are all left untouched.

Each row carries:
  * mapping_json   — validated {raw_field: canonical} dict (the consolidated map)
  * sources_json   — list of feed names the mapping was consolidated from
  * field_scope    — 'all' | 'configured' (provenance of the raw fields used)
  * proposal_id    — link to proposals.id when produced by an approved proposal
  * active         — 0/1; at most ONE active row globally
                     (partial unique index ``WHERE active = 1``)
  * note           — free-text audit annotation
  * created_at     — ISO-8601 UTC

Concurrency: ``BEGIN IMMEDIATE`` + the partial unique index guarantee at most
one active row even under concurrent activation (the loser raises
IntegrityError).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from backend.normalizer.mappings import diff_mappings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Same physical DB file as the per-source registry, distinct table.
_CONSOLIDATED_DB_PATH = _PROJECT_ROOT / "data" / "mapping_versions.db"

_VALID_FIELD_SCOPES = frozenset({"all", "configured"})


CREATE_CONSOLIDATED_TABLE = """
CREATE TABLE IF NOT EXISTS consolidated_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_json  TEXT    NOT NULL DEFAULT '{}',
    sources_json  TEXT    NOT NULL DEFAULT '[]',
    field_scope   TEXT,
    proposal_id   INTEGER,
    active        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    note          TEXT
);
"""

# Partial unique index over the constant active=1: because every matching row
# has active=1, uniqueness forces at most one active row globally.
CREATE_CONSOLIDATED_IDX_ACTIVE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_cv_active_global
    ON consolidated_versions (active) WHERE active = 1;
"""


async def init_consolidated_db() -> None:
    """Create the consolidated_versions table if missing. Idempotent."""
    _CONSOLIDATED_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        await db.execute(CREATE_CONSOLIDATED_TABLE)
        await db.execute(CREATE_CONSOLIDATED_IDX_ACTIVE)
        await db.commit()


def _row_to_dict(row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    d = dict(row)
    try:
        d["mapping"] = json.loads(d.pop("mapping_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["mapping"] = {}
    try:
        d["sources"] = json.loads(d.pop("sources_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["sources"] = []
    if "active" in d:
        d["active"] = bool(d["active"])
    return d


async def create_consolidated_version(
    *,
    mapping: dict[str, str],
    sources: list[str],
    field_scope: str | None = None,
    proposal_id: int | None = None,
    note: str | None = None,
) -> int:
    """Insert a new consolidated version (inactive by default).

    Use ``activate_consolidated_version`` to atomically promote it.
    """
    if not isinstance(mapping, dict):
        raise ValueError("mapping must be a dict")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    if field_scope is not None and field_scope not in _VALID_FIELD_SCOPES:
        raise ValueError(f"invalid field_scope: {field_scope!r}")
    await init_consolidated_db()
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO consolidated_versions (
                mapping_json, sources_json, field_scope,
                proposal_id, active, created_at, note
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                json.dumps(mapping, ensure_ascii=False, sort_keys=True),
                json.dumps(sources, ensure_ascii=False),
                field_scope,
                proposal_id,
                created_at,
                note,
            ),
        )
        await db.commit()
        new_id = cur.lastrowid
        await cur.close()
        return int(new_id)


async def list_consolidated_versions() -> list[dict[str, Any]]:
    """List all consolidated versions, newest first."""
    await init_consolidated_db()
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(
            "SELECT * FROM consolidated_versions ORDER BY id DESC"
        ):
            rows.append(_row_to_dict(row))
    return rows


async def get_consolidated_version(version_id: int) -> dict[str, Any] | None:
    await init_consolidated_db()
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM consolidated_versions WHERE id = ?", (version_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return None
    return _row_to_dict(row)


async def get_active_consolidated() -> dict[str, Any] | None:
    """Return the single active consolidated version, or None."""
    await init_consolidated_db()
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM consolidated_versions WHERE active = 1 LIMIT 1"
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return None
    return _row_to_dict(row)


async def activate_consolidated_version(version_id: int) -> None:
    """Atomically promote ``version_id`` to the single global active row.

    Within a single ``BEGIN IMMEDIATE`` transaction:
      1. Verify the target row exists.
      2. Demote any currently-active row.
      3. Promote the target.

    Combined with the partial unique index this guarantees at most one active
    row globally even under concurrent activate calls.

    Raises:
        LookupError: when the target version_id does not exist.
    """
    await init_consolidated_db()
    async with aiosqlite.connect(_CONSOLIDATED_DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT id FROM consolidated_versions WHERE id = ?",
                (version_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                await db.rollback()
                raise LookupError(
                    f"consolidated version {version_id} not found"
                )
            await db.execute(
                "UPDATE consolidated_versions SET active = 0 "
                "WHERE active = 1 AND id != ?",
                (version_id,),
            )
            await db.execute(
                "UPDATE consolidated_versions SET active = 1 WHERE id = ?",
                (version_id,),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def diff_consolidated(
    from_mapping: dict[str, str],
    to_mapping: dict[str, str],
) -> dict[str, list[dict[str, str]]]:
    """Three-bucket diff between two consolidated mappings.

    Thin wrapper over ``mappings.diff_mappings`` so callers don't need to
    reach across modules; semantics and ordering are identical.
    """
    return diff_mappings(from_mapping, to_mapping)
