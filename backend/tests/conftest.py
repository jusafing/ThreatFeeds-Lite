"""Shared pytest fixtures for the backend test suite.

prompts-039: ``run_normalizer`` now records every run into
``data/run_history.db``. Many tests exercise ``run_normalizer`` (engine,
scheduler, routes) without caring about history. This autouse fixture
redirects the run-history DB to a per-test temp file so those tests never
touch the real ``data/`` directory. Tests that assert on history (e.g.
``test_run_history.py``) override ``_RUN_DB_PATH`` themselves — applied last,
their fixture wins.
"""
from __future__ import annotations

import pytest

from backend.normalizer import run_history as run_history_mod


@pytest.fixture(autouse=True)
def _isolate_run_history_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_history_mod, "_RUN_DB_PATH", tmp_path / "run_history.db"
    )
    yield
