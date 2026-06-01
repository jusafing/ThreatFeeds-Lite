"""Tests for the consolidated (multi-feed) smart-mode runner (prompts-032 Phase C).

Covers ``smart.sample_consolidated_entries`` union/skip behaviour and
``smart_runner.run_consolidated_smart_job`` end-to-end with a mocked LLM and a
real proposals.db:

  * a multi-feed request produces exactly ONE proposal row whose
    source_name is the consolidated sentinel, sources_json lists the
    contributing feeds, and mapping_json is the consolidated (validated) dict;
  * field_scope is persisted; status='pending' / outcome='pending_review'
    (no scoring, no auto-apply);
  * feeds with no entries are skipped (not fatal); all-empty fails the job.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.llm import config as llm_cfg_mod
from backend.normalizer import config as norm_cfg_mod
from backend.normalizer import proposals as proposals_mod
from backend.normalizer import smart as smart_mod
from backend.normalizer import smart_runner as smart_runner_mod
from backend.normalizer.proposals import CONSOLIDATED_SENTINEL
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


class _FakeLLMClient:
    name = "fake"
    model = "fake-model"

    def __init__(self, response_text: str):
        self._text = response_text

    def complete(self, _prompt: str, *, system: str = "", max_tokens: int = 0,
                 temperature: float = 0.0, timeout: float | None = None,
                 model: str | None = None) -> str:
        return self._text

    def last_exchange_raw(self, error: Exception | None = None) -> tuple[str, str]:
        body = getattr(error, "body", None) if error is not None else None
        return ("POST https://fake/v1/chat\n\n{}", body or "{\"resp\": true}")


def _seed_canonical_fields_yaml(monkeypatch):
    def fake_loader():
        return {
            "core_fields": [{"name": "title"}, {"name": "indicator"}, {"name": "severity"}],
            "custom_fields": [],
        }
    monkeypatch.setattr(smart_mod, "load_fields", fake_loader)


def _mock_per_source_samples(monkeypatch, mapping: dict[str, list[dict]]):
    """Patch smart.sample_raw_entries to return per-source fixtures.

    ``mapping`` maps a source name to its rows; an absent source raises
    SmartModeError (mirroring an empty feed)."""
    async def fake(source_name, sample_size=20):
        rows = mapping.get(source_name)
        if rows is None:
            raise smart_mod.SmartModeError(f"source {source_name!r} has no entries")
        return rows
    monkeypatch.setattr(smart_mod, "sample_raw_entries", fake)


# ── sample_consolidated_entries unit behaviour ──────────────────────────────


@pytest.mark.asyncio
async def test_sample_consolidated_unions_and_skips_empty(monkeypatch):
    _mock_per_source_samples(monkeypatch, {
        "feed-a": [{"a": "1", "b": "2"}],
        "feed-b": [{"b": "x", "c": "y"}],
        # feed-empty absent → skipped
    })
    samples, contributing = await smart_mod.sample_consolidated_entries(
        ["feed-a", "feed-empty", "feed-b"], sample_size=5,
    )
    assert contributing == ["feed-a", "feed-b"]
    assert len(samples) == 2
    assert set(smart_mod.discover_raw_field_names(samples)) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_sample_consolidated_all_empty_raises(monkeypatch):
    _mock_per_source_samples(monkeypatch, {})
    with pytest.raises(smart_mod.SmartModeError):
        await smart_mod.sample_consolidated_entries(["x", "y"], sample_size=5)


# ── run_consolidated_smart_job end-to-end ───────────────────────────────────


@pytest.mark.asyncio
async def test_run_consolidated_persists_single_proposal(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    _mock_per_source_samples(monkeypatch, {
        "feed-a": [{"a": "1", "b": "2"}] * 3,
        "feed-b": [{"b": "x", "c": "y"}] * 3,
    })
    # LLM maps a/b/c plus one unknown canonical (dropped) and one skip.
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient(
            '{"a": "title", "b": "indicator", "c": "severity", '
            '"z": "nope", "b_extra": "__skip__"}'
        ),
    )
    job = jobs_mod.job_store.create(CONSOLIDATED_SENTINEL, "smart_proposal")
    await smart_runner_mod.run_consolidated_smart_job(
        job_id=job.id,
        sources=["feed-a", "feed-b"],
        provider_name=None,
        sample_size=3,
        field_scope="configured",
    )

    rows = await proposals_mod.list_proposals(source=CONSOLIDATED_SENTINEL)
    assert len(rows) == 1
    p = rows[0]
    assert p["source_name"] == CONSOLIDATED_SENTINEL
    assert p["sources"] == ["feed-a", "feed-b"]
    assert p["field_scope"] == "configured"
    assert p["status"] == "pending"
    assert p["outcome"] == "pending_review"
    assert p["auto_applied"] is False
    # Consolidated mapping: a/b/c kept; unknown 'z' dropped; '__skip__' dropped.
    assert p["mapping"] == {"a": "title", "b": "indicator", "c": "severity"}
    assert set(p["raw_fields"]) == {"a", "b", "c"}
    # prompts-037: raw exchange captured on the consolidated proposal too.
    assert p["llm_request_raw"].startswith("POST https://fake/v1/chat")
    assert p["llm_response_json"] == '{"resp": true}'

    done = jobs_mod.job_store.get(job.id)
    assert done.state == "done"


@pytest.mark.asyncio
async def test_run_consolidated_all_empty_fails_job(monkeypatch):
    _seed_canonical_fields_yaml(monkeypatch)
    _mock_per_source_samples(monkeypatch, {})
    monkeypatch.setattr(
        smart_runner_mod, "get_client",
        lambda name=None: _FakeLLMClient("{}"),
    )
    job = jobs_mod.job_store.create(CONSOLIDATED_SENTINEL, "smart_proposal")
    await smart_runner_mod.run_consolidated_smart_job(
        job_id=job.id,
        sources=["x", "y"],
        provider_name=None,
        sample_size=3,
        field_scope="all",
    )
    failed = jobs_mod.job_store.get(job.id)
    assert failed.state == "error"
    rows = await proposals_mod.list_proposals(source=CONSOLIDATED_SENTINEL)
    assert rows == []
