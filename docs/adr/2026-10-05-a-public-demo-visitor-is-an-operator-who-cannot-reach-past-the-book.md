---
title: A public demo visitor is an operator who cannot reach past the book
status: Accepted
date: 2026-10-05
deciders: Repo owner, Claude
---

# A public demo visitor is an operator who cannot reach past the book

## Context

A public demo (`DEMO_MODE=1`, `scripts/run_demo.sh`, `deploy/aws-demo/`) publishes its password
so anyone can try the app. Every visitor is therefore the operator. Until now `DEMO_MODE` did
only one thing: it turned on the per-visitor chat quotas. Everything else an operator can do was
open to anyone on the internet, including:

- **Reading a stored secret back.** `GET /api/model-config/providers/{p}/reveal-key` returns
  the demo's AI provider key in plaintext. `GET /api/connectors/{id}/secret/{field}` does the
  same for connector credentials.
- **Redirecting the key without reading it.** `editable_base_url: False` is a UI hint, not a
  server rule. A visitor can repoint the OpenRouter `base_url`, or make a `custom` provider the
  default, at their own server, and the next chat request delivers the key in its
  `Authorization` header. Lucius's `update_model_config` and `set_provider_credentials` do the
  same through the store, and on a demo the visitor approves their own confirm card. Hiding the
  key, or moving it into an environment variable, closes neither path.
- **Running arbitrary code on the host.** A `stdio` MCP server (`POST /api/mcp-clients`, or
  `/probe`) is documented as arbitrary local code. So is the sandbox once its Settings switches
  are on, because it is not a jail.
- **Locking every other visitor out** by changing or removing the shared password.

## Decision

On a demo, a visitor may do anything to the demo's own fabricated book, but nothing that reaches
past it. This is enforced in two places, both inert unless `settings.demo_mode` is on:

1. **Routes** (`backend/api/demo_lockdown.py`, an app-wide dependency next to `require_auth`)
   answer `403 {"code": "demo_locked"}` for both reveal endpoints, every write under
   `/api/model-config` and `/api/mcp-clients`, and `POST /api/auth/{change,remove}-password`.
2. **Stores**, for the paths that never pass through a route (Lucius tools):
   `ModelConfigStore.update` / `update_provider` / `remove_provider` / `remove_provider_key`
   raise `ConfigurationError`. `code_execution_enabled`, `script_skills_enabled` and
   `script_skills_unattended_enabled` read `False` whatever is stored. (Amended 2026-10-11: that switch no longer exists.)

The demo's host is still the operator. It changes AI settings from a shell on the host with
`DEMO_MODE=0` for that one process (`demo-aws.sh lucius` does exactly this).

Reading the key with SQL was already closed: `secret_config` is in `SECRET_TABLES`, which
`run_sql_query` denies.

## Consequences

- A demo's AI key can be spent only by chatting, within the `DEMO_TOKENS_*` caps. The provider
  key's own credit limit remains the hard stop.
- Visitors can still change, break or delete the demo book itself. A demo is reset by reseeding,
  not protected row by row.
- In Settings on a demo, these actions fail with "This is disabled on the public demo." There is
  no special demo UI.
- A new route or store that can reveal a secret, change where one is sent, or execute code must
  join this list. `tests/test_demo_lockdown.py` pins today's set.
