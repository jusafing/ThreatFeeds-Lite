"""
Normalizer config — loads normalizer-config.yaml.

If the YAML file is missing, sane defaults are returned and a warning is logged.
This prevents a clean install from crashing the FastAPI lifespan if the config
file is absent. Partial YAML files are merged over defaults so newer keys
remain populated.

Migration (prompts-021E-pre): operator-defined ``manual_mappings`` whose
``raw_field → canonical`` values reference the engine's old private canonicals
(``ip_address``/``domain``/``hash``/``cve``/``timestamp``/``source_name_norm``)
are auto-translated to the equivalent yaml canonical names and the file is
rewritten in place. Each translation is logged at WARNING level so the
operator can audit the change.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NORMALIZER_CONFIG_PATH = _PROJECT_ROOT / "config" / "normalizer-config.yaml"

# prompts-032 Phase E: the engine's three normalization modes. ``smart``
# applies the active CONSOLIDATED mapping (see consolidated.py) to all feeds,
# falling back to ``auto`` with a warning when none is active.
VALID_MODES = frozenset({"auto", "manual", "smart"})

_DEFAULTS: dict[str, Any] = {
    "mode": "auto",
    "enabled": True,
    "interval_minutes": 10,
    "manual_mappings": {},
    # prompts-021E-3: smart-mode triggers + concurrency. Deep-merged below.
    "smart_mode": {
        "enabled": False,
        "provider": None,            # null → fall back to llm.default_provider
        "sample_size": 10,
        # prompts-034: the consolidated-proposal LLM call can be slow (large
        # union of raw fields, big sample block, slow local models). This
        # timeout governs ONLY the proposal ``complete()`` call and overrides
        # the per-provider ``timeout_seconds`` (kept short for Test/Discover).
        # Default 600s (10 min) per the operator requirement.
        "llm_timeout_seconds": 600,
        # prompts-034: output-token budget for the consolidated mapping JSON.
        # A consolidated mapping spans the union of raw fields across many
        # feeds, so 1024 truncated the JSON and produced parse failures.
        # prompts-035 (#2b): raised 4096→8192 — reasoning models (e.g.
        # gpt-oss:120b) spend part of the budget on hidden reasoning tokens,
        # which left the final channel empty at 4096. Still operator-tunable.
        "llm_max_tokens": 8192,
        "schedule": {
            "enabled": False,
            "interval_minutes": 1440,  # daily
        },
        "on_new_feed": {
            "enabled": True,           # acts only when smart_mode.enabled=True
            "first_ingest_only": True,
        },
        # auto_apply: 021E-4 makes this functional.
        # `enabled` gates whether scored proposals auto-apply at all.
        # `min_coverage_delta` is the minimum population-weighted coverage
        # improvement required for auto-apply (0.0..1.0). Default 0.05 =
        # 5 percentage points.
        "auto_apply": {
            "enabled": False,
            "min_coverage_delta": 0.05,
        },
        "concurrency": {
            "max_concurrent": 2,
        },
        "sources": [],
    },
}

# Old engine canonical → yaml canonical (021E-pre).
# Note: ip_address/domain/hash all collapse to ``indicator``. The accompanying
# ``indicator_type`` is NOT auto-inserted because the operator's original
# mapping intent is preserved (e.g. they may have multiple raw fields all
# mapped to ``ip_address``; auto-inserting a type for each would be wrong).
_LEGACY_CANONICAL_MAP: dict[str, str] = {
    "ip_address": "indicator",
    "domain": "indicator",
    "hash": "indicator",
    "cve": "cve_id",
    "timestamp": "published_at",
    "source_name_norm": "source",
}


def _migrate_manual_mappings(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Translate legacy canonical names in ``manual_mappings``.

    Returns (possibly-mutated-data, changed-flag). When changed=True, the
    caller should persist ``data`` back to disk.
    """
    mappings = data.get("manual_mappings") or {}
    if not isinstance(mappings, dict):
        return data, False
    changed = False
    new_mappings: dict[str, dict[str, str]] = {}
    for source_name, source_map in mappings.items():
        if not isinstance(source_map, dict):
            new_mappings[source_name] = source_map
            continue
        translated: dict[str, str] = {}
        for raw_field, canonical in source_map.items():
            new_canonical = _LEGACY_CANONICAL_MAP.get(canonical, canonical)
            if new_canonical != canonical:
                logger.warning(
                    "normalizer-config.yaml: migrating manual_mapping "
                    "%s.%s: %s → %s (021E-pre canonical reconciliation)",
                    source_name, raw_field, canonical, new_canonical,
                )
                changed = True
            translated[raw_field] = new_canonical
        new_mappings[source_name] = translated
    if changed:
        data = dict(data)
        data["manual_mappings"] = new_mappings
    return data, changed


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overrides`` over ``defaults``.

    For each key:
      * If both sides hold a dict, recurse.
      * Otherwise, override wins (including for lists — operator-supplied
        lists are treated as authoritative, not appended).

    Used so that a partial operator YAML (e.g. ``smart_mode: {enabled: true}``)
    does not strip nested defaults (021E-3).
    """
    out: dict[str, Any] = dict(defaults)
    for key, value in overrides.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read() -> dict[str, Any]:
    if not _NORMALIZER_CONFIG_PATH.exists():
        logger.warning(
            "Normalizer config file missing at %s; using defaults",
            _NORMALIZER_CONFIG_PATH,
        )
        return _deep_merge(_DEFAULTS, {})
    with open(_NORMALIZER_CONFIG_PATH, "r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    merged = _deep_merge(_DEFAULTS, loaded)
    merged, changed = _migrate_manual_mappings(merged)
    if changed:
        try:
            _write(merged)
            logger.info(
                "normalizer-config.yaml rewritten with migrated manual_mappings"
            )
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning(
                "Failed to persist migrated normalizer-config.yaml: %s", exc,
            )
    return merged


def _write(data: dict[str, Any]) -> None:
    _NORMALIZER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_NORMALIZER_CONFIG_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_normalizer_config() -> dict[str, Any]:
    return _read()


def save_normalizer_config(data: dict[str, Any]) -> None:
    _write(data)
