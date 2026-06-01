"""Tests for backend.llm.config (prompts-021D)."""
from __future__ import annotations

import pytest
import yaml

from backend.llm import config as cfg_mod
from backend.llm.errors import LLMConfigError


@pytest.fixture(autouse=True)
def _isolate_config_path(tmp_path, monkeypatch):
    """Redirect _LLM_CONFIG_PATH to a tmp file for every test."""
    monkeypatch.setattr(cfg_mod, "_LLM_CONFIG_PATH", tmp_path / "llm-providers.yaml")
    yield


def test_missing_file_returns_defaults():
    result = cfg_mod.load_llm_config()
    assert result["enabled"] is False
    assert result["default_provider"] is None
    assert result["providers"] == []


def test_partial_yaml_merges_defaults():
    cfg_mod._LLM_CONFIG_PATH.write_text("enabled: false\n")
    result = cfg_mod.load_llm_config()
    assert result["enabled"] is False
    assert result["providers"] == []          # default preserved
    assert result["default_provider"] is None  # default preserved


def test_redact_replaces_nonempty_api_key_with_stars():
    cfg = {
        "enabled": True,
        "providers": [
            {"name": "a", "kind": "openai", "api_key": "sk-secret-123"},
        ],
    }
    red = cfg_mod.redact_config(cfg)
    assert red["providers"][0]["api_key"] == "***"
    # Original must not be mutated.
    assert cfg["providers"][0]["api_key"] == "sk-secret-123"


def test_redact_leaves_empty_api_key_as_empty_string():
    cfg = {"providers": [{"name": "a", "kind": "ollama", "api_key": ""}]}
    red = cfg_mod.redact_config(cfg)
    assert red["providers"][0]["api_key"] == ""


def test_merge_write_only_key_retains_existing_when_stars():
    existing = {"providers": [{"name": "a", "kind": "openai", "api_key": "sk-real"}]}
    incoming = {"providers": [{"name": "a", "kind": "openai", "api_key": "***"}]}
    merged = cfg_mod.merge_write_only_key(incoming, existing)
    assert merged["providers"][0]["api_key"] == "sk-real"


def test_merge_write_only_key_replaces_when_new_value():
    existing = {"providers": [{"name": "a", "kind": "openai", "api_key": "sk-old"}]}
    incoming = {"providers": [{"name": "a", "kind": "openai", "api_key": "sk-new"}]}
    merged = cfg_mod.merge_write_only_key(incoming, existing)
    assert merged["providers"][0]["api_key"] == "sk-new"


def test_validate_rejects_unknown_kind():
    bad = {"enabled": False, "providers": [{"name": "a", "kind": "magic"}]}
    with pytest.raises(LLMConfigError, match="kind must be one of"):
        cfg_mod.validate_config(bad)


def test_validate_rejects_duplicate_names():
    bad = {
        "enabled": False,
        "providers": [
            {"name": "x", "kind": "openai"},
            {"name": "x", "kind": "ollama"},
        ],
    }
    with pytest.raises(LLMConfigError, match="duplicate provider name"):
        cfg_mod.validate_config(bad)


def test_validate_rejects_unknown_default_provider():
    bad = {
        "enabled": False,
        "default_provider": "nope",
        "providers": [{"name": "x", "kind": "openai"}],
    }
    with pytest.raises(LLMConfigError, match="default_provider"):
        cfg_mod.validate_config(bad)


def test_save_and_reload_round_trip():
    data = {
        "enabled": False,
        "default_provider": "openai-prod",
        "providers": [
            {
                "name": "openai-prod",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": "sk-stored",
            }
        ],
    }
    cfg_mod.save_llm_config(data)
    reloaded = cfg_mod.load_llm_config()
    assert reloaded["default_provider"] == "openai-prod"
    assert reloaded["providers"][0]["api_key"] == "sk-stored"
    # And confirm the file on disk is valid yaml.
    on_disk = yaml.safe_load(cfg_mod._LLM_CONFIG_PATH.read_text())
    assert on_disk["providers"][0]["name"] == "openai-prod"


# ── prompts-022: validate_new_provider_name ─────────────────────────────────


@pytest.mark.parametrize("name", ["openai-prod", "my_llm_1", "A", "x" * 40, "a-b_c-1"])
def test_validate_new_provider_name_accepts_valid_identifiers(name):
    cfg_mod.validate_new_provider_name(name, existing_names=set())


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("", "non-empty"),
        ("   ", "non-empty"),
        ("a b", "match"),
        ("a.b", "match"),
        ("a/b", "match"),
        ("a:b", "match"),
        ("ä", "match"),
        ("x" * 41, "match"),
    ],
)
def test_validate_new_provider_name_rejects_bad_identifiers(bad, reason):
    with pytest.raises(LLMConfigError, match=reason):
        cfg_mod.validate_new_provider_name(bad, existing_names=set())


def test_validate_new_provider_name_rejects_non_string():
    with pytest.raises(LLMConfigError, match="non-empty string"):
        cfg_mod.validate_new_provider_name(123, existing_names=set())  # type: ignore[arg-type]


def test_validate_new_provider_name_rejects_duplicate():
    with pytest.raises(LLMConfigError, match="already exists"):
        cfg_mod.validate_new_provider_name(
            "openai-prod", existing_names={"openai-prod", "ollama-local"},
        )


def test_existing_legacy_names_are_grandfathered_by_save_llm_config():
    """A legacy provider with a name that violates the regex (e.g. contains
    a dot) must round-trip through save_llm_config without being rejected;
    validate_new_provider_name is only enforced on new additions."""
    legacy_name = "legacy.with.dots"
    cfg_mod.save_llm_config({
        "enabled": False,
        "default_provider": None,
        "providers": [{"name": legacy_name, "kind": "openai"}],
    })
    reloaded = cfg_mod.load_llm_config()
    assert reloaded["providers"][0]["name"] == legacy_name
    # But adding a new one with the same broken format must still fail.
    with pytest.raises(LLMConfigError, match="match"):
        cfg_mod.validate_new_provider_name(
            "another.bad.name", existing_names={legacy_name},
        )


# ── available_models field (prompts-027) ───────────────────────────────────


def test_validate_accepts_optional_available_models():
    cfg_mod.save_llm_config({
        "enabled": False,
        "providers": [
            {
                "name": "p1",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk",
                "available_models": ["m1", "m2"],
            },
        ],
    })
    loaded = cfg_mod.load_llm_config()
    assert loaded["providers"][0]["available_models"] == ["m1", "m2"]


def test_validate_rejects_non_list_available_models():
    with pytest.raises(LLMConfigError, match="available_models"):
        cfg_mod.save_llm_config({
            "enabled": False,
            "providers": [
                {
                    "name": "p1", "kind": "openai", "base_url": "https://x",
                    "model": "m", "api_key": "sk",
                    "available_models": "not-a-list",
                },
            ],
        })


def test_validate_rejects_empty_strings_in_available_models():
    with pytest.raises(LLMConfigError, match="available_models"):
        cfg_mod.save_llm_config({
            "enabled": False,
            "providers": [
                {
                    "name": "p1", "kind": "openai", "base_url": "https://x",
                    "model": "m", "api_key": "sk",
                    "available_models": ["ok", ""],
                },
            ],
        })


def test_redact_config_does_not_touch_available_models():
    """available_models is not a secret; redact_config must preserve it."""
    cfg = {
        "enabled": False,
        "providers": [
            {
                "name": "p1", "kind": "openai", "base_url": "https://x",
                "model": "m", "api_key": "sk-real",
                "available_models": ["m1"],
            },
        ],
    }
    out = cfg_mod.redact_config(cfg)
    assert out["providers"][0]["api_key"] == "***"
    assert out["providers"][0]["available_models"] == ["m1"]


# ── tested_models field + record_tested_model (prompts-034) ─────────────────


def _save_provider(name: str = "p1", **extra):
    cfg_mod.save_llm_config({
        "enabled": False,
        "providers": [
            {
                "name": name, "kind": "openai", "base_url": "https://x",
                "model": "m", "api_key": "sk", **extra,
            },
        ],
    })


def test_validate_accepts_optional_tested_models():
    _save_provider(tested_models=["m1", "m2"])
    loaded = cfg_mod.load_llm_config()
    assert loaded["providers"][0]["tested_models"] == ["m1", "m2"]


def test_validate_rejects_non_list_tested_models():
    with pytest.raises(LLMConfigError, match="tested_models"):
        _save_provider(tested_models="not-a-list")


def test_validate_rejects_empty_strings_in_tested_models():
    with pytest.raises(LLMConfigError, match="tested_models"):
        _save_provider(tested_models=["ok", ""])


def test_record_tested_model_appends_and_dedupes():
    _save_provider()
    assert cfg_mod.record_tested_model("p1", "gpt-x") is True
    assert cfg_mod.load_llm_config()["providers"][0]["tested_models"] == ["gpt-x"]
    # Second distinct model appends; order preserved.
    assert cfg_mod.record_tested_model("p1", "gpt-y") is True
    assert cfg_mod.load_llm_config()["providers"][0]["tested_models"] == [
        "gpt-x", "gpt-y",
    ]
    # Duplicate is a no-op.
    assert cfg_mod.record_tested_model("p1", "gpt-x") is False
    assert cfg_mod.load_llm_config()["providers"][0]["tested_models"] == [
        "gpt-x", "gpt-y",
    ]


def test_record_tested_model_noop_for_unknown_provider_or_empty():
    _save_provider()
    assert cfg_mod.record_tested_model("nope", "gpt-x") is False
    assert cfg_mod.record_tested_model("p1", "") is False
    assert cfg_mod.record_tested_model(None, "gpt-x") is False
    assert "tested_models" not in cfg_mod.load_llm_config()["providers"][0]


# ── extra_body field (prompts-035 #2b) ──────────────────────────────────────


def test_validate_accepts_optional_extra_body():
    _save_provider(extra_body={"reasoning_effort": "low"})
    loaded = cfg_mod.load_llm_config()
    assert loaded["providers"][0]["extra_body"] == {"reasoning_effort": "low"}


def test_validate_rejects_non_dict_extra_body():
    with pytest.raises(LLMConfigError, match="extra_body"):
        _save_provider(extra_body=["not", "a", "mapping"])


def test_validate_rejects_extra_body_non_string_keys():
    with pytest.raises(LLMConfigError, match="extra_body"):
        _save_provider(extra_body={"": "blank-key"})

