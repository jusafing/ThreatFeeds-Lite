"""LLM provider configuration (prompts-021D).

Storage model:
    config/llm-providers.yaml (gitignored) — real file, written by
        save_llm_config() via the /api/llm/config PUT endpoint.
    config/llm-providers.yaml.example      — committed template.

Secret hygiene:
    * api_key is write-only: GET endpoints return ``"***"`` via
      ``redact_config``; PUT endpoints use ``merge_write_only_key`` so
      sending ``"***"`` back means "keep existing".
    * Keys are NEVER logged. Validation logs include provider name only.
"""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from backend.llm.errors import LLMConfigError

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LLM_CONFIG_PATH = _PROJECT_ROOT / "config" / "llm-providers.yaml"

_VALID_KINDS = {"openai", "anthropic", "ollama", "openai_compatible"}
_REDACTED = "***"

# prompts-022: provider names are operator-facing identifiers used in
# URLs (e.g. /api/llm/providers/{name}/test) and YAML keys. We restrict
# the charset so they survive shell/url usage and stay short enough
# for the UI. Existing rows are *grandfathered*: validate_config only
# checks structure + uniqueness; the regex is enforced by
# ``validate_new_provider_name`` which is called by the new
# ``POST /api/llm/providers`` route on insert.
PROVIDER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "default_provider": None,
    "providers": [],
}


def _read() -> dict[str, Any]:
    if not _LLM_CONFIG_PATH.exists():
        return copy.deepcopy(_DEFAULTS)
    with open(_LLM_CONFIG_PATH, "r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    merged = copy.deepcopy(_DEFAULTS)
    merged.update(loaded)
    if not isinstance(merged.get("providers"), list):
        merged["providers"] = []
    return merged


def _write(data: dict[str, Any]) -> None:
    _LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LLM_CONFIG_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_llm_config() -> dict[str, Any]:
    """Return the current LLM config (with defaults merged in)."""
    return _read()


def save_llm_config(data: dict[str, Any]) -> None:
    """Validate and persist the LLM config."""
    validate_config(data)
    _write(data)


def redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with each provider's ``api_key`` redacted.

    A non-empty key becomes ``"***"``; an empty/missing key stays ``""``.
    Operators can detect "key is set" without seeing the value.
    """
    out = copy.deepcopy(cfg)
    for p in out.get("providers", []):
        if not isinstance(p, dict):
            continue
        existing = p.get("api_key", "")
        p["api_key"] = _REDACTED if existing else ""
    return out


def merge_write_only_key(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge ``incoming`` config over ``existing`` with write-only api_key.

    For each incoming provider whose ``api_key`` is ``"***"`` (or absent),
    the existing provider's ``api_key`` is preserved. Any other value
    replaces it. Provider matching is by ``name``.
    """
    result = copy.deepcopy(incoming)
    existing_by_name: dict[str, dict[str, Any]] = {}
    for p in existing.get("providers", []):
        if isinstance(p, dict) and p.get("name"):
            existing_by_name[p["name"]] = p

    for p in result.get("providers", []):
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        incoming_key = p.get("api_key", _REDACTED)
        if incoming_key == _REDACTED or incoming_key is None:
            prior = existing_by_name.get(name, {}).get("api_key", "")
            p["api_key"] = prior
    return result


def validate_config(cfg: dict[str, Any]) -> None:
    """Raise :class:`LLMConfigError` on any structural problem.

    Rules:
        * top-level ``enabled`` must be bool
        * ``providers`` must be a list
        * each provider must have ``name`` (str, unique) and ``kind`` in
          the supported set
        * ``default_provider``, if set, must reference an existing name
        * when ``enabled=true``: providers must be non-empty AND each
          non-ollama provider must have a non-empty ``api_key``
    """
    if not isinstance(cfg, dict):
        raise LLMConfigError("LLM config must be a mapping")

    enabled = cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise LLMConfigError("'enabled' must be a boolean")

    providers = cfg.get("providers", [])
    if not isinstance(providers, list):
        raise LLMConfigError("'providers' must be a list")

    seen: set[str] = set()
    for p in providers:
        if not isinstance(p, dict):
            raise LLMConfigError("each provider entry must be a mapping")
        name = p.get("name")
        if not isinstance(name, str) or not name.strip():
            raise LLMConfigError("provider 'name' is required and must be a non-empty string")
        if name in seen:
            raise LLMConfigError(f"duplicate provider name: {name!r}")
        seen.add(name)
        kind = p.get("kind")
        if kind not in _VALID_KINDS:
            raise LLMConfigError(
                f"provider {name!r}: kind must be one of {sorted(_VALID_KINDS)}, got {kind!r}"
            )

        # prompts-027: optional persisted list of models discovered by
        # the "Discover Models" button on the persisted ProviderCard.
        # Not a secret, not redacted. Stored verbatim. Validated as a
        # list of non-empty strings if present.
        avail = p.get("available_models")
        if avail is not None:
            if not isinstance(avail, list) or not all(
                isinstance(m, str) and m for m in avail
            ):
                raise LLMConfigError(
                    f"provider {name!r}: 'available_models' must be a list of "
                    "non-empty strings"
                )

        # prompts-034: persisted list of models that have passed a green
        # Test against this provider. Used as the source for the Smart
        # Mapping model dropdown (decision A). Public ids, not redacted.
        tested = p.get("tested_models")
        if tested is not None:
            if not isinstance(tested, list) or not all(
                isinstance(m, str) and m for m in tested
            ):
                raise LLMConfigError(
                    f"provider {name!r}: 'tested_models' must be a list of "
                    "non-empty strings"
                )

        # prompts-035 (#2b): optional config-driven request-body additions
        # merged into the OpenAI-compatible /chat/completions payload (e.g.
        # reasoning-model controls). Must be a mapping of string keys when
        # present. Not a secret; stored verbatim. Values are pass-through.
        extra_body = p.get("extra_body")
        if extra_body is not None:
            if not isinstance(extra_body, dict) or not all(
                isinstance(k, str) and k for k in extra_body
            ):
                raise LLMConfigError(
                    f"provider {name!r}: 'extra_body' must be a mapping with "
                    "non-empty string keys"
                )

    default_provider = cfg.get("default_provider")
    if default_provider is not None and default_provider not in seen:
        raise LLMConfigError(
            f"default_provider {default_provider!r} is not a configured provider"
        )

    if enabled:
        if not providers:
            raise LLMConfigError("LLM is enabled but no providers are configured")
        for p in providers:
            if p.get("kind") == "ollama":
                continue
            if not p.get("api_key"):
                raise LLMConfigError(
                    f"provider {p.get('name')!r}: api_key is required when LLM is enabled"
                )


def record_tested_model(provider_name: str | None, model: str | None) -> bool:
    """Append ``model`` to a provider's ``tested_models`` and persist.

    prompts-034 (decision A): called when a Test passes green, recording the
    exact model that succeeded so the Smart Mapping dropdown can offer it.
    The list is de-duplicated and order-preserving (first-tested first). Model
    ids are public, so they are stored verbatim (never redacted).

    Returns True if the config changed (model newly appended); False on a
    no-op (provider not found, empty inputs, or model already present).
    """
    if not provider_name or not model:
        return False
    cfg = load_llm_config()
    for p in cfg.get("providers", []):
        if not isinstance(p, dict) or p.get("name") != provider_name:
            continue
        tested = p.get("tested_models")
        if not isinstance(tested, list):
            tested = []
        if model in tested:
            return False
        tested.append(model)
        p["tested_models"] = tested
        save_llm_config(cfg)
        return True
    return False


def validate_new_provider_name(name: str, *, existing_names: set[str]) -> None:
    """Enforce identifier rules on a *new* provider name (prompts-022).

    Called by the ``POST /api/llm/providers`` route. Pre-existing names
    in the on-disk YAML are grandfathered (never re-validated by this
    helper); only freshly-added names must pass the regex.

    Raises :class:`LLMConfigError` with a 400-suitable message on:
        * empty / non-string name
        * regex mismatch (charset or length)
        * duplicate against ``existing_names``
    """
    if not isinstance(name, str) or not name.strip():
        raise LLMConfigError("provider 'name' is required and must be a non-empty string")
    if not PROVIDER_NAME_RE.match(name):
        raise LLMConfigError(
            "provider 'name' must match ^[A-Za-z0-9_-]{1,40}$ "
            "(letters, digits, '_' or '-'; up to 40 chars)"
        )
    if name in existing_names:
        raise LLMConfigError(f"provider name already exists: {name!r}")
