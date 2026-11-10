---
number: 0010
title: Categorization learning keys on the payee entity, not the description-derived key
status: Accepted
date: 2026-06-20
deciders: Repo owner (interactive) + Claude (systematic-debugging investigation → ADR)
---

# 0010. Categorization learning keys on the payee entity, not the description-derived key

## Context

The app carries **two independent notions of "payee identity"**, and they disagree:

1. The payee **entity** — `payee_registry.resolve_payee_id` (over `payees._normalize_payee`) maps a description to a real `payee_id` that owns aliases, a `contains/is/regex` **rules** list, and a pinned `default_category`. Resolution is **rules-first** (`_find_by_rules`) then alias, so any description containing a payee's rule substring collapses to one id — e.g. the rule `contains "anthropic"` folds `"Anthropic Claude API"`, `"Anthropic PBC invoice 0792"`, `"Anthropic San Francisco USA"` all to the same id. (The aliases alone do **not** unify them — `_normalize_payee` produces a distinct key per variant; the *rule* is the unifier.)
2. The learning categorizer's **`_payee_key`** (`services/corrections._payee_key`) — a separate, dumber normalizer: lowercase, strip non-`[a-z\s]`, collapse whitespace. **No aliases, no entity link.** It is documented in-code as "the single seam P2-SP4's payee-merge will later replace."

`services/categorize.suggest_categories` / `_payee_history` build their evidence (user corrections + already-categorized rows) keyed **entirely on `_payee_key`** and never read `payee_id` or `default_category` (a grep across `categorize.py`, `corrections.py`, `ai/tools/categorize.py` finds zero references to either). So the one durable, entity-level instruction a user can give — a pinned `default_category` — is invisible to the learner.

The field consequence (live payee `Anthropic`, `default_category=startup`, `autocategorize=true`, rule `contains "anthropic"`): the **one** real payee fragments into **11 `_payee_key` buckets** (`anthropic`=22 startup, `anthropic pbc`=18 startup, `anthropic san francisco usa`=2 startup/2 subscriptions, `anthropic api subscription`=1 subscriptions, `anthropic claude api`=1, …). When a charge arrives with a description that normalizes to a never-seen key, the importing agent has no learned history and free-guesses — and for SaaS/API-sounding text it guesses `subscriptions`.

The pinned default does *not* save it. The fill-only payee default in `create_transaction` (`payee_registry.default_category_for`, applied only when the category is blank) **does** run for AI imports — `ai/tools/_shared._txn_data_from_ai` deliberately blanks unnamed/kind-name categories so it can, and `tests/test_payee_ledger.py::test_bulk_import_fills_category_from_payee_default` pins that. But a **non-blank** LLM guess (`subscriptions`) is treated as *explicit* and outranks the fill-only default. So the durable instruction loses to a per-row guess.

Corrections don't rescue it either: a correction is stored under `_payee_key`, so it covers only that one description string. The two live Anthropic corrections even disagree (`anthropic` → `subscriptions` on 2026-06-10; `anthropic claude api` → `startup` on 2026-06-19) — whack-a-mole, one variant at a time, never generalizing. The 2026-06-10 correction is provably **inert**: it lands in the `anthropic` bucket that already holds 22 `startup` rows (drowned 22:1) and is absent from every failing-row bucket.

The kicker: the correct entity-level unifier **already exists and is already stamped on every one of these rows** (`payee_id=pay_8b8b40a70000`). The learner just doesn't use it.

## Decision

We will make **payee-entity identity — not the description-derived `_payee_key` — the grouping key for categorization learning**, and let an entity's pinned `default_category` participate authoritatively.

1. **Group learning by `payee_id`.** `categorize._payee_history` / `suggest_categories` aggregate evidence per resolved `payee_id` when a row has one, falling back to `_payee_key` only for rows with no resolved entity. One correction on any description variant then generalizes to every variant of that payee.
2. **Record `payee_id` on corrections.** `corrections.record_correction` stores the resolved `payee_id` alongside `payee_key`, so historical and future corrections aggregate per entity (with a one-time backfill resolving each existing `payee_sample` → `payee_id`).
3. **The pinned default participates and beats an AI guess.** When a payee has `autocategorize=true` and a `default_category`, that default is a first-class, high-weight signal in `suggest_categories`, and at import it **beats an AI importer's free-guessed category** for that payee — but **not** a human's explicitly-typed category. A pinned default is a stronger signal than an LLM guess; it is not stronger than a deliberate human choice.

## Consequences

- **One correction (or the existing pinned default) fixes every description variant** of a payee; the strong "Anthropic → startup" signal (40+ rows) stops being shattered across 11 buckets, and pinning a `default_category` becomes the do-it-once control it was meant to be — no more whack-a-mole.
- **The entity data the app already computes finally drives categorization** instead of a parallel, weaker description key that re-implements payee identity badly.
- **Precedence is the risk center.** `explicit > rule > payee-default` must still hold for a **human's** explicit category. The decision hinges on distinguishing a human's deliberate category from an AI importer's guess — both currently arrive as a non-blank `category` at `create_transaction`. The fix must carry that distinction (the AI import helper defers its guess to a pinned default; a human UI edit still wins). Getting it wrong would let a stale default override a deliberate human choice — the inverse failure.
- **`_payee_key` is the documented SP4 payee-merge seam.** Coupling the learner to `payee_id` partially pre-empts that work and must stay consistent with it; the fallback-to-`_payee_key` path keeps entity-less rows working during the transition.
- **Migration / follow-ups:** implement the three changes in `services/categorize.py` + `services/corrections.py` (+ the AI import helper for #3); backfill `payee_id` onto existing corrections (or dual-read during transition); add regression tests (a correction on `"Anthropic Claude API"` changes the suggestion for `"Anthropic PBC"`; a pinned default beats an AI guess but loses to a human edit); flip the `docs/archive/run-logs/known_issues.md` entry to FIXED on ship.

## Alternatives considered

- **Data-only cleanup** (delete the stale correction, re-categorize the rows) — rejected: treats the symptom; the next new description variant re-triggers the guess and the user keeps correcting forever.
- **Extend `_payee_key` aliasing to cover more variants** — rejected: re-implements the entity resolver, worse; `resolve_payee_id` (rule-first) already unifies variants correctly — reuse it, don't duplicate a weaker copy.
- **Make a pinned `default_category` override ALL categories, including human edits** — rejected: breaks the `explicit (human) > payee-default` invariant; a user must always be able to override a default on a specific row.
- **Have the importer always leave the category blank and rely solely on the fill-only default + rules** — rejected: throws away the LLM's genuine signal for payees with *no* pinned default; the targeted rule (default beats guess only when a default is pinned) keeps both.

## Agent provenance

- **Decided by:** Repo owner (interactive) + Claude — systematic-debugging investigation that traced the miscategorization to the `payee_key`/`payee_id` split.
- **Lane / branch:** `feature/intel-redesign` (debugging session, no worktree).
- **Alternatives weighed:** data-only cleanup; widen `_payee_key` aliasing; default overrides everything; importer always-blank — see above.
- **Driving artifacts:** the `docs/archive/run-logs/known_issues.md` entry "AI re-imports a known payee under a new description and ignores its pinned default"; adversarial verification of four load-bearing claims against `services/categorize.py`, `services/corrections.py`, `services/payee_registry.py`, `services/ledger.py`, `ai/tools/_shared.py`, and live data for payee `pay_8b8b40a70000`.
- **Why this way (one line):** the app already resolves every description variant to one `payee_id` with a pinned default — categorization learning should consume that entity signal, not a parallel description key that fragments it.

## Update — implemented (shipped 2026-06-20)

All three decisions shipped, test-first (red→green), behind the existing write/suggestion seams:

- **Keystone (#3 import-time).** `ai/tools/_shared._txn_data_from_ai` now blanks an AI-guessed category when the resolved payee has an `autocategorize` pinned default, so the existing fill-only default in `create_transaction` wins. Human edits (via `update_transaction`) never reach this helper, so a deliberate human category still wins. Tests: `tests/test_ai_import_default_beats_guess.py`.
- **Learning keyed on `payee_id` (#1).** `categorize._payee_history` / `suggest_categories` bucket evidence by a namespaced `_evidence_bucket` (`id:<payee_id>` when known, else `key:<_payee_key>`), so every description variant of a payee shares a bucket. Tests: `tests/test_categorize_payee_entity.py`.
- **Record `payee_id` (#2) + a one-time forward migration.** `corrections.record_correction` + `CorrectionStore.record` persist `payee_id` going forward. The pre-existing corrections were upgraded by a **one-time backfill** (resolving each `payee_sample` via the rules-first `resolve_payee_id`), NOT a perpetual read-time shim — so the read path is forward-only (`suggest_categories` just reads `payee_id`). No backward-compat code in the app; `_payee_key` survives only as a structural fallback for a correction/row that genuinely resolves to no entity.
- **Pinned default participates in suggestions (#3 suggestion-side).** A payee's pinned default seeds its bucket ONLY when it has no learned evidence (a fallback at weight 2 — surfaces on its own, still out-voted by ~3+ recent corrections), via `_live_payee_defaults` on the live path / an injected `payee_defaults` on the pure path.

**Intentionally NOT changed:** `corrections.suggestions()` (the rule-PROPOSAL path) stays keyed on `payee_key` — it emits a `description_contains` rule, so a description-derived key is the correct grouping there; an entity id can't seed a description rule.


## Amendment 2026-11-09 — a user's standing rule may override the pinned default, and the swap is reported

The keystone treated every model-supplied category as a guess. A standing instruction from the owner (e.g. a meal-by-time rule in Instructions) arrives through the same path, so a payee pinned to Lunch silently turned a 21:36 Dinner into Lunch — and neither the model nor the notification could tell.

- `add_transactions` items accept `override_payee_default` (default false). When true, `_txn_data_from_ai` keeps the model's category; unset, the keystone is unchanged. The description reserves it for a category a standing user instruction decides — never a guess.
- When the keystone does replace a model-supplied category, the result carries a `category_replaced_by_payee_default` warning (single-row and multi-row paths) naming both categories and the two ways to correct it.
- The capture prompt tells the model to use the flag for such rules and to fix a reported replacement before replying.
- Verified live through `POST /api/capture` on a scratch instance with two models: both set the flag on the first call for a time-ruled restaurant payee and neither set it for a plain default.

Rejected: making the flag implicit whenever Instructions exist (the ledger cannot read prose rules); a deterministic meal-by-time setting (a larger feature, not needed to close this gap).
