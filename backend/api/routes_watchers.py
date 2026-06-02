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
from backend.db.manager import CORE_COLUMNS, _get_all_sources
from backend.models.watcher import WatcherEnabledIn, WatcherIn
from backend.normalizer.db import _allowed_columns, get_normalized_summary
from backend.config.loader import load_watcher_max_events
from backend import scheduler as scheduler_mod

router = APIRouter(prefix="/api/watchers", tags=["watchers"])


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
async def meta_fields(dataset: str = Query("all")) -> dict[str, list[str]]:
    """Return the matchable field names for a dataset (all|raw|normalized).

    Used to populate the wizard's field dropdown. Matching is applied per field
    name; the feed multiselect scopes which feeds are searched.
    """
    ds = (dataset or "all").strip().lower()
    raw_fields = set(CORE_COLUMNS)
    norm_fields = {c for c in _allowed_columns() if c not in {"extra_norm"}}
    if ds == "raw":
        fields = raw_fields
    elif ds == "normalized":
        fields = norm_fields
    else:
        fields = raw_fields | norm_fields
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
