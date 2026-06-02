"""
In-memory background job store for ingestion progress reporting.

Each ingest path can run synchronously (the legacy/default behaviour) or
be wrapped in a Job that lets the frontend poll for progress via
GET /api/jobs/{id}.

This is a single-process tool — no Redis, no persistence. Jobs are
purged from memory <_TTL_SECONDS> after they enter a terminal state.

When a job reaches a terminal state, the latest counters are also
persisted to data/meta.db via backend.db.meta.record_ingest so the
Summary table can render the last-ingest column across restarts.

Threading note: FastAPI's BackgroundTasks run in the same asyncio loop
as the request handler (when the task is a coroutine), so plain dict
mutation is safe here.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

_TTL_SECONDS = 3600  # 1 hour after completion

JobKind = Literal[
    "api_pull",
    "rss_pull",
    "remote_feed",
    "remote_json",
    "local_feed",
    "push",
    "preview_confirm",
    "source_preview_confirm",
    "smart_proposal",
]

JobState = Literal["queued", "running", "done", "error"]
JobStep = Literal["fetching", "parsing", "normalising", "inserting", "done"]


@dataclass
class Job:
    id: str
    source: str
    kind: JobKind
    state: JobState = "queued"
    step: JobStep = "fetching"
    processed: int = 0
    total: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    first_ingest: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    expires_at: Optional[float] = None
    error_msg: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop internal-only field
        d.pop("expires_at", None)
        return d


class JobStore:
    """In-memory job registry. Holds all jobs; evicts terminal ones after TTL."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def create(self, source: str, kind: JobKind, first_ingest: bool = False) -> Job:
        self._evict()
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, source=source, kind=kind, first_ingest=first_ingest)
        self._jobs[job_id] = job
        logger.debug("job_created id=%s source=%s kind=%s first=%s",
                     job_id, source, kind, first_ingest)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        self._evict()
        return self._jobs.get(job_id)

    def list_active(self) -> list[Job]:
        """Return jobs that are not in a terminal state."""
        self._evict()
        return [j for j in self._jobs.values() if j.state in ("queued", "running")]

    # ── Updates ──────────────────────────────────────────────────────────────

    def set_running(self, job_id: str) -> None:
        """Transition a queued job to ``running`` without changing its step.

        Used by callers that have just spawned the worker task and want the
        public API to report ``state='running'`` even before the worker has
        ticked ``update_step``. Idempotent; no-op for unknown ids.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.state = "running"

    def update_step(self, job_id: str, step: JobStep, total: int | None = None) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.state = "running"
        job.step = step
        if total is not None:
            job.total = total

    def update_progress(self, job_id: str, processed: int) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.processed = processed

    def complete(self, job_id: str, counters: dict[str, int]) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.state = "done"
        job.step = "done"
        job.counters = dict(counters)
        # Snap processed up to total if a final insert loop ended cleanly
        if job.total and job.processed < job.total:
            job.processed = job.total
        job.finished_at = time.time()
        job.expires_at = job.finished_at + _TTL_SECONDS
        logger.debug("job_done id=%s counters=%s", job_id, counters)
        self._persist_meta(job)
        self._maybe_trigger_smart_mode(job)
        self._maybe_trigger_watchers(job)

    def fail(self, job_id: str, error_msg: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.state = "error"
        job.error_msg = error_msg
        job.finished_at = time.time()
        job.expires_at = job.finished_at + _TTL_SECONDS
        logger.warning("job_error id=%s msg=%s", job_id, error_msg)
        self._persist_meta(job)

    # ── Persistence to data/meta.db ──────────────────────────────────────────

    def _persist_meta(self, job: Job) -> None:
        """Best-effort: record the latest counters for this source.

        Imported lazily to keep db.meta from creating a circular import
        and to make this a no-op if the meta module is unavailable
        (e.g. in unit tests that monkeypatch DATA_DIR).
        """
        try:
            from backend.db import meta  # noqa: WPS433 (lazy import is intentional)
        except Exception:  # pragma: no cover
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not inside an event loop — likely a sync unit test. Skip.
            return
        loop.create_task(
            meta.record_ingest(
                source=job.source,
                counters=job.counters,
                state=job.state,
                kind=job.kind,
            )
        )

    # ── Smart-mode trigger (021E-3) ──────────────────────────────────────────

    def _maybe_trigger_smart_mode(self, job: Job) -> None:
        """Submit a smart-mode proposal job when this completion was the
        first ingest for the source.

        Lazy-imports backend.scheduler to avoid an import cycle, and runs
        the submission as a background task so the calling code path
        (which is synchronous) is not blocked. Smart-mode jobs are only
        spawned for ingestion job kinds that carry a real ``first_ingest``
        flag; smart_proposal / preview kinds are explicitly excluded.
        """
        if not job.first_ingest:
            return
        if job.kind in ("smart_proposal", "preview_confirm", "source_preview_confirm"):
            return
        try:
            from backend import scheduler as scheduler_mod  # noqa: WPS433
        except Exception:  # pragma: no cover — defensive
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            scheduler_mod.submit_smart_job(job.source, reason="on_new_feed")
        )

    # ── Watcher trigger (issue_local_006) ────────────────────────────────────

    def _maybe_trigger_watchers(self, job: Job) -> None:
        """Evaluate realtime watchers against the raw dataset when this job
        actually indexed new events.

        Lazy-imports the watcher engine to avoid an import cycle, and runs the
        evaluation as a background task so the synchronous completion path is
        not blocked. No-op when nothing was inserted or when not inside an event
        loop (e.g. sync unit tests).
        """
        if int(job.counters.get("inserted", 0) or 0) <= 0:
            return
        try:
            from backend.watchers.engine import run_watchers  # noqa: WPS433
        except Exception:  # pragma: no cover — defensive
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(run_watchers("ingest", {"raw"}))

    # ── Maintenance ──────────────────────────────────────────────────────────

    def _evict(self) -> None:
        now = time.time()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.expires_at is not None and j.expires_at < now
        ]
        for jid in expired:
            del self._jobs[jid]

    def reset(self) -> None:
        """Test helper — drop all jobs."""
        self._jobs.clear()


# Module-level singleton — the rest of the backend imports this.
job_store = JobStore()
