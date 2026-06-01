"""Tests for YAML loader graceful-fallback behaviour."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_app_prefix_env(monkeypatch):
    """Ensure SIMPLE_FEED_BASE_PREFIX never leaks in from the developer's shell.

    Individual tests that exercise the env-override path can re-set it via
    ``monkeypatch.setenv(...)``; the autouse fixture only clears the baseline.
    """
    monkeypatch.delenv("SIMPLE_FEED_BASE_PREFIX", raising=False)


def test_load_fields_missing_file_returns_empty(tmp_path, monkeypatch):
    """When feed-fields.yaml is missing, load_fields() returns {} (no exception)."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "FIELDS_PATH", tmp_path / "missing-fields.yaml")
    assert loader.load_fields() == {}


def test_load_sources_missing_file_returns_empty(tmp_path, monkeypatch):
    """When sources.yaml is missing, load_sources() returns {} (no exception)."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "SOURCES_PATH", tmp_path / "missing-sources.yaml")
    assert loader.load_sources() == {}


def test_load_default_sources_missing_file_returns_empty(tmp_path, monkeypatch):
    """When default-sources.yaml is missing, load_default_sources() returns []."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "DEFAULT_SOURCES_PATH", tmp_path / "missing.yaml")
    assert loader.load_default_sources() == []


def test_load_default_sources_parses_catalogue(tmp_path, monkeypatch):
    """load_default_sources() returns the threat_intel_sources list of dicts."""
    import backend.config.loader as loader
    target = tmp_path / "default-sources.yaml"
    target.write_text(
        "threat_intel_sources:\n"
        "  - name: cisa_kev\n"
        "    title: CISA KEV\n"
        "    kind: remote_json_pull\n"
        "    url: https://example.com/kev.json\n"
        "    info: actively exploited CVEs\n"
        "    default_interval_minutes: 360\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "DEFAULT_SOURCES_PATH", target)
    items = loader.load_default_sources()
    assert len(items) == 1
    assert items[0]["name"] == "cisa_kev"
    assert items[0]["kind"] == "remote_json_pull"


def test_load_default_sources_ignores_non_list(tmp_path, monkeypatch):
    """A malformed threat_intel_sources (non-list) yields [] with no exception."""
    import backend.config.loader as loader
    target = tmp_path / "default-sources.yaml"
    target.write_text("threat_intel_sources: not-a-list\n", encoding="utf-8")
    monkeypatch.setattr(loader, "DEFAULT_SOURCES_PATH", target)
    assert loader.load_default_sources() == []


def test_real_default_sources_catalogue_is_wellformed():
    """The shipped config/default-sources.yaml loads and every entry is valid."""
    import backend.config.loader as loader
    items = loader.load_default_sources()
    assert len(items) >= 30
    names = [it["name"] for it in items]
    assert len(names) == len(set(names)), "duplicate catalogue names"
    for it in items:
        assert it["kind"] in {"rss_pull", "remote_json_pull"}
        assert it["url"].startswith("http")
        assert it.get("title")
        assert isinstance(it.get("default_interval_minutes"), int)


def test_save_fields_creates_parent_dir(tmp_path, monkeypatch):
    """save_fields() auto-creates parent dirs that do not yet exist."""
    import backend.config.loader as loader
    target = tmp_path / "newdir" / "feed-fields.yaml"
    monkeypatch.setattr(loader, "FIELDS_PATH", target)
    loader.save_fields({"core_fields": []})
    assert target.exists()


def test_load_ingest_all_fields_defaults_true(tmp_path, monkeypatch):
    """When feed-fields.yaml is missing or has no ingest_all_fields key, default is True."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "FIELDS_PATH", tmp_path / "missing.yaml")
    assert loader.load_ingest_all_fields() is True

    # Also true when file exists but key absent
    p = tmp_path / "present.yaml"
    p.write_text("core_fields: []\n")
    monkeypatch.setattr(loader, "FIELDS_PATH", p)
    assert loader.load_ingest_all_fields() is True

    # False when explicitly set
    p.write_text("ingest_all_fields: false\n")
    assert loader.load_ingest_all_fields() is False


# ── prompts-032 Phase E: configured (enabled) field names ─────────────────────


def test_get_configured_field_names_filters_disabled(tmp_path, monkeypatch):
    """Only ENABLED core + custom fields are returned, core first then custom.

    Fields with no ``enabled`` key default to enabled=True.
    """
    import backend.config.loader as loader
    p = tmp_path / "feed-fields.yaml"
    p.write_text(
        "core_fields:\n"
        "  - name: indicator\n"
        "    enabled: true\n"
        "  - name: severity\n"          # no enabled key → defaults True
        "  - name: confidence\n"
        "    enabled: false\n"
        "custom_fields:\n"
        "  - name: vendor_score\n"
        "    enabled: true\n"
        "  - name: internal_only\n"
        "    enabled: false\n"
    )
    monkeypatch.setattr(loader, "FIELDS_PATH", p)
    assert loader.get_configured_field_names() == [
        "indicator", "severity", "vendor_score",
    ]


def test_get_configured_field_names_missing_file_empty(tmp_path, monkeypatch):
    """Missing feed-fields.yaml yields an empty configured set (no exception)."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "FIELDS_PATH", tmp_path / "missing.yaml")
    assert loader.get_configured_field_names() == []


# ── prompts-017: app_base_prefix ──────────────────────────────────────────────


def test_load_app_base_prefix_defaults_empty(tmp_path, monkeypatch):
    """Missing file or missing key -> empty string."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing-app.yaml")
    assert loader.load_app_base_prefix() == ""


def test_load_app_base_prefix_invalid_silently_falls_back(tmp_path, monkeypatch):
    """A malformed app_base_prefix on disk is logged and treated as empty."""
    import backend.config.loader as loader
    p = tmp_path / "application.yaml"
    p.write_text('app_base_prefix: "no-leading-slash"\n')
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", p)
    assert loader.load_app_base_prefix() == ""


@pytest.mark.parametrize("prefix", ["", "/feeds", "/a/b", "/x.y_z-1", "/api/v1"])
def test_save_app_base_prefix_accepts_valid(tmp_path, monkeypatch, prefix):
    """All format-conformant prefixes round-trip via save/load."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    loader.save_app_base_prefix(prefix)
    assert loader.load_app_base_prefix() == prefix


@pytest.mark.parametrize("bad", [
    "feeds",          # missing leading slash
    "/feeds/",        # trailing slash
    "/has spaces",    # whitespace
    "http://x",       # scheme
    "/a//b",          # double slash
    "/" + "a" * 250,  # too long
    "/end-with-slash/",
])
def test_save_app_base_prefix_rejects_invalid(tmp_path, monkeypatch, bad):
    """Invalid prefixes raise ValueError; nothing is written."""
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    with pytest.raises(ValueError):
        loader.save_app_base_prefix(bad)
    # No file created on failed save.
    assert not target.exists()


def test_save_app_base_prefix_rejects_non_string(tmp_path, monkeypatch):
    """Non-string payloads (incl. None, int, bool) raise ValueError."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    for bad in [None, 42, True, ["/feeds"], {"v": "/feeds"}]:
        with pytest.raises(ValueError):
            loader.save_app_base_prefix(bad)  # type: ignore[arg-type]


# ── prompts-018: SIMPLE_FEED_BASE_PREFIX env-var override ────────────────────


def _write_yaml_prefix(tmp_path, monkeypatch, value):
    """Helper: point loader at a fresh application.yaml carrying ``value``."""
    import backend.config.loader as loader
    p = tmp_path / "application.yaml"
    p.write_text(f'app_base_prefix: "{value}"\n')
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", p)
    return loader


def test_load_app_base_prefix_env_override_valid_wins_over_yaml(
    tmp_path, monkeypatch,
):
    """SIMPLE_FEED_BASE_PREFIX with a valid value beats the yaml file."""
    loader = _write_yaml_prefix(tmp_path, monkeypatch, "/yamlpath")
    monkeypatch.setenv("SIMPLE_FEED_BASE_PREFIX", "/feeds")
    assert loader.load_app_base_prefix() == "/feeds"


def test_load_app_base_prefix_env_override_empty_string_means_root(
    tmp_path, monkeypatch,
):
    """An explicit empty-string env override mounts at root, ignoring yaml."""
    loader = _write_yaml_prefix(tmp_path, monkeypatch, "/yamlpath")
    monkeypatch.setenv("SIMPLE_FEED_BASE_PREFIX", "")
    assert loader.load_app_base_prefix() == ""


def test_load_app_base_prefix_env_invalid_falls_back_to_yaml(
    tmp_path, monkeypatch, caplog,
):
    """An invalid env override is ignored with a warning; yaml is consulted."""
    import logging
    loader = _write_yaml_prefix(tmp_path, monkeypatch, "/yamlpath")
    monkeypatch.setenv("SIMPLE_FEED_BASE_PREFIX", "not-valid")  # no leading slash
    with caplog.at_level(logging.WARNING, logger="backend.config.loader"):
        result = loader.load_app_base_prefix()
    assert result == "/yamlpath"
    assert any(
        "SIMPLE_FEED_BASE_PREFIX" in rec.getMessage() and "invalid" in rec.getMessage()
        for rec in caplog.records
    )


def test_load_app_base_prefix_env_absent_uses_yaml(tmp_path, monkeypatch):
    """When env is unset, yaml is used as before (regression guard)."""
    loader = _write_yaml_prefix(tmp_path, monkeypatch, "/yamlpath")
    monkeypatch.delenv("SIMPLE_FEED_BASE_PREFIX", raising=False)
    assert loader.load_app_base_prefix() == "/yamlpath"


# ── prompts-043: pagination_max ──────────────────────────────────────────────


def test_load_pagination_max_defaults(tmp_path, monkeypatch):
    """Missing file or key -> default 1000."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing-app.yaml")
    assert loader.load_app_pagination_max() == 1000


@pytest.mark.parametrize("bad", ["abc", "0", "99999999"])
def test_load_pagination_max_invalid_falls_back(tmp_path, monkeypatch, bad):
    """Non-integer or out-of-range on disk falls back to the default."""
    import backend.config.loader as loader
    p = tmp_path / "application.yaml"
    p.write_text(f'pagination_max: "{bad}"\n')
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", p)
    assert loader.load_app_pagination_max() == 1000


@pytest.mark.parametrize("value", [50, 100, 1000, 100_000])
def test_save_pagination_max_round_trip(tmp_path, monkeypatch, value):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    loader.save_app_pagination_max(value)
    assert loader.load_app_pagination_max() == value


def test_save_pagination_max_rejects_invalid(tmp_path, monkeypatch):
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    for bad in [None, "100", True, 49, 0, -1, 100_001, 1.5]:
        with pytest.raises(ValueError):
            loader.save_app_pagination_max(bad)  # type: ignore[arg-type]
    assert not target.exists()


def test_save_pagination_max_preserves_other_keys(tmp_path, monkeypatch):
    """Saving pagination_max must not clobber app_base_prefix."""
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    loader.save_app_base_prefix("/feeds")
    loader.save_app_pagination_max(200)
    assert loader.load_app_base_prefix() == "/feeds"
    assert loader.load_app_pagination_max() == 200


# ── auth_enabled (prompts-045) ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_auth_env(monkeypatch):
    monkeypatch.delenv("SIMPLE_FEED_ENABLE_AUTH", raising=False)


def test_load_auth_enabled_default_false(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing.yaml")
    assert loader.load_auth_enabled() is False


def test_load_auth_enabled_from_yaml(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    loader.save_auth_enabled(True)
    assert loader.load_auth_enabled() is True
    loader.save_auth_enabled(False)
    assert loader.load_auth_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_load_auth_enabled_env_override(tmp_path, monkeypatch, val, expected):
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    # yaml says True; env must win regardless.
    loader.save_auth_enabled(True)
    monkeypatch.setenv("SIMPLE_FEED_ENABLE_AUTH", val)
    assert loader.load_auth_enabled() is expected


def test_save_auth_enabled_rejects_non_bool(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    for bad in [None, "true", 1, 0]:
        with pytest.raises(ValueError):
            loader.save_auth_enabled(bad)  # type: ignore[arg-type]


# ── logo_path (prompts-045) ───────────────────────────────────────────────────


def test_load_logo_path_default_empty(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing.yaml")
    assert loader.load_logo_path() == ""


def test_save_and_load_logo_path(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    loader.save_logo_path("data/branding/logo.png")
    assert loader.load_logo_path() == "data/branding/logo.png"
    loader.save_logo_path("")
    assert loader.load_logo_path() == ""


def test_save_logo_path_rejects_traversal_and_absolute(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "application.yaml")
    for bad in [
        "/etc/passwd",
        "data/branding/../../etc/passwd",
        "../secret.png",
        "logo.png",                      # not under data/branding/
        123,                             # not a string
    ]:
        with pytest.raises(ValueError):
            loader.save_logo_path(bad)  # type: ignore[arg-type]


# ── cookie_secure (prompts-045 audit MINOR) ───────────────────────────────────


def test_load_cookie_secure_default_auto(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.delenv("SIMPLE_FEED_COOKIE_SECURE", raising=False)
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing.yaml")
    assert loader.load_cookie_secure() is None


def test_load_cookie_secure_from_yaml(tmp_path, monkeypatch):
    import yaml
    import backend.config.loader as loader
    monkeypatch.delenv("SIMPLE_FEED_COOKIE_SECURE", raising=False)
    target = tmp_path / "application.yaml"
    target.write_text(yaml.safe_dump({"cookie_secure": True}), encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    assert loader.load_cookie_secure() is True
    target.write_text(yaml.safe_dump({"cookie_secure": False}), encoding="utf-8")
    assert loader.load_cookie_secure() is False
    target.write_text(yaml.safe_dump({"cookie_secure": "auto"}), encoding="utf-8")
    assert loader.load_cookie_secure() is None


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("on", True),
    ("0", False), ("false", False), ("off", False),
    ("auto", None), ("", None),
])
def test_load_cookie_secure_env_override(tmp_path, monkeypatch, val, expected):
    import yaml
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    # yaml says True; env must win regardless.
    target.write_text(yaml.safe_dump({"cookie_secure": True}), encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    monkeypatch.setenv("SIMPLE_FEED_COOKIE_SECURE", val)
    assert loader.load_cookie_secure() is expected


# ── password policy (prompts-046) ────────────────────────────────────────────

def test_load_password_policy_defaults(tmp_path, monkeypatch):
    import backend.config.loader as loader
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", tmp_path / "missing.yaml")
    assert loader.load_password_policy() == {
        "min_length": 8,
        "required_classes": 3,
        "max_bytes": 72,
    }


def test_load_password_policy_from_yaml(tmp_path, monkeypatch):
    import yaml
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    target.write_text(
        yaml.safe_dump(
            {"password_min_length": 12, "password_required_classes": 4}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    policy = loader.load_password_policy()
    assert policy["min_length"] == 12
    assert policy["required_classes"] == 4
    assert policy["max_bytes"] == 72


@pytest.mark.parametrize("min_len,classes,exp_len,exp_cls", [
    (4, 3, 8, 3),       # below floor → clamped up to 8
    (100, 3, 64, 3),    # above ceiling → clamped to 64 (byte headroom)
    (10, 0, 10, 1),     # classes below 1 → clamped to 1
    (10, 9, 10, 4),     # classes above 4 → clamped to 4
    ("oops", "x", 8, 3),  # non-integer → defaults
])
def test_load_password_policy_clamps_and_defaults(
    tmp_path, monkeypatch, min_len, classes, exp_len, exp_cls
):
    import yaml
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    target.write_text(
        yaml.safe_dump(
            {"password_min_length": min_len, "password_required_classes": classes}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    policy = loader.load_password_policy()
    assert policy["min_length"] == exp_len
    assert policy["required_classes"] == exp_cls


def test_load_password_policy_min_length_ceiling_leaves_byte_headroom():
    """The min-length ceiling must stay below the 72-byte cap so a password
    meeting the minimum always has room for some multi-byte characters."""
    import backend.config.loader as loader
    assert loader._PASSWORD_MIN_LEN_CEIL < loader._PASSWORD_MAX_BYTES


def test_load_password_policy_invalid_yaml_falls_back_to_defaults(tmp_path, monkeypatch):
    """A corrupt application.yaml must not 500 the public status endpoint."""
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    target.write_text("password_min_length: [unbalanced\n", encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    assert loader.load_password_policy() == {
        "min_length": 8,
        "required_classes": 3,
        "max_bytes": 72,
    }


def test_load_password_policy_non_mapping_yaml_falls_back_to_defaults(tmp_path, monkeypatch):
    """A YAML file whose top level is a list/scalar must fall back, not raise."""
    import backend.config.loader as loader
    target = tmp_path / "application.yaml"
    target.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", target)
    assert loader.load_password_policy() == {
        "min_length": 8,
        "required_classes": 3,
        "max_bytes": 72,
    }
