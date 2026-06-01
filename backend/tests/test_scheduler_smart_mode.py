"""Tests for backend.scheduler.submit_smart_job + on_new_feed trigger (021E-3).

Strategy: mock backend.normalizer.smart_runner.run_smart_job so no LLM is
called. Assert that submit_smart_job:
  * fires under correct config + non-pending state
  * is suppressed by every gate (disabled global, disabled per-source,
    pending-proposal idempotency, invalid reason, on_new_feed.enabled=False)
  * honours provider precedence
  * respects the concurrency semaphore
  * is unaffected by auto_apply.enabled (021E-3 contract — 021E-4 changes this)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend import scheduler as scheduler_mod
from backend.ingestion import jobs as jobs_mod
from backend.normalizer import config as cfg_mod
from backend.normalizer import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    # Isolate proposals.db
    monkeypatch.setattr(proposals_mod, "_PROPOSALS_DB_PATH", tmp_path / "proposals.db")
    # Isolate normalizer-config.yaml
    cfg_path = tmp_path / "normalizer-config.yaml"
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", cfg_path)
    # Reset job_store + semaphore between tests
    jobs_mod.job_store.reset()
    # Re-init the semaphore (mirrors what reload() would do).
    scheduler_mod._smart_semaphore = asyncio.Semaphore(2)
    scheduler_mod._smart_semaphore_limit = 2
    yield cfg_path


def _write_cfg(path: Path, smart_mode: dict[str, Any]) -> None:
    path.write_text(yaml.dump({"smart_mode": smart_mode}), encoding="utf-8")


class _RunnerSpy:
    """Captures run_smart_job invocations and lets tests gate completion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()


@pytest.mark.asyncio
async def test_submit_fires_when_globally_enabled(_isolate: Path, monkeypatch):
    _write_cfg(_isolate, {"enabled": True, "on_new_feed": {"enabled": True}})
    spy = _RunnerSpy()
    monkeypatch.setattr(
        "backend.normalizer.smart_runner.run_smart_job", spy
    )
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    assert job_id is not None
    # Give the background task a tick to run
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(spy.calls) == 1
    assert spy.calls[0]["source"] == "feed-a"
    assert spy.calls[0]["trigger_reason"] == "on_new_feed"


@pytest.mark.asyncio
async def test_submit_suppressed_when_globally_disabled_for_schedule_reason(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {"enabled": False})
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="schedule")
    assert job_id is None
    assert spy.calls == []


@pytest.mark.asyncio
async def test_submit_manual_runs_even_when_globally_disabled(
    _isolate: Path, monkeypatch,
):
    """Manual triggers (operator-initiated) bypass the smart_mode.enabled gate."""
    _write_cfg(_isolate, {"enabled": False})
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="manual")
    assert job_id is not None
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_submit_suppressed_when_per_source_disabled(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {
        "enabled": True,
        "on_new_feed": {"enabled": True},
        "sources": [{"name": "feed-a", "enabled": False}],
    })
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    assert job_id is None
    assert spy.calls == []


@pytest.mark.asyncio
async def test_submit_suppressed_when_on_new_feed_disabled(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {"enabled": True, "on_new_feed": {"enabled": False}})
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    assert job_id is None
    assert spy.calls == []


@pytest.mark.asyncio
async def test_submit_suppressed_when_pending_proposal_exists(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {"enabled": True, "on_new_feed": {"enabled": True}})
    # Seed a pending proposal for feed-a
    await proposals_mod.insert_proposal(
        source_name="feed-a", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={}, prompt_system="", prompt_user="",
        llm_response_raw="", status="pending",
    )
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    assert job_id is None
    assert spy.calls == []


@pytest.mark.asyncio
async def test_invalid_reason_is_rejected(_isolate: Path, monkeypatch):
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="bogus")
    assert job_id is None
    assert spy.calls == []


@pytest.mark.asyncio
async def test_provider_precedence_per_call_beats_per_source(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {
        "enabled": True,
        "provider": "global-prov",
        "on_new_feed": {"enabled": True},
        "sources": [{"name": "feed-a", "enabled": True, "provider": "per-source-prov"}],
    })
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    # per-call override wins
    await scheduler_mod.submit_smart_job(
        "feed-a", reason="manual", provider="per-call-prov",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert spy.calls[0]["provider_name"] == "per-call-prov"


@pytest.mark.asyncio
async def test_provider_precedence_per_source_beats_global(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {
        "enabled": True,
        "provider": "global-prov",
        "on_new_feed": {"enabled": True},
        "sources": [{"name": "feed-a", "enabled": True, "provider": "per-source-prov"}],
    })
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert spy.calls[0]["provider_name"] == "per-source-prov"


@pytest.mark.asyncio
async def test_provider_precedence_global_when_no_per_source(
    _isolate: Path, monkeypatch,
):
    _write_cfg(_isolate, {
        "enabled": True,
        "provider": "global-prov",
        "on_new_feed": {"enabled": True},
    })
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert spy.calls[0]["provider_name"] == "global-prov"


@pytest.mark.asyncio
async def test_auto_apply_enabled_does_not_change_021e3_behaviour(
    _isolate: Path, monkeypatch,
):
    """021E-3 contract: auto_apply.enabled is a no-op field at this phase.
    Test locks the contract so 021E-4's behavioural change is observable."""
    _write_cfg(_isolate, {
        "enabled": True,
        "on_new_feed": {"enabled": True},
        "auto_apply": {"enabled": True},
    })
    spy = _RunnerSpy()
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)
    job_id = await scheduler_mod.submit_smart_job("feed-a", reason="on_new_feed")
    assert job_id is not None
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_concurrency_semaphore_caps_at_max_concurrent(
    _isolate: Path, monkeypatch,
):
    """Third concurrent job must wait until one of the first two completes.

    The semaphore is shared by all submitted jobs; we gate the spy so we
    can see the queued state.
    """
    _write_cfg(_isolate, {
        "enabled": True,
        "on_new_feed": {"enabled": True},
        "concurrency": {"max_concurrent": 2},
    })
    # Reset the semaphore to honour the configured limit (reload() does
    # this in production; we reset it manually here).
    scheduler_mod._smart_semaphore = asyncio.Semaphore(2)
    scheduler_mod._smart_semaphore_limit = 2

    spy = _RunnerSpy()
    spy.gate = asyncio.Event()  # block runner until released
    monkeypatch.setattr("backend.normalizer.smart_runner.run_smart_job", spy)

    # Submit 3 jobs against 3 distinct sources (avoids the
    # pending-proposal idempotency gate).
    await scheduler_mod.submit_smart_job("feed-1", reason="manual")
    await scheduler_mod.submit_smart_job("feed-2", reason="manual")
    await scheduler_mod.submit_smart_job("feed-3", reason="manual")

    # Yield control a few times so the first two acquire the semaphore.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(spy.calls) == 2, (
        f"expected exactly 2 runners through the semaphore, got {len(spy.calls)}"
    )

    # Release the gate; all three should now complete.
    spy.gate.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(spy.calls) == 3


# ── on_new_feed wiring through job_store.complete() ─────────────────────────


@pytest.mark.asyncio
async def test_job_complete_with_first_ingest_calls_submit_smart_job(
    _isolate: Path, monkeypatch,
):
    """When job_store.complete() runs with first_ingest=True, it must fan
    out to scheduler.submit_smart_job."""
    captured: list[dict[str, Any]] = []

    async def fake_submit(source: str, *, reason: str, **kw: Any) -> None:
        captured.append({"source": source, "reason": reason, **kw})

    monkeypatch.setattr(scheduler_mod, "submit_smart_job", fake_submit)

    job = jobs_mod.job_store.create("feed-a", "local_feed", first_ingest=True)
    jobs_mod.job_store.complete(job.id, {"inserted": 5})
    # The submission goes through loop.create_task; yield to run it.
    for _ in range(3):
        await asyncio.sleep(0)
    assert captured == [{"source": "feed-a", "reason": "on_new_feed"}]


@pytest.mark.asyncio
async def test_job_complete_without_first_ingest_does_not_fire(
    _isolate: Path, monkeypatch,
):
    captured: list[Any] = []

    async def fake_submit(source: str, *, reason: str, **kw: Any) -> None:
        captured.append(source)

    monkeypatch.setattr(scheduler_mod, "submit_smart_job", fake_submit)

    job = jobs_mod.job_store.create("feed-a", "local_feed", first_ingest=False)
    jobs_mod.job_store.complete(job.id, {"inserted": 5})
    for _ in range(3):
        await asyncio.sleep(0)
    assert captured == []


@pytest.mark.asyncio
async def test_smart_proposal_job_completion_does_not_fan_out(
    _isolate: Path, monkeypatch,
):
    """Smart-proposal jobs must not recursively trigger another smart job."""
    captured: list[Any] = []

    async def fake_submit(source: str, *, reason: str, **kw: Any) -> None:
        captured.append(source)

    monkeypatch.setattr(scheduler_mod, "submit_smart_job", fake_submit)

    job = jobs_mod.job_store.create("feed-a", "smart_proposal", first_ingest=True)
    jobs_mod.job_store.complete(job.id, {"proposal_id": 1})
    for _ in range(3):
        await asyncio.sleep(0)
    assert captured == []
