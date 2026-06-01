"""Tests for the normaliser module."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _disable_ingest_all_fields(monkeypatch):
    """These tests assume strict-mode normalisation; force ingest_all_fields=False."""
    monkeypatch.setattr(
        "backend.ingestion.normaliser.load_ingest_all_fields",
        lambda: False,
    )

MOCK_FIELDS = {
    "ingest_all_fields": False,
    "core_fields": [
        {"name": "indicator", "enabled": True},
        {"name": "severity",  "enabled": True},
        {"name": "source",    "enabled": True},
        {"name": "ingest_mode", "enabled": True},
        {"name": "raw",       "enabled": False},  # disabled — should be excluded
    ],
    "custom_fields": [
        {"name": "my_custom"},
    ],
}


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_keeps_enabled_core_fields(mock_fields):
    from backend.ingestion.normaliser import normalise
    raw = {"indicator": "1.2.3.4", "severity": "high", "unknown_field": "drop_me"}
    result = normalise(raw, ingest_mode="push", source_name="test_src")

    assert result["indicator"] == "1.2.3.4"
    assert result["severity"] == "high"
    assert "unknown_field" not in result


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_discards_disabled_core_fields(mock_fields):
    from backend.ingestion.normaliser import normalise
    raw = {"indicator": "evil.com", "raw": "should_be_discarded"}
    result = normalise(raw, ingest_mode="push", source_name="test_src")

    # raw is disabled in mock fields — should not appear
    assert "raw" not in result or result.get("raw") is None


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_keeps_custom_fields(mock_fields):
    from backend.ingestion.normaliser import normalise
    raw = {"indicator": "1.2.3.4", "my_custom": "custom_value"}
    result = normalise(raw, ingest_mode="push", source_name="test_src")

    assert result["my_custom"] == "custom_value"


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_always_sets_ingest_mode_and_source(mock_fields):
    from backend.ingestion.normaliser import normalise
    result = normalise({}, ingest_mode="rss_pull", source_name="my_feed")

    assert result["ingest_mode"] == "rss_pull"
    assert result["source"] == "my_feed"


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_empty_input(mock_fields):
    from backend.ingestion.normaliser import normalise
    result = normalise({}, ingest_mode="push", source_name="src")
    assert result["ingest_mode"] == "push"
    assert result["source"] == "src"


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_source_fields_override_disables_core_field(mock_fields):
    """Per-source field config can disable a globally-enabled core field."""
    from backend.ingestion.normaliser import normalise
    source_fields = {
        "core_fields": [{"name": "severity", "enabled": False}],
        "custom_fields": [],
    }
    raw = {"indicator": "1.2.3.4", "severity": "high"}
    result = normalise(raw, ingest_mode="api_pull", source_name="src", source_fields=source_fields)
    assert result["indicator"] == "1.2.3.4"
    assert "severity" not in result


@patch("backend.ingestion.normaliser.load_fields", return_value=MOCK_FIELDS)
def test_source_fields_adds_custom_field(mock_fields):
    """Per-source custom fields are added on top of global custom fields."""
    from backend.ingestion.normaliser import normalise
    source_fields = {
        "core_fields": [],
        "custom_fields": [{"name": "source_specific_field"}],
    }
    raw = {"indicator": "1.2.3.4", "my_custom": "val1", "source_specific_field": "val2"}
    result = normalise(raw, ingest_mode="remote_json", source_name="src", source_fields=source_fields)
    assert result["my_custom"] == "val1"
    assert result["source_specific_field"] == "val2"
