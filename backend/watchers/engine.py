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
from backend.watchers import delivery

logger = logging.getLogger("backend.watchers")

# A field token meaning "match against any field value in the row".
_ANY_FIELD_TOKENS = frozenset({"", "*", "all", "any"})

# Internal/serialization columns never matched by an "any field" condition.
# These are not real data fields — ``raw``/``normalized`` hold the entire
# serialized event, so matching them would make any-field match almost anything.
# Mirrors ``routes_watchers._FIELD_HIDDEN`` so the wizard's offered fields and
# the engine's any-field scan agree.
_HIDDEN_MATCH_KEYS = frozenset(
    {"id", "dedup_key", "normalized", "extra", "extra_norm", "raw"}
)

# Hard ceiling on a regex/value length we will attempt to match, as a cheap
# guard against pathological patterns.
_MAX_MATCH_LEN = 4000


def _row_source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("source_name") or "")


def _field_token(field: str) -> str:
    return (field or "").strip().lower()


def _condition_value_matches(
    target: str, value: str, match_type: str, case_sensitive: bool = False
) -> bool:
    """Return whether a single string ``target`` satisfies one condition.

    ``case_sensitive`` only affects the string match types (exact / wildcard /
    contains). For ``regex`` the caller controls case via inline flags, and the
    numeric comparisons are case-agnostic, so both ignore the flag.
    """
    if len(target) > _MAX_MATCH_LEN:
        target = target[:_MAX_MATCH_LEN]
    if match_type == "exact":
        if case_sensitive:
            return target == value
        return target.casefold() == value.casefold()
    if match_type == "contains":
        if case_sensitive:
            return value in target
        return value.casefold() in target.casefold()
    if match_type == "wildcard":
        if case_sensitive:
            return fnmatch.fnmatchcase(target, value)
        return fnmatch.fnmatchcase(target.casefold(), value.casefold())
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


def _row_matches_condition(row: dict[str, Any], cond: dict[str, Any]) -> bool:
    """Return whether ``row`` satisfies one field condition."""
    field = _field_token(cond.get("field", ""))
    value = cond.get("value", "")
    match_type = cond.get("match_type", "exact")
    case_sensitive = bool(cond.get("case_sensitive", False))
    if field in _ANY_FIELD_TOKENS:
        # Match if ANY real data field in the row satisfies the condition.
        # Internal serialization columns are skipped (see _HIDDEN_MATCH_KEYS).
        for key, v in row.items():
            if key in _HIDDEN_MATCH_KEYS or v is None:
                continue
            if _condition_value_matches(str(v), value, match_type, case_sensitive):
                return True
        return False
    raw = row.get(field)
    if raw is None:
        return False
    return _condition_value_matches(str(raw), value, match_type, case_sensitive)


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


def _new_rows_by_source(
    rows: Iterable[dict[str, Any]], hw_map: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Split candidate rows into those newer than their per-source high-water.

    Raw ``entries`` ids are independent per-source sequences, so freshness must
    be judged per source, not against a single global mark. Returns
    ``(fresh_rows, new_marks)`` where ``new_marks`` maps source_name -> the max
    id observed for that source this pass (>= the prior mark).
    """
    fresh: list[dict[str, Any]] = []
    new_marks: dict[str, int] = {}
    for row in rows:
        try:
            rid = int(row.get("id", 0))
        except (TypeError, ValueError):
            continue
        src = _row_source(row)
        hw = int(hw_map.get(src, 0))
        if rid > hw:
            fresh.append(row)
        if rid > int(new_marks.get(src, hw)):
            new_marks[src] = rid
    return fresh, new_marks


async def evaluate_watcher(
    watcher: dict[str, Any], datasets: set[str], *, ignore_enabled: bool = False
) -> int:
    """Evaluate one watcher against the given datasets. Returns new trigger count.

    ``datasets`` is the set of dataset namespaces to scan now, e.g. {"raw"},
    {"normalized"}, or {"raw","normalized"}. A watcher with dataset="all" scans
    whichever of these its trigger covers; a watcher with dataset="raw" only
    scans "raw" even if "normalized" is requested.

    High-water marks are tracked per (watcher, dataset, source) so a feed whose
    ids sit below another feed's high id is never skipped (issue_local_006
    review_02b).

    ``ignore_enabled`` lets a manual trigger evaluate a disabled watcher (so the
    operator can test it); the automatic callers leave it False.
    """
    if not ignore_enabled and not watcher.get("enabled"):
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
    marks_by_ds: dict[str, dict[str, int]] = {}

    for ds in scan:
        hw_map = await store.get_high_water_map(wid, ds)
        try:
            rows = await _candidate_rows(ds, feeds, window)
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("watcher %s: candidate fetch failed for %s: %s", wid, ds, exc)
            continue
        fresh, new_marks = _new_rows_by_source(rows, hw_map)
        for row in fresh:
            if row_matches(row, watcher):
                triggers.append({
                    "dataset": ds,
                    "source_entry_id": int(row.get("id", 0)),
                    "source_name": _row_source(row),
                    "event": row,
                })
        marks_by_ds[ds] = new_marks

    inserted = 0
    if triggers:
        inserted = await store.record_triggers(wid, triggers, max_events=max_events)
        if inserted:
            logger.info("watcher %s triggered on %d new event(s)", wid, inserted)
    for ds, new_marks in marks_by_ds.items():
        await store.update_high_water_map(wid, ds, new_marks)
    # Publish newly-recorded events to a remote target, if configured
    # (issue_local_007). Best-effort and non-blocking — delivery failures are
    # recorded per-event and never abort evaluation.
    if inserted and str(watcher.get("publish_target") or "local") != "local":
        _schedule_delivery(watcher)
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


def _schedule_delivery(watcher: dict[str, Any]) -> None:
    """Run a watcher's remote delivery as a best-effort background task.

    No-op when not inside a running event loop (e.g. synchronous unit tests);
    callers that need to await delivery should call ``delivery.deliver_pending``
    directly.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(delivery.deliver_pending(watcher))


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

