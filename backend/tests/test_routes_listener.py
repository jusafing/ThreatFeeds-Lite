"""
Tests for the generic push-listener endpoint and its logging (prompts-053).

POST /api/ingest/listener accepts any JSON (object or array), names the feed
after the authenticated sending user (prompts-058) — falling back to
"Received Feed <epoch>" when the request is anonymous — is gated by
listener.enabled, and logs every received payload plus detailed per-entry
errors.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

import backend.api.routes_ingest as ri
import backend.ingestion.push_listener as pl


def _fake_request(user=None):
    """Build a minimal stand-in for a Starlette Request.

    The listener route only reads ``request.state.user`` (set by the auth
    middleware when auth is enabled; absent otherwise). ``user=None`` models the
    anonymous / auth-disabled case.
    """
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.mark.asyncio
async def test_listener_names_feed_after_user(monkeypatch):
    """When a user is authenticated, the feed is named after their username (prompts-058)."""
    captured: list = []

    async def fake_process_push(payload, source_name, job_id=None):
        captured.append((source_name, payload))
        return {"inserted": 2, "skipped": 0, "total_read": 2, "duplicates": 0, "discarded": 0}

    monkeypatch.setattr(ri, "process_push", fake_process_push)
    monkeypatch.setattr(ri, "load_sources", lambda: {"listener": {"enabled": True}})

    payload = [{"indicator": "1.2.3.4"}, {"indicator": "5.6.7.8"}]
    request = _fake_request(user={"username": "sensor-01", "role": "sender"})
    result = await ri.listener_ingest(payload, BackgroundTasks(), request, background=False)

    assert result.inserted == 2
    source_name, sent = captured[0]
    assert source_name == "sensor-01"
    assert sent == payload


@pytest.mark.asyncio
async def test_listener_anonymous_falls_back_to_received_feed(monkeypatch):
    """With no authenticated user the feed falls back to 'Received Feed <epoch>' (prompts-058 option B)."""
    captured: list = []

    async def fake_process_push(payload, source_name, job_id=None):
        captured.append((source_name, payload))
        return {"inserted": 2, "skipped": 0, "total_read": 2, "duplicates": 0, "discarded": 0}

    monkeypatch.setattr(ri, "process_push", fake_process_push)
    monkeypatch.setattr(ri, "load_sources", lambda: {"listener": {"enabled": True}})

    payload = [{"indicator": "1.2.3.4"}, {"indicator": "5.6.7.8"}]
    result = await ri.listener_ingest(payload, BackgroundTasks(), _fake_request(), background=False)

    assert result.inserted == 2
    source_name, sent = captured[0]
    assert source_name.startswith("Received Feed ")
    assert source_name.split("Received Feed ")[1].isdigit()
    assert sent == payload


@pytest.mark.asyncio
async def test_listener_wraps_single_object(monkeypatch):
    """A single JSON object is accepted (process_push wraps it into a one-item list)."""
    captured: list = []

    async def fake_process_push(payload, source_name, job_id=None):
        captured.append(payload)
        return {"inserted": 1, "skipped": 0, "total_read": 1, "duplicates": 0, "discarded": 0}

    monkeypatch.setattr(ri, "process_push", fake_process_push)
    monkeypatch.setattr(ri, "load_sources", lambda: {"listener": {"enabled": True}})

    result = await ri.listener_ingest(
        {"indicator": "9.9.9.9"}, BackgroundTasks(), _fake_request(), background=False
    )
    assert result.inserted == 1
    assert captured[0] == {"indicator": "9.9.9.9"}


@pytest.mark.asyncio
async def test_listener_disabled_returns_503(monkeypatch):
    """When listener.enabled is false the route rejects with 503."""
    monkeypatch.setattr(ri, "load_sources", lambda: {"listener": {"enabled": False}})
    with pytest.raises(HTTPException) as exc:
        await ri.listener_ingest(
            {"indicator": "x"}, BackgroundTasks(), _fake_request(), background=False
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_listener_enabled_defaults_true(monkeypatch):
    """A missing enabled flag defaults to enabled (route does not 503)."""
    async def fake_process_push(payload, source_name, job_id=None):
        return {"inserted": 0, "skipped": 0, "total_read": 0, "duplicates": 0, "discarded": 0}

    monkeypatch.setattr(ri, "process_push", fake_process_push)
    monkeypatch.setattr(ri, "load_sources", lambda: {"listener": {}})
    result = await ri.listener_ingest([], BackgroundTasks(), _fake_request(), background=False)
    assert result.total_read == 0


@pytest.mark.asyncio
async def test_process_push_logs_receipt_and_entry_errors(monkeypatch, caplog):
    """process_push logs a receipt summary and a detailed error for non-dict elements."""
    async def fake_insert(name, entry):
        return "inserted"

    monkeypatch.setattr(pl, "insert_entry", fake_insert)
    monkeypatch.setattr(pl, "normalise", lambda raw, **kw: raw)
    monkeypatch.setattr(pl, "load_sources", lambda: {"listener": {}})

    with caplog.at_level(logging.INFO):
        result = await pl.process_push(
            [{"indicator": "1.1.1.1"}, "not-a-dict"], "Received Feed 123",
        )

    # One valid entry inserted, one bad element discarded and counted as an error.
    assert result["inserted"] == 1
    assert result["discarded"] == 1
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "listener_receive source=Received Feed 123 events=2" in messages
    assert "listener_entry_invalid" in messages
