"""
Normalizer run history (prompts-039).

Records every normalizer execution — manual ("Run Now"), scheduled (the
``normalizer__auto`` APScheduler tick), and on-demand consolidated re-apply
(``POST /smart-mappings/active/run``) — so operators can audit when the
normalizer ran and what it produced.

Stored in its OWN SQLite file (``data/run_history.db``), deliberately separate
from ``normalized.db`` (which is drop+recreated on any schema bump) so run
history is never wiped by a normalized-schema change.

Each row carries:
  * started_at     — ISO-8601 UTC timestamp of the run
  * trigger        — 'manual' | 'schedule' | 'reapply'
  * mode           — normalizer mode at run time ('auto' | 'manual' | 'smart')
  * proposal_id    — backing consolidated proposal id (smart applies only)
  * proposal_name  — human label of that proposal (smart applies only)
  * sources_json   — feeds the run applied to (smart applies only; else [])
  * status         — the run result status ('ok' | 'disabled' | 'error' | ...)
  * processed / inserted / errors — run counters
  * warning        — non-null when smart fell back to auto, etc.

Retention: capped at the newest ``_MAX_ROWS`` rows; older rows are trimmed on
every insert (prompts-039 decision: keep last 500).
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
_RUN_DB_PATH = _PROJECT_ROOT / "data" / "run_history.db"

# Bump if the table shape changes. v1 is the initial schema.
_RUN_SCHEMA_VERSION = 1

# Retention cap — trim to the newest N rows on each insert.
_MAX_ROWS = 500


CREATE_RUN_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS run_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,
    trigger       TEXT    NOT NULL DEFAULT 'manual',
    mode          TEXT,
    proposal_id   INTEGER,
    proposal_name TEXT,
    sources_json  TEXT    NOT NULL DEFAULT '[]',
    status        TEXT    NOT NULL DEFAULT 'ok',
    processed     INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    warning       TEXT
);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


async def init_run_history_db() -> None:
    """Create the run_history table + schema_version row if missing. Idempotent."""
    _RUN_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_RUN_DB_PATH) as db:
        await db.execute(CREATE_RUN_HISTORY_TABLE)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_RUN_SCHEMA_VERSION,),
            )
        await db.commit()


def _row_to_dict(row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    d = dict(row)
    try:
        d["sources"] = json.loads(d.pop("sources_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["sources"] = []
    return d


async def record_run(
    *,
    trigger: str,
    mode: str | None,
    status: str,
    processed: int = 0,
    inserted: int = 0,
    errors: int = 0,
    proposal_id: int | None = None,
    proposal_name: str | None = None,
    sources: list[str] | None = None,
    warning: str | None = None,
    started_at: str | None = None,
) -> int:
    """Insert one run-history row and trim to the newest ``_MAX_ROWS``.

    Returns the new row id. Best-effort: callers should not let a history
    write failure break a normalizer run.
    """
    await init_run_history_db()
    ts = started_at or datetime.now(timezone.utc).isoformat()
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    async with aiosqlite.connect(_RUN_DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO run_history (
                started_at, trigger, mode, proposal_id, proposal_name,
                sources_json, status, processed, inserted, errors, warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                trigger,
                mode,
                proposal_id,
                proposal_name,
                sources_json,
                status,
                int(processed),
                int(inserted),
                int(errors),
                warning,
            ),
        )
        new_id = cur.lastrowid
        await cur.close()
        # Retention: delete everything older than the newest _MAX_ROWS rows.
        await db.execute(
            """
            DELETE FROM run_history
            WHERE id NOT IN (
                SELECT id FROM run_history ORDER BY id DESC LIMIT ?
            )
            """,
            (_MAX_ROWS,),
        )
        await db.commit()
        return int(new_id)


async def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent run-history rows, newest first."""
    await init_run_history_db()
    if limit < 1:
        limit = 1
    async with aiosqlite.connect(_RUN_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(
            "SELECT * FROM run_history ORDER BY id DESC LIMIT ?", (limit,)
        ):
            rows.append(_row_to_dict(row))
    return rows
