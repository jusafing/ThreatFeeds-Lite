#!/usr/bin/env bash
# Build the local ThreatFeeds Lite image: threatfeeds-lite/local
#
# The image is STANDALONE: the application source is fetched from GitHub at build
# time, so no repository build context is required. By default it pulls the
# latest "main" branch; override with TFL_REF (branch, tag, or commit-ish) for a
# reproducible build.
#
# Usage:
#   docker/build/build.sh [extra docker build args...]
#
# Environment overrides:
#   IMAGE_TAG   image tag to produce         (default: threatfeeds-lite/local)
#   TFL_REF     git ref to build from GitHub  (default: main)
#   TFL_REPO    source repository URL         (default: upstream GitHub repo)
#
# Examples:
#   docker/build/build.sh
#   TFL_REF=v1.0.0 docker/build/build.sh
#   docker/build/build.sh --no-cache
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE_TAG="${IMAGE_TAG:-threatfeeds-lite/local}"
TFL_REF="${TFL_REF:-main}"
TFL_REPO="${TFL_REPO:-https://github.com/jusafing/ThreatFeeds-Lite.git}"

echo "[build] Building ${IMAGE_TAG} from ${TFL_REPO} @ ${TFL_REF}"

# The Dockerfile uses no local context (source is cloned from GitHub), so the
# build context is just this directory — kept tiny on purpose.
exec docker build \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${IMAGE_TAG}" \
    --build-arg "TFL_REF=${TFL_REF}" \
    --build-arg "TFL_REPO=${TFL_REPO}" \
    "$@" \
    "${SCRIPT_DIR}"
