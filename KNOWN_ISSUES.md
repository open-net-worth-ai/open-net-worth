# Known Issues & Technical Debt

> ⚠️ **Not the full picture — `docs/launch/BOARD.md` is the live source of truth** for
> in-progress and newly-found issues (its Inbox: `make bug MSG=…`) and for what's currently
> deferred (`docs/launch/BOARD.md`'s "Parked / deferred" section — e.g. the custodian/holdings
> aggregation model, non-Latin-script statement matching, split-settlement matching). This file
> holds the longer-lived items that are worth a permanent home rather than a board entry — each
> one below was re-checked against the code in this worktree on 2026-08-23.

_Last updated: 2026-10-17_

Issues are rated by **impact** (how wrong does it make the numbers?) and **effort** to fix.

---

## 🟡 Medium — apply_* AI tools trust conversational confirmation, not a server-stashed nonce

**Observed in production (2026-06-12):** mid-conversation, the chat model re-invoked
`update_positions` with a previously-confirmed payload after the user had reverted it —
no fresh proposal card, no fresh confirmation. The propose→confirm→apply contract is enforced
by the system prompt convention (and per-payload `idempotency_key`s), not structurally: an
`apply_*` tool will accept any well-formed change the model passes, whether or not a matching
`propose_*` was confirmed this turn.

**Still open as of 2026-08-23** (re-verified against `backend/ai/tools/lots.py`, `equity.py`,
`income.py`): the interactive positions/lots/equity/income `apply_*` tools still rely on
`idempotency_key` (a double-book guard against retries), not a nonce the caller must present
back. Two *adjacent* mechanisms shipped since this was filed and are worth knowing about, but
neither closes this specific gap: (1) `ai/tools/code.py`'s sandboxed `run_code` now does exactly
the nonce-staged pattern described below — proof the pattern already works in this codebase, not
evidence this surface got it; (2) the unified `tool_permissions` store (`CLAUDE.md` → Storage
engine, 2026-08-14 permission merge) means every `apply_*` tool defaults to `needs_approval` and
is configurable per-tool, which raises the bar for an *unattended* replay but doesn't address a
model re-submitting an already-approved-this-turn payload after the underlying state changed.

**Fix direction:** on `propose_*`, stash the proposed change server-side (keyed by nonce, TTL,
single-use); `apply_*` requires the nonce, refuses unknown/expired/consumed nonces, and refuses
payloads that don't hash-match the stash. The chat-connector confirm flow and `run_code` already
do this — extend the mechanism to the interactive tool surface (positions, lots, equity,
income). Cross-cutting: touches every propose/apply pair + the assistant dispatch loop. Until
then, the mitigations are the per-domain write guardrails (e.g. grant-managed positions refuse
cost_basis/quantity/bucket outright) and checkpoint undo.

**Impact:** medium (money-adjacent bookkeeping writes; checkpoint-reversible). **Effort:** medium.

---

## 🟠 Medium-High — every chat transcript is readable through `run_sql_query`

**Owner decision 2026-08-28: known and accepted, left unfixed for now.** Documented here so it
is a deliberate position rather than an oversight.

`backend/db/sql_console.py` denies three tables — `secret_config`, `mcp_clients` (credentials)
and `sessions`. The module says plainly why the third is there:

> `sessions` is added for a different reason and is NOT a secret: it holds every chat transcript
> as multi-MB JSON columns. Reading it through SQL is a bad idea for size alone, and there are
> purpose-built tools for session history.

Revisions 0051-0056 then moved transcripts **out** of `sessions` and into the `session_messages`
table (ADR `2026-08-24-a-transcript-is-a-table-of-messages`). **The denylist did not follow the
data.** `sessions` is still denied and now holds no transcript; `session_messages`
(`id`, `session_id`, `role`, `content`, …) holds all of it and is readable:

```
session_messages denied?  False
denied set:               ['mcp_clients', 'secret_config', 'sessions']
```

So `SELECT content FROM session_messages` returns every message of every conversation. Three
things stack to make that worse than a large read:

1. **Scope escalation.** A turn normally sees only its own conversation. One query returns the
   entire cross-session history — a far wider window than the turn legitimately has, and exactly
   the reach a prompt injection wants.
2. **Transcripts are not just chat.** They carry prior tool results (financial rows already
   bounded out of live context), `[System:]` confirm notes, and anything a user pasted —
   `update_model_config`'s own description says "prefer the Settings UI for entering secrets"
   precisely because people sometimes paste keys into chat.
3. **Nothing gates it.** `sql_query_enabled` defaults **True**, and `run_sql_query` resolves to
   `always_allow` (`core`-owned, `mutates=()`), so no confirm card on any surface. Paired with
   `fetch_page`, reading the history was the first half of an exfiltration. Since 2026-10-17
   `fetch_page` asks before opening a URL the user or a search didn't supply (ADR
   `2026-10-17-a-fetch-runs-unasked-only-for-a-url-the-user-or-a-search-supplied`), so the
   outbound half needs a tap; the over-wide READ is still open.

**Why the guard test did not catch it:** `tests/db/test_sql_introspect.py` computes
`known − readable_tables() − DENIED_TABLES`, but `readable_tables()` is *defined* as "everything
not denied", so that set is always empty and the assertion can never fail. A newly added table
therefore defaults to READABLE, silently. Any real fix should make readability an explicit
allowlist so a new table fails CI until someone classifies it.

**Fix when it is picked up** (one line plus the test):

```python
_EXTRA_DENIED_TABLES = frozenset({"sessions", "session_messages", "sql_query_log"})
```

`sql_query_log` belongs in the same change — it stores submitted SQL verbatim by design, so it
accumulates whatever literals earlier queries contained.

**Impact:** medium-high (cross-session history disclosure; no credential tables reachable).
**Effort:** low. From the 2026-08-25 security audit (kept private).

---

## 🟡 Medium — loopback is trusted without a password (`AUTH_TRUST_LOOPBACK`)

**Owner decision 2026-08-28: known and accepted, left as-is.** Recorded so it stays a
deliberate choice rather than becoming an oversight.

`require_auth` lets a request through with no credentials when the TCP peer is loopback
(`backend/api/_deps.py`), and `AUTH_TRUST_LOOPBACK` defaults `True`
(`backend/core/config.py`). The intent is stated in the config comment — *"so the local
operator never has to log in"* — and for a single-user app opened at `localhost` that is the
right trade.

**The condition under which it stops being right:** it infers *"this is the owner"* from
*"this connection came from this machine"*. Those diverge the moment anything on the host
forwards traffic. Behind a same-host TLS reverse proxy (nginx/Caddy — the normal way to put
HTTPS in front of this app), the proxy receives the internet request and forwards it to
`127.0.0.1`, so **every remote visitor presents as loopback** and is trusted. No
`X-Forwarded-For` handling exists (deliberately — reading it would be spoofable), so there is
nothing to distinguish the proxy's hop from a genuine local one.

Safe today: direct `localhost` use, or binding `0.0.0.0` with no proxy (the peer is then the
real remote IP). Not safe: any same-host reverse proxy.

**If that day comes**, pick one: set `AUTH_TRUST_LOOPBACK=false`, trust loopback only over a
Unix-domain socket rather than `127.0.0.1` TCP, or adopt `--proxy-headers` and treat a
forwarded chain as not-loopback.

**Impact:** medium, and entirely deployment-shaped — nil on a laptop, total behind a proxy.
**Effort:** low (a default flip). Audit context:
the 2026-08-25 security audit (kept private), FINDING 4.

---

## 📋 Design decision — inbound MCP does not re-ask; the calling client owns approval

**Owner decision 2026-08-28.** Not a gap to close: the audit proposed failing closed on this
surface, and that proposal was REJECTED as wrong for the feature.

`mcp/adapter.py` binds no cost surface, so `should_gate` finds no card-capable surface and a
`needs_approval` tool executes rather than staging a confirm card. The audit read that as a
fail-open bug (FINDING 5) and suggested refusing such calls.

**Why refusing is the wrong answer.** Inbound MCP exists so the owner can drive this app from
another client (Claude Desktop and similar). Those clients already prompt before executing a
tool — that is where the human sits on this path. Refusing every `needs_approval` call would
make the feature inert on first use: every write tool dead until the owner walked the whole
permission table setting `always_allow`, to buy a second prompt for an action they had just
approved in the client. A person who deliberately connects their app to another app has
already expressed the intent; blocking it there is friction, not safety.

This is also consistent with the app's standing position (CLAUDE.md → Security-critical
areas): the permission config is the single authority, there is no unconfigurable floor, and
`always_allow` means allow everywhere. The Bearer token is the authentication boundary for
this door, and it is fail-closed — no token configured, nothing gets in.

**The one thing that remains true, and is a labelling matter rather than a control gap:** a
tool whose stored level reads `needs_approval` does not, in fact, ask on this surface. Nobody
is blocked by that, but anyone auditing the permission table should know the word does not
mean "a card appears" here. If it ever becomes confusing in practice, the honest fix is to
make the level *report* the surface's real behaviour — never to start rejecting calls.

**Also stale because of this:** CLAUDE.md's claim that inbound MCP "KEEPS nonce staging that
fires server-STORED args" so approve-then-substitute is "structurally impossible" — that
staging never triggers on `tools/call`, since it belongs to the confirm-card path that does
not run here. The *property* still holds for a different reason (there is no card to
substitute against), but the stated mechanism is not what provides it.

---

## ✅ Fixed

### ~~AI system prompt shows non-base-currency-converted net worth~~ — FIXED
**Fix:** `build_system_prompt()` delegates to `ledger_service.net_worth(base)` (the same fold
the dashboard uses) and converts the per-class breakdown via `to_base`. Fixes three bugs:
(1) it converted to the wrong currency for non-default-base users; (2) `fx.get(ccy, 0)`
silently zeroed holdings in uncached currencies; (3) it summed market positions only despite
the "Total NW" label (now includes cash accounts − liabilities). Regression test:
`tests/test_prompt_networth.py`.

### ~~Cash flow starting balance computation~~ — FIXED
**Fix:** `_cash_balance_base()` in `backend/services/cashflow.py` converts all liquid cash rows
via `to_base` regardless of currency. Liabilities (credit-card/margin debits) are intentionally
excluded from the *cash-runway* start — those are paid via future expense flows already in the
projection, not netted from today's liquid cash. This is by design; if you want debit netted
into runway, that's a separate explicit decision to make.

### ~~`current_age` static field in profile.json~~ — FIXED
**Fix:** Removed from `profile.json`. Age is computed dynamically everywhere from `date_of_birth`.

### ~~No input validation on profile writes~~ — FIXED
**Fix:** `backend/api/profile.py` now uses a Pydantic `ProfileBody` model for the PUT endpoint.
Validates `risk_tolerance` enum, numeric ranges for ages and percentages. The `update_profile`
tool coerces numeric fields so the AI cannot accidentally write strings.

---

## 🟡 Medium — known, documented, not yet fixed

### FIRE calculator retirement expenses default to current expenses
**File:** `backend/api/fire.py`, `services/fire.py`, profile store (`base_currency`-denominated
profile row under `DATA_DIR`)
**What:** If `fire_annual_expenses` is not set, annual retirement expenses default to
`monthly_expenses × 12`. Current expenses typically include temporary items that won't exist
in retirement (e.g. temporary support obligations, transitional rent, one-time tax payments).
True retirement expenses may be substantially lower, making the FIRE number higher than it
needs to be.
**Workaround:** Ask the AI "set my retirement annual expenses to X" — it will call
`update_profile` with `fire_annual_expenses`. The AI system prompt is aware of this and
should prompt the user when doing FIRE analysis.
**Fix:** No code change needed — data quality task. AI should proactively ask when doing FIRE
analysis. Note that FIRE planning is currently a **deferred** area per `docs/launch/BOARD.md` —
this item is documentation of a known limitation, not a queued fix.

---

## 📋 Design decisions (not bugs, but worth documenting)

| Decision | Rationale | Revisit if |
|---|---|---|
| FX cache written on each `/api/prices` call | Avoids async FX fetch in prompt builder; stale by at most one session | You want real-time FX in the system prompt |
| FIRE uses profile monthly_expenses as retirement expenses by default | Safe starting point; user should override via AI | User sets a different retirement lifestyle budget |
| Net worth history uses recorded price series, not live | Consistent daily snapshot without needing a separate fetch | You want intraday accuracy (not needed for monthly tracking) |
| Local machine is auth-trusted by default (`AUTH_TRUST_LOOPBACK=true`) | Single-user, localhost-first design — the operator never has to log in to their own box | You expose the instance beyond `localhost`, or share the machine with other users — see `README.md` → Security posture |
