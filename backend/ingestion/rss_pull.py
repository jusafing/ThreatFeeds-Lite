"""
RSS pull ingestion — fetches and normalises RSS/Atom feeds on a schedule.
Maps common RSS fields to the supported field schema.
"""
from __future__ import annotations

import logging
from typing import Any

import feedparser
import httpx

from backend.db.manager import insert_entry
from backend.ingestion.normaliser import normalise

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")

# Mapping from feedparser entry attributes → our field names
_RSS_FIELD_MAP: dict[str, str] = {
    "title": "title",
    "summary": "description",
    "link": "source_url",
    "id": "source_url",          # fallback if no link
    "published": "published_at",
    "updated": "last_seen",
    "author": "actor",
    "tags": "_tags_raw",         # handled specially below
}


def _map_rss_entry(entry: Any, source_name: str) -> dict[str, Any]:
    """Convert a feedparser entry object to our flat dict format."""
    raw: dict[str, Any] = {}

    for rss_key, our_key in _RSS_FIELD_MAP.items():
        val = getattr(entry, rss_key, None)
        if val is None:
            continue
        if our_key == "_tags_raw":
            # feedparser tags is a list of dicts with 'term'
            if isinstance(val, list):
                raw["tags"] = ", ".join(t.get("term", "") for t in val if isinstance(t, dict))
        else:
            raw[our_key] = str(val) if not isinstance(val, str) else val

    raw["source"] = source_name
    return raw


async def pull_rss_source(source: dict[str, Any]) -> dict[str, int]:
    """
    Pull from a single RSS source config entry.
    source dict keys: name, url
    Returns {"inserted": N, "skipped": N, "errors": [...]}.
    """
    name: str = source["name"]
    url: str = source["url"]
    inserted = duplicates = discarded = 0
    errors: list[str] = []

    try:
        # feedparser can handle URLs directly but we use httpx for consistent
        # timeout/error handling then pass the text content
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.text
    except Exception as exc:
        msg = f"[rss_pull:{name}] fetch failed: {exc}"
        logger.error(msg)
        return {"inserted": 0, "skipped": 0, "errors": [msg],
                "total_read": 0, "duplicates": 0, "discarded": 0}

    feed = feedparser.parse(content)

    if feed.bozo and not feed.entries:
        msg = f"[rss_pull:{name}] feed parse error: {feed.bozo_exception}"
        logger.warning(msg)
        return {"inserted": 0, "skipped": 0, "errors": [msg],
                "total_read": 0, "duplicates": 0, "discarded": 0}

    total_read = len(feed.entries)

    for entry in feed.entries:
        try:
            raw = _map_rss_entry(entry, name)
            normalised = normalise(raw, ingest_mode="rss_pull", source_name=name, source_fields=source.get("fields"))
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
    logger.info(f"[rss_pull:{name}] read={total_read} inserted={inserted} duplicates={duplicates} discarded={discarded}")
    audit.info(
        "ingest source=%s mode=rss_pull total_read=%d inserted=%d duplicates=%d discarded=%d errors=%d",
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
