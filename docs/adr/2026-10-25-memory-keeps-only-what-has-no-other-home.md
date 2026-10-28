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

> **Amended 2026-10-27, four times** — (4) **simpler, and it wins over 2 and 3**: five fixed
> sections in code, a free-text topic name per fact, everything shown in full with dates, one
> 280-character limit, settings like Claude's; (3) for now, five fixed sections (You, Money, Plans,
> Open items, Other) with flexible topics; Lucius owns the structure, the user owns the content; (1) the profile's notes become Instructions for Lucius, and
> one routing guide decides profile vs instructions vs memory; (2) **sections and topics are
> dynamic** — seeded with defaults for a wealth assistant (You, Money, Plans, Open items), with
> behaviour carried by a section's switches ("always shown", "items expire") — replacing the
> fixed topic list below. See the amendments at the end; where they differ, they win.

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

## Amendment 2026-10-27 (2) — everything is dynamic, with sensible defaults

Replaces the fixed topic list (decision 2), amendment 1's first point, and amendment 1's closing
paragraph that kept the topic list fixed. The owner's call:
start from defaults that suit a wealth assistant, but let **sections and topics both** grow,
change and go away — as Claude's memory does (sections like "You / Topics / Areas", topics
inside them named as they come up).

**Behaviour belongs to a section's properties, not to its name.** A section is a row with two
switches, and that is all the code ever branches on:

- **Always shown** — every fact in it reaches every reply, whole.
- **Items expire** — every fact in it carries an expiry (14 days by default, 30 at most).

A section with neither switch is an ordinary one: its topics are shown whole up to a size budget,
then as `name — summary` with a pointer to `recall_memory`.

**Defaults, seeded once on every book** (renameable, removable, reorderable like anything else):

| Section | Switches | Default topic names offered to Lucius |
|---|---|---|
| **You** | always shown | *How you want Lucius to work*, *About you (beyond your profile)* |
| **Money** | — | *Housing*, *Family*, *Investing*, *Tax*, *Cards & payments*, *Income & work*, *Bookkeeping* |
| **Plans** | — | none — one topic per plan or big decision (a home purchase, an early-retirement idea) |
| **Open items** | items expire, always shown | none — one flat list |

Default topic names are suggestions: a topic row exists only once a fact lands in it.

**Who may change the structure.**
- The **user**, from the dialog: add, rename, reorder or delete a section; flip its switches;
  rename, move, merge or delete a topic.
- **Lucius in a conversation**: the same, through his memory tools, under the conversation's
  approval mode.
- The **background pass** may create a *topic* (reuse-first: it is given every existing name and
  summary and must use one that fits) but never a *section* — it files into the sections that
  exist, and stores nothing if none fits. The **tidy pass** merges overlapping topics and
  refreshes summaries; it never deletes a section.

**Bounds,** so a dynamic structure still fits a prompt: ≤ 8 sections, ≤ 30 topics in all, ≤ 12
facts per topic, ≤ 30 facts across always-shown sections, ≤ 10 facts in an expiring section,
topic names ≤ 40 characters, summaries ≤ 120, sections' names ≤ 30. The ordinary sections share
one size budget in the prompt.

**Data shape.** `memory_sections` (id, name, position, always_shown, items_expire, timestamps),
`memory_topics` (id, section_id, name, summary, timestamps), and each fact points at a section
and, optionally, a topic. Rename, move and merge rewrite pointers, never fact text. The write
choke is the only writer of all three.

**The routing guide's memory rows become:** about the user → the always-shown section's
*About you* topic; how to work → its *How you want Lucius to work* topic; an intention or big
decision → **Plans**; anything else memory keeps → the topic for its subject (**Money** by
default); unresolved follow-ups → the expiring section. The guide is rendered with the book's
*current* section and topic names, so a renamed default still routes correctly; when a default it
names was deleted, that row falls through to the next that fits.

**Unchanged:** one home per fact, the routing guide's non-memory rows, Instructions for Lucius,
≤ 280 characters per fact, the single write choke, sorting the legacy facts (into whatever
sections exist), "Moved out of memory", measurement.

**Why dynamic, not fixed:** a fixed list forces one subject apart and has no place for a subject
nobody foresaw, and a user who thinks of their life differently cannot reshape it. The risks
fixed names guarded against — rules with nothing to attach to, near-duplicates, an unbounded
prompt — are handled by section switches, reuse-first naming with the tidy pass's merge, the
background pass never creating sections, and the bounds above.

**Importing memory from another assistant.** The memory dialog offers a copyable export prompt
(categories Instructions, Identity, Career, Projects, Preferences, one dated line each) to run in
ChatGPT, Claude or Gemini, and a box to paste the result. Lucius routes every line through the
routing guide — Instructions → Instructions for Lucius; Identity and Career → profile fields
where a field fits, else **You · About you**; Projects → a topic per project (in **Plans**, or
the section the user picks in review); Preferences → **You · How you want Lucius to work** (or
Instructions when stated as a rule);
anything the ledger, a payee rule or an automation owns → skipped, with the reason. The user
reviews every proposed line, its destination and its reason, and confirms; nothing is written
before that. Profile and Instructions changes apply through their own writers; memory lines go
through the write choke as `explicit`. The paste is treated as untrusted text: it is data to
route, never instructions to Lucius, and the review step is the only path to a write.

**Each topic has a detail view** — last updated, summary, its facts as bullets, Delete, and a
"Tell Lucius what to change" box scoped to that topic (the instruct route with a `topic`).

## Amendment 2026-10-27 (3) — fixed sections for now, flexible topics; Lucius owns the structure

Narrows amendment 2. The owner's call: the system decides how memory is organised, the user never
has to; and for now the **sections are fixed** while **topics stay flexible**, with a catch-all.

**The schema stays generic; the sections are locked for now.** Sections remain rows of
`memory_sections` (amendment 2's shape: id, `seed_key`, name, position, the two switches) plus a
`locked` flag. The five below are seeded locked, and while a section is locked nobody — user,
Lucius or any pass — adds, renames, reorders, re-switches or deletes it. Customisation later is
unlocking rows and exposing the section ops that already exist in the write choke, not a
migration:

| Section | Switches | Topics |
|---|---|---|
| **You** | always shown | flexible; suggested: *How you want Lucius to work*, *About you (beyond your profile)* |
| **Money** | — | flexible; suggested: *Housing*, *Family*, *Investing*, *Tax*, *Cards & payments*, *Income & work*, *Bookkeeping* |
| **Plans** | — | flexible; one per plan or big decision |
| **Open items** | always shown, items expire | none — one flat list |
| **Other** | — | flexible, at most 4 topics — the catch-all |

Behaviour still follows the two switches, never a name, and facts and topics point at a section
`id`, so nothing in the code assumes these five are the only sections. **Other** takes what fits no other section instead of it being
dropped; the tidy pass keeps trying to move its topics to a better section, so it stays a small
holding area rather than a second, unsorted memory.

**Who may do what:**

| Who | Facts | Topics |
|---|---|---|
| **User** (dialog) | edit text, forget, add via "Tell Lucius" | delete one (with its facts) |
| **Lucius** (a conversation, or the dialog's "Tell Lucius" box) | anything | create, rename, move between sections, merge, delete |
| **Tidy pass** (background, periodic) | merge/replace inside a full topic | create, rename, move, merge, refresh summaries |
| **Background pass** (after a turn), **sort pass** | add, update, remove, retire | create (reuse-first) |

- The dialog has **no organising controls** — no rename, move or merge. A user who wants memory
  organised differently tells Lucius ("keep rent and the landlord under Housing") in the box or
  in chat. **Delete a topic** stays, as in Claude's memory.
- In a conversation, Lucius's memory writes follow the approval mode; from the "Tell Lucius" box
  the user's own request is the approval.
- **Unchanged from amendment 2:** the switches' meaning, the suggested topic names, reuse-first
  naming, the bounds (≤ 30 topics, ≤ 12 facts per topic, ≤ 30 always-shown facts, ≤ 10 expiring),
  the routing guide (its memory rows name these five sections by `seed_key`; anything memory keeps
  that fits nowhere else → **Other**), import, the topic detail view, the `memory_sections` and
  `memory_topics` tables. Amendment 2's section ops stay in the write choke but are refused for
  locked sections, and no route or tool exposes them yet.

## Amendment 2026-10-27 (4) — simpler: fixed sections, a topic name per fact; settings like Claude's

The owner's call: amendments 2 and 3 over-built the structure. **Where this amendment differs from
amendments 2 and 3, it wins.** It drops the `memory_sections` and `memory_topics` tables, the
section switches, locked rows, caps, the prompt size budget, topic summaries and the per-actor
permission table.

### Structure

- **Five sections, a fixed list in code:** You, Money, Plans, Open items, Other. Each fact stores
  its section as a plain string key (`you`, `money`, `plans`, `open_items`, `other`) — a column a
  later customisation can widen — and a **free-text topic name**. Facts with the same name in the
  same section group together; there is nothing else to keep in sync.
- **You** has two suggested topics: **About you** — only what has no profile field (an employer,
  languages, who shares the household) — and **Preferences** — observations Lucius makes, written in
  the third person ("Prefers short answers on reconciliation"). The memory UI is **not** merged with
  Settings → Profile, and no memory topic is named "Profile".
- **Storage is one row per fact; the model and the user see it differently.** Lucius gets every
  live fact in full, grouped section → topic, each with its month (`(Oct 2026)`). The user sees
  sections with topic rows (name, fact count, last updated); a topic opens to its facts, a Delete,
  and a "Tell Lucius what to change" box scoped to it. Open items are listed directly.
- **Lucius owns organising** (topic names, moving facts); the user edits or forgets a fact, deletes
  a topic, or asks Lucius to reorganise.

### What Lucius sees, and when to trust it

- **Everything, in full.** `recall_memory` stays as a keyword backup and gains lookup by topic.
  The scale-up path, when memory outgrows a prompt, is the Claude Code / Codex shape: always show
  You and Open items, list the other topics as an index, and let Lucius recall a topic on demand.
- **Memory is context, never truth.** Before acting on a memory that names something changeable (an
  account, a payee, a plan, an amount), Lucius checks the owning record. When they disagree the
  record wins, and Lucius corrects the memory in the same turn.

### Limits and decay

- **One limit: 280 characters per fact.**
- **Open items** expire after 14 days by default.
- **Plans fade:** a plan not mentioned for 90 days expires; mentioning it again resets the clock.
- **Usage tracking:** Lucius cites the facts he used in a reply (as Codex does). A fact unused for
  ~90 days is retired by the tidy pass and stays visible under "Moved out of memory" for 30 days.

### Instructions or memory

- An explicit, lasting "do this / don't do that" → **Instructions for Lucius** (profile notes):
  written by the user, or by Lucius when asked or when the user states a standing rule, under the
  approval mode; never by the background pass.
- A preference Lucius infers, or is told in passing → **You · Preferences**.

### Who writes, and when

- The noise was the **extraction rules** — no "other home" rule, a time-bound category that invited
  dated follow-ups, runs on fragments, facts that grew with each rewrite — not automations.
  **Automations and external MCP clients may write memory.**
- The background pass runs when a conversation **goes idle**: no new message for 30 minutes. The
  server checks every few minutes and on startup, using each conversation's last-message time and
  its existing extraction cursor, and reads only what is past the cursor. An automation run is
  processed as soon as it finishes.

### Settings (Settings → Chat & Memory → Memory), like Claude's

| Row | Behaviour |
|---|---|
| **Search and reference chats** | Lucius may search the user's past conversations for relevant details — a new capability |
| **Generate memory from chats** | today's master memory switch, renamed |
| **Include sensitive topics in memory** | off by default; covers health, religion, political views, sexuality, ethnicity, criminal or legal matters. Full card, account, ID or passport numbers and passwords are never stored whatever the setting; last-4 digits are fine |
| **Stored memories** — Review… | opens the memory dialog |
| **Import memory from other AI providers** — Start import | the reviewed import flow of amendment 2 |
| **Export memory** — Export | a `.zip` of Markdown files, one per topic |

### Kept, and not now

- **Kept:** one home per fact and the routing guide; Instructions for Lucius; the single write
  choke; the one-time sort of legacy facts with a reviewed dry run; "Moved out of memory"; import
  with review; the topic detail view.
- **Not now:** the in-chat "memory updated" line; a per-conversation "don't remember this chat";
  special handling for other installs' migration.

## Amendment 2026-10-27 (5) — final-review rulings on decay, sensitivity and the first sort

Amendment 4 still wins on everything it says; this only sharpens the rules the final review found
unsafe on a real book.

- **The unused clock** is the newest of the last citation, the last time the user said the fact
  again, the last user edit (`edited_at`), the day the sort filed it and its creation. Being shown
  in the prompt never counts. While no live fact has been cited for 90 days, nothing is retired
  as unused: the missing signal means citations are not arriving, not that the facts are unused.
  A plan's clock also counts its filing day and a user edit.
- **Expiry follows the section, not the legacy tier.** A filed fact expires on its `expires_at`,
  and an Open item always has one. A `durable` legacy fact filed outside Open items drops its
  stray date.
- **Sensitive facts stay out while the setting is off.** The prompt block and recall leave out a
  fact flagged sensitive (it stays in the list and returns when the setting is turned on). The
  sort flags sensitive facts, and with the setting off it retires them with the new reason
  `sensitive` ("Not kept — sensitive").
- **Filing runs the guards a new fact meets.** A legacy fact over 280 characters is filed only with
  a shortened wording the sort proposes — the one exception to "the sort never edits"; one with a
  full card, account or ID number is retired `sensitive`.
- **Idle extraction** skips Captures, and stops retrying a conversation after three failed
  extractions in a row until it has new messages.
- **The first sort of a live book** is reviewed with the app stopped, because the background sort
  starts at boot — `docs/ai/MEMORY_MIGRATION.md`.

## Amendment 6 (2026-10-27) — the sensitive-topics setting governs saving only

Supersedes amendment 5's "Sensitive facts stay out while the setting is off" (R24). Decided by the
owner in session.

- **The setting governs SAVING only.** With it off, a new save the model marks sensitive is refused
  (`sensitive_off`) — the extractor's `new`, an update that writes new sensitive wording to a live
  fact, `remember_fact` and import. The marker is the model's per-write yes/no and nothing more.
- **No `sensitive` field is stored on a fact.** A saved fact is used like any other — in the reply
  prompt, recall, the extraction listing, the daily merge and export — whatever the setting.
- **Filing a legacy fact is not a new save.** The sort files it whatever its topic and setting, and
  its prompt no longer asks for a sensitive flag.
- **The retire reason `sensitive` is renamed `identifier`** ("Not kept — had a card, account or ID
  number") and is kept only for a legacy fact that carries a full card, account or ID number or a
  password; those are never stored, whatever the setting.

## Amendment 7 (2026-10-27) — Profile; a model never deletes; a forgotten fact stays forgotten

Decided by the owner in session (rulings R29 and R30, and the unused-clock scope); amends
amendment 4's topic names and amendment 5's unused clock.

- **The You topic is "Profile"** (R30), with **Preferences** beside it. The routing guide's first
  row now reads "a Settings profile field (update_profile) — not memory" and its eighth
  "Memory · You · Profile", and the guide's header, the sort prompt and the import prompt say the
  two are different homes. The write choke files a topic "About you" (any case) in You as
  "Profile"; revision `202610271800_memory_forgotten` renamed the ones already stored. A data step
  in the revision, not a fix-up in the sort or tidy, because it is one-time, runs before anything
  reads the book, and leaves no code behind that every later pass would run for nothing.
- **A model never deletes a fact** (R29). An extraction `remove` retires the fact `stale`; a
  tidy merge retires the merged-away fact with the new reason `merged` ("Merged into another
  memory"). Both wait under "Moved out of memory" and are purged after 30 days like any retire.
  The tidy's "more than half of a topic" refusal counts merged retires, and an id a reply both
  updates and removes is kept. The choke backs this up: an implicit `remove` of a fact that is
  neither retired nor expired is a `stale` retire.
- **A forgotten fact stays forgotten** (R29). A user-initiated forget — the forget button,
  Forget everything, deleting a topic, `forget_fact`, a "Tell Lucius" removal — still
  hard-deletes and records a SHA-256 of the fact's text, casefolded with punctuation and
  whitespace collapsed, in `memory_forgotten` (the hash only, never the text). The choke refuses
  an implicit `new`, or an implicit update rewriting a live fact, that matches one (`forgotten`);
  filing a legacy fact is not a new save and is not checked. An explicit save of the same text
  — the user asks again — is allowed and clears the fingerprint. A paraphrase has a different
  hash and is not caught; that is accepted. A reset or a whole-book import wipes the
  fingerprints with the memory.
- **Lucius's own `remember_fact` is checked too** (R31, fix round 1). A tool call is not the user
  asking: it refuses a forgotten match (`forgotten`, telling Lucius to ask the user first) unless
  it carries `user_asked: true` — "only when the user, in their latest message, explicitly asked
  you to remember this" — which saves it and clears the fingerprint. The same holds for an MCP
  client. The settings page, import and "Tell Lucius" are the user's own actions and unchanged.
  A fact carrying a full card, account or ID number leaves no fingerprint when forgotten; the
  fingerprint folds Unicode variants (NFKC); and the sort may not retire as `unused` or `merged`,
  the tidy's own verdicts.
- **The unused clock covers only background-learned facts in Other.** A fact the user saved
  (`source: explicit`) or one in You or Money is never retired as `unused`. Plans and Open items
  keep their own decay, and nothing is retired as unused while no citations arrive.

## Amendment 8 (2026-10-27) — a one-line Summary per topic

Decided by the owner in session. Each topic page shows, between **Last updated** and **Details**,
a one-line **Summary** of what the topic covers ("Who they are — location, employer, background,
languages"), as Claude's memory pages do. It is display-only: not a fact, never written through
`apply_memory_change`, and never added to Lucius's prompt (the prompt keeps rendering the facts).
It is generated in the idle sweep, after extraction, the sort and the tidy, by
`ai.memory_summaries.refresh_summaries`: one memory-model call (`source=memory_summary`) for up to
20 topics whose stored row is missing or whose digest of live fact ids and text has changed. A
returned line is kept only for a topic that was sent, at most 120 characters, with no identifier
and no run of more than three digits. A topic sent with no usable line back backs off until its
facts change or 24 hours pass, behind topics never tried, so a few stubborn topics cannot starve
the rest. Rows live in `memory_topic_summaries` (revision `202610272100`), checkpoint-excluded
because they are derived. A topic with no live facts loses its row at once, whatever the memory
setting: the write choke prunes after any write that removes, retires or moves a fact (no model
call), the sweep prunes at the start of every tick, and the API and export show only rows whose
topic still has live facts. The export writes the summary as an italic line under each topic's title. Benchmark case
T03 holds the line to its length and to restating no number from the facts.

## Amendment 9 (2026-10-28) — one keep test, the house voice, Open items need your words

Decided by the owner in session, after reviewing their own memory once the upgrade sort had run:
of 99 filed facts, 62 failed the test below — snapshots the ledger holds, bookkeeping threads,
incidents and the lessons Lucius drew from them, payee-rule copies, stale what-ifs — and nearly
every Open item was a thread Lucius had left mid-way.

- **One keep test, in every writer** (`memory_structure.KEEP_TEST`, in the extract, sort and
  import prompts): a fact stays only if it is about the person or their money life, will still be
  true in three months, and nothing else in the app holds it. Never kept: a lesson about how
  Lucius should do a task, an incident, an app or connector problem, a suggestion the person did
  not take up, research results, a what-if. Only the lasting part is stored — no "decided on…",
  "stated…", "awaiting…" tail; a date stays only when it is the fact.
- **The house voice** (`memory_voice.VOICE_RULE`): a fact is written about the person, never
  "User…", "the advisor", "the client" or a leading "As of <date>:". The prompts no longer cast
  Lucius as an advisor keeping notes on a client — the framing the old memory copied into its
  facts. The upgrade sort marks a legacy note in the old framing `[reword]` and keeps its new
  wording only when `memory_voice.keeps_facts` finds every number and date (character for
  character — a leading "As of <date>:" is the one date it may drop), every quoted text, every
  name and as many negations intact, and no name the old wording lacked; otherwise the note keeps
  its old words. This is the sort's second licence to edit, beside shortening one over 280
  characters. Known limit: a rewording that flips a meaning without touching a number, name or
  negation ("up" → "down") passes; the sort prompt forbids it, the guard cannot see it.
- **An Open item needs the person's own words** (`memory_open_items.guard`, on every extraction):
  a new Open item, or an update moving a fact into Open items, must carry a `quote` found in what
  the person typed in that conversation — never Lucius's turns, an attachment, a harness note or
  other context the app adds (`chat_context_markers.is_not_said`) — that shares a content word
  with the item, so filler like "hmm, maybe" grounds nothing; at most one new Open item is kept
  per extraction pass. What Lucius offered, proposed or is waiting on never grounds one. The
  upgrade sort never files an Open item (nothing records that the person asked for an older
  note); a reply placing one there is a `stale` retire. "Tell Lucius" and `remember_fact` with
  `user_asked` are the person asking, and are unchanged.
- **Only Open items expire.** `memory_store.is_expired` (and its frontend mirror in
  `memoryGroups.ts`) lets a filed fact expire only in Open items, and the write choke stores no
  date on any other section, so a stray legacy date can no longer retire or hide a Preference. No
  data migration: the only book already through the upgrade sort is the owner's, cleaned by hand.
- **The owner's own book** was cleaned by hand from a reviewed list (kept facts rewritten to their
  lasting part, eight standing rules moved to Instructions for Lucius with the owner's approval,
  the rest moved out — kept 30 days), not by a migration: no other book has been through the
  upgrade sort yet, and the sort above now applies the same test.
- Benchmark cases E19–E23 and S03 hold these rules: an incident, a bookkeeping thread and an
  untaken offer store nothing; "remind me" makes exactly one Open item; a preference is stored as
  its kernel; older notes in the old framing are filed in the house voice or moved out, and none
  becomes an Open item.

## Amendment 10 (2026-10-29) — Recent context: what was decided reaches every run

Decided by the owner in session, after a Capture booked a flatmate's PayNow with the wrong split:
the agreed amount and split had been settled in a chat a few hours earlier, but long-term memory
held only to-dos ("send the request", "collect the share") and the Capture — a separate run —
fell back on past transactions. One long-term layer was doing two jobs.

- **The Open items section becomes Recent** (key `open_items` kept, no data to move; label
  "Recent", alias accepted by `normalise_section`). It holds what a chat decided or set in motion
  that matters over the next two weeks — an agreement about a payment someone owes and exactly how
  to book it, a decision whose result is still to come, something the person will do or asked to
  be reminded of — written ready to act on. The three-month keep test (amendment 9) is for
  long-term facts; Recent is its one exception.
- **Every run reads it first.** `memory_render` puts Recent at the head of the memory block, with
  the instruction to do what a matching entry says over any past pattern and to close it once
  done. The block already reaches chats, Captures, automation runs (the lean prompt that strips it
  is unused) and the daily briefing, which shows it without the close instruction.
- **Only the person adds; any run may close.** An entry still needs a quote from the person's own
  typed words (amendment 9's guard), at most three per extraction pass. An automation run's
  extraction may update or remove but never add one (`extract_and_merge(from_person=False)`: its
  "user" turn is the automation's prompt), and Captures stay unextracted. A run that does what an
  entry says closes it with the new tool `close_recent_items(handles)` — retired `resolved`, never
  deleted, so no later run waits for something that already happened. Unattended runs pass the
  approval gate unchanged, so a Capture can close what it acted on.
- **Bounded.** At most ten live entries; past that the least recently touched lapse (`stale`) in
  `purge_expired`. Each lapses after 14 days unless it names a date.
- Export writes `recent.md` (was `open-items.md`).
