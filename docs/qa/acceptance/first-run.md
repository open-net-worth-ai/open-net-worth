# Acceptance — First run & onboarding

Design: [`docs/superpowers/specs/2026-08-22-first-run-and-onboarding-design.md`](../../superpowers/specs/2026-08-22-first-run-and-onboarding-design.md)
Plan: [`docs/superpowers/plans/2026-08-22-first-run-onboarding.md`](../../superpowers/plans/2026-08-22-first-run-onboarding.md)

**Test instance:** `make qa-fresh` → web `http://localhost:8094`, api `http://localhost:8093`.
`make qa-fresh-reset` wipes it so first run appears again. NEVER drive `:8080` or `:5174`.

Several criteria need a *genuinely* fresh instance — run `make qa-fresh-reset` before each.

---

## The three screens

### FR-SHELL-3-STEPS
- GIVEN a fresh instance (`make qa-fresh-reset && make qa-fresh`)
- WHEN I open `http://localhost:8094`
- THEN first run is on screen and reads "Step 1 of 3"

### FR-SHELL-NOT-A-MODAL
- GIVEN first run is on screen
- WHEN I look at the page behind it
- THEN there is no dashboard, no nav and no "Add first account / Import a statement" row
  visible behind a dialog — first run occupies the whole viewport
- AND there is no floating "Ask Lucius" pill in the corner

> Why: the outgoing wizard was a modal over the Overview tab, whose own empty state offered a
> DIFFERENT and larger set of routes. Two disagreeing onboarding flows were legible at once.

### FR-CCY-FULL-LIST
- GIVEN I am on screen 1
- WHEN I open the "You think in" dropdown and search for `CHF`
- THEN Swiss Franc is selectable
- AND the same holds for `KRW`, `BRL`, `AED`, `NZD` and `TWD`

> Why: the outgoing list was 14 hardcoded codes on the one step with no skip, so users in those
> countries could not pick their own base currency at all — and base currency mis-stamps every
> `base_amount` under the historical-rate lock.

### FR-CCY-LOCALE-DEFAULT
- GIVEN I am on screen 1
- WHEN I look at "You think in" before touching it
- THEN it is pre-filled from the machine locale (on an `en-SG` machine: `SGD`), not blank

---

## Screen 2 — detection first

### FR-AI-DETECTS-LOCAL-CLI
- GIVEN `claude` (or `codex`) resolves on this machine's PATH
- WHEN I reach screen 2
- THEN it reads "Found Claude Code on this machine", shows the resolved binary path, and says
  "No API key needed"
- AND there is NO API-key field on screen

### FR-AI-USE-DETECTED
- GIVEN screen 2 shows a detected CLI
- WHEN I click "Use this"
- THEN first run advances to screen 3
- AND `GET /api/model-config` reports `provider: "claude_cli"` with `has_api_key: false`

> Why: `detected_clis` has been served by `GET /api/model-config/providers` all along; the
> outgoing wizard ignored it and asked a developer with `claude` installed to paste `sk-ant-…`.

### FR-AI-KEYED-FALLBACK
- GIVEN neither CLI is installed (or detection fails)
- WHEN I reach screen 2
- THEN a provider dropdown and an API-key field are shown instead

### FR-AI-SAVE-ACTUALLY-WORKS
- GIVEN I pick a provider and complete screen 2 by any route
- WHEN I then ask Lucius anything
- THEN he answers — he does NOT say "AI provider is not configured"

> Why: **the outgoing wizard's AI step could not configure AI at all.** Verified against a live
> instance: `{provider:'anthropic'}` → 422 "AI model is not configured"; `{provider:'ollama'}`
> and `{provider:'openai'}` → 422 "provider must be one of [...]". `provider` is a TRANSPORT and
> a `model` is required. The step advanced anyway, so users finished onboarding believing AI was
> set up.

### FR-AI-DECLINE-IS-A-CHOICE
- GIVEN I am on screen 2
- WHEN I look at the options
- THEN "Not now" is a real button (not a ghost "Skip") and the screen states plainly what stays
  off without a model
- WHEN I click it
- THEN first run advances and no model config is written

---

## Completion

### FR-DONE-SURVIVES-A-NEW-BROWSER
- GIVEN I have completed first run
- WHEN I open `http://localhost:8094` in a private window
- THEN first run does NOT reappear

> Why: completion used to live in `localStorage['onboarding-dismissed']` — per-browser — while
> the backend keyed freshness off `profile.name`, a field first run never wrote.

### FR-DONE-LUCIUS-GREETS
- GIVEN I completed first run WITH a model configured
- WHEN first run closes
- THEN the docked Lucius panel is open and Lucius has greeted me unprompted
- AND `__ONBOARDING_INIT__` is NOT visible anywhere in the transcript

### FR-DONE-LUCIUS-ASKS-FOR-A-NICKNAME
- GIVEN Lucius has greeted me
- WHEN I answer "Alex Tan, but call me Alex"
- THEN he addresses me as Alex from then on
- AND `GET /api/profile` shows `name: "Alex Tan"` AND `nickname: "Alex"`

### FR-LUCIUS-NOT-RAILROADED
- GIVEN a completed first run on an instance with no accounts yet
- WHEN I ask Lucius something unrelated ("what can you do?")
- THEN he answers the question rather than redirecting me into an onboarding interview

> Why: the block used to say his "ONLY job right now" is a five-question interview.

### FR-LUCIUS-HONEST-ABOUT-MIGRATION
- GIVEN Lucius asks where my money is
- WHEN I say "I'm coming from YNAB"
- THEN he says budget-history migration is not built yet AND that my YNAB *transactions* can
  sync today via the connector — he does not imply a full migration exists

---

## The empty states

### FR-EMPTY-OVERVIEW-ROUTES
- GIVEN a completed first run with no accounts
- WHEN I open the Overview tab
- THEN it offers: connect a bank or broker · import statement files · restore a backup ·
  coming from YNAB or Actual Budget (marked "coming soon") · add an account myself

### FR-EMPTY-ACCOUNTS-PARITY
- GIVEN the same instance
- WHEN I open the Accounts tab
- THEN it offers only two choices: "Add my accounts with Lucius" and "Add an account myself"
- AND it does not show Connect, Import, Restore, migration, or a "Start with" label

> Why: the Accounts empty state answers one decision — assisted or manual creation. Import,
> restore, migration, and connector management remain available in their dedicated surfaces.

### FR-EMPTY-ROUTES-GO-SOMEWHERE
- GIVEN the Overview empty state
- WHEN I click "Connect a bank or broker"
- THEN the Connectors tab opens
- WHEN I click "Import statement files"
- THEN the import flow opens
- WHEN I click "Add an account myself"
- THEN the add-account modal opens

### FR-EMPTY-COMING-SOON-IS-INERT
- GIVEN the Overview empty state
- WHEN I click "Coming from YNAB or Actual Budget"
- THEN nothing navigates and no error appears — it is visibly not-yet-available, never a broken
  screen

### FR-EMPTY-LUCIUS-ROUTE-GATED
- GIVEN an instance with NO model configured
- WHEN I look at either empty state
- THEN "Just tell Lucius what you have" is absent

---

## The dock

### FR-DOCK-HIDDEN-DURING-FIRST-RUN
- GIVEN first run is on screen
- THEN no floating "Ask Lucius" pill is visible

### FR-DOCK-HONEST-WITHOUT-A-MODEL
- GIVEN I completed first run choosing "Not now" for AI
- WHEN I look at the floating dock
- THEN it reads "Set up Lucius", not "Ask Lucius"
- WHEN I click it
- THEN Settings › AI opens

> Why: it used to offer a chat on an instance with no provider — permanently, for anyone who
> declined the model step.

### FR-DOCK-NORMAL-WITH-A-MODEL
- GIVEN a model is configured
- WHEN I look at the floating dock
- THEN it reads "Ask Lucius" and opens the composer

---

## Isolation (harness safety)

### FR-QA-ISOLATED
- GIVEN `make qa-fresh` is running
- WHEN I inspect `/tmp/onw-fresh/data`
- THEN a fresh `onw.db` exists there
- AND `~/.open-net-worth/` is untouched
- AND booting with `ONW_QA_MODE=1` against the live `DATA_DIR` refuses to start
