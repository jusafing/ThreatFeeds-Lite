#!/usr/bin/env python3
"""
ThreatFeeds Lite — standalone API client.

A dependency-free command-line client for the ThreatFeeds Lite HTTP API. It
uses only the Python standard library (urllib/json/argparse), so it runs under
any Python 3 without the project virtualenv.

Supported operations (query types):

  get-raw          Fetch the raw events table        -> JSON string on stdout
  get-normalized   Fetch the normalized data table   -> JSON string on stdout
  send             POST a generic JSON to the listener (indexed into a feed
                   named after the sending user, or "Received Feed <epoch>"
                   when auth is off)                  -> JSON response on stdout
  search           Full-text search the raw/normalized table (?search=)
                                                      -> JSON string on stdout
  list-feeds       List available feeds with per-source entry counts
                                                      -> JSON string on stdout

Both get operations accept an optional list of feeds (no feeds = all feeds) and
a maximum number of events (default 1000).

Authentication: when the server has auth enabled, pass --username (and
--password, or you will be prompted). The client logs in once and reuses the
session cookie for the request. `send` requires an 'admin' or 'sender' account.

Examples:

  # All raw events (up to 1000) from a local instance
  scripts/api_client.py get-raw

  # Up to 50 normalized events from two named feeds, against a custom host
  scripts/api_client.py --url http://10.0.0.5:8000 get-normalized feedA feedB --max 50

  # Against an auth-enabled server (prompts for the password)
  scripts/api_client.py --url http://10.0.0.5:8001 --username test get-raw

  # Send events to the listener (from a file, inline, or stdin)
  scripts/api_client.py send --file events.json
  scripts/api_client.py send --data '[{"indicator": "1.2.3.4"}]'
  cat events.json | scripts/api_client.py send

  # Search the raw table, and list available feeds
  scripts/api_client.py search "npm" --type raw --max 20
  scripts/api_client.py list-feeds

  # Filter raw or normalized rows by exact column value (repeatable --field)
  scripts/api_client.py get-raw --field severity=critical --field indicator_type=ipv4
  scripts/api_client.py get-normalized --field cve_id=CVE-2026-0001
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_MAX = 1000
_TIMEOUT = 30

# Shared help for the repeatable column-filter flag. The server validates each
# NAME against the table schema and silently ignores unknown columns.
_FIELD_HELP = (
    "Filter by an exact column value as NAME=VALUE (repeatable, e.g. "
    "--field severity=critical --field indicator_type=ipv4). Unknown columns "
    "are ignored by the server."
)


def build_opener() -> urllib.request.OpenerDirector:
    """Return an opener with an in-memory cookie jar (carries the session)."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def build_url(base: str, path: str, params: dict[str, object] | None = None) -> str:
    """Join a base URL, an API path, and optional query parameters.

    A list value is serialised as a repeated key (``?field=a=b&field=c=d``) via
    ``doseq=True`` — used by the ``--field`` column filters. ``None`` values and
    empty lists are dropped.
    """
    url = base.rstrip("/") + path
    if params:
        query = {
            k: v for k, v in params.items()
            if v is not None and v != []
        }
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
    return url


def http_get_json(opener: urllib.request.OpenerDirector, url: str) -> object:
    """GET a URL and decode the JSON response."""
    req = urllib.request.Request(url, method="GET")
    with opener.open(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (trusted local API)
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(opener: urllib.request.OpenerDirector, url: str, payload: object) -> object:
    """POST a JSON payload to a URL and decode the JSON response."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (trusted local API)
        return json.loads(resp.read().decode("utf-8"))


def login(opener: urllib.request.OpenerDirector, base: str, username: str, password: str) -> object:
    """Authenticate against /api/auth/login; the session cookie is stored in the jar."""
    return http_post_json(
        opener, build_url(base, "/api/auth/login"),
        {"username": username, "password": password},
    )


def fetch_events(
    opener: urllib.request.OpenerDirector, base: str, path: str,
    feeds: list[str], max_events: int, search: str | None = None,
    fields: list[str] | None = None,
) -> list[object]:
    """
    Fetch events from a read endpoint.

    With no feeds, issues a single request for all feeds (server-merged). With a
    list of feeds, issues one request per feed (?source=<feed>) and merges the
    results. The combined output is truncated to ``max_events``. An optional
    ``search`` term is forwarded as the ``?search=`` full-text query parameter.
    Optional ``fields`` ('name=value' strings) are forwarded as repeated
    ``?field=`` column filters (validated server-side against the table schema).
    """
    results: list[object] = []
    if not feeds:
        url = build_url(
            base, path,
            {"limit": max_events, "search": search, "field": fields},
        )
        results = list(http_get_json(opener, url))
    else:
        for feed in feeds:
            remaining = max_events - len(results)
            if remaining <= 0:
                break
            url = build_url(
                base, path,
                {"source": feed, "limit": remaining, "search": search,
                 "field": fields},
            )
            results.extend(http_get_json(opener, url))
    return results[:max_events]


# Read-table endpoints keyed by data type (raw events vs normalized data).
ENTRIES_PATHS = {
    "raw": "/api/viewer/entries",
    "normalized": "/api/normalizer/entries",
}
# Per-source summary endpoints (the closest thing to a "list of feeds").
SUMMARY_PATHS = {
    "raw": "/api/viewer/summary",
    "normalized": "/api/normalizer/summary",
}


def _read_send_payload(args: argparse.Namespace) -> object:
    """Resolve the JSON payload for `send` from --file, --data, or stdin."""
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            raw = fh.read()
    elif args.data is not None:
        raw = args.data
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no JSON payload provided (use --file, --data, or stdin)")
    return json.loads(raw)


def _maybe_login(opener: urllib.request.OpenerDirector, args: argparse.Namespace) -> None:
    """Log in if a username was supplied (prompting for a password if needed)."""
    if not args.username:
        return
    password = args.password
    if password is None:
        password = getpass.getpass(f"Password for {args.username}: ")
    login(opener, args.url, args.username, password)


def cmd_get_raw(args: argparse.Namespace) -> int:
    opener = build_opener()
    _maybe_login(opener, args)
    events = fetch_events(
        opener, args.url, "/api/viewer/entries", args.feeds, args.max,
        fields=args.field,
    )
    print(json.dumps(events))
    return 0


def cmd_get_normalized(args: argparse.Namespace) -> int:
    opener = build_opener()
    _maybe_login(opener, args)
    events = fetch_events(
        opener, args.url, "/api/normalizer/entries", args.feeds, args.max,
        fields=args.field,
    )
    print(json.dumps(events))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    payload = _read_send_payload(args)
    opener = build_opener()
    _maybe_login(opener, args)
    response = http_post_json(opener, build_url(args.url, "/api/ingest/listener"), payload)
    print(json.dumps(response))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Full-text search the raw or normalized table via the ``?search=`` param."""
    opener = build_opener()
    _maybe_login(opener, args)
    path = ENTRIES_PATHS[args.type]
    events = fetch_events(
        opener, args.url, path, args.feeds, args.max,
        search=args.query, fields=args.field,
    )
    print(json.dumps(events))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Natural-language query: the server's LLM translates the question into a
    constrained filter, runs it against the local DB, and returns the rows."""
    opener = build_opener()
    _maybe_login(opener, args)
    body: dict[str, object] = {"question": args.question}
    if args.type:
        body["dataset"] = args.type
    if args.source:
        body["source"] = args.source
    if args.max:
        body["limit"] = args.max
    response = http_post_json(opener, build_url(args.url, "/api/query/nl"), body)
    print(json.dumps(response))
    return 0


def cmd_list_feeds(args: argparse.Namespace) -> int:
    """List available feeds (per-source entry counts) from the summary endpoint."""
    opener = build_opener()
    _maybe_login(opener, args)
    summary = http_get_json(opener, build_url(args.url, SUMMARY_PATHS[args.type]))
    print(json.dumps(summary))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="api_client.py",
        description="Standalone command-line client for the ThreatFeeds Lite API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  api_client.py get-raw\n"
            "  api_client.py get-normalized feedA feedB --max 50\n"
            "  api_client.py --url http://host:8001 --username test get-raw\n"
            "  api_client.py --url http://host:8000 send --file events.json\n"
            "  cat events.json | api_client.py send\n"
            "  api_client.py search \"npm\" --type raw --max 20\n"
            "  api_client.py get-raw --field severity=critical --field indicator_type=ipv4\n"
            "  api_client.py get-normalized --field cve_id=CVE-2026-0001\n"
            "  api_client.py query \"critical CVEs from 2026 affecting nginx\"\n"
            "  api_client.py list-feeds\n"
        ),
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Base API endpoint URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--username", "-u", default=None,
        help="Username for auth-enabled servers (triggers a login)",
    )
    parser.add_argument(
        "--password", "-p", default=None,
        help="Password for --username (prompted securely if omitted)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_raw = sub.add_parser("get-raw", help="Get the raw events table as a JSON string")
    p_raw.add_argument("feeds", nargs="*", help="Feed names to fetch (omit for all feeds)")
    p_raw.add_argument(
        "--max", type=int, default=DEFAULT_MAX,
        help=f"Maximum number of events (default: {DEFAULT_MAX})",
    )
    p_raw.add_argument(
        "--field", action="append", metavar="NAME=VALUE", default=None,
        help=_FIELD_HELP,
    )
    p_raw.set_defaults(func=cmd_get_raw)

    p_norm = sub.add_parser("get-normalized", help="Get the normalized data table as a JSON string")
    p_norm.add_argument("feeds", nargs="*", help="Feed names to fetch (omit for all feeds)")
    p_norm.add_argument(
        "--max", type=int, default=DEFAULT_MAX,
        help=f"Maximum number of events (default: {DEFAULT_MAX})",
    )
    p_norm.add_argument(
        "--field", action="append", metavar="NAME=VALUE", default=None,
        help=_FIELD_HELP,
    )
    p_norm.set_defaults(func=cmd_get_normalized)

    p_send = sub.add_parser("send", help="POST a generic JSON to the listener endpoint")
    src = p_send.add_mutually_exclusive_group()
    src.add_argument("--file", help="Path to a JSON file to send")
    src.add_argument("--data", help="Inline JSON string to send")
    p_send.set_defaults(func=cmd_send)

    p_search = sub.add_parser("search", help="Full-text search the raw or normalized table")
    p_search.add_argument("query", help="Search term (matched against indexed text fields)")
    p_search.add_argument(
        "feeds", nargs="*", help="Feed names to restrict the search to (omit for all feeds)",
    )
    p_search.add_argument(
        "--type", choices=("raw", "normalized"), default="raw",
        help="Which table to search (default: raw)",
    )
    p_search.add_argument(
        "--max", type=int, default=DEFAULT_MAX,
        help=f"Maximum number of results (default: {DEFAULT_MAX})",
    )
    p_search.add_argument(
        "--field", action="append", metavar="NAME=VALUE", default=None,
        help=_FIELD_HELP,
    )
    p_search.set_defaults(func=cmd_search)

    p_query = sub.add_parser(
        "query",
        help="Ask a natural-language question; the server's LLM runs it against the DB",
    )
    p_query.add_argument("question", help="Natural-language question (quote it)")
    p_query.add_argument(
        "--type", choices=("raw", "normalized"), default=None,
        help="Force the dataset to query (default: let the server/LLM decide)",
    )
    p_query.add_argument(
        "--source", default=None, help="Restrict to a single feed name",
    )
    p_query.add_argument(
        "--max", type=int, default=None,
        help="Maximum number of results (overrides the LLM's limit)",
    )
    p_query.set_defaults(func=cmd_query)

    p_feeds = sub.add_parser("list-feeds", help="List available feeds with per-source counts")
    p_feeds.add_argument(
        "--type", choices=("raw", "normalized"), default="raw",
        help="Which catalogue to list (default: raw)",
    )
    p_feeds.set_defaults(func=cmd_list_feeds)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 401:
            print(
                f"HTTP 401 Unauthorized: {detail}\n"
                "Hint: this server requires authentication — pass --username "
                "(and --password), or check your credentials.",
                file=sys.stderr,
            )
        elif exc.code == 403:
            print(
                f"HTTP 403 Forbidden: {detail}\n"
                "Hint: your account lacks permission for this action. "
                "`send` requires an 'admin' or 'sender' account; if you must "
                "change your password first, do so in the web UI.",
                file=sys.stderr,
            )
        else:
            print(f"HTTP {exc.code} {exc.reason}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
