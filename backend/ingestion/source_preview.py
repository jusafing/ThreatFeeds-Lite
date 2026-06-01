"""
Source preview — two-step add-source flow for pull-type sources.

1. build_source_preview: fetch + parse + normalise the configured URL,
   cache the normalised entries (TTL), and return a sample of 10.
2. confirm_source_preview: persist the source to sources.yaml, insert the
   cached entries, schedule periodic pulls (if applicable).
3. cancel_source_preview: drop the cache entry.

Single-process tool — in-memory store with TTL, no Redis.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Literal

import feedparser
import httpx

from backend.config.loader import load_sources, save_sources
from backend.db.manager import insert_entry
from backend.ingestion.jobs import job_store
from backend.ingestion.normaliser import normalise
from backend.ingestion.parsers import extract_entries, parse_file
from backend.ingestion.rss_pull import _map_rss_entry
from backend.models.entry import PreviewResponse

logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")

_TTL_SECONDS = 300
_SAMPLE_SIZE = 10

SourceKind = Literal["api_pull", "rss_pull", "remote_json_pull"]

# Store: { preview_id: {entries, source, kind, expires, fmt} }
_store: dict[str, dict[str, Any]] = {}


def _evict() -> None:
    now = time.monotonic()
    for k in [k for k, v in _store.items() if v["expires"] < now]:
        del _store[k]


# ── Fetch + parse (no DB writes) ────────────────────────────────────────────

async def _fetch_and_parse_api(source: dict[str, Any]) -> tuple[str, list[dict]]:
    url = source["url"]
    headers = source.get("headers", {})
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    return "json", extract_entries(payload)


async def _fetch_and_parse_rss(source: dict[str, Any]) -> tuple[str, list[dict]]:
    url = source["url"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"RSS parse error: {feed.bozo_exception}")
    name = source["name"]
    entries = [_map_rss_entry(e, name) for e in feed.entries]
    return "rss", entries


async def _fetch_and_parse_remote_json(source: dict[str, Any]) -> tuple[str, list[dict]]:
    url = source["url"]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        raw_bytes = response.content
    fmt, entries = parse_file(raw_bytes)
    return fmt, entries


_FETCHERS = {
    "api_pull": _fetch_and_parse_api,
    "rss_pull": _fetch_and_parse_rss,
    "remote_json_pull": _fetch_and_parse_remote_json,
}

_INGEST_MODE_MAP = {
    "api_pull": "api_pull",
    "rss_pull": "rss_pull",
    "remote_json_pull": "remote_json",
}


# ── Public API ──────────────────────────────────────────────────────────────

async def build_source_preview(source: dict[str, Any], kind: SourceKind) -> PreviewResponse:
    """Fetch + parse + normalise the source, cache, return sample of 10."""
    _evict()
    fetcher = _FETCHERS.get(kind)
    if fetcher is None:
        raise ValueError(f"Unknown source kind: {kind}")

    fmt, raw_entries = await fetcher(source)
    name = source["name"]
    ingest_mode = _INGEST_MODE_MAP[kind]

    normalised: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        try:
            normalised.append(
                normalise(
                    raw,
                    ingest_mode=ingest_mode,
                    source_name=name,
                    source_fields=source.get("fields"),
                )
            )
        except Exception:
            pass

    preview_id = str(uuid.uuid4())
    _store[preview_id] = {
        "entries": normalised,
        "source": source,
        "kind": kind,
        "fmt": fmt,
        "expires": time.monotonic() + _TTL_SECONDS,
    }
    audit.info(
        "source_preview_built kind=%s name=%s url=%s total=%d",
        kind, name, source.get("url"), len(normalised),
    )
    return PreviewResponse(
        preview_id=preview_id,
        source_name=name,
        format=fmt,
        total=len(normalised),
        sample=normalised[:_SAMPLE_SIZE],
        expires_in_seconds=_TTL_SECONDS,
    )


async def confirm_source_preview(preview_id: str, job_id: str | None = None) -> dict[str, Any] | None:
    """Persist source to sources.yaml and insert cached entries.

    Returns the 4-counter ingest summary, or None if the preview is unknown/expired.
    If job_id is given, progress is reported to JobStore.
    """
    _evict()
    stored = _store.pop(preview_id, None)
    if stored is None:
        return None

    source: dict[str, Any] = stored["source"]
    kind: str = stored["kind"]
    entries: list[dict[str, Any]] = stored["entries"]
    name: str = source["name"]

    # Persist to sources.yaml
    yaml_key_map = {
        "api_pull": "api_pull",
        "rss_pull": "rss_pull",
        "remote_json_pull": "remote_json_pull",
    }
    yaml_key = yaml_key_map[kind]
    data = load_sources()
    bucket: list = data.setdefault(yaml_key, [])
    if any(s.get("name") == name for s in bucket):
        # Name collision after the preview was built — caller must handle.
        return {
            "inserted": 0, "skipped": 0,
            "errors": [f"Source name '{name}' already exists"],
            "total_read": len(entries), "duplicates": 0, "discarded": len(entries),
            "format": stored["fmt"],
        }
    if kind == "remote_json_pull":
        source.setdefault("continuous", False)
        source.setdefault("interval_minutes", 15)
    bucket.append(source)
    save_sources(data)
    audit.info("source_added_via_preview kind=%s name=%s", kind, name)

    # Insert cached entries
    inserted = duplicates = discarded = 0
    errors: list[str] = []
    total_read = len(entries)

    if job_id:
        job_store.update_step(job_id, "inserting", total=total_read)

    for idx, entry in enumerate(entries, start=1):
        try:
            result = await insert_entry(name, entry)
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

    audit.info(
        "source_preview_confirmed kind=%s name=%s total_read=%d inserted=%d duplicates=%d discarded=%d",
        kind, name, total_read, inserted, duplicates, discarded,
    )
    return {
        "inserted": inserted,
        "skipped": duplicates + discarded,
        "errors": errors,
        "total_read": total_read,
        "duplicates": duplicates,
        "discarded": discarded,
        "format": stored["fmt"],
    }


def cancel_source_preview(preview_id: str) -> bool:
    """Drop a preview from cache. Returns True if the preview was found."""
    _evict()
    return _store.pop(preview_id, None) is not None
