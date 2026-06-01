"""
Remote JSON ingest — fetches a JSON file from a remote URL and ingests it.
Validates that the content is plain JSON before parsing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from backend.db.manager import insert_entry
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import parse_file

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")


async def ingest_remote_json(url: str, source_name: str, source_fields: dict | None = None) -> dict[str, Any]:
    """
    Download a remote JSON file, validate, parse, and ingest it.
    Returns {"inserted": N, "skipped": N, "errors": [...]}.
    """
    errors: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            # Guard against non-JSON content types
            content_type = response.headers.get("content-type", "")
            if content_type and "json" not in content_type and "text" not in content_type:
                raise ValueError(f"Unexpected content-type: {content_type}")
            raw_bytes = response.content
    except Exception as exc:
        msg = f"[remote_json:{source_name}] fetch failed: {exc}"
        logger.error(msg)
        return {"inserted": 0, "skipped": 0, "errors": [msg],
                "total_read": 0, "duplicates": 0, "discarded": 0}

    try:
        _fmt, entries = parse_file(raw_bytes)
    except ValueError as exc:
        return {"inserted": 0, "skipped": 0, "errors": [str(exc)],
                "total_read": 0, "duplicates": 0, "discarded": 0}

    inserted = duplicates = discarded = 0
    total_read = len(entries)

    for raw in entries:
        if not isinstance(raw, dict):
            discarded += 1
            continue
        try:
            normalised = normalise(raw, ingest_mode="remote_json", source_name=source_name, source_fields=source_fields)
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
    logger.info(f"[remote_json:{source_name}] read={total_read} inserted={inserted} duplicates={duplicates} discarded={discarded}")
    audit.info(
        "ingest source=%s mode=remote_json total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
        source_name, total_read, inserted, duplicates, discarded, len(errors),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
    }
