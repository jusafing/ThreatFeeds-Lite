"""
Normalizer API routes — configuration, manual trigger, and results viewer.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.normalizer.config import (
    VALID_MODES,
    load_normalizer_config,
    save_normalizer_config,
)
from backend.normalizer.consolidated import get_active_consolidated
from backend.normalizer.db import get_normalized_summary, query_normalized
from backend.normalizer.engine import run_normalizer
from backend.normalizer.run_history import list_runs
from backend.normalizer.smart_runner import reapply_consolidated_to_sources

router = APIRouter(prefix="/api/normalizer", tags=["normalizer"])


@router.get("/config")
async def get_normalizer_config() -> dict[str, Any]:
    """Return the current normalizer configuration."""
    return load_normalizer_config()


@router.put("/config")
async def update_normalizer_config(body: dict[str, Any]) -> dict[str, Any]:
    """Update normalizer configuration. Body is merged with existing config."""
    if "mode" in body and body["mode"] not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {body['mode']!r}; must be one of "
            f"{sorted(VALID_MODES)}",
        )
    cfg = load_normalizer_config()
    cfg.update(body)
    save_normalizer_config(cfg)
    return cfg


@router.post("/run")
async def trigger_normalizer_run() -> dict[str, Any]:
    """Manually trigger an immediate normalizer run ("Run Now").

    prompts-039: in ``smart`` mode with an active consolidated mapping, a plain
    run only touches rows still flagged ``normalized=0`` — after the first run
    that set is empty, so "Run Now" appears to do nothing. To make Run Now
    actually (re-)apply the active mapping, clear+reset the mapping's feeds
    first, then run. Other modes keep the plain incremental run.
    """
    cfg = load_normalizer_config()
    reset_rows = 0
    if cfg.get("enabled", True) and cfg.get("mode") == "smart":
        active = await get_active_consolidated()
        if active is not None:
            reset_rows = await reapply_consolidated_to_sources(
                active.get("sources") or []
            )
    result = await run_normalizer(trigger="manual")
    if reset_rows:
        return {"reset_rows": reset_rows, **result}
    return result


@router.get("/runs")
async def get_run_history(limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent normalizer run-history rows, newest first.

    prompts-039: covers manual ("Run Now"), scheduled, and on-demand re-apply
    runs. Smart-mode applies carry the proposal name + feed list; auto/manual
    rows carry the mode only.
    """
    return await list_runs(limit=limit)


@router.get("/entries")
async def get_normalized_entries(
    source: str | None = None,
    limit: int = 500,
    offset: int = 0,
    search: str | None = None,
    mapping_version_id: int | None = None,
    field: list[str] | None = Query(
        None,
        description=(
            "Arbitrary column filter as 'name=value' (repeatable). Unknown "
            "columns are ignored. Validated against the normalized schema."
        ),
    ),
) -> list[dict[str, Any]]:
    """Query normalized entries.

    prompts-021F: ``mapping_version_id`` filters to rows produced under a
    specific mapping_version. Omit to return rows across all versions
    (incl. NULL-version legacy rows).

    issue_local_02: ``field`` carries repeatable 'name=value' column filters,
    validated against the yaml-derived schema inside query_normalized.
    """
    filters: dict[str, str] = {}
    for item in field or []:
        name, sep, value = item.partition("=")
        if sep and name:
            filters[name.strip()] = value

    return await query_normalized(
        source_name=source,
        limit=limit,
        offset=offset,
        search=search,
        mapping_version_id=mapping_version_id,
        filters=filters or None,
    )


@router.get("/summary")
async def get_normalizer_summary() -> list[dict[str, Any]]:
    """Return entry count per source in normalized DB."""
    return await get_normalized_summary()
