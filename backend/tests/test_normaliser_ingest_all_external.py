"""Verify ingest_all_fields applies to EXTERNAL feeds too (prompts-056).

The 'Ingest all fields' toggle is a single global bypass in
backend.ingestion.normaliser.normalise(), which every ingestion path funnels
through (api_pull, rss_pull, remote_feed, local, listener). These tests pin
that the bypass keeps *all* raw fields for external-feed ingest modes, so the
toggle is not silently limited to local feeds.
"""
import pytest


@pytest.mark.parametrize(
    "ingest_mode,source_name",
    [
        ("api_pull", "external-api-feed"),
        ("rss_pull", "external-rss-feed"),
        ("remote_json_pull", "external-threat-feed"),
    ],
)
def test_ingest_all_fields_keeps_all_fields_for_external_feeds(
    monkeypatch, ingest_mode, source_name
):
    monkeypatch.setattr(
        "backend.ingestion.normaliser.load_ingest_all_fields", lambda: True
    )
    # A minimal/strict configured field set — proving the bypass ignores it.
    monkeypatch.setattr(
        "backend.ingestion.normaliser.load_fields",
        lambda: {"core_fields": [{"name": "indicator", "enabled": True}], "custom_fields": []},
    )

    from backend.ingestion.normaliser import normalise

    raw = {
        "indicator": "1.2.3.4",
        "cve": "CVE-2024-9999",
        "vendor_specific_field": "keep-me",
        "nested": {"a": 1},
        "empty": "",
    }
    result = normalise(raw, ingest_mode=ingest_mode, source_name=source_name)

    # Every raw field survives despite the strict configured set.
    for key, value in raw.items():
        assert result[key] == value
    assert result["ingest_mode"] == ingest_mode
    assert result["source"] == source_name


def test_ingest_all_disabled_still_filters_external_feed(monkeypatch):
    """Sanity counterpart: with the flag OFF, an external feed is filtered."""
    monkeypatch.setattr(
        "backend.ingestion.normaliser.load_ingest_all_fields", lambda: False
    )
    monkeypatch.setattr(
        "backend.ingestion.normaliser.load_fields",
        lambda: {"core_fields": [{"name": "indicator", "enabled": True}], "custom_fields": []},
    )

    from backend.ingestion.normaliser import normalise

    raw = {"indicator": "1.2.3.4", "vendor_specific_field": "drop-me"}
    result = normalise(raw, ingest_mode="api_pull", source_name="external-api-feed")

    assert result["indicator"] == "1.2.3.4"
    assert "vendor_specific_field" not in result
