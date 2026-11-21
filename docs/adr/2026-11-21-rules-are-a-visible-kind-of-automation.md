---
number: 2026-11-21-rules-are-a-visible-kind-of-automation
title: Rules come back as a visible kind of Automation — all matching rules apply, after payee resolution
status: Accepted
date: 2026-11-21
deciders: senior-engineer lane + repo owner
---

# 2026-11-21. Rules come back as a visible kind of Automation — all matching rules apply, after payee resolution

Amends [`2026-08-02-replace-rules-and-scheduled-tasks-with-automations`](2026-08-02-replace-rules-and-scheduled-tasks-with-automations.md).
Design and API contract: [`docs/design/transaction-rules.md`](../design/transaction-rules.md).

## Context

The 2026-08-02 ADR folded Rules into Automations and then, in practice, made every Automation a
Lucius run: `_validate_execution` refuses anything but Lucius, and the store refused a
`transaction_transform` row. The deterministic engine stayed in the write path
(`ledger.resolve_transaction_row`, statement import) with nothing able to feed it. The owner wants
"payee is A, amount 10–30 → category X, tag T", and "amount ≈ 100 → split 30 / rest", applied to
every future row and, on demand, to past ones, without a model call per transaction. A Lucius
Automation runs after the write, costs a run, and cannot split reliably. The old engine could not
match on payee (it ran before payee resolution), could not split, and stopped at the first match.

Constraints: write chokes stay the only writers (CLAUDE.md); precedence on create stays explicit >
rule > payee default > None; a money write is never refused because of a Rule; a feature this large
ships behind a flag with the old path untouched.

## Decision

We will bring Rules back as a **visible kind of Automation** — the same row, store and
`apply_automation_change` choke, distinguished by `execution.kind == "transaction_transform"` —
behind the `transaction_rules` flag (default off).

- **Same row, no second store.** A Rule's trigger is a `txn` event whose `when` uses the Lucius
  trigger's `conditions` (+ `match: all|any`, + the amount ops `is`, `between`, `approx` ±7.5%,
  added once and shared). Its execution carries `set`, `splits`, `position`, `stop`,
  `override_existing`. `instructions` is the rule rendered as a sentence, regenerated on every save.
  The store accepts a Rule row; only the service decides whether one may be created (flag on).
  A Rule is never queued as a Lucius run.
- **Order and semantics change from first-match to all-apply.** Every active Rule runs in
  `position` order (legacy rows by `priority`); each sees the row the previous one left; a later
  Rule overwrites a field an earlier one set in the same pass; `stop` ends the pass. A stored row
  with no `stop` is a legacy first-match transform and still stops; a new Rule stores `stop: false`.
- **The pass moves after payee resolution** and before the payee default, so "payee is" matches and
  the chain stays explicit > rule > payee default > None. It runs on create only; an edit never
  re-runs Rules. A split is validated by the ledger's own `_apply_splits`; a split that does not add
  up is skipped for that row and logged, never raised. Statement routing stays at the draft phase;
  the statement preview mirrors the write.
- **Applying to history** (`rule_apply`) writes only through `ledger.update_transaction`, composed in
  one `locked_unit_of_work`; the caller (the router, or the Lucius turn) takes the one checkpoint,
  so Undo reverts the batch. `apply_rule` runs just the given Rule's actions; `run_rules` runs them
  all. A category already on a row is replaced only by a Rule with `override_existing`; an
  already-split row is never re-split; a Starting Balance is never touched.
- **Lucius** can create and edit a Rule through `create_automation` / `update_automations` and
  backfill with `apply_rule_to_transactions`; tool descriptions steer fixed labelling to Rules.
- With the flag off nothing changes: the old pass runs before payee resolution, creation is refused.

## Consequences

### Positive
Deterministic, free, instant labelling and splitting; one definition of a condition for the Lucius
trigger and Rules; history can be backfilled and undone.

### Negative
Two semantics coexist while the flag lives (first-match legacy vs all-apply). A split line's tags
become transaction tags, because the ledger stores tags per transaction, not per split line.

A Rule names categories by id in its actions (its sentence shows the label), so category delete
and merge treat it as an Automation naming the category. They do this through
`transaction_events.rewrite_rule_category_refs`, which covers `set.category` and every split line:
- A merge, or a delete with a reassign target, re-points the Rule.
- A delete without a target is refused, like any Automation naming the category.
- With `reassign` and no target, the Rule is paused, never deleted. `set.category` is cleared,
  and a split line keeps the dead id until edited: an empty line would fail validation on every
  later save of the Rule, a reorder included.

As a last line of defence the pass skips a category or payee that no longer exists, reported as
"category no longer exists". A Rule can therefore never make the ledger refuse a transaction.

### Amendment (2026-11-21): the audit trail names the Rules
Before this, only a Rule that set the CATEGORY was credited (`automation_applied` on the create);
tags, notes, payee and splits a Rule wrote, a Rule skipped, and a payee-default category were all
silent, and a history Apply/Run was credited only to the person. An audit event now carries a
sparse `provenance`: `rules` (`[{automation_id, automation_name, fields}]`, each Rule that
changed the row), `rules_skipped` (`[{automation_id, automation_name, reason}]`) and
`payee_default` (`{payee_id, category}`). A create sets it from `resolve_transaction_row`; Apply
/ Run on history sets `rules` through `core.audit_context.rules_applied` around each
`update_transaction`. The payload is JSON, so no migration; `automation_applied` keeps its shape.

### Follow-ups
Learn rules from categorizing; stages; date conditions; running Rules on edit. Remove the flag and
the legacy pass at `remove_by`.

## Alternatives considered

- A separate Rules store and service — rejected: the owner chose one visible Automation row, and a
  second store means a second choke and a second migration.
- Keep first-match and `priority` — rejected: "tag and categorize and split" needs several Rules to
  apply, as in Actual Budget's engine.
- Run the pass where it was (before payee resolution) — rejected: "payee is" could never match.
- Re-run Rules on every edit — rejected: it would silently undo the owner's manual corrections.

## Agent provenance

- **Decided by:** senior-engineer lane, design approved by the owner on 2026-11-21.
- **Lane / branch:** `feat/transaction-rules` worktree.
- **Alternatives weighed:** separate store vs same row; first-match vs all-apply; pass before vs after payee resolution.
- **Driving artifacts:** `docs/design/transaction-rules.md`.
- **Why this way (one line):** the engine, store and choke already existed — the smallest change that gives fixed rules payee and split power is to re-open creation, reorder the pass and add the apply service.
