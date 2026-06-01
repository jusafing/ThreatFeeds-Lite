"""
Smart-mode proposals storage (prompts-021E-1).

SQLite-backed registry of LLM-generated mapping proposals at
``data/proposals.db``. Each row carries:
  * source_name           — feed source the proposal targets
  * provider_name, model  — LLM provider + model used
  * sample_size           — number of raw rows sent
  * raw_fields_json       — list of raw field names presented to the LLM
  * mapping_json          — validated {raw_field: canonical} dict
  * prompt_system, prompt_user — full prompt content (audit)
  * llm_response_raw      — verbatim LLM output (audit)
  * status                — pending | approved | rejected | error
  * created_at, decided_at, decided_by_note
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
_PROPOSALS_DB_PATH = _PROJECT_ROOT / "data" / "proposals.db"

_PROPOSALS_SCHEMA_VERSION = 6

_VALID_STATUSES = frozenset({"pending", "approved", "rejected", "error"})
_VALID_TRIGGER_REASONS = frozenset({"manual", "schedule", "on_new_feed"})
# prompts-032: consolidated proposals span multiple feeds. field_scope records
# whether the raw fields presented to the LLM were the full discovered set or
# only the operator-configured fields (feed-fields.yaml).
_VALID_FIELD_SCOPES = frozenset({"all", "configured"})
# prompts-032: sentinel source_name for consolidated (multi-feed) proposals.
# Legacy single-source rows keep their real source name; the real feed list
# for consolidated rows lives in sources_json.
CONSOLIDATED_SENTINEL = "__consolidated__"
# prompts-021E-4: outcome enum for proposal audit. Distinct from `status`
# (which remains the operator-facing pending/approved/rejected/error label).
_VALID_OUTCOMES = frozenset({
    "pending_review",
    "auto_applied",
    "discarded_below_threshold",
    "approved",
    "rejected",
    "error",
})


CREATE_PROPOSALS_TABLE = """
CREATE TABLE IF NOT EXISTS proposals (
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
    -- prompts-021E-3: trigger provenance for activity-log audit.
    trigger_reason    TEXT    NOT NULL DEFAULT 'manual',
    -- prompts-021E-4: scoring columns. Structurally added here so the
    -- v1→v2 migration is single-shot; populated by 021E-4.
    score             REAL,
    score_breakdown   TEXT,
    outcome           TEXT,
    auto_applied      INTEGER NOT NULL DEFAULT 0,
    -- prompts-021G follow-up: link approved/auto-applied proposals to the
    -- mapping_version row they produced so the Activity tab can deep-link.
    -- NULL for pending/rejected/discarded rows.
    mapping_version_id INTEGER,
    -- prompts-032 (schema v4): consolidated multi-feed proposals.
    --   sources_json            — list of feed names spanned by the proposal
    --   field_scope             — 'all' | 'configured' (raw fields presented)
    --   consolidated_version_id — consolidated_versions row produced on approve
    sources_json            TEXT    NOT NULL DEFAULT '[]',
    field_scope             TEXT,
    consolidated_version_id INTEGER,
    -- prompts-034 (schema v5): proposal lifecycle.
    --   proposal_name — stable human-facing label "Proposal-<UTC timestamp>"
    --   archived      — 1 once the operator archives the row; archived rows
    --                   are hidden from the default review/list views.
    proposal_name           TEXT,
    archived                INTEGER NOT NULL DEFAULT 0,
    -- prompts-037 (schema v6): raw LLM HTTP exchange for diagnosis.
    --   llm_request_raw   — full HTTP request (method/url/redacted headers/body)
    --   llm_response_json — WHOLE HTTP response envelope (not just message
    --                       .content); empty for transport failures.
    llm_request_raw         TEXT    NOT NULL DEFAULT '',
    llm_response_json       TEXT    NOT NULL DEFAULT ''
);
"""

CREATE_PROPOSALS_IDX_SOURCE = """
CREATE INDEX IF NOT EXISTS idx_proposals_source ON proposals (source_name);
"""

CREATE_PROPOSALS_IDX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals (status);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


async def _migrate_proposals_schema(db: aiosqlite.Connection) -> None:
    """Idempotently add missing columns to the proposals table.

    Originally v1→v2 (021E-3 / 021E-4); extended to v3 (021G follow-up) to
    add ``mapping_version_id``; extended to v4 (prompts-032) to add the
    consolidated-proposal columns. Uses ``PRAGMA table_info`` to add only
    missing columns, so this is safe to run against any prior schema
    (fresh v4 DB → no-op; v1 DB → adds every later column; v3 DB → adds only
    the v4 columns).
    """
    cur = await db.execute("PRAGMA table_info(proposals)")
    rows = await cur.fetchall()
    await cur.close()
    existing = {r[1] for r in rows}  # r[1] is column name

    additions: list[tuple[str, str]] = [
        ("trigger_reason", "TEXT NOT NULL DEFAULT 'manual'"),
        ("score", "REAL"),
        ("score_breakdown", "TEXT"),
        ("outcome", "TEXT"),
        ("auto_applied", "INTEGER NOT NULL DEFAULT 0"),
        # prompts-021G follow-up (schema v3): link to mapping_versions.id.
        ("mapping_version_id", "INTEGER"),
        # prompts-032 (schema v4): consolidated multi-feed proposal columns.
        ("sources_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("field_scope", "TEXT"),
        ("consolidated_version_id", "INTEGER"),
        # prompts-034 (schema v5): proposal lifecycle (name + archive).
        ("proposal_name", "TEXT"),
        ("archived", "INTEGER NOT NULL DEFAULT 0"),
        # prompts-037 (schema v6): raw LLM HTTP exchange capture.
        ("llm_request_raw", "TEXT NOT NULL DEFAULT ''"),
        ("llm_response_json", "TEXT NOT NULL DEFAULT ''"),
    ]
    for col, decl in additions:
        if col not in existing:
            await db.execute(f"ALTER TABLE proposals ADD COLUMN {col} {decl}")
            logger.info("proposals.db migrated: added column %s", col)

    # prompts-021E-4: backfill outcome for legacy rows so the new
    # outcome-filter API returns sensible results. Idempotent: only touches
    # NULL outcomes (new rows are explicitly populated by insert_proposal).
    await db.execute(
        "UPDATE proposals SET outcome = 'pending_review' WHERE outcome IS NULL"
    )

    # prompts-034: backfill a stable name for legacy rows so every proposal
    # has a human-facing label. Idempotent: only touches NULL names (new rows
    # are populated explicitly by insert_proposal).
    await db.execute(
        "UPDATE proposals SET proposal_name = 'Proposal-' || created_at "
        "WHERE proposal_name IS NULL"
    )


async def init_proposals_db() -> None:
    _PROPOSALS_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        await db.execute(CREATE_PROPOSALS_TABLE)
        await db.execute(CREATE_PROPOSALS_IDX_SOURCE)
        await db.execute(CREATE_PROPOSALS_IDX_STATUS)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        # Idempotent migration to current schema version (021E-3 → 021G).
        await _migrate_proposals_schema(db)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_PROPOSALS_SCHEMA_VERSION,),
            )
        elif int(row[0]) < _PROPOSALS_SCHEMA_VERSION:
            await db.execute(
                "UPDATE schema_version SET version = ?",
                (_PROPOSALS_SCHEMA_VERSION,),
            )
        await db.commit()


async def insert_proposal(
    *,
    source_name: str,
    provider_name: str | None,
    model: str | None,
    sample_size: int,
    raw_fields: list[str],
    mapping: dict[str, str],
    prompt_system: str,
    prompt_user: str,
    llm_response_raw: str,
    status: str = "pending",
    trigger_reason: str = "manual",
    # prompts-021E-4: scoring + outcome. Defaults preserve the 021E-3
    # behaviour ("pending_review" outcome, no score populated yet) so
    # existing callers (e.g. the routes_smart manual flow) work unchanged.
    score: float | None = None,
    score_breakdown: dict[str, Any] | None = None,
    outcome: str = "pending_review",
    auto_applied: bool = False,
    # prompts-032: consolidated multi-feed proposals. Defaults preserve the
    # legacy single-source behaviour (empty sources list, no field_scope).
    sources: list[str] | None = None,
    field_scope: str | None = None,
    consolidated_version_id: int | None = None,
    # prompts-037: raw LLM HTTP exchange for the error/detail card. Default ''
    # preserves every existing caller. ``llm_response_json`` is the WHOLE HTTP
    # response envelope (not just the extracted ``llm_response_raw`` content).
    llm_request_raw: str = "",
    llm_response_json: str = "",
) -> int:
    """Insert a new proposal row and return its id."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    if trigger_reason not in _VALID_TRIGGER_REASONS:
        raise ValueError(f"invalid trigger_reason: {trigger_reason!r}")
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    if field_scope is not None and field_scope not in _VALID_FIELD_SCOPES:
        raise ValueError(f"invalid field_scope: {field_scope!r}")
    await init_proposals_db()
    created_at = datetime.now(timezone.utc).isoformat()
    # prompts-034: stable, unique human-facing label derived from the creation
    # timestamp (ISO-8601, microsecond precision → unique per insert).
    proposal_name = f"Proposal-{created_at}"
    breakdown_json = (
        json.dumps(score_breakdown, ensure_ascii=False)
        if score_breakdown is not None
        else None
    )
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO proposals (
                source_name, provider_name, model, sample_size,
                raw_fields_json, mapping_json, prompt_system, prompt_user,
                llm_response_raw, status, created_at, trigger_reason,
                score, score_breakdown, outcome, auto_applied,
                sources_json, field_scope, consolidated_version_id,
                proposal_name, llm_request_raw, llm_response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                provider_name,
                model,
                int(sample_size),
                json.dumps(raw_fields, ensure_ascii=False),
                json.dumps(mapping, ensure_ascii=False),
                prompt_system,
                prompt_user,
                llm_response_raw,
                status,
                created_at,
                trigger_reason,
                score,
                breakdown_json,
                outcome,
                1 if auto_applied else 0,
                json.dumps(sources or [], ensure_ascii=False),
                field_scope,
                consolidated_version_id,
                proposal_name,
                llm_request_raw,
                llm_response_json,
            ),
        )
        await db.commit()
        new_id = cur.lastrowid
        await cur.close()
        return int(new_id)


def _row_to_dict(row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
    d = dict(row)
    try:
        d["raw_fields"] = json.loads(d.pop("raw_fields_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["raw_fields"] = []
    try:
        d["mapping"] = json.loads(d.pop("mapping_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["mapping"] = {}
    # prompts-032: parse consolidated sources list.
    try:
        d["sources"] = json.loads(d.pop("sources_json", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["sources"] = []
    # prompts-021E-4: parse score_breakdown JSON if present.
    raw_breakdown = d.get("score_breakdown")
    if raw_breakdown:
        try:
            d["score_breakdown"] = json.loads(raw_breakdown)
        except (json.JSONDecodeError, TypeError):
            d["score_breakdown"] = None
    # Coerce auto_applied INTEGER → bool for JSON response shape.
    if "auto_applied" in d:
        d["auto_applied"] = bool(d["auto_applied"])
    # prompts-034: coerce archived INTEGER → bool.
    if "archived" in d:
        d["archived"] = bool(d["archived"])
    return d


async def get_proposal(proposal_id: int) -> dict[str, Any] | None:
    await init_proposals_db()
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    if row is None:
        return None
    return _row_to_dict(row)


async def list_proposals(
    source: str | None = None,
    status: str | None = None,
    outcome: str | None = None,
    limit: int = 100,
    archived: bool | None = None,
) -> list[dict[str, Any]]:
    """List proposals with optional filters.

    Filter precedence (prompts-021E-4):
      * If ``outcome`` is provided, it takes precedence and ``status`` is
        only applied if explicitly set as well.
      * If ``outcome`` is None, the legacy ``status`` filter is used.
      * Pass ``outcome='all'`` to bypass the outcome filter entirely (callers
        wanting an unfiltered audit view).

    prompts-034 ``archived`` filter:
      * None  → no archive filter (returns archived + non-archived).
      * False → only non-archived rows (``archived = 0``).
      * True  → only archived rows (``archived = 1``).

    Default behaviour for the HTTP layer to enforce: outcome='pending_review'
    and archived=False so discarded/auto-applied/archived rows are hidden from
    the review queue. This function itself does NOT default-filter — that
    policy lives in the route.
    """
    await init_proposals_db()
    where: list[str] = []
    params: list[Any] = []
    if source:
        where.append("source_name = ?")
        params.append(source)
    if status:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status filter: {status!r}")
        where.append("status = ?")
        params.append(status)
    if outcome and outcome != "all":
        if outcome not in _VALID_OUTCOMES:
            raise ValueError(f"invalid outcome filter: {outcome!r}")
        where.append("outcome = ?")
        params.append(outcome)
    if archived is not None:
        where.append("archived = ?")
        params.append(1 if archived else 0)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = (
        f"SELECT * FROM proposals {where_sql} "
        f"ORDER BY created_at DESC LIMIT ?"
    )
    params.append(int(limit))
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        async for row in await db.execute(sql, params):
            rows.append(_row_to_dict(row))
    return rows


async def update_proposal_status(
    proposal_id: int,
    new_status: str,
    note: str | None = None,
    outcome: str | None = None,
    mapping_version_id: int | None = None,
    consolidated_version_id: int | None = None,
) -> bool:
    """Update a proposal's decision state.

    021E-4: pass ``outcome`` to also update the audit column so the activity
    log distinguishes operator-approved from auto-applied. When omitted the
    outcome column is left untouched.

    021G follow-up: pass ``mapping_version_id`` to record the
    mapping_versions row produced by approve/auto-apply, so the Activity
    tab can deep-link from a proposal to its resulting version. Only
    written when not None; omitted on rejections / errors.
    """
    if new_status not in _VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status!r}")
    if outcome is not None and outcome not in _VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    await init_proposals_db()
    decided_at = datetime.now(timezone.utc).isoformat()
    # Build SET clause dynamically so we don't overwrite outcome /
    # mapping_version_id with NULL when callers don't supply them.
    sets: list[str] = ["status = ?", "decided_at = ?", "decided_by_note = ?"]
    params: list[Any] = [new_status, decided_at, note]
    if outcome is not None:
        sets.append("outcome = ?")
        params.append(outcome)
        # When outcome is 'auto_applied' also flip the boolean flag column
        # so the simple `WHERE auto_applied = 1` audit query works without
        # re-deriving from the outcome enum.
        sets.append("auto_applied = ?")
        params.append(1 if outcome == "auto_applied" else 0)
    if mapping_version_id is not None:
        sets.append("mapping_version_id = ?")
        params.append(int(mapping_version_id))
    if consolidated_version_id is not None:
        sets.append("consolidated_version_id = ?")
        params.append(int(consolidated_version_id))
    sql = f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?"
    params.append(proposal_id)
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        cur = await db.execute(sql, tuple(params))
        await db.commit()
        changed = cur.rowcount > 0
        await cur.close()
    return changed


async def archive_proposal(proposal_id: int, note: str | None = None) -> bool:
    """Archive a proposal (prompts-034): set ``archived = 1``.

    Archived rows are hidden from the default review/list views but remain in
    the DB for audit. Idempotent — archiving an already-archived row simply
    re-sets the flag. Returns True if a row matched. An optional ``note`` is
    appended to ``decided_by_note`` for provenance without clobbering any
    existing decision note.
    """
    await init_proposals_db()
    sets = ["archived = 1"]
    params: list[Any] = []
    if note:
        # Append rather than overwrite: keep any prior approve/reject note.
        sets.append(
            "decided_by_note = "
            "TRIM(COALESCE(decided_by_note, '') || ' ' || ?)"
        )
        params.append(f"[archived] {note}")
    sql = f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?"
    params.append(proposal_id)
    async with aiosqlite.connect(_PROPOSALS_DB_PATH) as db:
        cur = await db.execute(sql, tuple(params))
        await db.commit()
        changed = cur.rowcount > 0
        await cur.close()
    return changed
