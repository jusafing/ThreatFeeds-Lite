"""Tests for backend.ingestion.jobs (JobStore)."""
from __future__ import annotations

import time

import pytest

from backend.ingestion.jobs import JobStore


@pytest.fixture
def store():
    return JobStore()


def test_create_returns_job_with_initial_state(store):
    job = store.create("src1", "local_feed", first_ingest=True)
    assert job.source == "src1"
    assert job.kind == "local_feed"
    assert job.first_ingest is True
    assert job.state == "queued"
    assert job.step == "fetching"
    assert job.processed == 0
    assert job.total == 0
    assert job.finished_at is None


def test_get_returns_none_for_unknown_id(store):
    assert store.get("nope") is None


def test_update_step_transitions(store):
    job = store.create("src1", "local_feed")
    store.update_step(job.id, "parsing")
    assert store.get(job.id).state == "running"
    assert store.get(job.id).step == "parsing"
    store.update_step(job.id, "inserting", total=10)
    assert store.get(job.id).total == 10


def test_set_running_transitions_queued_to_running_without_step_change(store):
    """Regression: routes_smart.create_smart_job calls set_running right
    after job_store.create so the API can immediately report state='running'
    before the worker task has had a chance to call update_step. The method
    must exist on JobStore and must NOT clobber the default step."""
    job = store.create("src1", "smart_proposal")
    assert job.state == "queued"
    assert job.step == "fetching"  # default
    store.set_running(job.id)
    j = store.get(job.id)
    assert j.state == "running"
    assert j.step == "fetching"  # unchanged


def test_set_running_is_noop_for_unknown_id(store):
    # Must not raise — matches the tolerance of update_step / update_progress.
    store.set_running("nope")


def test_update_progress(store):
    job = store.create("src1", "local_feed")
    store.update_progress(job.id, 5)
    assert store.get(job.id).processed == 5


def test_complete_sets_terminal_state(store):
    job = store.create("src1", "local_feed")
    store.update_step(job.id, "inserting", total=10)
    counters = {"total_read": 10, "inserted": 8, "duplicates": 1, "discarded": 1}
    store.complete(job.id, counters)
    j = store.get(job.id)
    assert j.state == "done"
    assert j.step == "done"
    assert j.counters == counters
    assert j.processed == 10
    assert j.finished_at is not None
    assert j.expires_at is not None


def test_fail_sets_error_state(store):
    job = store.create("src1", "local_feed")
    store.fail(job.id, "network down")
    j = store.get(job.id)
    assert j.state == "error"
    assert j.error_msg == "network down"
    assert j.finished_at is not None


def test_list_active_excludes_terminal(store):
    a = store.create("a", "local_feed")
    b = store.create("b", "local_feed")
    store.complete(a.id, {})
    active = store.list_active()
    assert len(active) == 1
    assert active[0].id == b.id


def test_to_dict_omits_expires_at(store):
    job = store.create("src1", "local_feed")
    d = job.to_dict()
    assert "expires_at" not in d
    assert d["id"] == job.id
    assert d["source"] == "src1"


def test_evict_removes_terminal_jobs_past_ttl(store):
    job = store.create("src1", "local_feed")
    store.complete(job.id, {})
    # Force the expires_at into the past
    store._jobs[job.id].expires_at = time.time() - 1
    # Any operation triggers _evict
    assert store.get(job.id) is None
