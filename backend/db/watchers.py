"""
Watchers store (issue_local_006).

A *watcher* is a user-defined saved filter that queries the event databases
(raw per-source ``entries`` and/or the consolidated ``normalized_entries``) and
publishes matching events to a public per-watcher feed URL.

Two tables live in their OWN SQLite file (``data/watchers.db``), deliberately
separate from ``normalized.db`` (which is drop+recreated on any schema bump) so
watcher definitions and their triggered-event history are never wiped by a
normalized-schema change:

  * ``watchers``        — one row per watcher definition.
  * ``watcher_events``  — one row per *triggered* event. A given source event
                          triggers a given watcher at most once, enforced by a
                          UNIQUE(watcher_id, dataset, source_entry_id) index.

Retention: ``watcher_events`` is capped per watcher at the global
``watcher_max_events`` Application setting (default 1000); older rows are trimmed
on every insert batch.

The connection pattern mirrors the rest of the backend: short-lived
``async with aiosqlite.connect(...)`` per operation, no pool.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WATCHERS_DB_PATH = _PROJECT_ROOT / "data" / "watchers.db"

# Bump if the table shape changes. v1 is the initial schema.
_WATCHERS_SCHEMA_VERSION = 1

VALID_DATASETS: frozenset[str] = frozenset({"all", "raw", "normalized"})
VALID_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})
VALID_MODES: frozenset[str] = frozenset({"realtime", "scheduled"})
VALID_FORMATS: frozenset[str] = frozenset({"json", "csv", "xml"})
VALID_MATCH_TYPES: frozenset[str] = frozenset({"exact", "wildcard", "regex"})

_MIN_INTERVAL_SEC = 5
_MAX_FEED_EVENTS_DEFAULT = 10
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LEN = 64


CREATE_WATCHERS_TABLE = """
CREATE TABLE IF NOT EXISTS watchers (
    id               TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    severity         TEXT    NOT NULL DEFAULT 'low',
    dataset          TEXT    NOT NULL DEFAULT 'all',
    feeds_json       TEXT    NOT NULL DEFAULT '[]',
    conditions_json  TEXT    NOT NULL DEFAULT '[]',
    mode             TEXT    NOT NULL DEFAULT 'realtime',
    interval_sec     INTEGER NOT NULL DEFAULT 120,
    format           TEXT    NOT NULL DEFAULT 'json',
    max_feed_events  INTEGER NOT NULL DEFAULT 10,
    enabled          INTEGER NOT NULL DEFAULT 0,
    trigger_count    INTEGER NOT NULL DEFAULT 0,
    last_eval_raw_id  INTEGER NOT NULL DEFAULT 0,
    last_eval_norm_id INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
"""

CREATE_WATCHER_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS watcher_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher_id      TEXT    NOT NULL,
    dataset         TEXT    NOT NULL,
    source_entry_id INTEGER NOT NULL,
    source_name     TEXT,
    triggered_at    TEXT    NOT NULL,
    event_json      TEXT    NOT NULL DEFAULT '{}'
);
"""

CREATE_WATCHER_EVENTS_DEDUP_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_watcher_events_dedup
ON watcher_events (watcher_id, dataset, source_entry_id);
"""

CREATE_WATCHER_EVENTS_LOOKUP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_watcher_events_lookup
ON watcher_events (watcher_id, id DESC);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(name: str) -> str:
    """Derive a URL-safe watcher id from its name.

    Lowercased, non-alphanumeric runs collapsed to '-', trimmed. Raises
    ValueError if the result is empty (e.g. name was only punctuation).
    """
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    slug = slug[:_MAX_SLUG_LEN].strip("-")
    if not slug:
        raise ValueError("watcher name must contain at least one alphanumeric character")
    return slug


async def init_watchers_db() -> None:
    """Create the watcher tables + schema_version row if missing. Idempotent."""
    _WATCHERS_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        await db.execute(CREATE_WATCHERS_TABLE)
        await db.execute(CREATE_WATCHER_EVENTS_TABLE)
        await db.execute(CREATE_WATCHER_EVENTS_DEDUP_INDEX)
        await db.execute(CREATE_WATCHER_EVENTS_LOOKUP_INDEX)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_WATCHERS_SCHEMA_VERSION,),
            )
        await db.commit()


def _row_to_watcher(row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    d = dict(row)
    try:
        d["feeds"] = json.loads(d.pop("feeds_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["feeds"] = []
    try:
        d["conditions"] = json.loads(d.pop("conditions_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["conditions"] = []
    d["enabled"] = bool(d.get("enabled"))
    return d


def normalize_conditions(conditions: Any) -> list[dict[str, str]]:
    """Validate + normalize the field-condition list.

    Each condition is ``{field, value, match_type}``. ``field`` may be empty/
    ``"*"`` (match any field). ``match_type`` defaults to ``exact``. Regex
    conditions are pre-compiled here to reject invalid patterns early.
    """
    out: list[dict[str, str]] = []
    if not isinstance(conditions, list):
        raise ValueError("conditions must be a list")
    for raw in conditions:
        if not isinstance(raw, dict):
            raise ValueError("each condition must be an object")
        field = str(raw.get("field", "") or "").strip()
        value = str(raw.get("value", "") or "")
        match_type = str(raw.get("match_type", "exact") or "exact").strip().lower()
        if match_type not in VALID_MATCH_TYPES:
            raise ValueError(f"invalid match_type: {match_type!r}")
        if value == "":
            raise ValueError("condition value must not be empty")
        if len(value) > 2000:
            raise ValueError("condition value too long (max 2000 chars)")
        if match_type == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        out.append({"field": field, "value": value, "match_type": match_type})
    return out


def validate_definition(data: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce an incoming watcher definition into stored form.

    Returns a dict of normalized scalar fields (name, severity, dataset, feeds,
    conditions, mode, interval_sec, format, max_feed_events, enabled). Raises
    ValueError on any invalid input.
    """
    name = str(data.get("name", "") or "").strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > 120:
        raise ValueError("name too long (max 120 chars)")

    severity = str(data.get("severity", "low") or "low").strip().lower()
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity: {severity!r}")

    dataset = str(data.get("dataset", "all") or "all").strip().lower()
    if dataset not in VALID_DATASETS:
        raise ValueError(f"invalid dataset: {dataset!r}")

    feeds = data.get("feeds", []) or []
    if not isinstance(feeds, list) or not all(isinstance(f, str) for f in feeds):
        raise ValueError("feeds must be a list of source names")

    conditions = normalize_conditions(data.get("conditions", []) or [])

    mode = str(data.get("mode", "realtime") or "realtime").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode!r}")

    try:
        interval_sec = int(data.get("interval_sec", 120))
    except (TypeError, ValueError):
        raise ValueError("interval_sec must be an integer")
    if interval_sec < _MIN_INTERVAL_SEC:
        raise ValueError(f"interval_sec must be >= {_MIN_INTERVAL_SEC}")

    fmt = str(data.get("format", "json") or "json").strip().lower()
    if fmt not in VALID_FORMATS:
        raise ValueError(f"invalid format: {fmt!r}")

    try:
        max_feed_events = int(data.get("max_feed_events", _MAX_FEED_EVENTS_DEFAULT))
    except (TypeError, ValueError):
        raise ValueError("max_feed_events must be an integer")
    if max_feed_events < 1:
        raise ValueError("max_feed_events must be >= 1")

    enabled = bool(data.get("enabled", False))

    return {
        "name": name,
        "severity": severity,
        "dataset": dataset,
        "feeds": feeds,
        "conditions": conditions,
        "mode": mode,
        "interval_sec": interval_sec,
        "format": fmt,
        "max_feed_events": max_feed_events,
        "enabled": enabled,
    }


# ── Watcher CRUD ────────────────────────────────────────────────────────────


async def create_watcher(data: dict[str, Any]) -> dict[str, Any]:
    """Create a watcher. Raises ValueError on validation error or duplicate id."""
    await init_watchers_db()
    fields = validate_definition(data)
    wid = slugify(fields["name"])
    ts = _now()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        existing = await db.execute("SELECT 1 FROM watchers WHERE id = ?", (wid,))
        if await existing.fetchone() is not None:
            await existing.close()
            raise ValueError(f"a watcher with id '{wid}' already exists")
        await existing.close()
        await db.execute(
            """
            INSERT INTO watchers (
                id, name, severity, dataset, feeds_json, conditions_json,
                mode, interval_sec, format, max_feed_events, enabled,
                trigger_count, last_eval_raw_id, last_eval_norm_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
            """,
            (
                wid, fields["name"], fields["severity"], fields["dataset"],
                json.dumps(fields["feeds"], ensure_ascii=False),
                json.dumps(fields["conditions"], ensure_ascii=False),
                fields["mode"], fields["interval_sec"], fields["format"],
                fields["max_feed_events"], int(fields["enabled"]), ts, ts,
            ),
        )
        await db.commit()
    logger.info("watcher created: id=%s name=%s", wid, fields["name"])
    return await get_watcher(wid)  # type: ignore[return-value]


async def update_watcher(watcher_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Update a watcher in place (id is immutable). Returns the row or None."""
    await init_watchers_db()
    fields = validate_definition(data)
    ts = _now()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE watchers SET
                name = ?, severity = ?, dataset = ?, feeds_json = ?,
                conditions_json = ?, mode = ?, interval_sec = ?, format = ?,
                max_feed_events = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                fields["name"], fields["severity"], fields["dataset"],
                json.dumps(fields["feeds"], ensure_ascii=False),
                json.dumps(fields["conditions"], ensure_ascii=False),
                fields["mode"], fields["interval_sec"], fields["format"],
                fields["max_feed_events"], int(fields["enabled"]), ts, watcher_id,
            ),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
    logger.info("watcher updated: id=%s", watcher_id)
    return await get_watcher(watcher_id)


async def set_enabled(watcher_id: str, enabled: bool) -> dict[str, Any] | None:
    """Toggle a watcher's enabled flag. Returns the row or None if not found."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE watchers SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(bool(enabled)), _now(), watcher_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
    logger.info("watcher %s %s", watcher_id, "enabled" if enabled else "disabled")
    return await get_watcher(watcher_id)


async def delete_watcher(watcher_id: str) -> bool:
    """Delete a watcher and all its triggered events. Returns True if removed."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute("DELETE FROM watchers WHERE id = ?", (watcher_id,))
        await db.execute("DELETE FROM watcher_events WHERE watcher_id = ?", (watcher_id,))
        await db.commit()
        removed = cur.rowcount > 0
    if removed:
        logger.info("watcher deleted: id=%s", watcher_id)
    return removed


async def get_watcher(watcher_id: str) -> dict[str, Any] | None:
    """Return a single watcher row (parsed) or None."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM watchers WHERE id = ?", (watcher_id,))
        row = await cur.fetchone()
        await cur.close()
    return _row_to_watcher(row) if row is not None else None


async def list_watchers() -> list[dict[str, Any]]:
    """Return all watchers, newest first."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(
            "SELECT * FROM watchers ORDER BY created_at DESC"
        ):
            rows.append(_row_to_watcher(row))
    return rows


async def list_enabled_watchers() -> list[dict[str, Any]]:
    """Return only enabled watchers (for the evaluation engine / scheduler)."""
    return [w for w in await list_watchers() if w.get("enabled")]


# ── Triggered-event recording / reads ───────────────────────────────────────


async def record_triggers(
    watcher_id: str,
    rows: list[dict[str, Any]],
    *,
    max_events: int,
) -> int:
    """Insert triggered-event rows (dedup via UNIQUE index) and prune to
    ``max_events`` newest per watcher.

    ``rows`` items: ``{dataset, source_entry_id, source_name, event}`` where
    ``event`` is the full field snapshot dict. Returns the number of genuinely
    new rows inserted (i.e. excluding dedup-ignored duplicates).
    """
    if not rows:
        return 0
    await init_watchers_db()
    ts = _now()
    inserted = 0
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        for r in rows:
            cur = await db.execute(
                """
                INSERT OR IGNORE INTO watcher_events (
                    watcher_id, dataset, source_entry_id, source_name,
                    triggered_at, event_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    watcher_id,
                    str(r.get("dataset", "")),
                    int(r.get("source_entry_id", 0)),
                    r.get("source_name"),
                    ts,
                    json.dumps(r.get("event", {}), ensure_ascii=False, default=str),
                ),
            )
            inserted += cur.rowcount or 0
            await cur.close()
        if inserted:
            await db.execute(
                "UPDATE watchers SET trigger_count = trigger_count + ?, updated_at = ? WHERE id = ?",
                (inserted, ts, watcher_id),
            )
            # Retention: keep the newest ``max_events`` rows for this watcher.
            await db.execute(
                """
                DELETE FROM watcher_events
                WHERE watcher_id = ? AND id NOT IN (
                    SELECT id FROM watcher_events WHERE watcher_id = ?
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (watcher_id, watcher_id, int(max_events)),
            )
        await db.commit()
    return inserted


async def update_high_water(
    watcher_id: str,
    *,
    raw_id: int | None = None,
    norm_id: int | None = None,
) -> None:
    """Advance a watcher's per-dataset high-water marks (never decreases)."""
    await init_watchers_db()
    sets: list[str] = []
    params: list[Any] = []
    if raw_id is not None:
        sets.append("last_eval_raw_id = MAX(last_eval_raw_id, ?)")
        params.append(int(raw_id))
    if norm_id is not None:
        sets.append("last_eval_norm_id = MAX(last_eval_norm_id, ?)")
        params.append(int(norm_id))
    if not sets:
        return
    params.append(watcher_id)
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        await db.execute(
            f"UPDATE watchers SET {', '.join(sets)} WHERE id = ?", params
        )
        await db.commit()


async def list_events(
    watcher_id: str, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """Return a watcher's triggered events, newest first (parsed event_json)."""
    await init_watchers_db()
    if limit < 1:
        limit = 1
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(
            "SELECT * FROM watcher_events WHERE watcher_id = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (watcher_id, limit, offset),
        ):
            d = dict(row)
            try:
                d["event"] = json.loads(d.pop("event_json", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["event"] = {}
            rows.append(d)
    return rows


async def count_events(watcher_id: str) -> int:
    """Return the total triggered-event count stored for a watcher."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM watcher_events WHERE watcher_id = ?", (watcher_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    return int(row[0]) if row else 0


def list_scheduled_watchers_sync() -> list[dict[str, Any]]:
    """Return enabled watchers in 'scheduled' mode as ``{id, interval_sec}``.

    Synchronous (sqlite3) so the APScheduler ``reload()`` — which runs outside an
    event loop — can build per-watcher interval jobs without awaiting. Returns an
    empty list if the DB file does not exist yet.
    """
    if not _WATCHERS_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(_WATCHERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, interval_sec FROM watchers "
                "WHERE enabled = 1 AND mode = 'scheduled'"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return []
    return [{"id": r["id"], "interval_sec": int(r["interval_sec"])} for r in rows]
