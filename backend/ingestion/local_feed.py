"""
Local Feed ingest — reads a file uploaded by the user.
Supports JSON, NDJSON, CSV, and XML formats (auto-detected or explicit).
Also transparently decompresses .gz and single-member .zip uploads
(prompts-021B); see backend.ingestion.decompression.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.db.manager import insert_entry
from backend.ingestion.decompression import (
    DecompressionError,
    decompress_if_needed,
)
from backend.ingestion.jobs import job_store
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import parse_file

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")


async def ingest_local_feed(
    file_bytes: bytes,
    source_name: str,
    source_fields: dict | None = None,
    fmt: str | None = None,
    job_id: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Parse and ingest a local file upload (JSON, NDJSON, CSV, XML).
    Returns {"inserted": N, "skipped": N, "errors": [...], "format": detected}.

    When ``filename`` ends in ``.gz`` or ``.zip``, the body is
    transparently decompressed before parsing (prompts-021B).
    Compression errors are surfaced through the same error channel
    as parser errors.

    If job_id is provided, progress is reported to JobStore at the major
    step boundaries (parsing / normalising / inserting / done).
    """
    errors: list[str] = []

    if job_id:
        job_store.update_step(job_id, "parsing")

    # ── Decompress if needed (prompts-021B) ────────────────────────────────
    try:
        from backend.config.loader import load_max_decompressed_bytes
        _inner_name, file_bytes = decompress_if_needed(
            filename, file_bytes, max_bytes=load_max_decompressed_bytes(),
        )
    except DecompressionError as exc:
        return {
            "inserted": 0, "skipped": 0, "errors": [str(exc)], "format": fmt or "unknown",
            "total_read": 0, "duplicates": 0, "discarded": 0,
        }

    try:
        detected_fmt, entries = parse_file(file_bytes, fmt=fmt)
    except ValueError as exc:
        return {
            "inserted": 0, "skipped": 0, "errors": [str(exc)], "format": fmt or "unknown",
            "total_read": 0, "duplicates": 0, "discarded": 0,
        }

    inserted = duplicates = discarded = 0
    total_read = len(entries)

    if job_id:
        job_store.update_step(job_id, "normalising", total=total_read)

    if job_id:
        job_store.update_step(job_id, "inserting", total=total_read)

    for idx, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict):
            discarded += 1
            continue
        try:
            normalised = normalise(
                raw,
                ingest_mode="local_feed",
                source_name=source_name,
                source_fields=source_fields,
            )
            result = await insert_entry(source_name, normalised)
            if result == "inserted":
                inserted += 1
            elif result == "duplicate":
                duplicates += 1
            else:
                discarded += 1
        except Exception as exc:
            errors.append(str(exc))
            discarded += 1

        if job_id and idx % 50 == 0:
            job_store.update_progress(job_id, idx)

    if job_id:
        job_store.update_progress(job_id, total_read)

    skipped = duplicates + discarded
    logger.info(
        f"[local_feed:{source_name}] fmt={detected_fmt} read={total_read} inserted={inserted} duplicates={duplicates} discarded={discarded}"
    )
    audit.info(
        "ingest source=%s mode=local_feed fmt=%s total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
        source_name, detected_fmt, total_read, inserted, duplicates, discarded, len(errors),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "format": detected_fmt,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
    }
