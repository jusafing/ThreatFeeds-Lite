"""LLM provider registry (prompts-021D).

Maps a provider name (from config) to a concrete :class:`LLMClient`
instance. Refuses to construct anything when LLM is globally disabled.
"""
from __future__ import annotations

from typing import Any

from backend.llm.client import (
    AnthropicClient,
    LLMClient,
    OllamaClient,
    OpenAIClient,
    OpenAICompatibleClient,
)
from backend.llm.config import load_llm_config
from backend.llm.errors import LLMConfigError, LLMDisabledError

_CLIENT_KINDS: dict[str, type[LLMClient]] = {
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
    "ollama": OllamaClient,
    "openai_compatible": OpenAICompatibleClient,
}

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434",
}


def _find_provider(cfg: dict[str, Any], name: str | None) -> dict[str, Any]:
    providers = cfg.get("providers", [])
    if not providers:
        raise LLMConfigError("no providers configured")
    if name is None:
        name = cfg.get("default_provider")
        if not name:
            raise LLMConfigError("no provider specified and no default_provider set")
    for p in providers:
        if isinstance(p, dict) and p.get("name") == name:
            return p
    raise LLMConfigError(f"unknown provider: {name!r}")


def get_client(provider_name: str | None = None) -> LLMClient:
    """Return a constructed :class:`LLMClient` for the named provider.

    Raises:
        LLMDisabledError: when ``enabled=false`` at the top level.
        LLMConfigError: when the provider is unknown or misconfigured.
    """
    cfg = load_llm_config()
    if not cfg.get("enabled", False):
        raise LLMDisabledError("LLM is disabled (enabled=false in llm-providers.yaml)")

    p = _find_provider(cfg, provider_name)
    kind = p.get("kind")
    cls = _CLIENT_KINDS.get(kind)
    if cls is None:
        raise LLMConfigError(f"unsupported provider kind: {kind!r}")

    base_url = p.get("base_url") or _DEFAULT_BASE_URLS.get(kind, "")
    if not base_url:
        raise LLMConfigError(f"provider {p.get('name')!r}: base_url is required")
    model = p.get("model") or ""
    if not model:
        raise LLMConfigError(f"provider {p.get('name')!r}: model is required")

    return cls(
        name=p["name"],
        base_url=base_url,
        api_key=p.get("api_key", "") or "",
        model=model,
        timeout_seconds=float(p.get("timeout_seconds", 30)),
        max_retries=int(p.get("max_retries", 2)),
        skip_tls_verify=bool(p.get("skip_tls_verify", False)),
        extra_body=p.get("extra_body") if isinstance(p.get("extra_body"), dict) else None,
    )


def list_provider_names() -> list[dict[str, Any]]:
    """Return a redacted listing of configured providers — no secrets."""
    cfg = load_llm_config()
    out: list[dict[str, Any]] = []
    for p in cfg.get("providers", []):
        if not isinstance(p, dict):
            continue
        out.append({
            "name": p.get("name"),
            "kind": p.get("kind"),
            "model": p.get("model"),
            "has_api_key": bool(p.get("api_key")),
            "skip_tls_verify": bool(p.get("skip_tls_verify", False)),
            # prompts-034: surface the persisted tested-models list so the
            # Smart Mapping model dropdown can offer "provider · model" without
            # a second round-trip. Public ids, never a secret.
            # prompts-036: tested_models is retained (still recorded on a green
            # probe) but is no longer the dropdown source.
            "tested_models": list(p.get("tested_models") or []),
            # prompts-036: surface the discovered model catalog so the Smart
            # Mapping proposal dropdown can be populated from discovered models
            # (no green Test required). Public ids, never a secret; [] when the
            # provider has not been discovered yet.
            "available_models": list(p.get("available_models") or []),
        })
    return out


def build_client_from_payload(payload: dict[str, Any]) -> LLMClient:
    """Construct a transient :class:`LLMClient` from a request body (022 step 4).

    Used by the ephemeral ``POST /api/llm/providers/test`` route so the
    Add LLM wizard can run Test Connection BEFORE the provider has been
    persisted to ``llm-providers.yaml``. Pure construction; does not
    touch the YAML file and does not consult the LLM ``enabled`` flag
    (the ephemeral test is allowed even when the overall LLM toggle is
    off — otherwise an operator could never set up the first provider).

    Validation is intentionally narrow: just enough to construct a
    working client. Provider-name regex + uniqueness are NOT enforced
    here because this code path never persists the value — those rules
    are applied by ``POST /api/llm/providers`` (the persist route).
    """
    if not isinstance(payload, dict):
        raise LLMConfigError("provider payload must be a mapping")

    name = payload.get("name") or "draft-provider"
    if not isinstance(name, str) or not name.strip():
        name = "draft-provider"

    kind = payload.get("kind")
    cls = _CLIENT_KINDS.get(kind)
    if cls is None:
        raise LLMConfigError(
            f"unsupported provider kind: {kind!r}; expected one of "
            f"{sorted(_CLIENT_KINDS)}"
        )

    base_url = payload.get("base_url") or _DEFAULT_BASE_URLS.get(kind, "")
    if not base_url:
        raise LLMConfigError(f"base_url is required for kind={kind!r}")

    # Model is NOT required for the ephemeral test (the whole point is to
    # discover it). prompts-024: ``test_runner.run_provider_test`` records
    # a skipped ``complete`` step instead of hitting the upstream and
    # receiving HTTP 400 when the model is empty.
    model = payload.get("model") or ""

    return cls(
        name=name,
        base_url=base_url,
        api_key=payload.get("api_key", "") or "",
        model=model,
        timeout_seconds=float(payload.get("timeout_seconds", 30)),
        max_retries=int(payload.get("max_retries", 2)),
        skip_tls_verify=bool(payload.get("skip_tls_verify", False)),
        extra_body=(
            payload.get("extra_body")
            if isinstance(payload.get("extra_body"), dict)
            else None
        ),
    )
