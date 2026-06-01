"""
Sources routes — CRUD for sources.yaml (listener, api_pull, rss_pull).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.config.loader import (
    load_default_sources,
    load_fields,
    load_sources,
    save_sources,
)
from backend.db.manager import get_entry_count_for_source
from backend.ingestion.api_pull import pull_api_source
from backend.ingestion.jobs import job_store
from backend.ingestion.refresh import run_tracked_pull
from backend.ingestion.remote_feed import ingest_remote_feed
from backend.ingestion.rss_pull import pull_rss_source
from backend.ingestion.source_preview import (
    build_source_preview,
    cancel_source_preview,
    confirm_source_preview,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])
logger = logging.getLogger(__name__)
audit = logging.getLogger("backend.audit")


# ── Secret redaction (prompts-045 security audit) ─────────────────────────────
#
# Per-source `headers` carry credentials (API keys, Authorization tokens). The
# list/echo responses are admin-only, but we still mask the header VALUES in
# every read response so credentials are never sent to the browser — defense in
# depth against a future allowlist regression or an over-broad role.
#
# Because the editor round-trips the whole source object (toggle / edit re-PUT
# the masked object), update endpoints restore any still-masked header value
# from the stored source before persisting, so editing never wipes a secret.
_REDACTED = "__redacted__"


def _redact_source(src: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of a source with header values masked."""
    headers = src.get("headers")
    if not isinstance(headers, dict) or not headers:
        return src
    out = dict(src)
    out["headers"] = {k: _REDACTED for k in headers}
    return out


def _redact_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_source(s) for s in sources]


def _restore_source_secrets(
    new: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Replace masked header values in an incoming update with the stored ones.

    A header whose value is still the redaction sentinel is restored from the
    existing source. A masked header with no stored counterpart is dropped
    (it was never a real value). New / changed header values pass through
    unchanged, so admins can still rotate credentials.
    """
    headers = new.get("headers")
    if not isinstance(headers, dict):
        return new
    existing_headers = existing.get("headers")
    if not isinstance(existing_headers, dict):
        existing_headers = {}
    restored: dict[str, Any] = {}
    for key, value in headers.items():
        if value == _REDACTED:
            if key in existing_headers:
                restored[key] = existing_headers[key]
            # else: masked but unknown → drop (never a real secret)
        else:
            restored[key] = value
    out = dict(new)
    out["headers"] = restored
    return out


# ── Auto-ingest helpers ───────────────────────────────────────────────────────

def _should_auto_ingest(source: dict[str, Any]) -> bool:
    """Auto-ingest on add only if explicitly opted in via auto_ingest=true
    AND the source is enabled (default True).

    Default is False: the UI now uses an explicit preview/confirm flow, so
    posting a bare source dict will NOT trigger an ingest unless the caller
    sets auto_ingest=true.
    """
    return bool(source.get("auto_ingest", False)) and bool(source.get("enabled", True))


async def _safe_kickoff(coro_fn, *args, label: str) -> None:
    """Run an ingest coroutine; log result/exception so background failures aren't silent."""
    try:
        result = await coro_fn(*args)
        audit.info("auto_ingest_done %s result=%s", label, result)
    except Exception as exc:
        logger.exception("auto_ingest_failed %s: %s", label, exc)


def _kickoff_immediate_pull(kind: str, source: dict[str, Any]) -> None:
    """Schedule an immediate background pull for a just-enabled feed (issue #1).

    Runs the pull inside a job_store Job (via ``run_tracked_pull``) so the UI can
    show a "pulling…" / green "ready" / red "error" marker per feed. Fire and
    forget: the task records its own outcome and never raises out of here.
    """
    audit.info("immediate_pull_scheduled kind=%s name=%s", kind, source.get("name"))
    asyncio.create_task(run_tracked_pull(kind, source))


# ── Listener ──────────────────────────────────────────────────────────────────


@router.get("/listener")
async def get_listener() -> dict[str, Any]:
    return load_sources().get("listener", {})


@router.put("/listener")
async def update_listener(body: dict[str, Any]) -> dict[str, Any]:
    data = load_sources()
    existing = data.get("listener", {}) or {}
    # Persist only the supported keys. `port` is intentionally not stored — the
    # listener runs on the main application port via POST /api/ingest/listener.
    updated: dict[str, Any] = {"enabled": bool(body.get("enabled", True))}
    fields = body.get("fields", existing.get("fields", {}))
    if fields is not None:
        updated["fields"] = fields
    data["listener"] = updated
    save_sources(data)
    return updated


# ── API Pull ──────────────────────────────────────────────────────────────────


@router.get("/api-pull")
async def list_api_pull() -> list[dict[str, Any]]:
    return _redact_sources(load_sources().get("api_pull", []))


@router.post("/api-pull")
async def add_api_pull(source: dict[str, Any]) -> dict[str, Any]:
    _validate_source(source)
    data = load_sources()
    sources: list = data.setdefault("api_pull", [])
    _assert_unique_name(source["name"], sources)
    sources.append(source)
    save_sources(data)
    audit.info(
        "source_added type=api_pull name=%s url=%s interval=%s enabled=%s",
        source["name"], source.get("url"), source.get("interval_minutes", 15), source.get("enabled", True),
    )
    if _should_auto_ingest(source):
        audit.info("auto_ingest_start type=api_pull name=%s", source["name"])
        asyncio.create_task(
            _safe_kickoff(pull_api_source, source, label=f"api_pull:{source['name']}")
        )
    return _redact_source(source)


@router.put("/api-pull/{name}")
async def update_api_pull(name: str, body: dict[str, Any]) -> dict[str, Any]:
    data = load_sources()
    sources: list = data.get("api_pull", [])
    idx = _find_index(name, sources)
    old_enabled = bool(sources[idx].get("enabled", True))
    body["name"] = name
    body = _restore_source_secrets(body, sources[idx])
    sources[idx] = body
    save_sources(data)
    if bool(body.get("enabled", True)) and not old_enabled:
        _kickoff_immediate_pull("api_pull", body)
    return _redact_source(body)


@router.delete("/api-pull/{name}")
async def delete_api_pull(name: str) -> dict[str, str]:
    data = load_sources()
    sources: list = data.get("api_pull", [])
    idx = _find_index(name, sources)
    sources.pop(idx)
    save_sources(data)
    return {"deleted": name}


# ── RSS Pull ──────────────────────────────────────────────────────────────────

@router.get("/rss-pull")
async def list_rss_pull() -> list[dict[str, Any]]:
    return _redact_sources(load_sources().get("rss_pull", []))


@router.post("/rss-pull")
async def add_rss_pull(source: dict[str, Any]) -> dict[str, Any]:
    _validate_source(source)
    data = load_sources()
    sources: list = data.setdefault("rss_pull", [])
    _assert_unique_name(source["name"], sources)
    sources.append(source)
    save_sources(data)
    audit.info(
        "source_added type=rss_pull name=%s url=%s interval=%s enabled=%s",
        source["name"], source.get("url"), source.get("interval_minutes", 15), source.get("enabled", True),
    )
    if _should_auto_ingest(source):
        audit.info("auto_ingest_start type=rss_pull name=%s", source["name"])
        asyncio.create_task(
            _safe_kickoff(pull_rss_source, source, label=f"rss_pull:{source['name']}")
        )
    return _redact_source(source)


@router.put("/rss-pull/{name}")
async def update_rss_pull(name: str, body: dict[str, Any]) -> dict[str, Any]:
    data = load_sources()
    sources: list = data.get("rss_pull", [])
    idx = _find_index(name, sources)
    old_enabled = bool(sources[idx].get("enabled", True))
    body["name"] = name
    body = _restore_source_secrets(body, sources[idx])
    sources[idx] = body
    save_sources(data)
    if bool(body.get("enabled", True)) and not old_enabled:
        _kickoff_immediate_pull("rss_pull", body)
    return _redact_source(body)


@router.delete("/rss-pull/{name}")
async def delete_rss_pull(name: str) -> dict[str, str]:
    data = load_sources()
    sources: list = data.get("rss_pull", [])
    idx = _find_index(name, sources)
    sources.pop(idx)
    save_sources(data)
    return {"deleted": name}


# ── Remote JSON Pull ──────────────────────────────────────────────────────────


@router.get("/remote-json-pull")
async def list_remote_json_pull() -> list[dict[str, Any]]:
    return _redact_sources(load_sources().get("remote_json_pull", []))


@router.post("/remote-json-pull")
async def add_remote_json_pull(source: dict[str, Any]) -> dict[str, Any]:
    _validate_source(source)
    data = load_sources()
    sources: list = data.setdefault("remote_json_pull", [])
    _assert_unique_name(source["name"], sources)
    source.setdefault("continuous", False)
    source.setdefault("interval_minutes", 15)
    sources.append(source)
    save_sources(data)
    audit.info(
        "source_added type=remote_json_pull name=%s url=%s continuous=%s interval=%s enabled=%s",
        source["name"], source.get("url"), source.get("continuous"), source.get("interval_minutes", 15), source.get("enabled", True),
    )
    if _should_auto_ingest(source):
        audit.info("auto_ingest_start type=remote_json_pull name=%s", source["name"])
        asyncio.create_task(
            _safe_kickoff(
                ingest_remote_feed,
                source["url"],
                source["name"],
                source.get("fields"),
                label=f"remote_json_pull:{source['name']}",
            )
        )
    return _redact_source(source)


@router.put("/remote-json-pull/{name}")
async def update_remote_json_pull(name: str, body: dict[str, Any]) -> dict[str, Any]:
    data = load_sources()
    sources: list = data.get("remote_json_pull", [])
    idx = _find_index(name, sources)
    old_enabled = bool(sources[idx].get("enabled", True))
    body["name"] = name
    body = _restore_source_secrets(body, sources[idx])
    sources[idx] = body
    save_sources(data)
    if bool(body.get("enabled", True)) and not old_enabled:
        _kickoff_immediate_pull("remote_json_pull", body)
    return _redact_source(body)


@router.delete("/remote-json-pull/{name}")
async def delete_remote_json_pull(name: str) -> dict[str, str]:
    data = load_sources()
    sources: list = data.get("remote_json_pull", [])
    idx = _find_index(name, sources)
    sources.pop(idx)
    save_sources(data)
    return {"deleted": name}


# ── Threat-intel source catalogue (prompts-042) ───────────────────────────────

# Catalogue kinds map directly to sources.yaml bucket keys.
_CATALOG_KINDS = {"rss_pull", "remote_json_pull"}
# Marks a sources.yaml entry as managed by the threat-intel catalogue card.
_CATALOG_ORIGIN = "threat_intel_catalog"


class ThreatIntelToggle(BaseModel):
    name: str
    enabled: bool = False
    continuous: bool = False
    interval_minutes: int | None = None


def _build_catalog_view() -> list[dict[str, Any]]:
    """Merge the default catalogue with live sources.yaml state.

    For each catalogue item, an entry with the same ``name`` in the matching
    bucket means the feed is enabled; ``continuous``/``interval_minutes`` are
    read back from that entry (falling back to the catalogue defaults).
    """
    catalog = load_default_sources()
    sources = load_sources()
    merged: list[dict[str, Any]] = []
    for item in catalog:
        name = item.get("name")
        kind = item.get("kind")
        if not name or kind not in _CATALOG_KINDS:
            continue
        bucket = sources.get(kind, []) or []
        existing = next((s for s in bucket if s.get("name") == name), None)
        default_interval = int(item.get("default_interval_minutes", 60))
        if existing is not None:
            enabled = True
            continuous = bool(existing.get("continuous", False))
            interval = int(existing.get("interval_minutes", default_interval))
        else:
            enabled = False
            continuous = False
            interval = default_interval
        merged.append({
            "name": name,
            "title": item.get("title", name),
            "kind": kind,
            "url": item.get("url", ""),
            "info": item.get("info", ""),
            "default_interval_minutes": default_interval,
            "enabled": enabled,
            "continuous": continuous,
            "interval_minutes": interval,
        })
    return merged


@router.get("/threat-intel-catalog")
async def get_threat_intel_catalog() -> list[dict[str, Any]]:
    """Return the default threat-intel catalogue merged with live enabled state."""
    return _build_catalog_view()


@router.put("/threat-intel")
async def save_threat_intel_sources(
    body: list[ThreatIntelToggle],
) -> list[dict[str, Any]]:
    """Bulk-apply enable/continuous/interval changes for catalogue feeds.

    Enabling a feed writes a real entry into the matching sources.yaml bucket;
    disabling removes the catalogue-managed entry. Returns the refreshed
    merged catalogue.
    """
    catalog = {item["name"]: item for item in load_default_sources() if item.get("name")}
    data = load_sources()
    # Feeds that transition from disabled -> enabled get an immediate pull after
    # the config is persisted (issue #1).
    newly_enabled: list[tuple[str, dict[str, Any]]] = []
    for toggle in body:
        item = catalog.get(toggle.name)
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown catalogue source '{toggle.name}'"
            )
        kind = item.get("kind")
        if kind not in _CATALOG_KINDS:
            raise HTTPException(
                status_code=400, detail=f"Unsupported catalogue kind '{kind}'"
            )
        bucket: list = data.setdefault(kind, [])
        idx = next(
            (i for i, s in enumerate(bucket) if s.get("name") == toggle.name), None
        )
        if toggle.enabled:
            interval = toggle.interval_minutes
            if not isinstance(interval, int) or interval < 1:
                interval = int(item.get("default_interval_minutes", 60))
            entry = dict(bucket[idx]) if idx is not None else {}
            entry.update({
                "name": toggle.name,
                "enabled": True,
                "url": item.get("url", ""),
                "continuous": bool(toggle.continuous),
                "interval_minutes": interval,
                "source_origin": _CATALOG_ORIGIN,
            })
            if idx is not None:
                bucket[idx] = entry
            else:
                bucket.append(entry)
                newly_enabled.append((kind, entry))
            audit.info(
                "threat_intel_enabled name=%s kind=%s continuous=%s interval=%s",
                toggle.name, kind, toggle.continuous, interval,
            )
        elif idx is not None:
            bucket.pop(idx)
            audit.info("threat_intel_disabled name=%s kind=%s", toggle.name, kind)
    save_sources(data)
    for kind, entry in newly_enabled:
        _kickoff_immediate_pull(kind, entry)
    return _build_catalog_view()


# ── Per-source field config ───────────────────────────────────────────────────

_SOURCE_TYPE_MAP: dict[str, str] = {
    "api-pull": "api_pull",
    "rss-pull": "rss_pull",
    "remote-json-pull": "remote_json_pull",
}


def _merge_fields_with_global(source_fields: dict[str, Any] | None) -> dict[str, Any]:
    """Return global field config merged with per-source overrides."""
    global_config = load_fields()
    if not source_fields:
        return global_config
    override_map: dict[str, bool] = {
        f["name"]: f["enabled"]
        for f in source_fields.get("core_fields", [])
        if "enabled" in f
    }
    merged_core = [
        {**f, "enabled": override_map.get(f["name"], f.get("enabled", True))}
        for f in global_config.get("core_fields", [])
    ]
    global_custom = {f["name"]: f for f in global_config.get("custom_fields", [])}
    source_custom = {f["name"]: f for f in source_fields.get("custom_fields", [])}
    merged_custom = list({**global_custom, **source_custom}.values())
    return {"core_fields": merged_core, "custom_fields": merged_custom}


@router.get("/listener/fields")
async def get_listener_fields() -> dict[str, Any]:
    source_fields = load_sources().get("listener", {}).get("fields") or None
    return _merge_fields_with_global(source_fields)


@router.put("/listener/fields")
async def put_listener_fields(body: dict[str, Any]) -> dict[str, Any]:
    data = load_sources()
    data.setdefault("listener", {})["fields"] = body
    save_sources(data)
    return _merge_fields_with_global(body)


@router.get("/{source_type}/{name}/fields")
async def get_source_fields(source_type: str, name: str) -> dict[str, Any]:
    yaml_key = _SOURCE_TYPE_MAP.get(source_type)
    if yaml_key is None:
        raise HTTPException(status_code=404, detail=f"Unknown source type '{source_type}'")
    sources: list = load_sources().get(yaml_key, [])
    src = next((s for s in sources if s.get("name") == name), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"Source '{name}' not found in {source_type}")
    return _merge_fields_with_global(src.get("fields") or None)


@router.put("/{source_type}/{name}/fields")
async def put_source_fields(source_type: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
    yaml_key = _SOURCE_TYPE_MAP.get(source_type)
    if yaml_key is None:
        raise HTTPException(status_code=404, detail=f"Unknown source type '{source_type}'")
    data = load_sources()
    sources: list = data.get(yaml_key, [])
    idx = _find_index(name, sources)
    sources[idx]["fields"] = body
    save_sources(data)
    return _merge_fields_with_global(body)


# ── Preview / Confirm flow ────────────────────────────────────────────────────

_PREVIEW_KIND_MAP: dict[str, str] = {
    "api-pull": "api_pull",
    "rss-pull": "rss_pull",
    "remote-json-pull": "remote_json_pull",
}


@router.post("/preview/{source_type}")
async def preview_pull_source(source_type: str, source: dict[str, Any]) -> dict[str, Any]:
    """Fetch + parse a pull source without persisting it. Returns a sample of 10
    normalised entries plus a preview_id to confirm later."""
    kind = _PREVIEW_KIND_MAP.get(source_type)
    if kind is None:
        raise HTTPException(status_code=404, detail=f"Unknown source type '{source_type}'")
    _validate_source(source)

    # Detect name collision early so the user does not waste a fetch.
    yaml_key = kind
    existing = load_sources().get(yaml_key, [])
    if any(s.get("name") == source["name"] for s in existing):
        raise HTTPException(status_code=409, detail=f"Source name '{source['name']}' already exists")

    try:
        preview = await build_source_preview(source, kind)  # type: ignore[arg-type]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Preview fetch failed: {exc}") from exc
    return preview.model_dump()


@router.post("/preview/confirm/{preview_id}")
async def confirm_preview_source(
    preview_id: str,
    background_tasks: BackgroundTasks,
    background: bool = False,
) -> dict[str, Any]:
    """Persist a previewed source to sources.yaml and insert its cached entries."""
    if background:
        # Peek the cached preview to learn the source name & first-ingest flag.
        from backend.ingestion.source_preview import _store as _preview_store
        stored = _preview_store.get(preview_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Preview not found or expired")
        source_name = stored["source"]["name"]
        first = (await get_entry_count_for_source(source_name)) == 0
        job = job_store.create(source_name, "source_preview_confirm", first_ingest=first)
        background_tasks.add_task(_run_source_confirm_job, job.id, preview_id)
        return {"job_id": job.id}
    result = await confirm_source_preview(preview_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Preview not found or expired")
    return result


async def _run_source_confirm_job(job_id: str, preview_id: str) -> None:
    try:
        result = await confirm_source_preview(preview_id, job_id=job_id)
        if result is None:
            job_store.fail(job_id, f"Preview '{preview_id}' not found or expired")
            return
        job_store.complete(job_id, {
            "total_read":  result.get("total_read", 0),
            "inserted":    result.get("inserted", 0),
            "duplicates":  result.get("duplicates", 0),
            "discarded":   result.get("discarded", 0),
        })
    except Exception as exc:
        job_store.fail(job_id, str(exc))


@router.post("/preview/cancel/{preview_id}")
async def cancel_preview_source(preview_id: str) -> dict[str, bool]:
    """Drop a previewed source from cache without persisting anything."""
    found = cancel_source_preview(preview_id)
    return {"cancelled": found}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_source(source: dict[str, Any]) -> None:
    if not source.get("name"):
        raise HTTPException(status_code=400, detail="Source must have a non-empty 'name'")
    if not source.get("url"):
        raise HTTPException(status_code=400, detail="Source must have a non-empty 'url'")


def _assert_unique_name(name: str, sources: list[dict]) -> None:
    if any(s.get("name") == name for s in sources):
        raise HTTPException(status_code=409, detail=f"Source name '{name}' already exists")


def _find_index(name: str, sources: list[dict]) -> int:
    for i, s in enumerate(sources):
        if s.get("name") == name:
            return i
    raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
