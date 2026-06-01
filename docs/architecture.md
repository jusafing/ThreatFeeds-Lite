# Architecture — ThreatFeeds Lite v0.1

**Status:** Complete  
**Last updated:** 2026-05-24

---

## Overview

ThreatFeeds Lite is a standalone, local Threat Intelligence feed aggregator.
It listens for, pulls, and normalises threat intel from multiple sources, stores
data in SQLite, and exposes a web UI for viewing and configuration.

It is designed to run independently and integrate later as a module within a
larger security framework.

---

## Components

```
┌─────────────────────────────────────────────────────────────┐
│                     threatfeeds-lite                    │
│                  (bash runner — start/stop)                 │
└────────────┬──────────────────────┬─────────────────────────┘
             │                      │
    ┌────────▼────────┐    ┌────────▼────────┐
    │   Backend       │    │   Frontend      │
    │   (Python /     │    │   (React /      │
    │    FastAPI)     │◄───┤    TypeScript / │
    │   port: 8000    │    │    Vite)        │
    └────────┬────────┘    │   port: 5173    │
             │             │   (dev only)    │
    ┌────────▼────────┐    └─────────────────┘
    │   SQLite DBs    │
    │   data/*.db     │
    │  (per source)   │
    └─────────────────┘
```

---

## Backend Modules

| Module | Path | Responsibility |
|---|---|---|
| Config loader | `backend/config/loader.py` | Reads/writes `config/*.yaml` |
| DB manager | `backend/db/manager.py` | Per-source SQLite file CRUD |
| DB schema | `backend/db/schema.py` | `entries` table DDL |
| Normaliser | `backend/ingestion/normaliser.py` | Filters fields against config |
| Push listener | `backend/ingestion/push_listener.py` | Normalises pushed JSON payloads; logs receipts + per-entry errors (ADR-0022) |
| API pull | `backend/ingestion/api_pull.py` | Scheduled HTTP GET ingestion |
| RSS pull | `backend/ingestion/rss_pull.py` | Scheduled RSS fetch + normalisation |
| Local JSON | `backend/ingestion/local_json.py` | File upload ingestion |
| Remote JSON | `backend/ingestion/remote_json.py` | URL-based JSON ingestion |
| API routes | `backend/api/routes_*.py` | FastAPI route groups |
| Main app | `backend/main.py` | FastAPI app, CORS, static files (delegates scheduling to `backend/scheduler.py`) |
| Scheduler | `backend/scheduler.py` | APScheduler lifecycle (`start`/`stop`/`reload`), smart-mode fan-out, `submit_smart_job(source, reason, provider?)` with `asyncio.Semaphore` concurrency cap (021E-3). `reload()` schedules a pull job only when a source is `enabled` **and** `continuous` (prompts-042 / ADR-0014): `api_pull` and `rss_pull` treat a missing `continuous` as `True` (non-breaking for pre-existing sources); `remote_json_pull` defaults it to `False` |
| Smart runner | `backend/normalizer/smart_runner.py` | `run_smart_job(...)` — pulls sample rows, calls LLM, persists proposal row with `trigger_reason` (021E-3); `approve_proposal_core(...)` is the shared write-path for manual approve + 021E-4 auto-apply, and as of 021F writes through to `mapping_versions.db` |
| Mapping versions | `backend/normalizer/mappings.py` | Per-source `mapping_version` history in `data/mapping_versions.db` (021F). `init_mappings_db`, `create_version`, `list_versions`, `get_version`, `get_active_version`, `get_all_active_mappings`, `activate_version` (BEGIN IMMEDIATE + partial unique index `WHERE active=1`), `migrate_yaml_manual_mappings_once`, `regenerate_yaml_snapshot`, `diff_mappings` (three-bucket added/removed/changed) |
| Mapping routes | `backend/api/routes_mappings.py` | HTTP surface for the mapping_version history under `/api/normalizer/mappings/{versions,versions/{id},versions/{id}/activate,diff}` (021F) |
| LLM | `backend/llm/{client,config,registry,errors,test_runner}.py` | Multi-provider LLM abstraction (openai / anthropic / ollama / openai_compatible). `config.py` reads `config/llm-providers.yaml`, enforces `PROVIDER_NAME_RE` on insert via `validate_new_provider_name` (existing names grandfathered, 022), and validates the optional `available_models: list[str]` field added in 027 (populated by the Discover Models surface; not redacted on read — model identifiers are public); `registry.py` resolves provider name + caches client instances, and exposes `build_client_from_payload()` for the ephemeral wizard test (022); `client.py` exposes async `complete()` + `list_models()` with retry/timeout policy and emits structured `llm.request`/`llm.response` log records on every `_send`; for `openai_compatible` providers `list_models()` probes an ordered, de-duplicated set of candidate catalog URLs (`{base}/models`, `{base}/v1/models`, `{base}/openai/models`, host-root `/v1/models` and `/models`) and returns the first OpenAI-shaped payload, so path-split servers such as OpenWebUI — whose chat-completions live under `{base_url}/chat/completions` but whose catalog is not at `{base_url}/models` — resolve without a non-JSON `JSONDecodeError` (029); the common case (OpenAI proper / `…v1`) still resolves on the first candidate as a single GET, a 2xx-with-unusable-body on every candidate raises one `LLMProviderError` carrying `attempted_urls`, and an all-candidate transport/HTTP failure soft-fails to `None` for the 028 free-text fallback (`OpenAIClient` keeps its strict single-URL `list_models`) with header redaction (`Authorization`, `x-api-key`, `api-key`) and body truncation at 8 KiB; per-call `step=` kwarg + instance-level `self._tap` callback let the test runner stamp each wire call with a step label without touching concrete clients (022); `test_runner.py` (022) is the canonical Test envelope — `run_provider_test` always runs both `list_models` and `complete`, aggregates `ok` only if both succeed, and synthesises a skipped `list_models` step for Anthropic; 027 factored the list-models leg into `_run_list_models_step` and added `run_discover_only` so the Discover routes share the 023 empty-models verdict and 025 safety net without duplicating wire code; `errors.py` defines the wire-error taxonomy (021D) |
| LLM routes | `backend/api/routes_llm.py` | HTTP surface for LLM configuration (022 rewrite, extended in 027). `POST/PUT/DELETE /api/llm/providers[/{name}]` are the per-provider CRUD path, `POST /api/llm/providers/test` is the draft Test (no name in path; the 027 merge-stored-key branch injects the on-disk key when the body carries a `name` matching a persisted record AND `api_key == "***"`, so the persisted-card surface can probe without echoing keys through the wire), `POST /api/llm/providers/{name}/test` is the legacy persisted-provider test, `POST /api/llm/providers/discover` and `POST /api/llm/providers/{name}/discover` (027) are the discover-only routes used by the Discover Models surface on both the wizard and the persisted card (both delegate to `test_runner.run_discover_only`), and `PUT /api/llm/config` is narrowed to `{enabled, default_provider}` only — per-provider fields move through the CRUD routes. All Test routes return the `LLMTestRunResult` envelope produced by `test_runner.run_provider_test`; discover routes return the same envelope with the `complete` step omitted. `DELETE /api/llm/providers/{name}` clears `default_provider` if it pointed at the deleted provider and, when the delete empties the providers list while LLM is enabled, also clears `enabled` in the same write so the last provider is deletable and the persisted config never lands in the invalid `enabled=true` + zero-providers state (031) |
| NL query logic | `backend/query/nl.py` | Pure logic for the natural-language query feature (prompts-064 / ADR-0023): builds the LLM prompt from a closed key whitelist (`STRUCTURED_FILTER_KEYS`/`COLUMN_FILTER_KEYS`), parses + validates the LLM's JSON into a `StructuredQuery` (dataset ∈ `VALID_DATASETS`, default `normalized`; `source` checked against `known_sources`; `limit` clamped to `MAX_LIMIT=2000`), and runs it via the existing parameterized query layer (`query_entries` for raw, `query_normalized` for normalized). Normalized column filters are applied as an in-process post-filter (`_post_filter`) over a wide fetch (`_NORMALIZED_FETCH_CAP`) because `query_normalized` has no column-filter support; absent columns are ignored, not emptied. Never emits SQL. Reuses `parse_llm_response`/`_loads_tolerant` from `smart.py` |
| NL query routes | `backend/api/routes_query.py` | `POST /api/query/nl` (prompts-064): reader-gated (admin + normal; `sender` 403). Resolves provider + timeout from smart-mode config (`_NL_MAX_TOKENS=512`), calls the LLM via `asyncio.to_thread(client.complete, …)`, maps LLM errors to `503`/`502` and unparseable/invalid filters to `422`. Explicit client `dataset`/`source`/`limit` overrides win over the LLM's choice; `source` is re-validated against `_get_all_sources()`. Returns matched rows + interpreted filter (no second LLM call) |

---

## Frontend Pages

| Page | Path | Content |
|---|---|---|
| Viewer | `src/pages/Viewer.tsx` | Summary table + live entry table + Normalized tab (client-side viewer: bounded `pagination_max` fetch, in-browser paging/search/per-field filters/column picker/per-source colours — prompts-043, ADR-0015; the 021F mapping_version filter was removed from this surface) |
| Smart Mappings | `src/pages/SmartMappings.tsx` | LLM proposal review queue with outcome badges and auto-applied/discarded audit toggle (021E-2 + 021E-4) |
| Configuration | `src/pages/Configuration.tsx` | Tabbed config (reordered prompts-043, default `local-feed`): Local Feed, Remote Feed, External RSS, External API, Listener Endpoint, Global Field Defaults, Application (now hosts `PaginationMaxSetting`), Normalizer (sub-tabs: Settings, Mapping versions [`MappingVersionsPanel.tsx`, 021F], Activity [`ActivityTab.tsx`, 021G]), LLM Providers (`src/pages/configuration/LLMProvidersTab.tsx`, 021D-2; rebuilt in 022 around per-card `ProviderCard` with symmetric Test/Save/Delete wired to per-provider CRUD; rewritten in 027 to mirror the wizard's Discover-then-Probe staging — the "Default model to use" dropdown reads from `draft.available_models` on first paint, a new "Discover Models" button calls the persisted discover route and PUTs the resulting list to disk, "Test connection" probes via the draft endpoint with `api_key: "***"` (relying on the 027 merge-stored-key branch), and Save is gated on a probe-since-last-edit hash so editing any gating field re-locks Save until a fresh green probe lands). The "Add LLM" entry point opens `components/AddProviderWizard.tsx` — a 4-stage modal (Identify → Connect to provider → Test Model → Add Provider) rewritten in 027 around `draftHash` / `lastProbedHash` so each stage gates the next and the final "Add Provider" button is not even mounted in the DOM until the probe gate is satisfied. 028 decoupled the stage-2 (model picker) reveal from the discover call's aggregate `status`: stage 2 now shows whenever a non-empty model list came back (any status — a 200 with models but backend `status==='error'` still surfaces the dropdown) or the server returned a 2xx empty catalog (`emptyCatalog` → free-text model input + an amber "Server reachable, 0 models published" note instead of a red error); a thrown discover error or a non-2xx transport failure still shows the red error and keeps stage 2 hidden. A shared `components/TestDetailsModal.tsx` renders the structured per-step transcript returned by both the Test and Discover routes (it still receives the real aggregate `status`; only the wizard gating is decoupled) |
| About | `src/pages/About.tsx` | Version, git commit, backend health |

---

## Data Flow

```
External feed / push
        │
        ▼
[Ingestion module]
        │
        ▼
[Normaliser] ── filter against feed-fields.yaml ──► discard unknown fields
        │
        ▼
[DB Manager] ── INSERT OR IGNORE (dedup: source + indicator + published_at)
        │
        ▼
data/<source_name>.db  (SQLite)
        │
        ▼
[GET /api/viewer/entries] ◄── Frontend auto-refresh (polling)
        │
        ▼
EntryTable (React)
```

---

## Configuration Files

| File | Purpose |
|---|---|
| `config/feed-fields.yaml` | Core fields (31) + custom fields; enabled/disabled per field |
| `config/sources.yaml` | Listener status (`enabled`), API pull sources, RSS pull sources |
| `config/default-sources.yaml` | Curated catalogue of 30 no-auth threat-intel / vulnerability feeds (`threat_intel_sources`); read by `load_default_sources()`. Source of truth for the Remote Feed tab catalogue card (ADR-0014) |
| `config/application.yaml` | App-wide settings: `app_base_prefix`, `pagination_max` (prompts-043 — viewer row cap; default 1000, bounds 50–100000; read live, no restart), and the auth layer (prompts-045): `auth_enabled` (default false; env `SIMPLE_FEED_ENABLE_AUTH` overrides; restart required) and `logo_path` (branding logo under `data/branding/`, managed via the Application tab) |

---

## External Dependencies

| Dependency | Purpose |
|---|---|
| FastAPI + uvicorn | Backend HTTP server |
| aiosqlite | Async SQLite access |
| APScheduler | Scheduled pull jobs |
| feedparser | RSS/Atom parsing |
| httpx | Async HTTP client |
| pyyaml | Config file parsing |
| bcrypt | Password hashing for the auth module (prompts-045) |
| React + Vite | Frontend framework + build tool |
| Tailwind CSS | Utility-first styling |
| @tanstack/react-query | Server state management |
| react-router-dom | Client-side routing |

---

## Key Decisions

- See `docs/plans/simple-feed-ingestor-v0.1.md` for the full confirmed decision table.
- ADRs in `docs/decisions/` as significant decisions arise.

---

## Smart Mode (021E-3)

Smart Mode produces LLM-authored field-mapping **proposals** that an
operator reviews before they affect ingestion. It never alters
ingestion behaviour on its own.

**Triggers** (configured in `config/normalizer-config.yaml` under
`smart_mode`):

| Reason | Source |
|---|---|
| `manual` | Operator hits `POST /api/normalizer/smart/run` |
| `on_new_feed` | `job_store.complete()` fires on a job whose `first_ingest=True` and whose `kind` is not in `{smart_proposal, preview_confirm, source_preview_confirm}` |
| `schedule` | APScheduler interval job in `backend/scheduler.py` (driven by `smart_mode.schedule.interval_minutes`) |

**Gates** (applied by `backend.scheduler.submit_smart_job`):

1. `smart_mode.enabled=False` blocks `schedule` and `on_new_feed`;
   `manual` always proceeds (operator intent).
2. Per-source `enabled=False` blocks all reasons for that source.
3. `smart_mode.on_new_feed.enabled=False` blocks the `on_new_feed`
   reason only.
4. **Idempotency:** if a pending proposal already exists for the
   source, no new job is queued.
5. **Concurrency:** `smart_mode.concurrency.max_concurrent`
   (default 2) is enforced by an `asyncio.Semaphore` on the
   scheduler module. `reload()` re-initialises it.

**Provider precedence** (first non-null wins):

```
per-call argument → per-source override → smart_mode.provider →
llm.default_provider → error proposal row
```

**Persistence:** all triggers funnel through
`backend.normalizer.smart_runner.run_smart_job(...)` which persists
exactly one row in `data/proposals.db` (schema v5, see below). The
`trigger_reason` column records which reason produced the row.

**Known gap:** API-pull, RSS-pull and remote-JSON-pull sources do
not currently route through `job_store`, so `on_new_feed` does not
fire for them yet. Local-feed uploads and push-listener payloads do.

### `proposals.db` schema (v1 → v5)

021E-3 migrated `proposals.db` from v1 → v2 by adding five columns
(idempotent `ALTER TABLE` guarded by `PRAGMA table_info`). 021G's
follow-up bumped the schema to v3 by adding one more column
(`mapping_version_id`); prompts-032 added the consolidated-proposal
columns (v4); prompts-034 added the proposal-lifecycle columns (v5).
The same idempotent migration routine handles any prior-version jump
in a single pass (a v1 DB gains every later column; a fresh v5 DB is a
no-op).

| Column | Owner | Purpose |
|---|---|---|
| `trigger_reason` | 021E-3 (v2) | One of `manual`/`on_new_feed`/`schedule` |
| `score` | 021E-4 (v2) | Aggregate proposal confidence (populated later) |
| `score_breakdown` | 021E-4 (v2) | JSON per-field score detail |
| `outcome` | 021E-4 (v2) | Final classification once decided |
| `auto_applied` | 021E-4 (v2) | Flag set when 021E-4 auto-apply pipeline accepts |
| `mapping_version_id` | 021G (v3) | FK-ish link to `mapping_versions.id` produced by approve / auto-apply; NULL for pending/rejected/discarded |
| `sources_json` | 032 (v4) | Feed names spanned by a consolidated proposal (`source_name` is the `__consolidated__` sentinel) |
| `field_scope` | 032 (v4) | `all` or `configured` — raw fields presented to the LLM |
| `consolidated_version_id` | 032 (v4) | `consolidated_versions` row produced on approve |
| `proposal_name` | 034 (v5) | Stable human-facing label `Proposal-<UTC timestamp>`; legacy rows back-filled from `created_at` |
| `archived` | 034 (v5) | `1` once archived; archived rows are hidden from the default review/list views (kept for audit) |

021E-3 populates only `trigger_reason`. The four scoring columns
exist in the schema so 021E-4 is purely a population/UX change.
`mapping_version_id` is written by `approve_proposal_core` (the single
canonical apply path) so both the HTTP approve route and the 021E-4
auto-apply branch surface the link without behaviour drift.

`smart_mode.auto_apply.enabled` is structurally accepted but **no-op
in 021E-3** — see `docs/plans/prompts-021E-4-proposal-scoring.md`.

### Consolidated reliability, model selection + lifecycle (prompts-034)

The consolidated proposal pipeline
(`backend.normalizer.smart_runner.run_consolidated_smart_job`) gained
three reliability/UX knobs in prompts-034:

- **Dedicated LLM timeout/budget.** The consolidated `LLMClient.complete`
  call reads `smart_mode.llm_timeout_seconds` (default **600**) and
  `smart_mode.llm_max_tokens` (default **4096**) instead of the shared
  30s provider timeout. The longer timeout and larger token budget stop
  large multi-feed consolidations from timing out or truncating
  mid-JSON. Test Connection and Discover Models keep the 30s provider
  timeout. The override is passed per-call (`complete(..., timeout=,
  max_tokens=, model=)`) so cached clients are never mutated.
- **Tolerant response parsing.** `normalizer.smart.parse_llm_response`
  recovers control-character and trailing-comma responses via
  `_loads_tolerant` (`json.loads(strict=False)` → trailing-comma strip →
  `ast.literal_eval`) and otherwise fails with a diagnostic that names
  the likely cause (e.g. truncation against `llm_max_tokens`).
- **Per-proposal model override.** `POST /api/smart-mappings/jobs`
  accepts an optional `model` (blank → provider default). It is threaded
  through `run_consolidated_smart_job(model=)` to `complete(model=...)`
  and persisted to the proposal's `model` column as the *effective*
  model (`model or client.model`). The UI offers only models that have a
  successful Test Connection on record: `GET /api/llm/providers` now
  surfaces `tested_models: string[]` per provider (recorded by
  `llm.config.record_tested_model` whenever a Test returns `status=ok`),
  and the Smart Mappings dialog lists them as `provider · model` pairs.

**Lifecycle.** Every proposal carries a `proposal_name` and an `archived`
flag (schema v5 above). `POST /api/smart-mappings/proposals/{id}/archive`
hides a proposal from the default views without deleting it;
`GET /api/smart-mappings/proposals?archived=active|all|only` (default
`active`) selects the visibility filter.

### Reasoning-model support in smart mode (prompts-035)

`gpt-oss:120b`-class **reasoning models** broke consolidated mapping in four
distinct ways that the prompts-034 tolerant parser did not cover. prompts-035
hardens the pipeline along four independent layers (see ADR 0008 for the full
analysis and the Phase 0 live diagnosis):

- **Reasoning-output parsing.** `normalizer.smart.parse_llm_response` now runs
  `_strip_harmony` (extracts the content after the last `final` Harmony channel
  and removes `<|channel|>`/`<|message|>`/… control tokens; a no-op when no
  `<|…|>` tokens are present) and `_strip_json_comments` (a string-aware scanner
  removing `//` and `/* */` comments some models embed *inside* the JSON) before
  the existing fence/brace/`_loads_tolerant` recovery.
- **Token budget.** `smart_mode.llm_max_tokens` default raised **4096 → 8192**:
  reasoning models spend part of the budget on hidden reasoning tokens, which
  left the final channel empty at 4096. Still operator-tunable.
- **Config-driven reasoning control (`extra_body`).** Providers may set an
  optional `extra_body` mapping that is merged (via `setdefault`, so core
  `model`/`messages`/`stream` keys can never be clobbered) into the
  OpenAI-compatible `/chat/completions` payload. This carries reasoning controls
  (e.g. `reasoning_effort`, `chat_template_kwargs`) **without hardcoding any
  vendor param**, and is also the delivery path for an optional
  `response_format: {"type":"json_object"}` / `json_schema` constrained-decoding
  control. No-op when absent. The exact working value/param for a given gateway
  is confirmed by a one-off supervised live probe (deferred; not required for the
  parser + budget defaults to work).
- **Deterministic empty-content diagnostics.** When `choices[0].message.content`
  is empty, `OpenAICompatibleClient`/`OpenAIClient` raise a typed
  `LLMProviderError` whose cause is read from the **standard envelope fields**
  the gateway already provides — `finish_reason == "length"` (token-budget
  exhausted), `message.reasoning_content`/`reasoning` present (model reasoned
  without a final answer), or `content_filter` — instead of silently returning
  `""`. A populated `content` is returned verbatim (contract unchanged). This is
  the "structured output" countermeasure realized via the gateway's own fields
  rather than by re-prompting the model.

**Request-type logging (no gating change).** `LLMClient._send` now emits
`purpose=` (`list_models` | `complete`) and `context=` (`test` when a Test/Discover
tap is installed vs `direct` for smart-mapping jobs) on the INFO
`llm.request`/`llm.response` lines, so provider-side "multiple requests"
observations can be correlated to request type — including the OpenAI-compatible
candidate-URL discovery loop (ADR 0004). The locked two-step Test behavior is
unchanged; no over-discovery exists in the backend or frontend (Phase 0 evidence).

The tested-models dropdown population (`record_tested_model` on a green probe) is
unchanged from prompts-034; prompts-035 verifies the draft `POST /providers/test`
path records the operator-**selected** model (not the persisted default) and that
the Phase 2.5 diagnostics correctly keep an empty-content reasoning probe out of
the dropdown until it returns usable content.

**prompts-036 — proposal dropdown now reads discovered models; Save is
non-blocking.** prompts-034/035 sourced the proposal model dropdown from
`tested_models` (only models with a green Test on record). In practice this left
the dropdown empty for the common case of a freshly discovered provider whose
catalog was never individually tested (e.g. an OpenAI-compatible host with 60+
`available_models` but an empty `tested_models`), so no proposal model could be
picked. prompts-036 reverses ADR-0007 §1: `GET /api/llm/providers` now also
surfaces each provider's `available_models: string[]` (default `[]`), and
`SmartProposalConfirmModal` builds its `provider · model` options from
`available_models`, de-duplicated per `(provider, model)`. `tested_models` and
`record_tested_model` are retained but are no longer the dropdown source. The
empty-state hint appears only when **no** provider has any discovered model.

Correspondingly, per-card **Save is no longer gated on a connection test**
(amends ADR-0006 / prompts-033 decision A): once a provider has models, Save
persists directly. Model discovery still runs automatically only when the base
URL changes (a changed URL that yields no models still hard-blocks Save, since
the provider would be unusable); the manual "Test connection" and "Discover
Models" buttons are kept as optional operator tools. An unusable model now
surfaces at proposal request/response time via the prompts-035 diagnostics
rather than as a pre-flight Save rejection. See ADR 0009.

**prompts-037 — raw LLM exchange capture + reasoning-content answer recovery.**
Two changes, driven by a live diagnosis of why `gpt-oss:120b` (provider `cdt2`)
still failed consolidated mapping after prompts-035. (1) **Capture.** Every
proposal row now persists the full LLM HTTP exchange in two new columns
(`proposals.db` schema **v6**): `llm_request_raw` (method, URL, **redacted**
headers, request body) and `llm_response_json` (the WHOLE response envelope, not
just the extracted `choices[0].message.content` that `llm_response_raw` holds).
`LLMClient._send` fills a `_last_exchange` sink (separate from the prompts-035
`_tap` so the `context=` log label is preserved); `last_exchange_raw(error)`
sources the response body from `LLMProviderError.body` on HTTP 4xx/5xx and from
the sink otherwise. Both fields are capped at 64 KiB and surfaced read-only in
`SmartProposalErrorModal`. (2) **Recovery (amends ADR-0008 §4 / prompts-035
Phase 2.5).** The live capture showed the true failure: the gateway returns
`finish_reason: "stop"` with `message.content` **empty** while the *complete*
answer sits in `message.reasoning_content` (the model emitted its answer into the
reasoning channel). prompts-035 treated a populated `reasoning_content` + empty
`content` as a fatal typed error; prompts-037 instead **recovers** it —
`_extract_openai_content` calls `_recover_json_object_from_reasoning`, which scans
the reasoning text for the LAST top-level balanced `{…}` object (string/escape
aware so braces in prose do not unbalance it) and returns it when it parses as a
non-empty JSON object. The downstream validator still drops non-canonical keys,
so a false recovery cannot corrupt a mapping; only when nothing parseable is
found does the original empty-content diagnostic fire. The `finish_reason=length`
and `content_filter` diagnostics are unchanged. See ADR 0010.

**prompts-038 — consolidated mapping re-apply + Smart Mappings UX.** Approving a
consolidated mapping reported success but wrote no normalized rows
(`processed=0`): `_approve_consolidated_core` activated the version but never
reset the per-feed `normalized` flags, and the engine selects only rows
`WHERE normalized=0`; even with flags reset, the `normalized_entries` dedup index
+ `INSERT OR IGNORE` ignored re-inserts of rows the previous mapping produced. A
true **re-apply** therefore clears *and* re-runs the affected feeds:
`delete_normalized_for_sources(sources)` (`backend.normalizer.db`,
`DELETE … WHERE source_name IN (…)`, no-op on empty) + per-source flag reset,
exposed as `reapply_consolidated_to_sources(sources)`
(`backend.normalizer.smart_runner`) and wired into approval immediately after
`activate_consolidated_version`. The sweep is scoped to the mapping's own feeds.
A new `POST /api/smart-mappings/active/run` re-applies the active mapping on
demand (`409` when none active; returns `{reset_rows, **result}`), and
`GET /api/smart-mappings/active` now joins `proposal_id → proposals.proposal_name`
so the UI can label the active card by name. Frontend: the active card shows the
proposal name (fallback `version #id`), an expand/collapse chevron, and a **Run**
button; the proposal outcome filter defaults to `{pending_review, approved}`
(empty = show all); and LLM provider cards render collapsed to a one-line summary
(name · kind · base_url) with a chevron toggle, Delete staying in the header. See
ADR 0011.

**prompts-039 — normalizer run history, Run Now apply, active-mapping exposure &
archive lock.** Every normalizer execution is now recorded to a **separate**
SQLite file `data/run_history.db` (distinct from `normalized.db`, which is
drop+recreated on schema bumps) via `backend.normalizer.run_history`
(`record_run`/`list_runs`, schema v1, capped at the newest 500 rows).
`run_normalizer(trigger=…)` writes one row at completion — covering the three
call sites with one path: `POST /api/normalizer/run` (`manual`), the scheduler
`normalizer__auto` job (`schedule`), and `POST /api/smart-mappings/active/run`
(`reapply`); the disabled early-return records nothing and history writes are
best-effort. Smart applies capture `proposal_id`/`proposal_name`/`sources`;
auto/manual rows record only the mode. `POST /api/normalizer/run` now **re-applies**
in smart mode when a mapping is active (clear+reset its feeds, then run, returning
`reset_rows`) so "Run Now" is no longer a no-op after the first run — scheduled
smart runs stay incremental. `GET /api/smart-mappings/active` now returns the full
`{raw_field → canonical}` mapping, and `POST /api/smart-mappings/proposals/{id}/archive`
returns `409` for the proposal backing the active mapping. Frontend: a read-only
`RunHistoryModal` (time · trigger · proposal · feeds · result) opened from both
Normalizer Settings and the active card; the expanded active card renders the
full mapping and locks archive on the active proposal; the Mode select widens
`w-56 → w-72`; and the Normalized viewer shows an amber disabled banner via a
`normalizer-config` query. See ADR 0012.

**prompts-040 — navigation-proof, globally-consistent run state & viewer dynamic
columns.** Frontend-only. Every run button now shares one react-query mutation
(`useNormalizerRun` hook, key `['normalizer-run']`): `useRunNow()` →
`api.runNormalizer()` and `useRunActive()` → `api.smartMappings.runActive()`.
Because the MutationCache lives on the app-global `QueryClient`, an in-flight run
survives a route/sub-tab unmount, and **every** button reads its disabled/running
state from `useNormalizerRunning()` (`useIsMutating(['normalizer-run']) > 0`)
rather than a local `isPending`. This yields a single global lock: any run in
flight disables Settings "Run Now", the Smart Mappings active-card "Run", and the
viewer "Run & Apply" together (no concurrent runs), and the indicator no longer
resets when navigating away mid-run. Post-run cache invalidation
(`normalized-entries`, `normalizer-runs`, `normalizer-summary`,
`normalizer-config`, `smart-active`, `consolidated-active`) is registered once in
`main.tsx` via `setMutationDefaults(['normalizer-run'], { onSettled })` so it
fires even if the triggering component already unmounted. The Normalized viewer
table now tracks the active mapping: columns become
`['source_name', …unique(values(mapping))]` (with `normalized_at` trailing),
falling back to the static `CANONICAL_COLS` when no mapping is active; it also
gains a "Run & Apply" button (mode-aware `runNormalizer`) and renames "Refresh" →
"Refresh Table". Known trade-off: the lock/indicator is navigation-proof but the
inline per-run result counters remain local to the triggering component (the
durable record is Run History). See ADR 0013.

**prompts-049 — in-flight smart jobs survive navigation.** Smart Mappings
in-flight proposal jobs previously lived in page-local `useState`, so the
optimistic **Processing row** vanished when leaving and returning to the page (and
took the "processing above the default filter" rows with it — same root cause).
The new `useSmartActiveJobs` hook (`frontend/src/hooks/useSmartActiveJobs.ts`)
moves the active-job set into the app-global `QueryClient` cache (key
`['smart-active-jobs']`, `staleTime`/`gcTime` `Infinity`), so it survives the page
unmount exactly like `useNormalizerRun` (ADR 0013). The full `SmartJobHandle` is
preserved (the row shows sources/provider/model/scope/sample size, which the
generic active-job listing lacks). No `processing` proposal status is added —
ProcessingRows already render above the outcome filter, so restoring them on
remount fixes both reports. A persisted job whose `getJob` poll errors (404 after
the in-memory job TTL or a backend restart) is treated as terminal: the row
refreshes the list and removes itself instead of spinning forever. This is
session-scoped — jobs survive in-app nav but are dropped on a full reload. See
ADR 0021.

### Proposal scoring + auto-apply (021E-4)

Every proposal — manual, scheduled, or on_new_feed — runs through a
population-weighted coverage score before persistence:

```
population[f] = #sample rows where raw field f has a non-empty value
coverage(mapping) = sum(population[k] for k in mapping.keys()) / sum(population.values())
coverage_delta = coverage(existing ⊕ proposed) - coverage(existing)
```

where ⊕ is the existing-wins overlay-merge (021E-2). The triple
`(coverage_before, coverage_after, coverage_delta)` plus a per-field
population breakdown is persisted as `score_breakdown` JSON, with
`coverage_delta` mirrored to the scalar `score` column for cheap
filtering.

**Outcome decision** (in `backend/normalizer/smart_runner._decide_outcome`):

| Branch | Outcome |
|---|---|
| `trigger_reason == 'manual'` | `pending_review` (operator already chose to look) |
| empty proposal mapping | `pending_review` |
| `smart_mode.auto_apply.enabled = False` | `pending_review` |
| any raw_field in proposed is already in `manual_mappings[source]` | `pending_review` |
| `sample_count < 5` (hardcoded `_AUTO_APPLY_MIN_SAMPLE_SIZE`) | `pending_review` |
| `coverage_delta < smart_mode.auto_apply.min_coverage_delta` | `discarded_below_threshold` (logged + persisted, `status='rejected'`) |
| all gates passed | `auto_applied` (calls `approve_proposal_core(..., auto_applied=True)`) |

Discarded proposals stay in `proposals.db` for audit. The HTTP
`GET /api/smart-mappings/proposals` defaults to `outcome='pending_review'`
so they don't clutter the review queue; pass `?outcome=all` or any
specific outcome value to see them. The frontend "Include
auto-applied / discarded" toggle on SmartMappings flips this.

**Approve path unification:** the HTTP handler at
`POST /api/smart-mappings/proposals/{id}/approve` and the auto-apply
branch both call `backend.normalizer.smart_runner.approve_proposal_core`.
The `auto_applied` boolean column (1 iff outcome=`auto_applied`) is set
inside `update_proposal_status` so the simple `WHERE auto_applied=1`
audit query works.

**Config knobs added in 021E-4** (defaults preserve 021E-3 behaviour —
auto-apply is opt-in):

```yaml
smart_mode:
  auto_apply:
    enabled: false           # global gate (must be true to auto-apply)
    min_coverage_delta: 0.05 # 5 percentage-point coverage improvement
```

---

## Mapping Versioning (021F)

Until 021F, per-source field mappings lived only in
`config/normalizer-config.yaml` under `manual_mappings.<source>`.
Approving a smart-mode proposal mutated that file in place, so
there was no history, no rollback, no provenance, and no way to ask
"which mapping produced this normalized row?".

021F adds a separate **`data/mapping_versions.db`** as the source of
truth. The yaml file becomes a **write-through snapshot** so existing
operators / external readers keep working.

### Storage

```
data/mapping_versions.db          # 021F — operator-immutable history
  └─ mapping_versions             # one row per (source, version)
       id, source_name, mapping_json, origin, source_proposal_id,
       active, created_at, note
  └─ idx_mv_source                # source_name lookup
  └─ idx_mv_active_per_source     # UNIQUE WHERE active = 1
                                  # safety-net against double-active
  └─ schema_version               # _MAPPINGS_SCHEMA_VERSION = 1

config/normalizer-config.yaml     # write-through snapshot only
  manual_mappings:                # regenerated on every activate
    <source>: {...}               # always == active version's mapping
```

`origin` is one of `migration` | `proposal` | `manual`. The
`source_proposal_id` column carries the originating
`proposals.id` for `origin='proposal'` rows so the audit trail back
to the LLM run is preserved.

### Lifecycle

1. **First-boot migration** (idempotent). `backend/main.py` lifespan
   calls `init_mappings_db()` followed by
   `migrate_yaml_manual_mappings_once()`: every existing
   `manual_mappings.<source>` entry in yaml becomes a v1 row with
   `origin='migration'`, `active=1`. Re-runs are no-ops.
2. **Proposal approval** (manual or auto-apply) goes through
   `backend.normalizer.smart_runner.approve_proposal_core`, which
   reads the existing-side mapping from the currently-active version
   (yaml fallback only when none exists), merges existing-wins,
   creates a new `origin='proposal'` row, activates it, regenerates
   the yaml snapshot, then marks the source dirty
   (`reset_normalized_flag_for_source`).
3. **Rollback / arbitrary activation** uses
   `POST /api/normalizer/mappings/versions/{id}/activate`. Same
   side-effects as approval: atomic activate (`BEGIN IMMEDIATE` +
   the partial unique index demote-then-promote pattern), yaml
   snapshot regeneration, source flagged dirty.
4. **Re-normalization** is lazy: the scheduler's next normalizer tick
   picks up `normalized=0` rows and rebuilds them via
   `engine.run_normalizer`, threading the active
   `mapping_version_id` into each new `normalized_entries` row.

### Normalizer changes

`backend/normalizer/db.py` schema bumped **v2 → v3**: added the
`mapping_version_id INTEGER` housekeeping column on
`normalized_entries`. The bump triggers the existing drop+recreate
pipeline so rows produced before 021F are rebuilt with a non-null
version id. `query_normalized(..., mapping_version_id=...)` filters
rows by version.

`engine.run_normalizer` consults `get_active_version(source)` per
run (cached in an `active_cache` dict) with precedence:

```
active mapping_version → yaml fallback → auto mode
```

Auto-mode rows carry `mapping_version_id=NULL` — no mapping
produced them, so the dropdown filter correctly skips them.

### HTTP surface

| Route | Purpose |
|---|---|
| `GET /api/normalizer/mappings/versions?source=` | List newest-first; optional source filter |
| `GET /api/normalizer/mappings/versions/{id}` | Row + three-bucket diff vs. currently-active version |
| `POST /api/normalizer/mappings/versions/{id}/activate` | Promote + snapshot + mark dirty; returns `{version_id, source, reset_rows}` |
| `GET /api/normalizer/mappings/diff?from={id}&to={id}` | Three-bucket diff between any two versions |
| `GET /api/normalizer/entries?mapping_version_id={id}` | Existing route extended with version filter |

`POST /api/smart-mappings/proposals/{id}/approve` response gained
`mapping_version_id` and `reset_rows` keys (same approve_proposal_core
path).

### Frontend

* **Viewer → Normalized tab** got a per-source mapping-version
  dropdown next to the source filter (disabled until a source is
  picked).
* **Configuration → Normalizer tab** is now split into two
  sub-tabs: `Settings` (the original config card) and `Mapping
  versions` (`MappingVersionsPanel`) with a source dropdown,
  version list (active badge, origin label, proposal back-ref), and
  a side panel showing the selected mapping plus a three-bucket
  diff against the active version; non-active rows expose an
  Activate button.

See `docs/decisions/0001-mapping-versioning-storage.md` for the
rationale behind the separate-DB / per-source-granularity /
yaml-as-snapshot choices.

---

## Activity Log (021G)

Configuration → Normalizer → **Activity** sub-tab
(`src/pages/configuration/ActivityTab.tsx`) is a read-only audit
view of smart-mode proposal history. It reuses
`GET /api/smart-mappings/proposals?outcome=all` (already returns
everything needed after 021E-4) so 021G added no new backend
endpoint.

Columns: timestamp, source, trigger reason, provider / model,
outcome (single colour-coded badge), Δ coverage,
mapping_version. Filters: source dropdown + outcome multi-select.
Default sort: timestamp DESC (backend ordering). Client-side
pagination at 50 rows per page.

The `mapping_version` column links each approved / auto-applied
proposal to the `mapping_versions.id` it produced. Backed by the v3
`proposals.mapping_version_id` column written by
`approve_proposal_core`; clicking the link deep-links the operator
into the **Mapping versions** sub-tab with that source + version
pre-selected (via `initialSource` / `initialVersionId` props on
`MappingVersionsPanel`, cleared by an `onDeepLinkConsumed` callback
so the panel doesn't override subsequent operator clicks). Rows that
never produced a version (pending / rejected / discarded_below_threshold
/ error) render `—`.

---

## Client-side Normalized Feeds viewer (prompts-043)

`src/components/NormalizedTable.tsx` is a **client-side viewer**. It reads
`pagination_max` (the `['pagination-max']` query →
`GET /api/app/pagination-max`) and fetches up to that many rows **once**
(`api.getNormalizedEntries({ limit: paginationMax })`), then derives all
presentation in the browser — no per-interaction round-trips. The entries query
no longer sends source / search / mapping-version parameters.

In-browser controls:

* **Page size** dropdown (50/100/150/200, default 100) with Prev/Next and a
  "Page X of Y" indicator.
* **Search** row committed only on the Search button / Enter (`appliedSearch`
  vs the live `searchInput`); matches across all columns.
* **Per-field filters** (prompts-044: collapsed by default behind a "Filters"
  toggle): a `<select>` when a column's distinct value count is > 0 and ≤ 25
  (`isDropdownCol`), otherwise a contains-text `<input>`. The product/CVE/CVSS
  columns in `FORCE_DROPDOWN_COLS` (`cve_id, cvss_score, cvss_vector,
  affected_product, affected_vendor`) always render a dropdown regardless of
  cardinality; `title` is deliberately excluded (near-unique → text only).
  Filters AND together, then the committed search is applied. `aria-label`:
  `Filter by {col}`.
* **Column ordering** (prompts-044): `orderColumns()` sorts by `COLUMN_PRIORITY`
  — `source_name` → `published_at` → informative fields (title, indicator,
  indicator_type, cve_id, cvss_*, affected_product/vendor, severity, threat_type,
  actor, malware_family, campaign) → remaining columns alphabetically →
  `normalized_at` last. Canonical `source` is dropped from the active mapping's
  columns so it does not duplicate the housekeeping `source_name`.
* **Column picker**: collapsible, hidden by default ("Columns" toggle) with
  `aria-pressed` chips and "Add all" / "Clear all" bulk controls (Clear all keeps
  `source_name`). Column universe = active mapping canonical fields (else
  `CANONICAL_COLS`) with `normalized_at` appended.
* **Per-source colour chips**: fixed 10-entry `SOURCE_PALETTE`; `sourceColor()`
  hashes the source name to a palette class. Chip cells carry `data-source-chip`.

The 021F mapping-version filter and the normalizer-summary query were removed
from this surface; the backend version metadata is retained. Backed by the
`pagination_max` Application setting (`config/application.yaml`, default 1000,
bounds 50–100000, read live with no restart). The Summary tab table
(`SummaryTable.tsx`, wrapped at `max-w-3xl` in `Viewer.tsx`) was enlarged for
readability in prompts-044. See
`docs/decisions/0015-client-side-normalized-viewer-and-pagination-max.md` and
`docs/decisions/0016-normalized-viewer-refinements-and-summary-table-size.md`
(amends ADR-0013 / ADR-0015).

---

## Authentication, roles & branding (prompts-045)

Optional, off-by-default identity layer. With `auth_enabled` false (the default)
the app is fully open and behaves exactly as in earlier versions; nothing below
applies. The toggle resolves `SIMPLE_FEED_ENABLE_AUTH` env → `auth_enabled`
yaml → false, and the CLI flag `./threatfeeds-lite start --enable-auth` sets
the env var for one run. See
`docs/decisions/0017-authentication-module-roles-sessions-branding-logo.md`.

### Backend

* `backend/auth/db.py` — `users.db` with `users` and `sessions` tables;
  registered as a system DB (excluded from source-reset tooling).
* `backend/auth/service.py` — bcrypt (cost 12) password hashing, password byte
  cap 72, username regex `^[A-Za-z0-9._-]{1,40}$`, opaque `token_urlsafe(32)`
  sessions stored as SHA-256 hashes with a 12 h TTL, generic login error, and a
  per-`(username, ip)` login throttle (5 / 300 s) keyed on the real socket peer
  (`X-Forwarded-For` is deliberately ignored — it is attacker-controlled).
* `backend/api/routes_auth.py` — `/api/auth/{status,login,logout,me}`, self-service
  `change-password`, and admin user CRUD (`/api/auth/users…`) with last-admin
  guards. `_validate_password` is the single choke point for create/change/reset:
  it enforces a **configurable** policy — `password_min_length` (default 8, clamped
  `[8,64]`, leaving byte headroom under the cap) and `password_required_classes`
  (default 3, clamped `[1,4]`, over {lower, upper, number, symbol}) loaded by
  `loader.load_password_policy()` (which falls back to defaults rather than 500ing
  the public status endpoint on corrupt YAML) — plus the fixed 72-byte cap. Self-service change additionally rejects `new == current`
  (admin reset is unconstrained). The non-sensitive policy (counts only) is echoed
  by `GET /api/auth/status` for client-side mirroring. Login is **not**
  policy-validated, so legacy passwords keep working. A password change/reset
  revokes the user's sessions (`db.set_password`):
  admin reset evicts all of them; self-service change keeps the caller's own
  session and evicts the rest.
* `backend/auth/dependencies.py` + `backend/main.py` middleware — a single
  auth-enforcement middleware: when enabled, requests need a valid session
  cookie; `admin` is unrestricted, `normal` is limited to a safe-GET allowlist
  (`_NORMAL_GET_PREFIXES`: viewer/normalizer reads, smart-mapping reads,
  `/api/app/logo`, `/api/app/pagination-max`). Source `*-pull` reads are **not**
  in the allowlist — they carry per-source request headers (credentials), which
  are also redacted server-side (`__redacted__`) on every source read/echo.
  `GET /api/app/logo` is additionally exact-public for the unauthenticated login
  screen. Mutating `/api/app/*` routes add a `require_admin_when_enabled`
  dependency as defence in depth over the middleware.
* Session cookie is `HttpOnly` + `SameSite=Lax`, `Secure` over TLS (or when the
  `cookie_secure` config/env override forces it); logout deletes the server-side
  row (revocation). First start with auth on and no users provisions a default
  `admin` whose generated password is written to a git-ignored 0600 file
  (`data/first-run-admin-credentials.txt`); only the file path is logged.
* **Default-password recovery + forced change (prompts-047, ADR-0019).** The
  `users` schema (v2) carries `must_change_password`; `init_users_db` migrates
  legacy v1 DBs idempotently via `ALTER TABLE` (existing rows default 0, so no
  upgrade forces a change). The flag is set true **only** for generated default
  admin passwords — first-run `bootstrap_admin_if_empty` and
  `reset_admin_password`; `set_password` clears it by default, so ordinary
  self-change and admin resets of other users do not force a change.
  `./threatfeeds-lite --reset-admin-password` (`cmd_reset_admin_password`,
  no uvicorn) regenerates the `admin` credential offline — creating the row if
  missing, else resetting and evicting its sessions — reuses the 0600 credentials
  file, and prints the new password + file path to **stdout** (operator-initiated;
  never through the logger). While the flag is set, the `main.py` middleware
  restricts the user to the `_SELF_PATHS` allowlist (`/api/auth/{me,password,
  logout}`) and returns `403 "Password change required"` elsewhere; changing the
  password clears the flag and lifts the gate.

### Branding logo

`routes_app.py` adds `GET /api/app/logo-info`, public `GET /api/app/logo`
(FileResponse, `nosniff` + `no-cache`, traversal-guarded), multipart
`POST /api/app/logo` (raster only — PNG/JPEG/WebP/GIF, ≤ 2 MiB; **SVG rejected**
to avoid stored-XSS; the type is confirmed by magic-byte sniffing, not the
client `Content-Type`), and `DELETE`. The path is persisted as `logo_path`
(validated relative, under `data/branding/`, no `..`).

When no logo is uploaded, the shared `BrandLogo` component renders a **default
brand mark** (prompts-048, ADR-0020) — `BrandMark.tsx`, an inline-SVG security
shield enclosing RSS-style feed waves (threat intel + feed tracking), white on
the `bg-brand-600` badge — used identically on the login screen, in the sidebar,
and in the Configuration preview. The same artwork ships as a self-contained,
hard-coloured favicon at `frontend/public/favicon.svg`, referenced from
`index.html` with a relative `href` (prefix-safe) and served from `dist/` by the
existing SPA static catch-all (no backend route).

### Frontend

`src/auth/{context.ts,AuthContext.tsx,useAuth.ts}` (split for react-refresh) host
`AuthProvider`, which bootstraps from `GET /api/auth/status` and, when enabled,
`/api/auth/me`; `isAdmin = !authEnabled || role==='admin'`. `ProtectedLayout`
guards the shell and redirects to `/login` preserving the requested path (with an
absolute `/viewer` fallback), and additionally exposes the policy via
`passwordPolicy`. When the authenticated user's `must_change_password` is set
(prompts-047), `ProtectedLayout` instead renders a full-screen forced
change-password step (the shared `ChangePasswordCard` in `mode="self"` plus a
logout button, shell suppressed) and reloads `/api/auth/me` via `refresh()` on
success; this mirrors the server-side `_SELF_PATHS` gate. Per-route privilege is enforced in `App.tsx`: `RequireAdmin`
wraps `configuration` + `normalizer`, `RequireAuthEnabled` wraps `account`; an
unauthorised hit redirects to the **absolute, basename-aware** `/viewer` (a
relative target would wrongly resolve under the current path). The API client
funnels all `401`s through one handler. `pages/Login.tsx` is rendered outside the
shell. Self-service **Account is now its own top-level page** (`pages/Account.tsx`,
in `KNOWN_ROUTES`), not a Configuration tab; User Management stays an admin-only
Configuration tab. A single `ChangePasswordCard` (`mode: 'self' | 'admin'`) backs
both the Account page and the reset modal, and `passwordPolicy.ts` mirrors the
backend rules for inline validation (`CreateUserForm` and both change/reset flows
require a confirm-match field). The collapsible `Sidebar` (state persisted to
`localStorage` `sfi.sidebar.collapsed`) filters entries by `adminOnly` AND
`authOnly` (hiding Configuration/Normalizer from `normal` users and Account in
open mode), shows a logout footer, and renders the configured logo via the shared
`BrandLogo` component (also used on the login screen) backed by the `['logo-info']`
query.

**prompts-049 — delete confirmation + sidebar toggle placement.** Deleting a user
in `UserManagementTab` now requires confirmation: the trash button *arms* a
per-row inline `role="alertdialog"` panel (testids `delete-confirm-<id>` /
`delete-confirm-yes-<id>`) with Confirm/Cancel; only Confirm fires the delete
mutation, which clears the armed state on success or error. This follows the
repo-wide destructive-action convention (no `window.confirm`, no generic modal;
same pattern as `LLMProvidersTab` delete and `SmartMappings` archive). Separately,
the `Sidebar` collapse toggle moves from the footer into the header (right of the
logo when expanded; stacked centered below it when collapsed); the footer keeps
only the version label, and the `sfi.sidebar.collapsed` key and toggle behaviour
are unchanged. See ADR 0021.
