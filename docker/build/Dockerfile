# syntax=docker/dockerfile:1
#
# ThreatFeeds Lite — local container image (threatfeeds-lite/local)
#
# Design (deliberately minimal and repo-independent):
#   * A MINIMAL SYSTEM with only the toolchain the app's own startup script
#     needs at runtime: bash, git, Node.js + npm (frontend build), Python 3 +
#     venv + pip, and uv (fast dependency installs).
#   * The application is fetched with `git clone` from GitHub at BUILD time and
#     SHIPPED as-is under the user's home (/home/threatfeeds).
#   * NOTHING is installed or built manually here — no `pip install`, no
#     `npm ci/build`. The app's startup script (./threatfeeds-lite) performs ALL
#     dependency installation and the frontend build at runtime (first start).
#   * The image never copies or references the repository's app config; the repo
#     is used only to download the app, which is then run via its own script.
#
# Build (standalone — no app build context is consumed):
#   docker build -f docker/build/Dockerfile -t threatfeeds-lite/local docker/build
#   # or use the helper:  docker/build/build.sh
#
# Pin a branch/tag for a reproducible source snapshot:
#   docker build --build-arg TFL_REF=v1.2.3 -f docker/build/Dockerfile -t threatfeeds-lite/local docker/build

ARG TFL_REPO=https://github.com/jusafing/ThreatFeeds-Lite.git
ARG TFL_REF=main

# Node base provides Node.js 20 + npm (needed by the startup script's frontend
# build). Everything else is added below.
FROM node:20-bookworm-slim

ARG TFL_REPO
ARG TFL_REF

ENV APP_HOME=/home/threatfeeds \
    HOME=/home/threatfeeds \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Minimal toolchain only (NOT the app's dependencies):
#   git           — clone the app at build time
#   python3/venv/pip — the startup script provisions the backend venv
#   curl/ca-certs — fetch the uv installer + container HEALTHCHECK probe
#   gosu          — drop from root to the app user in the entrypoint
# uv is installed system-wide so the startup script prefers it over pip.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        python3 \
        python3-venv \
        python3-pip \
        curl \
        ca-certificates \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv --version

# Ship the whole application: shallow-clone the requested ref and place its
# contents at the app home (the home dir already exists because the uv installer
# ran with HOME there, so clone via a temp dir and copy the tree in).
RUN git clone --depth 1 --branch "${TFL_REF}" "${TFL_REPO}" /tmp/app \
    && mkdir -p "${APP_HOME}" \
    && cp -a /tmp/app/. "${APP_HOME}/" \
    && rm -rf /tmp/app \
    # Pre-create the runtime data/log dirs so the named volumes mounted there
    # inherit the unprivileged user's ownership (no root entrypoint required).
    && mkdir -p "${APP_HOME}/data" "${APP_HOME}/logs"

# Unprivileged user/group with a fixed uid/gid whose home is the cloned app dir
# (already present, so do NOT --create-home). Then hand it ownership of the tree.
RUN groupadd --gid 10001 threatfeeds \
    && useradd --uid 10001 --gid 10001 --home-dir ${APP_HOME} --shell /bin/bash threatfeeds \
    && chown -R threatfeeds:threatfeeds "${APP_HOME}"

WORKDIR ${APP_HOME}

# Entrypoint fixes ownership of the bind-mounted data/logs dirs as root, then
# drops to the unprivileged "threatfeeds" user before running the startup script.
# (It is the only file taken from the build context.)
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# Liveness probe. The very first start installs deps + builds the frontend, so
# allow a generous start period before failures count.
HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Run the app EXCLUSIVELY through its own startup script. `start --dev` runs
# uvicorn in the FOREGROUND (so the script stays PID 1 and the container stays
# up), builds + serves the frontend, and `--enable-auth` turns authentication on
# (first run bootstraps an "admin" account, printing the password to this log and
# writing data/first-run-admin-credentials.txt, mode 0600).
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["./threatfeeds-lite", "start", "--dev", "--enable-auth", "--bind", "0.0.0.0:8000"]
