"""
Public per-watcher feed (issue_local_006).

Serves a watcher's most recent triggered events at ``/feed/watcher/<id>/`` in
the watcher's configured format (JSON / CSV / XML-RSS). This endpoint is PUBLIC:
it lives outside the ``/api/`` namespace so the auth middleware (which only
guards ``/api/``) leaves it reachable without a session — feed clients connect
to it like any other syndication feed.

Output is rendered on the fly from the stored ``watcher_events`` table (capped at
the watcher's ``max_feed_events``); nothing is written to disk. Each rendered
event carries the trigger timestamp, the watcher name, and all event fields.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.db import watchers as store

router = APIRouter(prefix="/feed", tags=["feed"])


def _flatten_event(name: str, ev_row: dict[str, Any]) -> dict[str, Any]:
    """Compose the public event shape: trigger metadata + all event fields."""
    event = dict(ev_row.get("event") or {})
    out: dict[str, Any] = {
        "triggered_at": ev_row.get("triggered_at"),
        "watcher": name,
        "source_name": ev_row.get("source_name"),
    }
    # Event fields override nothing above; keep watcher metadata authoritative.
    for k, v in event.items():
        if k not in out:
            out[k] = v
    return out


def _render_json(name: str, rows: list[dict[str, Any]]) -> Response:
    payload = {
        "watcher": name,
        "count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [_flatten_event(name, r) for r in rows],
    }
    return JSONResponse(content=payload)


def _render_csv(name: str, rows: list[dict[str, Any]]) -> Response:
    events = [_flatten_event(name, r) for r in rows]
    # Stable column order: metadata first, then the union of event keys sorted.
    lead = ["triggered_at", "watcher", "source_name"]
    extra: list[str] = []
    seen = set(lead)
    for ev in events:
        for k in ev:
            if k not in seen:
                seen.add(k)
                extra.append(k)
    columns = lead + sorted(extra)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for ev in events:
        writer.writerow({k: _csv_cell(ev.get(k)) for k in columns})
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _render_xml(name: str, rows: list[dict[str, Any]], request: Request) -> Response:
    events = [_flatten_event(name, r) for r in rows]
    self_url = str(request.url)
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{escape(name)} — ThreatFeeds Lite watcher</title>",
        f"    <link>{escape(self_url)}</link>",
        f"    <description>Triggered events for watcher {escape(name)}</description>",
        f"    <lastBuildDate>{escape(datetime.now(timezone.utc).isoformat())}</lastBuildDate>",
    ]
    for ev in events:
        title = ev.get("indicator") or ev.get("title") or ev.get("cve_id") or name
        guid = f"{ev.get('source_name') or ''}:{ev.get('id') or ''}"
        body = json.dumps(ev, ensure_ascii=False, default=str)
        parts.extend([
            "    <item>",
            f"      <title>{escape(str(title))}</title>",
            f"      <guid isPermaLink=\"false\">{escape(guid)}</guid>",
            f"      <pubDate>{escape(str(ev.get('triggered_at') or ''))}</pubDate>",
            f"      <description>{escape(body)}</description>",
            "    </item>",
        ])
    parts.extend(["  </channel>", "</rss>"])
    return Response(content="\n".join(parts), media_type="application/rss+xml; charset=utf-8")


@router.get("/watcher/{watcher_id}/")
@router.get("/watcher/{watcher_id}")
async def watcher_feed(watcher_id: str, request: Request) -> Response:
    """Public feed: latest N triggered events for a watcher in its format."""
    watcher = await store.get_watcher(watcher_id)
    if watcher is None:
        raise HTTPException(status_code=404, detail="watcher not found")
    limit = int(watcher.get("max_feed_events", 10) or 10)
    rows = await store.list_events(watcher_id, limit=limit)
    fmt = str(watcher.get("format", "json") or "json").lower()
    name = watcher.get("name", watcher_id)
    if fmt == "csv":
        return _render_csv(name, rows)
    if fmt == "xml":
        return _render_xml(name, rows, request)
    return _render_json(name, rows)
