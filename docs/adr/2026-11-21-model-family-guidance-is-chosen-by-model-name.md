---
title: Model-family prompt guidance is chosen by model name, and added only with replay evidence
status: Accepted
date: 2026-11-21
deciders: repo owner + Claude
---

# Model-family prompt guidance is chosen by model name, and added only with replay evidence

## Context

Lucius sends one system prompt to every model. The tool offer is the core set plus whatever
the session loaded; every other tool is named in the prompt's TOOL CATALOG and reached with
`find_tools` (ADR `2026-07-19-core-tool-offer-diet`). A user's report (gpt-6-luna,
2026-11-21): asked to fix one move between two cash accounts that was booked as a reversed
transfer plus a duplicate expense, Lucius answered "I can't make the ledger changes from this session".
`delete_transactions` was one `find_tools` call away. GPT-family models treat their declared
function list as the whole of what they can do; the catalog's prose instruction was not
enough.

Options weighed and rejected:

- **Add the missing tools to the core set.** Fixes this case only; the next tool hits the same
  wall, and the core set was cut by measured usage on purpose.
- **Detect "I can't" in the reply and nudge.** Matching on the model's wording misfires on real
  limits and breaks as phrasing drifts.
- **Put every catalog name in `find_tools`' schema (an enum).** Grows every request.
- **A Hermes-style `call_tool(name, arguments)` bridge.** Moves the real tool name into
  arguments, so every name-keyed rule (permissions, cards, demo refusals, scopes, audit) must
  unwrap it first — a large security surface for an unproven gain.

Others do this per model: Hermes injects a "tool-use enforcement" block for GPT/Codex models
only and keeps model-specific lines only when its benchmark says they help; Codex ships a base
prompt per model.

## Decision

- **`backend/ai/model_guidance.py` holds the per-family blocks.** `model_family(model)` decides
  from the bare model id (vendor path and `:variant` dropped), never the transport, so a model
  gets the same guidance via OpenAI, OpenRouter, a compatible endpoint or the Codex CLI.
- **`build_system_prompt` appends the block** after the catalog, in the stable (cached) half;
  it is constant per model, so the cache holds. Automation runs pass their model and get it too.
- **One family today: GPT** (`gpt*`, `o<digit>*`, `*codex*`), with a short LOADING TOOLS block:
  load a missing tool with `find_tools` and make the change this turn; never say you can't
  because a tool isn't loaded. Other families get nothing.
- **A family earns a block only with evidence** from `python3 -m backend.evals.tool_discovery`
  (`make tool-discovery-eval`): it replays the report against a scratch book, guidance off and
  on, and scores the resulting ledger.

## Evidence

`codex_cli:gpt-6-luna`, two runs (2 + 3 repeats per arm), measured before the scenario's
account names, amount and date were swapped for the synthetic ones it now ships with (same
shape: one move booked as a reversed transfer plus a duplicate expense):

| guidance | fixed | not fixed |
|---|---|---|
| off | 1 / 5 | 4 — converted the expense into a second transfer (movement booked twice) |
| on | 4 / 5 | 1 — fixed the transfer, left the expense, asked whether to delete it |

Without guidance the model rarely gave up outright here; it did something worse, a silent
double-count, because the only write it held was `update_transactions`. Small samples: re-run
before tightening or extending the block.

## Consequences

- A per-family block is a few hundred characters for that family only; no change for Claude.
- Adding a family = an entry in `_GUIDANCE` plus a replay showing it helps.
- The structural options above stay open if guidance stops being enough.
