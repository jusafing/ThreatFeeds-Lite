"""
Ingest routes — push listener, local feed upload, remote feed fetch.
Supports JSON, NDJSON, CSV, and XML for local and remote feeds.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.config.loader import load_sources
from backend.db.manager import get_entry_count_for_source
from backend.ingestion.jobs import job_store
from backend.ingestion.local_feed import ingest_local_feed
from backend.ingestion.push_listener import process_push
from backend.ingestion.remote_feed import ingest_remote_feed
from backend.models.entry import IngestResponse, PreviewResponse
from backend.ingestion.preview import build_preview, confirm_preview

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# Plaintext formats + compressed wrappers (prompts-021B). The
# decompression layer unwraps .gz / single-member .zip before the
# parser sees the bytes.
_ALLOWED_EXTENSIONS = {".json", ".csv", ".xml", ".ndjson", ".txt", ".gz", ".zip"}


# ── Job-runner helpers ─────────────────────────────────────────────────────────

async def _run_local_feed_job(
    job_id: str, file_bytes: bytes, source_name: str, filename: str | None = None,
) -> None:
    try:
        result = await ingest_local_feed(
            file_bytes, source_name, job_id=job_id, filename=filename,
        )
        job_store.complete(job_id, {
            "total_read":  result.get("total_read", 0),
            "inserted":    result.get("inserted", 0),
            "duplicates":  result.get("duplicates", 0),
            "discarded":   result.get("discarded", 0),
        })
    except Exception as exc:
        job_store.fail(job_id, str(exc))


async def _run_confirm_preview_job(job_id: str, preview_id: str) -> None:
    try:
        result = await confirm_preview(preview_id, job_id=job_id)
        if result is None:
            job_store.fail(job_id, f"Preview '{preview_id}' not found or expired")
            return
        job_store.complete(job_id, {
            "total_read":  result.get("total_read", 0),
            "inserted":    result.get("inserted", 0),
            "duplicates":  result.get("duplicates", 0),
            "discarded":   result.get("discarded", 0),
        })
    except Exception as exc:
        job_store.fail(job_id, str(exc))


async def _run_push_job(job_id: str, payload, source_name: str) -> None:
    try:
        result = await process_push(payload, source_name, job_id=job_id)
        job_store.complete(job_id, {
            "total_read":  result.get("total_read", 0),
            "inserted":    result.get("inserted", 0),
            "duplicates":  result.get("duplicates", 0),
            "discarded":   result.get("discarded", 0),
        })
    except Exception as exc:
        job_store.fail(job_id, str(exc))


async def _first_ingest(source_name: str) -> bool:
    """Return True if the source has 0 prior rows (i.e. this is its first ingestion)."""
    try:
        return (await get_entry_count_for_source(source_name)) == 0
    except Exception:
        return True


def _trigger_watchers(result: dict[str, Any]) -> dict[str, Any]:
    """Fire realtime watchers for a *synchronous* ingest result.

    The background-job path triggers watchers via JobStore.complete(); the
    synchronous routes return without a job, so they must invoke the shared
    engine hook themselves (issue_local_006 review_02). Best-effort and
    non-blocking. Returns ``result`` unchanged for call-site convenience.
    """
    try:
        from backend.watchers.engine import schedule_realtime_ingest_eval
        schedule_realtime_ingest_eval(int(result.get("inserted", 0) or 0))
    except Exception:  # pragma: no cover — never break ingestion on a hook error
        pass
    return result


class PushPayload(BaseModel):
    model_config = {"extra": "allow"}
    source: str


def _listener_enabled() -> bool:
    """Whether the generic push listener is enabled (defaults to on)."""
    return bool(load_sources().get("listener", {}).get("enabled", True))


@router.post("/listener", response_model=None)
async def listener_ingest(
    payload: dict[str, Any] | list[dict[str, Any]],
    background_tasks: BackgroundTasks,
    request: Request,
    background: bool = False,
):
    """
    Generic push-listener endpoint.

    Accepts any JSON — a single object or an array of objects — and indexes it
    into a feed named after the authenticated user that pushed the events
    (prompts-058). When authentication is disabled (or no user is resolved) the
    request is anonymous and falls back to a per-request feed named
    ``Received Feed <epoch_seconds>``. This is the endpoint surfaced by the
    Configuration → Listener Endpoint tab. It is gated by the
    ``listener.enabled`` flag in config/sources.yaml (enabled by default).
    """
    if not _listener_enabled():
        raise HTTPException(status_code=503, detail="Listener is disabled")
    # Name the feed after the sending user. The auth middleware stashes the
    # resolved user at request.state.user when auth is enabled; with auth off it
    # is never set, so we fall back to the legacy per-request epoch name.
    # Usernames are validated [A-Za-z0-9._-]{1,40} (filesystem-safe), so the
    # value is safe to use verbatim as the feed/source identity.
    user = getattr(request.state, "user", None)
    if user and user.get("username"):
        source_name = user["username"]
    else:
        source_name = f"Received Feed {int(time.time())}"
    if background:
        job = job_store.create(source_name, "push", first_ingest=True)
        background_tasks.add_task(_run_push_job, job.id, payload, source_name)
        return {"job_id": job.id}
    result = _trigger_watchers(await process_push(payload, source_name))
    return IngestResponse(**result)


@router.post("/push/{source_name}", response_model=None)
async def push_ingest(
    source_name: str,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    background: bool = False,
):
    """Receive a JSON payload and ingest it into the named source DB."""
    if background:
        first = await _first_ingest(source_name)
        job = job_store.create(source_name, "push", first_ingest=first)
        background_tasks.add_task(_run_push_job, job.id, payload, source_name)
        return {"job_id": job.id}
    result = _trigger_watchers(await process_push(payload, source_name))
    return IngestResponse(**result)


@router.post("/push-batch/{source_name}", response_model=None)
async def push_ingest_batch(
    source_name: str,
    payload: list[dict[str, Any]],
    background_tasks: BackgroundTasks,
    background: bool = False,
):
    """Receive a JSON array and ingest all entries into the named source DB."""
    if background:
        first = await _first_ingest(source_name)
        job = job_store.create(source_name, "push", first_ingest=first)
        background_tasks.add_task(_run_push_job, job.id, payload, source_name)
        return {"job_id": job.id}
    result = _trigger_watchers(await process_push(payload, source_name))
    return IngestResponse(**result)


@router.post("/local/{source_name}", response_model=None)
async def local_feed_ingest(
    source_name: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    background: bool = False,
):
    """Upload a local feed file (JSON, NDJSON, CSV, XML) and ingest its contents."""
    suffix = ""
    if file.filename:
        from pathlib import Path
        suffix = Path(file.filename).suffix.lower()
    if suffix and suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Accepted: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )
    raw_bytes = await file.read()
    if background:
        first = await _first_ingest(source_name)
        job = job_store.create(source_name, "local_feed", first_ingest=first)
        background_tasks.add_task(
            _run_local_feed_job, job.id, raw_bytes, source_name, file.filename,
        )
        return {"job_id": job.id}
    result = await ingest_local_feed(raw_bytes, source_name, filename=file.filename)
    _trigger_watchers(result)
    return IngestResponse(**{k: v for k, v in result.items() if k != "format"})


# ── Preview / Confirm ──────────────────────────────────────────────────────────


@router.post("/preview/local/{source_name}", response_model=PreviewResponse)
async def preview_local_feed(source_name: str, file: UploadFile = File(...)) -> PreviewResponse:
    """Parse a local feed file and return a preview without persisting entries."""
    raw_bytes = await file.read()
    return await build_preview(raw_bytes, source_name, origin="local", filename=file.filename)


@router.post("/preview/confirm/{preview_id}", response_model=None)
async def confirm_local_preview(
    preview_id: str,
    background_tasks: BackgroundTasks,
    background: bool = False,
):
    """Confirm and persist a previously previewed feed."""
    if background:
        # Peek the preview entry to find the source name & first-ingest flag.
        # We can't pop here; the runner will pop. Just look it up.
        from backend.ingestion.preview import _store as _preview_store
        stored = _preview_store.get(preview_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"Preview '{preview_id}' not found or expired")
        source_name = stored["source_name"]
        first = await _first_ingest(source_name)
        job = job_store.create(source_name, "preview_confirm", first_ingest=first)
        background_tasks.add_task(_run_confirm_preview_job, job.id, preview_id)
        return {"job_id": job.id}
    result = await confirm_preview(preview_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Preview '{preview_id}' not found or expired")
    _trigger_watchers(result)
    return IngestResponse(**{k: v for k, v in result.items() if k != "format"})


class RemoteIngestRequest(BaseModel):
    url: str


@router.post("/remote/{source_name}", response_model=IngestResponse)
async def remote_feed_ingest(source_name: str, body: RemoteIngestRequest) -> IngestResponse:
    """Fetch a remote feed file by URL (JSON, NDJSON, CSV, XML) and ingest its contents."""
    result = await ingest_remote_feed(body.url, source_name)
    _trigger_watchers(result)
    return IngestResponse(**{k: v for k, v in result.items() if k != "format"})
