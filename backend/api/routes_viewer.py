"""
Viewer routes — query ingested entries for the frontend.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from backend.db.manager import get_summary, query_entries
from backend.ingestion.jobs import job_store

router = APIRouter(prefix="/api/viewer", tags=["viewer"])


@router.get("/entries")
async def get_entries(
    source: Optional[str] = Query(None, description="Filter by source name"),
    search: Optional[str] = Query(None, description="Full-text search across key fields"),
    severity: Optional[str] = Query(None),
    indicator_type: Optional[str] = Query(None),
    threat_type: Optional[str] = Query(None),
    ingest_mode: Optional[str] = Query(None),
    field: Optional[list[str]] = Query(
        None,
        description=(
            "Arbitrary column filter as 'name=value' (repeatable). Unknown "
            "columns are ignored. Validated against the entries table columns."
        ),
    ),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    filters: dict[str, str] = {}
    if severity:
        filters["severity"] = severity
    if indicator_type:
        filters["indicator_type"] = indicator_type
    if threat_type:
        filters["threat_type"] = threat_type
    if ingest_mode:
        filters["ingest_mode"] = ingest_mode

    # issue_local_02: arbitrary 'field=name=value' filters. The column name is
    # validated against the entries-table whitelist inside query_entries, so an
    # unknown/unsafe name is silently dropped rather than reaching SQL.
    for item in field or []:
        name, sep, value = item.partition("=")
        if sep and name:
            filters[name.strip()] = value

    return await query_entries(
        source_name=source,
        limit=limit,
        offset=offset,
        search=search,
        filters=filters or None,
    )


@router.get("/summary")
async def get_summary_endpoint(
    include_active: bool = Query(False, description="Include in-flight jobs per source"),
) -> list[dict]:
    """Return entry counts per source plus overall total.

    When include_active=true, each per-source row also includes an
    ``active_jobs`` list of currently-running jobs targeting that source
    (job_id, kind, step, processed, total).
    """
    rows = await get_summary()
    if not include_active:
        return rows

    active = job_store.list_active()
    by_source: dict[str, list[dict]] = {}
    for j in active:
        by_source.setdefault(j.source, []).append({
            "job_id": j.id,
            "kind": j.kind,
            "step": j.step,
            "processed": j.processed,
            "total": j.total,
        })
    for row in rows:
        if row["source"] == "__total__":
            continue
        row["active_jobs"] = by_source.get(row["source"], [])
    return rows
