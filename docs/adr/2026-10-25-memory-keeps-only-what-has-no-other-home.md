---
title: Memory keeps only what has no other home — four topics, every fact shown whole, legacy facts sorted once
status: Accepted
date: 2026-10-25
deciders: orchestrating Claude agent + repo owner
---

# Memory keeps only what has no other home

## Context

Lucius's cross-session memory (`ai/memory.py`, table `memory`) is a flat list of facts. A
background pass distils facts after most chat turns; a tidy pass merges and drops them past 60;
each reply carries the **newest 30**, each **cut at 240 characters**; `recall_memory` searches
the rest.

A review of a real book (≈130 facts, 16 days of cost and tool logs) found:

- **Cheap, not useful.** Memory spend is ≈3% of chat spend, and the block is ≈1% of a reply's
  input, after the cache marker. Cost is not the problem.
- **The useful facts are rarely shown.** Recency selection filled the 30 slots with recent
  "open follow-up" and "resolved" notes (19 of 30). The lasting preferences and bookkeeping
  conventions were among the ~100 never shown. `recall_memory` ran twice in ~3,100 tool calls.
- **What is shown is cut off.** Facts average ≈400 characters against a 240-character render
  cap, so most injected facts end mid-sentence.
- **Most facts have another home.** About 20 repeat profile fields (date of birth, residency,
  income, target allocation, briefing time), ~30 repeat payee or category rules, ~7 repeat
  automations, and many repeat ledger state (holdings, share counts, prices, finished tasks) —
  all read live elsewhere, so the memory copy only goes stale.
- **Almost nothing was saved on purpose.** In chat, `remember_fact` / `forget_fact` were never
  called; nearly every fact came from the background pass.

The codebase already holds "one definition per fact across screens" (ADR
`2026-10-14-one-definition-per-fact-across-screens`). Memory broke it: it kept a second, staler
copy of facts the profile, payee rules, automations and the ledger own.

## Decision

> **Amended 2026-10-27** — five topics (adds "About you (beyond your profile)"), the profile's
> notes become Instructions for Lucius, and one routing guide decides profile vs instructions vs
> memory. See the amendment at the end; where it differs, it wins.

We will make memory hold **only what has no other home in the app**, organised into four fixed
topics, every fact shown to Lucius whole.

1. **One home per fact.** Memory never stores a fact another store owns:

   | Fact | Its home | How it gets there |
   |---|---|---|
   | Identity, residency, tax, employment, income/expenses/saving, risk, retirement age, date of birth, target allocation, briefing time, benchmark | **Profile** | Lucius calls `update_profile` in the turn (approval-gated) |
   | How a payee or bank text is categorised or named | **Payee rules / defaults** | Lucius creates or edits the rule in the turn |
   | Anything recurring: bookings, reports, reminders | **Automations** | Lucius creates or edits the automation in the turn |
   | Holdings, balances, prices, transactions, trades, finished tasks | **Ledger** (read live) | never stored |
   | Goals, commitments, the plan baseline | **Plan / goals / commitments** | never stored |

   The background pass **never writes** profile, payee rules or automations — the profile
   drives money math, and a silent edit would move numbers. It simply does not store those facts.

2. **Four topics.** Every fact carries `topic`, one of:

   | Topic | Holds | Cap | Expiry |
   |---|---|---|---|
   | `working_style` | how the user wants Lucius to work (answer style, what to check first, what not to do) | 15 | none |
   | `conventions` | bookkeeping conventions no rule can express ("money to my sister is a transfer, not spending") | 20 | none |
   | `plans` | intentions the Plan screen doesn't capture ("considering a small position in X") | 10 | optional |
   | `open_items` | unresolved things the user expects to come back to | 8 | required, default 14 days, max 30 |

   `tier` (`durable` / `time_bound`) is retired: expiry is a property of the topic.

3. **Short at write, whole at read.** A fact is ≤ 280 characters, enforced at the write choke
   (a user edit over the limit is refused with a message; an extracted one is dropped and
   logged). The prompt shows **every live fact in the four topics, grouped by topic, never
   truncated** — the newest-30 selection and the 240-character render cut are retired. A topic at
   its cap takes new facts only by merging or replacing.

4. **One write choke.** `ai.memory_write.apply_memory_change` is the single writer — extraction, the
   tidy pass, `remember_fact` / `forget_fact`, the dialog's edit, forget and "Tell Lucius", and
   the migration all go through it. It validates topic, length, expiry and cap.

5. **A stricter background pass.** The extraction prompt states the home table above and refuses
   conversation state ("awaiting the user's answer", "the advisor offered to…"), ledger figures
   and task logs. Facts it writes are the user's preferences and plans in the user's terms, not a
   narrative of the conversation.

6. **Legacy facts are sorted once, never silently lost.**
   - A deterministic Alembic data revision stamps every existing row `topic="unsorted"`. Unsorted
     facts are **not** injected (only `recall_memory` finds them) and show in the dialog under
     their own heading.
   - A bounded background **sort pass** (memory task model, ≤25 facts per run, runs after the
     next extraction until none remain) files each unsorted fact into a topic — rewritten to the
     length rule — or **retires** it with a reason (`profile`, `payee_rule`, `automation`,
     `ledger`, `resolved`, `stale`).
   - Retired facts are not injected, stay visible in the dialog under "Moved out of memory" with
     their reason for 30 days, then are purged. Nothing disappears without the user being able
     to see why.
   - `backend/scripts/migrate_memory_topics.py` does the same sort on demand, dry-run by default,
     printing every decision for review before `--apply`.

7. **Measure it.** Turn diagnostics record, per reply, how many facts and characters each topic
   contributed, so "does memory help" can be answered from data next time.

## Consequences

### Positive
- Lucius sees every lasting preference and convention, in full, on every reply — the facts that
  were evicted by recency now always arrive.
- No second copy of profile, payee-rule, automation or ledger facts to go stale.
- The memory dialog can group by topic, as Claude's memory does.
- Prompt size stays bounded by the caps (worst case ≈53 × 280 characters; typical ≈2k tokens,
  about what the block costs today).

### Negative
- Until the sort pass has run, the legacy facts are not in the prompt. On a book with ~130
  facts that is ~6 background runs (a few chat turns). Running the script closes the gap at once.
- Caps force merging: a user with many genuine conventions hits 20 and Lucius must consolidate
  rather than add.
- A fact that belongs in the profile is no longer kept anywhere unless Lucius updates the profile
  in the turn — the background pass will not do it for them.
- The sort pass is an LLM judgement; it can misfile or retire a fact the user wanted. The 30-day
  "Moved out of memory" view and the script's dry run are the safety net.

### Follow-ups
- Implementation plan: `.claude/rfcs/2026-10-25-memory-one-home-per-fact.md` (local).
- `CLAUDE.md` (Key invariants → AI harness) gains one line pointing here; the write-choke table
  gains a Memory row.
- `docs/product/lucius/memory.md` and `docs/DATA_MODEL.md` (the `memory` table) are updated in
  the same change as the code.
- Revisit `recall_memory` once every live fact is injected — it may only be needed for unsorted
  and retired facts.

## Alternatives considered

- **Keep the flat list, fix selection and truncation only** — rejected as the end state: it shows
  the right facts, but keeps storing copies of profile, rule and ledger facts that go stale, and
  gives the dialog no structure. Its pieces (inject durable facts first, enforce length) are
  folded into this decision instead.
- **One Markdown document per topic, rewritten by the model (Claude's shape)** — rejected: every
  change rewrites a whole document (cost, drift, silent loss of a line), and per-fact edit, forget,
  recall and dedup by id all break. Rows with a `topic` give the same grouping on screen.
- **An "About you" topic** — rejected: the profile already has structured fields for it and is in
  every prompt; a memory copy is the duplication this ADR removes.
- **Let the background pass write the profile and payee rules** — rejected: unattended writes to
  stores that drive money math and import categorisation, with no approval; Lucius does it in the
  turn, gated like any other change.
- **Classify the legacy facts inside the Alembic revision** — rejected: a migration must run
  offline, deterministically, on every install; an LLM call at boot is neither.
- **Drop the legacy facts and start fresh** — rejected: silent loss of things the user taught
  Lucius.

## Agent provenance

- **Decided by:** orchestrating Claude agent, with the repo owner choosing the direction ("one
  home per fact"; topics like Claude's memory) after a data review of a real book.
- **Lane / branch:** `main`, working session on the memory dialog (2026-10-24/25).
- **Alternatives weighed:** selection-only fix; document-per-topic; an About-you topic;
  background writes to profile and rules; LLM migration in Alembic; drop and restart.
- **Driving artifacts:** the memory review in the same session (cost log, tool-call log and the
  `memory` table of a real book, read-only); ADR `2026-10-14-one-definition-per-fact-across-screens`.
- **Why this way (one line):** memory was cheap but mostly carried stale copies and leftovers
  while the facts that matter never reached the prompt — giving each fact one home and showing
  memory's own facts whole fixes both.

## Amendment 2026-10-27 — a fifth topic, and the profile's notes become your instructions

Reviewing how the design plays out against the profile found two gaps.

**1. A fifth topic: `about_you` — "About you (beyond your profile)".** Cap 15, no expiry. It holds
what the profile has no field for: the people in the user's life (a roommate who shares the
rent, family they support), life context (where they split their time, their employer), small
personal facts that change how Lucius helps (colour-blindness, a pet's name). It never repeats a
profile field. Without it the stricter background pass would drop these facts, and nothing else
in the app keeps them. Topic order becomes `about_you`, `working_style`, `conventions`, `plans`,
`open_items`. "An About-you topic", rejected above, was rejected as a copy of the profile; this
one is defined as the part the profile cannot hold.

**2. The profile's "Notes for Lucius" become "Instructions for Lucius"** — the user's standing
instructions, as in Claude's own settings (instructions you write; memory it learns).

- **Who writes them:** the user in Settings, or Lucius in a conversation — when asked, or on his
  own judgement when the user states something as a standing rule ("always…", "never…") —
  through `update_profile`'s `notes` field. That write follows the conversation's approval mode
  like every profile write, so the user sees the before and after. Lucius edits a line or adds
  one; he does not rewrite the user's text wholesale. The **background pass never writes them**:
  it runs unattended, with no approval step.
- **Instructions or memory?** A rule the user states, and would expect to find and edit
  themselves, goes in the instructions. A preference Lucius infers or is told in passing goes
  in memory (`working_style`). Life events and plans go to `plans`; people and personal context
  to `about_you`. The system prompt's current line sending pronouns, preferences and life events
  to notes is replaced by this split.
- **No duplicate in memory:** the background pass is given the instructions text and does not
  store what they already say.
- **Every reply includes them in full**, as notes are today. Existing notes text is kept as
  it is — there is no telling which lines Lucius wrote — and the user can edit it.

**3. One routing guide decides where a fact goes.** It is written once, in code
(`ai.memory_topics.ROUTING_GUIDE`), and rendered into everything that chooses a home: the
system prompt, the background pass's prompt, the "Tell Lucius" prompt, and the descriptions of
`remember_fact` and `update_profile`. No second copy in prose anywhere. Its content:

| The fact is… | It goes to | Written by |
|---|---|---|
| Something the profile has a field for (name, date of birth, residence, nationality, tax residency, employment, marital status, income, expenses, saving, risk, retirement age, target allocation, briefing time, benchmark) | **Profile field** | user, or Lucius with approval |
| A standing rule the user states for how Lucius should behave, that they would expect to find and edit themselves ("always…", "never…") | **Instructions for Lucius** | user, or Lucius with approval |
| How a payee or bank text is categorised or named | **Payee rule / default** | user, or Lucius with approval |
| Something that should happen on a schedule (a booking, a report, a reminder) | **Automation** | user, or Lucius with approval |
| A balance, holding, price, transaction, trade, or a task that was done | **Nowhere** — the ledger already has it | — |
| A goal, commitment or plan figure the Plan screen holds | **Plan / goals / commitments** | user, or Lucius with approval |
| About the user but no profile field fits (people in their life, personal context) | Memory → **About you** | Lucius, the background pass, or the user |
| A preference Lucius infers, or is told in passing, about how to work | Memory → **How you want Lucius to work** | Lucius, the background pass, or the user |
| A bookkeeping convention no payee rule can express | Memory → **Conventions** | Lucius, the background pass, or the user |
| An intention or upcoming life event | Memory → **Plans** | Lucius, the background pass, or the user |
| Something unresolved the user will expect Lucius to come back to | Memory → **Open items** (expires) | Lucius, the background pass, or the user |
| State of this conversation (what is being waited on, what was offered) | **Nowhere** | — |

When two rows fit, the first one wins: a profile field beats an instruction, an instruction
beats memory. Only the memory rows are open to the background pass; every other home is
written in a conversation, through its own tool, under the conversation's approval mode.

The topic list stays **fixed**. The model never creates topics: a topic carries behaviour (cap,
expiry, what the background pass may store), and model-invented topics drift into
near-duplicates. An optional per-fact `subject` label for grouping inside a topic, with no
behaviour attached, is a possible later addition and is not part of this change.
