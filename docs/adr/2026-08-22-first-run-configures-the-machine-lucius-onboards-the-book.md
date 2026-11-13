# First run configures the machine; Lucius onboards the book

- **Date:** 2026-08-22
- **Status:** Accepted
- **Supersedes in practice:** `.claude/rfcs/lucius-onboarding.md` (Approved 2026-05-31, only
  ever half-shipped — see Context) and the 7-step `OnboardingWizard` modal
- **Agent provenance:** designed in-session with the owner via `superpowers:brainstorming`, spec
  at `docs/superpowers/specs/2026-08-22-first-run-and-onboarding-design.md`, plan at
  `docs/superpowers/plans/2026-08-22-first-run-onboarding.md`. Several owner rulings recorded
  inline below.

## Context

First run was organised around *configuring a product*, not around *where the user's money
already is*: seven modal steps, four of them before any number could appear, whose entire
data-entry surface was one manually-typed account name and balance. Meanwhile the app shipped
nine financial aggregators, seven statement formats, an institution-search router and a portable
restore, and first run referenced none of them.

An audit against the running app (not just the code) found four defects that are each worse than
the length complaint:

1. **The AI step could not configure AI.** Verified live: `{provider:'anthropic'}` → 422 *"AI
   model is not configured"*; `{provider:'ollama'}` and `{provider:'openai'}` → 422 *"provider
   must be one of ['anthropic','openai_compatible','claude_cli','codex_cli']"*. `provider` is a
   TRANSPORT and a `model` is required (except `codex_cli`). The step swallowed the failure and
   advanced, so users finished onboarding believing Lucius was set up. The per-provider key WAS
   stored — only the instance default was never set.
2. **Local-CLI detection already existed and first run ignored it.**
   `ModelConfigStore.detected_clis()` resolves `claude`/`codex` via `shutil.which` and is served
   by `GET /api/model-config/providers`; `claude_cli`/`codex_cli` are first-class transports that
   enrol by DETECTION with no credential. The wizard offered `anthropic|openai|ollama` and asked
   a developer with `claude` on their PATH to paste `sk-ant-…`.
3. **Lucius-led onboarding was half-alive.** `prompts.py` still injected a block telling Lucius
   his *"ONLY job right now"* was a five-question interview, while `ChatPanel`'s
   `__ONBOARDING_INIT__` trigger — built, tested, present — was never armed by any caller. A
   fresh user got silence, and any unrelated question got railroaded.
4. **The wizard contradicted the page underneath it.** It was a modal over Overview, whose own
   empty state already offered *Add first account · **Import a statement** · Load demo data* —
   a STRICTLY LARGER set than the dialog covering it, in different words.

Plus: base currency was a 14-code hardcoded list on the one step with no skip (so Switzerland,
Korea, Brazil, the UAE, New Zealand and Taiwan could not choose their own base — under the
historical-rate lock that mis-stamps every `base_amount`), and "is this instance set up?" had two
disagreeing answers — `is_fresh_setup()` sniffing `profile.name`, a field first run never wrote,
plus a per-browser `localStorage['onboarding-dismissed']` backstop.

## Decision

**Split first run by what the two halves are actually good at.**

- **The wizard configures the MACHINE** — three screens: where data lives + base currency,
  connect Lucius, an optional password. Deterministic, and it must never depend on an LLM.
- **Lucius onboards the BOOK** — name and nickname, then "where's your money right now?", then
  accounts, holdings and income, in conversation.

The dividing rule, which is what actually decides where a field goes:

> **Anything that must be true before the first money row is written belongs to the wizard.
> Everything that enriches after belongs to Lucius.**

So base currency stays in the wizard (historical-rate lock; and a user who declines AI would
otherwise never be asked and would silently get USD), while name/nickname move to Lucius — a form
asking "Name" and "What should Lucius call you?" side by side, before Lucius exists, is strange,
and a conversation takes *"Tai, but call me T"* in one turn. **`nickname` was asked by neither
flow before this**, despite Settings labelling it *"What should Lucius call you?"* and
`prompts.py` using it to address the user.

**Six further rulings:**

1. **Detection before interrogation.** Screen 2 calls the endpoint that already exists and offers
   a found CLI as one click with no key. The guiding principle for the whole redesign, in the
   owner's framing — *"it makes it feel easier and faster and more smart for them"* — is that
   **the app demonstrates competence by knowing, not by asking.**
2. **Force the decision, not the key.** No ghost "Skip" on the AI step: three equal choices, with
   "Not now" stating plainly what stays off. A skipped step reads as *"did I just break
   something?"*; a chosen one does not.
3. **First run is NOT a modal over the app.** It owns the viewport for three screens, then gets
   out of the way — never a dialog competing with the screen underneath it.
4. **No new "get your data in" hub screen.** One was designed and then rejected: four of its six
   routes already had UI homes (`AccountHeader`'s Connect/Import, Settings › Data, the Accounts
   tab), so it would have been a menu pointing at existing screens — a second surface to maintain
   and the exact drift `catalog.ts` already warns about. The routes go on the **empty states**
   instead, from ONE shared definition, where the need actually is and where they self-retire
   once data exists.
5. **One server-side completion stamp.** `OnboardingStateStore` (a `runtime_state` namespace row,
   checkpoint-EXCLUDED) replaces both the `profile.name` sniff and the localStorage flag.
6. **The dock must not offer a chat it cannot deliver.** With no provider configured it reads
   "Set up Lucius" and routes to Settings › AI. Deliberately NOT hidden — hiding removes the only
   visible route back to configuring him, which is the wrong outcome for exactly the user who
   declined.

### Two predicates, not one

Splitting the completion stamp out surfaced a bug that would have shipped silently:

- `is_fresh_setup()` — *"should first run appear?"* — keyed on the **stamp**.
- `is_empty_book()` — *"is there anything in here yet?"* — keyed on the **data**.

First run stamps completion and *then* hands off to Lucius. Gating his onboarding block on
`is_fresh_setup()` would therefore silence his instructions at exactly the moment they are
needed. The block keys on `is_empty_book()`, so it is present while the book is empty and
disappears on its own once an account or holding exists — which is also what stops it riding
every turn forever. `api/setup.py`'s duplicate copy of the predicate (which had already drifted
from the `services` one — different position reads) now delegates rather than re-implementing.

### Restore still configures the machine — 2026-10-02

Portable exports carry the book and its currency, but exclude AI provider configuration,
credentials, machine authentication and the completion stamp. A successful restore on a fresh
instance therefore continues to provider/model and optional security setup, rather than
marking setup complete. It skips currency selection and keeps the imported profile unchanged.

The existing non-portable `onboarding` runtime namespace records `restore_pending=true`.
That marker overrides the legacy populated-book compatibility check until normal completion
clears it. Web navigation and desktop startup resume at the provider page after reload or
restart. Preview and failed imports do not set the marker; imports into configured or legacy
populated instances retain their setup status. The ordinary empty-book setup path is unchanged.

## Consequences

- **Empty states become load-bearing.** Demo data was dropped from first run (owner call), so
  every tab a new user opens is zeros — the exact failure the 2026-05-31 RFC was written to fix.
  Lucius greeting from the dock is the primary mitigation; the shared empty-state routes are the
  secondary one. Recorded as an accepted cost, not solved.
- **The AI choice is now the hinge.** If Lucius *is* onboarding, "Not now" means no guided
  onboarding — acceptable only because the empty-state routes are a real fallback, and because
  detection-first keeps most users from facing the fork at all.
- **A fresh instance still lands on `/overview`**, not the `/lucius` default. That was leftover
  from the 2026-05-31 RFC but is now load-bearing: the dock only renders OFF the Lucius tab, so
  Overview is what lets the dashboard fill in behind the conversation.
- **Demo data carries an unfixed debt** if it ever returns as an on-ramp: seeded rows are
  distinguishable only by an `example-` id prefix that nothing uses, the only undo is a full
  `/api/reset`, and every demo transaction carries `source: "manual"` — so fabricated data reads
  in the provenance display as *"you entered this."*

## Alternatives considered

- **Intent router first** — open with *"Where is your financial life right now?"* and route
  immediately. Rejected by the owner: it asks for a commitment before the user knows what they
  are committing to, and it is self-defeating for the migration case specifically, whose preview
  ends on a Ready-to-Assign reconciliation screen a user who has never seen an envelope cannot
  evaluate. The router was not wrong, it was in the wrong place — it moved to the empty states.
- **A dedicated "bring in your data" hub screen** — see ruling 4. Also: the working title *"Get
  your money in"* reads as *deposit funds* in an app with live brokerage connectors.
- **Lucius leads everything, wizard is the no-AI fallback** — rejected: data location, provider
  and password must not depend on a model being reachable.
- **Retiring the `ONBOARDING MODE` block entirely** — rejected: its domain sequencing is
  genuinely good (`resolve_instrument` before `propose_position_add` to pin a live price source;
  physical metal's venue is its storage location; never block on cost basis). It was narrowed and
  re-gated, not deleted.
- **A Lucius-drives-the-UI navigate capability** (he switches tabs while explaining, repairing
  the empty-state cost) — explored and **parked**, at the owner's prompt *"am I over
  complicating this?"* It is a dependency of nothing here and its value cannot be judged until
  the basic flow exists. Note the READ half already ships (`state/screenSnapshot` — "Lucius can
  see this screen"); this would be its mirror. **Playwright MCP is not the mechanism**: it drives
  its own browser rather than the user's tab, it is DOM-level and brittle against a codebase with
  a class-churn lint rule, and a packaged desktop app has no attachable browser.
- **Config without `.env`** (platform data-root default, pointer file, "Move my data") — a real
  finding from the same audit: the default `DATA_DIR` is `backend/data`, *inside the repo
  checkout*, which is why `.gitignore` carries 58 hand-maintained entries for it. Split into its
  own plan as a distinct subsystem; screen 1 renders the path read-only until then.

## Amendment (2026-11-14): "Not now" is back on the AI step

Ruling 2 asked for a plain "Not now" on the AI step; a later change removed it ("an install with
no model is the product with its point removed"), so the model step had no way past. That locked
out the users bank sync without AI exists for (ADR
`2026-11-13-a-bank-connector-can-book-its-rows-without-ai`) and anyone keeping the book by hand:
first run owns the viewport, so no model meant no app. "Not now" is restored as ruling 2 framed
it, a ghost button beside Continue (the password step's Skip, same weight), with the step's lead
saying the rest of the app works without one. Setup then finishes and lands on the Dashboard, not
the Lucius tab: with no model there is no onboarding conversation to hand off to (FirstRun's
`onDone({withModel})`, read from `GET /api/model-config`, not from the click). Ruling 6 already
covers what follows: the dock reads "Set up Lucius", and a chat turn says the provider isn't
configured with a link to Settings.
