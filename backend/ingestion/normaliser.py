"""
Normaliser — filters an incoming dict against the enabled fields from
feed-fields.yaml. Unknown fields are discarded unless they match a
custom field.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.config.loader import load_fields, load_ingest_all_fields

audit = logging.getLogger("backend.audit")


def normalise(raw_data: dict[str, Any], ingest_mode: str, source_name: str, source_fields: dict | None = None) -> dict[str, Any]:
    """
    Given a raw parsed dict, return a new dict containing only enabled fields.

    - Core fields that are enabled are kept if present.
    - Custom fields are always kept if present.
    - Everything else is discarded.
    - ingest_mode and source are always set.
    - source_fields (optional): per-source overrides merged on top of global defaults.
      Shape: {"core_fields": [{"name": ..., "enabled": bool}], "custom_fields": [...]}
    """
    global_config = load_fields()

    # ── ingest_all_fields bypass ───────────────────────────────────────────
    if load_ingest_all_fields():
        result = dict(raw_data)
        result["ingest_mode"] = ingest_mode
        result["source"] = source_name
        return result

    if source_fields:
        # Build override map from source-specific core field settings
        override_map: dict[str, bool] = {
            f["name"]: f["enabled"]
            for f in source_fields.get("core_fields", [])
            if "enabled" in f
        }
        merged_core = [
            {**f, "enabled": override_map.get(f["name"], f.get("enabled", True))}
            for f in global_config.get("core_fields", [])
        ]
        # Custom fields: union of global + source-specific (source overrides global by name)
        global_custom = {f["name"]: f for f in global_config.get("custom_fields", [])}
        source_custom = {f["name"]: f for f in source_fields.get("custom_fields", [])}
        merged_custom = list({**global_custom, **source_custom}.values())
        fields_config: dict[str, Any] = {"core_fields": merged_core, "custom_fields": merged_custom}
    else:
        fields_config = global_config

    enabled_core: set[str] = {
        f["name"]
        for f in fields_config.get("core_fields", [])
        if f.get("enabled", True)
    }
    custom_names: set[str] = {
        f["name"] for f in fields_config.get("custom_fields", [])
    }
    allowed = enabled_core | custom_names

    result: dict[str, Any] = {}
    for key, value in raw_data.items():
        if key in allowed:
            result[key] = value

    result["ingest_mode"] = ingest_mode
    result["source"] = source_name

    # Store full raw payload if raw field is enabled
    if "raw" in enabled_core:
        import json
        result["raw"] = json.dumps(raw_data)

    # Audit: log accepted and dropped fields at DEBUG level
    _meta = {"ingest_mode", "source", "raw"}
    accepted = sorted(set(result.keys()) - _meta)
    dropped = sorted(set(raw_data.keys()) - allowed - _meta)
    audit.debug(
        "fields source=%s mode=%s accepted=%s dropped=%s",
        source_name, ingest_mode, accepted, dropped,
    )

    return result
