"""
DB manager — per-source SQLite file management using aiosqlite.
Each source gets its own file: data/<source_name>.db
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from .schema import (
    ALTER_ADD_DEDUP_KEY,
    ALTER_ADD_NORMALIZED,
    CREATE_DEDUP_INDEX,
    CREATE_DEDUP_KEY_INDEX,
    CREATE_ENTRIES_TABLE,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _PROJECT_ROOT / "data"

# DB stems that are NOT feed sources (system/internal DBs that live in data/).
# These are excluded from source enumeration so the Viewer does not try to
# query the `entries` table on them.
_SYSTEM_DB_STEMS: set[str] = {"normalized", "meta", "proposals", "users"}

InsertResult = Literal["inserted", "duplicate", "error"]

# Known core columns of the per-source ``entries`` table. Used both to split a
# pushed entry into typed columns vs the ``extra`` JSON blob AND, in
# query_entries, as the strict whitelist for arbitrary ``field`` filters — the
# filter column name is interpolated into SQL, so anything not in this set MUST
# be rejected to prevent SQL-identifier injection. ``id``/``dedup_key`` are
# real columns too and safe to filter on.
CORE_COLUMNS: frozenset[str] = frozenset({
    "indicator", "indicator_type", "threat_type", "severity", "confidence",
    "source", "source_url", "title", "description", "tags", "tlp",
    "published_at", "first_seen", "last_seen", "ingested_at",
    "cve_id", "cvss_score", "cvss_vector", "affected_product", "affected_vendor",
    "patch_available", "mitre_attack_id", "malware_family", "campaign", "actor",
    "country", "autonomous_system", "port", "protocol", "geo_lat", "geo_lon",
    "ingest_mode", "raw",
})

# Columns that are valid filter targets but are NOT packed from a pushed entry
# (system-managed). Kept separate from CORE_COLUMNS so add_entry's split logic
# is unchanged while query_entries can still filter on them.
_FILTERABLE_EXTRA_COLUMNS: frozenset[str] = frozenset({"id", "dedup_key"})

# Full whitelist of columns an arbitrary ``field`` filter may target.
FILTERABLE_COLUMNS: frozenset[str] = CORE_COLUMNS | _FILTERABLE_EXTRA_COLUMNS



def _db_path(source_name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{source_name}.db"


# Volatile / housekeeping fields that must NOT participate in the dedup hash:
# they change between ingests of the same logical record (ingested_at) or are
# meta/internal (id, dedup_key, normalized) or are redundant with the explicit
# `source` argument we mix in separately.
_DEDUP_VOLATILE_KEYS: frozenset[str] = frozenset({
    "ingested_at", "ingest_mode", "source", "dedup_key", "id", "normalized",
})


def _compute_dedup_key(source: str, entry: dict[str, Any]) -> str:
    """Stable SHA256 over the full content of the entry (excluding volatile keys).

    Strategy: serialise every non-volatile, non-empty field in the entry as
    sorted-key JSON and hash that, prefixed with the source name. This makes
    dedup work for ANY feed shape — including flattened JSON (`cve.id`,
    `cve.metrics.score`) and arbitrary CSV headers (`c2_ip`, `malware`) — not
    just rows that happen to populate the canonical `indicator/title/...`
    columns.

    Two rows with identical content but different `ingested_at` collapse to
    the same key (idempotent re-ingest). Two rows that differ in any non-
    volatile field produce different keys.
    """
    stable: dict[str, Any] = {}
    for key in sorted(entry.keys()):
        if key in _DEDUP_VOLATILE_KEYS:
            continue
        value = entry[key]
        if value is None or value == "":
            continue
        stable[key] = value
    payload = json.dumps(stable, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(f"{source or ''}\x1f{payload}".encode("utf-8")).hexdigest()


async def init_db(source_name: str) -> None:
    """Create the DB file and tables if they don't exist. Run migrations."""
    async with aiosqlite.connect(_db_path(source_name)) as db:
        await db.execute(CREATE_ENTRIES_TABLE)
        await db.execute(CREATE_DEDUP_INDEX)
        # Migration: add `normalized` column to existing DBs (ignore if already present)
        try:
            await db.execute(ALTER_ADD_NORMALIZED)
        except Exception:
            pass
        # Migration: add `dedup_key` column to existing DBs (ignore if already present)
        try:
            await db.execute(ALTER_ADD_DEDUP_KEY)
        except Exception:
            pass
        await db.commit()

        # One-shot backfill: compute dedup_key for any rows that are missing it.
        # We must read the rows to compute the SHA256 since the input fields are
        # source/indicator/published_at/source_url/title.
        try:
            cursor = await db.execute(
                "SELECT id, source, indicator, published_at, source_url, title "
                "FROM entries WHERE dedup_key IS NULL"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            if rows:
                for rid, src, indicator, published_at, source_url, title in rows:
                    key = _compute_dedup_key(
                        src or source_name,
                        {
                            "indicator": indicator,
                            "published_at": published_at,
                            "source_url": source_url,
                            "title": title,
                        },
                    )
                    # Use INSERT OR IGNORE semantics: if a duplicate key already
                    # exists, leave this row's dedup_key NULL (it will be a
                    # logical duplicate but won't crash the migration).
                    try:
                        await db.execute(
                            "UPDATE entries SET dedup_key = ? WHERE id = ?",
                            (key, rid),
                        )
                    except sqlite3.IntegrityError:
                        # Another row already has this dedup_key — skip.
                        pass
                await db.commit()
        except sqlite3.OperationalError:
            # Table missing or partially initialised — ignore.
            pass

        # Create the unique index AFTER backfill so existing duplicates do not
        # block index creation.
        try:
            await db.execute(CREATE_DEDUP_KEY_INDEX)
            await db.commit()
        except sqlite3.IntegrityError:
            logger.warning(
                "Could not create unique dedup_key index on %s: pre-existing duplicates remain",
                source_name,
            )


async def insert_entry(source_name: str, entry: dict[str, Any]) -> InsertResult:
    """
    Insert a normalised entry into the source DB.

    Returns:
        "inserted"  — new row written
        "duplicate" — matched an existing dedup_key (no insert)
        "error"     — DB error or malformed entry (no insert)
    """
    await init_db(source_name)

    row: dict[str, Any] = {k: v for k, v in entry.items() if k in CORE_COLUMNS}
    row["source"] = source_name
    row["ingested_at"] = row.get("ingested_at") or datetime.now(timezone.utc).isoformat()

    # Coerce non-scalar core-column values (e.g. a pushed ``tags: [...]`` list or
    # a nested dict) to a JSON string. sqlite3 cannot bind list/dict parameters,
    # and without this the INSERT raises and the whole entry is silently
    # discarded. Feed parsers already stringify such fields (e.g. rss_pull joins
    # tags), but generic pushed JSON does not — so guard at the storage boundary.
    for key, value in row.items():
        if isinstance(value, (list, dict)):
            row[key] = json.dumps(value, ensure_ascii=False, default=str)

    # Pack unknown keys into extra JSON blob
    extra = {k: v for k, v in entry.items() if k not in CORE_COLUMNS and k != "extra"}
    row["extra"] = json.dumps(extra)

    # Dedup hash is computed over the FULL entry (core + extras), not just the
    # core-columns subset, so that rows whose identifying data lives in the
    # extras (e.g. `c2_ip`, `cve.id`) are not all collapsed to one identical
    # hash. See `_compute_dedup_key` for the canonicalisation rules.
    dedup_input: dict[str, Any] = {k: v for k, v in entry.items() if k != "extra"}
    dedup_input["source"] = source_name
    row["dedup_key"] = _compute_dedup_key(source_name, dedup_input)

    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    values = list(row.values())

    try:
        async with aiosqlite.connect(_db_path(source_name)) as db:
            cursor = await db.execute(
                f"INSERT OR IGNORE INTO entries ({cols}) VALUES ({placeholders})",
                values,
            )
            await db.commit()
            changed = cursor.rowcount > 0
            await cursor.close()
            return "inserted" if changed else "duplicate"
    except sqlite3.IntegrityError:
        return "duplicate"
    except sqlite3.OperationalError as exc:
        logger.warning("DB error inserting into %s: %s", source_name, exc)
        return "error"
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Unexpected error inserting into %s: %s", source_name, exc)
        return "error"


async def query_entries(
    source_name: str | None = None,
    limit: int = 500,
    offset: int = 0,
    search: str | None = None,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Query entries across all source DBs (or a single one).
    Returns a list of row dicts with extra fields merged in.
    """
    sources = _get_all_sources() if source_name is None else [source_name]
    results: list[dict[str, Any]] = []

    for src in sources:
        path = _db_path(src)
        if not path.exists():
            continue
        try:
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                where_clauses: list[str] = []
                params: list[Any] = []

                if filters:
                    for col, val in filters.items():
                        # SECURITY: ``col`` is interpolated into the SQL text,
                        # so only known table columns may be filtered. Unknown
                        # keys (e.g. extra-JSON fields, or an injection attempt)
                        # are silently dropped rather than executed.
                        if col not in FILTERABLE_COLUMNS:
                            continue
                        where_clauses.append(f"{col} = ?")
                        params.append(val)
                if search:
                    search_cols = ["indicator", "title", "description", "tags", "actor", "campaign"]
                    like_clauses = " OR ".join(f"{c} LIKE ?" for c in search_cols)
                    where_clauses.append(f"({like_clauses})")
                    params.extend([f"%{search}%"] * len(search_cols))

                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                sql = f"SELECT * FROM entries {where_sql} ORDER BY ingested_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                async for row in await db.execute(sql, params):
                    d = dict(row)
                    extra = json.loads(d.pop("extra", "{}") or "{}")
                    d.update(extra)
                    results.append(d)
        except sqlite3.OperationalError as exc:
            logger.warning("Skipping source %s in query_entries: %s", src, exc)
            continue

    results.sort(key=lambda r: r.get("ingested_at", ""), reverse=True)
    return results[:limit]


async def get_summary() -> list[dict[str, Any]]:
    """Return entry count per source plus overall total.

    Each row also includes the latest ingest stats from data/meta.db
    (last_ingested_at + 4 counters + state) when available, so the
    frontend Summary table can render relative-time and delta columns.
    """
    # Lazy import to avoid a circular dependency at module load time.
    from backend.db.meta import get_meta

    sources = _get_all_sources()
    summary: list[dict[str, Any]] = []
    total = 0

    meta_by_source = await get_meta(sources)

    for src in sources:
        path = _db_path(src)
        if not path.exists():
            continue
        try:
            async with aiosqlite.connect(path) as db:
                row = await db.execute_fetchall("SELECT COUNT(*) FROM entries")
                count = row[0][0] if row else 0
                entry: dict[str, Any] = {"source": src, "count": count}
                m = meta_by_source.get(src)
                if m:
                    entry["last_ingested_at"] = m.get("last_ingested_at")
                    entry["last_total_read"]  = m.get("last_total_read")
                    entry["last_inserted"]    = m.get("last_inserted")
                    entry["last_duplicates"]  = m.get("last_duplicates")
                    entry["last_discarded"]   = m.get("last_discarded")
                    entry["last_job_state"]   = m.get("last_job_state")
                summary.append(entry)
                total += count
        except sqlite3.OperationalError as exc:
            logger.warning("Skipping source %s in get_summary: %s", src, exc)
            continue

    summary.append({"source": "__total__", "count": total})
    return summary


async def get_entry_count_for_source(source_name: str) -> int:
    """Return the entry count for a single source. 0 if the DB does not exist."""
    path = _db_path(source_name)
    if not path.exists():
        return 0
    try:
        async with aiosqlite.connect(path) as db:
            row = await db.execute_fetchall("SELECT COUNT(*) FROM entries")
            return row[0][0] if row else 0
    except sqlite3.OperationalError:
        return 0


def reset_db(source_name: str | None = None) -> list[str]:
    """
    Delete DB file(s). Pass None to reset all.
    Returns list of deleted file names.

    prompts-045: the auth store (``users.db``) is never deleted by a full
    reset so administrators do not lose their accounts when wiping feed data.
    """
    deleted: list[str] = []
    if source_name:
        targets = [_db_path(source_name)]
    else:
        targets = [
            p for p in DATA_DIR.glob("*.db") if p.stem != "users"
        ]
    for path in targets:
        if path.exists():
            path.unlink()
            deleted.append(path.name)
    return deleted


def _get_all_sources() -> list[str]:
    """Return all source names based on existing DB files (system DBs excluded)."""
    if not DATA_DIR.exists():
        return []
    return [
        p.stem for p in sorted(DATA_DIR.glob("*.db"))
        if p.stem not in _SYSTEM_DB_STEMS
    ]


async def reset_normalized_flag_for_all_sources() -> int:
    """Set normalized=0 across every source DB.

    Called once after a ``normalized.db`` schema bump (prompts-021E-pre) so
    the next normalizer run rebuilds the derived table from raw entries.
    Raw entry data is untouched; only the housekeeping flag is reset.

    Returns the total number of rows updated across all source DBs.
    """
    total = 0
    for src in _get_all_sources():
        path = _db_path(src)
        if not path.exists():
            continue
        try:
            async with aiosqlite.connect(path) as db:
                cursor = await db.execute(
                    "UPDATE entries SET normalized=0 WHERE normalized=1"
                )
                await db.commit()
                total += cursor.rowcount or 0
                await cursor.close()
        except sqlite3.OperationalError as exc:
            logger.warning(
                "reset_normalized_flag: skipping source %s: %s", src, exc,
            )
            continue
    return total


async def mark_normalized(source_name: str, entry_ids: list[int]) -> int:
    """
    Set normalized=1 for the given entry IDs in a source DB.
    Returns the number of rows updated.
    """
    if not entry_ids:
        return 0
    path = _db_path(source_name)
    if not path.exists():
        return 0
    placeholders = ",".join("?" for _ in entry_ids)
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            f"UPDATE entries SET normalized=1 WHERE id IN ({placeholders})",
            entry_ids,
        )
        await db.commit()
        return cursor.rowcount


async def reset_normalized_flag_for_source(source_name: str) -> int:
    """Set normalized=0 for every entry in one source DB.

    Used by prompts-021F mapping-version activation: when the operator (or
    auto-apply) switches to a new mapping_version, all of that source's
    rows must be re-normalized through the new mapping. Raw entry data
    is untouched; only the housekeeping flag is reset.

    Returns the number of rows updated, or 0 if the source DB is missing.
    """
    path = _db_path(source_name)
    if not path.exists():
        return 0
    try:
        async with aiosqlite.connect(path) as db:
            cursor = await db.execute(
                "UPDATE entries SET normalized=0 WHERE normalized=1"
            )
            await db.commit()
            count = cursor.rowcount or 0
            await cursor.close()
            return count
    except sqlite3.OperationalError as exc:
        logger.warning(
            "reset_normalized_flag_for_source: skipping %s: %s",
            source_name, exc,
        )
        return 0
