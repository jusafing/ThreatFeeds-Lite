#!/usr/bin/env bash
# security-check.sh — basic security checks
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PROJECT_ROOT}/.venv"

echo "=== Secret scan (grep for common patterns) ==="
if git -C "${PROJECT_ROOT}" grep -n \
    -E "(password\s*=\s*['\"][^'\"]+['\"]|api_key\s*=\s*['\"][^'\"]+['\"]|secret\s*=\s*['\"][^'\"]+['\"])" \
    -- '*.py' '*.ts' '*.tsx' '*.json' '*.yaml' '*.yml' 2>/dev/null; then
    echo "WARNING: Potential hardcoded secrets found above. Review before committing."
else
    echo "No obvious hardcoded secrets found."
fi

echo ""
echo "=== Python dependency audit (pip-audit) ==="
if command -v uv &>/dev/null && [ -d "${VENV}" ]; then
    if uv run --python "${VENV}/bin/python" python -c "import pip_audit" 2>/dev/null; then
        uv run --python "${VENV}/bin/python" pip-audit -r "${PROJECT_ROOT}/backend/requirements.txt"
    else
        echo "pip-audit not installed."
        echo "  Install with: uv pip install pip-audit --python ${VENV}/bin/python"
    fi
elif [ -x "${VENV}/bin/pip-audit" ]; then
    "${VENV}/bin/pip-audit" -r "${PROJECT_ROOT}/backend/requirements.txt"
else
    echo "pip-audit not installed."
    echo "  Install with: uv pip install pip-audit --python .venv/bin/python"
    echo "  (or: .venv/bin/pip install pip-audit)"
fi

echo ""
echo "=== .env file check ==="
if find "${PROJECT_ROOT}" -maxdepth 1 -name ".env" ! -name ".env.example" 2>/dev/null | grep -q .; then
    echo "WARNING: .env file(s) found in project root. Ensure they are gitignored."
else
    echo "No unguarded .env files found."
fi

echo ""
echo "Security check complete."
