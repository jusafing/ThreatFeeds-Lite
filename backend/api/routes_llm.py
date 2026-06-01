"""LLM provider API routes (prompts-021D, refactored in 022 step 4).

Default-off, no-auth (matches project posture).

Route map (post-022):

  GET    /api/llm/config                — full config, api_key redacted
  PUT    /api/llm/config                — UPDATES ONLY {enabled, default_provider};
                                          a body that includes 'providers' is
                                          rejected with 400. Providers are
                                          managed via the per-provider routes.
  GET    /api/llm/providers             — redacted listing, no secrets
  POST   /api/llm/providers             — add a new provider (enforces name regex
                                          + uniqueness via validate_new_provider_name)
  PUT    /api/llm/providers/{name}      — replace one provider in-place; write-only
                                          api_key semantics retained ("***" keeps)
  DELETE /api/llm/providers/{name}      — remove provider; clears default_provider
                                          if it was pointing at the deleted one
  POST   /api/llm/providers/test        — ephemeral Test Connection from request
                                          body (used by the Add LLM wizard
                                          before the provider is persisted)
  POST   /api/llm/providers/{name}/test — Test Connection against an already-
                                          persisted provider

Both /test routes return the canonical run_provider_test shape
(see backend/llm/test_runner.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from backend.llm.config import (
    PROVIDER_NAME_RE,  # re-exported for tests; not used here directly
    load_llm_config,
    merge_write_only_key,
    record_tested_model,
    redact_config,
    save_llm_config,
    validate_new_provider_name,
)
from backend.llm.errors import (
    LLMConfigError,
    LLMDisabledError,
    LLMProviderError,
    LLMTransportError,
)
from backend.llm.registry import (
    build_client_from_payload,
    get_client,
    list_provider_names,
)
from backend.llm.test_runner import run_discover_only, run_provider_test

router = APIRouter(prefix="/api/llm", tags=["llm"])

logger = logging.getLogger(__name__)

# Top-level keys legal in PUT /api/llm/config after 022 step 4. The
# 'providers' key is intentionally NOT in this set; managing the list
# moves to the per-provider routes below.
_CONFIG_ALLOWED_KEYS = {"enabled", "default_provider"}


# ── /config ─────────────────────────────────────────────────────────────────


@router.get("/config")
async def get_llm_config() -> dict[str, Any]:
    """Return the LLM config with api_key values redacted."""
    return redact_config(load_llm_config())


@router.put("/config")
async def update_llm_config(body: dict[str, Any]) -> dict[str, Any]:
    """Update the top-level LLM config keys (enabled, default_provider).

    prompts-022: the providers list is now managed by the dedicated
    /providers routes; a body that includes 'providers' is rejected
    with 400 so a stale client can't silently overwrite the list.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a mapping")

    extra = set(body) - _CONFIG_ALLOWED_KEYS
    if extra:
        raise HTTPException(
            status_code=400,
            detail=(
                f"keys not allowed on PUT /api/llm/config: {sorted(extra)}; "
                "use the /api/llm/providers routes to manage the providers list"
            ),
        )

    existing = load_llm_config()
    merged: dict[str, Any] = dict(existing)  # preserve providers verbatim
    if "enabled" in body:
        merged["enabled"] = body["enabled"]
    if "default_provider" in body:
        merged["default_provider"] = body["default_provider"]

    try:
        save_llm_config(merged)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return redact_config(load_llm_config())


# ── /providers (collection + per-name) ──────────────────────────────────────


@router.get("/providers")
async def get_llm_providers() -> list[dict[str, Any]]:
    """Return the list of configured providers (no secrets)."""
    return list_provider_names()


@router.post("/providers", status_code=201)
async def add_llm_provider(body: dict[str, Any]) -> dict[str, Any]:
    """Append a new provider to llm-providers.yaml.

    prompts-022: enforces the new identifier regex and uniqueness via
    ``validate_new_provider_name`` (regex applied ONLY to new rows;
    legacy provider names already on disk are grandfathered). The
    rest of the structural validation is delegated to
    ``save_llm_config``.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a mapping")
    name = body.get("name")

    cfg = load_llm_config()
    existing_names: set[str] = {
        p.get("name") for p in cfg.get("providers", []) if isinstance(p, dict) and p.get("name")
    }
    try:
        validate_new_provider_name(name, existing_names=existing_names)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cfg["providers"].append(body)
    try:
        save_llm_config(cfg)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Return the freshly-saved redacted summary for this provider.
    for entry in list_provider_names():
        if entry["name"] == name:
            return entry
    # Defensive — save_llm_config persisted but listing didn't see it.
    raise HTTPException(status_code=500, detail="provider saved but not found in listing")


@router.put("/providers/{name}")
async def update_llm_provider(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Replace an existing provider in-place.

    The path ``name`` is authoritative; if the body carries a different
    ``name`` it is replaced. Write-only api_key semantics still apply:
    sending ``"***"`` (or omitting the key) preserves the stored value.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a mapping")

    cfg = load_llm_config()
    providers: list[dict[str, Any]] = cfg.get("providers", [])
    idx = next(
        (i for i, p in enumerate(providers) if isinstance(p, dict) and p.get("name") == name),
        -1,
    )
    if idx < 0:
        raise HTTPException(status_code=404, detail=f"provider not found: {name!r}")

    incoming = dict(body)
    incoming["name"] = name  # enforce path == record

    # Reuse the merge helper at the wrapped-config level so the "***"
    # semantics behave identically to a full-config PUT did historically.
    merged_cfg = merge_write_only_key(
        {"providers": [incoming]},
        {"providers": [providers[idx]]},
    )
    providers[idx] = merged_cfg["providers"][0]
    cfg["providers"] = providers

    try:
        save_llm_config(cfg)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    for entry in list_provider_names():
        if entry["name"] == name:
            return entry
    raise HTTPException(status_code=500, detail="provider updated but not found in listing")


@router.delete("/providers/{name}", status_code=204)
async def delete_llm_provider(name: str) -> Response:
    """Remove a provider. Clears ``default_provider`` if it pointed here."""
    cfg = load_llm_config()
    providers: list[dict[str, Any]] = cfg.get("providers", [])
    new_providers = [p for p in providers if not (isinstance(p, dict) and p.get("name") == name)]
    if len(new_providers) == len(providers):
        raise HTTPException(status_code=404, detail=f"provider not found: {name!r}")
    cfg["providers"] = new_providers
    if cfg.get("default_provider") == name:
        cfg["default_provider"] = None
    # prompts-031: deleting the *last* provider while LLM is enabled would
    # otherwise be rejected by validate_config ("LLM is enabled but no
    # providers are configured"), making the last provider undeletable.
    # Auto-disable LLM in the same write so the delete always succeeds and
    # the config remains valid; the operator re-enables after adding one.
    if not new_providers and cfg.get("enabled"):
        cfg["enabled"] = False
    try:
        save_llm_config(cfg)
    except LLMConfigError as exc:
        # Saving may still fail if enabled=true and the *remaining* providers
        # are non-ollama without keys — surface the error to the operator
        # instead of half-persisting.
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(status_code=204)


# ── /providers/test (ephemeral + persisted) ─────────────────────────────────


@router.post("/providers/test")
async def test_llm_provider_draft(body: dict[str, Any]) -> dict[str, Any]:
    """Run Test Connection against a *draft* provider (not yet persisted).

    prompts-022: used by the Add LLM wizard before the operator clicks
    'Add LLM'. The provider config is constructed in-memory by
    ``build_client_from_payload``; the on-disk YAML is never touched.

    prompts-027: when the body carries a ``name`` matching an
    already-persisted provider AND ``api_key == "***"`` (the redacted
    placeholder), merge the stored key from disk via
    ``merge_write_only_key``. This lets the persisted ProviderCard's
    "Test connection" button reuse the draft endpoint with the
    operator's currently-selected dropdown model, without forcing the
    operator to re-type a key the server already has. Anonymous drafts
    (wizard pre-save) take the original path verbatim because either
    ``name`` is absent or no on-disk record matches.
    """
    if isinstance(body, dict):
        name = body.get("name")
        api_key = body.get("api_key")
        if isinstance(name, str) and name and api_key == "***":
            cfg = load_llm_config()
            existing = {
                "providers": [
                    p for p in cfg.get("providers", [])
                    if isinstance(p, dict) and p.get("name") == name
                ]
            }
            if existing["providers"]:
                merged = merge_write_only_key(
                    {"providers": [body]}, existing,
                )
                body = merged["providers"][0]

    try:
        client = build_client_from_payload(body)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Provider/transport errors are captured inside the transcript by
    # run_provider_test itself; we still trap LLMConfigError that may
    # bubble up from any defensive validation inside the runner.
    try:
        result = await asyncio.to_thread(run_provider_test, client)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # prompts-034: on a green test against a *persisted* provider, record the
    # exact model so the Smart Mapping dropdown can offer it. A no-op when the
    # draft body has no name matching a persisted provider (wizard pre-save).
    if result.get("status") == "ok":
        record_tested_model(body.get("name"), client.model)
    return result


# ── /providers/discover (ephemeral + persisted) — prompts-027 ───────────────


@router.post("/providers/discover")
async def discover_llm_provider_draft(body: dict[str, Any]) -> dict[str, Any]:
    """Run the discover (list_models) step ONLY against a draft provider.

    prompts-027 stage 2: the Add Provider wizard's "Connect to provider"
    button calls this to obtain a model list *before* the operator has
    picked a model to probe. Returns ``{status, details[], models}``.
    """
    try:
        client = build_client_from_payload(body)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await asyncio.to_thread(run_discover_only, client)
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/providers/{name}/discover")
async def discover_llm_provider(name: str) -> dict[str, Any]:
    """Run the discover (list_models) step ONLY against a persisted provider.

    prompts-027: the persisted ProviderCard's "Discover Models" button
    calls this to refresh the per-provider list. The frontend then
    persists the returned ``models`` to ``available_models`` via the
    existing PUT route.
    """
    try:
        client = get_client(name)
    except LLMDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await asyncio.to_thread(run_discover_only, client)


@router.post("/providers/{name}/test")
async def test_llm_provider(name: str) -> dict[str, Any]:
    """Run Test Connection against an already-persisted provider.

    prompts-022: response shape changed from
    ``{status, method, models?, sample?}`` to the canonical
    ``run_provider_test`` payload (``{status, details[], models, sample}``).
    The frontend's Test Details modal renders the ``details[]`` array.
    """
    try:
        client = get_client(name)
    except LLMDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        result = await asyncio.to_thread(run_provider_test, client)
    except LLMTransportError as exc:
        # Defensive: run_provider_test catches these into the transcript,
        # so this branch should be unreachable. Keep the legacy 502 wrap
        # in case a future runner change re-raises.
        raise HTTPException(status_code=502, detail=f"transport: {exc!s}")
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "status": exc.status, "body": exc.body},
        )
    # prompts-034: record the model on a green test (see /providers/test).
    if result.get("status") == "ok":
        record_tested_model(name, client.model)
    return result
