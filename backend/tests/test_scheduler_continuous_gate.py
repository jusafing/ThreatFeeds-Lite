"""Tests for the prompts-042 continuous-pull scheduling gate.

api_pull / rss_pull honour an optional ``continuous`` flag; absent defaults to
True (backward compatible). remote_json_pull keeps continuous default False.
"""
from __future__ import annotations

import pytest

from backend import scheduler as scheduler_mod


@pytest.fixture(autouse=True)
def _quiet_other_schedules(monkeypatch):
    # Disable normalizer + smart-mode scheduling so only pull jobs are built.
    monkeypatch.setattr(scheduler_mod, "load_normalizer_config", lambda: {"enabled": False})
    monkeypatch.setattr(
        scheduler_mod,
        "_resolved_smart_mode_config",
        lambda cfg: {
            "enabled": False,
            "schedule": {"enabled": False, "interval_minutes": 30},
            "concurrency": {"max_concurrent": 2},
        },
    )
    yield
    scheduler_mod.scheduler.remove_all_jobs()


def _job_ids() -> set[str]:
    return {j.id for j in scheduler_mod.scheduler.get_jobs()}


def test_rss_absent_continuous_is_scheduled(monkeypatch):
    """An rss_pull entry without a continuous key keeps being scheduled."""
    monkeypatch.setattr(
        scheduler_mod,
        "load_sources",
        lambda: {"rss_pull": [{"name": "legacy", "url": "http://x", "enabled": True}]},
    )
    scheduler_mod.reload()
    assert "rss_pull__legacy" in _job_ids()


def test_rss_continuous_false_not_scheduled(monkeypatch):
    """continuous: false suppresses scheduling for rss_pull."""
    monkeypatch.setattr(
        scheduler_mod,
        "load_sources",
        lambda: {
            "rss_pull": [
                {"name": "manualrss", "url": "http://x", "enabled": True, "continuous": False}
            ]
        },
    )
    scheduler_mod.reload()
    assert "rss_pull__manualrss" not in _job_ids()


def test_api_continuous_false_not_scheduled(monkeypatch):
    """continuous: false suppresses scheduling for api_pull."""
    monkeypatch.setattr(
        scheduler_mod,
        "load_sources",
        lambda: {
            "api_pull": [
                {"name": "manualapi", "url": "http://x", "enabled": True, "continuous": False}
            ]
        },
    )
    scheduler_mod.reload()
    assert "api_pull__manualapi" not in _job_ids()


def test_api_continuous_true_scheduled(monkeypatch):
    """continuous: true schedules an api_pull entry."""
    monkeypatch.setattr(
        scheduler_mod,
        "load_sources",
        lambda: {
            "api_pull": [
                {"name": "liveapi", "url": "http://x", "enabled": True, "continuous": True}
            ]
        },
    )
    scheduler_mod.reload()
    assert "api_pull__liveapi" in _job_ids()


def test_remote_json_absent_continuous_not_scheduled(monkeypatch):
    """remote_json_pull keeps its default-False continuous gate."""
    monkeypatch.setattr(
        scheduler_mod,
        "load_sources",
        lambda: {
            "remote_json_pull": [{"name": "rj", "url": "http://x", "enabled": True}]
        },
    )
    scheduler_mod.reload()
    assert "remote_json_pull__rj" not in _job_ids()
