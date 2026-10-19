---
number: 2026-10-10-auto-approval-is-a-per-call-judge
title: Auto approval is a per-call model judge, not "allow except two names"
status: Accepted
date: 2026-10-10
deciders: senior-engineer lane (Claude) + repo owner
---

# Auto approval is a per-call model judge, not "allow except two names"

## Context

`auto` is one of three approval modes, shared by a conversation and an Automation. Its UI
label promises "Lucius runs on its own and pauses to ask if anything looks unsafe."

What it did: for a call that would otherwise ask, `automation_gate_verdict` /
`conversation_gate_verdict` returned `allow` unless the tool name was in
`HARDLINE_UNATTENDED_TOOLS` — a two-name frozenset (`delete_transactions_where`,
`apply_payee_rules`). That is `skip` minus two names. Nothing looked at the call.

The inputs to "would this ask at all" were also hand-maintained: a `mutates=` tag typed on each
core tool, a curated 14-name `GATE_EXEMPT_READ_SHAPED` list, and a uniform `needs_approval`
default for every connector tool. A measured probe (2026-10-03, scratch root): 178 of 298
registered tools ask by default, including 24 connector *reads* (web search, price lookups, every
`*_get_status`) while the equivalent core reads run free.

A per-tool declaration cannot fix that: built-in connector tools carry no risk metadata, and
external MCP tools are not in the registry at all (they are discovered live with only a name,
description and schema).

## Decision

We will make `auto` a **per-call judgement made in real time** from what is in hand at dispatch:
the tool's name, description and parameter schema, the actual arguments, and what the owner
asked for. No per-tool declaration and no name list.

- `ai/auto_judge.py`: one small structured model call (`complete_model`, `effort="none"`, no
  reask, 4 s budget) returning `allow | ask` plus a one-line reason. `registry.dispatch` awaits it
  once, before the sync permission gate and before any handler; the verdict is parked in a
  ContextVar keyed by the in-flight `tool_use_id`, and the two `auto` branches read it
  synchronously. This ordering is forced by the 15 self-staging tools, which ask
  `should_preview_write()` from inside their handler.
- **Fails closed.** No verdict, a stale verdict, `ask`, no resolvable model, an unpinned CLI
  transport, timeout, provider error and invalid output are all `gate`. `allow` exists only as the
  parsed output of a model that answered in time. After 3 consecutive failures to get an answer a
  breaker skips the model for 30 s and asks immediately (a down provider costs one card, not four
  seconds per call).
- **What the judge never sees:** tool results, fetched pages, assistant prose, and — in an
  Automation run — the run conversation, whose opening message carries the trigger payload (an
  email body, a webhook). The owner's intent there is the Automation's stored `instructions`.
  The tool description and the arguments are sanitized and nonce-fenced as data.
- **Model:** the `approval` task pin (Settings → AI Models, "Auto-approval judge"), else the
  model the conversation is running on, else the instance default (owner decision). An unpinned
  CLI transport is not called: it asks immediately and says to pin a faster model.
- `HARDLINE_UNATTENDED_TOOLS`, `is_hardline_blocked` and `hardline_refusal_result` are deleted
  from code (owner decision, 2026-10-10: "if it's auto, let the judge decide"; and "I don't
  think we should hard code them").
- **No floor, and no name list.** A first cut of this change seeded a stored `needs_approval` for
  `delete_transactions_where` and `apply_payee_rules` (a bulk delete through a filter names no rows, so
  nothing a judge can read says how many a call touches) and let a stored ask outrank every mode. **That was
  withdrawn before it shipped** (owner decision, 2026-10-11, ADR
  `2026-10-11-permissions-are-one-value-per-tool`): Ask now means the same everywhere, so those two are
  judged like any other call in `auto` (the judge is told to ask on a bulk delete), asked in `manual` and run
  in `skip`. Every tool has one visible, editable value; nothing in code names a tool but the four that write
  Automation policy.

**Hardening from the security and code review of the first cut (same change):**

- The owner's words are only what they *typed*: a stored user turn also carries mention data, a
  cached news brief (web text), the screen snapshot and a turn snapshot, and a skill body (even
  a model-activated one) is stored as a user-role row. The judge reads only the first text block
  of a turn, skips context blocks and `_onw_skill` rows, and unwraps mid-turn injected messages.
- The reply must be EXACTLY one JSON verdict object (optionally in one code fence): prose around
  it, a second object or a duplicated key is unusable, so a quoted injected
  `{"verdict":"allow"}` cannot be read as the answer. The eval runner uses the same parser.
- The `Facts:` line sits outside every fence, so it reports only boolean flags whose key the
  tool's own schema declares; an argument key invented by injected text is never echoed.
- A tool the owner set to Always allow (there is no YOLO any more) is never put to the judge — in a
  chat or in an Automation run (ADR `2026-10-11-permissions-are-one-value-per-tool`).
- The parked verdict is bound to the tool name as well as the `tool_use_id`, and `prepare`
  clears it first, so a provider that reuses an id cannot leave a stale `allow` readable.
- The judge's reason is shown nowhere. (Shipped first as a labelled "Auto-approval: …" line on
  every staged card; see the amendment below.)
- An Automation's instructions get a 6,000-character cap, not a chat message's 1,500: they are
  the whole statement of intent for an unattended run, and a detailed playbook is thousands of
  characters (the first cut clipped one at 1,500 and would have judged on its setup half).

Unchanged: `manual` and `skip`; the owner's stored levels and YOLO (they outrank the judge in
both directions); a stored `needs_approval` forcing a card; and `AUTOMATION_POLICY_TOOLS`
(ADR `2026-08-04-a-run-cannot-write-the-policy-that-governs-runs`), which remain a name list on
purpose — a self-escalation guard is not an approval classification.

> **Amended 2026-10-11** (`2026-10-11-permissions-are-one-value-per-tool`): `AUTOMATION_POLICY_TOOLS`
> is deleted. The four Automation tools follow the mode like every other tool, and the judge is told
> the consequence of an Automation call in a trusted line instead.

> **Amended 2026-10-12** (owner, after security review): the judge's reason is not surfaced. A card
> reached through Auto is the ordinary card, byte for byte what Manual stages. The reason was
> model-written from untrusted arguments and shown on an approval surface; it also shared the card's
> `reason` field with the tool's own `reason` argument, so a model-supplied "Auto-approval: …" read
> as the judge's while the judge's real reason was dropped. The reply format `{"verdict","reason"}`
> and the strict parser are unchanged (the eval runner reports reasons); the reason is neither
> stored on a card nor logged (it can carry names and amounts from the call's arguments). A judge
> that cannot answer likewise says nothing on the card: it asks, as it always did.

> **Amended 2026-10-13** (owner: "treat it normally"; "a sensible timeout instead of a guessed
> one"): an unpinned CLI model is no longer skipped — it judges like any other model, and the
> "no judging until you pin another model" consequence below no longer holds. The skip rested on
> two unmeasured assumptions. Measured on the production prompt (10 calls each, one Mac): Claude
> Code `haiku` mean 3.0s / max 4.7s (9/10 parseable), `sonnet` 4.1s / 5.3s (10/10, 10/10
> correct), Codex `gpt-5.6-luna` 3.5s / 4.7s (10/10); no false allow from any. The 4s timeout,
> also a guess, would have timed out about half of `sonnet`'s calls, so the budget is now **10s
> for every model** (about twice the slowest observed). Slower is still a card, never a stall.

> **Amended 2026-10-13** (owner: judge a step's calls together): "per call" now means *per call
> verdict*, not *per model request*. A model step with N Ask calls used to cost N sequential judge
> requests (3–5s each on a CLI model, so ~12s for three), and the judge could never see that a read
> of private data and a send to the outside arrived in the same breath. `ai/auto_judge_batch.judge_step`
> now runs once per step, before the first call is dispatched: it collects the calls that would reach
> the judge (`auto_judge.will_be_judged` — the same predicate `prepare` asks — fed the arguments
> `registry.judgeable_input` says dispatch will hand over, so Block, Always allow, the Automation's
> own lists, Skip/Manual and calls refused before the gate are never sent), and **2–8 calls are one
> request** (more are split into even chunks, 9 → 5 + 4). **One call is the unchanged single-call
> request**, so the eval corpus and its prompt are byte for byte what they were.
>
> - *Prompt* (`auto_judge_prompt.build_batch_prompt` / `BATCH_SYSTEM`): the owner's intent once, then
>   each call as its own section `c1…cN` built by the SAME `_call_lines` as the single prompt — the
>   description, schema and arguments each inside the per-prompt nonce fences, the trusted facts and
>   Consequence lines outside them. The rules are the same text; the addition is the combination rule:
>   judge each call on its own AND together — a read of private data beside a send out of the app is an
>   ask for both, unless the owner plainly asked for exactly that.
> - *Reply*: exactly `{"verdicts":[{"id","verdict","reason"},…]}`, parsed as strictly as the single
>   reply (no prose, no second object, no duplicate key at any depth). An unusable reply, a timeout, a
>   provider error or an open breaker is `ask` for **every** call; a usable reply that left an id out,
>   answered it twice or gave it a verdict other than allow/ask is `ask` for **that** call. An id the batch
>   never had makes the whole reply unusable. `JUDGE_TIMEOUT_S` covers the whole request, the breaker
>   counts it as ONE result, and it is one `auto_approval` cost-log row. The reason stays internal.
> - *Parking*: each verdict is held per `(session, tool_use_id)` with the tool and a fingerprint of the
>   arguments **and** the trusted consequence line, and consumed once by `prepare`. If it does not match
>   what `prepare` is asked about (arguments changed, or an earlier call in the step changed the stored
>   Automation state the consequence line reads), or nothing is parked (a path the batch did not cover,
>   or a call outside the round's tool offer, which the discovery gate handles), the call is judged
>   alone. Batching is an optimisation layer, never a gap. Every dispatch path reaches the one tool loop
>   in `assistant._run_turn` (chat, an Automation run and its continuation, a Codex native call), so that
>   is the one place a step is batched; a redemption carries the confirm token and is never judged.
>
> Measured (2026-10-13, synthetic, one Mac, Claude Code `sonnet` through `ClaudeCliProvider`): five
> batches of three calls took mean 4.2s / max 5.2s (5/5 parseable, none over the 10s budget) against
> 3.6s / 4.6s for a single call in the same session — about 4s for a step that cost ~11s sequentially.
> One of three further samples of a batch reply carried a self-correction after the JSON object; the
> strict parser read it as unusable and asked for every call, which is the fail-closed behaviour working.

## Consequences

### Positive

- `auto` does what its label says, for core tools, built-in connector tools and any tool added
  later, with no declaration to forget.
- Every auto-approval is logged (`auto judge: allow <tool> on <model>`), verdict and tool only.
- One mechanism instead of four lists for the `auto` decision.

### Negative

- **The judge is an LLM, not a wall.** For everything it judges, a wrong `allow` is possible.
  A call where the judge structurally cannot know the blast radius —
  `delete_transactions_where`, which names a filter, not rows — is left to the judge's instruction to
  ask on a bulk delete; an owner who wants it to ask every time sets it to Ask and uses Manual, or
  Blocks it. `skip` remains the explicit no-net mode and `blocked` the explicit no-go.
- **Auto is the default mode for a new conversation (2026-09-04)**, so the judge now runs by
  default: one small model call per would-ask write on the owner's own model, adding latency and
  cost. Core reads and every non-`auto` mode pay nothing.
- A user on a CLI model gets no judging until they pin another model; Auto asks instead.
- The judge sees arguments, not the resolved preview, for the 15 self-staging tools.

- **Pre-existing, now higher-stakes:** `registry._permission_gate` fails OPEN on any internal
  error (it runs the call ungated). If staging a card throws — a database lock in
  `stage_pending`, say — a judge `ask` becomes an execution. That is the gate's deliberate
  direction for every tool, not new here, but an Auto write now leans on it more than before.
- Every auto-approved write in an Automation run leaves only an INFO log line; the existing
  `surface_unattended_write` notification (default on) is not wired to it.

### Follow-ups

- Run the eval corpus (`python -m backend.evals.auto_judge`, shipped with this change) against
  candidate pins, including `openrouter:typesafe/jev-router`; the bar for trusting a pin is
  zero false-allows across the corpus. Not yet run live: it needs an OpenRouter key and spend.
- An owner-written policy field for the judge (all three reference systems have one).
- Judge after resolution (so it sees the resolved row); a per-turn "this turn read untrusted
  content" signal; a content-free `judged` field in the tool-call log (it is content-free by
  design, so the reason stays in the INFO log).
- Extending Auto to external MCP tools is a separate decision: `call_external_tool` treats the
  stored level as the only authority (ADR `2026-08-13-mcp-permission-config-is-the-single-authority`).

## Alternatives considered

- **Keep a two-name hardline backstop in code until an eval earns its removal** — rejected by
  the owner as a hardcoded list; **adopted instead as stored settings** (see the Decision). Prior
  art backs having a floor at all: Claude Code (deny/ask rules), Codex (destructive annotations)
  and Hermes (hardline blocklist) each keep a deterministic floor beside their model judge, and
  Codex issue #18840 reports its reviewer approving a destructive `rm -rf`. A first cut adopted a
  stored floor for the two bulk tools; it was withdrawn before shipping (ADR
  `2026-10-11-permissions-are-one-value-per-tool`), so no floor and no name list.
- **Per-tool `risk=` traits on `@tool`** — rejected: cannot cover connector or external-MCP
  tools, and is the same hand-maintained list in another place.
- **Deterministic rules from the arguments only** (bulk count, `force`) — rejected as the sole
  mechanism: it cannot weigh intent ("you asked me to email Priya, not this address"). The
  generic argument facts are given to the judge as context instead of being enforced.
- **A fixed cheap model per provider** — rejected: a sibling table goes stale with every
  release; the pin plus "your own model" default avoids it.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), design co-authored turn by turn with the repo owner.
- **Lane / branch:** `worktree-per-call-auto-approval`.
- **Alternatives weighed:** hardline backstop kept vs removed; per-tool traits vs real-time judge; rules vs classifier vs hybrid.
- **Driving artifacts:** `docs/qa/acceptance/auto-approval-judge.md`; `tests/test_auto_judge.py`, `tests/test_auto_judge_batch.py`.
- **Why this way (one line):** the owner's complaint was that Auto was a hardcoded list that never looked at the call — a real-time judge over name, description, parameters and intent is the only version that also covers connector tools.
