#!/usr/bin/env bash
#
# run_tests.sh — automated test runner for the ThreatFeeds Lite API client.
#
# Executes the T1-T9 API-client test plan against a running server, driving
# scripts/api_client.py. Every run creates a fresh results directory
# test-client-<epoch>/ containing:
#   - T1..T11 markdown files (one per test: command + captured response)
#   - script-run.log   the exact api_client.py invocation per test (password masked)
#   - execution.log    timestamps, per-test status/exit code, and any errors
#
# Connection and credentials are read from a .env file (default: ./.env.test,
# resolved next to this script). Push tests use the push account; read/query
# tests use the read account.
#
# Usage:
#   ./run_tests.sh [ENV_FILE]
#   ./run_tests.sh --env /path/to/.env.test
#
# The results directory is NOT committed (see .gitignore in this folder); it is
# for local verification only.

set -u

# ── locate ourselves ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
API_CLIENT="$(cd "${SCRIPT_DIR}/.." && pwd)/api_client.py"
GEN_EVENTS="${SCRIPT_DIR}/gen_events.py"

# Prefer the project virtualenv python, fall back to python3.
if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

# ── parse args ───────────────────────────────────────────────────────────────
ENV_FILE="${SCRIPT_DIR}/.env.test"
case "${1:-}" in
  --env)
    ENV_FILE="${2:?--env requires a path}" ;;
  "" )
    ;;
  -* )
    echo "Unknown option: $1" >&2
    echo "Usage: $0 [ENV_FILE] | $0 --env <path>" >&2
    exit 2 ;;
  * )
    ENV_FILE="$1" ;;
esac

# Resolve a relative ENV_FILE against the script dir for convenience.
if [ ! -e "${ENV_FILE}" ] && [ -e "${SCRIPT_DIR}/${ENV_FILE}" ]; then
  ENV_FILE="${SCRIPT_DIR}/${ENV_FILE}"
fi

if [ ! -r "${ENV_FILE}" ]; then
  echo "Error: env file not readable: ${ENV_FILE}" >&2
  exit 1
fi
if [ ! -f "${API_CLIENT}" ]; then
  echo "Error: api_client.py not found at ${API_CLIENT}" >&2
  exit 1
fi

# ── tolerant .env parser ─────────────────────────────────────────────────────
# Accepts `key=value` and `key:value`. Ignores blank lines and `#` comments.
# Splits on the FIRST delimiter only, so values may contain '=' or ':'.
HOST=""; PORT=""; USER_PUSH=""; PASS_PUSH=""; USER_READ=""; PASS_READ=""
while IFS= read -r raw || [ -n "${raw}" ]; do
  line="${raw%$'\r'}"                      # strip trailing CR
  case "${line}" in
    ''|'#'*) continue ;;
  esac
  # Find the first '=' and first ':'; use whichever comes earliest.
  rest_eq="${line#*=}"; rest_co="${line#*:}"
  if [ "${rest_eq}" != "${line}" ] && { [ "${rest_co}" = "${line}" ] || [ ${#rest_eq} -ge ${#rest_co} ]; }; then
    key="${line%%=*}"; val="${rest_eq}"
  else
    key="${line%%:*}"; val="${rest_co}"
  fi
  # trim surrounding whitespace
  key="$(printf '%s' "${key}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  val="$(printf '%s' "${val}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "${key}" in
    host)      HOST="${val}" ;;
    port)      PORT="${val}" ;;
    user_push) USER_PUSH="${val}" ;;
    pass_push) PASS_PUSH="${val}" ;;
    user_read) USER_READ="${val}" ;;
    pass_read) PASS_READ="${val}" ;;
  esac
done < "${ENV_FILE}"

missing=""
[ -n "${HOST}" ]      || missing="${missing} host"
[ -n "${PORT}" ]      || missing="${missing} port"
[ -n "${USER_PUSH}" ] || missing="${missing} user_push"
[ -n "${PASS_PUSH}" ] || missing="${missing} pass_push"
[ -n "${USER_READ}" ] || missing="${missing} user_read"
[ -n "${PASS_READ}" ] || missing="${missing} pass_read"
if [ -n "${missing}" ]; then
  echo "Error: ${ENV_FILE} is missing required key(s):${missing}" >&2
  echo "Expected keys: host, port, user_push, pass_push, user_read, pass_read" >&2
  echo "See .env.example for the canonical format." >&2
  exit 1
fi

BASE_URL="http://${HOST}:${PORT}"

# ── results directory ────────────────────────────────────────────────────────
EPOCH="$(date +%s)"
OUT_DIR="${SCRIPT_DIR}/test-client-${EPOCH}"
mkdir -p "${OUT_DIR}"
RUN_LOG="${OUT_DIR}/script-run.log"
EXEC_LOG="${OUT_DIR}/execution.log"
EVENTS_FILE="${OUT_DIR}/events.json"
: > "${RUN_LOG}"
: > "${EXEC_LOG}"

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
exec_log() { printf '%s %s\n' "$(ts)" "$*" >> "${EXEC_LOG}"; }

exec_log "run started: env=${ENV_FILE} base_url=${BASE_URL} out_dir=${OUT_DIR}"
exec_log "python=${PYTHON}"

# ── per-test driver ──────────────────────────────────────────────────────────
# run_test <num> <slug> <title> <role> <description> -- <api_client args...>
# <role> is "push" or "read"; selects which credentials to use.
run_test() {
  local num="$1" slug="$2" title="$3" role="$4" desc="$5"
  shift 5
  [ "$1" = "--" ] && shift
  local user pass
  if [ "${role}" = "push" ]; then user="${USER_PUSH}"; pass="${PASS_PUSH}"
  else user="${USER_READ}"; pass="${PASS_READ}"; fi

  local md="${OUT_DIR}/T${num}-${slug}.md"
  local args=( --url "${BASE_URL}" --username "${user}" --password "${pass}" "$@" )
  # Display version with the password masked for the run log + markdown.
  local disp=( api_client.py --url "${BASE_URL}" --username "${user}" --password '***' "$@" )

  printf 'T%s [%s] %s\n' "${num}" "${role}" "${disp[*]}" >> "${RUN_LOG}"
  exec_log "T${num} ${slug} start (role=${role}, user=${user})"

  local out err rc
  out="$("${PYTHON}" "${API_CLIENT}" "${args[@]}" 2> "${OUT_DIR}/.stderr")"
  rc=$?
  err="$(cat "${OUT_DIR}/.stderr")"
  rm -f "${OUT_DIR}/.stderr"

  # Pretty-print JSON stdout when possible; otherwise keep it raw.
  local pretty
  if [ -n "${out}" ] && pretty="$(printf '%s' "${out}" | "${PYTHON}" -m json.tool 2>/dev/null)"; then
    :
  else
    pretty="${out}"
  fi

  {
    printf '# T%s — %s\n\n' "${num}" "${title}"
    printf '%s\n\n' "${desc}"
    printf -- '- **Role/account:** %s (`%s`)\n' "${role}" "${user}"
    printf -- '- **Base URL:** `%s`\n' "${BASE_URL}"
    printf -- '- **Exit code:** `%s`\n\n' "${rc}"
    printf '## Command\n\n```bash\n%s\n```\n\n' "${disp[*]}"
    if [ -n "${pretty}" ]; then
      printf '## Response\n\n```json\n%s\n```\n' "${pretty}"
    fi
    if [ -n "${err}" ]; then
      printf '\n## Errors / diagnostics\n\n```\n%s\n```\n' "${err}"
    fi
  } > "${md}"

  if [ "${rc}" -eq 0 ]; then
    exec_log "T${num} ${slug} ok (exit=0)"
  else
    exec_log "T${num} ${slug} FAILED (exit=${rc})"
    [ -n "${err}" ] && exec_log "T${num} ${slug} stderr: $(printf '%s' "${err}" | tr '\n' ' ')"
  fi
  return 0
}

# ── T1: generate + push events ───────────────────────────────────────────────
exec_log "generating events -> ${EVENTS_FILE}"
if "${PYTHON}" "${GEN_EVENTS}" > "${EVENTS_FILE}" 2>> "${EXEC_LOG}"; then
  exec_log "generated $(wc -c < "${EVENTS_FILE}") bytes of events"
else
  exec_log "ERROR: gen_events.py failed; T1 will have no payload"
fi
printf '# gen_events.py %s > %s\n' "${GEN_EVENTS}" "${EVENTS_FILE}" >> "${RUN_LOG}"

run_test 1 push-events "Push simulated events to the listener" push \
  "Generate randomized threat events and POST them to the listener as the push (sender) account." \
  -- send --file "${EVENTS_FILE}"

# ── T2: verify the push landed ───────────────────────────────────────────────
run_test 2 verify-push "Verify pushed events landed" read \
  "Read back the raw table as the read (normal) account to confirm the pushed events were indexed." \
  -- get-raw --max 10

# ── T3: raw events ───────────────────────────────────────────────────────────
run_test 3 raw-10 "Fetch 10 raw events" read \
  "Fetch up to 10 rows from the raw events table." \
  -- get-raw --max 10

# ── T4: normalized events ────────────────────────────────────────────────────
run_test 4 normalized-10 "Fetch 10 normalized events" read \
  "Fetch up to 10 rows from the normalized table." \
  -- get-normalized --max 10

# ── T5: list feeds ───────────────────────────────────────────────────────────
run_test 5 list-feeds "List available feeds" read \
  "List available feeds with per-source entry counts (raw catalogue)." \
  -- list-feeds

# ── T6: search ───────────────────────────────────────────────────────────────
run_test 6 search-npm "Full-text search for npm" read \
  "Server-side full-text search of the raw table for the term \"npm\"." \
  -- search npm --type raw --max 20

# ── T7-T9: natural-language queries (require an LLM provider on the server) ───
run_test 7 query-npm-supplychain "NL query: npm supply-chain compromise" read \
  "Natural-language query translated server-side by the LLM. Requires an LLM provider on the server (else HTTP 503)." \
  -- query "supply-chain compromise or malicious packages in the npm registry" --type raw --max 20

run_test 8 query-critical-cves "NL query: critical 2026 CVEs by vendor" read \
  "Natural-language query for critical 2026 CVEs affecting a vendor/product. Requires an LLM provider (else HTTP 503)." \
  -- query "critical CVEs from 2026 affecting nginx" --type normalized --max 25

run_test 9 query-actor-indicators "NL query: high-severity indicators by actor" read \
  "Natural-language query for high-severity indicators attributed to a threat actor. Requires an LLM provider (else HTTP 503)." \
  -- query "high severity indicators attributed to the Lazarus group" --type normalized --max 25

# ── T10-T11: exact-column field search (raw + normalized) ────────────────────
# These need no LLM — they exercise the deterministic ?field=name=value filter
# added in issue_local_02, querying directly from the raw and normalized stores.
run_test 10 field-raw-severity "Field search from raw: severity=critical" read \
  "Fetch raw rows filtered by an exact column value (severity=critical) using the repeatable --field flag. Unknown columns are ignored server-side." \
  -- get-raw --field severity=critical --max 25

run_test 11 field-normalized-indicator-type "Field search from normalized: indicator_type=ipv4-addr" read \
  "Fetch normalized rows filtered by an exact column value (indicator_type=ipv4-addr) from the normalized store. Validated against the normalized schema." \
  -- get-normalized --field indicator_type=ipv4-addr --max 25

exec_log "run finished: results in ${OUT_DIR}"
echo "Done. Results: ${OUT_DIR}"
echo "  - markdown:      T1..T11-*.md"
echo "  - command log:   script-run.log"
echo "  - execution log: execution.log"
