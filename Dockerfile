# Open Net Worth — multi-stage build.
#   1) frontend-builder: builds the React/Vite SPA (frontend/dist/).
#   2) cli-tools: installs the Claude Code / Codex / OpenCode CLI providers (no Node in the
#      runtime image otherwise).
#   3) runtime: a slim Python image that serves the API and the built frontend
#      from the SAME process (backend/main.py mounts frontend/dist/ when it's
#      present) — no separate nginx/static container needed.
#
# `AUTH_PASSWORD=... docker compose up` from a clean clone is the target — see
# docker-compose.yml for the default port/volume wiring and why the password is required.
# Releases publish this image to ghcr.io/open-net-worth-ai/open-net-worth (release.yml).

# ── Stage 1: frontend build ───────────────────────────────────────────────────
# --platform=$BUILDPLATFORM: the JS build's output is architecture-independent, so a
# multi-arch release build runs it natively instead of under QEMU (npm ci + vite under
# emulation is the slowest step by far). cli-tools below MUST stay on the target platform —
# its `node` binary is copied into the runtime image.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend-builder
WORKDIR /app/frontend

# Install deps first so this layer caches across backend-only changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# The SPA imports two dependency-free modules shared with the desktop shell
# (frontend/src/platform/desktop.ts, utils/artifactFrame.ts → ../../../desktop/src/…).
COPY desktop/src/contracts.ts desktop/src/artifact-document.ts /app/desktop/src/
RUN npm run build

# The product docs, built to be served by the app at /docs — this is what the in-app Help
# modal shows. Needs docs/product/ (the corpus) as well as docs-site/ itself, since the site
# reads the markdown from one directory up at build time.
WORKDIR /app/docs-site
COPY docs-site/package.json docs-site/package-lock.json ./
RUN npm ci
COPY docs-site/ ./
COPY docs/product/ /app/docs/product/
RUN npm run build:embed

# ── Stage 2: Claude Code CLI + Codex CLI + OpenCode CLI ───────────────────────
# All three are local-CLI AI providers (backend/ai/models/claude_cli_provider.py,
# codex_cli_provider.py, opencode_cli_provider.py) — the runtime image has no Node of
# its own, so they're installed into a throwaway node image and copied over below.
# Their SUBSCRIPTION auth (as opposed to an API key entered in Settings) lives in
# host files this image can't see by itself — see docker-compose.yml for the
# bind-mount that supplies it. opencode-ai pulls its platform binary in as an
# optional dependency, which is one more reason this stage builds on the TARGET
# platform.
FROM node:22-slim AS cli-tools
RUN npm install -g @anthropic-ai/claude-code @openai/codex opencode-ai

# ── Stage 3: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user. HOME matters here specifically: the app's zero-config default
# data/cache/log directories resolve to ~/.open-net-worth (backend/core/config.py)
# — creating a real user with a real HOME means the default just works with the
# named volume mounted below, with no DATA_DIR/CACHE_DIR/LOGS_DIR env override.
RUN groupadd --gid 1000 onw \
    && useradd --uid 1000 --gid onw --create-home --shell /bin/bash onw

WORKDIR /app

# System deps: curl for the healthcheck, procps (ps) for the Codex CLI, which tracks its
# background app-server with it and refuses to start without. Everything else in the lock ships
# manylinux/musllinux wheels for this base image, so no build toolchain is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl procps \
    && rm -rf /var/lib/apt/lists/*

# Installed from the hash-pinned lock (the same one the desktop runtime uses, compiled from
# requirements.txt — regeneration command in desktop/README.md), never from the open `>=`
# ranges: every artifact is pinned to a sha256, so a compromised or yanked-and-republished
# release cannot enter the image. --no-deps because the lock already holds the full closure.
COPY desktop/runtime-requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

# App source. .dockerignore keeps this from pulling in .venv/, node_modules/,
# frontend/dist/ (built fresh in stage 1, not reused from the host), test data,
# and anything under backend/data/ that isn't the tracked seed content.
# (alembic.ini itself is NOT needed here — db/alembic_config.py builds Alembic's
# config programmatically, pointed at backend/db/alembic/, for the boot-time
# migration path; alembic.ini is only used by the standalone `alembic` CLI, a
# dev-only tool this image never runs.)
COPY backend/ ./backend/
# The source is copied, not pip-installed, so there is no distribution metadata for
# backend/core/version.py to read: bake pyproject's version (the one source of truth) into the
# file it falls back to, so /api/health inside the image reports the real release version.
COPY pyproject.toml ./pyproject.toml
RUN mkdir -p backend/_bundled \
    && python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])" \
       > backend/_bundled/VERSION \
    && rm pyproject.toml
# backend/services/product_docs.py reads docs/product/**/*.md at runtime for the
# in-app Help corpus (core/paths.py::PRODUCT_DOCS_DIR) — a real runtime
# dependency, not documentation-only.
COPY docs/product/ ./docs/product/
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
COPY --from=frontend-builder /app/docs-site/dist-embed ./docs-site/dist-embed

# Node + the three CLIs, copied whole from the throwaway build stage above —
# `claude`/`codex`/`opencode`'s bin scripts run on this `node`, so it has to come along.
# npm installs each CLI's /usr/local/bin entry as a SYMLINK into its package's
# bin/ dir, and each script locates its own package root relative to __dirname
# — so `COPY --from=` the symlink FILE dereferences it into a flattened copy at
# the wrong path and breaks that resolution (observed: codex then can't find
# its own bundled platform binary). Copy node_modules, then recreate the
# symlinks ourselves, matching npm's own targets exactly.
COPY --from=cli-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=cli-tools /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude \
    && ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && ln -s ../lib/node_modules/opencode-ai/bin/opencode.exe /usr/local/bin/opencode \
    && test -e /usr/local/bin/claude && test -e /usr/local/bin/codex \
    && test -e /usr/local/bin/opencode

# Pre-create the volume mount points, owned by onw, BEFORE they're ever mounted —
# a fresh named volume (or bind mount) attached at a path with no prior image
# content is otherwise created root-owned, which the non-root `onw` user can't
# write to. .claude/.codex/.local/share/opencode are where each CLI's own subscription
# login state lives (see docker-compose.yml) — this app never reads or writes them itself.
RUN mkdir -p /home/onw/.open-net-worth /home/onw/.claude /home/onw/.codex \
       /home/onw/.local/share/opencode \
    && chown -R onw:onw /app /home/onw/.open-net-worth /home/onw/.claude /home/onw/.codex \
       /home/onw/.local
USER onw

# Zero-config default HOST is 127.0.0.1 (correct for a bare local process, not
# for a container reached from outside its network namespace) — override it here.
# PORT stays the app's own default (8080); override via `docker compose` env if
# you need a different one.
ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

# DB migrations run automatically at boot (backend/db/migrate.py::ensure_migrated,
# first thing in the FastAPI lifespan, fail-closed) — no separate migrate step here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT}/api/health || exit 1

CMD ["python3", "-m", "backend.main"]
