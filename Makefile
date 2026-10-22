# Open Net Worth — developer task runner (P0-E2).
# Mirrors .github/workflows/ci.yml so `make check` is the one command to run
# before pushing. Uses the project virtualenv tooling (.venv) for the backend
# and the frontend's own node tooling — NEVER boots a server.
#
# Quick start (new clone, zero configuration):
#   make setup        # venv, backend + frontend deps, frontend build, DB migration
#   make start         # run it, then open the URL `make setup` printed
#
# Everyday development:
#   make install      # backend + frontend deps (no build/migrate — use `make setup` for that)
#   make check        # lint -> type -> test -> build (the full CI gate, local)
#   make format       # auto-format backend (ruff) + frontend (prettier)
#   make install-hooks# activate the pre-commit git hook

# Prefer the project virtualenv if present, else fall back to PATH.
VENV       := .venv
PY         := $(if $(wildcard $(VENV)/bin/python3),$(VENV)/bin/python3,python3)
RUFF       := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
MYPY       := $(if $(wildcard $(VENV)/bin/mypy),$(VENV)/bin/mypy,mypy)
PRECOMMIT  := $(if $(wildcard $(VENV)/bin/pre-commit),$(VENV)/bin/pre-commit,pre-commit)
IMPORTLINT := $(if $(wildcard $(VENV)/bin/lint-imports),$(VENV)/bin/lint-imports,lint-imports)

.DEFAULT_GOAL := check
.PHONY: release-commit check lint imports type test build format install install-hooks setup start bundle wheel seed dev serve demo demo-host demo-reset demo-rebuild demo-stop demo-layout-save demo-layout-restore qa qa-cu qa-cu-mock qa-clean qa-manual qa-manual-stop qa-fresh qa-fresh-stop qa-fresh-reset qa-ports-free ui-gate qa-e2e qa-e2e-crypto-market-data qa-e2e-setup qa-acceptance-check qa-data dedup-eval bug help db-upgrade db-revision db-check daemon-start daemon-stop daemon-status docs-site docs-site-embed docs-lint cli-smoke cli-smoke-update cli-smoke-install cli-smoke-uninstall

# QA needs a real key for any LLM-dependent spec: clone_data_for_qa deliberately strips
# stored provider keys, so the cloned instance has none. Prefer an exported ANTHROPIC_API_KEY;
# otherwise fall back to the operator's own stored key. Without this every LLM spec returns
# SKIP — and the runner exits 0 on "passed or skipped", so the gate reports green having
# verified nothing. Resolved here (once, at parse time) rather than relying on the env
# reaching a recipe sub-shell, which is what silently failed before.
QA_STORED_KEY := $(shell $(PY) -c "import json,pathlib;p=pathlib.Path.home()/'.open-net-worth/data/model_config.json';print(json.loads(p.read_text()).get('providers',{}).get('anthropic',{}).get('api_key','') if p.exists() else '')" 2>/dev/null)
QA_KEY := $(if $(ANTHROPIC_API_KEY),$(ANTHROPIC_API_KEY),$(QA_STORED_KEY))
QA_APP_PROVIDER ?= anthropic
QA_APP_MODEL ?= claude-sonnet-4-6

# --- QA venv settings (python3.12 required; 3.11+ would also work) ---
QA_DIR    := qa/browser-use
QA_PYTHON := $(QA_DIR)/.venv/bin/python3
# Prefer python3.12, then python3.11, then python3 (will fail version check if <3.11)
QA_INTERP := $(or $(shell command -v python3.12 2>/dev/null), \
                  $(shell command -v python3.11 2>/dev/null), \
                  python3)

## check: full local gate — lint -> type -> test -> build -> docs-lint (mirrors CI; no server)
check: lint type test build docs-lint

## dev: run the backend with auto-reload SCOPED to backend/ only — so editing
## worktrees under .claude/ (background agents) or frontend/ never reloads the app.
## Override the port with `make dev PORT=8081`.
PORT ?= 8080
dev:
	$(PY) -m uvicorn backend.main:app --reload --reload-dir backend --port $(PORT)

## serve: run the backend WITHOUT --reload, LAN-bound (0.0.0.0) — the exact command
##        the ai.opennetworth.backend launchd service runs. Foreground; Ctrl-C to stop.
serve:
	$(PY) -m uvicorn backend.main:app --host 0.0.0.0 --port $(PORT)

## daemon-start: load the launchd services so the backend (:8080) and frontend (:5174)
##               run persistently, restart on crash, and start at login again.
daemon-start:
	launchctl bootstrap gui/$$(id -u) $$HOME/Library/LaunchAgents/ai.opennetworth.backend.plist
	launchctl bootstrap gui/$$(id -u) $$HOME/Library/LaunchAgents/ai.opennetworth.frontend.plist
	@echo "==> Loaded. Check with: make daemon-status"

## daemon-stop: unload the launchd services — stops the backend/frontend and, unlike
##              a plain kill, actually prevents them from auto-restarting.
daemon-stop:
	launchctl bootout gui/$$(id -u)/ai.opennetworth.backend 2>/dev/null || true
	launchctl bootout gui/$$(id -u)/ai.opennetworth.frontend 2>/dev/null || true
	@echo "==> Unloaded."

## daemon-status: show whether the launchd services are currently loaded.
daemon-status:
	@launchctl list 2>/dev/null | grep opennetworth || echo "not loaded"

# Where a HOSTED demo keeps its book. Outside the repo, like the live instance dir, so a
# `git clean` can never touch it. Override per-invocation: `make demo-host DEMO_ROOT=/srv/onw`.
DEMO_ROOT ?= $(HOME)/.open-net-worth-demo
# The launch log lives OUTSIDE DEMO_ROOT on purpose: `demo-reset` deletes that whole
# directory, and a log inside it vanishes mid-write — the wrapper then waits on a file
# that no longer exists and reports a failure for a demo that started fine.
DEMO_LOG   ?= $(if $(TMPDIR),$(TMPDIR),/tmp)/onw-demo-launch.log

## demo: throwaway demo on frontend :5175 / backend :8175 — fresh book, erased on Ctrl-C
demo:
	./scripts/run_demo.sh

## demo-host: hosted demo whose book SURVIVES a restart (this is the one for demo.opennetworth.ai)
##            Visitors' edits persist. Runs in the BACKGROUND — you get your terminal back.
demo-host:
	@ONW_DEMO_ROOT="$(DEMO_ROOT)" ./scripts/demo_launch.sh host

## demo-reset: throw away whatever visitors did and rebuild the book, dated to TODAY, then serve.
##             Your dashboard arrangement is carried across. This is the "put it back" button.
demo-reset:
	@ONW_DEMO_ROOT="$(DEMO_ROOT)" ./scripts/demo_launch.sh reset

## demo-rebuild: rebuild the frontend bundle from CURRENT source and restart. Keeps the book.
demo-rebuild:
	@ONW_DEMO_ROOT="$(DEMO_ROOT)" ./scripts/demo_launch.sh rebuild

## demo-layout-save: snapshot the demo's dashboard arrangement to demo-layout.json
##                    Arranging widgets by hand is real work and nothing else backs it up —
##                    a reseed, a fresh book or a stray write loses it with no undo.
demo-layout-save:
	./scripts/demo_layout.sh save

## demo-layout-restore: put a saved arrangement back (after a reset, or after someone clobbers it)
demo-layout-restore:
	./scripts/demo_layout.sh restore

## demo-stop: stop whatever demo is running (both ports), leaving a hosted book on disk
demo-stop:
	@pkill -f run_demo.sh 2>/dev/null || true
	@for port in 5175 8175; do \
	  for pid in $$(lsof -nP -iTCP:$$port -sTCP:LISTEN -t 2>/dev/null); do kill $$pid 2>/dev/null || true; done; \
	done
	@# WAIT for the ports to actually free. `kill` only asks; the socket lingers for a moment
	@# after, so returning immediately made `demo-rebuild` (stop-then-start) race itself and
	@# die on "port already in use". Escalate to SIGKILL if a process ignores the request.
	@for i in $$(seq 1 30); do \
	  busy=""; \
	  for port in 5175 8175; do \
	    if lsof -nP -iTCP:$$port -sTCP:LISTEN -t >/dev/null 2>&1; then busy="$$busy $$port"; fi; \
	  done; \
	  [ -z "$$busy" ] && break; \
	  if [ $$i -eq 15 ]; then \
	    for port in $$busy; do \
	      for pid in $$(lsof -nP -iTCP:$$port -sTCP:LISTEN -t 2>/dev/null); do kill -9 $$pid 2>/dev/null || true; done; \
	    done; \
	  fi; \
	  sleep 1; \
	done
	@echo "==> demo stopped (a hosted book in $(DEMO_ROOT) is left alone)"

## lint: ruff check + ruff format --check + import-linter dependency rule + AI-tool naming
lint:
	$(RUFF) check .
	$(RUFF) format --check .
	$(IMPORTLINT)
	$(PY) -m backend.scripts.tool_inventory --lint
	$(PY) -m backend.scripts.adr_index --check
	$(PY) -m backend.scripts.pii_lint

## tool-names: AI-tool naming rule only (entity-vs-attribute arity, create-vs-add)
## Lives in `lint`, not `check`, for the same reason import-linter does: it is a static
## CONTRACT check over the registry, it needs no server and no test fixtures, and `check`
## already runs `lint` first — so putting it here gets it into both gates at one cost.
## Putting it in `check` alone would leave the fast, most-run gate blind to the one defect
## class this repo has re-introduced across five separate lanes.
tool-names:
	$(PY) -m backend.scripts.tool_inventory --lint

## imports: import-linter dependency rule only (api/ai -> services -> db -> core)
imports:
	$(IMPORTLINT)

## type: mypy backend (blocking) + frontend tsc (blocking)
type:
	$(MYPY) backend
	# Frontend typecheck is BLOCKING since the P2-FE1 burn-down (0 errors).
	# `vite build` strips types without checking — this is the only frontend
	# type gate, so it must stay blocking or errors silently accumulate again.
	cd frontend && npm run typecheck

## test: fast pytest gate (integration suite excluded — it needs API keys + ~8min)
## PLUS the frontend vitest suite. The frontend half is NOT optional: it was
## outside the gate until 2026-08-06, and a commit that broke 10 MobileTabBar
## tests landed on main under a green `make check` precisely because of that.
## `make type` only proves the types compile; nothing ran the 3200 unit tests.
## pytest runs on every core (-n auto): one process took ~16 min for ~21k tests.
test:
	$(PY) -m pytest -q -m "not integration" -n auto
	cd frontend && npm run test

## build: frontend production build (vite build — exits, no dev server) + CSS class/token guards
build: docs-site-embed
	cd frontend && npm run lint:classes
	cd frontend && npm run lint:entity-rows
	cd frontend && npm run lint:tokens
	cd frontend && npm run lint:breakpoints
	cd frontend && npm run build

## docs-lint: lint docs/product/**/*.md against the corpus contract (frontmatter schema,
##            category/directory match, duplicate order, H1 presence/match, cross-links).
##            Also runs as its own step in `check` (below) and in CI's lint job, so a bad
##            page fails both `make check` and CI, not just a manually-run `make docs-lint`
##            (a corpus-lint gap found in review, 2026-08-23 — `services/product_docs.py`
##            fails closed for the WHOLE Help feature on one bad file, so this must be a
##            real gate, not a target nothing calls). Kept as its own named target too —
##            fast, no build/Node needed — so an author can run just this on every corpus
##            edit. Complements docs-site's own postbuild Astro/Starlight-specific check.
docs-lint:
	$(PY) -m backend.scripts.docs_lint

## docs-site: build the public docs site (Astro + Starlight) from docs/product/ — its own
##            package.json, not part of `make check`/CI (deploys via its own GitHub Actions
##            workflow, .github/workflows/docs-site.yml, on push to main). `npm run build`
##            runs a postbuild link-integrity check itself. Preview locally with
##            `cd docs-site && npm run dev` (Astro's default dev port, 4321). Output:
##            docs-site/dist/ (gitignored).
docs-site:
	cd docs-site && npm install && npm run build

## docs-site-embed: build the SAME site a second time for the app to serve at /docs — this is
##                  what the in-app Help modal shows (frontend/src/components/help/HelpDocsFrame.tsx).
##                  One reader for both surfaces: the app has no docs stylesheet of its own.
##                  Output: docs-site/dist-embed/ (gitignored). Runs as part of `make build`.
docs-site-embed:
	cd docs-site && npm install && npm run build:embed

## format: auto-format backend (ruff) + frontend (prettier --write)
format:
	$(RUFF) format .
	$(RUFF) check . --fix
	cd frontend && npm run format

## install: backend (requirements + dev) + frontend deps
install:
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt
	cd frontend && npm ci

## install-hooks: activate the pre-commit and commit-msg (docs-impact) git hooks (once per clone)
install-hooks:
	$(PRECOMMIT) install --hook-type pre-commit --hook-type commit-msg

## setup: ONE-COMMAND install for a fresh clone — venv, backend + frontend deps,
##        frontend build, DB migration, then prints the URL. Idempotent (safe to
##        re-run) and fails LOUDLY with an actionable message on a missing/wrong
##        prerequisite (Python <3.12, no Node, native Windows) instead of a later
##        traceback. No .env required — see .env.example and
##        docs/adr/2026-08-23-zero-config-default-data-dir.md. macOS/Linux/WSL2
##        only; on native Windows use `docker compose up` instead.
setup:
	@bash scripts/setup.sh

## start: run the app (serves the built frontend from frontend/dist/ if `make
##        setup`/`make build` produced one). Honors HOST/PORT/DATA_DIR/etc. from
##        the environment or .env, same as every other entrypoint.
start:
	$(PY) -m backend.main

## autostart: ONE command for someone not using Docker — setup, then install a
##            per-user boot service (launchd on macOS, systemd --user + linger on
##            Linux) so the app starts at boot and restarts if it crashes. This is
##            the non-Docker answer to Docker's `restart: unless-stopped`; Docker
##            already has that built in and needs no target here.
autostart: setup
	@$(VENV)/bin/onw autostart install

## autostart-stop: stop the boot service and stop it starting at boot again
autostart-stop:
	@$(VENV)/bin/onw autostart stop

## autostart-status: is the boot service currently installed/running
autostart-status:
	@$(VENV)/bin/onw autostart status

## bundle: copy frontend/dist, docs-site/dist-embed and docs/product under backend/_bundled/ —
##         the wheel's input (backend/core/shipped_assets.py reads them from there when installed)
bundle:
	$(PY) scripts/bundle_assets.py

## wheel: build the installable wheel into dist/ — frontend + docs site, bundle, then `uv build`
wheel: build bundle
	uv build --wheel

## release-commit: stamp VERSION=x.y.z into pyproject/desktop/compose and commit it as `release: vX.Y.Z`.
##         Does not tag: wait for CI on that commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`
##         (RELEASING.md). Refuses a dirty tree.
release-commit:
	@test -n "$(VERSION)" || { echo "usage: make release-commit VERSION=x.y.z"; exit 1; }
	@test -z "$$(git status --porcelain --untracked-files=no)" || { echo "working tree has uncommitted changes"; exit 1; }
	$(PY) scripts/stamp_version.py $(VERSION)
	git add pyproject.toml docker-compose.yml desktop/package.json desktop/package-lock.json
	git commit -m "release: v$(VERSION)" -m "Docs: none — version stamp"
	@echo "Committed. Push it, wait for CI to pass on this commit, then tag v$(VERSION)."

## db-upgrade: bring the local database to head (with backup, fail-closed)
db-upgrade:
	.venv/bin/python3 -c "from backend.db.migrate import ensure_migrated; ensure_migrated(allow_live_instance=True)"

## db-revision: autogenerate an alembic revision from tables.py changes (dev)
db-revision:
	.venv/bin/alembic -x allow_live_instance=1 revision --autogenerate -m "$(MSG)"

## db-check: fail if tables.py and the revision chain disagree
db-check:
	.venv/bin/python3 -m pytest tests/db/test_migrations.py -q

## seed: build the fabricated demo book into DATA_DIR (refuses a non-empty book)
seed:
	$(PY) -m backend.scripts.seed_demo --commit

## bug: capture a bug/idea into the Hardening Board inbox — `make bug MSG="net worth off by FX"`
bug:
	@$(PY) scripts/add_bug.py "$(MSG)"

## ios-shortcuts: regenerate the unsigned placeholder .shortcut files under docs/shortcuts/dist/
ios-shortcuts:
	$(PY) -m scripts.shortcuts.build

## ios-shortcuts-sign: rebuild AND sign the shipped shortcuts (macOS, signed in to iCloud; at release)
ios-shortcuts-sign:
	$(PY) -m scripts.shortcuts.build --sign

## refresh-prices: re-pull the vendored model price map from LiteLLM's public data
refresh-prices:
	$(PY) scripts/refresh_model_prices.py

## perf-bench: measure read-flow latency + concurrent tab-load contention against a COPY of the
##             DB (read-only, no server). Usage: make perf-bench DB=/path/to/copy/onw.db
perf-bench:
	@test -n "$(DB)" || { echo "usage: make perf-bench DB=/path/to/copy/onw.db  (a COPY — never the live tree)"; exit 2; }
	@mkdir -p /tmp/onw-perf-bench/data /tmp/onw-perf-bench/cache /tmp/onw-perf-bench/logs
	@ONW_DATABASE_URL="sqlite:///$(DB)" \
	  ONW_ROOT=/tmp/onw-perf-bench \
	  $(PY) scripts/perf_bench.py

## cli-smoke: OPT-IN live regression of the claude/codex CLI transports — real logins, a few cents.
##            Tool reachability, no-leakage, strict structured output vs reviewed snapshots.
##            Never part of `make check`. See docs/ai/CLI_REGRESSION.md.
cli-smoke:
	$(PY) scripts/cli_smoke.py

## cli-smoke-update: refresh tests/cli_live/snapshots after REVIEWING what changed in the CLIs
cli-smoke-update:
	$(PY) scripts/cli_smoke.py --update

## cli-smoke-install: install the launchd job (hourly check → runs on a CLI version change or daily,
##                    macOS notification on failure). cli-smoke-uninstall removes it.
cli-smoke-install:
	$(PY) scripts/cli_smoke.py install

cli-smoke-uninstall:
	$(PY) scripts/cli_smoke.py uninstall

## backup: on-demand full-data backup (also runs automatically once a day — see Settings → Data)
backup:
	$(PY) -m backend.scripts.run_backup

## qa-data: clone the live DATA_DIR (secrets stripped) into a throwaway dir for agent/QA testing; prints the path
qa-data:
	@$(PY) -m backend.scripts.clone_data_for_qa

## qa-e2e-setup: create the isolated Python 3.12 venv for browser-use + install playwright chromium
qa-e2e-setup:
	@echo "==> Checking for Python 3.11+ interpreter: $(QA_INTERP)"
	@$(QA_INTERP) -c "import sys; assert sys.version_info >= (3,11), f'Need Python 3.11+, got {sys.version}'; print(f'  Python {sys.version.split()[0]} OK')"
	@echo "==> Creating qa/.venv at $(QA_DIR)/.venv ..."
	$(QA_INTERP) -m venv $(QA_DIR)/.venv
	$(QA_DIR)/.venv/bin/pip install --quiet --upgrade pip
	$(QA_DIR)/.venv/bin/pip install -r $(QA_DIR)/requirements.txt
	$(QA_DIR)/.venv/bin/playwright install chromium
	@echo "==> qa-e2e-setup complete. Activate with: source $(QA_DIR)/.venv/bin/activate"

## qa-e2e: boot the app on ports 8091/8092, run all browser-use specs, tear down
##         SPECS="LCG-01 LCG-02" runs only those spec IDs — use it to gate the criteria a
##         change actually touched instead of paying for the whole suite.
qa-e2e: $(if $(SPECS),$(if $(filter CMD-01,$(SPECS)),qa-e2e-crypto-market-data,),qa-e2e-crypto-market-data) qa-e2e-setup
	@# This is deliberately ONE shell. It owns the clone and both child PIDs from
	@# creation through runner exit, so failures before the runner cannot leak data
	@# or processes. The clone must never be a parse-time shell expansion.
	@set -eu; \
	qa_data_dir=''; qa_pids=''; \
	qa_nonce=$$(openssl rand -hex 32); \
	cleanup() { \
	  exit_code=$$?; trap - EXIT; \
	  if [ -n "$$qa_pids" ]; then kill $$qa_pids 2>/dev/null || true; fi; \
	  rm -f $(QA_DIR)/.qa_pids; \
	  if [ -n "$$qa_data_dir" ]; then \
	    if [ -n "$(KEEP_DATA)" ]; then \
	      echo "==> KEEP_DATA set — clone preserved at $$qa_data_dir"; \
	    else \
	      rm -rf -- "$$qa_data_dir" "$$qa_data_dir-cache" "$$qa_data_dir-logs"; \
	    fi; \
	  fi; \
	  exit $$exit_code; \
	}; \
	trap cleanup EXIT; trap 'exit 130' INT TERM; \
	for p in 8091 8092; do \
	  if lsof -nP -iTCP:$$p -sTCP:LISTEN -t >/dev/null 2>&1; then \
	    echo "ERROR: port $$p is already in use — an earlier QA run did not clean up."; \
	    echo "       Free it first: make qa-ports-free"; exit 1; \
	  fi; \
	done; \
	echo "==> Seeding QA data ..."; \
	qa_data_dir=$$(QA_APP_PROVIDER="$(QA_APP_PROVIDER)" QA_APP_MODEL="$(QA_APP_MODEL)" $(PY) -m backend.scripts.clone_data_for_qa); \
	if [ -z "$$qa_data_dir" ]; then \
	  echo "ERROR: clone_data_for_qa produced no path — refusing to boot."; exit 1; \
	fi; \
	case "$$qa_data_dir" in /tmp/*|/var/folders/*/T/*) ;; *) \
	  echo "ERROR: clone_data_for_qa returned unsafe path $$qa_data_dir"; exit 1;; esac; \
	echo "==> Building frontend ..."; \
	cd frontend && npm run build; cd ..; \
	mkdir -p $(QA_DIR)/artifacts/logs; \
	echo "==> Starting backend on port 8091 ..."; \
	ONW_DISABLE_TELEGRAM=1 ONW_QA_MODE=1 \
	  ONW_QA_NONCE="$$qa_nonce" \
	  DATA_DIR="$$qa_data_dir" CACHE_DIR="$$qa_data_dir-cache" LOGS_DIR="$$qa_data_dir-logs" \
	  AUTH_DISABLED=true ANTHROPIC_API_KEY="$(QA_KEY)" \
	  $(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port 8091 \
	  > $(QA_DIR)/artifacts/logs/backend.log 2>&1 & qa_pids="$$!"; \
	echo "==> Starting frontend preview on port 8092 ..."; \
	(cd frontend && VITE_API_URL=http://localhost:8091 npx vite preview \
	  --host 127.0.0.1 --port 8092 \
	  >> $(CURDIR)/$(QA_DIR)/artifacts/logs/frontend.log 2>&1) & qa_pids="$$qa_pids $$!"; \
	echo "==> Waiting for backend ..."; \
	for i in $$(seq 1 30); do curl -sf http://localhost:8091/api/health >/dev/null 2>&1 && break || sleep 2; done; \
	if ! curl -sf http://localhost:8091/api/health >/dev/null 2>&1; then \
	  echo "ERROR: backend did not start"; cat $(QA_DIR)/artifacts/logs/backend.log; exit 1; \
	fi; \
	echo "==> Waiting for frontend ..."; \
	for i in $$(seq 1 30); do curl -sf http://localhost:8092/ >/dev/null 2>&1 && break || sleep 2; done; \
	if ! curl -sf http://localhost:8092/ >/dev/null 2>&1; then \
	  echo "ERROR: frontend did not start"; exit 1; \
	fi; \
	echo "==> Both servers up. Running specs ..."; \
	APP_URL=http://localhost:8092 BACKEND_URL=http://localhost:8091 ONW_QA_NONCE="$$qa_nonce" ANTHROPIC_API_KEY="$(QA_KEY)" QA_MODEL="$(QA_APP_MODEL)" $(QA_PYTHON) $(QA_DIR)/runner.py $(if $(SPECS),--specs $(SPECS))
	@# The data clone is a full copy of the operator's book (~700MB here) into a fresh tmp
	@# dir on EVERY run, and nothing reclaimed it: a night of QA runs left 12GB of orphans
	@# and filled the disk to 100%, which then fails in confusing, unrelated-looking ways
	@# across every concurrent agent. Removed above on both the pass and fail paths.
	@# KEEP_DATA=1 preserves it when you need to inspect what a failing run actually wrote.

## qa-e2e-crypto-market-data: deterministic CMD-01 preflight — self-boots/tears down a disposable production build
qa-e2e-crypto-market-data:
	cd frontend && npm run e2e:crypto-market-data

## qa-acceptance-check: report coverage — which GWT criteria have specs vs which are uncovered
qa-acceptance-check:
	@$(PY) $(QA_DIR)/acceptance_check.py

## qa: dual-mode UI QA dispatcher. `make qa MODE=browser-use` (deterministic merge gate) or
## `make qa MODE=computer-use` (on-demand Claude computer-use acceptance). No MODE → prints guidance.
MODE ?=
qa:
	@case "$(MODE)" in \
	  browser-use) $(MAKE) qa-e2e ;; \
	  computer-use) $(MAKE) qa-cu ;; \
	  *) echo "Two UI-QA modes — choose one:"; \
	     echo "  make qa MODE=browser-use   # deterministic PASS/FAIL merge gate (Playwright + docs/qa/acceptance specs)"; \
	     echo "  make qa MODE=computer-use  # on-demand per-feature acceptance (Claude computer-use; judges 'does it feel right')"; \
	     echo "  make qa-cu-mock            # computer-use smoke, ZERO API cost (CI-safe)"; \
	     echo "  make ui-gate               # fast geometry/CSS gate for a frontend change"; \
	     echo "See docs/qa/browser-use.md and docs/qa/computer-use.md for when to use which." ;; \
	esac

## qa-cu: Claude computer-use acceptance QA — boots a DISPOSABLE demo instance and drives it via
## computer use. On-demand, NOT a per-commit gate. Vars: TASK, QA_CU_MODEL, QA_CU_MAX_ACTIONS.
qa-cu:
	cd frontend && npm run acceptance-qa -- \
	  $(if $(TASK),--task $(TASK),) \
	  $(if $(QA_CU_MODEL),--model $(QA_CU_MODEL),) \
	  $(if $(QA_CU_MAX_ACTIONS),--max-actions $(QA_CU_MAX_ACTIONS),)

## qa-cu-mock: computer-use harness in --mock mode — ZERO API cost, CI-safe (proves boot+executor+report).
qa-cu-mock:
	cd frontend && npm run acceptance-qa -- --mock $(if $(TASK),--task $(TASK),)

## ui-gate: fast UI gate for a frontend change — CSS-class guard + self-booting visual-qa (geometry /
## overflow / console-errors / chat-overlap at 3 widths on a hardened DISPOSABLE instance, never :8080).
ui-gate:
	cd frontend && npm run lint:classes
	cd frontend && npm run lint:entity-rows
	cd frontend && npm run lint:tokens
	cd frontend && npm run lint:breakpoints
	cd frontend && node scripts/visual-qa.mjs --boot

## qa-si100: browser-level check for SI-100 (refused duplicates named on the Booked screen).
##           Self-boots a disposable isolated instance; free — no LLM call, the parse is stubbed.
qa-si100:
	cd frontend && node qa/import-refused-duplicates.mjs

## qa-cc-live-state: screenshot evidence for the concurrent-conversations sidebar live states
##                   (needs_you/done/failed/mixed + the C2 pending chip). Self-boots a disposable
##                   isolated instance; free — no LLM call, seeds DATA_DIR/sessions/index.json and
##                   IndexedDB directly rather than driving a real turn. 'working' is NOT covered —
##                   see the script header. Screenshots land in qa/artifacts/concurrent-conversations-live-state/.
qa-cc-live-state:
	cd frontend && node qa/concurrent-conversations-live-state.mjs

## qa-cc-live-turn: closes E1/E2/E4 — the three concurrent-conversations criteria that need a
##                  GENUINELY LIVE turn (the `working` indicator has no file/IndexedDB backing to
##                  seed). Self-boots a disposable isolated instance AND a tiny local
##                  OpenAI-compatible mock (frontend/qa/mock-llm-server.mjs, SSE, deliberately
##                  slow, one real tool-call round) — zero network egress, zero cost, no paid
##                  provider involved. Screenshots land in
##                  qa/artifacts/concurrent-conversations-live-state/live-*.png.
qa-cc-live-turn:
	@# Build FIRST. This target serves a built dist, so without this it silently drives a
	@# STALE bundle — it reports on code you did not change. That is exactly how the E4 fix
	@# came back still-failing on its first verification: the harness was testing a build
	@# made before the fix existed. qa-e2e and qa-manual both build for the same reason.
	cd frontend && npm run build
	cd frontend && node qa/concurrent-conversations-live-turn.mjs

## qa-manual: boot the SAME isolated stack qa-e2e uses (8091/8092, throwaway data clone) and
##            LEAVE IT RUNNING so a human can click through it. Every other qa target boots and
##            tears down around an agent; this one is for hands-on verification of anything an
##            agent cannot judge — a confirm card, an animation, whether a screen feels right.
##            Passes ANTHROPIC_API_KEY through so the QA instance's Lucius actually answers
##            (clone_data_for_qa deliberately strips stored provider keys). Stop with qa-manual-stop.
qa-manual:
	@test -n "$$ANTHROPIC_API_KEY" || (echo "ERROR: export ANTHROPIC_API_KEY first — without it Lucius cannot answer and any chat test is meaningless."; exit 1)
	@echo "==> Seeding throwaway QA data ..."
	$(eval QA_DATA_DIR := $(shell $(PY) -m backend.scripts.clone_data_for_qa))
	@echo "==> Building frontend ..."
	@cd frontend && npm run build > /dev/null
	@mkdir -p $(QA_DIR)/artifacts/logs
	@ONW_DISABLE_TELEGRAM=1 ONW_QA_MODE=1 \
	  DATA_DIR="$(QA_DATA_DIR)" CACHE_DIR="$(QA_DATA_DIR)-cache" LOGS_DIR="$(QA_DATA_DIR)-logs" \
	  AUTH_DISABLED=true ANTHROPIC_API_KEY="$(QA_KEY)" \
	  $(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port 8091 \
	  > $(QA_DIR)/artifacts/logs/manual-backend.log 2>&1 & echo $$! > $(QA_DIR)/.qa_manual_pids
	@cd frontend && VITE_API_URL=http://localhost:8091 npx vite preview \
	  --host 127.0.0.1 --port 8092 \
	  >> $(CURDIR)/$(QA_DIR)/artifacts/logs/manual-frontend.log 2>&1 & echo $$! >> $(CURDIR)/$(QA_DIR)/.qa_manual_pids
	@echo "==> Waiting for backend ..." && \
	  for i in $$(seq 1 30); do curl -sf http://localhost:8091/api/health >/dev/null 2>&1 && break || sleep 2; done && \
	  curl -sf http://localhost:8091/api/health >/dev/null || \
	  (echo "ERROR: backend did not start"; tail -30 $(QA_DIR)/artifacts/logs/manual-backend.log; exit 1)
	@echo "==> Waiting for frontend ..." && \
	  for i in $$(seq 1 30); do curl -sf http://localhost:8092/ >/dev/null 2>&1 && break || sleep 2; done
	@echo ""
	@echo "  QA instance ready:  http://localhost:8092"
	@echo "  Data (disposable):  $(QA_DATA_DIR)"
	@echo "  Your live book at ~/.open-net-worth is NOT touched."
	@echo "  Stop with: make qa-manual-stop"
	@echo ""

## qa-manual-stop: stop the servers started by qa-manual.
qa-manual-stop:
	@kill $$(cat $(QA_DIR)/.qa_manual_pids 2>/dev/null) 2>/dev/null || true
	@rm -f $(QA_DIR)/.qa_manual_pids
	@echo "==> QA instance stopped."

## qa-fresh: spin up a BRAND-NEW, EMPTY instance for working on first-run / onboarding.
##           Backend :8093 (ONW_QA_MODE=1, isolated EMPTY dirs, --reload), frontend :8094
##           (vite dev, hot reload, proxying /api to :8093). Unlike qa-manual this does NOT
##           clone your data — an empty instance is the whole point, since is_fresh_setup()
##           must be true for the onboarding flow to appear at all.
##           Provider env vars are cleared so the AI step is genuinely unconfigured.
##           Your live book at ~/.open-net-worth is never touched (QA guard is fail-closed).
# ── try: a personal end-to-end instance ───────────────────────────────────────
#   live      ~/.open-net-worth      the real book (port 8080)
#   demo      seeded fabricated data (make seed)
#   qa-fresh  /tmp/onw-fresh         disposable, vite dev server, for development (8093/8094)
#   try       ~/onw-instances/try    THIS — the real packaging, on your own data-free folder
#
# Mirrors `make start`: ONE process, ONE port, the BUILT frontend served by the backend — not
# a vite dev server proxying to a reloader. That is what a person who cloned the repo runs.
#
# The folder comes from ONW_ROOT. That is the ONE thing it cannot share with a real install:
# the built-in default is `$$HOME/.open-net-worth`, which on this machine is the live book, so
# an instance that set nothing would open real money data. ONW_ROOT is also exactly what a
# real user running a second instance sets, so the screen it produces is honest.
TRY_ROOT := $(HOME)/onw-instances/try
TRY_PORT ?= 8095

## try: run a personal end-to-end instance (built frontend; defaults to :8095)
try:
	@$(MAKE) --no-print-directory try-stop
	@# ALWAYS rebuild. This instance serves the BUILT bundle (that is the point — it is what a
	@# real install runs), so it has no hot reload, and a stale dist/ silently shows yesterday's
	@# UI while you wonder why a fix did nothing.
	@echo "==> Building frontend ..."
	@# `npm run build` directly, NOT `make build`: that target runs the merge-gate linters
	@# first (classes, tokens, breakpoints), so an unrelated in-flight edit elsewhere in the
	@# tree blocks your ability to RUN the app. Those gates belong to `make check` before
	@# shipping, not to starting a local instance.
	@cd frontend && npm run build >/dev/null
	@mkdir -p $(TRY_ROOT)
	@echo "==> Starting try instance on :$(TRY_PORT) ..."
	@# NO ONW_QA_MODE here — this is meant to be a REAL instance ("the real packaging... what
	@# a person who cloned the repo runs", per the header above), not a QA lane, and that flag
	@# does more than gate the data-dir fail-closed safety check: it also SUPPRESSES real
	@# external network calls app-wide (equity price fetches, price-history backfill,
	@# benchmark-instrument seeding, ...) — with no stub in place for most of them, so a stock
	@# ticker just came back "unavailable" forever, unlike a real install. That data-dir safety
	@# check exists for configs that DON'T hard-isolate their own root; this target already
	@# does — ONW_ROOT is hardcoded to TRY_ROOT on every invocation, never user/env-supplied —
	@# so the actual protection against ever touching the live tree does not depend on QA_MODE.
	@nohup env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u AI_PROVIDER -u AI_MODEL \
	  -u DATA_DIR -u CACHE_DIR -u LOGS_DIR \
	  ONW_DISABLE_TELEGRAM=1 \
	  ONW_ROOT="$(TRY_ROOT)" PORT=$(TRY_PORT) \
	  $(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port $(TRY_PORT) \
	  > $(TRY_ROOT)/app.log 2>&1 < /dev/null & echo $$! > $(TRY_ROOT)/.try-server.pid; disown
	@for i in $$(seq 1 40); do \
	  pid=$$(cat $(TRY_ROOT)/.try-server.pid); \
	  kill -0 $$pid 2>/dev/null || break; \
	  lsof -nP -a -p $$pid -iTCP:$(TRY_PORT) -sTCP:LISTEN -t >/dev/null 2>&1 && \
	    curl -sf http://127.0.0.1:$(TRY_PORT)/api/health >/dev/null 2>&1 && break; \
	  sleep 1; \
	done; \
	pid=$$(cat $(TRY_ROOT)/.try-server.pid); \
	(lsof -nP -a -p $$pid -iTCP:$(TRY_PORT) -sTCP:LISTEN -t >/dev/null 2>&1 && \
	 curl -sf http://127.0.0.1:$(TRY_PORT)/api/health >/dev/null) || \
	  (echo "ERROR: try instance did not start"; tail -40 $(TRY_ROOT)/app.log; exit 1)
	@echo "==> Open http://localhost:$(TRY_PORT)   (folder: $(TRY_ROOT))"

## try-stop: stop the try instance
try-stop:
	@pids=$$(lsof -nP -iTCP:$(TRY_PORT) -sTCP:LISTEN -t 2>/dev/null); \
	for pid in $$pids; do \
	  files=$$(lsof -p $$pid -Fn 2>/dev/null); \
	  case "$$files" in \
	    *"n$(TRY_ROOT)/.instance.lock"*) ;; \
	    *) echo "ERROR: :$(TRY_PORT) belongs to another instance (PID $$pid)"; exit 1;; \
	  esac; \
	done; \
	for pid in $$pids; do kill -9 $$pid 2>/dev/null && echo "  stopped :$(TRY_PORT)"; done
	@echo "==> try stopped."

## try-reset: wipe the try instance so first run appears again
try-reset:
	@$(MAKE) --no-print-directory try-stop
	@case "$(TRY_ROOT)" in *"/onw-instances/try") rm -rf "$(TRY_ROOT)";; \
	  *) echo "refusing to remove $(TRY_ROOT)"; exit 1;; esac
	@echo "==> try instance wiped. Run 'make try' for a brand-new first run."

QA_FRESH_ROOT := /tmp/onw-fresh
QA_FRESH_API  := 8093
QA_FRESH_WEB  := 8094
qa-fresh:
	@$(MAKE) --no-print-directory qa-fresh-stop >/dev/null 2>&1 || true
	@mkdir -p $(QA_FRESH_ROOT)/data $(QA_FRESH_ROOT)/cache $(QA_FRESH_ROOT)/logs
	@echo "==> Starting empty backend on :$(QA_FRESH_API) ..."
	@nohup env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u AI_PROVIDER -u AI_MODEL \
	  ONW_QA_MODE=1 ONW_DISABLE_TELEGRAM=1 \
	  ONW_ROOT="$(QA_FRESH_ROOT)" \
	  $(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port $(QA_FRESH_API) \
	    --reload --reload-dir backend \
	  > $(QA_FRESH_ROOT)/backend.log 2>&1 < /dev/null &
	@echo "==> Waiting for backend ..." && \
	  for i in $$(seq 1 30); do \
	    curl -sf http://localhost:$(QA_FRESH_API)/api/health >/dev/null 2>&1 && break || sleep 1; \
	  done; \
	  curl -sf http://localhost:$(QA_FRESH_API)/api/health >/dev/null || \
	    (echo "ERROR: backend did not start"; tail -40 $(QA_FRESH_ROOT)/backend.log; exit 1)
	@echo "==> Starting frontend (hot reload) on :$(QA_FRESH_WEB) ..."
	@cd frontend && nohup env VITE_DEV_API_TARGET=http://localhost:$(QA_FRESH_API) \
	  npx vite --host 127.0.0.1 --port $(QA_FRESH_WEB) --strictPort \
	  > $(QA_FRESH_ROOT)/frontend.log 2>&1 < /dev/null &
	@for i in $$(seq 1 30); do \
	  curl -sf http://localhost:$(QA_FRESH_WEB)/ >/dev/null 2>&1 && break || sleep 1; \
	done
	@echo ""
	@echo "  Fresh instance:     http://localhost:$(QA_FRESH_WEB)"
	@echo "  Backend API:        http://localhost:$(QA_FRESH_API)"
	@echo "  Disposable data:    $(QA_FRESH_ROOT)"
	@echo "  Logs:               $(QA_FRESH_ROOT)/{backend,frontend}.log"
	@echo "  Frontend hot-reloads; backend reloads on backend/ changes."
	@echo "  Re-run onboarding:  make qa-fresh-reset"
	@echo "  Stop:               make qa-fresh-stop"
	@echo ""

## qa-fresh-stop: stop the qa-fresh servers (by port, so uvicorn --reload children die too).
qa-fresh-stop:
	@for p in $(QA_FRESH_API) $(QA_FRESH_WEB); do \
	  pid=$$(lsof -nP -iTCP:$$p -sTCP:LISTEN -t 2>/dev/null); \
	  if [ -n "$$pid" ]; then kill -9 $$pid 2>/dev/null && echo "  stopped :$$p"; fi; \
	done
	@echo "==> qa-fresh stopped."

## qa-fresh-reset: wipe the disposable instance so first run / onboarding appears again.
qa-fresh-reset:
	@$(MAKE) --no-print-directory qa-fresh-stop
	@rm -rf $(QA_FRESH_ROOT)
	@echo "==> Fresh instance wiped. Run 'make qa-fresh' for a brand-new first run."

## qa-ports-free: kill anything LISTENing on the QA port range (8090-8099). Never touches 8080
##                (the live app) or 5174 (the dev proxy) — those are deliberately excluded.
qa-ports-free:
	@for p in 8090 8091 8092 8093 8094 8095 8096 8097 8098 8099; do \
	  pid=$$(lsof -nP -iTCP:$$p -sTCP:LISTEN -t 2>/dev/null); \
	  if [ -n "$$pid" ]; then kill -9 $$pid 2>/dev/null && echo "  freed $$p (pid $$pid)"; fi; \
	done
	@echo "==> QA ports 8090-8099 free (8080 and 5174 untouched)."

## qa-clean: remove local QA run artifacts (browser-use screenshots/logs + computer-use & visual-qa out-dirs).
qa-clean:
	rm -rf $(QA_DIR)/artifacts/* $(QA_DIR)/.qa_pids /tmp/onw-acceptance-qa /tmp/onw-visual-qa 2>/dev/null || true
	@echo "==> QA artifacts cleaned."

## dedup-eval: run the dedup accuracy harness (deterministic scorecard + hard gate)
dedup-eval:
	$(PY) -m backend.scripts.dedup_eval

## tool-surface-eval-mock: zero-cost proof of the tool-surface harness (no API calls, CI-safe)
tool-surface-eval-mock:
	$(PY) -m backend.evals.tool_surface --mock --repeats $(or $(REPEATS),3)

## tool-surface-eval: A/B which tool surface / model / result-size the model performs
## better against — scored on the resulting DATA, reported as cost per SUCCESSFUL task.
## Needs ANTHROPIC_API_KEY and SPENDS MONEY (falls back to the operator's own stored key
## via QA_KEY, same trick as the QA harnesses — see model_client.py's _resolve_api_key
## for the in-process fallback that backs this up even for a bare `python3 -m ...` call).
## Vars: ARMS, REPEATS, TASKS, MODEL, OUT.
##   make tool-surface-eval
##   make tool-surface-eval ARMS="--arm A --arm haiku=A,model=claude-haiku-4-5"
tool-surface-eval:
	ANTHROPIC_API_KEY="$(QA_KEY)" $(PY) -m backend.evals.tool_surface \
	  $(or $(ARMS),--arm A --arm B --arm C) \
	  --repeats $(or $(REPEATS),3) \
	  $(if $(TASKS),--tasks $(TASKS),) \
	  $(if $(MODEL),--model $(MODEL),) \
	  $(if $(OUT),--out $(OUT),)

## No default path: "pre-merge" is a claim about git ancestry, not a filename you can
## assume — verify with `git merge-base --is-ancestor <candidate-commit> HEAD` before
## trusting what a baseline file implies about itself, then name it after what you
## verified (see baselines/README.md for why the committed one is named by commit hash,
## not "pre-merge"). BASELINE_PATH is REQUIRED on both targets below, on purpose.
BASELINE_PATH ?=

## tool-surface-baseline-capture: run the REAL production tool registry (arm A-real) —
## not the A/B/C architecture comparison — and save cost+correctness as the reference
## other runs diff against. Needs ANTHROPIC_API_KEY and SPENDS MONEY (QA_KEY fallback,
## see tool-surface-eval above). Vars: REPEATS, OUT
## (required — name it after the commit you verified it at, e.g. baselines/<sha>.json).
tool-surface-baseline-capture:
	@test -n "$(OUT)" || (echo "OUT=<path> is required — name it after the commit, not 'pre-merge'" && exit 1)
	ANTHROPIC_API_KEY="$(QA_KEY)" $(PY) -m backend.evals.tool_surface --arm A-real \
	  --repeats $(or $(REPEATS),3) \
	  --save-baseline $(OUT)

## tool-surface-baseline-compare: re-run arm A-real and diff cost+correctness against a
## previously captured baseline. Needs ANTHROPIC_API_KEY and SPENDS MONEY (QA_KEY
## fallback, see tool-surface-eval above).
## Vars: REPEATS, BASELINE_PATH (required).
tool-surface-baseline-compare:
	@test -n "$(BASELINE_PATH)" || (echo "BASELINE_PATH=<path> is required" && exit 1)
	ANTHROPIC_API_KEY="$(QA_KEY)" $(PY) -m backend.evals.tool_surface --arm A-real \
	  --repeats $(or $(REPEATS),3) \
	  --baseline $(BASELINE_PATH)

## help: list available targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'

## desktop-runtime: bundle regular CPython, locked dependencies and the ONW wheel (build tools only)
.PHONY: desktop-runtime desktop-dev desktop-test desktop-e2e desktop-package

desktop-runtime:
	$(PY) scripts/build_desktop_runtime.py

## desktop-dev: run the desktop app from source as "Open Net Worth Dev" — its own settings and
## default book (~/.open-net-worth-dev), so it runs beside an installed app without touching it
desktop-dev:
	cd desktop && ONW_DESKTOP_PROFILE=dev npm start

## desktop-test: typecheck and test the native host
desktop-test:
	cd desktop && npm ci && npm run typecheck && npm test

## desktop-e2e: exercise Electron with a disposable synthetic book
desktop-e2e:
	cd desktop && npm run build && npm run e2e && node qa/book-picker.mjs

## desktop-package: build native application and installers using desktop/runtime
desktop-package:
	cd desktop && npm run make
