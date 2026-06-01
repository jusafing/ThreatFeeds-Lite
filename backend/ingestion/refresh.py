"""
Shared single-source refresh helpers.

`refresh_source` is the canonical dispatcher that maps a sources.yaml bucket
kind to its pull/ingest coroutine. It is reused by:

- the manual refresh routes (`backend/api/routes_control.py`), which call it
  synchronously and return the resulting counters to the caller, and
- the immediate "load on enable" path (`backend/api/routes_sources.py`), which
  runs it in the background wrapped in a `job_store` Job via
  `run_tracked_pull` so the UI can show a "pulling…" / "ready" / "error"
  status marker per feed (issue #1).
"""
from __future__ import annotations

import logging
from typing import Any

from backend.ingestion.api_pull import pull_api_source
from backend.ingestion.rss_pull import pull_rss_source
from backend.ingestion.remote_feed import ingest_remote_feed
from backend.ingestion.jobs import job_store

logger = logging.getLogger(__name__)

# Maps a sources.yaml bucket kind to the JobKind label used by job_store /
# the frontend status markers.
_JOB_KIND: dict[str, str] = {
    "api_pull": "api_pull",
    "rss_pull": "rss_pull",
    "remote_json_pull": "remote_json",
}


async def refresh_source(kind: str, source: dict[str, Any]) -> dict[str, Any]:
    """Refresh a single source of the given kind, returning its counters dict.

    Raises ``ValueError`` for an unknown kind. Any network/parse error from the
    underlying pull coroutine propagates to the caller.
    """
    if kind == "api_pull":
        return await pull_api_source(source)
    if kind == "rss_pull":
        return await pull_rss_source(source)
    if kind == "remote_json_pull":
        return await ingest_remote_feed(
            source["url"], source["name"], source_fields=source.get("fields")
        )
    raise ValueError(f"unknown source kind: {kind}")


async def run_tracked_pull(kind: str, source: dict[str, Any]) -> None:
    """Run an immediate pull wrapped in a job_store Job (fire-and-forget).

    Creating a Job makes the pull observable while it runs (via
    GET /api/jobs?active=true) and persists last-ingest meta on completion
    (via GET /api/viewer/summary), which together drive the per-feed
    "pulling…" / green "ready" / red "error" markers. Failures are recorded on
    the Job and logged; they never raise out of this coroutine so a background
    task is never left with an unhandled exception.
    """
    name = source.get("name", "")
    job = job_store.create(name, _JOB_KIND.get(kind, kind))
    job_store.set_running(job.id)
    try:
        result = await refresh_source(kind, source)
    except Exception as exc:  # noqa: BLE001 — record on the job, never re-raise
        job_store.fail(job.id, str(exc))
        logger.exception("immediate_pull_failed kind=%s name=%s: %s", kind, name, exc)
        return
    job_store.complete(job.id, result)
    logger.info("immediate_pull_done kind=%s name=%s result=%s", kind, name, result)
