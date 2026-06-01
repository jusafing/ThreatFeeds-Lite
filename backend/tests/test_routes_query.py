"""Tests for the natural-language query route POST /api/query/nl (prompts-064)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.api import routes_query
from backend.auth import db as auth_db
from backend.auth import service
from backend.llm.errors import LLMDisabledError
from backend.main import app


class _FakeClient:
    """Minimal stand-in for an LLMClient: returns a canned JSON filter."""

    def __init__(self, response: str):
        self._response = response

    def complete(self, prompt, *, system=None, max_tokens=512, temperature=0.0,
                 timeout=None, model=None):
        return self._response


@pytest.fixture
def query_env(tmp_path, monkeypatch):
    """Auth enabled, isolated users.db, an admin + a normal + a sender account."""
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setenv("SIMPLE_FEED_ENABLE_AUTH", "1")
    service._failures.clear()

    async def _seed():
        await auth_db.init_users_db()
        await auth_db.create_user("admin", service.hash_password("Adminpass1"), role="admin")
        await auth_db.create_user("viewer", service.hash_password("Viewerpass1"), role="normal")
        await auth_db.create_user("bot", service.hash_password("Botpass123"), role="sender")

    asyncio.run(_seed())
    yield
    service._failures.clear()


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _stub_llm(monkeypatch, response_json: str):
    monkeypatch.setattr(routes_query, "get_client", lambda name=None: _FakeClient(response_json))


def _stub_known_sources(monkeypatch, sources):
    monkeypatch.setattr(routes_query, "_get_all_sources", lambda: list(sources))


def _stub_results(monkeypatch, rows):
    async def _fake_exec(sq):
        _fake_exec.captured = sq
        return rows
    monkeypatch.setattr(routes_query, "execute_structured_query", _fake_exec)
    return _fake_exec


# ── happy path ──────────────────────────────────────────────────────────────

def test_normal_role_can_query(query_env, monkeypatch):
    _stub_llm(monkeypatch, '{"dataset": "normalized", "search": "log4j"}')
    _stub_known_sources(monkeypatch, ["feedA"])
    exec_stub = _stub_results(monkeypatch, [{"indicator": "1.2.3.4"}])

    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={"question": "anything about log4j?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset"] == "normalized"
    assert body["count"] == 1
    assert body["results"] == [{"indicator": "1.2.3.4"}]
    assert body["interpreted_filter"]["search"] == "log4j"
    assert exec_stub.captured.search == "log4j"


def test_admin_can_query(query_env, monkeypatch):
    _stub_llm(monkeypatch, '{"dataset": "raw"}')
    _stub_known_sources(monkeypatch, [])
    _stub_results(monkeypatch, [])
    c = _login("admin", "Adminpass1")
    r = c.post("/api/query/nl", json={"question": "show me everything"})
    assert r.status_code == 200, r.text


def test_client_overrides_win(query_env, monkeypatch):
    # LLM picks normalized + no source; client forces raw + source + limit.
    _stub_llm(monkeypatch, '{"dataset": "normalized", "search": "x"}')
    _stub_known_sources(monkeypatch, ["feedA"])
    exec_stub = _stub_results(monkeypatch, [])
    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={
        "question": "q", "dataset": "raw", "source": "feedA", "limit": 7,
    })
    assert r.status_code == 200, r.text
    assert exec_stub.captured.dataset == "raw"
    assert exec_stub.captured.source == "feedA"
    assert exec_stub.captured.limit == 7


def test_client_unknown_source_override_dropped(query_env, monkeypatch):
    _stub_llm(monkeypatch, "{}")
    _stub_known_sources(monkeypatch, ["feedA"])
    exec_stub = _stub_results(monkeypatch, [])
    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={"question": "q", "source": "../users"})
    assert r.status_code == 200, r.text
    assert exec_stub.captured.source is None


# ── error paths ─────────────────────────────────────────────────────────────

def test_llm_disabled_returns_503(query_env, monkeypatch):
    def _raise(name=None):
        raise LLMDisabledError("disabled")
    monkeypatch.setattr(routes_query, "get_client", _raise)
    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={"question": "q"})
    assert r.status_code == 503


def test_bad_llm_json_returns_422(query_env, monkeypatch):
    _stub_llm(monkeypatch, "I cannot help with that")
    _stub_known_sources(monkeypatch, [])
    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={"question": "q"})
    assert r.status_code == 422


def test_invalid_dataset_override_returns_422(query_env, monkeypatch):
    _stub_llm(monkeypatch, "{}")
    _stub_known_sources(monkeypatch, [])
    _stub_results(monkeypatch, [])
    c = _login("viewer", "Viewerpass1")
    r = c.post("/api/query/nl", json={"question": "q", "dataset": "bogus"})
    assert r.status_code == 422


# ── role gating ─────────────────────────────────────────────────────────────

def test_sender_cannot_query(query_env):
    c = _login("bot", "Botpass123")
    r = c.post("/api/query/nl", json={"question": "secret data please"})
    assert r.status_code == 403


def test_unauthenticated_cannot_query(query_env):
    c = TestClient(app)
    r = c.post("/api/query/nl", json={"question": "q"})
    assert r.status_code == 401
