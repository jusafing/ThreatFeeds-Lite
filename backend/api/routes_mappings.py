"""
Mapping versions API (prompts-021F).

Per-source mapping_version history, diff, and activation. Companion to
the smart-mappings flow (routes_smart) which creates new versions via
proposal approval; this router exposes the operator-visible history
and rollback controls.

Routes:
  GET  /api/normalizer/mappings/versions?source=...
  GET  /api/normalizer/mappings/versions/{id}
  POST /api/normalizer/mappings/versions/{id}/activate
  GET  /api/normalizer/mappings/diff?from={id}&to={id}
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.db.manager import reset_normalized_flag_for_source
from backend.normalizer.mappings import (
    activate_version,
    diff_mappings,
    get_active_version,
    get_version,
    list_versions,
    regenerate_yaml_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/normalizer/mappings", tags=["mapping-versions"])


@router.get("/versions")
async def list_mapping_versions(
    source: str | None = Query(
        None, description="Filter by source name (omit to list all sources)"
    ),
) -> list[dict[str, Any]]:
    """Return all mapping_version rows newest-first.

    Each row carries id, source_name, mapping, origin, source_proposal_id,
    active (bool), note, created_at.
    """
    return await list_versions(source)


@router.get("/versions/{version_id}")
async def get_mapping_version(version_id: int) -> dict[str, Any]:
    """Return one mapping_version row + its diff vs. the currently-active
    version for the same source.

    Response shape:
      {
        "version": <row dict>,
        "active":  <row dict | None>,
        "diff":    {"added": [...], "removed": [...], "changed": [...]},
      }

    When the requested version IS the active one, ``diff`` compares it to
    itself (all-empty buckets).
    """
    row = await get_version(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"version {version_id} not found")
    active = await get_active_version(row["source_name"])
    from_map = (active or {}).get("mapping") or {}
    to_map = row.get("mapping") or {}
    return {
        "version": row,
        "active": active,
        "diff": diff_mappings(from_map, to_map),
    }


@router.post("/versions/{version_id}/activate")
async def activate_mapping_version(version_id: int) -> dict[str, Any]:
    """Promote ``version_id`` to active for its source.

    Side effects:
      * Demotes any previously-active version for the same source
        (atomic via BEGIN IMMEDIATE + partial unique index).
      * Regenerates yaml manual_mappings snapshot.
      * Marks the source's raw entries dirty (normalized=0) so the
        scheduler's next normalizer tick rebuilds rows with the new
        mapping_version_id.

    Returns:
      {
        "version_id": <int>,
        "source": <str>,
        "reset_rows": <int>,   # raw rows now flagged for re-normalization
      }
    """
    row = await get_version(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"version {version_id} not found")
    source = row["source_name"]
    try:
        await activate_version(version_id)
    except LookupError as exc:  # pragma: no cover — guarded above
        raise HTTPException(status_code=404, detail=str(exc))
    await regenerate_yaml_snapshot()
    reset_rows = await reset_normalized_flag_for_source(source)
    logger.info(
        "mapping_version %d activated for source %s (reset %d rows)",
        version_id, source, reset_rows,
    )
    return {
        "version_id": version_id,
        "source": source,
        "reset_rows": reset_rows,
    }


@router.get("/diff")
async def diff_mapping_versions(
    from_id: int = Query(..., alias="from", description="Baseline version id"),
    to_id: int = Query(..., alias="to", description="Target version id"),
) -> dict[str, Any]:
    """Compute the three-bucket diff from one mapping_version to another.

    Both versions must exist. They are NOT required to belong to the
    same source — operators may want to compare snapshots across feeds —
    but the response includes both source_name values so the UI can warn.
    """
    from_row = await get_version(from_id)
    if from_row is None:
        raise HTTPException(status_code=404, detail=f"from version {from_id} not found")
    to_row = await get_version(to_id)
    if to_row is None:
        raise HTTPException(status_code=404, detail=f"to version {to_id} not found")
    return {
        "from": {
            "id": from_row["id"],
            "source_name": from_row["source_name"],
        },
        "to": {
            "id": to_row["id"],
            "source_name": to_row["source_name"],
        },
        "diff": diff_mappings(
            from_row.get("mapping") or {},
            to_row.get("mapping") or {},
        ),
    }
