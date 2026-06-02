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
    def __init__(self, status_code: int, *, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.reason_phrase = "Bad Request" if status_code == 400 else ""
        self.request = httpx.Request("POST", "https://x.example/in")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self  # type: ignore[arg-type]
            )


class _FakeClient:
    """Records every POST and returns a configured response (or raises)."""

    def __init__(
        self,
        status_code: int = 200,
        raise_exc: Exception | None = None,
        *,
        text: str = "",
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.text = text
        self.headers = headers
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeResponse(self.status_code, text=self.text, headers=self.headers)


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


@pytest.mark.asyncio
async def test_discord_format_sends_content():
    w = await store.create_watcher(
        _defn(name="Disc W", publish_target="webhook", webhook_format="discord",
              webhook_url="https://discord.com/api/webhooks/1/abc")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=204)
    with _patch_client(fake):
        res = await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert res == {"delivered": 1, "failed": 0}
    body = fake.calls[0]["json"]
    # Summary content plus an embed code block carrying every event field.
    assert set(body.keys()) == {"content", "embeds"}
    assert "Disc W" in body["content"]
    desc = body["embeds"][0]["description"]
    assert "cve_id: CVE-2024-1" in desc
    assert "id: 1" in desc


@pytest.mark.asyncio
async def test_slack_format_includes_all_event_fields():
    w = await store.create_watcher(
        _defn(name="Slack F", publish_target="webhook", webhook_format="slack",
              webhook_url="https://hooks.slack.com/services/x")
    )
    await store.record_triggers(
        w["id"],
        [{"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a",
          "event": {"id": 1, "cve_id": "CVE-2024-9", "severity": "high"}}],
        max_events=100,
    )
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    text = fake.calls[0]["json"]["text"]
    assert "cve_id: CVE-2024-9" in text
    assert "severity: high" in text


@pytest.mark.asyncio
async def test_teams_format_lists_event_fields_as_facts():
    w = await store.create_watcher(
        _defn(name="Teams F", publish_target="webhook", webhook_format="teams",
              webhook_url="https://acme.webhook.office.com/x")
    )
    await store.record_triggers(
        w["id"],
        [{"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a",
          "event": {"id": 1, "cve_id": "CVE-2024-7"}}],
        max_events=100,
    )
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    body = fake.calls[0]["json"]
    facts = {f["name"]: f["value"] for f in body["sections"][0]["facts"]}
    assert facts["cve_id"] == "CVE-2024-7"


@pytest.mark.asyncio
async def test_slack_format_sends_text():
    w = await store.create_watcher(
        _defn(name="Slack W", publish_target="webhook", webhook_format="slack",
              webhook_url="https://hooks.slack.com/services/x")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    body = fake.calls[0]["json"]
    assert set(body.keys()) == {"text"}
    assert "Slack W" in body["text"]


@pytest.mark.asyncio
async def test_teams_format_sends_messagecard():
    w = await store.create_watcher(
        _defn(name="Teams W", publish_target="webhook", webhook_format="teams",
              webhook_url="https://acme.webhook.office.com/x")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(status_code=200)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    body = fake.calls[0]["json"]
    assert body["@type"] == "MessageCard"
    assert "Teams W" in body["text"]


@pytest.mark.asyncio
async def test_discord_content_is_truncated():
    long_title = "X" * 5000
    w = await store.create_watcher(
        _defn(name="Long W", publish_target="webhook", webhook_format="discord",
              webhook_url="https://discord.com/api/webhooks/1/abc")
    )
    await store.record_triggers(
        w["id"],
        [{"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a",
          "event": {"id": 1, "title": long_title}}],
        max_events=100,
    )
    fake = _FakeClient(status_code=204)
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    assert len(fake.calls[0]["json"]["content"]) <= 2000


@pytest.mark.asyncio
async def test_http_error_records_rich_detail():
    w = await store.create_watcher(
        _defn(name="Detail W", publish_target="webhook", webhook_format="discord",
              webhook_url="https://discord.com/api/webhooks/1/abc")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(
        status_code=400,
        text='{"message": "Cannot send an empty message", "code": 50006}',
        headers={"content-type": "application/json"},
    )
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    pending = await store.list_pending_deliveries(w["id"])
    detail = pending[0]["delivery_detail"]
    assert detail["status"] == 400
    assert detail["url"] == "https://discord.com/api/webhooks/1/abc"
    assert "Cannot send an empty message" in detail["body"]
    assert detail["headers"]["content-type"] == "application/json"
    # And the watcher summary surfaces the latest error detail.
    got = await store.get_watcher(w["id"])
    assert got["last_delivery_detail"]["status"] == 400


@pytest.mark.asyncio
async def test_connection_error_records_detail_without_response():
    w = await store.create_watcher(
        _defn(name="ConnD W", publish_target="webhook",
              webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    fake = _FakeClient(raise_exc=httpx.ConnectError("refused"))
    with _patch_client(fake):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    pending = await store.list_pending_deliveries(w["id"])
    detail = pending[0]["delivery_detail"]
    assert detail["error_type"] == "ConnectError"
    assert "status" not in detail
    assert detail["url"] == "https://x.example/in"


@pytest.mark.asyncio
async def test_success_clears_prior_error_detail():
    w = await store.create_watcher(
        _defn(name="Clear W", publish_target="webhook",
              webhook_url="https://x.example/in")
    )
    await _seed(w["id"], n=1)
    with _patch_client(_FakeClient(status_code=500, text="boom")):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    with _patch_client(_FakeClient(status_code=200)):
        await delivery.deliver_pending(await store.get_watcher(w["id"]))
    events = await store.list_events(w["id"])
    assert events[0]["delivery_status"] == "ok"
    assert events[0]["delivery_detail"] is None
