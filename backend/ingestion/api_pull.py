"""
API pull ingestion — fetches JSON from a configured remote URL on a schedule.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.db.manager import insert_entry
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import extract_entries

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")


async def pull_api_source(source: dict[str, Any]) -> dict[str, int]:
    """
    Pull from a single API source config entry.
    source dict keys: name, url, headers (optional)
    Returns {"inserted": N, "skipped": N, "errors": [...]}.
    """
    name: str = source["name"]
    url: str = source["url"]
    headers: dict[str, str] = source.get("headers", {})
    inserted = duplicates = discarded = 0
    errors: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        msg = f"[api_pull:{name}] fetch failed: {exc}"
        logger.error(msg)
        return {"inserted": 0, "skipped": 0, "errors": [msg],
                "total_read": 0, "duplicates": 0, "discarded": 0}

    entries = extract_entries(payload)
    total_read = len(entries)

    for raw in entries:
        if not isinstance(raw, dict):
            discarded += 1
            continue
        try:
            normalised = normalise(raw, ingest_mode="api_pull", source_name=name, source_fields=source.get("fields"))
            result = await insert_entry(name, normalised)
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
    logger.info(f"[api_pull:{name}] read={total_read} inserted={inserted} duplicates={duplicates} discarded={discarded}")
    audit.info(
        "ingest source=%s mode=api_pull total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
        name, total_read, inserted, duplicates, discarded, len(errors),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
    }
