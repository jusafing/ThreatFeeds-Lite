"""
Push listener ingestion — handles normalisation of pushed JSON payloads.
The actual HTTP route lives in api/routes_ingest.py.
This module provides the processing logic.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.db.manager import insert_entry
from backend.ingestion.jobs import job_store
from backend.ingestion.normaliser import normalise
from backend.config.loader import load_sources

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")


def _payload_bytes(payload: Any) -> int:
    """Best-effort byte size of the received JSON payload (for receipt logging)."""
    try:
        return len(json.dumps(payload, default=str).encode("utf-8"))
    except Exception:  # pragma: no cover — defensive, never block ingestion
        return -1


async def process_push(
    payload: dict[str, Any] | list[dict[str, Any]],
    source_name: str,
    job_id: str | None = None,
) -> dict[str, int]:
    """
    Normalise and store a pushed JSON payload.
    Accepts either a single entry dict or a list of entry dicts.
    Returns {"inserted": N, "skipped": N}.

    If job_id is given, progress is reported to JobStore.

    Every received payload is logged: an INFO receipt summary (source, event
    count, byte size) on the audit log, plus the full JSON body at DEBUG (kept
    below the default INFO threshold so payloads are not flooded/leaked unless
    debug logging is explicitly enabled). Per-entry failures are counted and
    logged with a full traceback in the application log.
    """
    entries = payload if isinstance(payload, list) else [payload]
    inserted = duplicates = discarded = errors = 0
    total_read = len(entries)

    # Receipt logging — record that a payload arrived before processing it.
    audit.info(
        "listener_receive source=%s events=%d bytes=%d",
        source_name, total_read, _payload_bytes(payload),
    )
    logger.debug("listener_payload source=%s body=%s", source_name, payload)

    listener_fields = load_sources().get("listener", {}).get("fields") or None

    if job_id:
        job_store.update_step(job_id, "inserting", total=total_read)

    for idx, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict):
            errors += 1
            discarded += 1
            logger.error(
                "listener_entry_invalid source=%s idx=%d: expected JSON object, got %s",
                source_name, idx, type(raw).__name__,
            )
            continue
        try:
            normalised = normalise(
                raw, ingest_mode="push", source_name=source_name, source_fields=listener_fields,
            )
            result = await insert_entry(source_name, normalised)
        except Exception:
            errors += 1
            discarded += 1
            logger.exception(
                "listener_entry_failed source=%s idx=%d", source_name, idx,
            )
            continue
        if result == "inserted":
            inserted += 1
        elif result == "duplicate":
            duplicates += 1
        else:
            errors += 1
            discarded += 1
            logger.error(
                "listener_entry_rejected source=%s idx=%d result=%s",
                source_name, idx, result,
            )
        if job_id and idx % 50 == 0:
            job_store.update_progress(job_id, idx)

    if job_id:
        job_store.update_progress(job_id, total_read)

    skipped = duplicates + discarded
    audit.info(
        "ingest source=%s mode=push total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
        source_name, total_read, inserted, duplicates, discarded, errors,
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
    }
