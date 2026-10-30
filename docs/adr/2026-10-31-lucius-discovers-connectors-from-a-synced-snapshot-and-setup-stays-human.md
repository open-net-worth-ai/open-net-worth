---
title: Lucius discovers connectors from a synced snapshot of the frontend directory; setup stays human-only through a UI command
status: Accepted
date: 2026-10-31
deciders: repo owner (design) + implementing agent (Claude)
---

# Lucius discovers connectors from a synced snapshot; setup stays human-only

## Context

Asked "what connectors are available?", Lucius named no broker. The curated directory behind
Browse & add (`CATALOG` + `MCP_INSTALL_PRESETS` in `frontend/.../connectors/catalog.ts`) lives only
in the frontend; the backend knows installed connectors, not what could be added. An interim
pointer (`more_in_directory` -> a doc section) relied on a hand-kept list in prose.

`backend/api/mcp_client_api.py` states that no Lucius tool reaches the connector-config routes,
and an MCP endpoint or env block is exactly where a credential lives. Whatever we build must not
weaken that, and must not become a second hand-kept directory.

## Decision

We will:

- Keep `catalog.ts` the single source. A checked-in snapshot, `backend/services/connector_directory.json`,
  is DERIVED from it (`npm run sync:connector-directory`) and a vitest fails when it differs. It
  holds non-secret fields only (id, name, families, categories, publisher, verification, sub,
  how it connects, env variable NAMES, runtime). It skips `mcp-custom`, and refuses a preset
  endpoint carrying userinfo or a query string. The endpoint is kept only to join "already set up"
  by endpoint (as `catalogProvenanceFor` does) and is never returned to the model.
- Give Lucius a read tool, `search_connector_directory` (`mutates=()`), bounded to 12 results with a
  total, joining native connectors by id and MCP servers by endpoint, never by name.
- Do setup through a screen command, `connectors.open_setup(id, reason?)`, advertised by the
  Connectors screen and parked with a navigation chip when it is not open. It changes UI state
  only: it opens the add form (preset pre-filled), a native connector's form, or an installed
  connector's settings. The id must be one the directory has; `run_screen_command` refuses any
  other (naming the closest) before dispatch, and the screen also fails cleanly on one. The user
  enters credentials and clicks Connect themselves; Lucius re-reads `installed` afterwards.
- With no interactive screen (an unattended run) the command returns `no_screen`, never success.

## Consequences

### Positive
- One directory, no drift; the model never touches config or secrets; the "human-only" comment on
  the MCP routes stays true.
- Same shape as Claude's own connector flow: search, suggest, the user clicks.

### Negative
- A regenerated JSON must be committed with any catalog change (the test says so).
- A thin directory-specific check lives in `ai/tools/screen.py` for this one command.

### Follow-ups
- A persistent "don't suggest this again" list for connectors the user dismissed (today the
  tool description only tells Lucius not to push one the user ignored).

## Alternatives considered

- Hand-maintained backend list or doc pointer — rejected: a second list that drifts.
- Backend runtime fetch of the frontend catalog — rejected: no such channel; the backend cannot import it.
- A tool that adds the connector or takes keys — rejected: puts credentials in the model's path.
- Free-text connector ids in the command — rejected: only exact directory ids are accepted.

## Agent provenance

- **Decided by:** implementing Claude agent, design fixed by the repo owner
- **Lane / branch:** `feat/lucius-connector-discovery`
- **Alternatives weighed:** hand list / runtime fetch / write tool / snapshot + UI command
- **Driving artifacts:** owner report 2026-10-31 (Lucius named no broker)
- **Why this way (one line):** one source of truth, enforced derived copy, and a model that can point but never touch.
