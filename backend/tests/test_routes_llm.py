"""Tests for backend.api.routes_llm (prompts-021D, refactored in 022 step 4)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.llm import config as cfg_mod
from backend.llm.errors import LLMProviderError
from backend.main import app
from backend.api import routes_llm as routes_llm_mod


@pytest.fixture(autouse=True)
def _isolate_llm_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "_LLM_CONFIG_PATH", tmp_path / "llm-providers.yaml")
    yield


def _preload(cfg):
    cfg_mod.save_llm_config(cfg)


# ── GET /config ─────────────────────────────────────────────────────────────


def test_get_config_redacts_api_key():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })
    client = TestClient(app)
    r = client.get("/api/llm/config")
    assert r.status_code == 200
    data = r.json()
    assert data["providers"][0]["api_key"] == "***"


# ── PUT /config (022: narrowed to {enabled, default_provider}) ──────────────


def test_put_config_updates_enabled_and_default_provider():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })
    client = TestClient(app)
    r = client.put("/api/llm/config", json={"enabled": True, "default_provider": "a"})
    assert r.status_code == 200, r.text
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["enabled"] is True
    assert on_disk["default_provider"] == "a"
    # Provider list preserved verbatim including the real api_key.
    assert on_disk["providers"][0]["api_key"] == "sk-real"


def test_put_config_rejects_providers_key():
    """022 step 4: managing providers moved to dedicated routes."""
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })
    client = TestClient(app)
    r = client.put(
        "/api/llm/config",
        json={"enabled": True, "providers": [{"name": "x", "kind": "openai"}]},
    )
    assert r.status_code == 400
    assert "providers" in r.json()["detail"]


def test_put_config_rejects_unknown_top_level_key():
    client = TestClient(app)
    r = client.put("/api/llm/config", json={"surprise": 1})
    assert r.status_code == 400


# ── GET /providers ──────────────────────────────────────────────────────────


def test_get_providers_excludes_secrets():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })
    client = TestClient(app)
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["name"] == "a"
    assert data[0]["has_api_key"] is True
    assert "api_key" not in data[0]


def test_get_providers_exposes_tested_models():
    """prompts-034: the listing surfaces tested_models (default []) for the
    Smart Mapping model dropdown."""
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x",
             "model": "m", "api_key": "sk-real",
             "tested_models": ["m", "m2"]},
            {"name": "b", "kind": "ollama", "base_url": "https://y", "model": "n"},
        ],
    })
    client = TestClient(app)
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    data = {p["name"]: p for p in r.json()}
    assert data["a"]["tested_models"] == ["m", "m2"]
    assert data["b"]["tested_models"] == []


def test_get_providers_exposes_available_models():
    """prompts-036: the listing surfaces available_models (default []) so the
    Smart Mapping proposal dropdown can be populated from DISCOVERED models
    without a green Test."""
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x",
             "model": "m", "api_key": "sk-real",
             "available_models": ["m", "m2", "m3"]},
            {"name": "b", "kind": "ollama", "base_url": "https://y", "model": "n"},
        ],
    })
    client = TestClient(app)
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    data = {p["name"]: p for p in r.json()}
    assert data["a"]["available_models"] == ["m", "m2", "m3"]
    assert data["b"]["available_models"] == []


# ── POST /providers (022 step 4) ────────────────────────────────────────────


def test_post_providers_adds_new_provider():
    _preload({"enabled": False, "providers": []})
    client = TestClient(app)
    body = {
        "name": "newone",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "sk-real",
    }
    r = client.post("/api/llm/providers", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "newone"
    assert data["has_api_key"] is True
    assert "api_key" not in data
    # Persisted on disk with real key.
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"][0]["api_key"] == "sk-real"


def test_post_providers_rejects_bad_name():
    _preload({"enabled": False, "providers": []})
    client = TestClient(app)
    r = client.post(
        "/api/llm/providers",
        json={"name": "bad name with spaces", "kind": "openai", "base_url": "https://x", "model": "m"},
    )
    assert r.status_code == 400


def test_post_providers_rejects_duplicate_name():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "dup", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"}
        ],
    })
    client = TestClient(app)
    r = client.post(
        "/api/llm/providers",
        json={"name": "dup", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"},
    )
    assert r.status_code == 400


# ── PUT /providers/{name} (022 step 4) ──────────────────────────────────────


def test_put_provider_updates_in_place_and_retains_key_on_stars():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })
    client = TestClient(app)
    r = client.put(
        "/api/llm/providers/a",
        json={"name": "a", "kind": "openai", "base_url": "https://x", "model": "m2", "api_key": "***"},
    )
    assert r.status_code == 200, r.text
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"][0]["api_key"] == "sk-real"
    assert on_disk["providers"][0]["model"] == "m2"


def test_put_provider_replaces_key_when_real_value_sent():
    _preload({
        "enabled": False,
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-old"}
        ],
    })
    client = TestClient(app)
    r = client.put(
        "/api/llm/providers/a",
        json={"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-new"},
    )
    assert r.status_code == 200
    assert cfg_mod.load_llm_config()["providers"][0]["api_key"] == "sk-new"


def test_put_provider_404_for_unknown_name():
    _preload({"enabled": False, "providers": []})
    client = TestClient(app)
    r = client.put(
        "/api/llm/providers/ghost",
        json={"name": "ghost", "kind": "openai", "base_url": "https://x", "model": "m"},
    )
    assert r.status_code == 404


# ── DELETE /providers/{name} (022 step 4) ───────────────────────────────────


def test_delete_provider_removes_and_clears_default():
    _preload({
        "enabled": False,
        "default_provider": "a",
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"}
        ],
    })
    client = TestClient(app)
    r = client.delete("/api/llm/providers/a")
    assert r.status_code == 204
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"] == []
    assert on_disk["default_provider"] is None


def test_delete_provider_keeps_default_when_unrelated():
    _preload({
        "enabled": False,
        "default_provider": "keep",
        "providers": [
            {"name": "keep", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"},
            {"name": "drop", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"},
        ],
    })
    client = TestClient(app)
    r = client.delete("/api/llm/providers/drop")
    assert r.status_code == 204
    on_disk = cfg_mod.load_llm_config()
    assert [p["name"] for p in on_disk["providers"]] == ["keep"]
    assert on_disk["default_provider"] == "keep"


def test_delete_provider_404_for_unknown_name():
    _preload({"enabled": False, "providers": []})
    client = TestClient(app)
    r = client.delete("/api/llm/providers/ghost")
    assert r.status_code == 404


def test_delete_last_provider_while_enabled_auto_disables_llm():
    # prompts-031: deleting the only provider while enabled=true must not be
    # rejected by validate_config; the route auto-disables LLM in the same
    # write so the delete succeeds and the config stays valid.
    _preload({
        "enabled": True,
        "default_provider": "only",
        "providers": [
            {"name": "only", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"}
        ],
    })
    client = TestClient(app)
    r = client.delete("/api/llm/providers/only")
    assert r.status_code == 204, r.text
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"] == []
    assert on_disk["enabled"] is False
    assert on_disk["default_provider"] is None


def test_delete_non_last_provider_while_enabled_keeps_llm_enabled():
    # Deleting a non-last provider while enabled=true must leave enabled
    # untouched (a valid provider with a key remains).
    _preload({
        "enabled": True,
        "default_provider": "keep",
        "providers": [
            {"name": "keep", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"},
            {"name": "drop", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk"},
        ],
    })
    client = TestClient(app)
    r = client.delete("/api/llm/providers/drop")
    assert r.status_code == 204, r.text
    on_disk = cfg_mod.load_llm_config()
    assert [p["name"] for p in on_disk["providers"]] == ["keep"]
    assert on_disk["enabled"] is True


# ── POST /providers/{name}/test (022 step 4 — new shape) ────────────────────


def test_provider_test_returns_run_provider_test_shape():
    _preload({
        "enabled": True,
        "default_provider": "a",
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })

    class _Stub:
        name = "a"
        model = "m"  # prompts-024: runner skips complete when model is empty
        _tap = None

        def list_models(self):
            return ["m", "m2"]

        def complete(self, *a, **kw):
            return "pong"

    client = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = client.post("/api/llm/providers/a/test")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert data["models"] == ["m", "m2"]
    assert data["sample"].startswith("pong")
    assert isinstance(data["details"], list)


def test_persisted_test_records_tested_model_on_green(monkeypatch):
    """prompts-034: a green persisted Test appends the model to tested_models."""
    _preload({
        "enabled": True,
        "default_provider": "a",
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x",
             "model": "m", "api_key": "sk-real"}
        ],
    })

    class _Stub:
        name = "a"
        model = "m"
        _tap = None

        def list_models(self):
            return ["m"]

        def complete(self, *a, **kw):
            return "pong"

    client = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = client.post("/api/llm/providers/a/test")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"][0]["tested_models"] == ["m"]


def test_persisted_test_does_not_record_on_error():
    """A failing Test must NOT append to tested_models."""
    _preload({
        "enabled": True,
        "default_provider": "a",
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x",
             "model": "m", "api_key": "sk-real"}
        ],
    })

    class _Stub:
        name = "a"
        model = "m"
        _tap = None

        def list_models(self):
            raise LLMProviderError("upstream 401", status=401, body="nope")

        def complete(self, *a, **kw):
            raise LLMProviderError("upstream 401", status=401, body="nope")

    client = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = client.post("/api/llm/providers/a/test")
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    on_disk = cfg_mod.load_llm_config()
    assert "tested_models" not in on_disk["providers"][0]


def test_provider_test_captures_provider_error_into_transcript():
    """022 step 4: provider errors no longer surface as HTTP 502; they
    are captured into the transcript with aggregate status='error'."""
    _preload({
        "enabled": True,
        "default_provider": "a",
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x", "model": "m", "api_key": "sk-real"}
        ],
    })

    class _Stub:
        name = "a"
        model = "m"  # prompts-024: runner skips complete when model is empty
        _tap = None

        def list_models(self):
            raise LLMProviderError("upstream 401", status=401, body="unauthorized")

        def complete(self, *a, **kw):
            raise LLMProviderError("upstream 401", status=401, body="unauthorized")

    client = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = client.post("/api/llm/providers/a/test")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "error"


# ── POST /providers/test (ephemeral) (022 step 4) ───────────────────────────


def test_ephemeral_provider_test_constructs_client_without_persisting():
    """The ephemeral test must NOT touch llm-providers.yaml."""
    _preload({"enabled": False, "providers": []})

    captured: dict = {}

    def _fake_run(client):
        captured["name"] = client.name
        captured["base_url"] = client.base_url
        return {"status": "ok", "details": [], "models": ["m"], "sample": "pong"}

    client = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = client.post(
            "/api/llm/providers/test",
            json={
                "name": "draft",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": "sk-real",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert captured["name"] == "draft"
    assert captured["base_url"] == "https://api.openai.com/v1"
    # YAML still empty — ephemeral test must not persist.
    assert cfg_mod.load_llm_config()["providers"] == []


def test_ephemeral_provider_test_rejects_unknown_kind():
    client = TestClient(app)
    r = client.post(
        "/api/llm/providers/test",
        json={"name": "draft", "kind": "magic", "base_url": "https://x", "model": "m"},
    )
    assert r.status_code == 400


# ── POST /providers/test merge-stored-key (prompts-027) ─────────────────────


def test_test_route_merges_stored_key_when_redacted_for_persisted_name():
    """The persisted ProviderCard sends api_key='***' + the operator-chosen
    model; the route must merge the stored real key from disk so the
    request reaches the upstream with a valid credential."""
    _preload({
        "enabled": False,
        "providers": [
            {
                "name": "kept",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "old-model",
                "api_key": "sk-stored",
            },
        ],
    })

    captured: dict = {}

    def _fake_run(client):
        captured["api_key"] = client.api_key
        captured["model"] = client.model
        return {"status": "ok", "details": [], "models": ["m"], "sample": "pong"}

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = tc.post(
            "/api/llm/providers/test",
            json={
                "name": "kept",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "new-model",
                "api_key": "***",
            },
        )
    assert r.status_code == 200, r.text
    assert captured["api_key"] == "sk-stored"  # merged from disk
    assert captured["model"] == "new-model"   # operator override honoured


def test_draft_test_records_selected_model_into_persisted_provider(monkeypatch):
    """prompts-035 (#1): the frontend 'Test Model' probe hits the DRAFT
    /providers/test with the operator-SELECTED model and api_key='***'. On a
    green probe the route must append THAT selected model (not the provider's
    persisted default) to the persisted provider's tested_models, so the Smart
    Mapping dropdown can offer it. This is the path runProbe actually uses."""
    _preload({
        "enabled": True,
        "default_provider": "cdtnew",
        "providers": [
            {
                "name": "cdtnew",
                "kind": "openai_compatible",
                "base_url": "https://gw.example/api",
                "model": "default-model",      # persisted default
                "api_key": "sk-stored",
            },
        ],
    })

    def _fake_run(client):
        # Echo the constructed model so we prove the SELECTED one is probed.
        return {"status": "ok", "details": [], "models": ["gpt-oss:120b"],
                "sample": "pong", "model": client.model}

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = tc.post(
            "/api/llm/providers/test",
            json={
                "name": "cdtnew",
                "kind": "openai_compatible",
                "base_url": "https://gw.example/api",
                "model": "gpt-oss:120b",       # operator-SELECTED model
                "api_key": "***",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    on_disk = cfg_mod.load_llm_config()["providers"][0]
    # The SELECTED model is recorded — NOT the persisted default.
    assert on_disk["tested_models"] == ["gpt-oss:120b"]
    assert "default-model" not in on_disk["tested_models"]


def test_draft_test_does_not_record_on_error():
    """prompts-035 (#1): a RED draft probe must not append to tested_models —
    this is what (correctly) keeps an empty-content reasoning probe out of the
    dropdown until #2 makes it green."""
    _preload({
        "enabled": True,
        "default_provider": "cdtnew",
        "providers": [
            {
                "name": "cdtnew",
                "kind": "openai_compatible",
                "base_url": "https://gw.example/api",
                "model": "default-model",
                "api_key": "sk-stored",
            },
        ],
    })

    def _fake_run(client):
        return {"status": "error", "details": [], "models": [], "sample": None}

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = tc.post(
            "/api/llm/providers/test",
            json={
                "name": "cdtnew",
                "kind": "openai_compatible",
                "base_url": "https://gw.example/api",
                "model": "gpt-oss:120b",
                "api_key": "***",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"
    assert "tested_models" not in cfg_mod.load_llm_config()["providers"][0]


def test_test_route_does_not_merge_when_name_is_anonymous():
    """Anonymous drafts (wizard pre-save) must use the literal ``***`` (which
    will fail the build for kinds that require a key)."""
    _preload({"enabled": False, "providers": []})

    captured: dict = {}

    def _fake_run(client):
        captured["api_key"] = client.api_key
        return {"status": "ok", "details": [], "models": [], "sample": None}

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = tc.post(
            "/api/llm/providers/test",
            json={
                "name": "unknown",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "***",
            },
        )
    # No matching record on disk → no merge → literal "***" reaches the
    # client construction. The test still succeeds at the route layer
    # because the build succeeded (api_key is just a string here).
    assert r.status_code == 200, r.text
    assert captured["api_key"] == "***"


def test_test_route_does_not_merge_when_real_key_provided_for_persisted_name():
    """If the operator types a fresh key the route must use it verbatim."""
    _preload({
        "enabled": False,
        "providers": [
            {
                "name": "kept",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk-stored",
            },
        ],
    })

    captured: dict = {}

    def _fake_run(client):
        captured["api_key"] = client.api_key
        return {"status": "ok", "details": [], "models": [], "sample": None}

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_provider_test", side_effect=_fake_run):
        r = tc.post(
            "/api/llm/providers/test",
            json={
                "name": "kept",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk-new",
            },
        )
    assert r.status_code == 200, r.text
    assert captured["api_key"] == "sk-new"


# ── POST /providers/discover (draft + persisted) (prompts-027) ──────────────


def test_discover_draft_returns_canonical_subset():
    _preload({"enabled": False, "providers": []})

    def _fake_discover(client):
        return {
            "status": "ok",
            "details": [{"step": "list_models", "error": None}],
            "models": ["a", "b"],
        }

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "run_discover_only", side_effect=_fake_discover):
        r = tc.post(
            "/api/llm/providers/discover",
            json={
                "name": "draft",
                "kind": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "",
                "api_key": "sk-real",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "status": "ok",
        "details": [{"step": "list_models", "error": None}],
        "models": ["a", "b"],
    }
    # sample key intentionally absent — discover does NOT probe.
    assert "sample" not in body


def test_discover_draft_rejects_unknown_kind():
    tc = TestClient(app)
    r = tc.post(
        "/api/llm/providers/discover",
        json={"name": "draft", "kind": "magic", "base_url": "https://x"},
    )
    assert r.status_code == 400


def test_discover_persisted_uses_get_client():
    _preload({
        "enabled": False,
        "providers": [
            {
                "name": "p1",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk-real",
            },
        ],
    })

    seen: dict = {}

    class _Stub:
        name = "p1"
        model = "m"
        _tap = None

        def list_models(self):
            seen["called"] = True
            return ["m1", "m2"]

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = tc.post("/api/llm/providers/p1/discover")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["models"] == ["m1", "m2"]
    assert seen.get("called") is True


def test_discover_persisted_empty_list_is_023_failure():
    """list_models returns [] → discover reports status='error' with the
    023 explanatory verdict (server reachable, no models published)."""
    _preload({
        "enabled": False,
        "providers": [
            {
                "name": "p1",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk-real",
            },
        ],
    })

    class _Stub:
        name = "p1"
        model = "m"
        _tap = None

        def list_models(self):
            return []

    tc = TestClient(app)
    with patch.object(routes_llm_mod, "get_client", return_value=_Stub()):
        r = tc.post("/api/llm/providers/p1/discover")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "error"
    # The 023 verdict text appears in some detail entry.
    assert any(
        d.get("error") and "0 models" in d["error"]
        for d in body["details"]
    )


# ── available_models persistence (prompts-027) ──────────────────────────────


def test_provider_round_trips_available_models_field():
    """PUT a provider with available_models, GET back, see it preserved."""
    _preload({
        "enabled": False,
        "providers": [
            {
                "name": "p1",
                "kind": "openai",
                "base_url": "https://x",
                "model": "m",
                "api_key": "sk-real",
            },
        ],
    })
    tc = TestClient(app)
    r = tc.put(
        "/api/llm/providers/p1",
        json={
            "kind": "openai",
            "base_url": "https://x",
            "model": "m",
            "api_key": "***",  # keep stored
            "available_models": ["m1", "m2", "m3"],
        },
    )
    assert r.status_code == 200, r.text

    on_disk = cfg_mod.load_llm_config()
    assert on_disk["providers"][0]["available_models"] == ["m1", "m2", "m3"]
    # api_key still preserved (write-only semantics).
    assert on_disk["providers"][0]["api_key"] == "sk-real"

