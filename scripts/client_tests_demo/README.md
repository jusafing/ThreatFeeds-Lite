# API Client — Demo Test Runner

This folder contains an automated demo of the standalone API client
(`scripts/api_client.py`), driven by `run_tests.sh`. It runs the full T1–T11
test plan against a running ThreatFeeds Lite server and saves the results to
disk.

Contents:

- `run_tests.sh` — the test runner (bash). Reads connection + credentials from a
  `.env` file and executes T1–T11.
- `gen_events.py` — generates randomized threat events for the push test (T1).
  Six events are deterministically seeded so the search/query tests return
  meaningful hits (npm vulnerable-package, CVE-2026, and supply-chain content).
- `.env.example` — template for the `.env.test` you create (see below).
- `T1-*.md` … `T11-*.md` — **sample recordings from an earlier run**, kept as a
  reference for the expected command + response shape of each test.
- `events.json` — a sample generated payload.

## How to run

**1. Configure connection + credentials.** Copy the example env file and edit it
(`run_tests.sh` reads `./.env.test` by default):

```bash
cp .env.example .env.test
```

Fill in `.env.test` (the parser accepts either `=` or `:` as the delimiter):

```
host=192.168.0.10
port=8001
user_push=pusher      # pushes events to the listener (T1); a sender or admin account
pass_push=...
user_read=analyst     # reads, search, and NL queries (T2–T11); a normal or admin account
pass_read=...
```

Real `.env*` files are **gitignored** — only `.env.example` is committed.

**2. Run the plan.**

```bash
./run_tests.sh                      # uses ./.env.test
./run_tests.sh --env /path/to.env   # or point at another env file
```

**3. Inspect the results.** Each run creates a fresh `test-client-<epoch>/`
directory containing:

- `T1..T11-*.md` — one markdown file per test (the exact command run + the
  captured API response),
- `script-run.log` — the `api_client.py` invocation for each test, with the
  password masked,
- `execution.log` — timestamps, per-test status, and any errors.

These `test-client-*/` directories are **gitignored** — they are for local
verification only and are never committed.

## Tests

| Test | Account | What it does |
|---|---|---|
| `T1-push-events` | push | Generate randomized events and push them to the listener |
| `T2-verify-push` | read | Read the raw table back to confirm the events landed |
| `T3-raw-10` | read | Fetch 10 events from the raw table |
| `T4-normalized-10` | read | Fetch 10 events from the normalized table |
| `T5-list-feeds` | read | List available feeds with per-source counts |
| `T6-search-npm` | read | Full-text search the raw table for "npm" |
| `T7-query-npm-supplychain` | read | NL query: npm supply-chain compromise (LLM) |
| `T8-query-critical-cves` | read | NL query: critical 2026 CVEs by vendor (LLM) |
| `T9-query-actor-indicators` | read | NL query: high-severity indicators by actor (LLM) |
| `T10-field-raw-severity` | read | Field search from raw: exact `severity=critical` via `--field` (no LLM) |
| `T11-field-normalized-indicator-type` | read | Field search from normalized: exact `indicator_type=ipv4` via `--field` (no LLM) |

The natural-language tests (T7–T9) require an LLM provider configured on the
server; without one they record an HTTP `503`. See the **API Client Script →
Demo runner** section of the top-level `README.md` for more detail.
