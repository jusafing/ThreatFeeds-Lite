"""
Normalizer DB — SQLite schema and helpers for the normalized_entries table.
Stored in data/normalized.db (single file, all sources).

The schema is derived from ``config/feed-fields.yaml`` (prompts-021E-pre).
This keeps the engine's canonical namespace, the raw source-DB schema, and
the operator-visible field list in lockstep. ``normalized.db`` is regenerable
data: on any ``_NORM_SCHEMA_VERSION`` bump the file is dropped, recreated,
and the next normalizer run rebuilds its rows. Raw source DBs are untouched.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from backend.config.loader import load_fields

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NORM_DB_PATH = _PROJECT_ROOT / "data" / "normalized.db"

# Bump when the derived schema or housekeeping columns change. On bump,
# ``check_and_handle_schema_bump`` drops + recreates normalized.db and resets
# the ``normalized`` flag across every source DB so the next normalizer run
# rebuilds the table.
# prompts-021F: bumped to 3 to add the mapping_version_id housekeeping
# column. On bump, normalized.db is dropped+recreated and source rows are
# reset to normalized=0 so the next normalizer run rebuilds with the new
# column populated.
_NORM_SCHEMA_VERSION = 3

# Housekeeping columns the engine always writes regardless of yaml schema.
_HOUSEKEEPING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("source_entry_id", "INTEGER NOT NULL"),
    ("source_name", "TEXT NOT NULL"),
    ("extra_norm", "TEXT DEFAULT '{}'"),
    ("normalized_at", "TEXT NOT NULL"),
    # prompts-021F: which mapping_versions.id produced this row. Nullable
    # because rows produced by sources without an active mapping version
    # (e.g. yaml-only configs where migration found nothing) carry NULL.
    ("mapping_version_id", "INTEGER"),
)

# Yaml fields that must always be a numeric SQL type when present.
_REAL_COLUMNS: frozenset[str] = frozenset({
    "cvss_score", "confidence", "geo_lat", "geo_lon",
})
_INTEGER_COLUMNS: frozenset[str] = frozenset({
    "port",
})


def _yaml_field_names() -> list[str]:
    """Return the ordered list of canonical field names from feed-fields.yaml.

    Custom fields are included so the normalized DB can hold operator-defined
    canonicals as well. Names that collide with housekeeping columns are
    skipped (defensive — should never happen in practice).
    """
    data = load_fields() or {}
    names: list[str] = []
    seen: set[str] = set()
    housekeeping = {name for name, _ in _HOUSEKEEPING_COLUMNS}
    for group in ("core_fields", "custom_fields"):
        for field in data.get(group, []) or []:
            name = (field or {}).get("name")
            if not name or name in seen or name in housekeeping:
                continue
            seen.add(name)
            names.append(name)
    return names


def _column_sql_type(name: str) -> str:
    if name in _REAL_COLUMNS:
        return "REAL"
    if name in _INTEGER_COLUMNS:
        return "INTEGER"
    return "TEXT"


def _build_create_sql() -> str:
    """Emit the CREATE TABLE statement for normalized_entries from feed-fields.yaml."""
    cols: list[str] = []
    for name, ddl in _HOUSEKEEPING_COLUMNS:
        cols.append(f"    {name:<16} {ddl}")
    for name in _yaml_field_names():
        cols.append(f"    {name:<16} {_column_sql_type(name)}")
    body = ",\n".join(cols)
    return f"CREATE TABLE IF NOT EXISTS normalized_entries (\n{body}\n);"


CREATE_NORM_DEDUP_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_norm_dedup
ON normalized_entries (source_entry_id, source_name);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def _allowed_columns() -> set[str]:
    """Set of column names valid for INSERT into normalized_entries."""
    cols = {name for name, _ in _HOUSEKEEPING_COLUMNS}
    cols.update(_yaml_field_names())
    return cols


async def _read_schema_version(db: aiosqlite.Connection) -> int:
    """Return the stored schema version, or 0 if absent / table missing."""
    try:
        cursor = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return 0
        return int(row[0])
    except sqlite3.OperationalError:
        return 0


async def _write_schema_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute("DELETE FROM schema_version")
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


async def init_norm_db() -> None:
    _NORM_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_NORM_DB_PATH) as db:
        await db.execute(_build_create_sql())
        await db.execute(CREATE_NORM_DEDUP_INDEX)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        current = await _read_schema_version(db)
        if current == 0:
            # Fresh DB — stamp it.
            await _write_schema_version(db, _NORM_SCHEMA_VERSION)
        await db.commit()


async def check_and_handle_schema_bump() -> bool:
    """If the stored schema version is older than ``_NORM_SCHEMA_VERSION``,
    drop & recreate ``normalized.db`` and reset the ``normalized`` flag across
    every source DB so the next normalizer run rebuilds.

    Returns True when a bump was applied, False otherwise. Idempotent.
    """
    _NORM_DB_PATH.parent.mkdir(exist_ok=True)
    needs_bump = False

    if _NORM_DB_PATH.exists():
        async with aiosqlite.connect(_NORM_DB_PATH) as db:
            current = await _read_schema_version(db)
        if current < _NORM_SCHEMA_VERSION:
            needs_bump = True
            logger.warning(
                "Normalized DB schema version %s < required %s — "
                "dropping data/normalized.db and resetting source normalized flags",
                current, _NORM_SCHEMA_VERSION,
            )
            _NORM_DB_PATH.unlink()

    if needs_bump:
        # Reset normalized=0 on all source DBs so the next run rebuilds.
        # Local import to avoid an import cycle at module load time.
        from backend.db.manager import reset_normalized_flag_for_all_sources
        touched = await reset_normalized_flag_for_all_sources()
        logger.info("Reset normalized=0 on %d source rows after schema bump", touched)

    # Always init (creates fresh DB if it was dropped, or no-op otherwise).
    await init_norm_db()
    return needs_bump


async def insert_normalized(entry: dict[str, Any]) -> bool:
    """
    Insert a normalized entry. Returns True if inserted, False if duplicate.
    """
    await init_norm_db()

    allowed = _allowed_columns()
    row = {k: v for k, v in entry.items() if k in allowed}
    row["normalized_at"] = row.get("normalized_at") or datetime.now(timezone.utc).isoformat()

    extra = {k: v for k, v in entry.items() if k not in allowed and k != "id"}
    if extra:
        row["extra_norm"] = json.dumps(extra)

    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    try:
        async with aiosqlite.connect(_NORM_DB_PATH) as db:
            await db.execute(
                f"INSERT OR IGNORE INTO normalized_entries ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            await db.commit()
            return db.total_changes > 0
    except sqlite3.IntegrityError:
        return False


async def delete_normalized_for_sources(sources: list[str]) -> int:
    """Delete all normalized rows belonging to the given source feeds.

    prompts-038: re-applying a consolidated mapping must replace the prior
    normalized output, but ``normalized_entries`` has a unique dedup index
    (``idx_norm_dedup``) and rows are written with ``INSERT OR IGNORE``, so a
    re-run alone would silently ignore every previously-inserted row. Clearing
    the affected sources first lets the next normalizer run re-insert them under
    the new mapping. Scoped to the named feeds only; no-op on an empty list.

    Returns the number of rows deleted.
    """
    sources = [s for s in (sources or []) if s]
    if not sources:
        return 0
    await init_norm_db()
    placeholders = ", ".join("?" for _ in sources)
    async with aiosqlite.connect(_NORM_DB_PATH) as db:
        cur = await db.execute(
            f"DELETE FROM normalized_entries WHERE source_name IN ({placeholders})",
            sources,
        )
        await db.commit()
        deleted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    logger.info(
        "delete_normalized_for_sources: removed %d rows for sources=%s",
        deleted, sources,
    )
    return deleted


async def query_normalized(
    source_name: str | None = None,
    limit: int = 500,
    offset: int = 0,
    search: str | None = None,
    mapping_version_id: int | None = None,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    await init_norm_db()
    where_clauses: list[str] = []
    params: list[Any] = []

    if source_name:
        where_clauses.append("source_name = ?")
        params.append(source_name)
    # issue_local_02: arbitrary per-column equality filters. The column name is
    # interpolated into SQL, so each is validated against the yaml-derived
    # schema (_allowed_columns); unknown columns — including extra-JSON fields
    # that don't exist as real columns, or an injection attempt — are silently
    # dropped rather than executed.
    if filters:
        allowed = _allowed_columns()
        for col, val in filters.items():
            if col not in allowed:
                continue
            where_clauses.append(f"{col} = ?")
            params.append(val)
    # prompts-021F: viewer dropdown sends mapping_version_id when the
    # operator picks a non-active version; rows produced under that version
    # are returned exclusively. NULL-version rows (pre-021F or sources
    # without a mapping) are never matched by an explicit filter.
    if mapping_version_id is not None:
        where_clauses.append("mapping_version_id = ?")
        params.append(int(mapping_version_id))
    if search:
        # Search across the most distinguishing string columns. All must exist
        # in the yaml-derived schema; if a column was removed from yaml it will
        # be silently absent from the WHERE clause.
        candidate_cols = [
            "indicator", "title", "description", "actor", "cve_id",
            "country", "source_name", "campaign", "malware_family",
        ]
        allowed = _allowed_columns()
        search_cols = [c for c in candidate_cols if c in allowed]
        if search_cols:
            like_clauses = " OR ".join(f"{c} LIKE ?" for c in search_cols)
            where_clauses.append(f"({like_clauses})")
            params.extend([f"%{search}%"] * len(search_cols))

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = (
        f"SELECT * FROM normalized_entries {where_sql} "
        f"ORDER BY normalized_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    async with aiosqlite.connect(_NORM_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = []
        async for row in await db.execute(sql, params):
            d = dict(row)
            extra = json.loads(d.pop("extra_norm", "{}") or "{}")
            d.update(extra)
            rows.append(d)
    return rows


async def get_normalized_summary() -> list[dict[str, Any]]:
    await init_norm_db()
    async with aiosqlite.connect(_NORM_DB_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT source_name, COUNT(*) as count FROM normalized_entries GROUP BY source_name"
        )
    summary = [{"source": r[0], "count": r[1]} for r in rows]
    total = sum(r["count"] for r in summary)
    summary.append({"source": "__total__", "count": total})
    return summary
