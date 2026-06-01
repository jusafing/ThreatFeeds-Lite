"""
Remote Feed ingest — fetches a file from a remote URL and ingests it.
Supports JSON, NDJSON, CSV, and XML formats (auto-detected).
Also transparently decompresses .gz / single-member .zip responses
(prompts-021B); see backend.ingestion.decompression.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.db.manager import insert_entry
from backend.ingestion.decompression import (
    DecompressionError,
    decompress_if_needed,
)
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import parse_file

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")

# Content-type fragments that indicate a supported text/structured format.
# 'gzip'/'zip' are accepted here because the decompression layer will
# unpack them before the parser sees the bytes (prompts-021B).
_ALLOWED_CT_FRAGMENTS = (
    "json", "text", "csv", "xml", "octet-stream", "gzip", "zip",
)


async def ingest_remote_feed(
    url: str,
    source_name: str,
    source_fields: dict | None = None,
) -> dict[str, Any]:
    """
    Download a remote file, auto-detect format, parse and ingest.
    Returns {"inserted": N, "skipped": N, "errors": [...], "format": detected}.
    """
    errors: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            content_encoding = response.headers.get("content-encoding", "")
            if content_type and not any(f in content_type for f in _ALLOWED_CT_FRAGMENTS):
                raise ValueError(f"Unexpected content-type: {content_type}")
            raw_bytes = response.content
    except Exception as exc:
        msg = f"[remote_feed:{source_name}] fetch failed: {exc}"
        logger.error(msg)
        return {
            "inserted": 0, "skipped": 0, "errors": [msg], "format": "unknown",
            "total_read": 0, "duplicates": 0, "discarded": 0,
        }

    # ── Decompress if needed (prompts-021B) ────────────────────────────────
    url_filename = os.path.basename(urlparse(url).path) or None
    try:
        from backend.config.loader import load_max_decompressed_bytes
        _inner_name, raw_bytes = decompress_if_needed(
            url_filename,
            raw_bytes,
            content_type=content_type,
            content_encoding=content_encoding,
            max_bytes=load_max_decompressed_bytes(),
        )
    except DecompressionError as exc:
        msg = f"[remote_feed:{source_name}] decompression failed: {exc}"
        logger.error(msg)
        return {
            "inserted": 0, "skipped": 0, "errors": [msg], "format": "unknown",
            "total_read": 0, "duplicates": 0, "discarded": 0,
        }

    try:
        detected_fmt, entries = parse_file(raw_bytes)
    except ValueError as exc:
        return {
            "inserted": 0, "skipped": 0, "errors": [str(exc)], "format": "unknown",
            "total_read": 0, "duplicates": 0, "discarded": 0,
        }

    inserted = duplicates = discarded = 0
    total_read = len(entries)

    for raw in entries:
        if not isinstance(raw, dict):
            discarded += 1
            continue
        try:
            normalised = normalise(
                raw,
                ingest_mode="remote_feed",
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

    skipped = duplicates + discarded
    logger.info(
        f"[remote_feed:{source_name}] fmt={detected_fmt} read={total_read} inserted={inserted} duplicates={duplicates} discarded={discarded}"
    )
    audit.info(
        "ingest source=%s mode=remote_feed fmt=%s total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
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


# ---------------------------------------------------------------------------
# Back-compat shim — old name still works for any residual callers
# ---------------------------------------------------------------------------
async def ingest_remote_json(
    url: str,
    source_name: str,
    source_fields: dict | None = None,
) -> dict[str, Any]:
    """Deprecated alias for ingest_remote_feed."""
    result = await ingest_remote_feed(url, source_name, source_fields=source_fields)
    result.pop("format", None)
    return result
