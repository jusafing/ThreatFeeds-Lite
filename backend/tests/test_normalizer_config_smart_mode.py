"""Tests for the smart-mode block in normalizer-config (021E-3)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.normalizer import config as cfg_mod


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch):
    fake = tmp_path / "normalizer-config.yaml"
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", fake)
    yield fake


def test_defaults_include_full_smart_mode_block(_isolate_config: Path):
    """When config file is missing, defaults must include the full smart_mode tree."""
    cfg = cfg_mod.load_normalizer_config()
    sm = cfg["smart_mode"]
    assert sm["enabled"] is False
    assert sm["provider"] is None
    assert sm["sample_size"] == 10
    assert sm["schedule"]["enabled"] is False
    assert sm["schedule"]["interval_minutes"] == 1440
    assert sm["on_new_feed"]["enabled"] is True
    assert sm["on_new_feed"]["first_ingest_only"] is True
    assert sm["auto_apply"]["enabled"] is False
    assert sm["concurrency"]["max_concurrent"] == 2
    assert sm["sources"] == []


def test_partial_smart_mode_yaml_deep_merges_defaults(_isolate_config: Path):
    """Operator-supplied partial smart_mode block must not strip nested defaults."""
    _isolate_config.write_text(
        yaml.dump({"smart_mode": {"enabled": True}}),
        encoding="utf-8",
    )
    cfg = cfg_mod.load_normalizer_config()
    sm = cfg["smart_mode"]
    # Operator override preserved
    assert sm["enabled"] is True
    # Nested defaults preserved (the bug we're guarding against)
    assert sm["schedule"]["interval_minutes"] == 1440
    assert sm["on_new_feed"]["enabled"] is True
    assert sm["concurrency"]["max_concurrent"] == 2


def test_partial_nested_override_deep_merges(_isolate_config: Path):
    """Overriding only smart_mode.schedule.enabled must keep interval_minutes."""
    _isolate_config.write_text(
        yaml.dump({"smart_mode": {"schedule": {"enabled": True}}}),
        encoding="utf-8",
    )
    cfg = cfg_mod.load_normalizer_config()
    assert cfg["smart_mode"]["schedule"]["enabled"] is True
    assert cfg["smart_mode"]["schedule"]["interval_minutes"] == 1440


def test_operator_sources_list_is_authoritative(_isolate_config: Path):
    """Lists are replaced wholesale; defaults' empty list does not append."""
    _isolate_config.write_text(
        yaml.dump({
            "smart_mode": {
                "sources": [{"name": "feed-a", "enabled": True}],
            },
        }),
        encoding="utf-8",
    )
    cfg = cfg_mod.load_normalizer_config()
    assert cfg["smart_mode"]["sources"] == [{"name": "feed-a", "enabled": True}]


def test_legacy_manual_mappings_top_level_unaffected(_isolate_config: Path):
    """The pre-existing top-level keys still work alongside smart_mode."""
    _isolate_config.write_text(
        yaml.dump({"mode": "manual", "smart_mode": {"enabled": True}}),
        encoding="utf-8",
    )
    cfg = cfg_mod.load_normalizer_config()
    assert cfg["mode"] == "manual"
    assert cfg["smart_mode"]["enabled"] is True
