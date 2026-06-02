"""
Watchers management routes (issue_local_006) — admin surface.

CRUD + enable toggle for watcher definitions, a paginated triggered-events
reader for the Summary card / Details view, and small "meta" helpers that feed
the create-wizard dropdowns (available feeds + matchable fields).

All routes are under ``/api/watchers`` and are therefore admin-only when auth is
enabled (the middleware in main.py fails closed for non-admin roles on any path
not in its viewer allowlist). The PUBLIC per-watcher feed lives separately in
``routes_feed.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.db import watchers as store
from backend.db.manager import CORE_COLUMNS, _get_all_sources, query_entries
from backend.models.watcher import WatcherEnabledIn, WatcherIn
from backend.normalizer.db import _allowed_columns, get_normalized_summary
from backend.normalizer.db import query_normalized
from backend.config.loader import load_watcher_max_events
from backend import scheduler as scheduler_mod

router = APIRouter(prefix="/api/watchers", tags=["watchers"])

# How many recent events to sample per feed when deriving the live field list.
_FIELD_SAMPLE_PER_FEED = 200
# System/internal keys never offered as matchable condition fields.
_FIELD_HIDDEN = {"id", "dedup_key", "normalized", "extra", "extra_norm", "raw"}


def _reschedule() -> None:
    """Rebuild scheduler jobs so scheduled-mode watcher changes take effect."""
    try:
        scheduler_mod.reload()
    except Exception:  # pragma: no cover — defensive; never break the request
        pass


@router.get("")
async def list_watchers() -> list[dict[str, Any]]:
    """Return all watcher definitions (newest first)."""
    return await store.list_watchers()


@router.post("", status_code=201)
async def create_watcher(body: WatcherIn) -> dict[str, Any]:
    """Create a new watcher."""
    try:
        watcher = await store.create_watcher(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reschedule()
    return watcher


@router.get("/meta/feeds")
async def meta_feeds() -> dict[str, list[str]]:
    """Return the union of raw source names and normalized source names, for the
    wizard's feed multiselect."""
    raw = set(_get_all_sources())
    norm = {
        r["source"]
        for r in await get_normalized_summary()
        if r.get("source") and r["source"] != "__total__"
    }
    return {"feeds": sorted(raw | norm)}


@router.get("/meta/fields")
async def meta_fields(
    dataset: str = Query("all"),
    feeds: list[str] = Query(default_factory=list),
) -> dict[str, list[str]]:
    """Return the matchable field names for a dataset (all|raw|normalized).

    Used to populate the wizard's field dropdown. The list is derived by
    sampling recent stored events so custom/extra-JSON fields that only appear
    in specific sources are offered:
      * When ``feeds`` are supplied, only those feeds are sampled.
      * When no feeds are supplied, ALL feeds are sampled (the full union across
        sources), so the dropdown shows every available field rather than a
        subset.
    Falls back to the static schema column lists only when sampling yields
    nothing (e.g. an empty database).
    """
    ds = (dataset or "all").strip().lower()
    selected = [f for f in (feeds or []) if f and f.strip()]

    raw_fields_static = set(CORE_COLUMNS)
    norm_fields_static = {c for c in _allowed_columns() if c not in {"extra_norm"}}

    def _keys_from(rows: list[dict[str, Any]]) -> set[str]:
        keys: set[str] = set()
        for row in rows:
            keys.update(k for k in row.keys() if k not in _FIELD_HIDDEN)
        return keys

    raw_fields: set[str] = set()
    norm_fields: set[str] = set()

    # Feeds to sample: the selection, or — when none chosen — every source so
    # the union covers all fields across all feeds.
    if ds in ("raw", "all"):
        raw_feeds = selected or _get_all_sources()
        for feed in raw_feeds:
            rows = await query_entries(source_name=feed, limit=_FIELD_SAMPLE_PER_FEED)
            raw_fields |= _keys_from(rows)
    if ds in ("normalized", "all"):
        if selected:
            norm_feeds = selected
        else:
            norm_feeds = [
                r["source"]
                for r in await get_normalized_summary()
                if r.get("source") and r["source"] != "__total__"
            ]
        for feed in norm_feeds:
            rows = await query_normalized(source_name=feed, limit=_FIELD_SAMPLE_PER_FEED)
            norm_fields |= _keys_from(rows)

    # Fall back to the static schema for any side that produced nothing.
    if ds == "raw":
        fields = raw_fields or raw_fields_static
    elif ds == "normalized":
        fields = norm_fields or norm_fields_static
    else:
        sampled = raw_fields | norm_fields
        fields = sampled or (raw_fields_static | norm_fields_static)
    return {"fields": sorted(fields)}


@router.get("/{watcher_id}")
async def get_watcher(watcher_id: str) -> dict[str, Any]:
    """Return one watcher definition."""
    watcher = await store.get_watcher(watcher_id)
    if watcher is None:
        raise HTTPException(status_code=404, detail="watcher not found")
    return watcher


@router.put("/{watcher_id}")
async def update_watcher(watcher_id: str, body: WatcherIn) -> dict[str, Any]:
    """Update a watcher definition in place (id is immutable)."""
    try:
        watcher = await store.update_watcher(watcher_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if watcher is None:
        raise HTTPException(status_code=404, detail="watcher not found")
    _reschedule()
    return watcher


@router.put("/{watcher_id}/enabled")
async def set_enabled(watcher_id: str, body: WatcherEnabledIn) -> dict[str, Any]:
    """Enable or disable a watcher."""
    watcher = await store.set_enabled(watcher_id, body.enabled)
    if watcher is None:
        raise HTTPException(status_code=404, detail="watcher not found")
    _reschedule()
    return watcher


@router.delete("/{watcher_id}", status_code=204)
async def delete_watcher(watcher_id: str) -> None:
    """Delete a watcher and its triggered-event history."""
    removed = await store.delete_watcher(watcher_id)
    if not removed:
        raise HTTPException(status_code=404, detail="watcher not found")
    _reschedule()


@router.get("/{watcher_id}/events")
async def list_events(
    watcher_id: str,
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return a watcher's triggered events (newest first), capped at the global
    ``watcher_max_events`` setting, plus the total count."""
    watcher = await store.get_watcher(watcher_id)
    if watcher is None:
        raise HTTPException(status_code=404, detail="watcher not found")
    capped = min(int(limit), int(load_watcher_max_events()))
    events = await store.list_events(watcher_id, limit=capped, offset=offset)
    total = await store.count_events(watcher_id)
    return {"events": events, "total": total}
