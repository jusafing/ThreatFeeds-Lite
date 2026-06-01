"""
Smart-mode runner (prompts-021E-3, extended in prompts-021E-4).

Extracted from ``backend.api.routes_smart._run_smart_job`` so that
``backend.scheduler.submit_smart_job`` can invoke the runner without an
import-cycle through ``backend.api.*``.

021E-3 added ``trigger_reason`` to the persisted proposal row so the
activity log in 021G can distinguish manually-triggered, scheduled, and
on-new-feed proposals at audit time.

021E-4 adds population-weighted coverage scoring and an auto-apply
decision. The scoring values and final ``outcome`` are persisted with
every proposal regardless of trigger, so the operator-facing review
queue and the audit log share one shape.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.ingestion.jobs import job_store
from backend.llm.errors import (
    LLMConfigError,
    LLMDisabledError,
    LLMProviderError,
    LLMTransportError,
)
from backend.llm.registry import get_client
from backend.normalizer.config import load_normalizer_config, save_normalizer_config
from backend.normalizer.mappings import (
    activate_version,
    create_version,
    get_active_version,
    regenerate_yaml_snapshot,
)
from backend.normalizer.proposals import (
    CONSOLIDATED_SENTINEL,
    get_proposal,
    insert_proposal,
    update_proposal_status,
)
from backend.normalizer.smart import (
    SmartModeError,
    _canonical_field_names,
    build_consolidated_prompt,
    build_prompt,
    conflicts_with_existing,
    discover_raw_field_names,
    parse_llm_response,
    raw_field_population,
    sample_consolidated_entries,
    sample_raw_entries,
    score_proposal,
    validate_proposal,
)

logger = logging.getLogger(__name__)

# Auto-apply requires a non-trivial sample so a lucky high coverage delta
# from a 2-row sample cannot silently land. Hardcoded constant rather than
# a config knob (021E-4 user decision); revisit if operators need tuning.
_AUTO_APPLY_MIN_SAMPLE_SIZE = 5


# ── approve_proposal_core (shared by HTTP and auto-apply paths) ─────────────


def _merge_with_existing_wins(
    existing: dict[str, str],
    proposal_mapping: dict[str, str],
) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
    """Overlay proposal onto existing with existing winning on conflict.

    Returns (merged, added, skipped_conflicts). Identical to the helper
    that previously lived in ``routes_smart.py``; moved here so the
    auto-apply code path (which has no HTTP context) can share it.
    """
    merged = dict(existing)
    added: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for raw_field, canonical in proposal_mapping.items():
        if raw_field in existing:
            if existing[raw_field] != canonical:
                skipped.append({
                    "raw_field": raw_field,
                    "existing_canonical": existing[raw_field],
                    "proposal_canonical": canonical,
                })
            continue
        merged[raw_field] = canonical
        added.append({"raw_field": raw_field, "canonical": canonical})
    return merged, added, skipped


async def reapply_consolidated_to_sources(sources: list[str]) -> int:
    """Re-apply the active consolidated mapping to the given feeds (prompts-038).

    Clears the existing normalized output for ``sources`` (so the dedup index +
    ``INSERT OR IGNORE`` can no longer suppress the re-insert) and resets each
    source's ``normalized`` flag to 0 so the next normalizer run reprocesses
    every raw row under the freshly-activated consolidated mapping.

    Returns the number of raw rows whose ``normalized`` flag was reset (the
    count of rows the next run will reprocess). No-op on an empty list.
    """
    sources = [s for s in (sources or []) if s]
    if not sources:
        return 0
    # Local imports avoid pulling backend.db.manager / normalizer.db at module
    # load time (smart_runner is imported from scheduler before lifespan).
    from backend.db.manager import reset_normalized_flag_for_source
    from backend.normalizer.db import delete_normalized_for_sources

    deleted = await delete_normalized_for_sources(sources)
    reset_total = 0
    for src in sources:
        reset_total += await reset_normalized_flag_for_source(src)
    logger.info(
        "reapply_consolidated_to_sources: cleared %d normalized rows, reset %d "
        "raw rows across sources=%s",
        deleted, reset_total, sources,
    )
    return reset_total


async def _approve_consolidated_core(
    proposal: dict[str, Any],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve a consolidated (global) proposal (prompts-032 Phase D).

    Creates a new ``consolidated_versions`` row from the proposal's mapping +
    feed list + field_scope, then atomically activates it. Activation demotes
    the previously-active consolidated version, so only the last approved
    consolidated mapping is ever active (Q2/Q6 "only last approved is active").

    Unlike the per-source path this does NOT merge with an existing mapping,
    touch ``manual_mappings``, or flip normalizer mode. It DOES perform the
    re-normalization sweep (prompts-038): it clears the normalized output for
    the mapping's feeds and resets their ``normalized`` flag so the next
    normalizer run re-applies the freshly-activated consolidated mapping.
    Engine application of the active consolidated mapping is wired in Phase E.

    The caller (``approve_proposal_core``) has already verified the proposal
    exists and is ``pending``.
    """
    from backend.normalizer.consolidated import (
        activate_consolidated_version,
        create_consolidated_version,
    )

    proposal_id = int(proposal["id"])
    mapping: dict[str, str] = proposal["mapping"] or {}
    sources: list[str] = proposal.get("sources") or []
    field_scope: str | None = proposal.get("field_scope")

    version_id = await create_consolidated_version(
        mapping=mapping,
        sources=sources,
        field_scope=field_scope,
        proposal_id=proposal_id,
        note=note or f"approved from consolidated proposal {proposal_id}",
    )
    await activate_consolidated_version(version_id)
    await update_proposal_status(
        proposal_id, "approved", note=note, outcome="approved",
        consolidated_version_id=version_id,
    )
    reset_rows = await reapply_consolidated_to_sources(sources)
    logger.info(
        "consolidated approve: proposal=%d new consolidated_version_id=%d "
        "sources=%s fields=%d reset_rows=%d",
        proposal_id, version_id, sources, len(mapping), reset_rows,
    )
    return {
        "proposal_id": proposal_id,
        "consolidated_version_id": version_id,
        "sources": sources,
        "field_scope": field_scope,
        "field_count": len(mapping),
        "outcome": "approved",
        "auto_applied": False,
    }


async def approve_proposal_core(
    proposal_id: int,
    *,
    note: str | None = None,
    set_mode_manual: bool = False,
    auto_applied: bool = False,
) -> dict[str, Any]:
    """Apply a proposal's mapping by creating + activating a new
    mapping_version row (prompts-021F), then regenerate the yaml snapshot
    and mark the source for re-normalization.

    This is the single canonical apply path. Both the HTTP handler
    (``POST /api/smart-mappings/proposals/{id}/approve``) and the
    021E-4 auto-apply branch call this function, so behaviour cannot drift.

    Pre-021F this wrote directly to ``normalizer-config.yaml::manual_mappings``;
    post-021F that yaml block is a write-through snapshot of all active
    mapping_version rows. The previous active version (if any) is the
    "existing" mapping for the merge — NOT the yaml file — because yaml
    is no longer authoritative.

    Re-normalization: this function only marks the source dirty
    (``reset_normalized_flag_for_source``); the scheduler's next tick
    picks it up and rebuilds rows with the new ``mapping_version_id``.
    The explicit ``POST /mappings/versions/{id}/activate`` route (Step 5)
    will additionally submit an immediate JobStore job for low-latency
    operator feedback.

    Raises:
        LookupError: proposal id not found.
        ValueError: proposal is not in 'pending' status.

    Returns the HTTP response dict shape with the new mapping_version_id.
    """
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        raise LookupError(f"Proposal {proposal_id} not found")
    if proposal["status"] != "pending":
        raise ValueError(
            f"Proposal {proposal_id} is {proposal['status']}, not pending"
        )

    # prompts-032 Phase D: consolidated (global, multi-feed) proposals follow
    # a distinct apply path — they create + activate a consolidated_versions
    # row rather than a per-source mapping_version. Auto-apply never produces
    # consolidated proposals (they are always operator-reviewed), so this
    # branch is reached only from the HTTP approve handler.
    if proposal["source_name"] == CONSOLIDATED_SENTINEL:
        return await _approve_consolidated_core(proposal, note=note)

    source = proposal["source_name"]
    proposal_mapping: dict[str, str] = proposal["mapping"] or {}

    # Existing mapping comes from the active version (021F). Fall back to
    # yaml only if no active version exists (e.g. brand-new source whose
    # migration step found nothing to seed).
    cfg = load_normalizer_config()
    active = await get_active_version(source)
    if active is not None:
        existing_for_source: dict[str, str] = dict(active.get("mapping") or {})
    else:
        yaml_mappings: dict[str, dict[str, str]] = cfg.get("manual_mappings") or {}
        existing_for_source = dict(yaml_mappings.get(source) or {})

    merged, added, skipped = _merge_with_existing_wins(
        existing_for_source, proposal_mapping,
    )
    for s in skipped:
        logger.warning(
            "smart-mode approve: keeping existing mapping for source=%s field=%s "
            "(existing=%s, proposal=%s)",
            source, s["raw_field"], s["existing_canonical"], s["proposal_canonical"],
        )

    # Create + activate the new version atomically. Local import for the
    # source-reset helper to avoid pulling backend.db.manager at module
    # load time (smart_runner is imported from scheduler before lifespan).
    from backend.db.manager import reset_normalized_flag_for_source

    note_str = (
        f"approved from proposal {proposal_id}"
        if not auto_applied
        else f"auto-applied from proposal {proposal_id}"
    )
    new_version_id = await create_version(
        source_name=source,
        mapping=merged,
        origin="proposal",
        source_proposal_id=proposal_id,
        note=note_str,
    )
    await activate_version(new_version_id)
    await regenerate_yaml_snapshot()
    reset_rows = await reset_normalized_flag_for_source(source)
    logger.info(
        "smart-mode approve: source=%s new mapping_version_id=%d "
        "(reset %d rows for re-normalization)",
        source, new_version_id, reset_rows,
    )

    # Refresh cfg in case set_mode_manual is requested. yaml has already
    # been written by regenerate_yaml_snapshot, so we re-read.
    cfg = load_normalizer_config()
    mode_was = cfg.get("mode", "auto")
    mode_changed = False
    if set_mode_manual and mode_was != "manual":
        cfg["mode"] = "manual"
        save_normalizer_config(cfg)
        mode_changed = True

    outcome = "auto_applied" if auto_applied else "approved"
    await update_proposal_status(
        proposal_id, "approved", note=note, outcome=outcome,
        mapping_version_id=new_version_id,
    )

    response: dict[str, Any] = {
        "proposal_id": proposal_id,
        "source": source,
        "added": added,
        "skipped_conflicts": skipped,
        "mode": cfg["mode"],
        "mode_changed": mode_changed,
        "auto_applied": auto_applied,
        "outcome": outcome,
        # prompts-021F additions:
        "mapping_version_id": new_version_id,
        "reset_rows": reset_rows,
    }
    if mode_was == "auto" and not set_mode_manual:
        response["hint"] = (
            "Normalizer mode is 'auto'; manual_mappings will be ignored until "
            "you set mode to 'manual' (re-send approve with set_mode_manual=true "
            "or update /api/normalizer/config)."
        )
    return response


# ── Outcome decision (021E-4) ───────────────────────────────────────────────


def _decide_outcome(
    *,
    trigger_reason: str,
    sample_count: int,
    proposed_mapping: dict[str, str],
    existing_mapping: dict[str, str],
    coverage_delta: float,
    smart_mode_cfg: dict[str, Any],
) -> tuple[str, str | None]:
    """Decide the (outcome, reason) for a freshly-scored proposal.

    Returns one of:
      * ("pending_review", reason_or_None) — manual trigger, conflicts,
        auto_apply disabled, sub-sample, or empty mapping
      * ("auto_applied",  None)            — all auto-apply preconditions met
      * ("discarded_below_threshold", str) — automated trigger above all
        gates but coverage_delta < min_coverage_delta

    The reason string is logged but not persisted in the proposal row
    (the breakdown JSON carries the score detail).
    """
    # Manual triggers never auto-apply: operator already chose to look at it.
    if trigger_reason == "manual":
        return ("pending_review", "manual trigger never auto-applies")
    if not proposed_mapping:
        return ("pending_review", "proposal mapping is empty")

    auto_cfg = (smart_mode_cfg or {}).get("auto_apply") or {}
    if not auto_cfg.get("enabled", False):
        return ("pending_review", "smart_mode.auto_apply.enabled is false")

    conflicts = conflicts_with_existing(proposed_mapping, existing_mapping)
    if conflicts:
        return (
            "pending_review",
            f"conflicts with existing operator mappings: {conflicts}",
        )

    if sample_count < _AUTO_APPLY_MIN_SAMPLE_SIZE:
        return (
            "pending_review",
            f"sample size {sample_count} < {_AUTO_APPLY_MIN_SAMPLE_SIZE} "
            f"(auto-apply minimum)",
        )

    min_delta = float(auto_cfg.get("min_coverage_delta", 0.05))
    if coverage_delta < min_delta:
        return (
            "discarded_below_threshold",
            f"coverage_delta={coverage_delta:.4f} < min_coverage_delta={min_delta}",
        )

    return ("auto_applied", None)


# ── run_smart_job ───────────────────────────────────────────────────────────


async def run_smart_job(
    *,
    job_id: str,
    source: str,
    provider_name: str | None,
    sample_size: int,
    trigger_reason: str,
) -> None:
    """Drive one smart-mode proposal end-to-end.

    Step transitions reuse the existing JobStep literals:
        fetching    → acquire client + sample
        parsing     → build prompt
        normalising → LLM call
        inserting   → parse + validate + score + persist + maybe auto-apply
        done        → terminal
    """
    try:
        job_store.update_step(job_id, "fetching")
        try:
            client = get_client(provider_name)
        except LLMDisabledError as exc:
            job_store.fail(job_id, f"LLM disabled: {exc!s}")
            return
        except LLMConfigError as exc:
            job_store.fail(job_id, f"LLM config error: {exc!s}")
            return

        try:
            samples = await sample_raw_entries(source, sample_size=sample_size)
        except SmartModeError as exc:
            job_store.fail(job_id, str(exc))
            return
        raw_fields = discover_raw_field_names(samples)
        canonical_fields = _canonical_field_names()

        job_store.update_step(job_id, "parsing")
        system_prompt, user_prompt = build_prompt(
            source_name=source,
            samples=samples,
            raw_fields=raw_fields,
            canonical_fields=canonical_fields,
        )

        job_store.update_step(job_id, "normalising")
        try:
            response_text = await asyncio.to_thread(
                client.complete, user_prompt, system=system_prompt,
                max_tokens=1024, temperature=0.0,
            )
        except (LLMTransportError, LLMProviderError) as exc:
            # prompts-037: capture the raw request + full HTTP response (from
            # the exception body on HTTP errors) so the error card can show what
            # the server actually returned.
            req_raw, resp_json = client.last_exchange_raw(exc)
            # Persist as an error proposal for audit.
            await insert_proposal(
                source_name=source,
                provider_name=client.name,
                model=client.model,
                sample_size=len(samples),
                raw_fields=raw_fields,
                mapping={},
                prompt_system=system_prompt,
                prompt_user=user_prompt,
                llm_response_raw="",
                status="error",
                trigger_reason=trigger_reason,
                outcome="error",
                llm_request_raw=req_raw,
                llm_response_json=resp_json,
            )
            job_store.fail(job_id, f"LLM call failed: {exc!s}")
            return

        # prompts-037: the call succeeded — capture the raw request + full HTTP
        # response envelope once and reuse it on every persisted proposal below.
        req_raw, resp_json = client.last_exchange_raw()

        job_store.update_step(job_id, "inserting")
        try:
            raw_mapping = parse_llm_response(response_text)
        except SmartModeError as exc:
            await insert_proposal(
                source_name=source,
                provider_name=client.name,
                model=client.model,
                sample_size=len(samples),
                raw_fields=raw_fields,
                mapping={},
                prompt_system=system_prompt,
                prompt_user=user_prompt,
                llm_response_raw=response_text or "",
                status="error",
                trigger_reason=trigger_reason,
                outcome="error",
                llm_request_raw=req_raw,
                llm_response_json=resp_json,
            )
            job_store.fail(job_id, f"parse failed: {exc!s}")
            return

        cleaned = validate_proposal(raw_mapping, raw_fields, canonical_fields)

        # ── 021E-4: scoring + outcome decision ──────────────────────────
        cfg = load_normalizer_config()
        smart_mode_cfg = cfg.get("smart_mode") or {}
        all_mappings: dict[str, dict[str, str]] = cfg.get("manual_mappings") or {}
        existing_for_source: dict[str, str] = dict(all_mappings.get(source) or {})

        population = raw_field_population(samples)
        cov_before, cov_after, cov_delta = score_proposal(
            existing_for_source, cleaned, population,
        )
        breakdown = {
            "coverage_before": cov_before,
            "coverage_after": cov_after,
            "coverage_delta": cov_delta,
            "raw_field_population": population,
        }

        outcome, reason = _decide_outcome(
            trigger_reason=trigger_reason,
            sample_count=len(samples),
            proposed_mapping=cleaned,
            existing_mapping=existing_for_source,
            coverage_delta=cov_delta,
            smart_mode_cfg=smart_mode_cfg,
        )

        if outcome == "discarded_below_threshold":
            # Persist with the discard outcome; status='rejected' so the
            # legacy pending-queue filter excludes it.
            logger.warning(
                "smart-mode discarded: source=%s trigger=%s reason=%s "
                "coverage_before=%.4f after=%.4f delta=%.4f",
                source, trigger_reason, reason, cov_before, cov_after, cov_delta,
            )
            proposal_id = await insert_proposal(
                source_name=source,
                provider_name=client.name,
                model=client.model,
                sample_size=len(samples),
                raw_fields=raw_fields,
                mapping=cleaned,
                prompt_system=system_prompt,
                prompt_user=user_prompt,
                llm_response_raw=response_text or "",
                status="rejected",
                trigger_reason=trigger_reason,
                score=cov_delta,
                score_breakdown=breakdown,
                outcome="discarded_below_threshold",
                auto_applied=False,
                llm_request_raw=req_raw,
                llm_response_json=resp_json,
            )
            # Mark a decided_at + note for audit.
            await update_proposal_status(
                proposal_id, "rejected",
                note=f"auto-discarded: {reason}",
                outcome="discarded_below_threshold",
            )
            job_store.complete(job_id, {
                "proposal_id": proposal_id,
                "outcome": "discarded_below_threshold",
            })
            return

        # outcome in {"pending_review", "auto_applied"} — both insert as 'pending'
        # first; auto_applied flips to 'approved' via approve_proposal_core.
        proposal_id = await insert_proposal(
            source_name=source,
            provider_name=client.name,
            model=client.model,
            sample_size=len(samples),
            raw_fields=raw_fields,
            mapping=cleaned,
            prompt_system=system_prompt,
            prompt_user=user_prompt,
            llm_response_raw=response_text or "",
            status="pending",
            trigger_reason=trigger_reason,
            score=cov_delta,
            score_breakdown=breakdown,
            outcome="pending_review",
            auto_applied=False,
            llm_request_raw=req_raw,
            llm_response_json=resp_json,
        )

        if outcome == "auto_applied":
            logger.info(
                "smart-mode auto-applying proposal %d source=%s trigger=%s "
                "coverage_delta=%.4f",
                proposal_id, source, trigger_reason, cov_delta,
            )
            try:
                await approve_proposal_core(
                    proposal_id,
                    note=f"auto-applied (trigger={trigger_reason}, "
                         f"coverage_delta={cov_delta:.4f})",
                    set_mode_manual=False,
                    auto_applied=True,
                )
            except (LookupError, ValueError) as exc:
                # Race condition (proposal already touched); demote to pending.
                logger.warning(
                    "smart-mode auto-apply failed for proposal %d: %s",
                    proposal_id, exc,
                )
                job_store.complete(job_id, {
                    "proposal_id": proposal_id,
                    "outcome": "pending_review",
                    "auto_apply_error": str(exc),
                })
                return

        job_store.complete(job_id, {
            "proposal_id": proposal_id,
            "outcome": outcome,
            "coverage_delta": cov_delta,
        })
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("smart-mode job %s crashed", job_id)
        try:
            job_store.fail(job_id, f"unexpected error: {exc!s}")
        except Exception:
            pass


# ── run_consolidated_smart_job (prompts-032 Phase C) ─────────────────────────


async def run_consolidated_smart_job(
    *,
    job_id: str,
    sources: list[str],
    provider_name: str | None,
    sample_size: int,
    field_scope: str,
    model: str | None = None,
) -> None:
    """Drive ONE consolidated (multi-feed) smart-mode proposal end-to-end.

    prompts-032 Q2/Q3: a single LLM call consolidates the **union** of raw
    fields across all selected feeds into one global ``{raw_field: canonical}``
    mapping, persisted as exactly **one** proposal row whose ``source_name`` is
    the :data:`CONSOLIDATED_SENTINEL` and whose real feed list lives in
    ``sources_json``.

    Differs from :func:`run_smart_job` in three deliberate ways:
      * samples across many feeds (``sample_consolidated_entries``);
      * NO coverage scoring and NO auto-apply — a consolidated proposal is
        always inserted ``status='pending'`` / ``outcome='pending_review'``
        for operator review (manual, global decision);
      * ``field_scope`` ('all' | 'configured') selects the canonical set
        offered to the LLM (and used to close the validated proposal):
        'configured' restricts it to ENABLED feed-fields.yaml fields, 'all'
        offers every core + custom field. Persisted for audit either way.

    Step transitions reuse the existing JobStep literals:
        fetching    → acquire client + sample across feeds
        parsing     → build consolidated prompt
        normalising → LLM call
        inserting   → parse + validate + persist (always pending)
        done        → terminal
    """
    try:
        job_store.update_step(job_id, "fetching")
        try:
            client = get_client(provider_name)
        except LLMDisabledError as exc:
            job_store.fail(job_id, f"LLM disabled: {exc!s}")
            return
        except LLMConfigError as exc:
            job_store.fail(job_id, f"LLM config error: {exc!s}")
            return

        try:
            samples, contributing = await sample_consolidated_entries(
                sources, sample_size=sample_size,
            )
        except SmartModeError as exc:
            job_store.fail(job_id, str(exc))
            return
        raw_fields = discover_raw_field_names(samples)
        canonical_fields = _canonical_field_names(field_scope)

        job_store.update_step(job_id, "parsing")
        system_prompt, user_prompt = build_consolidated_prompt(
            sources=contributing,
            samples=samples,
            raw_fields=raw_fields,
            canonical_fields=canonical_fields,
        )

        job_store.update_step(job_id, "normalising")
        # prompts-034: the consolidated call is the slow, large one. Use the
        # dedicated smart_mode budget/timeout (defaults: 8192 tokens, 600s)
        # rather than the short per-provider timeout used for Test/Discover.
        smart_cfg = (load_normalizer_config().get("smart_mode") or {})
        llm_max_tokens = int(smart_cfg.get("llm_max_tokens") or 8192)
        llm_timeout_seconds = float(smart_cfg.get("llm_timeout_seconds") or 600)
        # prompts-034: per-proposal model override. Falls back to the
        # provider's configured model. Persisted on every proposal row so the
        # audit reflects the exact model used.
        effective_model = model or client.model
        try:
            response_text = await asyncio.to_thread(
                client.complete, user_prompt, system=system_prompt,
                max_tokens=llm_max_tokens, temperature=0.0,
                timeout=llm_timeout_seconds, model=model,
            )
        except (LLMTransportError, LLMProviderError) as exc:
            # prompts-037: capture raw request + full HTTP response for the card.
            req_raw, resp_json = client.last_exchange_raw(exc)
            await insert_proposal(
                source_name=CONSOLIDATED_SENTINEL,
                provider_name=client.name,
                model=effective_model,
                sample_size=len(samples),
                raw_fields=raw_fields,
                mapping={},
                prompt_system=system_prompt,
                prompt_user=user_prompt,
                llm_response_raw="",
                status="error",
                trigger_reason="manual",
                outcome="error",
                sources=contributing,
                field_scope=field_scope,
                llm_request_raw=req_raw,
                llm_response_json=resp_json,
            )
            job_store.fail(job_id, f"LLM call failed: {exc!s}")
            return

        # prompts-037: capture the successful exchange once, reuse below.
        req_raw, resp_json = client.last_exchange_raw()

        job_store.update_step(job_id, "inserting")
        try:
            raw_mapping = parse_llm_response(response_text)
        except SmartModeError as exc:
            await insert_proposal(
                source_name=CONSOLIDATED_SENTINEL,
                provider_name=client.name,
                model=effective_model,
                sample_size=len(samples),
                raw_fields=raw_fields,
                mapping={},
                prompt_system=system_prompt,
                prompt_user=user_prompt,
                llm_response_raw=response_text or "",
                status="error",
                trigger_reason="manual",
                outcome="error",
                sources=contributing,
                field_scope=field_scope,
                llm_request_raw=req_raw,
                llm_response_json=resp_json,
            )
            job_store.fail(job_id, f"parse failed: {exc!s}")
            return

        cleaned = validate_proposal(raw_mapping, raw_fields, canonical_fields)

        # Consolidated proposals are always operator-reviewed: no scoring,
        # no auto-apply. One proposal row spanning all contributing feeds.
        proposal_id = await insert_proposal(
            source_name=CONSOLIDATED_SENTINEL,
            provider_name=client.name,
            model=effective_model,
            sample_size=len(samples),
            raw_fields=raw_fields,
            mapping=cleaned,
            prompt_system=system_prompt,
            prompt_user=user_prompt,
            llm_response_raw=response_text or "",
            status="pending",
            trigger_reason="manual",
            outcome="pending_review",
            auto_applied=False,
            sources=contributing,
            field_scope=field_scope,
            llm_request_raw=req_raw,
            llm_response_json=resp_json,
        )
        job_store.complete(job_id, {
            "proposal_id": proposal_id,
            "outcome": "pending_review",
            "sources": contributing,
        })
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("consolidated smart-mode job %s crashed", job_id)
        try:
            job_store.fail(job_id, f"unexpected error: {exc!s}")
        except Exception:
            pass
