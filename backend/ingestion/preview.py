"""
Preview — two-step ingest flow.
1. build_preview: parse + normalise entries, store in memory, return a summary + sample.
2. confirm_preview: persist the pre-parsed entries to SQLite.

In-memory store uses a 5-minute TTL (single-process tool; no Redis needed).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from backend.db.manager import insert_entry
from backend.ingestion.decompression import (
    DecompressionError,
    decompress_if_needed,
)
from backend.ingestion.jobs import job_store
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import parse_file
from backend.models.entry import PreviewResponse

_TTL_SECONDS = 300
_SAMPLE_SIZE = 10

# Store: { preview_id: {"entries": [...], "source_name": str, "format": str, "expires": float} }
_store: dict[str, dict[str, Any]] = {}


def _evict() -> None:
    """Remove expired entries from the in-memory store."""
    now = time.monotonic()
    expired = [k for k, v in _store.items() if v["expires"] < now]
    for k in expired:
        del _store[k]


async def build_preview(
    file_bytes: bytes,
    source_name: str,
    origin: str = "local",
    source_fields: dict | None = None,
    filename: str | None = None,
) -> PreviewResponse:
    """
    Parse bytes, normalise each entry, and cache for later confirm.
    Returns a PreviewResponse with a preview_id, detected format,
    total count, and a sample of up to 10 normalised entries.
    Raises ValueError (propagated from parsers, including decompression
    errors raised as ``DecompressionError`` which subclass ``ValueError``)
    on malformed input.

    When ``filename`` ends in ``.gz`` or ``.zip``, the body is
    transparently decompressed before parsing (prompts-021B).
    """
    _evict()

    # ── Decompress if needed (prompts-021B) ────────────────────────────────
    try:
        from backend.config.loader import load_max_decompressed_bytes
        _inner_name, file_bytes = decompress_if_needed(
            filename, file_bytes, max_bytes=load_max_decompressed_bytes(),
        )
    except DecompressionError:
        # Re-raise as-is; DecompressionError is a ValueError subclass so
        # callers that already translate ValueError → HTTP 400 continue
        # to work unchanged.
        raise

    detected_fmt, raw_entries = parse_file(file_bytes)

    normalised_entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        try:
            normalised = normalise(
                raw,
                ingest_mode=f"preview_{origin}",
                source_name=source_name,
                source_fields=source_fields,
            )
            normalised_entries.append(normalised)
        except Exception:
            pass

    preview_id = str(uuid.uuid4())
    _store[preview_id] = {
        "entries": normalised_entries,
        "source_name": source_name,
        "format": detected_fmt,
        "expires": time.monotonic() + _TTL_SECONDS,
    }

    return PreviewResponse(
        preview_id=preview_id,
        source_name=source_name,
        format=detected_fmt,
        total=len(normalised_entries),
        sample=normalised_entries[:_SAMPLE_SIZE],
        expires_in_seconds=_TTL_SECONDS,
    )


async def confirm_preview(preview_id: str, job_id: str | None = None) -> dict[str, Any] | None:
    """
    Persist the pre-parsed entries from a preview to SQLite.
    Returns ingest result dict, or None if the preview is not found/expired.

    If job_id is given, progress is reported to JobStore.
    """
    _evict()
    stored = _store.pop(preview_id, None)
    if stored is None:
        return None

    source_name: str = stored["source_name"]
    entries: list[dict[str, Any]] = stored["entries"]
    detected_fmt: str = stored["format"]

    inserted = duplicates = discarded = 0
    errors: list[str] = []
    total_read = len(entries)

    if job_id:
        job_store.update_step(job_id, "inserting", total=total_read)

    for idx, entry in enumerate(entries, start=1):
        try:
            result = await insert_entry(source_name, entry)
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

    return {
        "inserted": inserted,
        "skipped": duplicates + discarded,
        "errors": errors,
        "format": detected_fmt,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
    }
