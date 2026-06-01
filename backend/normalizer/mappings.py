"""
Per-source mapping versioning (prompts-021F).

SQLite-backed registry at ``data/mapping_versions.db`` tracking the history of
``{raw_field: canonical}`` mappings for each feed source. Each row carries:
  * source_name          — feed source the mapping applies to
  * mapping_json         — validated {raw_field: canonical} dict
  * origin               — 'manual' | 'proposal' | 'migration'
  * source_proposal_id   — link to proposals.id when origin='proposal'
  * active               — 0/1; at most one active row per source
                           (enforced by partial unique index)
  * note                 — free-text audit annotation
  * created_at           — ISO-8601 UTC

Design decisions (locked 021F kickoff):
  * Storage: separate file (matches one-DB-per-concern pattern with
    proposals.db / normalized.db).
  * Granularity: per-source.
  * Initial migration: idempotent walk over yaml ``manual_mappings`` block
    on first startup, creating one ``origin='migration'`` active row per
    source. yaml is left untouched at this step.
  * yaml snapshot: regenerated on every activation as a write-through
    backwards-compat snapshot of the active versions (see
    ``regenerate_yaml_snapshot``).
  * Concurrency: ``BEGIN IMMEDIATE`` + partial unique index
    ``WHERE active=1`` so only one writer can mutate the active state.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAPPINGS_DB_PATH = _PROJECT_ROOT / "data" / "mapping_versions.db"

_MAPPINGS_SCHEMA_VERSION = 1

_VALID_ORIGINS = frozenset({"manual", "proposal", "migration"})


CREATE_MAPPINGS_TABLE = """
CREATE TABLE IF NOT EXISTS mapping_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name         TEXT    NOT NULL,
    mapping_json        TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    NOT NULL,
    source_proposal_id  INTEGER,
    origin              TEXT    NOT NULL,
    active              INTEGER NOT NULL DEFAULT 0,
    note                TEXT
);
"""

CREATE_MAPPINGS_IDX_SOURCE = """
CREATE INDEX IF NOT EXISTS idx_mv_source ON mapping_versions (source_name);
"""

# Partial unique index: at most one active row per source. SQLite supports
# partial indices since 3.8; combined with BEGIN IMMEDIATE this is the race
# safety net against double-active state.
CREATE_MAPPINGS_IDX_ACTIVE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_active_per_source
    ON mapping_versions (source_name) WHERE active = 1;
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


async def init_mappings_db() -> None:
    """Create the mapping_versions DB if missing. Idempotent."""
    _MAPPINGS_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        await db.execute(CREATE_MAPPINGS_TABLE)
        await db.execute(CREATE_MAPPINGS_IDX_SOURCE)
        await db.execute(CREATE_MAPPINGS_IDX_ACTIVE)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_MAPPINGS_SCHEMA_VERSION,),
            )
        await db.commit()


def _row_to_dict(row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    d = dict(row)
    try:
        d["mapping"] = json.loads(d.pop("mapping_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["mapping"] = {}
    if "active" in d:
        d["active"] = bool(d["active"])
    return d


async def create_version(
    *,
    source_name: str,
    mapping: dict[str, str],
    origin: str,
    source_proposal_id: int | None = None,
    note: str | None = None,
) -> int:
    """Insert a new mapping version (inactive by default).

    Use ``activate_version`` to atomically promote it.
    """
    if origin not in _VALID_ORIGINS:
        raise ValueError(f"invalid origin: {origin!r}")
    if not isinstance(mapping, dict):
        raise ValueError("mapping must be a dict")
    await init_mappings_db()
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO mapping_versions (
                source_name, mapping_json, created_at,
                source_proposal_id, origin, active, note
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (
                source_name,
                json.dumps(mapping, ensure_ascii=False, sort_keys=True),
                created_at,
                source_proposal_id,
                origin,
                note,
            ),
        )
        await db.commit()
        new_id = cur.lastrowid
        await cur.close()
        return int(new_id)


async def list_versions(source_name: str | None = None) -> list[dict[str, Any]]:
    """List all mapping versions, newest first. Optionally filter by source."""
    await init_mappings_db()
    where_sql = ""
    params: list[Any] = []
    if source_name:
        where_sql = "WHERE source_name = ?"
        params.append(source_name)
    sql = (
        f"SELECT * FROM mapping_versions {where_sql} "
        f"ORDER BY id DESC"
    )
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(sql, params):
            rows.append(_row_to_dict(row))
    return rows


async def get_version(version_id: int) -> dict[str, Any] | None:
    await init_mappings_db()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM mapping_versions WHERE id = ?", (version_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return None
    return _row_to_dict(row)


async def get_active_version(source_name: str) -> dict[str, Any] | None:
    await init_mappings_db()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM mapping_versions "
            "WHERE source_name = ? AND active = 1 LIMIT 1",
            (source_name,),
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return None
    return _row_to_dict(row)


async def activate_version(version_id: int) -> None:
    """Atomically promote ``version_id`` to active for its source.

    Within a single ``BEGIN IMMEDIATE`` transaction:
      1. Look up the target row's source_name.
      2. Demote any currently-active row for that source.
      3. Promote the target.

    Combined with the partial unique index on (source_name) WHERE active=1
    this guarantees at most one active row per source even under concurrent
    activate calls (the loser raises IntegrityError).

    Raises:
        LookupError: when the target version_id does not exist.
    """
    await init_mappings_db()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        # Acquire an immediate write lock so the read-then-write below is
        # serialised with other activate calls.
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT source_name FROM mapping_versions WHERE id = ?",
                (version_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                await db.rollback()
                raise LookupError(f"mapping version {version_id} not found")
            source_name = row[0]
            await db.execute(
                "UPDATE mapping_versions SET active = 0 "
                "WHERE source_name = ? AND active = 1 AND id != ?",
                (source_name, version_id),
            )
            await db.execute(
                "UPDATE mapping_versions SET active = 1 WHERE id = ?",
                (version_id,),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def get_all_active_mappings() -> dict[str, dict[str, str]]:
    """Return ``{source_name: mapping}`` for every active version.

    Used by ``regenerate_yaml_snapshot`` and any code path that needs the
    "current state of the world" without going through the per-source helper
    in a loop.
    """
    await init_mappings_db()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT source_name, mapping_json FROM mapping_versions "
            "WHERE active = 1"
        )
        out: dict[str, dict[str, str]] = {}
        async for row in cur:
            try:
                out[row["source_name"]] = json.loads(row["mapping_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                out[row["source_name"]] = {}
        await cur.close()
    return out


async def migrate_yaml_manual_mappings_once() -> int:
    """Idempotent one-shot: seed v1 rows from yaml ``manual_mappings``.

    Behaviour:
      * If ``mapping_versions`` already contains any rows, do nothing and
        return 0 (idempotency guard — re-running startup is safe).
      * Otherwise, walk ``normalizer-config.yaml::manual_mappings`` and
        create one ``origin='migration', active=1`` row per source.
      * The yaml file is left untouched. The active rows ARE the source of
        truth from this point on; the yaml snapshot is regenerated by
        ``regenerate_yaml_snapshot`` after the next activation.

    Returns:
        Number of rows created (0 if migration was skipped).
    """
    await init_mappings_db()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM mapping_versions")
        row = await cur.fetchone()
        await cur.close()
        if row is not None and int(row[0]) > 0:
            return 0

    # Local import to avoid a circular dependency at module load
    # (config.py is leaf-y but doesn't import mappings; keep it that way).
    from backend.normalizer.config import load_normalizer_config

    cfg = load_normalizer_config()
    manual_mappings = cfg.get("manual_mappings") or {}
    if not isinstance(manual_mappings, dict) or not manual_mappings:
        logger.info(
            "mapping_versions migration: no manual_mappings in yaml; nothing to seed"
        )
        return 0

    created = 0
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_MAPPINGS_DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            for source_name, mapping in manual_mappings.items():
                if not isinstance(mapping, dict):
                    continue
                await db.execute(
                    """
                    INSERT INTO mapping_versions (
                        source_name, mapping_json, created_at,
                        source_proposal_id, origin, active, note
                    ) VALUES (?, ?, ?, NULL, 'migration', 1, ?)
                    """,
                    (
                        source_name,
                        json.dumps(mapping, ensure_ascii=False, sort_keys=True),
                        created_at,
                        "seeded from normalizer-config.yaml manual_mappings (021F)",
                    ),
                )
                created += 1
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    logger.info(
        "mapping_versions migration: seeded %d v1 row(s) from yaml", created
    )
    return created


async def regenerate_yaml_snapshot() -> None:
    """Write all active mappings back to ``normalizer-config.yaml``.

    Backwards-compat snapshot: legacy readers of ``manual_mappings`` keep
    seeing the current state without needing to read mapping_versions.db.

    Atomicity: ``save_normalizer_config`` is not currently atomic; we rely
    on writing the full dict at once and the small payload size. A future
    enhancement could swap in a tmp-file + rename helper.
    """
    from backend.normalizer.config import (
        load_normalizer_config,
        save_normalizer_config,
    )

    active = await get_all_active_mappings()
    cfg = load_normalizer_config()
    cfg = dict(cfg)
    cfg["manual_mappings"] = active
    save_normalizer_config(cfg)
    logger.info(
        "normalizer-config.yaml manual_mappings snapshot regenerated "
        "(%d source(s))", len(active),
    )


def diff_mappings(
    from_mapping: dict[str, str],
    to_mapping: dict[str, str],
) -> dict[str, list[dict[str, str]]]:
    """Compute the three-bucket diff between two mappings.

    Returns:
        {
          "added":   [{"raw_field": ..., "canonical": ...}, ...],
          "removed": [{"raw_field": ..., "canonical": ...}, ...],
          "changed": [{"raw_field": ..., "from": ..., "to": ...}, ...],
        }

    Deterministic ordering: raw_field keys are sorted alphabetically so
    snapshot tests and the diff UI render stably.
    """
    from_keys = set(from_mapping or {})
    to_keys = set(to_mapping or {})
    added_keys = sorted(to_keys - from_keys)
    removed_keys = sorted(from_keys - to_keys)
    common_keys = sorted(from_keys & to_keys)
    added = [
        {"raw_field": k, "canonical": to_mapping[k]} for k in added_keys
    ]
    removed = [
        {"raw_field": k, "canonical": from_mapping[k]} for k in removed_keys
    ]
    changed = [
        {
            "raw_field": k,
            "from": from_mapping[k],
            "to": to_mapping[k],
        }
        for k in common_keys
        if from_mapping[k] != to_mapping[k]
    ]
    return {"added": added, "removed": removed, "changed": changed}
