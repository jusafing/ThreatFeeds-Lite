"""Tests for backend.watchers.delivery (issue_local_007).

Covers outbound publishing of triggered watcher events: payload shape per
target (webhook envelope vs bare listener JSON), success/failure recording,
auth-header injection, and the local/no-URL no-op. The httpx client is replaced
with a fake so no network is touched.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from backend.db import watchers as store
from backend.watchers import delivery


@pytest.fixture(autouse=True)
def _isolate_watchers_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", tmp_path / "watchers.db")
    yield


def _defn(**over):
    base = {
        "name": "Deliverable",
        "severity": "critical",
        "dataset": "normalized",
        "feeds": [],
        "conditions": [{"field": "cve_id", "value": "*", "match_type": "wildcard"}],
        "mode": "realtime",
        "format": "json",
        "max_feed_events": 100,
        "enabled": True,
    }
    base.update(over)
    return base


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://x.example/in")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self  # type: ignore[arg-type]
            )


class _FakeClient:
    """Records every POST and returns a configured response (or raises)."""

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeResponse(self.status_code)


def _patch_client(fake: _FakeClient):
    return patch(
        "backend.watchers.delivery.httpx.AsyncClient",
        lambda *a, **k: fake,
    )


async def _seed(wid: str, n: int = 1) -> None:
    triggers = [
        {"dataset": "normalized", "source_entry_id": i, "source_name": "feed-a",
         "event": {"id": i, "cve_id": f"CVE-2024-{i}"}}
        for i in range(1, n + 1)
    ]
    await store.record_triggers(wid, triggers, max_events=100)


@pytest.mark.asyncio
async def test_local_target_is_a_noop():
    w = await store.create_watcher(_defn(name="Local W", publish_target="local"))
    await _seed(w["id"])
    fake = _FakeClient()
    with _patch_client(fake):
        res = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res == {"delivered": 0, "failed": 0}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_webhook_sends_envelope_and_records_ok():
    w = await store.create_watcher(
        _defn(name="Hook W", publish_target="webhook", webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        res = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res == {"delivered": 1, "failed": 0}
    assert len(fake.calls) == 1
    body = fake.calls[0]["json"]
    # Envelope wraps the event with watcher metadata.
    assert body["watcher_id"] == w["id"]
    assert body["dataset"] == "normalized"
    assert body["source_name"] == "feed-a"
    assert body["event"]["cve_id"] == "CVE-2024-1"
    # Event row now marked ok and no longer pending.
    assert await store.list_pending_deliveries(w["id"]) == []


@pytest.mark.asyncio
async def test_http_target_sends_bare_event():
    w = await store.create_watcher(
        _defn(name="Http W", publish_target="http", webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    body = fake.calls[0]["json"]
    # Bare event JSON — listener-compatible, no envelope keys.
    assert "watcher_id" not in body
    assert body["cve_id"] == "CVE-2024-1"


@pytest.mark.asyncio
async def test_auth_header_is_injected():
    w = await store.create_watcher(
        _defn(name="Auth W", publish_target="webhook", webhook_url="https://x.example/in",
              auth_header="Authorization", auth_value="Bearer secret")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_non_2xx_records_error_and_stays_pending():
    w = await store.create_watcher(
        _defn(name="Err W", publish_target="webhook", webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=500)
    with _patch_client(fake):
        res = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res == {"delivered": 0, "failed": 1}
    pending = await store.list_pending_deliveries(w["id"])
    assert len(pending) == 1  # retried next pass
    got = await store.get_watcher(w["id"])
    assert got["delivery_error_count"] == 1
    assert "HTTP 500" in (got["last_delivery_error"] or "")


@pytest.mark.asyncio
async def test_connection_error_is_caught_and_recorded():
    w = await store.create_watcher(
        _defn(name="Conn W", publish_target="webhook", webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    exc = httpx.ConnectError("refused")
    fake = _FakeClient(raise_exc=exc)
    with _patch_client(fake):
        res = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res == {"delivered": 0, "failed": 1}
    got = await store.get_watcher(w["id"])
    assert got["delivery_error_count"] == 1


@pytest.mark.asyncio
async def test_mixed_batch_counts_both_outcomes():
    w = await store.create_watcher(
        _defn(name="Mixed W", publish_target="webhook", webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=3)

    # Fail every POST on the first pass.
    fail = _FakeClient(status_code=503)
    with _patch_client(fail):
        res1 = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res1 == {"delivered": 0, "failed": 3}

    # All succeed on the retry pass.
    ok = _FakeClient(status_code=200)
    with _patch_client(ok):
        res2 = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res2 == {"delivered": 3, "failed": 0}
    assert await store.list_pending_deliveries(w["id"]) == []
