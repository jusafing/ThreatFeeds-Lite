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
