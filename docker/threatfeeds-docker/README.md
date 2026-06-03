# ThreatFeeds Lite — Docker

Run ThreatFeeds Lite as a single local container. The image is a **minimal
system**: it `git clone`s the application from GitHub at build time and ships it
unchanged. The application's **own startup script** (`./threatfeeds-lite`)
installs all dependencies and builds the frontend on the **first start**, then
runs `uvicorn` in the foreground. The Docker image and Compose file do **not**
install dependencies, build the app, or reference the repository's app config —
the repo is used only to download the app, which is then run via its script.

- **Image:** `threatfeeds-lite/local`
- **Base:** `node:20-bookworm-slim` + a minimal toolchain (`git`, Python 3 +
  venv + pip, `uv`, `curl`)
- **Source:** fetched from **GitHub at build time** (no local checkout required)
- **Started by:** the app's `./threatfeeds-lite start --dev --enable-auth --bind 0.0.0.0:8000`
- **Dependency install + frontend build:** done by the startup script at the
  **first start** (the container needs outbound internet then)
- **Authentication:** **enabled by default** (`--enable-auth`)
- **Runs as:** unprivileged user `threatfeeds` (uid/gid `10001`), app under
  `/home/threatfeeds`
- **Listens on:** `0.0.0.0:8000`

## Layout

```
docker/
  build/
    Dockerfile     # minimal image: clones + ships the app from GitHub
    build.sh       # builds threatfeeds-lite/local
  threatfeeds-docker/
    docker-compose.yml   # runs the app via its startup script; host-dir mounts
    README.md            # this file
```

## How it works

The `Dockerfile` installs **only the toolchain** the startup script needs
(Node.js + npm, Python 3 + venv + pip, `uv`, `git`, `curl`) and then
`git clone`s the application into the user's home (`/home/threatfeeds`). It does
**not** run `pip install`, `npm ci`, or build the frontend, and it never copies
the repository's config.

On the **first** `docker compose up`, the startup script:

1. provisions a Python virtual environment and installs the backend
   dependencies (via `uv`, falling back to `pip`),
2. installs the frontend packages and builds the SPA (`npm`), then
3. runs `uvicorn` in the **foreground** serving the API and the built frontend.

Because the install + build happen at runtime, the **first start is slow and
requires internet access** (PyPI + npm). Subsequent starts of the same container
reuse the work already done inside it.

Two build args control the source:

| Build arg  | Default                                            | Purpose                      |
|------------|----------------------------------------------------|------------------------------|
| `TFL_REF`  | `main`                                             | Branch, tag, or ref to build |
| `TFL_REPO` | `https://github.com/jusafing/ThreatFeeds-Lite.git` | Source repository URL        |

> **Reproducibility:** the default `main` build tracks the branch HEAD at build
> time. Pin `TFL_REF` to a tag or commit for a deterministic source snapshot.

## Build the image

```bash
docker/build/build.sh
```

Produces `threatfeeds-lite/local` from the latest `main`. Extra `docker build`
flags pass through, e.g. `docker/build/build.sh --no-cache`. Build a specific
ref with `TFL_REF=v1.0.0 docker/build/build.sh`.

To build by hand (the trailing `docker/build` is just a tiny context — the
source is cloned from GitHub):

```bash
docker build -f docker/build/Dockerfile -t threatfeeds-lite/local \
  --build-arg TFL_REF=main docker/build
```

## Run with Docker Compose

From this directory (`docker/threatfeeds-docker/`):

```bash
docker compose up -d
docker compose logs -f     # watch the first-run install/build + admin password
```

This starts a container named **`threatfeeds-lite`**, publishes **port 8000**,
and persists state in two **host directories** created next to
`docker-compose.yml`:

- `./threatfeeds-data` → `/home/threatfeeds/data` (SQLite databases + the
  first-run admin credential file)
- `./threatfeeds-logs` → `/home/threatfeeds/logs` (`app.log` / `audit.log`)

> These host folders are created root-owned by Docker; the container's
> entrypoint briefly runs as root to `chown` them to the app user (uid `10001`),
> then drops privileges before launching the startup script.

> The first start takes a while (it installs dependencies and builds the
> frontend). Follow `docker compose logs -f` and wait for `Application startup
> complete`; `docker ps` shows `healthy` once `/api/health` responds.

Open the UI / API:

- UI: <http://localhost:8000>
- Health: <http://localhost:8000/api/health> → `{"status":"ok","version":"0.1.0"}`
- API docs: <http://localhost:8000/docs>

Manage the container:

```bash
docker compose logs -f       # follow logs
docker compose ps            # status
docker compose down          # stop & remove (./threatfeeds-data and ./threatfeeds-logs persist)
```

## Authentication & the admin password

Authentication is **enabled by default** (`--enable-auth`). On the **first
start** the app bootstraps an `admin` account with a randomly generated
password. It is:

- **printed to the container log** (`docker compose logs threatfeeds-lite`), and
- **written to** `data/first-run-admin-credentials.txt` (mode `0600`, in the
  `threatfeeds-data` volume).

Read it, then log in at <http://localhost:8000> as `admin`:

```bash
docker compose logs threatfeeds-lite | grep -A4 'first-run admin account'
```

You are **required to change this password on first login**. Delete the
credential file afterwards.

> **Security note:** the generated password is printed to stdout and therefore
> captured in `docker compose logs`. Change it on first login and consider
> clearing the logs once you have stored it.

### Reset / regenerate the admin password

Use the startup script's own reset command in the running container:

```bash
docker compose exec threatfeeds-lite ./threatfeeds-lite --reset-admin-password
```

This sets a fresh random password, prints it, rewrites
`data/first-run-admin-credentials.txt`, and forces a change on the next login.

## Run without Compose

```bash
docker run -d --name threatfeeds-lite -p 8000:8000 \
  -v "$PWD/threatfeeds-data:/home/threatfeeds/data" \
  -v "$PWD/threatfeeds-logs:/home/threatfeeds/logs" \
  threatfeeds-lite/local
```

## Quick test

```bash
curl -fsS http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0"}
```

The container also has a built-in `HEALTHCHECK` (with a generous start period to
cover the first-run install/build); `docker ps` shows `healthy` once the app is
up.

## Notes

- **Runs as the unprivileged `threatfeeds` user (uid/gid `10001`).** The
  entrypoint starts as root only to fix ownership of the bind-mounted
  `./threatfeeds-data` / `./threatfeeds-logs` directories, then drops privileges
  via `gosu` — the application process always runs unprivileged.
- **Authentication is on by default.** The startup script runs with
  `--enable-auth`. To run open instead, drop `--enable-auth` from the `command`
  in `docker-compose.yml`.
- **No secrets baked in.** The real `config/llm-providers.yaml` (which may hold
  API keys) is not tracked in the repository, so it is never cloned into the
  image; the app runs with the LLM integration disabled unless you provide that
  config at runtime.
- **Fresh volumes** start with empty databases — expected on first run.
