# Spend advisor — when people shop and pay through their own AI agent

Date: 2026-11-20 · Status: draft for the owner · Companion to [PRD.md](PRD.md)

> **Lucius tells you before you spend, not after — so there is nothing to regret.**
> You shop and pay by talking to your own agent (ChatGPT, Claude, Muse, Gemini…). Open Net Worth
> sits behind that agent: it shapes what the agent buys, how it pays, and keeps the books.

---

## 1. The situation

- **The user talks to their agent, not to Open Net Worth.** Shopping, choosing and paying happen
  in the agent's chat. The agent pays through agentic checkout (UCP, ACP, AP2, x402, card-network
  agent tokens) or a store's site, from **its own wallet**.
- **Open Net Worth is behind the agent.** The agent asks it before, during and after. What the
  user experiences is mostly **what the agent says**, shaped by Open Net Worth's answers.
- **Open Net Worth speaks in its own channels** (phone notification, Telegram, voice, the app)
  only when the agent can't: the user isn't in the chat, the approval must not depend on the
  agent, something went wrong after, or a reminder is due days later.
- **Open Net Worth never pays** and never moves money without the owner's approval.

**How an agent is connected**

| Agent | Connection | Coverage |
|---|---|---|
| Claude (Desktop, claude.ai, mobile, Code), ChatGPT, Cursor, VS Code, Muse Code, any MCP client | MCP — API token, or OAuth with Read / Read and change (exists) | Full loop |
| Assistants that build connectors from a web API (possibly Meta Muse's app) | Plain web API with an OpenAPI description (to build) | Full loop |
| Closed agents (store chatbots, checkouts inside other apps) | None | Ask Lucius yourself before; the order email records it after |
| Enterprise agent platforms (Google, Microsoft, Amazon, Salesforce) | A2A (later) | Full loop + approvals that wait inside the task |

---

## 2. Principles

1. **The agent's chat is the main screen.** Design what the agent says first; Open Net Worth's own
   screens come second.
2. **Before, not after.** Shape the shopping, check before paying, not just report afterwards.
3. **Ready-to-say answers.** Open Net Worth gives the agent sentences to relay, so every agent says
   the same thing the same way.
4. **Silent when it's fine.** Inside the owner's rules, the agent just mentions it in passing.
5. **Confirm where the user already is.** If they're in the chat, the agent asks there. Open Net
   Worth's own channels only when they're not.
6. **The agent isn't trusted blindly.** Open Net Worth checks what the agent claims against the
   bank, and the owner's hard limits don't depend on the agent obeying.
7. **One extra line, never a lecture.** Never shame; talk about money, not the person.
8. **Every "no" has a way out**: a date, an alternative, or "buy anyway".
9. **Proof, not promises.** Figures match the app's screens; card rules cite their clause.
10. **Anyone, anywhere**: any agent, country, currency and bank.

---

## 3. The loop, step by step

Each step: what the user sees in the agent's chat, what happens behind it, and when Open Net Worth
speaks directly.

### Step 0 — Plan ahead (no purchase yet)
- **Agent's chat:** when the user mentions a future buy ("I'll need a new laptop next year"), the
  agent relays Lucius: *"Set aside 120 a month for it?"*
- **Behind:** Open Net Worth proposes a sinking fund, a wishlist entry, an occasion (birthday) date.
- **ONW directly:** the daily briefing and monthly report mention upcoming big buys and occasions.

### Step 1 — The user asks the agent to buy
> **User:** Find me noise-cancelling headphones, around 300.

- **Behind:** the agent asks Open Net Worth for the **shopping context** for this purchase.

### Step 2 — The agent shops inside the budget
- **Behind:** Open Net Worth returns: the category's room this month, the usual price the owner
  pays, preferred and never-merchants, the owner's limits, and whether a confirmation will be
  needed.
- **Agent's chat:**
  > **Agent:** Here are 3 options. **Your Electronics budget has 340 this month**; option B (289) fits.

### Step 3 — Before paying: the advice
> **User:** Get option B.

- **Behind:** the agent calls the before-payment check. Open Net Worth answers with the verdict,
  reasons, options, the card to use, at most one saving tip, whether the owner must confirm, and a
  ready-to-say sentence.
- **Agent's chat:**
  > **Agent:** Before I buy — **Lucius says it fits**: Electronics 340 → 51, rent is covered.
  > **Pay with Card B** (≈ 8.67 back). Confirm?

  Variants the agent relays the same way:
  - **Fits if you move money:** "Electronics is 40 short; Lucius can cover it from Ready to Assign
    (500). Do that and buy?"
  - **Wait:** "Rent (2,000) is due Friday; this would leave you 200 short until payday on the
    28th. Buy on the 28th? I'll come back then." / "Buy anyway?"
  - **Over:** "This doesn't fit this month. A cheaper option up to 120 would."
  - **Card warning:** "Not Card A — it's carrying a balance at 26%. Use Card B."
  - **Saving tip (one at most):** "You bought this at ACME Market 5 days ago for 38."
  - **Risk:** "This shop is new and asks for a gift-card payment — Lucius advises not to."

### Step 4 — Confirmation
- **User in the chat:** the agent asks, the user says yes there. Nothing else.
- **Inside the owner's "no-ask" limit:** the agent proceeds and just mentions it ("Buying —
  fits Groceries, Card A").
- **User not in the chat** (scheduled reorder, autonomous agent): **ONW directly** asks on the
  phone / Telegram / voice: *"ChatGPT wants to buy headphones, 289 at ACME Store. Fits Electronics.
  Approve?"* The answer goes back to the agent.
- **Above a hard limit or a never-rule:** **ONW directly** asks the owner even if they're in the
  chat — the approval mustn't depend on the agent.
- **Sleep-on-it rule / quiet hours:** the agent relays "Lucius suggests sleeping on it — I'll ask
  you tomorrow at 9."

### Step 5 — The agent pays
- The agent pays from its own wallet with the card Open Net Worth suggested, in the local currency
  (never dynamic currency conversion), split into instalments only if each one fits.
- Open Net Worth is not involved in the payment.

### Step 6 — The agent reports back
- **Behind:** the agent sends the order: merchant, items, amount, card, order / mandate id, the
  check id. Open Net Worth records an **uncleared** transaction "via ChatGPT · waiting for bank",
  splits line items across categories, attaches the receipt, flags tax-deductible / reimbursable,
  and **holds the amount in the envelope** until the charge lands.
- **Agent's chat:**
  > **Agent:** Ordered. Logged in your budget — Electronics now 51. Returns until Dec 10; Card B
  > adds 1 year of warranty.

### Step 7 — Aftercare (days later)
- **ONW directly, only when due:** return window closing, trial converting ("ends Dec 4 → 14.99/mo"),
  renewal coming, refund still not received, warranty to register, tourist tax refund to claim.

### Step 8 — The bank charge arrives
- **Matches:** the transaction quietly becomes confirmed. No message.
- **Doesn't match — ONW directly:** "You approved 289 at ACME Store; the bank charged 309."
  Same for: charged twice, a different card, charged after a cancellation, never charged, points
  not earned as expected. Each with one suggested action (draft a message to the merchant, mark it
  fine).
- **A charge with no check from any agent** at a merchant agents use: "A 289 charge at ACME Store
  came in with no check from ChatGPT."

### Step 9 — Monthly review (Lucius's report)
- What each agent bought, in or over budget, issues caught.
- Rewards earned vs possible, and the card switches worth most.
- Savings found: duplicates, unused subscriptions, price increases.
- Claims pending: tax, reimbursements, refunds.
- One encouragement ("the laptop wait saved you a 200 overdraft").

### Step 10 — Adjust
- Lucius suggests: raise or lower an agent's no-ask limit, new rules, a sinking fund for what keeps
  surprising, a card switch, acting on a card's terms change. The owner answers in Lucius's chat or
  their agent's.

---

## 4. What Open Net Worth gives the agent

Explicitly named tools (MCP and the web API), each with a description that says on its own when to
call it, and every answer carries a `say_to_user` sentence and a `next_step`.

| Tool | When the agent calls it | Returns |
|---|---|---|
| Shopping context (to build) | When shopping starts | Category room, usual price, preferred / never merchants, limits, whether confirmation will be needed |
| `get_spending_advice_before_payment` (built) | Before paying | Verdict, reasons, options, card, one tip, confirmation needed?, `say_to_user`, `check_id` |
| `record_spending_after_payment` (to build) | After paying | Recorded (uncleared), the envelope after, return / warranty / trial notes, `say_to_user` |
| Approval status (to build) | When the owner was asked in ONW's channels | Approved / declined / waiting / expired |

How agents are made to call them: explicit names, "call BEFORE" descriptions, `next_step` in every
answer, MCP server instructions, an MCP prompt, a generated skill (`SKILL.md`), paste-in
instructions, and **"Test my agent"** — a test purchase that checks both calls arrive.

---

## 5. Open Net Worth's own channels — only these

| Situation | Channel |
|---|---|
| Approval needed while the user isn't in the agent's chat | Phone / Telegram / voice / desktop |
| Approval above a hard limit or a never-rule | Same, even if the user is in the chat |
| Charge mismatch, duplicate, wrong card, no check | Notification |
| Reminders: returns, trials, renewals, refunds, warranty, tax refund, waited purchase now fits | Notification / daily briefing |
| Agent misbehaving (loops, odd merchants) → auto-paused | Notification |
| Card terms changed | Notification + card page |
| Monthly review | Lucius's report |

**In the app:** Settings → AI apps → each agent (limits, rules, activity log, scorecard, Test my
agent, Pause, Pause all); each card's page (rules with quoted terms, caps used, earned vs
predicted); the transaction drawer ("via ChatGPT", check, approval, receipt, match status); Budget
→ category rules.

---

## 6. The roles behind the agent

Lucius plays every professional a person with money help would have, and speaks through the
agent with one voice.

| Role | Step | What they make sure of |
|---|---|---|
| **Wealth manager** | 0, 3, 9 | Big buys weighed against goals, liquidity and investments; never raid the emergency fund silently |
| **Budget coach** | 0, 2, 3, 9 | Every dollar has a job; cover from somewhere on purpose; sinking funds for true expenses |
| **Personal CFO** | 3, 5 | No bill bounces, no needless interest, credit headroom, FX, statement-date float |
| **Procurement officer** | 2, 3 | Shop inside budget and preferred merchants; usual price; no duplicates; no surprise annual plans |
| **Risk officer** | 3, 4, 8 | Scam patterns, new shops, gift-card / crypto / wire requests, misbehaving agents |
| **Rewards specialist** | 3, 5, 9 | The right card, caps, bonuses, current terms |
| **Insurance advisor** | 5, 6 | Purchase protection, extended warranty, travel cover the card already gives |
| **Bookkeeper** | 6, 8 | Recorded right: category, line items, receipt, matched to the bank |
| **Tax advisor** | 6, 9 | Deductible, reimbursable, tourist tax refund — flagged and claimed |
| **Executive assistant** | 0, 7 | Returns, trials, renewals, warranties, occasions, the wishlist |
| **Auditor** | 8, 9 | The agent's claims vs the bank; promised points vs paid |
| **Behavioural coach** | 3, 4, 9 | Pause impulses (quiet hours, sleep on it), never shame, encourage what worked |

**Depth scales with the stakes**

| Stakes | Agent says |
|---|---|
| Small, inside the rules | "Buying — fits Groceries, Card A." |
| Small, over the envelope | One line + cover option |
| Medium | Verdict + card + one tip + confirm |
| Large (laptop) | Envelope, bills before payday, goal impact, best timing, card protection, an offer to plan |
| Very large (car, renovation) | Runway, goals, cost of selling investments, financing vs cash, cost of ownership; "sleep on it" |
| Risky / irreversible (gift cards to a stranger, crypto, a wire) | Stop; explain the pattern; the owner must approve in Open Net Worth |

**When roles disagree:** safety → bills and cash flow → the owner's rules → the budget →
long-term goals → card and timing optimisation → convenience. A lower one never overrides a higher
one, and gets at most one line.

**The voice:** answer first, then the reason, then the options; the owner's own names and numbers;
never shame; encourage; admit doubt ("probably codes as dining"); at most one question; remember
past decisions; act only with approval.

---

## 7. Feature inventory

Where it shows: **A** = the agent's chat, **N** = Open Net Worth's own channels, **P** = the
Open Net Worth app.

### 7.1 Before paying
| # | Feature | Where |
|---|---|---|
| B1 | Shopping context at the start (room, usual price, merchants, limits) | A |
| B2 | Verdict: fits / fits if you move money / wait / over, with reasons and options | A |
| B3 | Bills before the next income; cash after them | A |
| B4 | Cover a shortfall from Ready to Assign or another envelope (owner approves) | A |
| B5 | Goal impact for large buys | A |
| B6 | Big-decision mode (runway, investments, financing, cost of ownership) | A |
| B7 | Instalments / BNPL checked per due date | A |
| B8 | Pre-authorisation holds (hotel, rental, fuel) explained | A |
| B9 | Pre-orders checked against the month they're charged | A |
| B10 | Essentials never "wait" | A |
| B11 | Multi-currency, FX fee, never dynamic currency conversion | A |
| B12 | Scam / risk patterns | A, N |
| B13 | Ready-to-say sentence in every answer | A |

### 7.2 Rules (set once, by talking to Lucius or the agent, or in the app)
| # | Rule | Where set |
|---|---|---|
| R1 | No-ask below X (overall / per category / per agent) | A, P |
| R2 | Ask me above X | A, P |
| R3 | Sleep on it above X | A, P |
| R4 | Essential categories | P |
| R5 | Never (categories, merchants, payment types) | A, P |
| R6 | Per-agent monthly limit, allowed categories, per-purchase cap | P |
| R7 | New subscriptions / annual plans ask | P |
| R8 | Quiet hours | P |
| R9 | Foreign spend / trip budget with dates | A, P |
| R10 | Defaults proposed from the budget, one tap to accept | P |
| R11 | Budget comes first; strictest rule wins | — |

### 7.3 Confirmation
| # | Feature | Where |
|---|---|---|
| C1 | Confirm in the agent's chat when the user is there | A |
| C2 | Confirm in ONW's channels when they're not (phone, Telegram, voice, desktop) | N |
| C3 | Hard limits and never-rules always confirmed in ONW, not the agent | N |
| C4 | Answers: approve / not now / remind me on [date] / use another card / buy anyway | A, N |
| C5 | Approval returns to the agent (agent re-asks; webhook; A2A later) | — |
| C6 | Expiry of a pending approval | A, N |
| C7 | Batch ("approve all 3 grocery items") | A, N |
| C8 | Household partner can approve within limits | N |
| C9 | "Always OK at ACME Market under 60" from an approval | A, N |

### 7.4 Paying
| # | Feature | Where |
|---|---|---|
| P1 | Which card: budget first, then FX, then rewards, then protection | A |
| P2 | Card health: balance at APR, unfunded payment → last | A |
| P3 | Reward rules per card (rates by MCC, caps, periods, rounding, channel, minimum spend, chosen categories, exclusions, foreign rates, bonus vs base) | P |
| P4 | Point value per programme (owner-set; Lucius suggests) | P |
| P5 | Sign-up bonus progress | A, P |
| P6 | Cap tracking from the ledger | P |
| P7 | Credit utilisation / limit headroom | A |
| P8 | Card in credit used first | A |
| P9 | Statement-close and cap-reset timing | A |
| P10 | Card protection (purchase protection, warranty, travel cover) | A |
| P11 | MCC: feed, agent-provided, guess, web search only when it changes the card, owner correction, per descriptor | P |
| P12 | Terms: find on the bank's site, quote every rule, owner confirms, backtest against statements | P |
| P13 | Terms watch: pages, statement notices, bank emails; impact on the owner's own spend | N, P |

### 7.5 Saving tips (one line at checkout, the rest in the review)
| # | Tip | Where |
|---|---|---|
| S1 | Same item bought recently | A |
| S2 | Usual price / cheaper usual merchant | A |
| S3 | Duplicate subscription | A |
| S4 | Free trial end | A, N |
| S5 | Annual vs monthly | A |
| S6 | Price increase on a subscription | N, P |
| S7 | Unused subscription | P |
| S8 | Return window | A, N |
| S9 | Price drop / price protection after purchase | N |
| S10 | Tip threshold the owner sets | P |

### 7.6 After paying
| # | Feature | Where |
|---|---|---|
| X1 | Record as uncleared, "via [agent]", right category | A, P |
| X2 | Line-item split, receipt attached | P |
| X3 | Dedup by agent + order / mandate id | — |
| X4 | Hold the amount in the envelope until the charge lands (two agents can't spend the same money) | P |
| X5 | Bank charge confirms it quietly | P |
| X6 | Mismatch: amount, duplicate, wrong card, after cancel, never charged | N |
| X7 | Charge with no check from any agent | N |
| X8 | Refund / return tracking | N |
| X9 | Dispute / merchant message drafts | N |
| X10 | Tax-deductible, reimbursable, tourist tax refund flags | P, N |
| X11 | Warranty registration and documents filed | P |
| X12 | Closed checkouts recorded from order emails | P |
| X13 | Undo any record | P |

### 7.7 Agents
| # | Feature | Where |
|---|---|---|
| G1 | Connect: API token, OAuth Read / Read and change, web API, A2A later | P |
| G2 | Each agent named, with its own limits, log and scorecard | P |
| G3 | Instructions: explicit tool names, descriptions, `next_step`, server instructions, MCP prompt, generated skill, paste-in text | — |
| G4 | Test my agent | P |
| G5 | Scorecard: checks, records, charges with no check | P |
| G6 | Trust ladder: limits suggested up or down | N, P |
| G7 | Pause one / pause all | P, N |
| G8 | Auto-pause a misbehaving agent | N |

### 7.8 Review and planning
| # | Feature | Where |
|---|---|---|
| V1 | Monthly: per-agent spending, issues caught | N, P |
| V2 | Rewards earned vs possible | N, P |
| V3 | Savings found, one-tap actions | N, P |
| V4 | Claims pending (tax, reimbursements, refunds) | N, P |
| V5 | Encouragement | N |
| V6 | Sinking funds for upcoming big buys | A, N |
| V7 | Wishlist that resurfaces an item when it fits | A, N |
| V8 | Occasion calendar (birthdays, anniversaries) | N |
| V9 | Daily briefing: renewals, trials, waits coming due | N |

### 7.9 Household
| # | Feature | Where |
|---|---|---|
| H1 | A partner's agents under their own limits | P |
| H2 | Shared envelopes can need either partner's approval | N |
| H3 | A child's allowance agent with a hard per-purchase cap | P |
| H4 | Every record says which person and which agent | P |

### 7.10 Safety
| # | Feature |
|---|---|
| Z1 | Open Net Worth never pays and never moves money without approval |
| Z2 | No card numbers anywhere — nickname or last 4 |
| Z3 | What outside agents see is minimal by default |
| Z4 | Agent text is never instructions to Lucius |
| Z5 | Rate limits per agent |
| Z6 | Public demo refuses agent connections |
| Z7 | Private documents never read |
| Z8 | Every write checkpointed and undoable |

---

## 8. Edge cases

The agent skips the check · the agent misreports the amount · the agent pays with a different card
· the agent goes offline while an approval waits · the approval expires · two agents buy from the
same envelope at once · an autonomous agent buys at night · a fresh book with no budget · an
uncategorised or unbudgeted category · no income detected · a missing FX rate · an unknown card ·
a pre-order · a split shipment · a partial refund · a pre-authorisation hold · gift cards ·
quasi-cash · a subscription signed up by an agent · terms behind a login · an unknown MCC · a
statement with no rewards line · a shared household envelope · over an agent's monthly limit ·
quiet hours · a closed checkout with no order email.

## 9. Order of building (nothing dropped)

1. **Before paying** — built: `get_spending_advice_before_payment`; add `say_to_user`, `check_id`,
   `next_step`.
2. **Shopping context + after-payment record + "Test my agent"** — the full loop in the agent's
   chat.
3. **Rules + confirmation outside the chat + per-agent control.**
4. **Reconcile with the bank** (mismatches, holds, refunds).
5. **Which card** (rules from terms, MCC, backtest, terms watch).
6. **Saving tips, aftercare, review and planning.**
7. **Household; web API for API-built connectors; A2A.**

## 10. Open questions

1. How an approval given in ONW's channels gets back to a waiting agent (re-ask, webhook, A2A) —
   test with Claude and ChatGPT first.
2. The default no-ask limit for a newly connected agent.
3. Does a held amount show in Budget as its own line?
4. Which signals are good enough for "unused subscription"?
