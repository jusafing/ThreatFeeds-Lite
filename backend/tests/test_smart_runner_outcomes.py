"""Integration tests for 021E-4: scoring, outcome decision, auto-apply path.

Covers:
  * smart_runner._decide_outcome: every branch (manual/empty/disabled/
    conflict/sub-sample/below-threshold/auto-applied)
  * smart_runner.run_smart_job end-to-end with mocked LLM and a real
    proposals.db, verifying the persisted score / score_breakdown /
    outcome / auto_applied / status columns and the auto-apply flip
    to manual_mappings
  * routes_smart.get_proposals: default outcome filter hides discarded
    and auto-applied; outcome=all and explicit values work
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.llm import config as llm_cfg_mod
from backend.main import app
from backend.normalizer import config as norm_cfg_mod
from backend.normalizer import proposals as proposals_mod
from backend.normalizer import smart as smart_mod
from backend.normalizer import smart_runner as smart_runner_mod
from backend.ingestion import jobs as jobs_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_cfg_mod, "_LLM_CONFIG_PATH", tmp_path / "llm-providers.yaml")
    monkeypatch.setattr(proposals_mod, "_PROPOSALS_DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(
        norm_cfg_mod, "_NORMALIZER_CONFIG_PATH", tmp_path / "normalizer-config.yaml",
    )
    jobs_mod.job_store.reset()
    yield


# ── _decide_outcome unit tests ──────────────────────────────────────────────


def _cfg_auto_on(min_delta: float = 0.05) -> dict[str, Any]:
    return {"auto_apply": {"enabled": True, "min_coverage_delta": min_delta}}


def _cfg_auto_off() -> dict[str, Any]:
    return {"auto_apply": {"enabled": False, "min_coverage_delta": 0.05}}


def test_decide_manual_trigger_never_auto_applies():
    outcome, _ = smart_runner_mod._decide_outcome(
        trigger_reason="manual", sample_count=20,
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.5, smart_mode_cfg=_cfg_auto_on(),
    )
    assert outcome == "pending_review"


def test_decide_empty_proposal_falls_to_pending():
    outcome, _ = smart_runner_mod._decide_outcome(
        trigger_reason="schedule", sample_count=20,
        proposed_mapping={}, existing_mapping={},
        coverage_delta=0.0, smart_mode_cfg=_cfg_auto_on(),
    )
    assert outcome == "pending_review"


def test_decide_auto_apply_disabled_returns_pending():
    outcome, _ = smart_runner_mod._decide_outcome(
        trigger_reason="schedule", sample_count=20,
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.5, smart_mode_cfg=_cfg_auto_off(),
    )
    assert outcome == "pending_review"


def test_decide_conflict_blocks_auto_apply():
    outcome, reason = smart_runner_mod._decide_outcome(
        trigger_reason="on_new_feed", sample_count=20,
        proposed_mapping={"a": "title", "b": "indicator"},
        existing_mapping={"a": "url"},  # conflict on 'a'
        coverage_delta=0.5, smart_mode_cfg=_cfg_auto_on(),
    )
    assert outcome == "pending_review"
    assert "conflicts" in reason


def test_decide_sub_minimum_sample_blocks_auto_apply():
    outcome, reason = smart_runner_mod._decide_outcome(
        trigger_reason="schedule", sample_count=4,  # < 5
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.5, smart_mode_cfg=_cfg_auto_on(),
    )
    assert outcome == "pending_review"
    assert "sample size" in reason


def test_decide_below_threshold_returns_discarded():
    outcome, reason = smart_runner_mod._decide_outcome(
        trigger_reason="schedule", sample_count=20,
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.03, smart_mode_cfg=_cfg_auto_on(min_delta=0.05),
    )
    assert outcome == "discarded_below_threshold"
    assert "0.03" in reason or "0.0300" in reason


def test_decide_above_threshold_auto_applies():
    outcome, _ = smart_runner_mod._decide_outcome(
        trigger_reason="on_new_feed", sample_count=20,
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.99, smart_mode_cfg=_cfg_auto_on(min_delta=0.05),
    )
    assert outcome == "auto_applied"


def test_decide_at_threshold_boundary_auto_applies():
    """Exactly at threshold counts as auto-apply (>= comparison)."""
    outcome, _ = smart_runner_mod._decide_outcome(
        trigger_reason="schedule", sample_count=20,
        proposed_mapping={"a": "title"}, existing_mapping={},
        coverage_delta=0.05, smart_mode_cfg=_cfg_auto_on(min_delta=0.05),
    )
    assert outcome == "auto_applied"


# ── run_smart_job persists scoring + outcome ────────────────────────────────


class _FakeLLMClient:
    name = "fake"
    model = "fake-model"

    def __init__(self, response_text: str, *, raise_exc: Exception | None = None):
        self._text = response_text
        self._raise = raise_exc

    def complete(self, _prompt: str, *, system: str = "", max_tokens: int = 0,
                 temperature: float = 0.0, timeout: float | None = None,
                 model: str | None = None) -> str:
        if self._raise is not None:
            raise self._raise
        return self._text

    def last_exchange_raw(self, error: Exception | None = None) -> tuple[str, str]:
        # prompts-037: mirror the real client contract — request always
        # available; response body sourced from the error body on HTTP errors.
        body = getattr(error, "body", None) if error is not None else None
        return ("POST https://fake/v1/chat\n\n{}", body or "{\"resp\": true}")


def _seed_canonical_fields_yaml(monkeypatch):
    """The validator needs at least 'title' + 'indicator' as canonical."""
    def fake_loader():
        return {"core_fields": [{"name": "title"}, {"name": "indicator"}],
                "custom_fields": []}
    monkeypatch.setattr(smart_mod, "load_fields", fake_loader)


@pytest.mark.asyncio
async def test_run_smart_job_manual_persists_pending_review(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "1", "b": "2"}] * 10),
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"a": "title", "b": "indicator"}'),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="manual",
    )

    proposals = await proposals_mod.list_proposals(source="s")
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "pending"
    assert p["outcome"] == "pending_review"
    assert p["auto_applied"] is False
    assert p["trigger_reason"] == "manual"
    assert p["score"] is not None  # scoring runs for manual too
    bd = p["score_breakdown"]
    assert {"coverage_before", "coverage_after", "coverage_delta"} <= bd.keys()


@pytest.mark.asyncio
async def test_run_smart_job_auto_apply_flips_manual_mappings(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    # Operator opted-in to auto-apply with a 1% threshold.
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {},
        "smart_mode": {
            "enabled": True,
            "auto_apply": {"enabled": True, "min_coverage_delta": 0.01},
        },
    })
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "x", "b": "y"}] * 10),
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"a": "title", "b": "indicator"}'),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="on_new_feed",
    )

    proposals = await proposals_mod.list_proposals(source="s", outcome="all")
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "approved"
    assert p["outcome"] == "auto_applied"
    assert p["auto_applied"] is True
    # manual_mappings updated.
    on_disk = norm_cfg_mod.load_normalizer_config()
    assert on_disk["manual_mappings"]["s"] == {"a": "title", "b": "indicator"}


@pytest.mark.asyncio
async def test_run_smart_job_discard_when_no_improvement(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    # Existing maps the only populated field ("a"); LLM proposes "b" which
    # is absent from every sample row → population[b]=0 → coverage_delta=0.
    # No conflict (existing has no "b") so we reach the threshold check.
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {"s": {"a": "title"}},
        "smart_mode": {
            "enabled": True,
            "auto_apply": {"enabled": True, "min_coverage_delta": 0.05},
        },
    })
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "x"}] * 10),  # only "a" populated
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"a": "title"}'),  # proposes "a" again
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="schedule",
    )

    proposals = await proposals_mod.list_proposals(source="s", outcome="all")
    assert len(proposals) == 1
    p = proposals[0]
    # NOTE: proposal "a" is in existing → conflict → outcome pending_review,
    # not discard. To exercise the discard branch we need delta < threshold
    # without conflict. We re-run the helper directly below as a unit test.
    assert p["outcome"] == "pending_review"


@pytest.mark.asyncio
async def test_run_smart_job_discard_via_zero_population_field(monkeypatch):
    """A non-conflicting proposal whose mapped field has 0 sample population
    has coverage_delta=0 → outcome='discarded_below_threshold'."""
    _seed_canonical_fields_yaml(monkeypatch)
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual", "enabled": True, "interval_minutes": 10,
        "manual_mappings": {},
        "smart_mode": {
            "enabled": True,
            "auto_apply": {"enabled": True, "min_coverage_delta": 0.05},
        },
    })
    # Sample only contains "a"; LLM proposes "b" (absent from samples).
    # Population: a=10, b=0; total=10. Mapping b contributes 0/10 → delta=0.
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "x"}] * 10),
    )
    # Use discover_raw_field_names result by also having LLM see "b" via
    # response. validate_proposal will keep "b" only if b is in raw_fields —
    # raw_fields comes from discover_raw_field_names(samples) which only sees
    # "a". So the LLM mapping for "b" will be dropped by validator. We need
    # samples that include "b" with NO populated values.
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async(
            [{"a": "x", "b": None}] * 10  # b present-but-None → counted in
        ),                                 # raw_fields, population[b]=0
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"b": "title"}'),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="schedule",
    )
    proposals = await proposals_mod.list_proposals(source="s", outcome="all")
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "rejected"
    assert p["outcome"] == "discarded_below_threshold"
    assert p["auto_applied"] is False
    assert "auto-discarded" in (p["decided_by_note"] or "")


@pytest.mark.asyncio
async def test_run_smart_job_conflict_falls_back_to_pending_review(monkeypatch):
    """A conflict on an automated trigger must NOT auto-apply, even above
    the coverage threshold."""
    _seed_canonical_fields_yaml(monkeypatch)
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {"s": {"a": "url"}},  # conflict with proposal "a"
        "smart_mode": {
            "enabled": True,
            "auto_apply": {"enabled": True, "min_coverage_delta": 0.01},
        },
    })
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "x", "b": "y"}] * 10),
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"a": "title", "b": "indicator"}'),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="on_new_feed",
    )

    proposals = await proposals_mod.list_proposals(source="s")
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "pending"
    assert p["outcome"] == "pending_review"
    # Existing mapping untouched.
    assert norm_cfg_mod.load_normalizer_config()["manual_mappings"]["s"] == {"a": "url"}


# ── route filter: default hides discarded + auto_applied ────────────────────


@pytest.mark.asyncio
async def test_get_proposals_default_excludes_discarded_and_auto_applied():
    await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=20,
        raw_fields=["a"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
        outcome="pending_review",
    )
    await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=20,
        raw_fields=["b"], mapping={"b": "indicator"},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="approved", outcome="auto_applied", auto_applied=True,
    )
    await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=20,
        raw_fields=["c"], mapping={"c": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="rejected", outcome="discarded_below_threshold",
    )

    client = TestClient(app)
    # Default: only pending_review.
    r = client.get("/api/smart-mappings/proposals?source=s")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["outcome"] == "pending_review"

    # outcome=all: everything.
    r = client.get("/api/smart-mappings/proposals?source=s&outcome=all")
    assert r.status_code == 200
    assert len(r.json()) == 3

    # outcome=auto_applied: just the one.
    r = client.get("/api/smart-mappings/proposals?source=s&outcome=auto_applied")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["outcome"] == "auto_applied"
    assert data[0]["auto_applied"] is True

    # outcome=discarded_below_threshold.
    r = client.get(
        "/api/smart-mappings/proposals?source=s&outcome=discarded_below_threshold"
    )
    assert len(r.json()) == 1

    # Invalid outcome → 400.
    r = client.get("/api/smart-mappings/proposals?source=s&outcome=bogus")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_approve_via_route_persists_outcome_approved():
    """Manual operator approve sets outcome='approved' (not 'auto_applied')."""
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual", "enabled": True, "interval_minutes": 10,
        "manual_mappings": {},
    })
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=20,
        raw_fields=["a"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200
    assert r.json()["outcome"] == "approved"
    assert r.json()["auto_applied"] is False
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["outcome"] == "approved"


# ── prompts-037: raw exchange capture on every persisted proposal ───────────


@pytest.mark.asyncio
async def test_run_smart_job_success_persists_raw_exchange(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "1", "b": "2"}] * 10),
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient('{"a": "title", "b": "indicator"}'),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="manual",
    )
    p = (await proposals_mod.list_proposals(source="s", outcome="all"))[0]
    assert p["llm_request_raw"].startswith("POST https://fake/v1/chat")
    assert p["llm_response_json"] == '{"resp": true}'


@pytest.mark.asyncio
async def test_run_smart_job_provider_error_persists_response_body(monkeypatch):
    """On an HTTP error the persisted response JSON comes from the exc body."""
    from backend.llm.errors import LLMProviderError

    _seed_canonical_fields_yaml(monkeypatch)
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "1"}] * 10),
    )
    exc = LLMProviderError(
        "boom", status=400, body='{"error": "bad request"}', attempted_urls=[],
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient("", raise_exc=exc),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="manual",
    )
    p = (await proposals_mod.list_proposals(source="s", outcome="all"))[0]
    assert p["status"] == "error"
    assert p["outcome"] == "error"
    assert p["llm_request_raw"].startswith("POST https://fake/v1/chat")
    assert p["llm_response_json"] == '{"error": "bad request"}'


@pytest.mark.asyncio
async def test_run_smart_job_parse_error_persists_raw_exchange(monkeypatch):
    """A response that parses to nothing usable still captures the exchange."""
    _seed_canonical_fields_yaml(monkeypatch)
    monkeypatch.setattr(
        smart_runner_mod, "sample_raw_entries",
        lambda source, sample_size: _fake_async([{"a": "1"}] * 10),
    )
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient("this is not json at all"),
    )
    job = jobs_mod.job_store.create("s", "smart_proposal")
    await smart_runner_mod.run_smart_job(
        job_id=job.id, source="s", provider_name=None,
        sample_size=10, trigger_reason="manual",
    )
    rows = await proposals_mod.list_proposals(source="s", outcome="all")
    assert len(rows) == 1
    p = rows[0]
    assert p["status"] == "error"
    assert p["llm_request_raw"].startswith("POST https://fake/v1/chat")
    assert p["llm_response_json"] == '{"resp": true}'


# ── helpers ─────────────────────────────────────────────────────────────────


async def _fake_async(value):
    return value