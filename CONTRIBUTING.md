# Contributing to Open Net Worth

Thanks for considering a contribution. This is a self-hosted personal-finance app maintained by
one person outside a day job — small, reviewable, well-tested changes are far more likely to
land than large ones. This doc covers dev setup, the standards a change has to meet, and how to
open a PR. It is grounded in [`CLAUDE.md`](CLAUDE.md) (the authoritative codebase map) and
[`docs/ENGINEERING_GUIDE.md`](docs/ENGINEERING_GUIDE.md) (standards + the dependency rule in
depth) — read both before a non-trivial change.

---

## Before you start

- **Open an issue first** for anything beyond a small fix (a new feature, a data-model change, a
  UI redesign) — it saves both of us time if the direction needs discussing before code exists.
  Use the issue templates under `.github/ISSUE_TEMPLATE/`.
- **Open an issue before touching the ledger, budgeting, or investments engine** — those flows are in an active, evidence-driven hardening loop, and a
  change there needs to fit that process (see the **`hardening-loop`** skill if you're working
  inside the Claude-agent tooling this repo uses internally).
- **A stale feature area is marked "coming soon" on purpose** (intel, tax, equity comp, FIRE
  planning, bank-account connectors) — see `docs/launch/BOARD.md`'s "Parked / deferred" section.
  PRs there are welcome but won't be prioritized for review the way core-flow fixes are.

## Contributor License Agreement

<!-- TODO(owner): decide and document the exact mechanism (a CLA-bot with a signed CLA text, or
     a lightweight Developer Certificate of Origin sign-off convention like `git commit -s`) and
     replace this paragraph with the concrete instructions once decided. Do not merge outside
     contributions until this is resolved. -->

Open Net Worth is licensed under AGPL-3.0, and the owner may want the option to dual-license the
project commercially in the future. To keep that option open, contributions from outside the
owner will need either a signed Contributor License Agreement or a Developer Certificate of
Origin sign-off (mechanism **TODO — not yet decided**; this paragraph will be replaced with the
concrete process). Until that's settled, please raise this in your PR rather than assuming
either — the maintainer will confirm what's needed before merge.

---

## Dev setup

**Fastest path**: `make setup` (venv, backend + frontend deps, frontend build, DB migration —
idempotent, safe to re-run) then `make start`. The manual steps below are what `make setup` runs
and are worth knowing if it fails partway or you want an editable/dev-reload setup instead. On
native Windows use `docker compose up` instead (`Dockerfile` + `docker-compose.yml`; no other
setup needed — see the "Native Windows" note just below for why).

Requirements: **Python 3.12+**, **Node.js 22+** (see `.github/workflows/ci.yml` for the exact
pinned versions CI runs), and — only if you want to exercise the AI assistant locally — an
Anthropic API key or an OpenAI-compatible endpoint.

**Native Windows is not supported** for running the backend directly — it uses POSIX-only APIs
(`fcntl` file locking, process-group signals for the sandbox and MCP subprocesses). Use WSL2, or
Docker, if you're on Windows.

```bash
# Backend — create the project virtualenv and install runtime + dev tooling
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt -r requirements-dev.txt

# Frontend deps
cd frontend && npm install && cd ..
```

No `.env` is required to boot the app — every data/cache/log directory defaults to
`~/.open-net-worth/` outside the repo checkout. Only create one (`cp .env.example .env`) if you
need to override those defaults (e.g. pointing `DATA_DIR` at an isolated test location) or set
`CORS_ORIGINS`/`HOST`/`PORT`. Everything else (AI provider + key, price sources, connectors) is
configured from inside the running app and stored there — never in `.env`.

The `Makefile` wraps the backend+frontend install: `make install` installs backend
(`requirements.txt` + `requirements-dev.txt`) and frontend (`npm ci`) deps. Use `python3` /
`pip3` — plain `python` / `pip` are not assumed to exist.

`requirements-dev.txt` adds the lint/type/test toolchain (ruff, mypy, pytest, pytest-asyncio,
httpx for `TestClient`, pre-commit) on top of `requirements.txt`.

### Pre-commit hook (recommended, run once per clone)

```bash
make install-hooks      # activates the git pre-commit hook
```

The hook (`.pre-commit-config.yaml`) mirrors the CI lint/format gates: ruff (lint + format),
prettier on `frontend/src`, gitleaks secret scan, and basic hygiene. It is **not** run by any
automation — you install it yourself, and CI re-checks the same gates regardless.

---

## Running the app

Two processes in dev (the frontend dev server proxies API calls to the backend):

```bash
# Terminal 1 — backend (from the project root)
uvicorn backend.main:app --reload --port 8080

# Terminal 2 — frontend dev server
cd frontend && npm run dev        # → http://localhost:5174
```

For a production-style run, FastAPI serves the built frontend from `frontend/dist/`:

```bash
cd frontend && npm run build      # output → frontend/dist/
# then visit the backend at http://localhost:8080
```

Storage and server binding are env-configurable (defaults reproduce zero-config behavior —
leaving them unset is the normal case): `DATA_DIR`, `CACHE_DIR`, `LOGS_DIR`, `CORS_ORIGINS`,
`HOST`, `PORT`. See `.env.example` and [`backend/core/config.py`](backend/core/config.py) /
`backend/paths.py`.

> The CI gate and pre-commit hook **never start a server** — `make build` runs `vite build`
> (which exits); nothing runs `uvicorn`/`npm run dev`/`vite dev`. Don't add a
> watching/long-running process to any gate.

> **Never point a dev/QA run at your real data.** `ONW_QA_MODE=1` fails closed if `DATA_DIR` /
> `CACHE_DIR` / `LOGS_DIR` resolve into the live `~/.open-net-worth/` tree — see `CLAUDE.md` →
> QA rules. Never run a destructive/cleanup script against a shared live data store while a dev
> server is running.

> **No real personal or financial data in a commit — ever.** `make install-hooks` runs gitleaks,
> `pii_lint` and `privacy_guard` on every commit, and the CI `privacy` job runs them again on every
> PR, where they can't be skipped. They refuse databases, bank exports, statement PDFs/CSVs and
> screenshots outside `tests/fixtures/` and the product-art folders, real home paths, and ID,
> phone, account and card shapes. Use synthetic values in tests and docs. Keep your own
> identifiers (name, email, banks, merchants) in `~/.config/open-net-worth/privacy-denylist.txt`,
> one per line, and the guard refuses them too without the list ever entering the repo.

---

## The dependency rule (the spine)

This is the one architectural contract every change must respect
([ENGINEERING_GUIDE.md §2.1](docs/ENGINEERING_GUIDE.md), §4.1). Imports flow in **one direction
only**, and a module never imports another module's private (`_underscore`) symbols across a
layer:

```
api/  ─┐
        ├─► services ─► db ─► core
ai/   ─┘
```

- **`core/`** (config, paths, errors, logging, units, fx) — depends on **nothing internal**. Any
  layer may read it.
- **`db/`** (repositories under `backend/db/`) — data access only, no domain rules; depends only
  on `core/`. Every store is DB-backed (`db/engine.py` + `db/tables.py`, SQLite by default,
  Postgres supported — `JsonStore` is retired; a new flat-list store follows the
  `PayloadTableStore` template). **`db/` must NOT import `ai.*`.**
- **`services/`** (business-logic package: `backend/services/`) — **the only place financial
  math lives** (net worth, P&L, FX, balances). Services are import-pure (no FastAPI/HTTP types)
  and depend on `db/` + `core/`. **`services/` must NOT import `api.*`.**
- **`api/`** (thin routers) and **`ai/`** (agents, tools, prompts) sit on top: validate input →
  call **one** service → shape the response. **No business logic or financial math in `api/` or
  in agent/tool heads** — they call services; they never recompute net worth / P&L / FX.

Forbidden, concretely:

- `db/` importing `ai.*`.
- a service importing `api.*` (e.g. `services/commitments.py → backend.api.cashflow`).
- importing another module's `_private` symbol across a layer.

**This is enforced today:** `import-linter` runs five CI-blocking contracts (`make lint` /
`make imports`). A PR that adds a new layering violation will fail CI. See
[BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md) for the full layer reference and the five
contracts.

### Other code standards (ENGINEERING_GUIDE.md §4.1)

- **Routers are thin** — validate (pydantic) → call one service → return.
- **Money math is deterministic and tested**, and lives only in services. Never trust a stored
  `base_amount` — reporting amounts are recomputed into the current base from the native leg
  (see `CLAUDE.md` → transactions).
- **Errors & logging**: prefer typed exceptions and structured `logging`; never `print` in
  library code.
- **No backward-compat shims.** When a data structure changes, migrate the data directly (there
  are migration scripts under `backend/scripts/` and Alembic revisions under `db/alembic/`); do
  not add fallback logic that reads old and new formats at once.
- **Size budgets** (lint-warn, aspirational): functions ≤ ~60 lines, files ≤ ~400.
- **CSS class discipline** (frontend): never introduce a `className` without its definition in
  `frontend/src/index.css` in the same change — enforced by `npm run lint:classes`.

---

## Test policy

Backend tests live in `tests/` and run under pytest (config in `pyproject.toml`; async + an
`integration` marker via `tests/conftest.py`).

```bash
# Fast gate — what you run constantly and what CI blocks on:
.venv/bin/python3 -m pytest -q -m "not integration"

# Full suite including LLM/market-API tests (needs ANTHROPIC_API_KEY; ~minutes):
.venv/bin/python3 -m pytest -q
```

- **Unit tests never hit the network.** Tests that call an LLM or a live market API are marked
  `@pytest.mark.integration` and **skip automatically** when `ANTHROPIC_API_KEY` is unset (see
  `conftest.py`). The integration suite is opt-in in CI and never blocks a merge.
- **What must exist** (ENGINEERING_GUIDE.md §4.2): every service method has a unit test; every
  API endpoint has a `TestClient` smoke test (status + shape); every store has a CRUD/query test
  (pattern: [`tests/test_positions_stores.py`](tests/test_positions_stores.py)).
- **Every bug fix ships with a regression test** that fails before the fix.
- One behavior per test; Arrange-Act-Assert; descriptive names.
- Conventions in more depth: the **`write-test`** skill.

Frontend tests use Vitest (`cd frontend && npm run test`). The frontend typecheck
(`npm run typecheck`) is run as part of `make check`.

---

## `make check` — the local gate

One command mirrors CI end to end. Run it before opening a PR:

```bash
make check          # lint → type → test → build → docs-lint
```

| Target | What it runs |
|---|---|
| `make lint` | `ruff check .` + `ruff format --check .` (backend) + `import-linter` (5 contracts, CI-blocking) |
| `make type` | `mypy backend` (blocking) + frontend `tsc` |
| `make test` | `pytest -q -m "not integration"` (fast gate) |
| `make build` | `vite build` (frontend production build; exits) |
| `make docs-lint` | Lints `docs/product/**/*.md` against the corpus contract (frontmatter, cross-links, headings) — `backend/scripts/docs_lint.py` |
| `make format` | auto-fix: `ruff format` + `ruff --fix` + frontend `prettier --write` |

CI (`.github/workflows/ci.yml`) runs the same lint → type → test → build → docs-lint jobs on
every PR plus an opt-in integration job. A PR must be green on `make check` locally and in CI.

**Memory changes** (a memory prompt, the routing guide, the write choke, or the model a memory
pass runs on) additionally run the opt-in live benchmark, `make memory-eval REPEATS=3`, which exits
non-zero on any safety miss or a quality score under the bar — see
[`docs/ai/MEMORY_EVAL.md`](docs/ai/MEMORY_EVAL.md). It spends the chosen model's quota, so it is never
part of `make check`; `make memory-eval-mock` is the zero-cost proof of the harness itself.

**UI changes** additionally clear the UI Quality Gate — see
[`docs/UI_QUALITY_GATE.md`](docs/UI_QUALITY_GATE.md): a design-first spec, an acceptance file
under `docs/qa/acceptance/`, `make qa-e2e` green for the touched criteria, `make ui-gate` clean,
and screenshot evidence with empty/loading/error states verified. "It renders" is not the bar.

---

## PR workflow & Definition of Done

Lifecycle for non-trivial work (right-size it — a typo fix skips planning/design; a new
tab/tool/endpoint does the lot):

`/feature-planning` (PRD in `docs/prd/`) → `/system-architect` (RFC in `.claude/rfcs/`) →
implement to the standards above → tests + `make check` → `/code-review` (+ `/security-review`
if it touches money, auth, or external I/O) → ship.

Branch off `main` (never commit straight to `main`). Commit messages: imperative mood,
conventional-commit prefix where it fits (`feat:`, `fix:`, `refactor:`, `docs:`…).

**Definition of Done** — a PR is done when (ENGINEERING_GUIDE.md §4.3):

1. Code follows the standards above; **lint + type + tests + import-linter are green** locally
   and in CI.
2. New/changed behavior is **tested** per the test policy (bug fixes include a regression test).
3. **Docs are updated in the same PR** — `CLAUDE.md`, the relevant page under `docs/product/` if
   user-facing behavior changed, and the relevant RFC/PRD (this is a standing project rule).
4. **No secrets, no personal data, and no new layering violations.** Never stage runtime-noise
   data files; stage specific files, not `git add -A`. If you're ever unsure whether a file you
   touched could contain real financial data, don't commit it — ask first.
5. Reviewed (human or `/code-review`; `/security-review` for money/auth/external I/O — **a
   `docs/product/*.md` change is in this category too**, not exempt as "just docs": it's a
   model-reachable prompt surface, see "Where things live" above).

---

## Where things live

`CLAUDE.md` is the authoritative codebase map (backend layers, the data model, AI tools, the
data-access repository pattern, checkpoints). Quick orientation:

- `backend/main.py` — FastAPI app; mounts routers and serves `frontend/dist`.
- `backend/api/` — thin HTTP routers.
- `backend/ai/` — the Lucius assistant: `assistant.py`, `tools/` registry, `prompts.py`,
  provider/search abstractions, memory, checkpoints.
- `backend/db/` — the repository layer (DB-backed stores over `db/engine.py` + `db/tables.py`;
  Alembic migrations under `db/alembic/`).
- `backend/services/` — the service layer (all business/financial logic).
- `backend/core/` — typed `Settings` (`config.py`); `backend/paths.py` centralizes every
  data/cache path; leaf stdlib-only utilities.
- `docs/product/` — the user-facing docs corpus (powers the in-app `/help` tab and the public
  docs site) — update this, not a hand-maintained feature list, when user-visible behavior
  changes. **This is also a model-reachable prompt surface**: Lucius's system prompt tells him
  to call `search_docs`/`read_doc` and treat this corpus as "the source of truth for what the
  app actually does" before answering. A PR that only touches a `.md` file here is not
  docs-only from a security standpoint — see the checklist below.
- `frontend/src/` — React/TypeScript app (`App.tsx`, tabs under `components/tabs/`, chat under
  `components/chat/`, hooks, api, utils, types).

See [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md) for the backend deep-dive (layer
contracts, seams, store conventions, AI tool registry).
