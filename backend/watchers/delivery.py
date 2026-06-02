"""
Watcher delivery — outbound publishing of triggered events (issue_local_007).

A watcher may publish matching events to a remote target in addition to (and
independently of) the public ``/feed/watcher/<id>/`` URL:

  * ``webhook`` — POST a JSON payload per event. The exact shape depends on the
    watcher's ``webhook_format``:
        - ``generic`` — the ThreatFeeds envelope
              {"watcher", "watcher_id", "dataset", "source_name",
               "triggered_at", "event": {...}}
        - ``discord`` — Discord webhook ``{"content": "<summary>"}``
        - ``slack``   — Slack / Mattermost incoming webhook ``{"text": "<summary>"}``
        - ``teams``   — Microsoft Teams legacy MessageCard connector payload

  * ``http`` — POST the *bare* event JSON object per event, i.e. the same shape
    the local ``/api/ingest/listener`` endpoint accepts, so one ThreatFeeds-Lite
    instance can feed another.

Delivery is best-effort and per-event: each pending event is POSTed on its own,
its outcome (``ok`` / ``error`` + message + a rich detail blob) recorded on the
event row, so the UI can show a per-event Delivery column and a per-watcher
error card. Failed events stay pending and are retried on the next trigger /
manual run.

Security note (SSRF): the target URL is admin-configured and only validated to
be a well-formed http(s) URL — internal/LAN hosts are intentionally allowed so
operators can push to private listeners. Do not expose watcher configuration to
non-admin roles.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.db import watchers as store

logger = logging.getLogger("backend.watchers")

# Per-request timeout (seconds) for an outbound delivery POST.
_DELIVERY_TIMEOUT = 15.0
# Upper bound on events delivered in a single pass (also bounded by retention).
_MAX_DELIVER_PER_PASS = 500
# Discord hard-caps message content at 2000 characters.
_DISCORD_CONTENT_MAX = 2000
# Cap on the response body captured in an error detail blob.
_DETAIL_BODY_MAX = 2000


def _summarize(watcher: dict[str, Any], event_row: dict[str, Any]) -> str:
    """Build a one-message human summary for chat-style webhook formats."""
    event = event_row.get("event") or {}
    name = watcher.get("name") or watcher.get("id") or "watcher"
    title = (
        event.get("title")
        or event.get("name")
        or event.get("id")
        or event.get("cve")
        or ""
    )
    parts = [f"[{name}]"]
    if title:
        parts.append(str(title))
    severity = event.get("severity") or event.get("cvss")
    if severity:
        parts.append(f"(severity: {severity})")
    link = event.get("link") or event.get("url") or event.get("reference")
    if link:
        parts.append(str(link))
    summary = " ".join(parts).strip()
    return summary or f"[{name}] new event"


def _build_payload(watcher: dict[str, Any], event_row: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON body to POST for one event, per the watcher's target.

    The ``http`` target sends the bare event. The ``webhook`` target sends a
    payload shaped for the watcher's ``webhook_format``.
    """
    event = event_row.get("event") or {}
    target = str(watcher.get("publish_target") or "local")
    if target == "http":
        # Bare event JSON — the listener-compatible shape.
        return event

    fmt = str(watcher.get("webhook_format") or "generic")
    if fmt == "discord":
        return {"content": _summarize(watcher, event_row)[:_DISCORD_CONTENT_MAX]}
    if fmt == "slack":
        # Slack and Mattermost both accept {"text": ...} on incoming webhooks.
        return {"text": _summarize(watcher, event_row)}
    if fmt == "teams":
        # Legacy Office 365 connector MessageCard. Newer Teams Workflows expect
        # an Adaptive Card; this best-effort card works with classic connectors.
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": _summarize(watcher, event_row),
            "text": _summarize(watcher, event_row),
        }

    # Default generic envelope.
    return {
        "watcher": watcher.get("name") or watcher.get("id"),
        "watcher_id": watcher.get("id"),
        "dataset": event_row.get("dataset"),
        "source_name": event_row.get("source_name"),
        "triggered_at": event_row.get("triggered_at"),
        "event": event,
    }


def _headers(watcher: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    name = (watcher.get("auth_header") or "").strip()
    value = watcher.get("auth_value")
    if name and value:
        headers[name] = str(value)
    return headers


async def deliver_pending(watcher: dict[str, Any]) -> dict[str, int]:
    """Deliver all pending events for ``watcher`` to its configured target.

    Returns ``{"delivered": n_ok, "failed": n_err}``. A no-op (zeros) when the
    target is ``local`` or no URL is configured. Never raises — every per-event
    failure is logged and recorded on the event row.
    """
    target = str(watcher.get("publish_target") or "local")
    url = (watcher.get("webhook_url") or "").strip()
    wid = watcher.get("id")
    if target == "local" or not url:
        return {"delivered": 0, "failed": 0}

    try:
        pending = await store.list_pending_deliveries(wid, limit=_MAX_DELIVER_PER_PASS)
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("watcher %s: could not list pending deliveries: %s", wid, exc)
        return {"delivered": 0, "failed": 0}
    if not pending:
        return {"delivered": 0, "failed": 0}

    headers = _headers(watcher)
    ok = 0
    failed = 0
    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT) as client:
        for row in pending:
            event_id = int(row.get("id", 0))
            payload = _build_payload(watcher, row)
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            except Exception as exc:
                msg = _error_text(exc)
                detail = _error_detail(exc, url)
                logger.warning(
                    "watcher %s: delivery failed for event %s -> %s: %s",
                    wid, event_id, url, msg,
                )
                await store.update_delivery_status(event_id, "error", msg, detail)
                failed += 1
                continue
            await store.update_delivery_status(event_id, "ok", None)
            ok += 1
    if ok or failed:
        logger.info(
            "watcher %s delivery to %s (%s): %d ok, %d failed",
            wid, url, target, ok, failed,
        )
    return {"delivered": ok, "failed": failed}


def _error_text(exc: Exception) -> str:
    """Compact, human-readable error string for a failed delivery."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.RequestError):
        return f"connection error: {exc}"
    return str(exc)[:500] or exc.__class__.__name__


def _error_detail(exc: Exception, url: str) -> dict[str, Any]:
    """Capture a structured, UI-inspectable detail blob for a failed delivery.

    For HTTP status errors we include the response status / reason / headers /
    body so an operator can see *why* a receiver (e.g. Discord) rejected the
    payload. For connection/timeout errors we record the error class + message.
    """
    detail: dict[str, Any] = {
        "url": url,
        "error_type": exc.__class__.__name__,
        "message": _error_text(exc),
    }
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            detail["status"] = int(resp.status_code)
        except (TypeError, ValueError):  # pragma: no cover — defensive
            detail["status"] = None
        detail["reason"] = getattr(resp, "reason_phrase", None) or None
        try:
            detail["headers"] = {str(k): str(v) for k, v in dict(resp.headers).items()}
        except Exception:  # pragma: no cover — defensive
            detail["headers"] = None
        try:
            body = resp.text
        except Exception:  # pragma: no cover — defensive
            body = None
        if body:
            detail["body"] = body[:_DETAIL_BODY_MAX]
    return detail

