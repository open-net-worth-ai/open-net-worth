# Rules — fixed transaction rules, no AI (design)

Status: **draft for owner review** · 2026-11-21 · flag `transaction_rules` (default on since 2026-11-24)

## Problem

A user wants "if payee is A and amount is between 10 and 30 → category X, tag T1", or "payee A,
amount ≈ 100 → split 30 to X/T1, rest to Z/T3", applied to every future transaction and, on demand,
to past ones, without paying for a Lucius run or sending the row to a model. The payee default
covers the single-category case only. Today the only way to do more is a Lucius Automation on
"A transaction posts". That costs a model call and runs after the write.

The deterministic engine still exists: `transaction_transform` Automations run inside
`ledger.create_transaction` and statement import. But since 2026-08-02 nothing can create one, and
it cannot match on payee or split. This brings it back as a first-class **Rules** tab under
Automations: **Lucius · Data Sync · Rules**.

Modelled on Actual Budget's rules engine (research notes, 2026-11-21): ordered rules, all/any
conditions, every matching rule applies, split actions, "apply to matching transactions", and
"create rule from a transaction".

## What a Rule is

A Rule is still ONE Automation row, written through `apply_automation_change`, the same
store and the same choke as before. It is distinguished by:

```
trigger:   {kind: "event", event: "txn", when: {match: "all"|"any", conditions: [{field, op, value}]}}
execution: {kind: "transaction_transform", position: int, stop: bool, override_existing: bool,
            set: {category?, tags?, note_append?, memo_append?, payee?, route_to_account?},
            splits?: [{amount: {type: "fixed"|"percent"|"remainder", value?}, category, tags?, memo?}]}
```

- `_validate_execution` accepts `transaction_transform` again, and only when the flag is on. The
  shape is validated by `executors.validate_execution`, which is extended for `splits`, `payee`
  and `position`.
- Legacy rows (`priority`, flat `when` keys such as `description_contains`) keep working
  unchanged. `position` defaults to `priority`, and `stop` defaults to **true** for a stored row
  that has no `stop`. Every new Rule stores `stop: false`.
- Each Rule's `instructions` is the rule rendered as a sentence
  (`projections.describe_transaction_transform`), as now. It is shown in the list and is what
  Lucius reads.

### Conditions

These reuse `txn_conditions.py`, the same fields and validator as the Lucius "transaction posts"
trigger, so both editors share one condition row:

| field | ops |
|---|---|
| payee, account, kind | is / is not (one or a list) |
| amount | over / under (existing) + **is**, **between**, **approx (±7.5%)** |
| description, memo | contains / doesn't contain / starts with / regex |

New: `when.match = "any"`. The default is `all`. Conflict detection (`_conflict`) runs only for
`all`. The new amount ops are added once and used by the Lucius trigger too.

### Actions

- **Set**: category, add tags, append to note, append to memo, set payee.
- **Split** into lines: a fixed amount, a percent of what remains after the fixed lines, or the
  remainder. Each line has a category plus optional tags and memo. Lines are computed on the
  allocated leg (`allocated_leg_amount`), and rounding goes to the last remainder line. If the
  lines do not add up and there is no remainder line, the Rule is **skipped for that row** and the
  skip is recorded on the run. A money write is never refused because of a Rule.
- **Route to account**: an imported row becomes a transfer to another account. This is unchanged,
  statement-draft phase only.
- Guards are unchanged: a Rule never overrides a category the user picked
  (`category_explicit`). It never re-splits a row that already has splits. It never categorizes an
  adjustment. It replaces an existing category only with "Replace a category already set"
  (`override_existing`).

## When Rules run

**Order: every active Rule, in list order (`position`).** Each Rule sees the row as the previous
Rules left it, and when two Rules set the same field the later one wins. A Rule's "Stop here" stops
the Rules after it.

**Placement in `ledger.create_transaction`:** the label/split pass moves from before payee
resolution to just after it, and before the per-kind payee default fills a blank category. The
precedence stays **explicit > rule > payee default > None**, and "payee is" can now match.
`_apply_splits` re-runs on a row a Rule split, so the ledger's own split validation is the
authority. Statement routing stays at the statement-draft phase.

This runs on every path that converges on the choke: manual entry, Lucius, email, statement
import, bank sync, YNAB/Actual sync. It runs on create only. An edit never re-runs Rules, except
through "Run rules" below.

## Applying to transactions already in the book

This is a new service, `services/automations/rule_apply.py`. Writes go through the
`ledger_service` update choke in one `locked_unit_of_work`, with one checkpoint so Undo works.

- `preview_rule_matches(rule, limit)`: the existing transactions the (unsaved) rule matches,
  newest first, each with its before → after. The editor shows them live.
- `apply_rule(rule, txn_ids)`: **only this Rule's actions** on the ticked rows, without
  re-checking other Rules (as Actual does). The rule doesn't have to be saved first.
- `run_rules(txn_ids)`: re-runs every active Rule, in order, on the selected ledger rows.

All three respect the same guards (user-picked category, existing splits, reconciled/Starting
Balance rows refused as they are today).

## Screens

- **Automations → Rules tab** (flag on): an ordered list. Each row shows the rule's sentence, a
  match count and when it last matched, a pause toggle and a ⋯ menu. Rows reorder by drag. Below
  the list: "Rules run top to bottom; every matching rule applies." Actions: "New rule" and a
  filter box.
- **Rule editor** (modal). "If **all/any** of these match" with condition rows, then "Then"
  action rows, then a "Split into lines" section with a line per split (allocation + category,
  tags, memo), then "Matches N transactions" with a selectable table and **Apply to N**, then
  Save.
- **Transaction edit menu → "Create rule…"** opens the editor prefilled with: payee is X, amount
  ≈ Y, account is Z (only if the payee has none), and as actions the row's category, tags, or
  its split lines as fixed amounts.
- **Ledger multi-select → "Run rules"**.

## Lucius

`create_automation` and `update_automations` accept a Rule (the `execution` above), so "make a
rule for my gym payments" lands as a Rule rather than a Lucius run. The tool docs steer
deterministic labelling to Rules and judgement calls to Lucius Automations. A new
`apply_rule_to_transactions` tool (mutates ledger, Ask by default) wraps `apply_rule` and
`run_rules`.

## API contract (backend ↔ frontend, fixed)

A Rule is created, edited and deleted through the existing routes: `POST /api/automations`,
`PATCH /api/automations/{id}` and `DELETE /api/automations/{id}`. The body carries
`trigger: {kind: "event", event: "txn", when: {...}}` and `execution: {kind:
"transaction_transform", ...}` exactly as in "What a Rule is". `GET /api/automations` returns Rules
with everything else, and the client tells a Rule by `execution.kind === "transaction_transform"`.
With the flag off, the server refuses a create or edit that carries `transaction_transform`
(400), as it does today.

- `when.match`: `"all"` (the default) or `"any"`.
- `when.conditions[]` items are `{field, op, value}`. The amount ops are `over`, `under`, `is`
  (number), `between` (`[low, high]`, inclusive) and `approx` (number, ±7.5% inclusive). Every
  amount is an absolute value.
- A split line is `{amount: {type: "fixed", value: number} | {type: "percent", value: 0–100} |
  {type: "remainder"}, category: id, tags?: string[], memo?: string}`. A Rule has at most 10
  lines.

New routes (all 404 with the flag off):

| Route | Body | Returns |
|---|---|---|
| `POST /api/automations/rules/reorder` | `{ids: string[]}`, every Rule id in its new order | `{automations: Automation[]}`, the Rules in order |
| `POST /api/automations/rules/preview` | `{rule: {trigger, execution}, limit?: number (default 50, max 200)}` | `{total: number, matches: RuleMatch[]}` |
| `POST /api/automations/rules/apply` | `{rule: {trigger, execution}, transaction_ids: string[]}` (max 500) | `RuleApplyResult` |
| `POST /api/automations/rules/run` | `{transaction_ids: string[]}` (max 500) | `RuleApplyResult` |

```
RuleMatch = {transaction_id, date, description, memo, payee_id, payee_name, account_id, account_name,
             amount, currency, kind, category, tags, splits,
             after: {category, tags, description, memo, payee_id, splits} | null,   // null = no change
             skipped_reason: string | null}   // e.g. "category picked by you", "split doesn't add up"
RuleApplyResult = {applied: number, unchanged: number,
                   skipped: [{transaction_id, reason}], checkpoint_id: string | null}
```

The "Run rules" action and "Create rule…" use these routes plus the existing transaction fields.
The prefill happens on the client only.

## Out of scope (v1)

Learning rules from categorizing (the payee default already covers this). Stages
(pre/default/post); payee rules remain the "pre" stage. Formulas and templates. Date conditions.
Delete-transaction action. Rules running on edit.

## Decision record

An ADR amends `2026-08-02-replace-rules-and-scheduled-tasks-with-automations`. Rules come back
as a visible kind of Automation (the owner, 2026-11-21), on the same row, store and choke. The
order changes from first-match to all-apply. The pass moves after payee resolution.

## Verification

- **Engine** (pytest, both flag states): ordering and later-wins, `any`/`all`, the amount ops,
  split maths (fixed/percent/remainder/rounding/doesn't-add-up skip), the precedence chain, the
  placement after payee resolution, legacy rows unchanged, apply/run through the update choke,
  and Undo.
- **Frontend** (vitest): the tab, the editor, and the prefill from a transaction.
- **QA** on isolated ports 8090–8099 with synthetic data: Krishna's two examples end to end
  (create, sync, apply to history).

## Frontend notes

UI decisions made while building the screens (frontend only):

- **Rules tab** is the third `PageTabs` entry of Automations (`?view=rules`), shown when
  `transaction_rules` is on. The tabs now show when either data sync or Rules is on. The Lucius
  list filters out `execution.kind === 'transaction_transform'`.
- **Reorder**: drag the row handle, or focus it and press the arrow keys; ⋯ has Move up / Move
  down. Reordering is off while the filter box has text (a partial list cannot define an order).
  Each move posts every Rule id to `reorder`, optimistically, and reverts on failure.
- **Editor** is mounted only while open (a modal), so each opening starts from its own Rule or
  seed. New Rules take `position = max + 1`; `stop: false`. Name left blank is generated from the
  sentence. The client also writes `instructions` (the sentence) in the create/patch body in case
  the server does not render it; the server's own rendering wins on read.
- **Conditions** reuse `TransactionConditionFields`, with `match` (all/any) in the heading. Amount
  gets `is`, `between` (two inputs, stored `[low, high]`) and `is about (±7.5%)` for both the
  Rule and Lucius editors. Conflict checks run only for `all`.
- **Preview** is debounced 400 ms, asks for 50, and ticks every changing, non-skipped row. Apply
  sends the ticked ids with the unsaved rule; the result line points at the checkpoint for undo.
- **"Create rule…"** is a button in the transaction drawer footer. The drawer is hosted app-wide,
  so it hands a seed to the Rules tab through a module-level slot (`rules/ruleSeed.ts`) and
  navigates to `/automations?view=rules`; the tab opens the editor on mount. Hidden when the flag
  is off. **Run rules** is in the register bulk bar's More menu, in chunks of 500 ids.
- Assumed (not in the contract): `stats.match_count` / `stats.last_matched_at` feed the row
  summary (the existing engine already writes them); `set.payee` is a payee id; "Append to
  description" is `note_append`.
