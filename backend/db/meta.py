"""
Per-source ingestion metadata — `data/meta.db`.

Tracks the most recent ingest result for each source so the Summary
table can render \"Last Ingest\" and per-source delta columns even
across server restarts.

Schema:
    source              TEXT PRIMARY KEY
    last_ingested_at    TEXT (ISO8601, UTC)
    last_total_read     INTEGER
    last_inserted       INTEGER
    last_duplicates     INTEGER
    last_discarded      INTEGER
    last_job_state      TEXT  -- 'done' | 'error'
    last_job_kind       TEXT
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.db.manager import DATA_DIR

logger = logging.getLogger(__name__)

META_DB = DATA_DIR / "meta.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS source_meta (
    source           TEXT PRIMARY KEY,
    last_ingested_at TEXT,
    last_total_read  INTEGER DEFAULT 0,
    last_inserted    INTEGER DEFAULT 0,
    last_duplicates  INTEGER DEFAULT 0,
    last_discarded   INTEGER DEFAULT 0,
    last_job_state   TEXT,
    last_job_kind    TEXT
)
"""

# issue_local_009: persistent registry of which entry fields have been seen
# populated by ingestion. The Raw Feeds table uses this to pick its default
# visible columns (fields that actually have content) without scanning entries
# on every refresh. ``populated_count`` accumulates the number of inserted rows
# that carried a non-empty value for the field; ``last_populated_at`` records
# the most recent time it was seen, so defaults favour recently-active fields.
_CREATE_FIELD_PRESENCE = """
CREATE TABLE IF NOT EXISTS field_presence (
    field             TEXT PRIMARY KEY,
    populated_count   INTEGER NOT NULL DEFAULT 0,
    last_populated_at TEXT
)
"""

_UPSERT_FIELD_PRESENCE = """
INSERT INTO field_presence (field, populated_count, last_populated_at)
VALUES (?, ?, ?)
ON CONFLICT(field) DO UPDATE SET
    populated_count   = field_presence.populated_count + excluded.populated_count,
    last_populated_at = excluded.last_populated_at
"""

_UPSERT = """
INSERT INTO source_meta (
    source, last_ingested_at, last_total_read, last_inserted,
    last_duplicates, last_discarded, last_job_state, last_job_kind
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source) DO UPDATE SET
    last_ingested_at = excluded.last_ingested_at,
    last_total_read  = excluded.last_total_read,
    last_inserted    = excluded.last_inserted,
    last_duplicates  = excluded.last_duplicates,
    last_discarded   = excluded.last_discarded,
    last_job_state   = excluded.last_job_state,
    last_job_kind    = excluded.last_job_kind
"""


async def _ensure_schema() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    async with aiosqlite.connect(META_DB) as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_FIELD_PRESENCE)
        await db.commit()


async def record_ingest(
    source: str,
    counters: dict[str, int],
    state: str = "done",
    kind: str | None = None,
    when: datetime | None = None,
) -> None:
    """Upsert the last-ingest summary for a single source."""
    await _ensure_schema()
    ts = (when or datetime.now(timezone.utc)).isoformat()
    try:
        async with aiosqlite.connect(META_DB) as db:
            await db.execute(_UPSERT, (
                source,
                ts,
                int(counters.get("total_read", 0)),
                int(counters.get("inserted", 0)),
                int(counters.get("duplicates", 0)),
                int(counters.get("discarded", 0)),
                state,
                kind,
            ))
            await db.commit()
    except sqlite3.OperationalError as exc:
        logger.warning("meta.record_ingest failed for source=%s: %s", source, exc)


async def get_meta(sources: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return per-source meta as a dict keyed by source name.

    If sources is given, only those rows are returned. Missing entries
    map to nothing (caller decides default values).
    """
    if not META_DB.exists():
        return {}
    try:
        async with aiosqlite.connect(META_DB) as db:
            db.row_factory = aiosqlite.Row
            if sources is not None:
                if not sources:
                    return {}
                placeholders = ",".join("?" for _ in sources)
                cur = await db.execute(
                    f"SELECT * FROM source_meta WHERE source IN ({placeholders})",
                    list(sources),
                )
            else:
                cur = await db.execute("SELECT * FROM source_meta")
            rows = await cur.fetchall()
            return {r["source"]: dict(r) for r in rows}
    except sqlite3.OperationalError as exc:
        logger.warning("meta.get_meta failed: %s", exc)
        return {}


async def reset_meta() -> None:
    """Test helper — wipe the meta DB."""
    if META_DB.exists():
        META_DB.unlink()


async def record_field_presence(
    deltas: dict[str, int],
    when: datetime | None = None,
) -> None:
    """Accumulate field-population counts into ``field_presence``.

    ``deltas`` maps a field name to the number of newly-inserted rows (since the
    last flush) that carried a non-empty value for it. Called once per ingest
    job from the job-completion hook — never per row — so the write cost is
    bounded by the number of distinct fields, not the number of entries.
    """
    if not deltas:
        return
    await _ensure_schema()
    ts = (when or datetime.now(timezone.utc)).isoformat()
    rows = [(field, int(count), ts) for field, count in deltas.items() if count]
    if not rows:
        return
    try:
        async with aiosqlite.connect(META_DB) as db:
            await db.executemany(_UPSERT_FIELD_PRESENCE, rows)
            await db.commit()
    except sqlite3.OperationalError as exc:
        logger.warning("meta.record_field_presence failed: %s", exc)


async def get_field_presence() -> list[str]:
    """Return field names that have been seen populated, most-relevant first.

    Ordered by recency (``last_populated_at`` DESC) then frequency
    (``populated_count`` DESC) so the Raw Feeds table can pick its default
    visible columns from fields that actually carry content.
    """
    if not META_DB.exists():
        return []
    try:
        async with aiosqlite.connect(META_DB) as db:
            cur = await db.execute(
                "SELECT field FROM field_presence WHERE populated_count > 0 "
                "ORDER BY last_populated_at DESC, populated_count DESC, field ASC"
            )
            rows = await cur.fetchall()
            return [r[0] for r in rows]
    except sqlite3.OperationalError as exc:
        logger.warning("meta.get_field_presence failed: %s", exc)
        return []
