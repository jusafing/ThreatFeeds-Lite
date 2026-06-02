"""
Unit tests for the standalone API client (scripts/api_client.py, prompts-053).

The script is imported by file path (it lives outside the backend package) and
its pure helpers are exercised with urlopen monkeypatched — no network.
"""
from __future__ import annotations

import importlib.util
import io
import json
import ssl
import urllib.request
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "api_client.py"


def _load_client():
    spec = importlib.util.spec_from_file_location("api_client", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


client = _load_client()


def test_build_url_joins_path_and_params():
    url = client.build_url("http://h:8000/", "/api/viewer/entries", {"source": "a b", "limit": 10})
    assert url.startswith("http://h:8000/api/viewer/entries?")
    assert "source=a+b" in url
    assert "limit=10" in url


def test_build_url_drops_none_params():
    url = client.build_url("http://h:8000", "/api/x", {"source": None, "limit": 5})
    assert url == "http://h:8000/api/x?limit=5"


def test_fetch_events_all_feeds_single_request(monkeypatch):
    calls: list[str] = []

    def fake_get(opener, url):
        calls.append(url)
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    rows = client.fetch_events(object(), "http://h:8000", "/api/viewer/entries", [], 1000)
    assert rows == [{"id": 1}, {"id": 2}]
    assert len(calls) == 1
    assert "limit=1000" in calls[0]
    assert "source=" not in calls[0]


def test_fetch_events_per_feed_merge_and_truncate(monkeypatch):
    def fake_get(opener, url):
        # Each feed returns two rows.
        return [{"u": url}, {"u": url}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    rows = client.fetch_events(object(), "http://h:8000", "/api/viewer/entries", ["a", "b", "c"], 3)
    # Truncated to max=3 across feeds, so the third feed is never queried.
    assert len(rows) == 3


def test_read_send_payload_from_data():
    ns = type("NS", (), {"file": None, "data": '[{"indicator": "1.2.3.4"}]'})()
    assert client._read_send_payload(ns) == [{"indicator": "1.2.3.4"}]


def test_read_send_payload_empty_raises():
    ns = type("NS", (), {"file": None, "data": "   "})()
    with pytest.raises(ValueError):
        client._read_send_payload(ns)


def test_cmd_send_posts_to_listener(monkeypatch, capsys):
    posted: dict = {}

    def fake_post(opener, url, payload):
        posted["url"] = url
        posted["payload"] = payload
        return {"inserted": 1}

    monkeypatch.setattr(client, "http_post_json", fake_post)
    ns = type("NS", (), {
        "url": "http://h:8000", "file": None, "data": '{"indicator": "x"}',
        "username": None, "password": None, "insecure": False,
    })()
    rc = client.cmd_send(ns)
    assert rc == 0
    assert posted["url"] == "http://h:8000/api/ingest/listener"
    assert posted["payload"] == {"indicator": "x"}
    assert json.loads(capsys.readouterr().out) == {"inserted": 1}


# ── auth (prompts-054) ──────────────────────────────────────────────────────────


def test_maybe_login_noop_without_username():
    calls: list = []
    ns = type("NS", (), {"username": None, "password": None, "url": "http://h:8000"})()
    # login is never invoked, so a sentinel opener that would explode is fine.
    client._maybe_login(object(), ns)
    assert calls == []  # nothing happened


def test_maybe_login_calls_login_with_credentials(monkeypatch):
    captured: dict = {}

    def fake_login(opener, base, username, password):
        captured["base"] = base
        captured["username"] = username
        captured["password"] = password
        return {"user": {"username": username}}

    monkeypatch.setattr(client, "login", fake_login)
    ns = type("NS", (), {
        "username": "test", "password": "secret", "url": "http://h:8001",
    })()
    client._maybe_login(object(), ns)
    assert captured == {"base": "http://h:8001", "username": "test", "password": "secret"}


def test_maybe_login_prompts_when_password_omitted(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(client.getpass, "getpass", lambda prompt="": "prompted-pw")
    monkeypatch.setattr(
        client, "login",
        lambda opener, base, username, password: captured.update(password=password),
    )
    ns = type("NS", (), {"username": "test", "password": None, "url": "http://h:8001"})()
    client._maybe_login(object(), ns)
    assert captured["password"] == "prompted-pw"


def test_login_posts_credentials_to_login_endpoint(monkeypatch):
    posted: dict = {}

    def fake_post(opener, url, payload):
        posted["url"] = url
        posted["payload"] = payload
        return {"user": {"username": "test"}}

    monkeypatch.setattr(client, "http_post_json", fake_post)
    client.login(object(), "http://h:8001", "test", "pw")
    assert posted["url"] == "http://h:8001/api/auth/login"
    assert posted["payload"] == {"username": "test", "password": "pw"}


def test_cmd_get_raw_logs_in_before_fetch(monkeypatch, capsys):
    order: list[str] = []
    monkeypatch.setattr(
        client, "login",
        lambda opener, base, username, password: order.append("login"),
    )

    def fake_fetch(opener, base, path, feeds, max_events, fields=None):
        order.append("fetch")
        return [{"id": 1}]

    monkeypatch.setattr(client, "fetch_events", fake_fetch)
    ns = type("NS", (), {
        "url": "http://h:8001", "feeds": [], "max": 10, "field": None,
        "username": "test", "password": "pw", "insecure": False,
    })()
    rc = client.cmd_get_raw(ns)
    assert rc == 0
    assert order == ["login", "fetch"]
    assert json.loads(capsys.readouterr().out) == [{"id": 1}]


def test_parser_accepts_global_auth_flags():
    parser = client.build_parser()
    args = parser.parse_args(["--username", "test", "--password", "pw", "get-raw"])
    assert args.username == "test"
    assert args.password == "pw"


def test_parser_requires_subcommand():
    parser = client.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_get_raw_defaults():
    parser = client.build_parser()
    args = parser.parse_args(["get-raw"])
    assert args.feeds == []
    assert args.max == 1000
    assert args.url == "http://127.0.0.1:8000"


# ── search + list-feeds (prompts-057) ────────────────────────────────────────────


def test_fetch_events_forwards_search_term_all_feeds(monkeypatch):
    calls: list[str] = []

    def fake_get(opener, url):
        calls.append(url)
        return [{"id": 1}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    rows = client.fetch_events(
        object(), "http://h:8000", "/api/viewer/entries", [], 50, search="npm",
    )
    assert rows == [{"id": 1}]
    assert len(calls) == 1
    assert "search=npm" in calls[0]
    assert "limit=50" in calls[0]


def test_fetch_events_forwards_search_term_per_feed(monkeypatch):
    calls: list[str] = []

    def fake_get(opener, url):
        calls.append(url)
        return [{"u": url}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    client.fetch_events(
        object(), "http://h:8000", "/api/viewer/entries", ["a", "b"], 10, search="npm",
    )
    assert len(calls) == 2
    assert all("search=npm" in url for url in calls)
    assert "source=a" in calls[0] and "source=b" in calls[1]


# ── field filters (issue_local_02) ───────────────────────────────────────────


def test_build_url_serialises_repeated_field_params():
    url = client.build_url(
        "http://h:8000", "/api/viewer/entries",
        {"field": ["severity=critical", "indicator_type=ipv4"]},
    )
    assert "field=severity%3Dcritical" in url
    assert "field=indicator_type%3Dipv4" in url


def test_build_url_drops_empty_field_list():
    url = client.build_url("http://h:8000", "/api/x", {"limit": 5, "field": []})
    assert url == "http://h:8000/api/x?limit=5"


def test_fetch_events_forwards_field_filters_all_feeds(monkeypatch):
    calls: list[str] = []

    def fake_get(opener, url):
        calls.append(url)
        return [{"id": 1}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    client.fetch_events(
        object(), "http://h:8000", "/api/normalizer/entries", [], 50,
        fields=["cve_id=CVE-2026-0001"],
    )
    assert len(calls) == 1
    assert "field=cve_id%3DCVE-2026-0001" in calls[0]


def test_fetch_events_forwards_field_filters_per_feed(monkeypatch):
    calls: list[str] = []

    def fake_get(opener, url):
        calls.append(url)
        return [{"u": url}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    client.fetch_events(
        object(), "http://h:8000", "/api/viewer/entries", ["a", "b"], 10,
        fields=["severity=critical"],
    )
    assert len(calls) == 2
    assert all("field=severity%3Dcritical" in url for url in calls)


def test_parser_get_raw_accepts_repeated_field():
    parser = client.build_parser()
    args = parser.parse_args(
        ["get-raw", "--field", "severity=critical", "--field", "indicator_type=ipv4"]
    )
    assert args.field == ["severity=critical", "indicator_type=ipv4"]


def test_parser_get_normalized_field_defaults_none():
    parser = client.build_parser()
    args = parser.parse_args(["get-normalized"])
    assert args.field is None


def test_cmd_search_raw_hits_viewer_entries(monkeypatch, capsys):
    captured: dict = {}

    def fake_fetch(opener, base, path, feeds, max_events, search=None, fields=None):
        captured.update(path=path, feeds=feeds, max=max_events, search=search)
        return [{"id": 7}]

    monkeypatch.setattr(client, "fetch_events", fake_fetch)
    ns = type("NS", (), {
        "url": "http://h:8000", "feeds": [], "max": 20, "type": "raw",
        "query": "npm", "field": None, "username": None, "password": None,
        "insecure": False,
    })()
    rc = client.cmd_search(ns)
    assert rc == 0
    assert captured["path"] == "/api/viewer/entries"
    assert captured["search"] == "npm"
    assert captured["max"] == 20
    assert json.loads(capsys.readouterr().out) == [{"id": 7}]


def test_cmd_search_normalized_hits_normalizer_entries(monkeypatch, capsys):
    captured: dict = {}

    def fake_fetch(opener, base, path, feeds, max_events, search=None, fields=None):
        captured["path"] = path
        return []

    monkeypatch.setattr(client, "fetch_events", fake_fetch)
    ns = type("NS", (), {
        "url": "http://h:8000", "feeds": [], "max": 1000, "type": "normalized",
        "query": "npm", "field": None, "username": None, "password": None,
        "insecure": False,
    })()
    assert client.cmd_search(ns) == 0
    assert captured["path"] == "/api/normalizer/entries"


def test_cmd_list_feeds_hits_summary(monkeypatch, capsys):
    captured: dict = {}

    def fake_get(opener, url):
        captured["url"] = url
        return [{"source": "a", "count": 3}]

    monkeypatch.setattr(client, "http_get_json", fake_get)
    ns = type("NS", (), {
        "url": "http://h:8000", "type": "raw", "username": None, "password": None,
        "insecure": False,
    })()
    rc = client.cmd_list_feeds(ns)
    assert rc == 0
    assert captured["url"] == "http://h:8000/api/viewer/summary"
    assert json.loads(capsys.readouterr().out) == [{"source": "a", "count": 3}]


def test_cmd_list_feeds_normalized_summary(monkeypatch, capsys):
    captured: dict = {}
    monkeypatch.setattr(
        client, "http_get_json",
        lambda opener, url: captured.update(url=url) or [],
    )
    ns = type("NS", (), {
        "url": "http://h:8000", "type": "normalized", "username": None, "password": None,
        "insecure": False,
    })()
    assert client.cmd_list_feeds(ns) == 0
    assert captured["url"] == "http://h:8000/api/normalizer/summary"


def test_parser_search_defaults():
    parser = client.build_parser()
    args = parser.parse_args(["search", "npm"])
    assert args.query == "npm"
    assert args.feeds == []
    assert args.type == "raw"
    assert args.max == 1000


def test_parser_list_feeds_defaults():
    parser = client.build_parser()
    args = parser.parse_args(["list-feeds"])
    assert args.type == "raw"


# ── query subcommand (prompts-064) ──────────────────────────────────────────


def test_parser_query_defaults():
    parser = client.build_parser()
    args = parser.parse_args(["query", "critical CVEs from 2026"])
    assert args.question == "critical CVEs from 2026"
    assert args.type is None
    assert args.source is None
    assert args.max is None
    assert args.func is client.cmd_query


def test_parser_query_overrides():
    parser = client.build_parser()
    args = parser.parse_args([
        "query", "log4j hits", "--type", "raw", "--source", "feedA", "--max", "7",
    ])
    assert args.type == "raw"
    assert args.source == "feedA"
    assert args.max == 7


def test_cmd_query_posts_to_nl_endpoint(monkeypatch, capsys):
    posted: dict = {}

    def fake_post(opener, url, payload):
        posted["url"] = url
        posted["payload"] = payload
        return {"count": 0, "results": [], "dataset": "raw"}

    monkeypatch.setattr(client, "http_post_json", fake_post)
    ns = type("NS", (), {
        "url": "http://h:8000", "question": "any log4j?",
        "type": "raw", "source": "feedA", "max": 5,
        "username": None, "password": None, "insecure": False,
    })()
    rc = client.cmd_query(ns)
    assert rc == 0
    assert posted["url"] == "http://h:8000/api/query/nl"
    assert posted["payload"] == {
        "question": "any log4j?", "dataset": "raw", "source": "feedA", "limit": 5,
    }
    assert json.loads(capsys.readouterr().out)["dataset"] == "raw"


def test_cmd_query_omits_unset_optionals(monkeypatch):
    posted: dict = {}

    def fake_post(opener, url, payload):
        posted["payload"] = payload
        return {"count": 0, "results": []}

    monkeypatch.setattr(client, "http_post_json", fake_post)
    ns = type("NS", (), {
        "url": "http://h:8000", "question": "q",
        "type": None, "source": None, "max": None,
        "username": None, "password": None, "insecure": False,
    })()
    assert client.cmd_query(ns) == 0
    assert posted["payload"] == {"question": "q"}


# ── TLS verification skip (issue_local_003) ──────────────────────────────────

def test_parser_insecure_defaults_false():
    parser = client.build_parser()
    args = parser.parse_args(["get-raw"])
    assert args.insecure is False


def test_parser_insecure_long_flag():
    parser = client.build_parser()
    args = parser.parse_args(["--insecure", "get-raw"])
    assert args.insecure is True


def test_parser_insecure_short_flag():
    parser = client.build_parser()
    args = parser.parse_args(["-k", "get-raw"])
    assert args.insecure is True


def _https_handler(opener):
    for h in opener.handlers:
        if isinstance(h, urllib.request.HTTPSHandler):
            return h
    return None


def test_build_opener_default_verifies_tls():
    """The default opener installs no custom unverified HTTPS context."""
    opener = client.build_opener()
    handler = _https_handler(opener)
    # Either no explicit HTTPSHandler, or one without a CERT_NONE context.
    if handler is not None:
        ctx = getattr(handler, "_context", None)
        if ctx is not None:
            assert ctx.verify_mode != ssl.CERT_NONE


def test_build_opener_insecure_disables_tls_verification():
    opener = client.build_opener(insecure=True)
    handler = _https_handler(opener)
    assert handler is not None, "insecure opener must install an HTTPSHandler"
    ctx = handler._context
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
