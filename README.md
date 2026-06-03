# ThreatFeeds Lite

A lightweight, standalone Threat Intelligence feed aggregator with an
**LLM-powered normalization engine** — it doesn't just collect feeds, it uses an
LLM to reconcile heterogeneous threat intel into a single canonical schema. It
listens for, pulls, and normalises threat intel from multiple sources, stores
data locally in SQLite, and exposes a web UI for viewing and configuration.

---

## Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | Required for the backend |
| Node.js | 18+ | Required for the frontend |
| npm | 9+ | Bundled with Node.js |
| uv | latest | Recommended Python package manager |

### Install uv

**macOS / Linux**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Homebrew**
```bash
brew install uv
```

> If `uv` is not installed, the startup script falls back to `python3 -m venv` + `pip` automatically.

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd OSS-Project-SimpleFeedIngestor

# 2. Start the application
./threatfeeds-lite start
```

The runner script will automatically:

- Create a Python virtual environment (`.venv/`) using `uv`
- Install all Python dependencies from `backend/requirements.txt`
- Build the frontend if `frontend/dist/` does not exist
- Start the backend on **`127.0.0.1:8000`** (localhost only by default)

Open your browser at **http://localhost:8000**

> By default the server binds to localhost only. To expose it on your network
> or use a different port, see [Binding & ports](#binding--ports) below.

> **Sharing the server?** Authentication is **off by default** (open UI and API).
> For anything beyond a local single-user run — especially when binding to a
> network interface — start with `./threatfeeds-lite start --enable-auth` and see
> [Authentication](#authentication-optional) for roles and first-run admin setup.

---

## Development Mode

Runs the backend and the Vite dev server separately for hot-reload on frontend changes:

```bash
./threatfeeds-lite start --dev
```

| Service | URL |
|---|---|
| Frontend (Vite) | http://localhost:5173 |
| Backend API | http://localhost:8000/api |
| API docs (Swagger) | http://localhost:8000/docs |

> URLs above assume `app_base_prefix` is empty (the default). When set, all
> URLs are served under `<prefix>/...` — e.g. `http://localhost:8000/feeds/api`.
> See `config/application.yaml` and the `--base-prefix` CLI flag.
>
> Reverse-proxy aliases (e.g. nginx `location /feeds/ → backend root`) work
> with no configuration: the frontend auto-detects the alias from
> `window.location` and uses it for routing, API fetches, and the displayed
> push URL. Set `app_base_prefix` explicitly only to PIN a specific prefix.

> Node modules are installed automatically on first run if `frontend/node_modules/` is absent.

---

## Commands

```
./threatfeeds-lite start [options]        Start the application
./threatfeeds-lite stop                   Stop all running processes
./threatfeeds-lite restart [options]      Stop then start (options forwarded to start)
./threatfeeds-lite status                 Show running process status
./threatfeeds-lite --reset-db             Delete and recreate all source databases
./threatfeeds-lite --reset-source <NAME>  Reset a single source database
./threatfeeds-lite --reset-admin-password Reset the admin password (see Authentication)
./threatfeeds-lite help                   Show full usage
```

### `start` / `restart` options

| Option | Description |
|---|---|
| `--dev` | Run uvicorn in the **foreground** with logs streamed to the terminal (Ctrl+C to stop). Without it, the server runs backgrounded. |
| `--bind <ip[:port]>` | Address (and optional port) to bind. Accepts `ip`, `ip:port`, or `:port`. If no port is given, **8000** is used. Default: `127.0.0.1:8000`. See [Binding & ports](#binding--ports). |
| `--base-prefix <value>` | Override `app_base_prefix` from `config/application.yaml` for this run only (via `SIMPLE_FEED_BASE_PREFIX`). Must start with `/`, must not end with `/`, must not contain `//`. Use `""` (empty string) to mount at root. |
| `--enable-auth` | Force-enable authentication for this run (via `SIMPLE_FEED_ENABLE_AUTH=1`, overriding the yaml). See [Authentication](#authentication-optional). |

> Runtime PID and port state are written to `.pids/` (gitignored). `stop`/`status`
> read the persisted port, so they work correctly even when the server was
> started on a custom `--bind` port.

---

## Binding & ports

By default the server binds to **`127.0.0.1:8000`** — reachable only from the
local machine. Use `--bind` to change the address and/or port:

```bash
./threatfeeds-lite start --bind 0.0.0.0          # all interfaces, port 8000
./threatfeeds-lite start --bind 0.0.0.0:9000     # all interfaces, port 9000
./threatfeeds-lite start --bind :9000            # localhost (127.0.0.1), port 9000
./threatfeeds-lite start --bind 192.168.1.10:8000  # a specific interface
```

- Syntax is `ip:port`. A bare `ip` keeps the default port (8000); a bare `:port`
  keeps the default address (`127.0.0.1`).
- The port must be between **1 and 65535** — an out-of-range value is rejected.
- Binding to `0.0.0.0` exposes the service on **all network interfaces**. Only do
  this on trusted networks, and consider enabling [authentication](#authentication-optional).

---

## Authentication (optional)

Authentication is **disabled by default** — the UI and API are open. Enable it
either per-run with `--enable-auth`, via the `SIMPLE_FEED_ENABLE_AUTH=1`
environment variable, or in `config/application.yaml`. The CLI flag and env var
take precedence over the yaml.

```bash
./threatfeeds-lite start --enable-auth
```

When enabled:

<a id="user-roles"></a>

- **Roles.** Three roles: `admin` (full access, including Configuration,
  Normalizer, and User Management), `normal` (viewer-scoped, read-only), and
  `sender` (a listener-only machine account that may **only** POST to
  `/api/ingest/listener` — ideal for unattended push automation). The sidebar and
  API enforce role gating server-side. The natural-language query endpoint
  (`POST /api/query/nl`) is a read operation available to `admin` and `normal`,
  but not to `sender`.
- **Sessions.** Login is session-cookie based; all API `401`s funnel through a
  single handler and the UI redirects to the login screen.
- **First-run admin.** On first start with auth enabled, an `admin` account is
  provisioned with a random password written to
  `data/first-run-admin-credentials.txt` (mode `0600`, **gitignored**, never
  logged). You must change this password on first login; delete the file
  afterwards.
- **Reset the admin password** without starting the server:

  ```bash
  ./threatfeeds-lite --reset-admin-password
  ```

  This generates a new random password, prints it, writes it to the same `0600`
  credential file, and forces a password change on the next admin login.
- **Password policy.** Minimum-length and composition rules are enforced on both
  the backend and the frontend (create-user, self-service change, and admin
  reset all require a confirm-match field).
- **Self-service Account** page (change your own password) and an admin-only
  **User Management** tab (create/delete users, reset passwords) live in the app.

---

## Supported Feed Formats

Local uploads and remote pulls both accept:

- **JSON** (object or array of objects; well-known envelope keys like
  `vulnerabilities`, `data`, `results` are auto-extracted)
- **NDJSON** (one JSON object per line)
- **CSV / TSV** (auto-detected delimiter from `, \t ; |`)
- **XML** (flat one-level-deep envelopes)

Both ingest paths also transparently decompress:

- **`.gz`** (single-layer gzip)
- **`.zip`** (must contain exactly one regular-file member; empty and
  multi-member archives are rejected)

The decompressed payload is verified to be one of the four plaintext
formats above before parsing. The default decompressed-size cap is
**100 MiB**, configurable in `config/application.yaml` under
`max_decompressed_bytes`. `.7z` is intentionally not supported (avoids
a non-stdlib dependency).

---

## Push Listener (API endpoint)

The push listener lets external tools POST threat-intel events straight into the
app over HTTP. It runs on the main application port (no separate port) and is
toggled from **Configuration → Listener Endpoint** (`listener.enabled` in
`config/sources.yaml`, enabled by default).

**Generic receive** — POST any JSON (a single object or an array of objects) to:

```bash
curl -X POST http://127.0.0.1:8000/api/ingest/listener \
  -H 'Content-Type: application/json' \
  -d '[{"indicator": "1.2.3.4", "threat_type": "c2"}]'
```

Events are indexed into a **feed named after the authenticated user** that
pushed them (prompts-058). When authentication is disabled the request is
anonymous and falls back to a **feed named `Received Feed <epoch>`** (the Unix
time of receipt). Every payload is logged (an INFO receipt summary in
`logs/audit.log`; the full body at DEBUG), and per-entry failures are logged with
detail in `logs/app.log`.

To push into an **explicitly-named** feed instead, POST to
`/api/ingest/push/<source_name>` (single object) or
`/api/ingest/push-batch/<source_name>` (array).

> When `auth_enabled` is on, ingest endpoints require an admin session; with the
> default local config (auth off) they are open.

---

## Watchers

A **Watcher** is a user-defined saved filter that continuously evaluates
ingested events and publishes the matches to a **public per-watcher feed URL**
(and, optionally, pushes them to a webhook). Use watchers to carve a focused
syndication feed out of the firehose — e.g. *"critical CVEs affecting nginx"* or
*"anything tagged ransomware from feedA"* — that downstream tools can poll
without touching the admin API.

Watchers are managed from the admin-only **Watchers** page (Summary / Config /
Activity tabs) and the admin-gated `/api/watchers/*` API. Definitions and
triggered-event history live in a dedicated `data/watchers.db`, kept separate
from `normalized.db` so they survive normalized-schema rebuilds.

### How matching works

Each watcher has one or more **conditions**, AND-combined. A condition is a
`field` + `value` plus a `match_type`:

| `match_type` | Matches when… |
|---|---|
| `exact` | field equals value |
| `contains` | value is a substring of the field |
| `wildcard` | field matches a `*`/`?` glob |
| `regex` | field matches the regular expression |
| `gte` / `lte` | numeric field is ≥ / ≤ a numeric value |

- Leave `field` empty (or `*`/`all`/`any`) to match the value against **any**
  field. `case_sensitive` (default off) applies to `exact`, `wildcard`, and
  `contains`.
- **Scope** is set per watcher: `dataset` (`all`, `raw`, or `normalized`) and
  `feeds` (a list of source names; empty = all feeds).
- `severity` (`low`/`medium`/`high`/`critical`) is a **classification label
  only** — it does not gate matching.

### Evaluation modes

| `mode` | When it evaluates |
|---|---|
| `realtime` | Automatically when an ingestion or normalizer run completes. |
| `scheduled` | On a fixed timer every `interval_sec` seconds (min 5). |

Each watcher tracks per-source high-water marks, so only **new** events are
considered on each pass. You can also press **Trigger** in the UI (or
`POST /api/watchers/{id}/trigger`) to evaluate immediately — this works even on
a disabled watcher.

### Public feed

Matched events are published at an **unauthenticated** syndication URL (it lives
outside `/api/`, so the auth layer does not guard it):

```bash
# JSON (default); also CSV and XML/RSS via the watcher's `format`
curl http://127.0.0.1:8000/feed/watcher/<watcher-id>/
```

The `<watcher-id>` is a slug derived from the watcher name. The feed serves the
most recent matches, capped by the watcher's `max_feed_events` (and the global
`watcher_max_events` ceiling, below).

### Webhook / HTTP delivery (optional)

Set `publish_target` to `webhook` or `http` to also **push** each match to an
external endpoint. The payload is shaped by `webhook_format`:

| `webhook_format` | Target |
|---|---|
| `generic` | Plain JSON POST |
| `discord` | Discord webhook |
| `slack` | Slack incoming webhook |
| `teams` | Microsoft Teams connector |

The format is auto-detected from the webhook URL host (and overridable). An
optional `auth_header` / `auth_value` pair attaches a custom auth header.
Delivery is per-event best-effort with automatic retries; failures are surfaced
in the **Activity** tab with the last error detail for inspection.

> **Admin-only by design.** Watcher webhook URLs are admin-configured and may
> target internal hosts, so all `/api/watchers/*` routes are restricted to
> `admin` accounts. Do not expose watcher configuration to non-admin roles.

### Retention

- **Per watcher:** `max_feed_events` bounds how many matches the feed keeps; a
  periodic cleanup job trims the stored feed every `cleanup_interval_sec`
  seconds (10–86400).
- **Global ceiling:** `watcher_max_events` in `config/application.yaml` caps
  retention across every watcher (default **1000**, range **10–100000**). It is
  editable from the UI or via `GET`/`POST /api/app/watcher-max-events`.

---

## Viewer & Normalization UI

Beyond the API and watchers, the web UI exposes the core day-to-day surfaces:

- **Viewer** — browse the **Raw** and **Normalized** event tables side by side,
  with a configurable column picker, full-text search, and a natural-language
  (LLM) query box. Raw and normalized are independent stores (see
  [Raw vs normalized](#get-normalized--normalized-events)).
- **Normalizer** — run and monitor the LLM-powered normalization engine, and
  review per-run history (counts, status, timing).
- **Smart Mappings** — an admin workspace for LLM-assisted field-mapping
  suggestions, with manual overrides that feed the canonical schema (see
  [Canonical schema reconciliation](#canonical-schema-reconciliation)).

---

## API Client Script

`scripts/api_client.py` is a standalone, dependency-free (Python standard library
only) client for the API. Run it with any Python 3 — no virtualenv needed. Every
command prints a JSON document to stdout.

```bash
scripts/api_client.py --help          # full syntax for all commands
```

**Global options**

- `--url` — base API endpoint URL (default `http://127.0.0.1:8000`). Include the
  scheme; use an `https://…` URL (optionally with a path prefix) to reach a
  server behind a reverse proxy / alias. The value must contain no whitespace —
  a stray inline comment (e.g. copied from a `.env` template) is rejected with a
  clear error instead of an obscure urllib traceback.
- `--username` / `-u`, `--password` / `-p` — credentials for an auth-enabled
  server (omit entirely when auth is disabled — see [Authentication](#authentication) below).
- `--insecure` / `-k` — skip TLS certificate verification (accept self-signed /
  untrusted certs). **Insecure:** disables MITM protection for the request; only
  use on a trusted network against a server you can otherwise vouch for.

### `get-raw` — raw events

Fetch the raw events table as a JSON array.

```bash
# All feeds, up to 1000 events (the default)
scripts/api_client.py get-raw

# Only two named feeds
scripts/api_client.py get-raw feedA feedB

# Cap the number of events returned
scripts/api_client.py get-raw --max 50

# Exact-column filter: only critical-severity rows (repeatable, AND-combined)
scripts/api_client.py get-raw --field severity=critical
scripts/api_client.py get-raw --field severity=critical --field indicator_type=url
```

> `--field NAME=VALUE` is a **deterministic exact-match** filter (no LLM). The
> column name is validated server-side against the table's real columns;
> unknown or unsafe names are silently dropped rather than reaching SQL, so the
> flag cannot inject arbitrary queries. Use `query` (below) for fuzzy,
> natural-language matching instead.

### `get-normalized` — normalized events

Identical interface to `get-raw`, but reads the normalized data table.

```bash
# All normalized events, up to 1000
scripts/api_client.py get-normalized

# A single feed, capped at 20 events
scripts/api_client.py get-normalized feedA --max 20

# Exact-column filter, validated against the normalized schema
scripts/api_client.py get-normalized --field indicator_type=ipv4-addr
```

> **Raw vs normalized are independent stores.** `get-raw` (and
> `search`/`query --type raw`) read the as-ingested **raw** events; `get-normalized`
> (and the `--type normalized` variants) read the **normalized** table produced by
> the normalizer. The same `--field`/`search`/`query` filter run against each can
> return different rows — raw reflects original feed fields, normalized reflects
> the mapped/canonical schema.

> With no feed names, both `get-*` commands issue a single all-feeds request.
> With feed names, they request each feed in turn and merge the results,
> truncated to `--max` (default 1000).

### `send` — push events to the listener

POST a generic JSON (a single object or an array) to `/api/ingest/listener`. The
server indexes it into a feed named after the authenticated user (or a
`Received Feed <epoch>` feed when auth is disabled). The payload can come
from a file, an inline string, or stdin:

```bash
scripts/api_client.py send --file events.json
scripts/api_client.py send --data '[{"indicator": "1.2.3.4"}]'
cat events.json | scripts/api_client.py send
```

When auth is enabled, `send` requires an **admin** or **sender** account (a
`normal` user gets `403`).

### `search` — full-text search

Search the raw or normalized table via the server-side `?search=` query. The
term is matched against indexed text fields (indicator, title, description,
tags, actor, campaign, …). Accepts the same optional feed names and `--max` as
the `get-*` commands.

```bash
# Search raw events for "npm" (default --type raw)
scripts/api_client.py search "npm" --max 20

# Search the normalized table, restricted to one feed
scripts/api_client.py search "ransomware" feedA --type normalized
```

### `query` — natural-language query (LLM)

Ask a question in plain English. The server's LLM translates it into a
**constrained, whitelisted filter** (never raw SQL), runs that filter against
the local database through the same parameterized query layer used by the rest
of the API, and returns the matched rows plus the interpreted filter. This
requires an LLM provider to be configured on the server (the same one used by
smart-mode normalization); without one the endpoint returns `503`.

```bash
# Let the server/LLM choose the dataset (defaults to normalized)
scripts/api_client.py query "critical CVEs from 2026 affecting nginx"

# Force the raw table, restrict to one feed, cap results
scripts/api_client.py query "supply-chain compromise in npm packages" \
  --type raw --source feedA --max 50
```

`--type {raw,normalized}`, `--source <feed>`, and `--max <n>` are optional
overrides: when supplied they take precedence over the LLM's choice. The
response is JSON with `dataset`, `count`, `interpreted_filter`, and `results`.

When auth is enabled, `query` is a **read** operation — available to `admin`
and `normal` accounts; `sender` accounts get `403` (push-only).

### `list-feeds` — available feeds

List the available feeds with their per-source entry counts (plus a `__total__`
row), from the summary endpoint.

```bash
scripts/api_client.py list-feeds                 # raw catalogue (default)
scripts/api_client.py list-feeds --type normalized
```

### Demo runner — run the whole test plan with one command

`scripts/client_tests_demo/run_tests.sh` drives `api_client.py` through the full
T1–T11 test plan against a running server and saves every result to disk. It is
the easiest way to see the client in action end to end.

**1. Configure connection + credentials.** Copy the example env file and fill it
in (it is read from `scripts/client_tests_demo/.env.test` by default):

```bash
cp scripts/client_tests_demo/.env.example scripts/client_tests_demo/.env.test
# then edit .env.test:
#   host [+ port]          — target host; port is OPTIONAL (defaults: 80 http / 443 https).
#                            host may include a scheme (https://host); bare host -> http. OR
#   url                    — full base URL incl. scheme (reverse-proxy alias / path prefix);
#                            wins over host/port, must include http:// or https://
#   skip_tls_verify=true   — accept self-signed/untrusted TLS (insecure; or use -k)
#   user_push / pass_push  — account used to push events (T1; a sender or admin)
#   user_read / pass_read  — account used for reads/search/queries (T2–T11; normal or admin)
```

**2. Run it.**

```bash
bash scripts/client_tests_demo/run_tests.sh                 # uses .env.test
bash scripts/client_tests_demo/run_tests.sh --env /path/to/other.env
bash scripts/client_tests_demo/run_tests.sh -k              # also skip TLS verification
```

**3. Read the results.** Each run creates a fresh `test-client-<epoch>/`
directory inside `scripts/client_tests_demo/` containing:

- `T1..T11-*.md` — one markdown file per test (the command run + the captured response),
- `script-run.log` — the exact `api_client.py` invocation for each test (password masked),
- `execution.log` — timestamps, per-test status, and any errors.

These result directories are **gitignored** — they are for local verification
only and are never committed. The natural-language tests (T7–T9) require an LLM
provider configured on the server; without one they record an HTTP `503`. The
field-search tests (T10–T11) are deterministic `--field` filters that need no
LLM.

### Authentication

Authentication is **optional**. The client adapts to whichever mode the server
runs in.

**Without auth (the default).** When the server has auth disabled, run any
command with no credentials — exactly as the examples above. `send` succeeds for
anyone:

```bash
# default local server, no credentials
scripts/api_client.py get-raw
scripts/api_client.py --url http://192.168.0.10:8001 get-normalized --max 50
scripts/api_client.py --url http://192.168.0.10:8001 send --data '[{"indicator": "1.2.3.4"}]'
```

**With auth enabled.** Supply `--username`; the client logs in once and reuses
the session cookie for the request. Omit `--password` to be prompted securely
(it never touches your shell history):

```bash
# prompts for the password, then fetches raw events from an auth-enabled host
scripts/api_client.py --url http://192.168.0.10:8001 --username analyst get-raw

# non-interactive (e.g. CI): pass the password explicitly
scripts/api_client.py --url http://192.168.0.10:8001 -u bot -p "$SFI_PW" \
  send --file events.json

# HTTPS behind a reverse-proxy alias, accepting a self-signed cert (insecure)
scripts/api_client.py --url https://proxy.example.com/threatfeeds -k \
  --username analyst get-raw
```

A dedicated **sender** account (see [User roles](#user-roles)) is the recommended
identity for unattended `send` automation — it can push to the listener and
nothing else. With auth enabled, `send` requires an **admin** or **sender**
account (a `normal` user gets `403`).

---

## Canonical schema reconciliation

The normalizer's canonical namespace is sourced from
`config/feed-fields.yaml`. The derived `data/normalized.db` schema is
rebuilt on backend startup whenever its stored `schema_version` is older
than the engine's required version: the file is dropped, recreated from
the current yaml field list, and every source DB has its `normalized`
flag reset so the next normalizer run repopulates the table. Raw source
DBs are never touched — `normalized.db` is treated as regenerable data.

Operator-defined `manual_mappings` in `config/normalizer-config.yaml`
that still reference the engine's pre-021E-pre canonicals (`ip_address`,
`domain`, `hash`, `cve`, `timestamp`, `source_name_norm`) are
auto-translated to the equivalent yaml canonical names on load; each
change is logged at WARNING level and the file is rewritten in place.

---

## LLM Provider (optional)

The normaliser can optionally call an external LLM for smart-mode
features (schema proposals, field-mapping suggestions). The plumbing
is **disabled by default** and ships in `prompts-021D`; smart-mode
behaviour itself lands in later phases.

### Supported provider kinds

| `kind`              | Notes |
|---|---|
| `openai`            | OpenAI public API |
| `anthropic`         | Anthropic public API |
| `ollama`            | Local Ollama (no API key) |
| `openai_compatible` | Any server speaking OpenAI's chat-completions wire shape (Together, Groq, vLLM, LM Studio, …) |

### Configuration

The real config lives at `config/llm-providers.yaml`, which is
**gitignored**. A documented template ships at
`config/llm-providers.yaml.example`. Populate the real file via:

```
PUT /api/llm/config        # write-only api_key semantics; see below
GET /api/llm/config        # returns redacted view (api_key == "***" if set)
GET /api/llm/providers     # listing without secrets
POST /api/llm/providers/{name}/test
                           # smoke-test: prefers list_models() (no token
                           # burn); falls back to a 1-token complete()
```

### Security properties

- **Default off.** `enabled: false` out of the box. Constructing a
  client while disabled raises `LLMDisabledError`.
- **Write-only API key.** `GET /api/llm/config` never returns the real
  key — `"***"` when set, `""` when unset. `PUT` accepts `"***"` as
  "keep existing"; any other string replaces.
- **TLS-skip is per-provider.** `skip_tls_verify: true` builds an
  unverified SSL context **per request** (never global) and emits a
  WARNING log including the provider name. Intended for self-hosted or
  lab use only.
- **No streaming, no history, no public `/complete` endpoint.** The
  plumbing exposes only config, listing, and a smoke-test endpoint.
- **Stdlib transport.** Uses `urllib.request` via `asyncio.to_thread`,
  with 5xx retries (exponential backoff) and timeout enforcement. No
  new runtime dependencies.

---

## Manual Dependency Setup

If you prefer to install dependencies before starting:

**Python (backend)**
```bash
uv venv .venv
uv pip install -r backend/requirements.txt --python .venv/bin/python
```

**Node.js (frontend)**
```bash
cd frontend && npm install
```

---

## Running Tests

```bash
./scripts/test.sh
```

Runs backend tests (`pytest`) and frontend tests (`vitest`).

---

## Project Layout

```
threatfeeds-lite        # Startup script — entry point
config/
  feed-fields.yaml          # Core + custom field definitions
  sources.yaml              # Configured ingestion sources
backend/                    # Python / FastAPI backend
  requirements.txt          # Python dependencies
frontend/                   # React / TypeScript frontend
data/                       # SQLite databases (auto-created, gitignored)
  watchers.db               # Watcher definitions + triggered-event history
docs/                       # Architecture, plans, session log
scripts/                    # check.sh, test.sh, security-check.sh, api_client.py
```

## License

ThreatFeeds Lite is released under the Apache License 2.0. See the
[`LICENSE`](LICENSE) file for the full terms.

This product includes third-party open-source software. Each bundled
dependency remains under its own license; see
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) for the complete list of
runtime dependencies and their licenses.

## Development

ThreatFeeds Lite was developed using [OpenCode](https://opencode.ai) — it is a vibecoding project, built largely through AI-assisted, conversational coding.
