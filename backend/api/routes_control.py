"""
Control routes — DB reset operations and manual source refresh.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.config.loader import load_sources
from backend.db.manager import reset_db
from backend.ingestion.api_pull import pull_api_source
from backend.ingestion.rss_pull import pull_rss_source
from backend.ingestion.remote_feed import ingest_remote_feed
from backend.models.entry import IngestResponse

router = APIRouter(prefix="/api/control", tags=["control"])


@router.post("/reset-db")
async def reset_all_dbs() -> dict:
    """Delete and recreate all source SQLite databases."""
    deleted = reset_db(source_name=None)
    return {"reset": deleted, "message": f"Deleted {len(deleted)} database(s)"}


@router.post("/reset-source/{source_name}")
async def reset_source_db(source_name: str) -> dict:
    """Delete and recreate the SQLite database for a specific source."""
    deleted = reset_db(source_name=source_name)
    if not deleted:
        return {"reset": [], "message": f"No database found for source '{source_name}'"}
    return {"reset": deleted, "message": f"Reset source '{source_name}'"}


@router.post("/refresh/api-pull/{name}", response_model=IngestResponse)
async def refresh_api_pull(name: str) -> IngestResponse:
    """Manually trigger an immediate pull for an api_pull source."""
    sources = load_sources().get("api_pull", [])
    source = next((s for s in sources if s.get("name") == name), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"api_pull source '{name}' not found")
    result = await pull_api_source(source)
    return IngestResponse(**result)


@router.post("/refresh/rss-pull/{name}", response_model=IngestResponse)
async def refresh_rss_pull(name: str) -> IngestResponse:
    """Manually trigger an immediate pull for an rss_pull source."""
    sources = load_sources().get("rss_pull", [])
    source = next((s for s in sources if s.get("name") == name), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"rss_pull source '{name}' not found")
    result = await pull_rss_source(source)
    return IngestResponse(**result)


@router.post("/refresh/remote-json-pull/{name}", response_model=IngestResponse)
async def refresh_remote_json_pull(name: str) -> IngestResponse:
    """Manually trigger an immediate fetch for a remote_json_pull source."""
    sources = load_sources().get("remote_json_pull", [])
    source = next((s for s in sources if s.get("name") == name), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"remote_json_pull source '{name}' not found")
    result = await ingest_remote_feed(source["url"], name, source_fields=source.get("fields"))
    return IngestResponse(**result)


async def _refresh_one(kind: str, source: dict) -> dict:
    """Refresh a single source of the given kind, returning its result dict."""
    if kind == "api_pull":
        return await pull_api_source(source)
    if kind == "rss_pull":
        return await pull_rss_source(source)
    if kind == "remote_json_pull":
        return await ingest_remote_feed(
            source["url"], source["name"], source_fields=source.get("fields")
        )
    raise ValueError(f"unknown source kind: {kind}")


async def _refresh_all(kind: str) -> dict:
    """Manually refresh every configured source of one kind in a single call.

    Each source is refreshed independently: a failure on one source is
    captured in that source's result entry and never aborts the batch, so
    the operator always gets a complete per-source report.
    """
    sources = load_sources().get(kind, [])
    results: list[dict] = []
    succeeded = 0
    failed = 0
    for source in sources:
        name = source.get("name", "")
        try:
            r = await _refresh_one(kind, source)
            results.append({
                "name": name,
                "ok": True,
                "inserted": r.get("inserted", 0),
                "duplicates": r.get("duplicates", 0),
                "skipped": r.get("skipped", 0),
                "errors": r.get("errors", []),
            })
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 — report, never abort the batch
            results.append({"name": name, "ok": False, "error": str(exc)})
            failed += 1
    return {
        "kind": kind,
        "total": len(sources),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


@router.post("/refresh/api-pull")
async def refresh_all_api_pull() -> dict:
    """Manually refresh all configured api_pull sources at once."""
    return await _refresh_all("api_pull")


@router.post("/refresh/rss-pull")
async def refresh_all_rss_pull() -> dict:
    """Manually refresh all configured rss_pull sources at once."""
    return await _refresh_all("rss_pull")


@router.post("/refresh/remote-json-pull")
async def refresh_all_remote_json_pull() -> dict:
    """Manually refresh all configured remote_json_pull (External Feed) sources."""
    return await _refresh_all("remote_json_pull")
