"""
Config loader — reads feed-fields.yaml and sources.yaml.
All other modules import from here; never read YAML files directly.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Resolve config directory relative to project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIELDS_PATH = _PROJECT_ROOT / "config" / "feed-fields.yaml"
SOURCES_PATH = _PROJECT_ROOT / "config" / "sources.yaml"
DEFAULT_SOURCES_PATH = _PROJECT_ROOT / "config" / "default-sources.yaml"
APP_CONFIG_PATH = _PROJECT_ROOT / "config" / "application.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("Config file missing at %s; returning empty dict", path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Fields ──────────────────────────────────────────────────────────────────


def load_fields() -> dict[str, Any]:
    """Return parsed feed-fields.yaml."""
    return _read_yaml(FIELDS_PATH)


def get_enabled_core_field_names() -> list[str]:
    """Return names of all enabled core fields."""
    data = load_fields()
    return [f["name"] for f in data.get("core_fields", []) if f.get("enabled", True)]


def get_all_field_names() -> list[str]:
    """Return names of all core + custom fields (regardless of enabled state)."""
    data = load_fields()
    core = [f["name"] for f in data.get("core_fields", [])]
    custom = [f["name"] for f in data.get("custom_fields", [])]
    return core + custom


def get_configured_field_names() -> list[str]:
    """Return names of all ENABLED core + custom fields (core first, then custom).

    prompts-032 Phase E: the "configured" canonical set offered to the LLM for
    a consolidated smart-mapping proposal when ``field_scope == 'configured'``.
    Both core and custom fields default ``enabled=True`` when the flag is
    absent. Order mirrors :func:`get_all_field_names` (core then custom) and
    duplicate names are de-duplicated, first occurrence wins.
    """
    data = load_fields()
    names: list[str] = []
    seen: set[str] = set()
    for group in ("core_fields", "custom_fields"):
        for field in data.get(group, []) or []:
            name = (field or {}).get("name")
            if not name or name in seen:
                continue
            if not (field or {}).get("enabled", True):
                continue
            names.append(name)
            seen.add(name)
    return names


def save_fields(data: dict[str, Any]) -> None:
    _write_yaml(FIELDS_PATH, data)


def load_ingest_all_fields() -> bool:
    """Return the ingest_all_fields flag from feed-fields.yaml (default True)."""
    return bool(load_fields().get("ingest_all_fields", True))


def save_ingest_all_fields(value: bool) -> None:
    """Persist the ingest_all_fields flag to feed-fields.yaml."""
    data = load_fields()
    data["ingest_all_fields"] = value
    save_fields(data)


# ── Flatten depth (prompts-015) ────────────────────────────────────────────

_FLATTEN_DEFAULT = 5
_FLATTEN_MIN = 1
_FLATTEN_MAX = 10


def load_flatten_max_depth() -> int:
    """Return the configured flatten depth for nested JSON entries.

    Default 5. Clamped to [1, 10]. Used by ingestion.parsers.flatten_entry.
    """
    raw = load_fields().get("flatten_max_depth", _FLATTEN_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _FLATTEN_DEFAULT
    if n < _FLATTEN_MIN:
        return _FLATTEN_MIN
    if n > _FLATTEN_MAX:
        return _FLATTEN_MAX
    return n


def save_flatten_max_depth(value: int) -> None:
    """Persist the flatten_max_depth setting to feed-fields.yaml."""
    if not isinstance(value, int) or value < _FLATTEN_MIN or value > _FLATTEN_MAX:
        raise ValueError(
            f"flatten_max_depth must be an integer in [{_FLATTEN_MIN}, {_FLATTEN_MAX}]"
        )
    data = load_fields()
    data["flatten_max_depth"] = value
    save_fields(data)


# ── Sources ──────────────────────────────────────────────────────────────────


def load_sources() -> dict[str, Any]:
    """Return parsed sources.yaml."""
    return _read_yaml(SOURCES_PATH)


def save_sources(data: dict[str, Any]) -> None:
    _write_yaml(SOURCES_PATH, data)


# ── Default threat-intel source catalogue (prompts-042) ───────────────────────


def load_default_sources() -> list[dict[str, Any]]:
    """Return the curated default threat-intel source catalogue.

    Reads the ``threat_intel_sources`` list from config/default-sources.yaml.
    This is a read-only catalogue maintainers edit between releases; the UI
    re-reads it on each request. Returns an empty list when the file is missing
    or malformed so the catalogue card degrades gracefully.
    """
    data = _read_yaml(DEFAULT_SOURCES_PATH)
    items = data.get("threat_intel_sources", [])
    if not isinstance(items, list):
        logger.warning(
            "threat_intel_sources in %s is not a list; ignoring", DEFAULT_SOURCES_PATH
        )
        return []
    return [item for item in items if isinstance(item, dict)]


# ── Application (prompts-017) ────────────────────────────────────────────────

_APP_PREFIX_DEFAULT = ""
_APP_PREFIX_MAX_LEN = 200
# Allowed: empty string OR a leading slash followed by one or more URL-safe chars,
# never ending in '/' and never containing two consecutive slashes.
# Anchored full-match.
_APP_PREFIX_RE = re.compile(r"^$|^/[A-Za-z0-9._\-/]*[A-Za-z0-9._\-]$")

# Env-var override (prompts-018). Set by threatfeeds-lite --base-prefix
# at uvicorn invocation time. Takes precedence over application.yaml on read.
# - absent     → read application.yaml (existing behaviour)
# - ""         → explicit root mount (yaml ignored)
# - valid val  → use as-is, log info, yaml ignored
# - invalid    → log warning, fall back to yaml
_APP_PREFIX_ENV = "SIMPLE_FEED_BASE_PREFIX"


def _is_valid_app_prefix(value: str) -> bool:
    """Return True if value is a valid app_base_prefix per the format rules."""
    return (
        _APP_PREFIX_RE.match(value) is not None
        and "//" not in value
        and len(value) <= _APP_PREFIX_MAX_LEN
    )


def load_app_config() -> dict[str, Any]:
    """Return parsed application.yaml (empty dict if missing)."""
    return _read_yaml(APP_CONFIG_PATH)


def load_app_base_prefix() -> str:
    """Return the active base-URL prefix (default empty string).

    Precedence:
      1. SIMPLE_FEED_BASE_PREFIX environment variable (if set)
      2. app_base_prefix in config/application.yaml
      3. "" (mount at root)

    The env-var path is set by the threatfeeds-lite runner script via
    ``--base-prefix``. When set to an empty string it explicitly means
    "mount at root" and yaml is bypassed. When set to an invalid value it
    is ignored with a warning and yaml is consulted instead.
    """
    env_raw = os.environ.get(_APP_PREFIX_ENV)
    if env_raw is not None:
        if env_raw == "":
            logger.info(
                "app_base_prefix overridden by %s (empty string → mount at root)",
                _APP_PREFIX_ENV,
            )
            return ""
        if _is_valid_app_prefix(env_raw):
            logger.info(
                "app_base_prefix overridden by %s=%r", _APP_PREFIX_ENV, env_raw,
            )
            return env_raw
        logger.warning(
            "%s=%r is invalid; falling back to %s",
            _APP_PREFIX_ENV, env_raw, APP_CONFIG_PATH,
        )

    raw = load_app_config().get("app_base_prefix", _APP_PREFIX_DEFAULT)
    if not isinstance(raw, str):
        return _APP_PREFIX_DEFAULT
    # Be tolerant on read: silently coerce unexpected non-conforming values to "".
    # Strict validation only happens on save.
    if not _is_valid_app_prefix(raw):
        logger.warning(
            "app_base_prefix in %s is invalid (%r); ignoring and using empty prefix",
            APP_CONFIG_PATH, raw,
        )
        return _APP_PREFIX_DEFAULT
    return raw


def save_app_base_prefix(value: str) -> None:
    """Persist the app_base_prefix to application.yaml.

    Raises ValueError if the value is not a valid prefix per the format rules
    documented in application.yaml.
    """
    if not isinstance(value, str):
        raise ValueError("app_base_prefix must be a string")
    if len(value) > _APP_PREFIX_MAX_LEN:
        raise ValueError(
            f"app_base_prefix exceeds maximum length of {_APP_PREFIX_MAX_LEN}"
        )
    if "//" in value:
        raise ValueError("app_base_prefix must not contain '//'")
    if not _APP_PREFIX_RE.match(value):
        raise ValueError(
            "app_base_prefix must be empty or start with '/', "
            "not end with '/', and use only [A-Za-z0-9._-/]"
        )
    data = load_app_config()
    data["app_base_prefix"] = value
    _write_yaml(APP_CONFIG_PATH, data)


# ── Normalized viewer pagination cap (prompts-043) ───────────────────────────

# Ceiling on rows the Normalized Feeds viewer pulls in a single request. The
# viewer paginates/filters/searches client-side over this window, so this caps
# the working set rather than a per-page size. Default 1000, bounded.
_PAGINATION_MAX_DEFAULT = 1000
_PAGINATION_MAX_MIN = 50
_PAGINATION_MAX_MAX = 100_000


def load_app_pagination_max() -> int:
    """Return the configured Normalized-viewer pagination cap (default 1000).

    Non-integer or out-of-range values on disk fall back to the default with a
    warning, so a malformed config never breaks the viewer.
    """
    raw = load_app_config().get("pagination_max", _PAGINATION_MAX_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "pagination_max in %s is not an integer (%r); using default %d",
            APP_CONFIG_PATH, raw, _PAGINATION_MAX_DEFAULT,
        )
        return _PAGINATION_MAX_DEFAULT
    if n < _PAGINATION_MAX_MIN or n > _PAGINATION_MAX_MAX:
        logger.warning(
            "pagination_max=%d is out of range [%d, %d]; using default %d",
            n, _PAGINATION_MAX_MIN, _PAGINATION_MAX_MAX, _PAGINATION_MAX_DEFAULT,
        )
        return _PAGINATION_MAX_DEFAULT
    return n


def save_app_pagination_max(value: int) -> None:
    """Persist the Normalized-viewer pagination cap to application.yaml.

    Raises ValueError when the value is not an integer in the supported range.
    Booleans are rejected explicitly (bool is a subclass of int).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("pagination_max must be an integer")
    if value < _PAGINATION_MAX_MIN or value > _PAGINATION_MAX_MAX:
        raise ValueError(
            f"pagination_max must be between {_PAGINATION_MAX_MIN} "
            f"and {_PAGINATION_MAX_MAX}"
        )
    data = load_app_config()
    data["pagination_max"] = value
    _write_yaml(APP_CONFIG_PATH, data)


# ── Watcher event retention cap (issue_local_006) ────────────────────────────

# Global ceiling on how many triggered events each watcher retains, and the
# hard limit for the Watcher Details full-list view. Default 1000, bounded.
_WATCHER_MAX_EVENTS_DEFAULT = 1000
_WATCHER_MAX_EVENTS_MIN = 10
_WATCHER_MAX_EVENTS_MAX = 100_000


def load_watcher_max_events() -> int:
    """Return the configured per-watcher event retention cap (default 1000).

    Non-integer or out-of-range values on disk fall back to the default with a
    warning, so a malformed config never breaks watcher evaluation.
    """
    raw = load_app_config().get("watcher_max_events", _WATCHER_MAX_EVENTS_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "watcher_max_events in %s is not an integer (%r); using default %d",
            APP_CONFIG_PATH, raw, _WATCHER_MAX_EVENTS_DEFAULT,
        )
        return _WATCHER_MAX_EVENTS_DEFAULT
    if n < _WATCHER_MAX_EVENTS_MIN or n > _WATCHER_MAX_EVENTS_MAX:
        logger.warning(
            "watcher_max_events=%d is out of range [%d, %d]; using default %d",
            n, _WATCHER_MAX_EVENTS_MIN, _WATCHER_MAX_EVENTS_MAX,
            _WATCHER_MAX_EVENTS_DEFAULT,
        )
        return _WATCHER_MAX_EVENTS_DEFAULT
    return n


def save_watcher_max_events(value: int) -> None:
    """Persist the per-watcher event retention cap to application.yaml.

    Raises ValueError when the value is not an integer in the supported range.
    Booleans are rejected explicitly (bool is a subclass of int).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("watcher_max_events must be an integer")
    if value < _WATCHER_MAX_EVENTS_MIN or value > _WATCHER_MAX_EVENTS_MAX:
        raise ValueError(
            f"watcher_max_events must be between {_WATCHER_MAX_EVENTS_MIN} "
            f"and {_WATCHER_MAX_EVENTS_MAX}"
        )
    data = load_app_config()
    data["watcher_max_events"] = value
    _write_yaml(APP_CONFIG_PATH, data)


# ── Authentication toggle (prompts-045) ──────────────────────────────────────
# Env-var override set by threatfeeds-lite --enable-auth at uvicorn
# invocation time. Takes precedence over application.yaml on read.
#   - absent              → read application.yaml (default false)
#   - "1"/"true"/"yes"/"on" (case-insensitive) → auth ON
#   - anything else       → auth OFF
# When auth is OFF the app is fully open, exactly as before prompts-045.
_AUTH_ENABLED_ENV = "SIMPLE_FEED_ENABLE_AUTH"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def load_auth_enabled() -> bool:
    """Return whether authentication enforcement is enabled (default False).

    Precedence:
      1. SIMPLE_FEED_ENABLE_AUTH environment variable (if set)
      2. auth_enabled in config/application.yaml
      3. False (app fully open)
    """
    env_raw = os.environ.get(_AUTH_ENABLED_ENV)
    if env_raw is not None:
        enabled = env_raw.strip().lower() in _TRUTHY
        logger.info(
            "auth_enabled overridden by %s=%r → %s",
            _AUTH_ENABLED_ENV, env_raw, enabled,
        )
        return enabled
    return bool(load_app_config().get("auth_enabled", False))


def save_auth_enabled(value: bool) -> None:
    """Persist the auth_enabled flag to application.yaml."""
    if not isinstance(value, bool):
        raise ValueError("auth_enabled must be a boolean")
    data = load_app_config()
    data["auth_enabled"] = value
    _write_yaml(APP_CONFIG_PATH, data)


_COOKIE_SECURE_ENV = "SIMPLE_FEED_COOKIE_SECURE"
_FALSEY = frozenset({"0", "false", "no", "off"})


def load_cookie_secure() -> bool | None:
    """Return the session-cookie ``Secure`` override, or None for auto-detect.

    Behind a TLS-terminating proxy the request scheme is plain HTTP and the only
    signal is the spoofable ``X-Forwarded-Proto`` header. Operators who serve
    over HTTPS in production can force the ``Secure`` flag instead of trusting
    that header.

    Precedence:
      1. SIMPLE_FEED_COOKIE_SECURE env (true/false, or "auto")
      2. cookie_secure in application.yaml (bool, or "auto")
      3. None → auto-detect from the request scheme / X-Forwarded-Proto
    """
    env_raw = os.environ.get(_COOKIE_SECURE_ENV)
    if env_raw is not None:
        val = env_raw.strip().lower()
        if val in _TRUTHY:
            return True
        if val in _FALSEY:
            return False
        return None  # "auto" or anything else
    raw = load_app_config().get("cookie_secure", "auto")
    if isinstance(raw, bool):
        return raw
    return None


# ── Branding logo (prompts-045) ──────────────────────────────────────────────

# Path (relative to project root) of the uploaded branding logo, or "" when
# none is configured and the default icon is used. Stored in application.yaml;
# the image bytes live under data/branding/.
_LOGO_PATH_DEFAULT = ""
_LOGO_PATH_MAX_LEN = 500


def load_logo_path() -> str:
    """Return the configured branding logo path (relative), or "" if unset."""
    raw = load_app_config().get("logo_path", _LOGO_PATH_DEFAULT)
    if not isinstance(raw, str):
        return _LOGO_PATH_DEFAULT
    return raw


def save_logo_path(value: str) -> None:
    """Persist the branding logo path to application.yaml.

    Accepts "" (no logo) or a project-relative path under data/branding/.
    Rejects absolute paths and parent-directory traversal.
    """
    if not isinstance(value, str):
        raise ValueError("logo_path must be a string")
    if len(value) > _LOGO_PATH_MAX_LEN:
        raise ValueError(f"logo_path exceeds maximum length of {_LOGO_PATH_MAX_LEN}")
    if value:
        norm = value.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError("logo_path must be a relative path without '..'")
        if not norm.startswith("data/branding/"):
            raise ValueError("logo_path must be under data/branding/")
    data = load_app_config()
    data["logo_path"] = value
    _write_yaml(APP_CONFIG_PATH, data)


# ── Password policy (prompts-046) ────────────────────────────────────────────
#
# The password strength rules are operator-configurable. Two knobs:
#   password_min_length        minimum length in characters (clamped [8, 64])
#   password_required_classes  how many of the four character classes
#                              {lowercase, uppercase, digit, symbol} a password
#                              must contain (clamped [1, 4])
# The 72-byte bcrypt ceiling is a hard limit and is NOT configurable. The
# minimum is measured in *characters* but the maximum is enforced in *bytes*
# (72). The min-length ceiling is therefore 64 (not 72) so a password meeting
# the minimum always has byte headroom for some multi-byte characters and the
# policy can never become effectively unsatisfiable for non-ASCII input.
_PASSWORD_MIN_LEN_DEFAULT = 8
_PASSWORD_MIN_LEN_FLOOR = 8        # never allow a weaker minimum than 8
_PASSWORD_MIN_LEN_CEIL = 64        # leaves byte headroom under the 72-byte cap
_PASSWORD_CLASSES_DEFAULT = 3
_PASSWORD_CLASSES_MIN = 1
_PASSWORD_CLASSES_MAX = 4
_PASSWORD_MAX_BYTES = 72           # bcrypt hard limit (fixed)


def _clamp_int(raw: Any, default: int, lo: int, hi: int, label: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s in %s is not an integer (%r); using default %d",
            label, APP_CONFIG_PATH, raw, default,
        )
        return default
    if n < lo or n > hi:
        clamped = min(max(n, lo), hi)
        logger.warning(
            "%s=%d is out of range [%d, %d]; clamping to %d",
            label, n, lo, hi, clamped,
        )
        return clamped
    return n


def load_password_policy() -> dict[str, int]:
    """Return the configured password policy (with safe, clamped defaults).

    Shape: ``{"min_length": int, "required_classes": int, "max_bytes": 72}``.
    A malformed or out-of-range value never weakens security below the floor
    (min length >= 8, classes in [1, 4]); it is clamped with a warning.

    This runs on the unauthenticated ``GET /api/auth/status`` endpoint, so a
    corrupt ``application.yaml`` (invalid YAML, or a non-mapping top level) must
    not surface as a 500. On any read failure we fall back to defaults.
    """
    try:
        cfg = load_app_config()
        if not isinstance(cfg, dict):
            raise TypeError(f"application.yaml top level is {type(cfg).__name__}, not a mapping")
    except (yaml.YAMLError, OSError, TypeError) as exc:
        logger.warning(
            "Could not read password policy from %s (%s); using defaults",
            APP_CONFIG_PATH, exc,
        )
        cfg = {}
    min_length = _clamp_int(
        cfg.get("password_min_length", _PASSWORD_MIN_LEN_DEFAULT),
        _PASSWORD_MIN_LEN_DEFAULT,
        _PASSWORD_MIN_LEN_FLOOR,
        _PASSWORD_MIN_LEN_CEIL,
        "password_min_length",
    )
    required_classes = _clamp_int(
        cfg.get("password_required_classes", _PASSWORD_CLASSES_DEFAULT),
        _PASSWORD_CLASSES_DEFAULT,
        _PASSWORD_CLASSES_MIN,
        _PASSWORD_CLASSES_MAX,
        "password_required_classes",
    )
    return {
        "min_length": min_length,
        "required_classes": required_classes,
        "max_bytes": _PASSWORD_MAX_BYTES,
    }


# ── Decompression size cap (prompts-021B) ────────────────────────────────────

_MAX_DECOMPRESSED_DEFAULT = 100 * 1024 * 1024  # 100 MiB
_MAX_DECOMPRESSED_MIN = 1024                   # 1 KiB lower bound (sanity)


def load_max_decompressed_bytes() -> int:
    """Return the configured cap (bytes) for decompressed feed payloads.

    Default 100 MiB. Values below ``_MAX_DECOMPRESSED_MIN`` are coerced
    up to that floor with a warning. Non-integer values fall back to
    the default with a warning. Used by
    ``backend.ingestion.decompression.decompress_if_needed`` via the
    callers in ``local_feed``, ``remote_feed`` and ``preview``.
    """
    raw = load_app_config().get("max_decompressed_bytes", _MAX_DECOMPRESSED_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "max_decompressed_bytes in %s is not an integer (%r); using default %d",
            APP_CONFIG_PATH, raw, _MAX_DECOMPRESSED_DEFAULT,
        )
        return _MAX_DECOMPRESSED_DEFAULT
    if n < _MAX_DECOMPRESSED_MIN:
        logger.warning(
            "max_decompressed_bytes=%d is below floor %d; coercing up",
            n, _MAX_DECOMPRESSED_MIN,
        )
        return _MAX_DECOMPRESSED_MIN
    return n
