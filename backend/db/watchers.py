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
from urllib.parse import urlparse

import aiosqlite

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WATCHERS_DB_PATH = _PROJECT_ROOT / "data" / "watchers.db"

# Bump if the table shape changes.
#   v1 — initial schema.
#   v2 — per-source high-water table + dedup index now includes source_name
#        (issue_local_006 review_02b: raw entry ids are per-source sequences, so
#        a single global high-water mark and a source-less dedup key were both
#        incorrect across feeds).
#   v3 — publish targets (issue_local_007): watchers gain publish_target /
#        webhook_url / auth_header / auth_value; watcher_events gain per-event
#        delivery_status / delivery_error / delivered_at.
#   v4 — webhook formats + rich delivery detail (issue_local_007 review_01):
#        watchers gain webhook_format (generic|discord|slack|teams); existing
#        webhook watchers are best-effort backfilled by URL host. watcher_events
#        gain delivery_detail (JSON: status/headers/body/url) so a failed
#        delivery can be inspected in a UI card.
#   v5 — periodic feed retention (issue_local_008): watchers gain
#        cleanup_interval_sec (seconds between background trims of a watcher's
#        feed down to its max_feed_events). Immediate inserts still keep up to
#        the global watcher_max_events hard limit; the periodic job enforces the
#        per-watcher max_feed_events.
_WATCHERS_SCHEMA_VERSION = 5

# Allowed publish targets for a watcher (issue_local_007).
#   local   — publish only to the public /feed/watcher/<id>/ URL (default).
#   webhook — POST a JSON envelope to a remote webhook URL.
#   http    — POST the bare event JSON to a remote listener (the same shape the
#             /api/ingest/listener endpoint accepts).
VALID_PUBLISH_TARGETS: frozenset[str] = frozenset({"local", "webhook", "http"})

# Webhook payload shapes (issue_local_007 review_01). Only meaningful when
# publish_target == "webhook":
#   generic — the ThreatFeeds envelope {watcher, watcher_id, ..., event}.
#   discord — Discord webhook {"content": ...}.
#   slack   — Slack / Mattermost incoming webhook {"text": ...}.
#   teams   — Microsoft Teams legacy MessageCard connector payload.
VALID_WEBHOOK_FORMATS: frozenset[str] = frozenset({"generic", "discord", "slack", "teams"})

# Best-effort mapping of a webhook URL host substring to its format, used both
# by the v4 backfill migration and (optionally) as a UI default.
WEBHOOK_HOST_FORMATS: tuple[tuple[str, str], ...] = (
    ("discord.com", "discord"),
    ("discordapp.com", "discord"),
    ("hooks.slack.com", "slack"),
    ("mattermost", "slack"),
    ("webhook.office.com", "teams"),
    ("office.com", "teams"),
    ("logic.azure.com", "teams"),
)


def detect_webhook_format(url: str | None) -> str:
    """Return the best-effort webhook format for a URL, or 'generic'."""
    host = (url or "").lower()
    for needle, fmt in WEBHOOK_HOST_FORMATS:
        if needle in host:
            return fmt
    return "generic"

VALID_DATASETS: frozenset[str] = frozenset({"all", "raw", "normalized"})
VALID_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})
VALID_MODES: frozenset[str] = frozenset({"realtime", "scheduled"})
VALID_FORMATS: frozenset[str] = frozenset({"json", "csv", "xml"})
VALID_MATCH_TYPES: frozenset[str] = frozenset(
    {"exact", "wildcard", "regex", "gte", "lte", "contains"}
)

# Match types that compare numerically and therefore require a numeric value.
NUMERIC_MATCH_TYPES: frozenset[str] = frozenset({"gte", "lte"})

# Match types whose comparison can honour a per-condition ``case_sensitive``
# flag (issue_local_008). regex callers control case via inline flags, and the
# numeric comparisons are case-agnostic, so neither is included here.
CASE_AWARE_MATCH_TYPES: frozenset[str] = frozenset({"exact", "wildcard", "contains"})

_MIN_INTERVAL_SEC = 5
_MAX_FEED_EVENTS_DEFAULT = 10
# Periodic feed-retention cleanup interval (issue_local_008).
_CLEANUP_INTERVAL_DEFAULT = 60
_CLEANUP_INTERVAL_MIN = 10
_CLEANUP_INTERVAL_MAX = 86400
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
    cleanup_interval_sec INTEGER NOT NULL DEFAULT 60,
    enabled          INTEGER NOT NULL DEFAULT 0,
    trigger_count    INTEGER NOT NULL DEFAULT 0,
    last_eval_raw_id  INTEGER NOT NULL DEFAULT 0,
    last_eval_norm_id INTEGER NOT NULL DEFAULT 0,
    publish_target   TEXT    NOT NULL DEFAULT 'local',
    webhook_url      TEXT,
    webhook_format   TEXT    NOT NULL DEFAULT 'generic',
    auth_header      TEXT,
    auth_value       TEXT,
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
    event_json      TEXT    NOT NULL DEFAULT '{}',
    delivery_status TEXT,
    delivery_error  TEXT,
    delivery_detail TEXT,
    delivered_at    TEXT
);
"""

CREATE_WATCHER_EVENTS_DEDUP_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_watcher_events_dedup
ON watcher_events (watcher_id, dataset, source_entry_id, source_name);
"""

CREATE_WATCHER_EVENTS_LOOKUP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_watcher_events_lookup
ON watcher_events (watcher_id, id DESC);
"""

# Per-source high-water marks. Raw `entries` ids are independent per-source
# sequences, so a single global mark on the watchers row is wrong (it blocks
# triggering for any feed whose ids fall below the global max). We track the
# last-evaluated id per (watcher, dataset, source). The consolidated normalized
# store uses a single logical source key of '' (empty string).
CREATE_WATCHER_HIGH_WATER_TABLE = """
CREATE TABLE IF NOT EXISTS watcher_high_water (
    watcher_id  TEXT    NOT NULL,
    dataset     TEXT    NOT NULL,
    source_name TEXT    NOT NULL DEFAULT '',
    last_id     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (watcher_id, dataset, source_name)
);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

# SQL fragment (issue_local_007) computing per-watcher delivery-error aggregates
# for the list/get responses: how many recorded events failed delivery, and the
# most recent error text. Spliced between SELECT expressions, so it ends with a
# trailing comma.
_DELIVERY_AGG_SQL = (
    "(SELECT COUNT(*) FROM watcher_events e "
    " WHERE e.watcher_id = watchers.id AND e.delivery_status = 'error') "
    "AS delivery_error_count, "
    "(SELECT e.delivery_error FROM watcher_events e "
    " WHERE e.watcher_id = watchers.id AND e.delivery_status = 'error' "
    " ORDER BY e.id DESC LIMIT 1) AS last_delivery_error, "
    "(SELECT e.delivery_detail FROM watcher_events e "
    " WHERE e.watcher_id = watchers.id AND e.delivery_status = 'error' "
    " ORDER BY e.id DESC LIMIT 1) AS last_delivery_detail "
)


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
    """Create the watcher tables + schema_version row if missing, and run
    forward migrations. Idempotent."""
    _WATCHERS_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        await db.execute(CREATE_WATCHERS_TABLE)
        await db.execute(CREATE_WATCHER_EVENTS_TABLE)
        await db.execute(CREATE_WATCHER_HIGH_WATER_TABLE)
        await db.execute(CREATE_WATCHER_EVENTS_LOOKUP_INDEX)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        current = int(row[0]) if row is not None else 0
        await _migrate(db, current)
        # Always (re)create the current dedup index after any migration.
        await db.execute(CREATE_WATCHER_EVENTS_DEDUP_INDEX)
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_WATCHERS_SCHEMA_VERSION,),
            )
        elif current != _WATCHERS_SCHEMA_VERSION:
            await db.execute(
                "UPDATE schema_version SET version = ?", (_WATCHERS_SCHEMA_VERSION,)
            )
        await db.commit()


async def _migrate(db: aiosqlite.Connection, from_version: int) -> None:
    """Apply forward migrations in place. Safe to call repeatedly.

    A fresh DB (``from_version`` 0) needs nothing beyond the CREATE statements
    in ``init_watchers_db``. An existing v1 DB must:
      * gain the ``watcher_high_water`` table (created unconditionally above), and
      * replace the old 3-column dedup index with the 4-column one that includes
        ``source_name`` (the new index is (re)created by the caller).

    The old global high-water columns (``last_eval_raw_id`` / ``last_eval_norm_id``)
    are intentionally NOT seeded into the per-source table — the global marks are
    wrong, so each watcher re-evaluates from scratch once (a bounded one-time
    backfill, capped by ``watcher_max_events``).
    """
    if from_version < 2:
        # Drop the legacy source-less dedup index; the caller recreates the
        # 4-column version. No-op if it was never created.
        await db.execute("DROP INDEX IF EXISTS idx_watcher_events_dedup")
    if from_version < 3:
        # Publish targets (issue_local_007): add columns to existing tables.
        # Fresh DBs already have them from the CREATE statements; for upgraded
        # DBs we add any that are missing (ALTER ADD COLUMN is idempotent-safe
        # only if guarded, so we check the existing column set first).
        await _add_columns_if_missing(
            db, "watchers",
            {
                "publish_target": "TEXT NOT NULL DEFAULT 'local'",
                "webhook_url": "TEXT",
                "auth_header": "TEXT",
                "auth_value": "TEXT",
            },
        )
        await _add_columns_if_missing(
            db, "watcher_events",
            {
                "delivery_status": "TEXT",
                "delivery_error": "TEXT",
                "delivered_at": "TEXT",
            },
        )
    if from_version < 4:
        # Webhook formats + rich delivery detail (review_01).
        await _add_columns_if_missing(
            db, "watchers",
            {"webhook_format": "TEXT NOT NULL DEFAULT 'generic'"},
        )
        await _add_columns_if_missing(
            db, "watcher_events",
            {"delivery_detail": "TEXT"},
        )
        # Best-effort backfill: existing webhook watchers stored before formats
        # existed default to 'generic', which breaks chat receivers (e.g. Discord
        # rejects the envelope). Infer the format from the URL host so they work
        # after redeploy + retry without manual reconfiguration.
        cur = await db.execute(
            "SELECT id, webhook_url FROM watchers "
            "WHERE publish_target = 'webhook' AND COALESCE(webhook_format, 'generic') = 'generic'"
        )
        rows = await cur.fetchall()
        await cur.close()
        for wid, url in rows:
            fmt = detect_webhook_format(url)
            if fmt != "generic":
                await db.execute(
                    "UPDATE watchers SET webhook_format = ? WHERE id = ?", (fmt, wid)
                )
    if from_version < 5:
        # Periodic feed retention (issue_local_008): add the cleanup interval
        # column. Existing watchers default to 60s.
        await _add_columns_if_missing(
            db, "watchers",
            {"cleanup_interval_sec": "INTEGER NOT NULL DEFAULT 60"},
        )


async def _add_columns_if_missing(
    db: aiosqlite.Connection, table: str, columns: dict[str, str]
) -> None:
    """Add any missing ``columns`` to ``table`` via ALTER TABLE ADD COLUMN.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so we read the current schema
    and only add columns that are absent — keeping the migration idempotent.
    """
    cur = await db.execute(f"PRAGMA table_info({table})")
    existing = {r[1] for r in await cur.fetchall()}
    await cur.close()
    for name, decl in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
    if "last_delivery_detail" in d:
        raw = d.get("last_delivery_detail")
        if raw:
            try:
                d["last_delivery_detail"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d["last_delivery_detail"] = None
        else:
            d["last_delivery_detail"] = None
    return d


def normalize_conditions(conditions: Any) -> list[dict[str, Any]]:
    """Validate + normalize the field-condition list.

    Each condition is ``{field, value, match_type, case_sensitive}``. ``field``
    may be empty/``"*"`` (match any field). ``match_type`` defaults to ``exact``.
    Regex conditions are pre-compiled here to reject invalid patterns early.
    ``case_sensitive`` (default False) only affects the string match types
    (exact / wildcard / contains); it is ignored for regex / gte / lte.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(conditions, list):
        raise ValueError("conditions must be a list")
    for raw in conditions:
        if not isinstance(raw, dict):
            raise ValueError("each condition must be an object")
        field = str(raw.get("field", "") or "").strip()
        value = str(raw.get("value", "") or "")
        match_type = str(raw.get("match_type", "exact") or "exact").strip().lower()
        case_sensitive = bool(raw.get("case_sensitive", False))
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
        if match_type in NUMERIC_MATCH_TYPES:
            try:
                float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"condition value for '{match_type}' must be numeric, got {value!r}"
                ) from None
        out.append({
            "field": field,
            "value": value,
            "match_type": match_type,
            "case_sensitive": case_sensitive,
        })
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
    if not conditions:
        raise ValueError("at least one condition is required")

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

    try:
        cleanup_interval_sec = int(
            data.get("cleanup_interval_sec", _CLEANUP_INTERVAL_DEFAULT)
        )
    except (TypeError, ValueError):
        raise ValueError("cleanup_interval_sec must be an integer")
    if not (_CLEANUP_INTERVAL_MIN <= cleanup_interval_sec <= _CLEANUP_INTERVAL_MAX):
        raise ValueError(
            f"cleanup_interval_sec must be between {_CLEANUP_INTERVAL_MIN} "
            f"and {_CLEANUP_INTERVAL_MAX}"
        )

    enabled = bool(data.get("enabled", False))

    publish_target = str(data.get("publish_target", "local") or "local").strip().lower()
    if publish_target not in VALID_PUBLISH_TARGETS:
        raise ValueError(f"invalid publish_target: {publish_target!r}")

    webhook_url = str(data.get("webhook_url", "") or "").strip()
    auth_header = str(data.get("auth_header", "") or "").strip()
    auth_value = str(data.get("auth_value", "") or "")
    webhook_format = str(data.get("webhook_format", "generic") or "generic").strip().lower()
    if webhook_format not in VALID_WEBHOOK_FORMATS:
        raise ValueError(f"invalid webhook_format: {webhook_format!r}")
    if publish_target == "local":
        # A local-feed watcher has no remote target; clear any URL/auth so they
        # are not silently persisted.
        webhook_url = ""
        auth_header = ""
        auth_value = ""
        webhook_format = "generic"
    else:
        if not webhook_url:
            raise ValueError("webhook_url is required when publish_target is not 'local'")
        parsed = urlparse(webhook_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("webhook_url must be a valid http(s) URL")
        if len(webhook_url) > 2000:
            raise ValueError("webhook_url too long (max 2000 chars)")
        # Auth header name and value are coupled: either both set or both empty.
        if bool(auth_header) != bool(auth_value):
            raise ValueError("auth_header and auth_value must be provided together")
        if len(auth_header) > 200 or len(auth_value) > 2000:
            raise ValueError("auth header name/value too long")
        # webhook_format only applies to the 'webhook' target; the 'http' target
        # always sends the bare event JSON, so force generic there.
        if publish_target != "webhook":
            webhook_format = "generic"

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
        "cleanup_interval_sec": cleanup_interval_sec,
        "enabled": enabled,
        "publish_target": publish_target,
        "webhook_url": webhook_url,
        "webhook_format": webhook_format,
        "auth_header": auth_header,
        "auth_value": auth_value,
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
                mode, interval_sec, format, max_feed_events, cleanup_interval_sec,
                enabled, trigger_count, last_eval_raw_id, last_eval_norm_id,
                publish_target, webhook_url, webhook_format, auth_header, auth_value,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wid, fields["name"], fields["severity"], fields["dataset"],
                json.dumps(fields["feeds"], ensure_ascii=False),
                json.dumps(fields["conditions"], ensure_ascii=False),
                fields["mode"], fields["interval_sec"], fields["format"],
                fields["max_feed_events"], fields["cleanup_interval_sec"],
                int(fields["enabled"]),
                fields["publish_target"], fields["webhook_url"] or None,
                fields["webhook_format"],
                fields["auth_header"] or None, fields["auth_value"] or None,
                ts, ts,
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
                max_feed_events = ?, cleanup_interval_sec = ?, enabled = ?,
                publish_target = ?, webhook_url = ?, webhook_format = ?,
                auth_header = ?, auth_value = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                fields["name"], fields["severity"], fields["dataset"],
                json.dumps(fields["feeds"], ensure_ascii=False),
                json.dumps(fields["conditions"], ensure_ascii=False),
                fields["mode"], fields["interval_sec"], fields["format"],
                fields["max_feed_events"], fields["cleanup_interval_sec"],
                int(fields["enabled"]),
                fields["publish_target"], fields["webhook_url"] or None,
                fields["webhook_format"],
                fields["auth_header"] or None, fields["auth_value"] or None,
                ts, watcher_id,
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
        await db.execute("DELETE FROM watcher_high_water WHERE watcher_id = ?", (watcher_id,))
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
        cur = await db.execute(
            "SELECT *, "
            "(SELECT MAX(triggered_at) FROM watcher_events e WHERE e.watcher_id = watchers.id) "
            "AS last_triggered_at, "
            + _DELIVERY_AGG_SQL +
            "FROM watchers WHERE id = ?",
            (watcher_id,),
        )
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
            "SELECT *, "
            "(SELECT MAX(triggered_at) FROM watcher_events e WHERE e.watcher_id = watchers.id) "
            "AS last_triggered_at, "
            + _DELIVERY_AGG_SQL +
            "FROM watchers ORDER BY created_at DESC"
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


async def get_high_water_map(watcher_id: str, dataset: str) -> dict[str, int]:
    """Return ``{source_name: last_id}`` per-source high-water marks for a
    watcher+dataset. Missing sources default to 0 at the call site."""
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            "SELECT source_name, last_id FROM watcher_high_water "
            "WHERE watcher_id = ? AND dataset = ?",
            (watcher_id, dataset),
        )
        rows = await cur.fetchall()
        await cur.close()
    return {str(r[0]): int(r[1]) for r in rows}


async def update_high_water_map(
    watcher_id: str, dataset: str, marks: dict[str, int]
) -> None:
    """Advance per-source high-water marks (never decreases). ``marks`` maps
    ``source_name -> max_id`` observed this pass."""
    if not marks:
        return
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        for source_name, last_id in marks.items():
            await db.execute(
                """
                INSERT INTO watcher_high_water (watcher_id, dataset, source_name, last_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(watcher_id, dataset, source_name)
                DO UPDATE SET last_id = MAX(last_id, excluded.last_id)
                """,
                (watcher_id, dataset, str(source_name), int(last_id)),
            )
        await db.commit()


async def update_high_water(
    watcher_id: str,
    *,
    raw_id: int | None = None,
    norm_id: int | None = None,
) -> None:
    """Advance a watcher's legacy global high-water columns (never decreases).

    Retained for backward compatibility; the engine now uses the per-source
    ``watcher_high_water`` table instead (see ``update_high_water_map``).
    """
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


def _decode_event_row(row: aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    """Convert a watcher_events row to a dict with parsed event + delivery_detail."""
    d = dict(row)
    try:
        d["event"] = json.loads(d.pop("event_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["event"] = {}
    raw_detail = d.get("delivery_detail")
    if raw_detail:
        try:
            d["delivery_detail"] = json.loads(raw_detail)
        except (json.JSONDecodeError, TypeError):
            d["delivery_detail"] = None
    else:
        d["delivery_detail"] = None
    return d


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
            rows.append(_decode_event_row(row))
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


async def cleanup_watcher_events(watcher_id: str, max_feed_events: int) -> int:
    """Trim a watcher's stored events down to its ``max_feed_events`` newest rows.

    This is the periodic feed-retention pass (issue_local_008): immediate inserts
    keep up to the global ``watcher_max_events`` hard limit so a delivery backlog
    is never lost, while this job enforces the per-watcher feed cap. Returns the
    number of rows deleted.
    """
    await init_watchers_db()
    keep = max(int(max_feed_events), 1)
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            """
            DELETE FROM watcher_events
            WHERE watcher_id = ? AND id NOT IN (
                SELECT id FROM watcher_events WHERE watcher_id = ?
                ORDER BY id DESC LIMIT ?
            )
            """,
            (watcher_id, watcher_id, keep),
        )
        deleted = cur.rowcount or 0
        await cur.close()
        await db.commit()
    if deleted:
        logger.info(
            "watcher %s cleanup trimmed %d event(s) to max_feed_events=%d",
            watcher_id, deleted, keep,
        )
    return deleted


async def run_watcher_cleanup(watcher_id: str) -> int:
    """Periodic-job entry point: trim a watcher to its current max_feed_events.

    Reads ``max_feed_events`` fresh so a mid-flight edit takes effect on the next
    tick. Returns the number of rows deleted (0 if the watcher is gone).
    """
    await init_watchers_db()
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        cur = await db.execute(
            "SELECT max_feed_events FROM watchers WHERE id = ?", (watcher_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return 0
    return await cleanup_watcher_events(watcher_id, int(row[0]))


# ── Delivery (issue_local_007) ──────────────────────────────────────────────


async def list_pending_deliveries(
    watcher_id: str, limit: int = 1000
) -> list[dict[str, Any]]:
    """Return events for a watcher that have not yet been delivered successfully.

    "Pending" means ``delivery_status`` is NULL (never attempted) or ``'error'``
    (a prior attempt failed and should be retried). Newest first. The parsed
    ``event`` dict is included so the caller can build the outbound payload.
    """
    await init_watchers_db()
    if limit < 1:
        limit = 1
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        out: list[dict[str, Any]] = []
        async for row in await db.execute(
            "SELECT * FROM watcher_events "
            "WHERE watcher_id = ? AND (delivery_status IS NULL OR delivery_status = 'error') "
            "ORDER BY id DESC LIMIT ?",
            (watcher_id, limit),
        ):
            out.append(_decode_event_row(row))
    return out


async def update_delivery_status(
    event_id: int,
    status: str,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record the outcome of a delivery attempt for one event row.

    ``status`` is ``'ok'`` or ``'error'``. ``delivered_at`` is stamped on every
    attempt; ``delivery_error`` holds the short failure text and
    ``delivery_detail`` a JSON blob with the full response (status / headers /
    body / url) for UI inspection. Both are cleared on success.
    """
    await init_watchers_db()
    if status == "error":
        error_text = error
        detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    else:
        error_text = None
        detail_json = None
    async with aiosqlite.connect(_WATCHERS_DB_PATH) as db:
        await db.execute(
            "UPDATE watcher_events SET delivery_status = ?, delivery_error = ?, "
            "delivery_detail = ?, delivered_at = ? WHERE id = ?",
            (status, error_text, detail_json, _now(), int(event_id)),
        )
        await db.commit()


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


def list_watchers_cleanup_sync() -> list[dict[str, Any]]:
    """Return all watchers as ``{id, cleanup_interval_sec}`` for the periodic
    feed-retention scheduler (issue_local_008).

    Synchronous (sqlite3) so the APScheduler ``reload()`` — which runs outside an
    event loop — can build one cleanup job per watcher. Cleanup runs for every
    watcher regardless of enabled state, so a disabled watcher's stored feed is
    still trimmed. Returns an empty list if the DB file does not exist yet.
    """
    if not _WATCHERS_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(_WATCHERS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, cleanup_interval_sec FROM watchers"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return []
    return [
        {"id": r["id"], "cleanup_interval_sec": int(r["cleanup_interval_sec"])}
        for r in rows
    ]
