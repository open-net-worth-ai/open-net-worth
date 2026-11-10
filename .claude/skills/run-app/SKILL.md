---
name: run-app
description: The hang-safe way to validate the app in automation — import smoke + make check, never a foreground server. Use before committing, or whenever you need to confirm the backend imports / the build passes without booting a long-running process.
---

# Run / validate the app (hang-safe)

In automation, **NEVER start a foreground or long-running process** — no `uvicorn`, no `npm run dev`, no `vite` dev server, no `pre-commit install`, no foreground `sleep`. They never return and hang the agent. Validate with the import smoke + `make check` instead.

## 1. Backend import smoke (always safe, ~instant)

The ONLY approved way to confirm the backend imports — self-timeouts at 25s so it can never hang:

```bash
.venv/bin/python3 -c "import signal,sys; signal.signal(signal.SIGALRM, lambda *a: (print('TIMEOUT'), sys.exit(99))); signal.alarm(25); import backend.main; print('IMPORT_OK')"
```

Expect `IMPORT_OK`. `TIMEOUT` or a traceback means a real import/circular-import problem to fix.

## 2. The full local gate — `make check`

Mirrors CI end to end and **never boots a server** (`vite build` exits; tests exclude integration):

```bash
make check      # lint -> type -> test -> build
```

| Target | Runs |
|---|---|
| `make lint` | `ruff check .` + `ruff format --check .` + `lint-imports` (the dependency rule) |
| `make imports` | `lint-imports` only (the api/ai -> services -> db -> core contracts) |
| `make type` | `mypy backend` (blocking) + frontend `tsc` (non-blocking for now) |
| `make test` | `pytest -q -m "not integration" -n auto` (fast gate, one worker per core) |
| `make build` | `vite build` (production build; exits) |
| `make format` | auto-fix: `ruff format` + `ruff --fix` + frontend prettier |

## 3. Testing routes / behavior without a server

- Routes → FastAPI `TestClient` in a pytest (see the `write-test` skill). Never curl a live server in automation.
- AI tools → drive `execute_tool` with `asyncio.run` in a test.

## If a human actually needs the running app (interactive only — NOT automation)

Documented in `CLAUDE.md` / `CONTRIBUTING.md`, run by a person in their own terminals:

```bash
uvicorn backend.main:app --reload --port 8080     # backend
cd frontend && npm run dev                         # frontend -> http://localhost:5174
```

Do not launch these from the agent.

## Suggested permission allowlist (`.claude/settings.local.json`)

To cut prompts for the safe checks, allow: `Bash(make check)`, `Bash(make lint)`, `Bash(make imports)`, `Bash(make test)`, `Bash(make build)`, `Bash(.venv/bin/python3 -m pytest *)`, and the import-smoke one-liner. Do NOT allowlist `uvicorn`, `npm run dev`, or `vite` — they must never run unattended.
