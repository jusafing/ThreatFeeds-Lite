"""
Smart-mode mapping proposal routes (prompts-021E-1).

Endpoints:
  * POST /api/smart-mappings/jobs                       — spawn job
  * GET  /api/smart-mappings/jobs/{job_id}              — poll status
  * GET  /api/smart-mappings/proposals?source=&status=  — list
  * GET  /api/smart-mappings/proposals/{id}             — detail
  * POST /api/smart-mappings/proposals/{id}/approve     — overlay-merge
  * POST /api/smart-mappings/proposals/{id}/reject       — mark rejected
  * POST /api/smart-mappings/proposals/{id}/reenable     — rejected → pending
  * GET  /api/smart-mappings/active                      — active consolidated mapping
  * POST /api/smart-mappings/dry-run                    — prompt preview, no LLM call

Approval semantics:
  * Existing operator entries in manual_mappings WIN over proposal entries
    on conflict (logged at WARNING). New entries are merged in.
  * The normalizer mode is NOT auto-switched. Body flag
    ``set_mode_manual=true`` opts in to flipping ``mode: auto`` → ``manual``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.ingestion.jobs import job_store
from backend.llm.errors import (
    LLMConfigError,
    LLMDisabledError,
    LLMProviderError,
    LLMTransportError,
)
from backend.llm.registry import get_client
from backend.normalizer.config import load_normalizer_config, save_normalizer_config
from backend.normalizer.consolidated import get_active_consolidated
from backend.normalizer.proposals import (
    CONSOLIDATED_SENTINEL,
    archive_proposal,
    get_proposal,
    insert_proposal,
    list_proposals,
    update_proposal_status,
)
from backend.normalizer.smart import (
    SmartModeError,
    _DEFAULT_SAMPLE_SIZE,
    _canonical_field_names,
    build_prompt,
    discover_raw_field_names,
    parse_llm_response,
    sample_raw_entries,
    validate_proposal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/smart-mappings", tags=["smart-mappings"])

_MAX_SAMPLE_SIZE = 100
_VALID_FIELD_SCOPES = frozenset({"all", "configured"})


def _clamp_sample_size(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="sample_size must be an integer")
    if n <= 0:
        raise HTTPException(status_code=400, detail="sample_size must be > 0")
    if n > _MAX_SAMPLE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"sample_size must be <= {_MAX_SAMPLE_SIZE}",
        )
    return n


def _require_source(body: dict[str, Any]) -> str:
    src = body.get("source")
    if not isinstance(src, str) or not src.strip():
        raise HTTPException(status_code=400, detail="'source' is required")
    return src.strip()


def _require_sources(body: dict[str, Any]) -> list[str]:
    """Validate + normalize the ``sources`` list for a consolidated job.

    prompts-032 Phase C: a consolidated proposal spans multiple feeds. The
    list must be non-empty and contain non-empty strings; duplicates are
    collapsed preserving first-seen order. Data-existence ("known") is
    enforced downstream by the runner, which skips feeds with no entries and
    fails the job only if NONE of the selected feeds yield samples — so an
    unknown/empty feed surfaces as a clear job error rather than a blanket
    request rejection here.
    """
    raw = body.get("sources")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(
            status_code=400, detail="'sources' must be a non-empty list",
        )
    sources: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if not isinstance(s, str) or not s.strip():
            raise HTTPException(
                status_code=400,
                detail="each entry in 'sources' must be a non-empty string",
            )
        name = s.strip()
        if name not in seen:
            seen.add(name)
            sources.append(name)
    return sources


def _validate_field_scope(body: dict[str, Any]) -> str:
    """Validate the optional ``field_scope`` enum (defaults to 'all')."""
    fs = body.get("field_scope", "all")
    if fs is None:
        return "all"
    if not isinstance(fs, str) or fs not in _VALID_FIELD_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="'field_scope' must be one of: all, configured",
        )
    return fs


# ── Dry-run (prompt preview) ───────────────────────────────────────────────


@router.post("/dry-run")
async def dry_run(body: dict[str, Any]) -> dict[str, Any]:
    """Return the prompt that would be sent to the LLM. No LLM call is made."""
    source = _require_source(body)
    sample_size = _clamp_sample_size(body.get("sample_size", _DEFAULT_SAMPLE_SIZE))

    try:
        samples = await sample_raw_entries(source, sample_size=sample_size)
    except SmartModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    raw_fields = discover_raw_field_names(samples)
    canonical_fields = _canonical_field_names()
    system_prompt, user_prompt = build_prompt(
        source_name=source,
        samples=samples,
        raw_fields=raw_fields,
        canonical_fields=canonical_fields,
    )
    return {
        "source": source,
        "sample_size": len(samples),
        "raw_fields": raw_fields,
        "canonical_fields": canonical_fields,
        "prompt_system": system_prompt,
        "prompt_user": user_prompt,
    }


# ── Job creation + polling ─────────────────────────────────────────────────


async def _run_consolidated_job(
    *,
    job_id: str,
    sources: list[str],
    provider_name: str | None,
    sample_size: int,
    field_scope: str,
    model: str | None = None,
) -> None:
    """Thin shim — delegates to the shared smart_runner (prompts-032 Phase C).

    Local import avoids pulling smart_runner (and, transitively, the
    scheduler) at module import time.
    """
    from backend.normalizer.smart_runner import run_consolidated_smart_job
    await run_consolidated_smart_job(
        job_id=job_id,
        sources=sources,
        provider_name=provider_name,
        sample_size=sample_size,
        field_scope=field_scope,
        model=model,
    )


@router.post("/jobs")
async def create_job(body: dict[str, Any]) -> dict[str, Any]:
    """Spawn ONE consolidated smart-mode proposal job spanning multiple feeds.

    prompts-032 Phase C: the manual UI flow is now consolidated/global. The
    request body is ``{sources: string[], provider?, sample_size?,
    field_scope?}`` and produces exactly one proposal row (source_name =
    ``__consolidated__``). The automatic per-source path
    (``scheduler.submit_smart_job`` → ``run_smart_job``) is unchanged.
    """
    sources = _require_sources(body)
    sample_size = _clamp_sample_size(body.get("sample_size", _DEFAULT_SAMPLE_SIZE))
    field_scope = _validate_field_scope(body)
    provider_name = body.get("provider")
    if provider_name is not None and not isinstance(provider_name, str):
        raise HTTPException(status_code=400, detail="'provider' must be a string")
    if isinstance(provider_name, str) and not provider_name.strip():
        provider_name = None  # blank → configured default

    # prompts-034: optional per-proposal model override. When omitted the
    # provider's configured model is used. The UI offers only tested models.
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        raise HTTPException(status_code=400, detail="'model' must be a string")
    if isinstance(model, str):
        model = model.strip() or None

    # Reject up-front when LLM is disabled — surfaced as 409 to the client.
    try:
        get_client(provider_name)  # constructed only for validation
    except LLMDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job = job_store.create(source=CONSOLIDATED_SENTINEL, kind="smart_proposal")  # type: ignore[arg-type]
    job_store.set_running(job.id)
    asyncio.create_task(
        _run_consolidated_job(
            job_id=job.id,
            sources=sources,
            provider_name=provider_name,
            sample_size=sample_size,
            field_scope=field_scope,
            model=model,
        )
    )
    return {
        "job_id": job.id,
        "sources": sources,
        "provider": provider_name,
        "sample_size": sample_size,
        "field_scope": field_scope,
        "model": model,
        "state": "running",
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job.to_dict()


# ── Proposals — list, detail, approve, reject ──────────────────────────────


@router.get("/proposals")
async def get_proposals(
    source: str | None = Query(None),
    status: str | None = Query(None),
    outcome: str | None = Query(
        None,
        description=(
            "Filter by outcome. Defaults to 'pending_review' (hides "
            "auto_applied + discarded). Pass 'all' to disable filtering."
        ),
    ),
    limit: int = Query(100, ge=1, le=500),
    archived: str = Query(
        "active",
        description=(
            "Archive filter: 'active' (default, hides archived) | 'all' "
            "(archived + active) | 'only' (archived only)."
        ),
    ),
) -> list[dict[str, Any]]:
    # 021E-4 default: hide auto_applied + discarded from the review queue.
    # Operators wanting the audit view pass outcome=all or a specific value.
    effective_outcome = outcome if outcome is not None else "pending_review"
    # prompts-034 default: hide archived rows unless explicitly requested.
    archived_map: dict[str, bool | None] = {
        "active": False, "all": None, "only": True,
    }
    if archived not in archived_map:
        raise HTTPException(
            status_code=400,
            detail=f"invalid archived filter: {archived!r} "
                   "(expected active|all|only)",
        )
    try:
        return await list_proposals(
            source=source, status=status, outcome=effective_outcome,
            limit=limit, archived=archived_map[archived],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/proposals/{proposal_id}")
async def get_proposal_route(proposal_id: int) -> dict[str, Any]:
    p = await get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    return p


def _merge_with_existing_wins(
    existing: dict[str, str],
    proposal_mapping: dict[str, str],
) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
    """Compatibility shim — the canonical implementation now lives in
    ``backend.normalizer.smart_runner`` so the auto-apply code path can
    share it. Kept here so any external import (tests) does not break."""
    from backend.normalizer import smart_runner as _sr
    return _sr._merge_with_existing_wins(existing, proposal_mapping)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """HTTP handler — delegates to ``smart_runner.approve_proposal_core``.

    021E-4 extracted the merge-and-persist body into ``smart_runner`` so
    the scheduled auto-apply branch can reuse it without an HTTP
    round-trip.
    """
    from backend.normalizer.smart_runner import approve_proposal_core
    body = body or {}
    note = body.get("note")
    set_mode_manual = bool(body.get("set_mode_manual", False))
    try:
        return await approve_proposal_core(
            proposal_id,
            note=note,
            set_mode_manual=set_mode_manual,
            auto_applied=False,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    note = body.get("note")
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    if proposal["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal {proposal_id} is {proposal['status']}, not pending",
        )
    await update_proposal_status(proposal_id, "rejected", note=note, outcome="rejected")
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.post("/proposals/{proposal_id}/archive")
async def archive_proposal_route(
    proposal_id: int, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Archive a proposal (prompts-034).

    Hides the proposal from the default review/list views without deleting it
    (it stays for audit). Works on a proposal in any status. An optional
    ``note`` is recorded for provenance.
    """
    body = body or {}
    note = body.get("note")
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404, detail=f"Proposal {proposal_id} not found"
        )
    # prompts-039: the proposal backing the active consolidated mapping is
    # locked — archiving it would orphan the live mapping. Deactivate first.
    active = await get_active_consolidated()
    if active is not None and active.get("proposal_id") == proposal_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot archive the proposal backing the active consolidated "
                "mapping. Deactivate the mapping first."
            ),
        )
    await archive_proposal(proposal_id, note=note)
    return {"proposal_id": proposal_id, "archived": True}


@router.post("/proposals/{proposal_id}/reenable")
async def reenable_proposal(
    proposal_id: int, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return an operator-rejected proposal to the review queue (prompts-032 D).

    Transition: ``rejected → pending`` (outcome ``rejected → pending_review``)
    so it can be approved again. Guarded so ONLY operator-rejected proposals
    qualify:
      * status must be 'rejected'              → otherwise 409
      * outcome must NOT be the automated
        'discarded_below_threshold' signal     → otherwise 409

    Auto-discarded proposals are an automated quality signal, not an operator
    decision, so re-enabling them is refused (Q6).
    """
    body = body or {}
    note = body.get("note")
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    if proposal["status"] != "rejected":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proposal {proposal_id} is {proposal['status']}, not rejected; "
                "only rejected proposals can be re-enabled"
            ),
        )
    if proposal.get("outcome") == "discarded_below_threshold":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proposal {proposal_id} was auto-discarded "
                "(below coverage threshold), not operator-rejected; "
                "it cannot be re-enabled"
            ),
        )
    await update_proposal_status(
        proposal_id, "pending", note=note, outcome="pending_review",
    )
    return {"proposal_id": proposal_id, "status": "pending"}


@router.get("/active")
async def get_active_consolidated_route() -> dict[str, Any]:
    """Return a summary of the single active consolidated mapping, or null.

    prompts-032 Phase D: drives the prominent active-mapping card above the
    proposal list. Returns ``{"active": null}`` when no consolidated mapping
    has been approved yet.
    """
    active = await get_active_consolidated()
    if active is None:
        return {"active": None}
    # prompts-038: surface the originating proposal's human name on the card.
    proposal_name: str | None = None
    proposal_id = active.get("proposal_id")
    if proposal_id is not None:
        proposal = await get_proposal(int(proposal_id))
        if proposal is not None:
            proposal_name = proposal.get("proposal_name")
    return {
        "active": {
            "id": active["id"],
            "sources": active.get("sources") or [],
            "field_count": len(active.get("mapping") or {}),
            "field_scope": active.get("field_scope"),
            "proposal_id": active.get("proposal_id"),
            "proposal_name": proposal_name,
            "created_at": active.get("created_at"),
            "note": active.get("note"),
            # prompts-039: expose the full {raw_field: canonical} map so the
            # expanded active card can render the mapping definition.
            "mapping": active.get("mapping") or {},
        }
    }


@router.post("/active/run")
async def run_active_consolidated_route() -> dict[str, Any]:
    """Re-apply the active consolidated mapping on demand (prompts-038).

    Clears the normalized output for the active mapping's feeds and resets
    their ``normalized`` flag, then runs the normalizer immediately so the
    operator sees the freshly re-applied results. Returns the normalizer run
    counters (``processed`` / ``inserted`` / ``errors`` / ...).

    Returns 409 when no consolidated mapping is active.
    """
    from backend.normalizer.engine import run_normalizer
    from backend.normalizer.smart_runner import reapply_consolidated_to_sources

    active = await get_active_consolidated()
    if active is None:
        raise HTTPException(
            status_code=409, detail="No active consolidated mapping to run",
        )
    sources = active.get("sources") or []
    reset_rows = await reapply_consolidated_to_sources(sources)
    result = await run_normalizer(trigger="reapply")
    logger.info(
        "active consolidated run: version=%s sources=%s reset_rows=%d result=%s",
        active.get("id"), sources, reset_rows, result,
    )
    return {"reset_rows": reset_rows, **result}
