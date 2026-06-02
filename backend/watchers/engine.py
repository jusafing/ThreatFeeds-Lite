"""
Watchers evaluation engine (issue_local_006).

Given a watcher definition, fetch candidate events from the selected dataset(s)
— raw per-source ``entries``, the consolidated ``normalized_entries``, or both
("all") — apply the watcher's match conditions in Python (exact / wildcard /
regex), and record matching events into ``watcher_events`` (deduped).

Why Python-side matching: the SQL query layer only supports exact-equality
column filters + ``LIKE`` substring search. Wildcard and regex matching are not
expressible safely in SQLite, so we pull a bounded candidate window via the
existing ``query_entries`` / ``query_normalized`` helpers and evaluate matches
in process. The window is bounded by the global ``watcher_max_events`` setting,
and a per-watcher high-water mark skips already-evaluated rows so re-evaluation
is cheap and never re-triggers a pruned event.

Triggering is invoked from three places (see callers):
  * ingestion job completion  → raw dataset       (trigger="ingest")
  * normalizer run completion → normalized dataset (trigger="normalize")
  * APScheduler interval      → both, per watcher  (trigger="schedule")
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from typing import Any, Iterable

from backend.config.loader import load_watcher_max_events
from backend.db import watchers as store
from backend.db.manager import query_entries
from backend.normalizer.db import query_normalized

logger = logging.getLogger("backend.watchers")

# A field token meaning "match against any field value in the row".
_ANY_FIELD_TOKENS = frozenset({"", "*", "all", "any"})

# Hard ceiling on a regex/value length we will attempt to match, as a cheap
# guard against pathological patterns.
_MAX_MATCH_LEN = 4000


def _row_source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("source_name") or "")


def _field_token(field: str) -> str:
    return (field or "").strip().lower()


def _condition_value_matches(target: str, value: str, match_type: str) -> bool:
    """Return whether a single string ``target`` satisfies one condition."""
    if len(target) > _MAX_MATCH_LEN:
        target = target[:_MAX_MATCH_LEN]
    if match_type == "exact":
        return target.casefold() == value.casefold()
    if match_type == "wildcard":
        return fnmatch.fnmatch(target.casefold(), value.casefold())
    if match_type == "regex":
        try:
            return re.search(value, target) is not None
        except re.error as exc:  # pragma: no cover — patterns pre-validated
            logger.warning("invalid regex %r skipped: %s", value, exc)
            return False
    if match_type in ("gte", "lte"):
        # Numeric comparison: both sides must parse as numbers, else no match.
        try:
            tgt = float(target.strip())
            ref = float(value.strip())
        except (TypeError, ValueError):
            return False
        return tgt >= ref if match_type == "gte" else tgt <= ref
    return False


def _row_matches_condition(row: dict[str, Any], cond: dict[str, str]) -> bool:
    """Return whether ``row`` satisfies one field condition."""
    field = _field_token(cond.get("field", ""))
    value = cond.get("value", "")
    match_type = cond.get("match_type", "exact")
    if field in _ANY_FIELD_TOKENS:
        # Match if ANY field value in the row satisfies the condition.
        for v in row.values():
            if v is None:
                continue
            if _condition_value_matches(str(v), value, match_type):
                return True
        return False
    raw = row.get(field)
    if raw is None:
        return False
    return _condition_value_matches(str(raw), value, match_type)


def row_matches(row: dict[str, Any], watcher: dict[str, Any]) -> bool:
    """Return whether ``row`` matches a watcher's feeds and the AND of its field
    conditions.

    The watcher's ``severity`` is a classification label only and is NOT used to
    gate matching (issue_local_006 review_02): solely the user-configured
    conditions (plus the feed scope) decide a trigger. To match on severity,
    add an explicit condition with ``field="severity"``.
    """
    # Feed gate: empty feeds == all feeds.
    feeds = watcher.get("feeds") or []
    if feeds and _row_source(row) not in set(feeds):
        return False
    # Field conditions (AND). At least one is always present (validated on save).
    for cond in watcher.get("conditions") or []:
        if not _row_matches_condition(row, cond):
            return False
    return True


async def _candidate_rows(dataset: str, feeds: list[str], window: int) -> list[dict[str, Any]]:
    """Fetch a bounded candidate window for a dataset.

    When the watcher targets a single feed we push that down to the query layer;
    otherwise we pull across all sources and gate by feed in Python.
    """
    single = feeds[0] if len(feeds) == 1 else None
    if dataset == "raw":
        return await query_entries(source_name=single, limit=window)
    return await query_normalized(source_name=single, limit=window)


def _new_rows(rows: Iterable[dict[str, Any]], high_water: int) -> tuple[list[dict[str, Any]], int]:
    """Filter to rows with id > high_water; return (rows, new_high_water)."""
    fresh: list[dict[str, Any]] = []
    max_id = high_water
    for row in rows:
        try:
            rid = int(row.get("id", 0))
        except (TypeError, ValueError):
            continue
        if rid > high_water:
            fresh.append(row)
        if rid > max_id:
            max_id = rid
    return fresh, max_id


async def evaluate_watcher(watcher: dict[str, Any], datasets: set[str]) -> int:
    """Evaluate one watcher against the given datasets. Returns new trigger count.

    ``datasets`` is the set of dataset namespaces to scan now, e.g. {"raw"},
    {"normalized"}, or {"raw","normalized"}. A watcher with dataset="all" scans
    whichever of these its trigger covers; a watcher with dataset="raw" only
    scans "raw" even if "normalized" is requested.
    """
    if not watcher.get("enabled"):
        return 0
    wid = watcher["id"]
    wanted = {"raw", "normalized"} if watcher.get("dataset") == "all" else {watcher.get("dataset")}
    scan = wanted & datasets
    if not scan:
        return 0

    window = max(int(load_watcher_max_events()), int(watcher.get("max_feed_events", 10)))
    max_events = int(load_watcher_max_events())
    feeds = list(watcher.get("feeds") or [])

    triggers: list[dict[str, Any]] = []
    new_raw_id: int | None = None
    new_norm_id: int | None = None

    for ds in scan:
        hw_key = "last_eval_raw_id" if ds == "raw" else "last_eval_norm_id"
        high_water = int(watcher.get(hw_key, 0) or 0)
        try:
            rows = await _candidate_rows(ds, feeds, window)
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("watcher %s: candidate fetch failed for %s: %s", wid, ds, exc)
            continue
        fresh, max_id = _new_rows(rows, high_water)
        for row in fresh:
            if row_matches(row, watcher):
                triggers.append({
                    "dataset": ds,
                    "source_entry_id": int(row.get("id", 0)),
                    "source_name": _row_source(row),
                    "event": row,
                })
        if ds == "raw":
            new_raw_id = max_id
        else:
            new_norm_id = max_id

    inserted = 0
    if triggers:
        inserted = await store.record_triggers(wid, triggers, max_events=max_events)
        if inserted:
            logger.info("watcher %s triggered on %d new event(s)", wid, inserted)
    await store.update_high_water(wid, raw_id=new_raw_id, norm_id=new_norm_id)
    return inserted


async def evaluate_watcher_by_id(watcher_id: str) -> int:
    """Load a watcher by id and evaluate it against both datasets.

    Used by the APScheduler per-watcher interval job (scheduled mode). Returns
    the number of new triggers, or 0 if the watcher is missing/disabled.
    """
    watcher = await store.get_watcher(watcher_id)
    if watcher is None or not watcher.get("enabled"):
        return 0
    return await evaluate_watcher(watcher, {"raw", "normalized"})


async def run_watchers(trigger: str, datasets: set[str] | None = None) -> dict[str, Any]:
    """Evaluate all enabled watchers relevant to ``trigger``.

    ``datasets`` restricts which dataset namespaces are scanned this pass:
      * ingestion completion  → {"raw"}
      * normalizer completion → {"normalized"}
      * scheduled tick        → {"raw","normalized"} (default)

    Best-effort: a failure in one watcher never aborts the others.
    """
    scan = datasets or {"raw", "normalized"}
    total = 0
    evaluated = 0
    try:
        enabled = await store.list_enabled_watchers()
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("run_watchers(%s): could not list watchers: %s", trigger, exc)
        return {"evaluated": 0, "triggered": 0}
    for watcher in enabled:
        # For a scheduled tick, only evaluate watchers in scheduled mode; the
        # realtime watchers are driven by the ingest/normalize completion hooks.
        if trigger == "schedule" and watcher.get("mode") != "scheduled":
            continue
        if trigger in ("ingest", "normalize") and watcher.get("mode") != "realtime":
            continue
        try:
            total += await evaluate_watcher(watcher, scan)
            evaluated += 1
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("watcher %s evaluation failed: %s", watcher.get("id"), exc)
    return {"evaluated": evaluated, "triggered": total}


def schedule_realtime_ingest_eval(inserted: int) -> None:
    """Schedule a realtime watcher pass over the raw dataset after an ingest.

    This is the shared hook used by *all* ingest paths — both the synchronous
    push/upload routes and the background job runner — so that watchers fire on
    any new raw events regardless of how they were indexed (issue_local_006
    review_02: the synchronous API-client push path never reached the job
    completion hook and so never triggered watchers).

    Non-blocking and best-effort: a no-op when nothing was inserted or when not
    running inside an event loop (e.g. synchronous unit tests). The evaluation
    runs as a background task so the calling request is not delayed.
    """
    try:
        if int(inserted or 0) <= 0:
            return
    except (TypeError, ValueError):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(run_watchers("ingest", {"raw"}))

