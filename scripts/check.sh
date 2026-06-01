#!/usr/bin/env bash
# check.sh — run lint and type checks for backend and frontend
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PROJECT_ROOT}/.venv"
FRONTEND="${PROJECT_ROOT}/frontend"

# Resolve Python runner — prefer uv, fall back to venv python directly
if command -v uv &>/dev/null && [ -d "${VENV}" ]; then
    RUN_PYTHON="uv run --python ${VENV}/bin/python python"
elif [ -x "${VENV}/bin/python" ]; then
    RUN_PYTHON="${VENV}/bin/python"
else
    echo "WARNING: no Python environment found — skipping backend checks."
    echo "         Run: ./threatfeeds-lite start (or stop/start) to set it up."
    RUN_PYTHON=""
fi

echo "=== Backend: Python syntax check ==="
if [ -n "${RUN_PYTHON}" ]; then
    ${RUN_PYTHON} -m py_compile \
        "${PROJECT_ROOT}"/backend/main.py \
        "${PROJECT_ROOT}"/backend/config/loader.py \
        "${PROJECT_ROOT}"/backend/db/manager.py \
        "${PROJECT_ROOT}"/backend/db/schema.py \
        "${PROJECT_ROOT}"/backend/ingestion/normaliser.py \
        "${PROJECT_ROOT}"/backend/ingestion/push_listener.py \
        "${PROJECT_ROOT}"/backend/ingestion/api_pull.py \
        "${PROJECT_ROOT}"/backend/ingestion/rss_pull.py \
        "${PROJECT_ROOT}"/backend/ingestion/local_feed.py \
        "${PROJECT_ROOT}"/backend/ingestion/remote_json.py
    echo "Syntax check passed."
fi

echo ""
echo "=== Frontend: TypeScript type check ==="
if [ -d "${FRONTEND}/node_modules" ]; then
    npx --prefix "${FRONTEND}" tsc --noEmit
    echo "TypeScript check passed."
else
    echo "WARNING: node_modules not found — skipping frontend checks."
    echo "         Run: cd frontend && npm install"
fi

echo ""
echo "All checks complete."
