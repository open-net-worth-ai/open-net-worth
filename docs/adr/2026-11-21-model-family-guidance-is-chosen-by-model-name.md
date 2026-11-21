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
  it is constant per model, so the cache holds. Every turn builds it for the model that serves
  it: chat (`conversation.stream_turn`), Automation runs, and — since the amendment below — a
  turn resumed by approving a card, a headless run and a capture.
- **One family today: GPT** (`gpt*`, `o<digit>*`, `*codex*`), with a short LOADING TOOLS block:
  load a missing tool with `find_tools` and make the change this turn; never say you can't
  because a tool isn't loaded. Other families get nothing.
- **A family earns a block only with evidence** from `python3 -m backend.evals.tool_discovery`
  (`make tool-discovery-eval`): it replays the report against a scratch book, guidance off and
  on, and scores the resulting ledger.

## Evidence

`codex_cli:gpt-6-luna`, 13 replays per arm over four sessions. The first 5 used the report's
own account names, amount and date; the last 8 use the synthetic scenario the eval ships with
(same shape: one move booked as a reversed transfer plus a duplicate expense).

| guidance | fixed | not fixed |
|---|---|---|
| off | 8 / 13 | 5 — four turned the expense into a second transfer (money booked twice), one stopped to ask which entry to keep |
| on | 11 / 13 | 2 — one turned the expense into a second transfer, one fixed the transfer and asked before deleting the expense |

That points the right way but is not statistically established at this sample size, and it
is noisy: the first session showed 0/2 vs 2/2, a later one 3/3 vs 3/3. Without guidance the model rarely gave up outright, as in the report; more often it
did something worse, a double-count, because the only write it held was `update_transactions`.
The block reduces that, it does not remove it, so the block is a mitigation, not a guarantee.
Control: `claude_cli:sonnet` gets no block (verified) and fixed 2/2. Re-run before tightening or
extending the block.

## Consequences

- A per-family block is a few hundred characters for that family only; no change for Claude.
- Adding a family = an entry in `_GUIDANCE` plus a replay showing it helps.
- The structural options above stay open if guidance stops being enough.

## Amendment (2026-11-21): a turn's prompt and model follow the turn, not the default

Only chat and Automation runs built the prompt for the turn's own model. The four interactive
approval-resume paths (web, Telegram and other channels, voice) built it for the instance
default and, passing no model, ran the rest of the turn on the default too; a headless run and
a capture ran their own model with the default's prompt. So a conversation switched to a GPT
model under a Claude default lost its model and this block the moment a card was approved.

All four resume paths now go through `conversation.resume_pending`: a conversation with a model
of its own resumes on it with its prompt; one without resumes exactly as before. Headless runs
and captures build their prompt with `conversation.system_prompt_for(model)`. Pinned by
`tests/test_turns_get_the_prompt_of_their_own_model.py`.
