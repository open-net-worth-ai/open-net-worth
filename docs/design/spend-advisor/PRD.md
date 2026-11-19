# Spend advisor — "Ask Lucius first": PRD

> **Lucius tells you before you spend, not after — so there is nothing to regret.**

Date: 2026-11-18 (updated 2026-11-20) · Status: draft for the owner's approval

## Problem

Every number Open Net Worth computes about spending arrives AFTER the money left: a bank feed,
a statement, an email receipt. Budgeting can then only tidy up. The one moment a budget can change
a decision is before the purchase, and today nothing in the app speaks at that moment.

That moment now has two shapes:

- **The owner**, in a shop or on a checkout page, asking Lucius (chat, Telegram, voice).
- **The owner's own AI assistant buying for them** (ChatGPT, Claude, Meta Muse, …), which can ask
  before it pays, if anything is there to answer.

Open Net Worth does **not** build a shopping agent. The assistant the owner already uses finds,
fills and pays; Open Net Worth is the advisor every one of them consults, because it alone holds
the budget, bills, debts and cards.

## Goal

At the moment of a purchase, give the buyer (person or agent) three answers in the owner's own
numbers:

1. **Can I afford this?** What it does to the envelope, Ready to Assign, upcoming bills and the card.
2. **Should it wait?** Only with a concrete reason and a date or alternative.
3. **Which card?** The best-paying card the budget can actually carry.

It informs; it never blocks, and Open Net Worth never initiates a payment. "Stop you" means
**pause you at the right moment** — and, for an assistant, **hold the purchase until the owner says
yes** above their own limit. The owner always has the final say.

The other half of "no regret" is a **guilt-free yes**: "fits, Dining has room, use Card A" lets
the owner buy without second-guessing.

## Tone rules

- **Never shame.** Talk about the money, never the person: "this leaves Dining short", never
  "you spend too much on dining".
- **Every "wait" has a way out**: a date, an alternative, or "buy anyway" — a pause, never a wall.
- **Silent when it's fine.** Planned spending gets a quick "fits" or nothing; nagging every
  purchase teaches people to stop asking.
- **The owner decides.** "Buy anyway" is one tap, and Lucius never brings it up later.

## Users

Any self-hoster, in any country. Nothing may assume US cards, US merchant category codes or one
currency. Card rewards are entered by the owner, never shipped as a card database.

## The principle: Lucius acts as the owner's money manager

Every answer and alert in this feature is judged by one test: would a good personal money manager,
who knew everything in the book, say this, now, this way?

| A real manager… | Here |
|---|---|
| Speaks first when it matters | Terms change ahead; a bill is due before payday; a cap is nearly used |
| Stays quiet when all is fine | A purchase inside the owner's rules gets a silent "fits" — no lecture |
| Recommends, with reasons | One card and why, not a table of five |
| Knows the whole picture | Budget, bills, debts, cards, terms and history in one answer |
| Watches what the owner doesn't | Terms, caps, promised vs paid rewards |
| Checks their own work | Backtests rules on statements; says when it was wrong |
| Shows sources, admits doubt | Quotes the clause; "probably codes as dining" |
| Reports back | Monthly: rewards earned vs available, and the few habits worth the most |
| Never moves money | Advises and drafts; the owner decides; Open Net Worth never pays |

## Scenarios where this beats today's tools

A rewards app knows the cards but not the money; a budgeting app knows the money but not the
cards; a banking app knows one bank. Open Net Worth knows all of it, plus bills and debts.

1. **Which card, at the till.** Abroad, at a restaurant: Lucius weighs the dining rate, how much of
   this period's cap the ledger says is used, the FX fee, and whether that card is paid in full —
   "Card B: A has 120 of cap left and a 3% FX fee."
2. **Can I afford this.** A 1,400 laptop: the envelope, Ready to Assign, rent due in 5 days, payday
   on the 28th — "it fits only by taking from Ready to Assign, and leaves rent 200 short until
   payday. Buy on the 28th."
3. **Rewards that cost money.** The 2% card carries a balance at 26% APR — "not this card; put the
   2% toward the debt instead." Only something that sees rewards and debt together can say it.
4. **Your card just got worse.** A bonus cut announced in a statement insert — "from Jan 1, ~1,500
   fewer points a month at your spend; Card C now wins online. Move your subscriptions?"
5. **Cards nobody covers.** A mid-size bank in a smaller market: Lucius finds that card's own
   terms and checks them against the owner's statements. Any card with published terms works.
6. **Promised vs paid.** "Rules predicted 2,300 points, the statement shows 1,700; most of the gap
   is three merchants that probably don't code as dining."
7. **Rewards left on the table** (monthly review). Computed from real transactions and confirmed
   rules: what was earned, what the best card per purchase would have earned, and the three
   habits worth the most.
8. **An agent shopping for the owner.** The grocery agent asks first — "fits your 150 Groceries
   rule; use Card A"; over the rule, it asks the owner.
9. **Private by design.** Transactions, cards and spending stay on the owner's machine.

**Where it won't win** (said plainly in the product docs too): finding new cards (it never
suggests one the owner doesn't hold); exact MCC at an unfamiliar merchant (a guess until
confirmed); zero setup (it needs the book, statements and a confirmation of found terms); product
deals and price drops.

**For:** someone already budgeting in Open Net Worth, holding 2–5 cards, often spending in more
than one currency, who cares about points and about not overspending — especially outside the
US, where rewards apps don't reach.

## Phases

### P1 — Can I afford this? + Not now (build first)

**Two tools, explicit names** (a tool name is persisted data and, for these two, lives outside the
app too — in the skill and instructions owners give their assistants; ADR
`2026-08-25-tool-names-are-final`). The name must say what the tool is for on its own, since some
assistants decide from names alone; the MCP `title` carries the friendly wording.

| Tool | MCP title | Writes |
|---|---|---|
| `get_spending_advice_before_payment` | "Ask Lucius before buying" | nothing (a small `check_id` record for matching) |
| `record_spending_after_payment` | "Tell Lucius what you bought" | an uncleared transaction (P3) |

**Lucius already has a `can-i-afford-it` starter skill** (`backend/starter_skills/can-i-afford-it/`)
for big buys and runway (cash on hand, cash-flow projection, commitments, goals). It lacks the
envelope view, bills before payday, the card, recent purchases and rules, and as a skill it can't
be called by an outside assistant and isn't deterministic. P1 therefore: (1) adds the tool, its
logic in `services/`; (2) upgrades the skill to call the tool for everyday purchases, keeping its
runway path for big ones — one skill, two depths, no duplicate.

**Input**

```json
{ "amount": 84.00, "currency": "USD", "merchant": "ACME Store", "category": "Gifts",
  "pay_with": "Card A", "date": "2026-11-20", "essential": false }
```

Amount + currency required. A missing category → the payee's usual one; ambiguous → candidates,
never a guess. A missing `pay_with` → checked as cash, said so. `date` defaults to today
(`core.user_time`). The owner's own "essential" marking overrides the caller's hint.

**Checks, in order — every figure from an existing authority, no second computation**

| # | Check | Authority |
|---|---|---|
| 1 | Resolve category and account | `resolve_category_ref`, payee default, account resolver |
| 2 | Value in base currency; the card's FX fee | `txn_base_amount` logic, current rates |
| 3 | Envelope available before/after; shortfall | budget engine (the Budget screen's figures) |
| 4 | Cover options for a shortfall | `cover_overspending` |
| 5 | Bills due before next income; cash after them | `commitments.due_summary`, `cashflow.runway_from_projection`, income schedule |
| 6 | Card: payment funded? carries a balance at what APR? | card coverage / payment envelope, account `apr` |
| 7 | Recent spending at this payee (14 days) | ledger |
| 8 | Owner rules (from step 2 of the build) | rules |

**Verdict — one of four, strictest wins**

| Verdict | When |
|---|---|
| `fits` | Envelope covers it, no bill at risk, card fine |
| `fits_with_cover` | Envelope short; Ready to Assign or other envelopes cover it; no bill at risk |
| `wait` | A bill due before next income would be short after it — **always with a date** |
| `over` | Even with every cover option, bills before income can't be paid |

Flags beside the verdict: `card_warning` (carries a balance at its APR, or payment unfunded —
names a better option), `needs_owner` (a rule says ask). **Essentials never get `wait`.** A goal's
envelope is listed as a cover option, never a reason to wait — Lucius doesn't second-guess
priorities.

**Output — minimal** (default for outside assistants):

```json
{ "check_id": "chk_7f3a", "verdict": "fits", "reasons": ["Gifts has room for this."],
  "card": { "use": "Card A", "warning": null }, "options": [],
  "next_step": "After the user pays, call record_spending_after_payment with check_id chk_7f3a." }
```

**Output — full** (Lucius itself, or an assistant the owner opted in) adds `resolved`
(category, account, base amount, FX fee), `envelope` (before/after), `cover` (sources with
available), `bills_before_income` (next income date, bills due, cash after), card detail
(`payment_funded`, `carries_balance`), `recent_at_payee`, `as_of`; `options` always include
`buy_anyway`, and `wait` options a `wait_until` date.

**Rules (step 2 of the build)** — decisions made once, in advance; they change the answer, never
block. Per category: `ok_below` (silent fits), `ask_above` (`needs_owner`), `sleep_on_above`
(wait + reminder offer), `essential` (never wait). Assistant-only: a monthly limit across all
assistant spending and allowed categories. The budget comes first (a rule never makes something
affordable); the strictest rule wins. Set in Budget settings or by telling Lucius.

### P2 — Use this card (the owner's own cards, their MCC, their terms)

Three facts decide which card earns most, and each comes from the owner's own data:

**1. The owner's existing cards.** Only cards already in the book: `credit` accounts, plus a
`cash` account the owner marks as having a debit card that earns rewards (common outside the US).
No card the owner doesn't hold is ever suggested; no card catalog ships.

**2. The merchant's MCC (ISO 18245 — global; Visa/Mastercard assign it everywhere).**

Evidence (a real book, 2026-11-18): none of ~60 uploaded statements (card PDFs from three issuers;
bank and e-wallet exports) carries an MCC. Bank feeds sometimes do (GoCardless
`merchantCategoryCode`, SimpleFIN `mcc`, both optional); Plaid and YNAB send none. Where a feed
sends one, it is **already kept**: the provider's whole row is stored in `connector_snapshots` and
linked to the booked transaction. So the MCC is mostly **resolved**, and read where a feed has it.

- **Bundled:** the full ISO 18245 code → description list (a public list; replaces the partial
  table in `services/aggregation/mcc.py`). No merchant → MCC database ships: none is free, and the
  paid enrichment services would send the owner's transactions to a third party.
- **Keyed by payee + raw descriptor**, never the brand alone: one brand codes differently by
  terminal/acquirer ("ACME*FOOD" 5814 vs "ACME*RIDES" 4121).
- **Resolver, first hit wins, the answer naming its source; the result is cached per merchant:**

| # | Source | Confidence |
|---|---|---|
| 1 | Owner-confirmed MCC for this merchant | confirmed |
| 2 | MCC in a stored bank-feed row (`connector_snapshots`) of a transaction at this payee | high |
| 3 | MCC passed by the caller (an agent at checkout) | as given |
| 4 | Lucius's guess: constrained to valid codes, from descriptor + country + typical amount; 1–3 candidates with confidence | low |
| 5 | Web search ("<merchant> MCC", community reports) — opt-in, merchant name only, result is a cited suggestion, never confirmed by itself | low–medium |

- **Resolve only when it changes the answer.** If every candidate MCC gives the same winning card,
  stop at the guess. Steps 5 and owner questions run only when candidates would flip the winner
  ("if 5411, Card A wins; if 5499, Card B wins").
- Unconfirmed merchants are offered to the owner in batches by Lucius ("6 merchants to check"),
  not a new screen.

**3. The card's terms.**

*Sourcing — Lucius finds the terms; the owner only confirms.* A card's rules usually span several
documents: product T&C, rewards-programme T&C, a separate **MCC exclusion list**, the fee schedule.

1. **Identify the exact card**, from the book first: the product name, issuer and network printed
   on the card's statements (statements name the product exactly, e.g. "ACME REWARDS WORLD
   MASTERCARD"), the account's name and currency (→ country). Asks the owner only if still
   ambiguous. The country variant matters: one product name has different terms per country.
2. **Search** (the web search connector) for each of the four document types for that product,
   issuer and country.
3. **Keep only official sources**: a document is accepted only from the issuer's own domain,
   which the owner confirms once per issuer ("is acme-bank.example your bank's site?") and is
   remembered. Blogs, comparison sites and forums may point to a document, never stand in for it.
   Each accepted document must name the product.
4. **Fetch** through the guarded fetcher (`assert_public_url` + `pinned_urlopen`) and store a dated
   copy in Documents; its URL becomes a watched source (P2b).
5. **Show the owner what was found** — card, documents, dates, links, and which of the four are
   missing — and proceed to extraction only after they confirm it is their card. A missing
   document is reported, with the option to upload it.

The owner can still upload PDFs or paste links instead (some terms sit behind a login). Terms are
public, so a model may read them; a document the owner marked Private is still never read.

*What the rules express* (anything not expressible is kept as a flagged, quoted "condition I
can't check" that the advice states — never silently dropped):

- Reward unit (cash % / points per currency unit), base rate, one point's value (owner-set;
  Lucius may suggest, labelled an estimate — it depends on how the owner redeems).
- Earn rules: MCC list/ranges or named merchants → rate, with conditions: **channel** (online /
  in-store / contactless / mobile wallet), **currency** (local / foreign), **minimum spend or
  transaction count** per period, owner-**chosen categories** (and the current picks).
- **Caps**: amount, period (calendar month / statement cycle / quarter / year), bonus-only vs
  total, shared across rules.
- **Rounding**: earn per full block, rounded per transaction (e.g. per full 5 spent).
- **Exclusions**: MCCs and named types (wallet top-ups, insurance, government, education).
- FX fee %, DCC fee; sign-up bonus (spend, deadline, value).
- `valid_from` / `valid_to` per rule, and the source document + clause per rule.

*Trust, four layers:*
1. Each rule cites document, page and a **verbatim quote**.
2. Mechanical check: the quote exists in the document text; MCCs are valid codes; numbers sane.
3. Plain-language review; the owner confirms or edits. **Unconfirmed rules never rank.**
4. **Backtest before confirming**: run the draft rules over past statements and compare with the
   points/rebate each statement reports (the card statements checked all show a rewards summary —
   per statement, not per transaction). A large gap flags the rules or MCC
   guesses, most-contributing merchants first.

*History.* Rules are never edited, only ended at an effective date; a past row keeps the rate it
earned, so backtests stay honest.

Ranking: expected value in base currency = rewardable amount (after rounding) × rate × point value
− FX/DCC fee, cap- and condition-aware (cap usage from the ledger within the period; channel-only
caps are estimates, since past rows don't record the channel), excluded → 0, plus bonus progress.
**The budget overrides the reward:** a card carrying a balance at its APR, or whose payment is
unfunded, ranks last with the reason stated.

### P2b — Lucius watches the terms of the cards in use

Banks change card terms; advice from stale rules is confidently wrong. Lucius watches every card
**in use** (a `credit` account with spend in the last 90 days, not archived), for every terms
source captured at setup **plus the page that lists those documents** (a new PDF often appears at
a new URL).

| Signal | How |
|---|---|
| Terms pages | Weekly fetch, guarded fetcher |
| Statement notices | At statement import, detect "changes to … with effect from …" (real statements already carry them) |
| Bank email | The email connector flags "changes to terms" mail from the card's issuer |

- **Cheap by default:** fetched text is normalised (dates, navigation, whitespace stripped) and
  fingerprinted; an unchanged fingerprint costs no model call. Only a changed one is read, and
  only its diff.
- **On a change:** Lucius classifies it **material** (rate, cap, exclusion, condition, fee,
  effective date) or **cosmetic** (logged only). A material change notifies the owner with: what
  changed (before/after quote), when it takes effect, and **what it means for them** from their
  last 3 months of spend ("~1,200 fewer points a month; for dining Card B now wins"). It
  drafts a new rule version from the effective date; **nothing applies until the owner confirms**,
  and the old rules hold until that date.
- **Never stops silently:** after a few failed fetches (404, blocked, now behind a login) the owner
  is told the source can no longer be watched and asked for the new link or the PDF.
- **Untrusted input:** a fetched page or email can only produce a draft for review, never a rule
  change or any other action.
- Delivery: the existing notify channels plus the in-app alert surface; Open question 6.

## Assistant flow (ChatGPT, Claude, Muse, …)

How the owner shops through their own AI assistant with Open Net Worth advising — Open Net Worth builds no shopping agent.

**One-time setup**
1. In Open Net Worth: create a **assistant access key** (allowed: `get_spending_advice_before_payment`, and from P3
   `record_spending_after_payment`; nothing else), pick its **detail level**, and set spending rules
   ("Groceries under 150: fine", "Fun over 200: ask me").
2. Make the instance reachable by the agent:
   - a local agent (Claude Desktop) uses the existing stdio bridge;
   - a cloud agent (ChatGPT, claude.ai) cannot reach `localhost`: it needs a public URL (a tunnel
     such as Cloudflare Tunnel / Tailscale Funnel, or a hosted instance) **and** their connector
     setup generally expects OAuth, while inbound MCP is Bearer-only today. Both are new work (S1).
3. Add Open Net Worth as a connector in the agent, plus one standing instruction (custom
   instructions / a project): "Before any purchase call `get_spending_advice_before_payment`; after paying call
   `record_spending_after_payment`." The product docs ship the exact text.

**A purchase**

```
Owner → agent: "Get a birthday gift for my mom, around 80"
Agent: finds options; owner picks one (84, online)
Agent → ONW  get_spending_advice_before_payment(84, USD, merchant="ACME Store", category≈"Gifts", channel=online)
ONW  → verdict: fits · card: "Card A" (3x online ≈ 250 pts, payment funded)
       reason: "Gifts has 120 left. No bills at risk."
Agent → owner: "Fits your Gifts budget. Pay with Card A for ~250 points. Buy it?"
Owner: "Yes" → agent pays from ITS OWN saved wallet with Card A
Agent → ONW  record_spending_after_payment(order_id, items, 84, "Card A", merchant)
ONW: an UNCLEARED transaction in Gifts, receipt attached
Days later: the bank-feed / statement row matches it — no duplicate
```

- **Doesn't fit:** verdict `wait` with the reason ("rent 2,000 due Friday; leaves you 150 short
  until payday on the 28th") and options (buy on the 28th / take 84 from Ready to Assign); the
  agent offers a reminder.
- **Inside a rule:** the agent proceeds and only mentions the card — no extra question.

**Design rules**
- **No card numbers, ever.** Cards are named by the owner's nickname or last 4; the agent pays
  from its own wallet, so the card must be saved there too.
- **Detail level on the token** (answers go to the agent's provider): `minimal` (default) =
  verdict + card + one short reason; `full` = also balances, bills and runway figures. Opt-in.
- **Advice, never enforcement.** The agent may skip the call; nothing relies on it calling.
- **An agent's word is never a final transaction.** A reported purchase stays uncleared until a
  bank row or statement matches it; one never matched is surfaced for review, not kept silently.

**Agents that can't call Open Net Worth** (store chatbots, closed in-app checkouts): the owner
asks Lucius before ("Can I buy this 84 gift? Which card?" — chat, Telegram, voice; the same engine)
and the order-confirmation email records it after, through the existing email ingest. Full flow for
open agents (anything that speaks MCP); ask-before + record-after for everything else.

### S1 — Reachable by cloud agents (prerequisite for ChatGPT / claude.ai)

OAuth for inbound MCP (scoped to the assistant access key's tools) and a documented way to expose
the instance (tunnel or hosted). Security-critical: `/security-review` + ADR; gateway mode and the
demo lockdown must refuse or scope it correctly.

### P3 — Record what an agent bought

`record_spending_after_payment` through `ledger_service.create_transaction`, as an **uncleared** row;
identity `(emitter, external order id)` for dedup; the later bank-feed / statement row matches
and clears it. The owner's approval card applies unless their rules allow it. Signed receipts
delivered to the buyer (AP2/ACP mandates) are future work: nothing pushes them to a self-hoster
today.

## Making assistants call before and after

No assistant can be forced; make calling the natural path and notice when it didn't happen.

1. **The tools prompt themselves** (strongest): explicit names; descriptions written as orders
   ("Call BEFORE confirming any purchase, booking or subscription, even if the user didn't mention
   their budget"); every advice answer carries `next_step` naming the after-call and its
   `check_id`, so the after-step is in front of the assistant at the moment it pays.
2. **Instructions where each assistant reads them**: MCP server `instructions`; an MCP prompt
   ("Shop with my budget"); a **skill** (`SKILL.md`) generated from Settings with the instance
   address filled in and no secret; and 5-line paste-in text for assistants without skills.
3. **Open Net Worth checks it happened**: advice with no after-call → the bank row that matches
   (amount, merchant, date) is linked to the check anyway — the bank row is the truth, the
   after-call a bonus; an after-call with no advice is flagged "bought without asking first";
   a per-assistant scorecard in the activity log ("14 checks, 11 recorded, 2 seen at the bank
   with no check"); order emails still cover anything missed.
4. **"Test my agent"**: at setup, a one-line test purchase ("pretend to buy 'ONW test item' for
   1.00"); Open Net Worth watches for both calls and shows ✅/❌ with the fix. A live regression
   (like `make cli-smoke`) keeps checking Claude and Codex after their own updates.

## Assistants and protocols

The agentic-commerce protocols (Nov 2026) are layers between the assistant, the merchant and the
payment network: **UCP** (Google + retailers) and **ACP** (OpenAI + Stripe; status contested) for
checkout, **AP2** (now with the FIDO Alliance) for payment mandates, **x402** for machine payments,
Visa Intelligent Commerce / Trusted Agent Protocol and Mastercard Agent Pay at the networks; **MCP**
connects assistants to tools; **A2A** connects agents to agents. Open Net Worth sits on the
**buyer's side** of all of them — advice before, a record after — so it works whichever one the
merchant uses. The after-call takes one simple shape; the assistant fills it from whatever order
record its protocol produced (a UCP/ACP order, an AP2 mandate, an x402 receipt), and that id is the
dedup key.

| Reach | Assistants | Needs |
|---|---|---|
| **MCP** | Claude (Desktop, claude.ai, Code), ChatGPT, Meta Muse Code, most others | local: stdio bridge; cloud: S1 |
| **Plain web API + OpenAPI** (same two actions, same key) | Assistants that build connectors from an API — Meta Muse's consumer app may be one (its MCP support is unclear; verify with Meta's docs) | S1's public address |
| **Neither** | Store chatbots, closed in-app checkouts | the owner asks Lucius before; the order email records it after |
| **A2A** (later) | Enterprise agent platforms: Google Vertex / Gemini Enterprise, Microsoft Copilot Studio / Foundry, Amazon Bedrock AgentCore, Salesforce Agentforce — not ChatGPT or Claude today | S1 |

### A2A adapter (later)

A2A lets another agent hand Lucius a *task* rather than call a tool, which gives two things MCP
can't: **the owner's approval happens in Open Net Worth** (task state `input-required` → the
existing approval card on app/Telegram/voice → task completes), so an assistant that ignores
instructions can't skip it; and **"wait" can become "OK now"** (the task stays open; on payday
Lucius pushes the update). It fits existing seams: a new channel in `backend/connectors/`
(auth first, `operator_trust` off, rate limit), a locked-down Lucius via `core/headless_turn`
(`allowed_tools=[advice, record, ask owner]`, `enforce_offer=True` — the seam's documented use for
untrusted text), approval cards, `ai/turns.py`, scheduled messages for pushed updates through the
SSRF guard. New: the A2A endpoint + Agent Card (likely the official Python SDK — ADR), a task table
(timestamped revision), outbound update delivery. Simple cases are answered by the engine with no
model call; a locked-down turn runs only for a conversation or an owner approval.
Security-critical: `/security-review` + ADR. Build when a client the owner uses supports it.

## Build order — the first three (the demo)

1. **"Can I afford this?" in Lucius** — the advice tool + the upgraded `can-i-afford-it` skill.
   *Demo:* "Can I buy a 1,400 laptop?" → "Only by taking 1,100 from Ready to Assign, and rent
   would be 200 short until payday on the 28th. Buy on the 28th?" (M)
2. **"Your AI assistant asks Lucius first"** — assistant access key (two tools, minimal detail,
   activity log), the `next_step` chain, `record_spending_after_payment` (uncleared), `ok_below` /
   `ask_above`, "Test my agent"; in **Claude Desktop** (local — no OAuth or tunnel yet).
   *Demo:* buy a gift → Lucius says fits → Claude buys → recorded; headphones for 349 → over the
   ask-above-300 rule → asks the owner. (M)
3. **"The right card, before you tap" (light)** — the owner's 2–3 cards, rules drafted by Lucius
   from uploaded terms (quoted clauses, owner confirms), cap/FX-aware ranking, the budget first;
   MCC = guess + correction. *Demo:* "Dinner abroad, which card?" → a reasoned pick that avoids
   the card carrying a balance. (M–L)

Then: **S1** (ChatGPT, claude.ai, Muse via the web API), bank-row matching for assistant
purchases, terms finding/watching (P2b), backtests, the monthly review, A2A.

## Acceptance criteria

P1
- `get_spending_advice_before_payment` returns the same envelope / RTA / due / runway figures as the Budget, Commitments
  and Cash Flow screens for the same date (one authority each; a test compares them).
- An ambiguous or unknown category returns candidates, never coerces.
- A purchase in a currency other than the card's or the base is valued at current rates and names
  the FX fee when the card has one.
- "Wait" appears only when a listed rule fires, and every "wait" carries a reason and a date or
  alternative; an already-budgeted or essential purchase never gets one.
- The assistant access key can call only its allowed tools; every other tool is refused; revoking
  it works without touching the operator token. Demo mode refuses issuing one.
- At detail level `minimal` the answer carries no balance, bill or runway figure; `full` is opt-in.
- No answer ever contains a card number; cards are named by nickname or last 4.
- Read-only: no store write on any path (owner rules are written only from the owner's UI/Lucius).

P2
- Only cards the owner holds are ranked; an unconfirmed rule never affects a ranking.
- A GoCardless / SimpleFIN MCC in a payee's stored snapshot rows is used as resolver source 2.
- The MCC answer names its source (confirmed / feed / caller / guess / web); a guess is labelled;
  web search never runs when every candidate gives the same winning card, nor with it switched off.
- Every extracted rule cites a quote that exists verbatim in its document; a Private document is
  never read; an inexpressible clause is kept as a flagged condition the advice mentions.
- An excluded MCC earns 0 on that card even when a broader rule would match.
- Rounding, minimum-spend and cap conditions change the computed reward as the terms say.
- A backtest over past statements reports predicted vs reported rewards per statement.
- A card carrying a balance never ranks first while a card with a funded payment can take it.
- Works for a cash-back card in one country and a points card in another, in two currencies.

- Finding terms: the card is identified from the book; only documents from the owner-confirmed
  issuer domain that name the product are accepted; nothing is extracted before the owner confirms
  the found set; missing documents are reported.

- The monthly review reports rewards earned vs the best-card-per-purchase figure from confirmed
  rules only, and names at most three habits, each with its estimated value.

P2b
- An unchanged source costs no model call.
- A material change notifies with before/after quote, effective date and the owner's estimated
  impact; it produces a draft that applies only after confirmation and only from its effective date.
- A source failing repeatedly is reported to the owner, never dropped silently.
- A change notice in an imported statement flags that card's rules for review.

S1
- A cloud agent connects via OAuth and can call only the shopping-agent tools; the operator token
  and every other tool stay unreachable; revoking the grant cuts it off.

P3
- An agent-reported purchase is written uncleared, deduped by `(emitter, order id)`, and cleared by
  the matching bank/statement row without a duplicate.
- A reported purchase never matched within N days is surfaced to the owner.

## Success

- Fewer envelopes still overspent at month-end, for owners who use it.
- Share of purchases checked before they happen (owner or agent).
- Owners keep it on: few cool-off rules disabled within a month (the nag signal).

## Out of scope

- Initiating, signing or authorizing payments (no AP2 mandates issued, no x402).
- A built-in card rewards database, issuer data feeds, or suggesting cards the owner doesn't hold.
- Product price history ("cheaper next week"); an agent may pass it in, but Open Net Worth does
  not hold it.
- Blocking a purchase. Answers are advice.
- A new tab. P1 is a tool + a small rules section in Budget settings; P2 adds a rewards section to
  the card account's edit screen.

## Open questions

1. Timing against the Hardening Loop: Budgeting (②) is not yet ✅ trusted. Ship behind a feature
   flag now, or wait for sign-off?
2. Where owner rules live: on the category, or a separate rules list?
3. Is the scoped token general (a token with a tool allowlist) or `get_spending_advice_before_payment`-only? General is
   reusable but is a security-critical surface (`/security-review`, ADR).
4. (Resolved 2026-11-18: feed MCCs are already stored in `connector_snapshots`; no storage
   change is needed.)
5. Does Lucius answer "can I afford this?" unprompted when the owner mentions a purchase, or only
   when asked?
6. Where a material terms change surfaces in-app: an Intel story (the one in-app alert surface
   today) or the card's account page?
7. Watch cadence: weekly, or tighter in the weeks after a change notice?
8. Is S1 worth its security surface now, or do cloud agents wait while Claude Desktop (local) and
   "ask Lucius" carry the first release?
9. Rule outcome for an agent: may "inside a rule" let `record_spending_after_payment` skip the owner's
   approval card, or does every agent-reported write ask?
