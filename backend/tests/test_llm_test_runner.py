"""Tests for backend.llm.test_runner (prompts-022 step 3)."""
from __future__ import annotations

import json

import pytest

from backend.llm.client import (
    AnthropicClient,
    OllamaClient,
    OpenAIClient,
    OpenAICompatibleClient,
)
from backend.llm.errors import LLMProviderError, LLMTransportError
from backend.llm.test_runner import run_discover_only, run_provider_test


class _Tx:
    """Scripted transport: each response is either a (status, headers, body)
    tuple OR an Exception instance to raise. Calls are recorded."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers, body, timeout, skip_tls_verify, max_retries, provider_name):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _models_body(*names: str) -> bytes:
    return json.dumps({"data": [{"id": n} for n in names]}).encode()


def _ollama_models_body(*names: str) -> bytes:
    return json.dumps({"models": [{"name": n} for n in names]}).encode()


def _chat_body(text: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode()


def _anthropic_body(text: str) -> bytes:
    return json.dumps({"content": [{"type": "text", "text": text}]}).encode()


def _ollama_chat_body(text: str) -> bytes:
    return json.dumps({"message": {"content": text}}).encode()


# ── happy paths ─────────────────────────────────────────────────────────────


def test_openai_happy_path_returns_models_and_sample_and_ok_status():
    tx = _Tx([
        (200, {}, _models_body("gpt-4o-mini", "gpt-4o")),  # list_models
        (200, {}, _chat_body("pong")),                      # complete
    ])
    c = OpenAIClient(
        name="prod", base_url="https://api.openai.com/v1",
        api_key="sk-xxx", model="gpt-4o-mini", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] == ["gpt-4o-mini", "gpt-4o"]
    assert result["sample"] == "pong"
    # Transcript carries both steps in order, with secrets redacted.
    assert [d["step"] for d in result["details"]] == ["list_models", "complete"]
    auth_value = result["details"][0]["headers_redacted"].get("Authorization")
    assert auth_value == "***"
    # No literal api_key anywhere in the serialised transcript.
    blob = json.dumps(result["details"])
    assert "sk-xxx" not in blob


def test_ollama_happy_path_with_no_auth_header():
    tx = _Tx([
        (200, {}, _ollama_models_body("llama3.1", "mistral")),
        (200, {}, _ollama_chat_body("pong")),
    ])
    c = OllamaClient(
        name="local", base_url="http://localhost:11434",
        api_key="", model="llama3.1", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] == ["llama3.1", "mistral"]
    assert "pong" in result["sample"]
    # Ollama makes no auth header, so the redacted dict has none either.
    for d in result["details"]:
        assert "Authorization" not in d["headers_redacted"]
        assert "x-api-key" not in d["headers_redacted"]


def test_openai_compatible_happy_path():
    tx = _Tx([
        (200, {}, _models_body("mistral-7b")),
        (200, {}, _chat_body("pong")),
    ])
    c = OpenAICompatibleClient(
        name="lab", base_url="http://10.0.0.5:8000/v1",
        api_key="", model="mistral-7b", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] == ["mistral-7b"]


# ── anthropic special case ──────────────────────────────────────────────────


def test_anthropic_skips_list_models_step_but_is_still_ok_when_complete_works():
    """Anthropic has no /models endpoint; list_models() returns None.
    The runner must record a synthetic skipped step and NOT downgrade
    the aggregate status if the complete step succeeds."""
    tx = _Tx([(200, {}, _anthropic_body("pong"))])
    c = AnthropicClient(
        name="claude", base_url="https://api.anthropic.com",
        api_key="sk-ant-xxx", model="claude-3-5-sonnet-latest", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] is None
    assert result["sample"] == "pong"
    # First details entry is synthetic.
    list_step = result["details"][0]
    assert list_step["step"] == "list_models"
    assert list_step["status_code"] is None
    assert "anthropic" in (list_step["error"] or "").lower()
    # No raw api_key anywhere.
    assert "sk-ant-xxx" not in json.dumps(result["details"])


def test_anthropic_aggregate_status_error_when_complete_fails():
    err = LLMTransportError("network down")
    tx = _Tx([err])
    c = AnthropicClient(
        name="claude", base_url="https://api.anthropic.com",
        api_key="sk-ant-xxx", model="claude-3-5-sonnet-latest", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "error"
    assert result["sample"] is None
    # complete step records the error.
    complete_step = result["details"][-1]
    assert complete_step["step"] == "complete"
    assert "network down" in (complete_step["error"] or "")


# ── error paths ─────────────────────────────────────────────────────────────


def test_list_models_failure_marks_overall_error_but_still_attempts_complete():
    """If list_models raises but complete succeeds, the aggregate is
    still 'error' (both required for a passing test). Both transcript
    entries must be present."""
    err = LLMProviderError("500 boom", status=500, body="server error")
    tx = _Tx([err, (200, {}, _chat_body("pong"))])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="gpt-4o-mini", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "error"
    # complete still ran and sample was extracted.
    assert result["sample"] == "pong"
    assert [d["step"] for d in result["details"]] == ["list_models", "complete"]
    assert result["details"][0]["error"] is not None
    assert result["details"][1]["error"] is None


def test_complete_failure_marks_overall_error_with_models_still_returned():
    err = LLMTransportError("timeout")
    tx = _Tx([
        (200, {}, _models_body("gpt-4o-mini")),
        err,
    ])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="gpt-4o-mini", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "error"
    assert result["models"] == ["gpt-4o-mini"]
    assert result["sample"] is None
    # complete entry carries the LLMTransportError message.
    complete_step = result["details"][-1]
    assert complete_step["step"] == "complete"
    assert "timeout" in (complete_step["error"] or "")


def test_openai_compatible_list_models_none_is_nonblocking_for_full_test():
    """prompts-061: when every catalog candidate fails (list_models swallows
    the errors and returns None) the FULL provider test no longer fails on that
    alone — the completion probe is the real gate. The failing wire attempts
    are still recorded so the Test Details modal shows them."""
    from backend.llm.client import _candidate_model_list_urls

    base = "http://lab/v1"
    n = len(_candidate_model_list_urls(base))
    err = LLMProviderError("404 not found", status=404, body="")
    # Every candidate 404s, then the complete step gets its own response.
    tx = _Tx([err] * n + [(200, {}, _chat_body("pong"))])
    c = OpenAICompatibleClient(
        name="lab", base_url=base,
        api_key="", model="m", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["sample"] == "pong"
    # The tap recorded the failing GET attempts; runner did NOT synthesise
    # a None-fallback record on top of them.
    list_records = [d for d in result["details"] if d["step"] == "list_models"]
    assert len(list_records) == n
    assert all(r["status_code"] is None or r["error"] for r in list_records)


def test_transcript_records_carry_method_url_status_and_duration():
    tx = _Tx([
        (200, {}, _models_body("m1")),
        (200, {}, _chat_body("ok")),
    ])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="m1", transport=tx,
    )
    result = run_provider_test(c)
    for d in result["details"]:
        assert d["method"] in ("GET", "POST")
        assert d["url"].startswith("https://api.openai.com/v1")
        assert d["status_code"] == 200
        assert d["duration_ms"] >= 0
        assert isinstance(d["response_body"], str)


def test_run_provider_test_clears_tap_after_run():
    """After the runner returns, the client's _tap must be back to its
    pre-call value (None by default) so subsequent production calls do
    not leak transcript records anywhere."""
    tx = _Tx([
        (200, {}, _models_body("m1")),
        (200, {}, _chat_body("ok")),
    ])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="m1", transport=tx,
    )
    assert getattr(c, "_tap", None) is None
    run_provider_test(c)
    assert getattr(c, "_tap", None) is None


# ── prompts-061: empty models list is a NON-BLOCKING warning ────────────────


def test_empty_models_list_is_nonblocking_warning():
    """prompts-061: a 200 OK from /models with an empty data[] no longer fails
    the full Test. The completion probe is the gate, so overall stays 'ok'
    and the list_models step carries a non-blocking warning (not an error)."""
    tx = _Tx([
        (200, {}, json.dumps({"data": []}).encode()),  # list_models — empty
        (200, {}, _chat_body("pong")),                 # complete — works
    ])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="m1", transport=tx,
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] == []
    # complete still ran and produced a sample.
    assert result["sample"] == "pong"


def test_empty_models_annotates_wire_record_with_warning():
    """prompts-061: when the wire call returned [] the runner keeps the single
    honest 200-OK record and annotates it with a non-blocking ``warning``
    rather than appending a red synthetic verdict that flips the aggregate."""
    tx = _Tx([
        (200, {}, json.dumps({"data": []}).encode()),
        (200, {}, _chat_body("pong")),
    ])
    c = OpenAIClient(
        name="o", base_url="https://api.openai.com/v1",
        api_key="sk", model="m1", transport=tx,
    )
    result = run_provider_test(c)
    list_records = [d for d in result["details"] if d["step"] == "list_models"]
    assert len(list_records) == 1  # single honest wire record, no synthetic verdict
    assert list_records[0]["status_code"] == 200
    assert list_records[0]["error"] is None
    assert "0 models" in (list_records[0]["warning"] or "")


def test_empty_models_via_compatible_swallow_only_records_failed_wire_call():
    """When the swallow path fires (every candidate catalog URL raises
    LLMProviderError → OpenAICompatibleClient.list_models returns None,
    prompts-029) the runner must NOT add a second synthetic 'returned
    None' record on top of the already-captured failing wire records.
    prompts-061: those swallowed failures are non-blocking, so the full test
    stays 'ok' when complete() works."""
    from backend.llm.client import _candidate_model_list_urls

    base = "http://lab/v1"
    n = len(_candidate_model_list_urls(base))
    err = LLMProviderError("404 not found", status=404, body="")
    tx = _Tx([err] * n + [(200, {}, _chat_body("pong"))])
    c = OpenAICompatibleClient(
        name="lab", base_url=base,
        api_key="", model="m", transport=tx,
    )
    result = run_provider_test(c)
    list_records = [d for d in result["details"] if d["step"] == "list_models"]
    # Exactly the n wire attempts — no synthetic None-fallback record.
    assert len(list_records) == n
    assert result["status"] == "ok"


# ── prompts-024: complete is skipped when model is empty ────────────────────


def test_complete_skipped_when_model_empty_returns_ok_with_models():
    """The wizard's first ephemeral Test arrives with no model selected
    (the picker only appears after list_models succeeds). The runner
    must NOT call client.complete() in that case — every spec-compliant
    LLM rejects model='' with HTTP 400. Instead it must record a
    skipped 'complete' transcript entry and keep overall_ok=True so
    the wizard can reveal the model picker."""
    tx = _Tx([
        (200, {}, _models_body("m1", "m2")),  # list_models only — no /chat call
    ])
    c = OpenAIClient(
        name="oai", base_url="https://api.openai.com/v1",
        api_key="sk", model="", transport=tx,  # ← empty model
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] == ["m1", "m2"]
    assert result["sample"] is None
    # The runner must NOT have hit /chat/completions. Single transport call.
    assert len(tx.calls) == 1
    assert tx.calls[0]["url"].endswith("/models")
    # Transcript has both steps; complete is the synthetic skip.
    by_step = {d["step"]: d for d in result["details"]}
    assert by_step["list_models"]["status_code"] == 200
    assert by_step["complete"]["status_code"] is None
    assert (by_step["complete"]["error"] or "").startswith("skipped: no model selected")


def test_complete_skipped_preserves_anthropic_special_case():
    """Anthropic + empty model: list_models is the existing synthetic
    skip ('no public list endpoint'), complete is the new no-model
    skip. No wire calls made. overall_ok stays True so the wizard
    flow still works (operator types the model id by hand, re-runs)."""
    tx = _Tx([])  # No transport calls expected at all.
    c = AnthropicClient(
        name="claude", base_url="https://api.anthropic.com",
        api_key="sk-ant", model="", transport=tx,  # ← empty model
    )
    result = run_provider_test(c)
    assert result["status"] == "ok"
    assert result["models"] is None
    assert result["sample"] is None
    assert tx.calls == []
    by_step = {d["step"]: d for d in result["details"]}
    assert "anthropic" in (by_step["list_models"]["error"] or "").lower()
    assert (by_step["complete"]["error"] or "").startswith("skipped: no model selected")


# ── prompts-025: runner safety net catches unexpected exceptions ────────────


class _ExplodingListModels(OpenAIClient):
    """Raises a plain RuntimeError (NOT a typed LLMProviderError) from
    list_models, simulating a future regression where a new decode site
    forgets the defensive parse. The runner must catch it and produce a
    transcript entry instead of letting it bubble to a HTTP 500."""

    def list_models(self):
        raise RuntimeError("kaboom in list_models")


class _ExplodingComplete(OpenAIClient):
    def list_models(self):
        return ["m1"]

    def complete(self, prompt, *, system=None, max_tokens=512, temperature=0.0):
        raise RuntimeError("kaboom in complete")


def test_runner_catches_unexpected_exception_in_list_models():
    c = _ExplodingListModels(
        name="x", base_url="https://x", api_key="k", model="m1",
        transport=_Tx([]),
    )
    result = run_provider_test(c)
    assert result["status"] == "error"
    by_step = {d["step"]: d for d in result["details"]}
    err = by_step["list_models"]["error"] or ""
    assert err.startswith("unexpected RuntimeError")
    assert "kaboom in list_models" in err
    # The complete step still ran (model was set, no skip).
    assert "complete" in by_step


def test_runner_catches_unexpected_exception_in_complete():
    c = _ExplodingComplete(
        name="x", base_url="https://x", api_key="k", model="m1",
        transport=_Tx([]),
    )
    result = run_provider_test(c)
    assert result["status"] == "error"
    by_step = {d["step"]: d for d in result["details"]}
    err = by_step["complete"]["error"] or ""
    assert err.startswith("unexpected RuntimeError")
    assert "kaboom in complete" in err


# ── run_discover_only (prompts-027) ─────────────────────────────────────────


def test_discover_only_openai_happy_path():
    """Single list_models call, no complete call, no sample key."""
    tx = _Tx([
        (200, {}, _models_body("gpt-4o-mini", "gpt-4o")),
    ])
    c = OpenAIClient(
        name="prod", base_url="https://api.openai.com/v1",
        api_key="sk-xxx", model="", transport=tx,
    )
    result = run_discover_only(c)
    assert result["status"] == "ok"
    assert result["models"] == ["gpt-4o-mini", "gpt-4o"]
    assert "sample" not in result
    assert [d["step"] for d in result["details"]] == ["list_models"]
    assert len(tx.calls) == 1  # no complete call


def test_discover_only_anthropic_returns_ok_with_synthetic_record():
    """Anthropic has no /models endpoint — discover still returns ok=True
    so the wizard can advance to the free-text model input."""
    tx = _Tx([])  # no wire calls expected
    c = AnthropicClient(
        name="anth", base_url="https://api.anthropic.com",
        api_key="sk-ant", model="", transport=tx,
    )
    result = run_discover_only(c)
    assert result["status"] == "ok"
    assert result["models"] is None
    assert len(result["details"]) == 1
    assert result["details"][0]["step"] == "list_models"
    assert "anthropic" in (result["details"][0]["error"] or "")
    assert len(tx.calls) == 0


def test_discover_only_empty_list_is_023_failure():
    tx = _Tx([(200, {}, _models_body())])  # empty data array
    c = OpenAIClient(
        name="x", base_url="https://x", api_key="k", model="", transport=tx,
    )
    result = run_discover_only(c)
    assert result["status"] == "error"
    assert any(
        d.get("error") and "0 models" in d["error"]
        for d in result["details"]
    )


def test_discover_only_transport_error():
    tx = _Tx([LLMTransportError("connection refused")])
    c = OpenAIClient(
        name="x", base_url="https://x", api_key="k", model="", transport=tx,
    )
    result = run_discover_only(c)
    assert result["status"] == "error"
    assert any(
        "LLMTransportError" in (d.get("error") or "")
        or "connection refused" in (d.get("error") or "")
        for d in result["details"]
    )

