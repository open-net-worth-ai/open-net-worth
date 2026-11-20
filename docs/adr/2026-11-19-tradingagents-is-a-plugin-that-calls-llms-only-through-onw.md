---
number: 2026-11-19
title: TradingAgents is a plugin that calls LLMs only through ONW
status: Accepted
date: 2026-11-19
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-11-19. TradingAgents is a plugin that calls LLMs only through ONW

## Context

TradingAgents (TauricResearch, Apache-2.0) runs a multi-agent research pipeline on one ticker: four
analysts, a bull/bear debate, a research manager, a trader, a three-way risk debate and a portfolio
manager, ending in a five-step rating. We want it on the asset page and behind Lucius, with the
owner's real investing amounts as context so the trader and risk team can size advice against what
is held.

The forces:

- It is third-party code with a heavy tree (pandas 3, LangChain, LangGraph, three vendor SDKs) and
  its own LLM clients that read vendor API keys. Importing it into ONW's interpreter would put that
  tree, and a path to a vendor, inside the process that holds every secret.
- Every model call in ONW goes through one provider config, one permission/disable switch and one
  cost path (`ai/cost_tracker`). A second set of keys and a second meter is exactly what
  `docs/INVARIANTS.md` rules out for AI spend.
- Real amounts are PII-grade data. They go to the owner's own AI provider only, at a level the
  owner picks, and the owner sees the exact payload before it leaves.
- Subscription transports (Claude Code, Codex, Antigravity) are ONW's way to run on a plan rather
  than a key. Their sessions are pooled or parked for Lucius's multi-round turns; TradingAgents
  re-sends its whole transcript each call and never wants that.
- Research is not money: no net worth, budget or ledger number may move, and nothing may place an
  order.

Facts of the pinned source that shaped the build are in `docs/specs/trading-agents-pinned-findings.md`.

## Decision

We will integrate TradingAgents as a **runtime-installed plugin in its own process**, and let it
reach a model **only through an ONW loopback bridge**.

1. **Isolation.** The environment is a venv of its own under `<data dir>/integrations/trading_agents/`,
   built by a click on the connector page (never at boot, never from a tool, refused in a demo and
   in QA) from ONE pinned commit, `9fa1196fffbcfd9de5146d2dd2869df84ab99838` (v0.7.1, 2026-11-04;
   `install.TA_COMMIT`). It is installed, not vendored. A bump is a code review against the pinned
   findings plus a release note. A run is one subprocess, `<venv python> -I ta_worker.py`, started
   only by `ai/trading_agents/runner.py` (one at a time, at most 3 queued). The worker imports the
   standard library and `tradingagents` only, and nothing imports it (import-linter contract plus an
   AST test). Its environment is `scrubbed_environ()` plus the per-run bridge token and the two
   optional data-vendor keys, never an LLM key, with HOME and every XDG/AppData folder pointed at
   the run's scratch folder and `.env` loading off (the worker also replaces python-dotenv's
   functions before it imports TradingAgents, as the environment variable is not honoured by every
   version). All three TradingAgents paths (results, cache, memory log) are forced into the
   connector's scratch folder, never `~/.tradingagents`. The install builds from a hash-locked
   dependency list (`--require-hashes --no-deps`, wheels only; a missing lock FAILS the install),
   then the pinned commit, then a `pip check`, and its self-test must find the pinned commit and
   import the modules TradingAgents loads lazily; installer and self-test run in that same
   redirected environment and a scratch working directory.
2. **The loopback LLM bridge.** TradingAgents is configured with its `openai_compatible` provider
   pointed at a per-run server on `127.0.0.1` (`ai/trading_agents/llm_bridge.py`), authenticated by a
   token valid for that run only, accepting loopback peers only. The worker sees two aliases,
   `onw-deep` and `onw-quick`; any other model name is refused, so it cannot choose a model the owner
   did not approve. The bridge translates OpenAI chat-completions to ONW's provider messages
   (`openai_shape.py`, pure) and calls the owner's own `AIProvider`, so a provider the owner turned
   off refuses the call and is never rerouted. **No key reaches the worker** because the worker never
   needs one: a vendor key would be a way around the owner's provider choice, the usage totals and
   the caps. It runs no tool: TradingAgents' own data tools run in the worker and the bridge only
   hands the model's tool calls back. Nothing logs a message body (bodies carry amounts); a call
   logs id, alias, tokens, latency and status.
3. **One cost path, measured at the bridge.** The bridge records one cost row per call under the
   source `trading_agents`, stamped with the run id, in the bridge's own process: tokens and dollars
   never come from the worker, which is untrusted for accounting. A transport that already records
   cost inside `stream_chat` (Antigravity, `RECORDS_COST_IN_STREAM_CHAT`) takes the run's
   `cost_attribution` instead of a second row.
4. **CLI transports and the one-shot turn.** `core/one_shot_turn.one_shot_turn()` marks the task, and
   the CLI transports' own "may I keep this open" switches read it and say no: a fresh CLI process
   per request, no pooled session, no parked Codex turn (so ADR
   `2026-10-05-codex-turns-stay-open-across-tool-rounds` is unchanged for Lucius). The bridge reads
   each provider stream to `done` instead of cutting it at the first tool call, because the Claude
   Code and Antigravity transports report the round's usage after its calls. Subscription calls are
   stamped `plan_included`: counted as calls, not dollars, and never blocked by the monthly dollar
   cap. A CLI transport gets 2 parallel calls by default (4 on an API). Evidence: **proven live for
   Claude Code; Codex and Antigravity are covered by fixtures only** and are not claimed as live-proven.
5. **Real-amount context at three levels.** `services/trading_agents/context.py` builds the
   `PortfolioContext` TradingAgents takes (`cash`, one `currency`, `positions[{ticker, quantity,
   average_price}]`), from `investments_book` (never recomputed): `investments` (the default;
   investable cash and every held, vested position of an investment account), `position` (cash and
   the analysed ticker only) or `ticker` (`None`, which TradingAgents reads as "not provided", never
   as a flat book). Everything is converted to the ticker's quote currency at current rates and every
   conversion is disclosed; a figure with no known rate is left out and named, never converted 1:1.
   No account name, institution, transaction, payee, income, budget, net worth, liability, retirement
   balance, owned asset, thesis, memory or profile ever leaves. The exact dict is frozen onto the
   run (`context_sent`) and shown to the owner before it starts. Data vendors get the ticker and the
   date, never an amount.
6. **Caps are enforced, not displayed.** A per-run dollar cap and a model-call cap are enforced by
   the bridge itself (it refuses to admit the next call), with the runner as backstop. A call
   reserves its worst case before it is made: its request bytes plus the provider's OWN output
   ceiling (32,000 tokens on Anthropic, 64,000 elsewhere), never the reply length the worker asks
   for. A call whose worst case alone is more than the run has left is refused even with nothing
   else outstanding, so a cap is not overshot by in-flight calls either. A model call also has an
   overall timeout, and `stop()` abandons a call that ignores its cancel instead of waiting. A 30-minute wall clock ends a run as `timed_out`.
   The monthly cap is checked once, at submit, against the run's estimate (median of the last ten
   matching finished runs, else a static heuristic) and refuses the run with the numbers in the
   message. The statuses `stopped_budget`, `stopped_calls`, `timed_out` and `interrupted` (an app
   restart; `recover()` at boot) are terminal and keep what was spent.
7. **A write choke and a flag.** `runs.apply_run_change` is the one writer of `trading_agents_runs`
   (one transaction per op, validated transitions, a data event after commit), behind flag
   `trading_agents` (default off: the connector, card and tools hide, runs are kept). The Lucius
   start tool is `external_effect`, seeded Ask, returns at once, and judges the result against the
   owner's thesis afterwards through `services/thesis_judgment`; the thesis text never goes to the
   worker.

## Consequences

### Positive

- No LLM key, vendor client or third-party import exists in ONW's process or in the worker's
  environment; turning a provider off turns TradingAgents off for it.
- Spend is in the same usage totals, per-source breakdown and run rows as everything else, with a
  hard stop a runaway debate cannot pass.
- The owner sees, before every run, the exact amounts that leave and who receives them.
- Removing the plugin is deleting a folder; run rows survive.

### Negative

- A large install (a Python venv with pandas, LangChain and three vendor SDKs we never use) and a
  15-minute install timeout.
- TradingAgents' `Memory Log` node keeps ONE decision log in a shared memory folder across runs, so
  text derived from an earlier run's portfolio context (decision text, past context) can be read back
  into a later run of the same tool, including at a lower context level than the earlier run. It
  stays on this computer and inside the owner's provider's scope, but it is shared state we do not
  own. `remove_environment` deletes it; there is no per-run isolation of it.
- One run at a time, and a subscription run is slow (a fresh CLI process per call).
- The pinned source's quirks are ours to carry: there is no Risk Judge node, analysts and the memory
  node run in parallel, and ONW's `max_risk_rounds` is `max_risk_discuss_rounds` in TradingAgents
  (translated in the worker). Both model tiers run at medium effort, as TradingAgents sends none.
- Codex and Antigravity carry only fixture evidence until someone runs them live.
- **The worker is third-party code that can read the Open Net Worth data folder.** It is not
  sandboxed (no chroot, seccomp or separate user — an OS sandbox is out of scope), so the redirected
  HOME and the scrubbed environment remove what it would find by habit, not what it could find on
  purpose. Hash-locked dependencies reduce supply-chain risk but do not confine it, and building the
  pinned commit's own source distribution still fetches its build backend unpinned (a pip config
  that pins it is not feasible for a VCS install). Whatever the worker or its stderr says is masked
  by value (the bridge token, the data-vendor keys, each model's provider key) and by shape before
  it is stored, but a secret it transforms first is not recognised.
- **"On this machine" is not "free".** A local endpoint is judged by its parsed host (exactly
  `localhost` or a loopback address, never a prefix, never with userinfo) and an unknown price is
  allowed only there. It still counts against the call cap, and a price in the table or a cost the
  endpoint reports is metered like any other. What cannot be known: a loopback LiteLLM or other
  proxy that forwards a model with no price in the table to a paid API and reports no cost. Its
  calls run, cost money and are invisible to the dollar caps; only the call cap holds them.
- A CLI is plan-included only when its provider's own cost basis says so for an account with no
  stored key AND the CLI reports it is signed in (Antigravity has no sign-in probe). Anything
  else — a stored key, a silent or signed-out CLI, an error — is metered: the monthly cap, the
  estimate and the per-call reservation apply. A premium model with a high output price can have a
  worst case above the default $2 per-run cap, and is then refused up front until the cap is raised.

### Follow-ups

- Run a live Codex and a live Antigravity analysis and record the result here.
- Re-read `docs/specs/trading-agents-pinned-findings.md` against any new commit before moving
  `TA_COMMIT`.
- Product page `docs/product/features/trading-agents.md`; `NOTICE` carries the Apache-2.0
  attribution.

## Amendment 2026-11-20 — Lucius's take reads the latest run

The asset page's "Lucius's take on your position" now also reads the ticker's TradingAgents
analysis while the feature is on, combined with Lucius's own analysis and never replacing it
(spec §16). The decision:

- **Input is a digest, not the run.** The latest `done` run of the ticker, at most 14 days old by
  `trade_date`, and only while the flag is on and the connector is set up and not paused
  (`services/trading_agents/take_input.py`): rating, date and age, the decision text capped at
  1,200 characters and sanitized as third-party output, the context level it was sized against and
  the model names. The audited `context_sent` amounts and the reports are never read; the store
  method returns only those columns. A `REVIEW` run (no rating settled) is not an input.
- **The rating is an input, not an instruction.** A rule appended to the synthesis prompt only
  when a digest exists tells the model to attribute it, say where it agrees or disagrees, keep its
  own call and verdict and add no length. `thesis_judgment` / `THESIS_RULE` still own thesis
  judgment, unchanged. Flag off or no usable run: the prompt and the written brief are identical to
  before.
- **Provenance** (`trading_agents_run_id`, `_signal`, `_trade_date`) is recorded on the asset brief
  (a payload table, no migration) and served as `trading_agents_input`. A take written without a
  run carries none.
- **Refresh uses the existing trigger.** A finished run asks `core.take_refresh` (a leaf seam,
  because `ai` may not import `monitoring`, which reaches `api`) to run
  `analyst.trigger_on_demand_brief_refresh(force=True)` as a background task: its in-flight and
  failure guards hold, nothing runs a model inside the run's path, and an instrument with no take
  yet costs nothing. If that trigger declines, `_key_issues_force_stale` is also true while the
  newest usable run is not the one the take used, so the next page open regenerates it.
- **The take never starts a run** (it costs money); it only uses an existing one. A finished run
  does start one background model call on the take's provider, once per run and only for a ticker
  that already has a take.
- **New disclosure.** The decision text (up to 1,200 characters) now also goes to the take's own
  model provider, the one that already receives the position block. The product page says so. The
  text may restate figures the run was shown (a position size, a price); the digest does not
  filter them.

Review fixes (same day):

- **Considered is not used.** The chip's `trading_agents_run_id` is the run the take USED.
  Staleness compares `trading_agents_considered_run_id`, set at the end of every completed pass
  that had a digest whatever it wrote (empty take, refused answer, off-subject skip, no evidence, a
  swallowed write), so a pass that records no run cannot be asked again by every page load.
  `trading_agents_attempted_run_id` / `_at` (set when the pass starts) bound a pass that never
  completes to one retry per run per 12 hours; a genuinely newer run is still taken once.
- **The digest is fenced data.** It is its own `--- BEGIN/END TRADINGAGENTS DATA ---` block beside
  the untrusted news, scrubbed like the news snippets, on one line per field, with the fence words
  stripped from the text; never part of the trusted context header.
- **Only the take's own pass reads it** (`brief_stale`). A crash, news-spike or material-news pass
  writes the Intel timeline and can push, so it never sees the digest; the rule also forbids the
  mention in `message`, `headline`, `analysis`, `why`, `impact` and `actions`. A take rewritten by
  such a pass drops the chip until the next `brief_stale` pass.
- **The page hears about the take.** A completed pass publishes an `intel` data event; the refetch
  it causes finds nothing superseded.

## Amendment 2026-11-20 (2) — One view: research on the stock, overlay on the position

Owner feedback: a held stock at 85% of the portfolio read "Hold" in the take and "Sell" on the
TradingAgents card, and the take called that a disagreement; a stock not held read WAIT against a
Sell. Two verdicts competed (spec §17). The decision:

- **They fill each other's gaps.** TradingAgents is the stock research; the take is the position
  overlay (size, concentration, thesis, levels, alerts, run age, whether the run saw the position)
  and writes ONE recommendation that uses it. The rule no longer asks for "agrees or disagrees";
  the take does not re-derive the stock view.
- **A vocabulary bridge, not a mapping of ratings to orders.** `services/trading_agents/vocabulary.py`
  holds one pure table: for each rating and held / not held, the actions the research supports and
  a plain reading. Held: Buy/Overweight add or hold, Hold hold, Underweight trim or hold, Sell sell
  or trim. Not held: Buy/Overweight buy or wait, anything else wait; a Sell is "not a buy now".
- **Checkable.** The verdict carries `ta_overlay_reason`. `position_verdict.normalize` replaces an
  action outside the allowed set, given no valid reason, with the nearest allowed one (and a plain
  `why`), drops the orders that no longer fit, and records `ta_aligned`. Two reasons are facts about
  the run (`stale_run`, `position_unseen`) and count only when true. No digest: unchanged.
  Alternative rejected: a second model pass to reconcile (cost).
- **Cheaper.** Decision text capped at 600 characters (was 1,200), the models line leaves the block,
  the rule is shorter, and with a digest the narrative is at most 2 sentences and `why` 1.
  `key_issues` and the evidence are not trimmed. The 1,200-character figure above is superseded.
- **Served.** `trading_agents_input` gains `view`, `held` and `allowed_actions` so the page does not
  carry its own copy of the table.

## Alternatives considered

- **Import `tradingagents` into ONW's interpreter** — rejected: dependency conflicts (pandas 3) and
  a vendor client with a key inside the process that holds every secret.
- **Give the worker the owner's vendor key** — rejected: bypasses provider disable, the usage totals
  and the caps, and puts a key in an untrusted process. It would also make subscription transports
  impossible.
- **Let TradingAgents talk to vendors with its own keys and meter its spend afterwards** — rejected:
  a second cost path that can only report, not stop.
- **Take tokens and cost from the worker's own report** — rejected: the worker is untrusted for
  accounting; the bridge sees every call.
- **Reuse Lucius's pooled CLI sessions and parked Codex turns** — rejected: TradingAgents re-sends
  its transcript and never answers a parked call in-thread; pooling would leak state between calls
  and park turns nobody resumes.
- **Cut the provider stream at the first tool call** — rejected: usage arrives after the calls on
  Claude Code and Antigravity, so cost would be lost.
- **Send the whole book, or no choice** — rejected: the owner picks one of three levels, defaulting
  to investments only, and sees the payload first.
- **Vendor the source into the repo** — rejected: heavy, Apache-2.0 attribution aside, and it would
  make a bump a merge rather than a pin.

## Agent provenance

- **Decided by:** orchestrating Claude agent, with work-package implementers WP1 to WP8 on
  `feat/trading-agents`.
- **Lane / branch:** `feat/trading-agents` (head `a266ec4b` when written).
- **Alternatives weighed:** in-process import, worker-held vendor keys, post-hoc metering, pooled CLI
  sessions, vendoring (see above).
- **Driving artifacts:** `docs/specs/trading-agents.md`, `docs/specs/trading-agents-pinned-findings.md`,
  `docs/specs/trading-agents-ui.md`.
- **Why this way (one line):** the worker is third-party code that must never hold a key or meter
  itself, so every model call crosses one loopback bridge that is ONW's provider, cost row and cap.
