"""
Scheduler module (prompts-021E-3).

Owns the single ``AsyncIOScheduler`` instance for the whole backend, extracted
from ``backend.main`` to keep the FastAPI lifespan lean and to give smart-mode
its own isolated submission entry point.

Public surface:
    * ``scheduler``                  — module-level ``AsyncIOScheduler`` instance
    * ``reload()``                   — re-read sources.yaml + normalizer-config.yaml
                                       and rebuild jobs (replaces the in-place
                                       ``_schedule_sources`` in main.py)
    * ``start()`` / ``stop()``       — lifecycle wrappers used by FastAPI lifespan
    * ``submit_smart_job(source, *,
        reason, provider=None,
        sample_size=None)``          — fire-and-forget smart-mode proposal,
                                       gated by ``smart_mode`` config + an
                                       idempotency check + a concurrency
                                       semaphore (default ``max_concurrent=2``).

Pull-source coverage:
    ``submit_smart_job`` only fires from the job_store.complete() hook, which
    is currently only wired for push, push-batch, local-feed, and
    preview-confirm ingestion paths. api_pull / rss_pull / remote_json_pull
    do not create job_store entries and therefore do not trigger smart-mode
    on first ingest. This is documented in docs/architecture.md.

Failure semantics (Q4):
    Smart-mode runner failures land as ``error``-status proposal rows (matches
    the existing 021E-1 behaviour). No exponential backoff. Next scheduled
    tick runs normally.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config.loader import load_sources
from backend.ingestion.api_pull import pull_api_source
from backend.ingestion.remote_feed import ingest_remote_feed
from backend.ingestion.rss_pull import pull_rss_source
from backend.normalizer.config import load_normalizer_config
from backend.normalizer.engine import run_normalizer

logger = logging.getLogger(__name__)


# ── Module singletons ──────────────────────────────────────────────────────

scheduler = AsyncIOScheduler()

# Concurrency cap for smart-mode submissions (Q3). Re-initialised on each
# reload() so an updated ``smart_mode.concurrency.max_concurrent`` takes effect
# without a process restart.
_smart_semaphore: asyncio.Semaphore = asyncio.Semaphore(2)
_smart_semaphore_limit: int = 2


# ── Pull-source / normalizer schedules ─────────────────────────────────────


def reload() -> None:
    """Re-read sources.yaml + normalizer-config.yaml and rebuild all jobs.

    Replaces ``_schedule_sources`` in the previous main.py. Identical job
    ids are preserved so APScheduler upserts cleanly.
    """
    global _smart_semaphore, _smart_semaphore_limit

    scheduler.remove_all_jobs()
    sources = load_sources()

    # prompts-042: api_pull/rss_pull honour an optional ``continuous`` gate.
    # Absent ``continuous`` defaults to True so existing entries keep being
    # scheduled (backward compatible). remote_json_pull defaults to False.
    for src in sources.get("api_pull", []):
        if not src.get("enabled", False) or not src.get("continuous", True):
            continue
        interval = int(src.get("interval_minutes", 15))
        scheduler.add_job(
            pull_api_source,
            trigger="interval",
            minutes=interval,
            args=[src],
            id=f"api_pull__{src['name']}",
            replace_existing=True,
        )
        logger.info("Scheduled api_pull '%s' every %dm", src["name"], interval)

    for src in sources.get("rss_pull", []):
        if not src.get("enabled", False) or not src.get("continuous", True):
            continue
        interval = int(src.get("interval_minutes", 15))
        scheduler.add_job(
            pull_rss_source,
            trigger="interval",
            minutes=interval,
            args=[src],
            id=f"rss_pull__{src['name']}",
            replace_existing=True,
        )
        logger.info("Scheduled rss_pull '%s' every %dm", src["name"], interval)

    async def _pull_remote_feed(s: dict) -> None:
        await ingest_remote_feed(s["url"], s["name"], source_fields=s.get("fields"))

    for src in sources.get("remote_json_pull", []):
        if not src.get("enabled", False) or not src.get("continuous", False):
            continue
        interval = int(src.get("interval_minutes", 15))
        scheduler.add_job(
            _pull_remote_feed,
            trigger="interval",
            minutes=interval,
            args=[src],
            id=f"remote_json_pull__{src['name']}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled remote_json_pull '%s' every %dm", src["name"], interval
        )

    # ── Normalizer auto-run ────────────────────────────────────────────────
    norm_cfg = load_normalizer_config()
    if norm_cfg.get("enabled", True):
        norm_interval = int(norm_cfg.get("interval_minutes", 30))
        scheduler.add_job(
            run_normalizer,
            trigger="interval",
            minutes=norm_interval,
            id="normalizer__auto",
            replace_existing=True,
            kwargs={"trigger": "schedule"},
        )
        logger.info(
            "Scheduled normalizer (mode=%s) every %dm",
            norm_cfg.get("mode", "auto"), norm_interval,
        )

    # ── Smart-mode scheduled trigger ───────────────────────────────────────
    smart_cfg = _resolved_smart_mode_config(norm_cfg)
    new_limit = int(smart_cfg["concurrency"]["max_concurrent"])
    if new_limit != _smart_semaphore_limit:
        _smart_semaphore = asyncio.Semaphore(new_limit)
        _smart_semaphore_limit = new_limit
        logger.info("Smart-mode concurrency semaphore set to %d", new_limit)

    if smart_cfg["enabled"] and smart_cfg["schedule"]["enabled"]:
        interval = int(smart_cfg["schedule"]["interval_minutes"])
        # The scheduled job fans out across sources at fire time so that
        # per-source overrides are honoured without re-reading config in
        # the job body.
        scheduler.add_job(
            _scheduled_smart_fanout,
            trigger="interval",
            minutes=interval,
            id="smart_mode__schedule",
            replace_existing=True,
        )
        logger.info("Scheduled smart_mode every %dm", interval)

    # ── Watchers (issue_local_006) ─────────────────────────────────────────
    # One interval job per enabled 'scheduled'-mode watcher. Realtime watchers
    # are driven by the ingest/normalize completion hooks instead, so they are
    # NOT scheduled here. Read synchronously (sqlite3) since reload() runs
    # outside an event loop.
    try:
        from backend.db.watchers import list_scheduled_watchers_sync
        from backend.watchers.engine import evaluate_watcher_by_id

        for w in list_scheduled_watchers_sync():
            seconds = max(int(w["interval_sec"]), 5)
            scheduler.add_job(
                evaluate_watcher_by_id,
                trigger="interval",
                seconds=seconds,
                args=[w["id"]],
                id=f"watcher__{w['id']}",
                replace_existing=True,
            )
            logger.info("Scheduled watcher '%s' every %ds", w["id"], seconds)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Watcher scheduling skipped: %s", exc)


def start() -> None:
    """Start the scheduler. Idempotent."""
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


def stop() -> None:
    """Stop the scheduler. Idempotent."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")


# ── Smart-mode submission ──────────────────────────────────────────────────


def _resolved_smart_mode_config(norm_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the ``smart_mode`` block from the normalizer config.

    The normalizer config loader deep-merges defaults under the ``smart_mode``
    key (see ``backend.normalizer.config``), so we can read it directly.
    """
    return dict(norm_cfg.get("smart_mode", {}))


def _resolve_per_source(
    smart_cfg: dict[str, Any], source_name: str
) -> dict[str, Any]:
    """Resolve per-source overrides for a given source.

    Returns a dict with keys: enabled, provider, sample_size. Per-source
    values take precedence over globals; missing per-source keys fall back
    to global ``smart_mode`` values.
    """
    for entry in smart_cfg.get("sources", []) or []:
        if isinstance(entry, dict) and entry.get("name") == source_name:
            return {
                "enabled": bool(entry.get("enabled", smart_cfg.get("enabled", False))),
                "provider": entry.get("provider", smart_cfg.get("provider")),
                "sample_size": int(
                    entry.get("sample_size", smart_cfg.get("sample_size", 20))
                ),
            }
    return {
        "enabled": bool(smart_cfg.get("enabled", False)),
        "provider": smart_cfg.get("provider"),
        "sample_size": int(smart_cfg.get("sample_size", 20)),
    }


async def _has_pending_proposal(source_name: str) -> bool:
    """Idempotency guard: skip submission when a pending proposal exists."""
    from backend.normalizer.proposals import list_proposals

    try:
        rows = await list_proposals(source=source_name, status="pending", limit=1)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Pending-proposal check failed for %s: %s", source_name, exc)
        return False
    return bool(rows)


async def _scheduled_smart_fanout() -> None:
    """Run when the schedule trigger fires: submit a smart job per source.

    Fans out across every source that has per-source ``enabled=true`` OR
    falls under the global ``smart_mode.enabled=true``. Each submission
    goes through the same gates as ``on_new_feed`` (pending-proposal
    suppression + concurrency semaphore).
    """
    norm_cfg = load_normalizer_config()
    smart_cfg = _resolved_smart_mode_config(norm_cfg)
    if not smart_cfg.get("enabled", False):
        return

    sources = load_sources()
    source_names: list[str] = []
    for kind in ("api_pull", "rss_pull", "remote_json_pull"):
        for src in sources.get(kind, []) or []:
            name = src.get("name")
            if name:
                source_names.append(name)

    for name in source_names:
        await submit_smart_job(name, reason="schedule")


async def submit_smart_job(
    source_name: str,
    *,
    reason: str,
    provider: str | None = None,
    sample_size: int | None = None,
) -> str | None:
    """Submit a smart-mode proposal job.

    Returns the spawned job_id on success, or ``None`` when the submission
    is suppressed by a gate (config disabled, pending-proposal exists,
    invalid trigger reason). Failures inside the LLM runner land as
    ``error``-status proposals; the submission itself does not raise.

    Args:
        source_name: feed source to map.
        reason: one of ``'manual' | 'schedule' | 'on_new_feed'``.
        provider: optional provider override (per-call > per-source >
                  global > default).
        sample_size: optional sample-size override.
    """
    if reason not in ("manual", "schedule", "on_new_feed"):
        logger.warning("submit_smart_job: invalid reason %r", reason)
        return None

    norm_cfg = load_normalizer_config()
    smart_cfg = _resolved_smart_mode_config(norm_cfg)

    if not smart_cfg.get("enabled", False) and reason != "manual":
        logger.debug(
            "scheduler.smart_job suppressed source=%s reason=%s cause=smart_mode_disabled",
            source_name, reason,
        )
        return None

    per_source = _resolve_per_source(smart_cfg, source_name)
    if not per_source["enabled"] and reason != "manual":
        logger.debug(
            "scheduler.smart_job suppressed source=%s reason=%s cause=per_source_disabled",
            source_name, reason,
        )
        return None

    # on_new_feed-specific guard: respect the on_new_feed.enabled toggle.
    if reason == "on_new_feed":
        on_new = smart_cfg.get("on_new_feed", {}) or {}
        if not on_new.get("enabled", False):
            logger.debug(
                "scheduler.smart_job suppressed source=%s reason=on_new_feed "
                "cause=on_new_feed_disabled", source_name,
            )
            return None

    # Idempotency: skip when a pending proposal already exists for this source.
    if await _has_pending_proposal(source_name):
        logger.info(
            "scheduler.smart_job suppressed source=%s reason=%s "
            "cause=pending_proposal_exists", source_name, reason,
        )
        return None

    # Provider precedence: per-call > per-source > global > default_provider.
    resolved_provider = (
        provider
        or per_source["provider"]
        # Fall through to llm.default_provider via the smart runner / registry.
    )
    resolved_sample_size = int(sample_size or per_source["sample_size"])

    # Late imports to avoid circulars (backend.api -> backend.scheduler).
    from backend.ingestion.jobs import job_store
    from backend.normalizer.smart_runner import run_smart_job

    job = job_store.create(source=source_name, kind="smart_proposal")
    logger.info(
        "scheduler.smart_job submitted job_id=%s source=%s reason=%s provider=%s",
        job.id, source_name, reason, resolved_provider,
    )

    async def _runner() -> None:
        async with _smart_semaphore:
            await run_smart_job(
                job_id=job.id,
                source=source_name,
                provider_name=resolved_provider,
                sample_size=resolved_sample_size,
                trigger_reason=reason,
            )

    asyncio.create_task(_runner())
    return job.id
