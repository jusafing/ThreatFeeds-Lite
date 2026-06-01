"""Tests for the per-section 'refresh all' batch endpoints (prompts-056).

Each batch endpoint iterates every configured source of one kind, refreshes
each independently, and returns a per-source report. A failure on one source
must be captured in that source's entry without aborting the whole batch.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_refresh_all_api_pull_reports_every_source(monkeypatch):
    import backend.api.routes_control as rc

    monkeypatch.setattr(
        rc, "load_sources",
        lambda: {"api_pull": [{"name": "a"}, {"name": "b"}]},
    )

    seen: list[str] = []

    async def fake_pull(src):
        seen.append(src["name"])
        return {"inserted": 3, "skipped": 1, "duplicates": 0, "errors": []}

    monkeypatch.setattr(rc, "pull_api_source", fake_pull)

    res = await rc.refresh_all_api_pull()

    assert seen == ["a", "b"]
    assert res["kind"] == "api_pull"
    assert res["total"] == 2
    assert res["succeeded"] == 2
    assert res["failed"] == 0
    assert [r["name"] for r in res["results"]] == ["a", "b"]
    assert all(r["ok"] for r in res["results"])
    assert res["results"][0]["inserted"] == 3


@pytest.mark.asyncio
async def test_refresh_all_continues_past_a_failing_source(monkeypatch):
    import backend.api.routes_control as rc

    monkeypatch.setattr(
        rc, "load_sources",
        lambda: {"rss_pull": [{"name": "ok1"}, {"name": "boom"}, {"name": "ok2"}]},
    )

    async def fake_pull(src):
        if src["name"] == "boom":
            raise RuntimeError("fetch failed: 500")
        return {"inserted": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(rc, "pull_rss_source", fake_pull)

    res = await rc.refresh_all_rss_pull()

    assert res["total"] == 3
    assert res["succeeded"] == 2
    assert res["failed"] == 1
    by_name = {r["name"]: r for r in res["results"]}
    assert by_name["ok1"]["ok"] is True
    assert by_name["ok2"]["ok"] is True
    assert by_name["boom"]["ok"] is False
    assert "fetch failed: 500" in by_name["boom"]["error"]


@pytest.mark.asyncio
async def test_refresh_all_remote_json_pull_passes_url_and_fields(monkeypatch):
    import backend.api.routes_control as rc

    monkeypatch.setattr(
        rc, "load_sources",
        lambda: {"remote_json_pull": [
            {"name": "feed1", "url": "http://x/1", "fields": ["a", "b"]},
        ]},
    )

    captured: dict = {}

    async def fake_ingest(url, name, source_fields=None):
        captured["url"] = url
        captured["name"] = name
        captured["fields"] = source_fields
        return {"inserted": 5, "skipped": 0, "errors": []}

    monkeypatch.setattr(rc, "ingest_remote_feed", fake_ingest)

    res = await rc.refresh_all_remote_json_pull()

    assert captured == {"url": "http://x/1", "name": "feed1", "fields": ["a", "b"]}
    assert res["succeeded"] == 1
    assert res["results"][0]["inserted"] == 5


@pytest.mark.asyncio
async def test_refresh_all_empty_section_is_noop(monkeypatch):
    import backend.api.routes_control as rc

    monkeypatch.setattr(rc, "load_sources", lambda: {})

    res = await rc.refresh_all_api_pull()

    assert res["total"] == 0
    assert res["succeeded"] == 0
    assert res["failed"] == 0
    assert res["results"] == []
