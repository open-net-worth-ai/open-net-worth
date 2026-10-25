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
