#!/usr/bin/env bash
# test.sh — run backend (pytest) and frontend (vitest) test suites
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PROJECT_ROOT}/.venv"
FRONTEND="${PROJECT_ROOT}/frontend"

FAILED=0

echo "=== Backend tests (pytest) ==="
if command -v uv &>/dev/null && [ -d "${VENV}" ]; then
    cd "${PROJECT_ROOT}"
    PYTHONPATH="${PROJECT_ROOT}" \
        uv run --python "${VENV}/bin/python" pytest backend/tests -v --tb=short || FAILED=1
elif [ -x "${VENV}/bin/pytest" ]; then
    cd "${PROJECT_ROOT}"
    PYTHONPATH="${PROJECT_ROOT}" "${VENV}/bin/pytest" backend/tests -v --tb=short || FAILED=1
else
    echo "WARNING: no Python environment found — skipping backend tests."
    echo "         Run: ./threatfeeds-lite start to set up the environment."
fi

echo ""
echo "=== Frontend tests (vitest) ==="
if [ -d "${FRONTEND}/node_modules" ]; then
    npm --prefix "${FRONTEND}" run test || FAILED=1
else
    echo "WARNING: node_modules not found — skipping frontend tests."
    echo "         Run: cd frontend && npm install"
fi

echo ""
if [ "${FAILED}" -eq 0 ]; then
    echo "All tests passed."
else
    echo "One or more test suites FAILED." >&2
    exit 1
fi
