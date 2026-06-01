"""Tests for backend.normalizer.proposals (021E-1)."""
from __future__ import annotations

import pytest

from backend.normalizer import proposals as proposals_mod
from backend.normalizer.proposals import (
    get_proposal,
    init_proposals_db,
    insert_proposal,
    list_proposals,
    update_proposal_status,
)


@pytest.fixture(autouse=True)
def _isolate_proposals_db(tmp_path, monkeypatch):
    fake = tmp_path / "proposals.db"
    monkeypatch.setattr(proposals_mod, "_PROPOSALS_DB_PATH", fake)
    yield


@pytest.mark.asyncio
async def test_init_creates_db_and_schema_version():
    await init_proposals_db()
    # Insert succeeds → schema is present.
    new_id = await insert_proposal(
        source_name="s",
        provider_name="openai",
        model="m",
        sample_size=10,
        raw_fields=["a"],
        mapping={"a": "title"},
        prompt_system="sys",
        prompt_user="usr",
        llm_response_raw='{"a":"title"}',
    )
    assert new_id > 0


@pytest.mark.asyncio
async def test_insert_and_get_roundtrip():
    pid = await insert_proposal(
        source_name="src",
        provider_name="p",
        model="m",
        sample_size=5,
        raw_fields=["x", "y"],
        mapping={"x": "title"},
        prompt_system="sys",
        prompt_user="usr",
        llm_response_raw="raw",
    )
    fetched = await get_proposal(pid)
    assert fetched is not None
    assert fetched["source_name"] == "src"
    assert fetched["raw_fields"] == ["x", "y"]
    assert fetched["mapping"] == {"x": "title"}
    assert fetched["status"] == "pending"


@pytest.mark.asyncio
async def test_get_proposal_missing_returns_none():
    await init_proposals_db()
    assert await get_proposal(99999) is None


@pytest.mark.asyncio
async def test_raw_exchange_fields_roundtrip():
    """prompts-037: llm_request_raw / llm_response_json persist and return."""
    pid = await insert_proposal(
        source_name="src", provider_name="p", model="m", sample_size=1,
        raw_fields=["x"], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="content",
        llm_request_raw="POST https://h/v1/chat\n\n{\"model\":\"m\"}",
        llm_response_json='{"choices":[{"message":{"content":"content"}}]}',
    )
    fetched = await get_proposal(pid)
    assert fetched is not None
    assert fetched["llm_request_raw"].startswith("POST https://h/v1/chat")
    assert fetched["llm_response_json"] == (
        '{"choices":[{"message":{"content":"content"}}]}'
    )


@pytest.mark.asyncio
async def test_raw_exchange_fields_default_empty():
    """Rows inserted without the new fields default to empty strings."""
    pid = await insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    fetched = await get_proposal(pid)
    assert fetched is not None
    assert fetched["llm_request_raw"] == ""
    assert fetched["llm_response_json"] == ""


@pytest.mark.asyncio
async def test_list_filters_by_source_and_status():
    await insert_proposal(
        source_name="s1", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    await insert_proposal(
        source_name="s2", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    s2_id = await insert_proposal(
        source_name="s2", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="", status="error",
    )
    s2_rows = await list_proposals(source="s2")
    assert len(s2_rows) == 2
    err_rows = await list_proposals(status="error")
    assert len(err_rows) == 1
    assert err_rows[0]["id"] == s2_id


@pytest.mark.asyncio
async def test_update_status_persists_decision_note():
    pid = await insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="",
    )
    changed = await update_proposal_status(pid, "approved", note="lgtm")
    assert changed
    fetched = await get_proposal(pid)
    assert fetched is not None
    assert fetched["status"] == "approved"
    assert fetched["decided_by_note"] == "lgtm"
    assert fetched["decided_at"] is not None


@pytest.mark.asyncio
async def test_invalid_status_raises():
    with pytest.raises(ValueError):
        await insert_proposal(
            source_name="s", provider_name=None, model=None, sample_size=1,
            raw_fields=[], mapping={}, prompt_system="", prompt_user="",
            llm_response_raw="", status="bogus",
        )


@pytest.mark.asyncio
async def test_list_invalid_status_filter_raises():
    await init_proposals_db()
    with pytest.raises(ValueError):
        await list_proposals(status="bogus")
