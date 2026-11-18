---
title: The emitter is the unit of identity, not the namespace
status: Accepted
date: 2026-09-02
deciders: orchestrating agent (Claude) + repo owner
---

# The emitter is the unit of identity, not the namespace

## Context

Invariant 1 (F03) says two rows carrying distinct authoritative refs under the same
`external_source` are two real events and must never be collapsed. Its premise is that **one
namespace is one issuer's event feed**.

That premise is false for `email`. An RFC-822 Message-ID is authoritative for the *email*, not
for the *payment*, and the `email` namespace carries every sender the book has ever received. One
charge announced by the bank *and* by the merchant yields two distinct ids for one event — and
F03 reads that as proof of distinctness, the exact opposite of the truth.

Two live bugs, one cause:

- **2026-08-26** — one S$180.00 Meridian→Union card payment booked twice; the paying bank emailed on send (24 Aug),
  the card issuer on receipt (26 Aug).
- **2026-08-28** — one S$24.40 Ridgeline Mobile charge booked twice, two automation runs apart; Harborview Bank's card
  alert (`aaaa1111cccc`, 28 Aug) and Ridgeline Mobile's merchant receipt (`dddd2222eeee`, 29 Aug). The run's
  own transcript shows the pre-flight reporting the row unmatched (`blocked_but_unmatched:[6,10]`),
  so Lucius booked it in good faith.

Both times the engine did not score the pair low — it never scored it at all. `_classify`
returned `None` at the veto before any layer ran.

ADR `2026-08-27-a-transfer-has-two-counterparties-and-both-notify` addressed the first by
exempting **two two-leg transfers on the same route**. That exemption is shape-scoped: it cannot
reach an expense, so the Ridgeline Mobile bug landed a day later. It is also *too broad within its shape* —
measured on the live book it left five pairs auto-merge-eligible (two at score 1.00), including
two genuinely distinct same-day wallet payments on one route, because it exempted any two
same-route transfers regardless of who announced them.

The measured shape of the hazard on the live book (341 email-ref'd rows, 57,970 pairs), grouped
by how the two rows' emitters relate:

| Emitter relation | Auto-merge eligible (≥0.80) | Review |
|---|---|---|
| Same emitter | 16 | 42 |
| Different emitters | 0 | 3 |
| Unresolvable | 2 | 6 |

Every one of the 16 same-emitter high scorers is a real, distinct transaction (two Grab rides on
one day, consecutive Wise transfers, daily lunches). Of the 3 cross-emitter pairs, exactly one —
Ridgeline Mobile — is a true duplicate. So *same emitter, distinct ids ⇒ two events* held 66 times out of 66;
the veto is only wrong when the emitters differ or cannot be established.

The stakes are asymmetric and the current config sharpens them: `dedup_policy = "auto_high"` with
`l3_enabled` means a NEAR_DUPLICATE at ≥`SCORE_STRONG` that the LLM calls "same" **auto-merges
with no human**. A surviving duplicate is visible (two rows, a wrong total). A real row silently
absorbed is not.

## Decision

We will make the **emitter** — who announced the event — the unit invariant 1 tests, not the
namespace.

`matching._same_source_event_veto` vetoes a pair when any *colliding* authoritative source has
emitters that are **equal or unresolved**. Different, established emitters lift the veto; the
pair is then scored by the ordinary layers, which decide on their own merits.

Unresolved **fails CLOSED**, matching `core.txn_sources.is_authoritative_source`'s own default:
we only ever loosen on a difference we can actually establish.

The emitter is stored on the ref (`external_refs[].emitter`), resolved **server-side** at the
ledger write choke from `email_snapshots.from_addr` — never taken from a caller, because the
model supplies `external_refs` and must not be able to assert who sent an email. Revision
`202609021430_backfill_external_ref_emitter` backfills every pre-existing row from the same join.

The 2026-08-27 same-route transfer exemption is **retired**. Route was a proxy for "two
counterparties"; the emitter is that thing directly, so the transfer case it was written for
still passes — via the emitters, not the route.

`_collision_sources` is extracted so the fact ("distinct refs under a shared namespace") and the
decision ("does that veto") stay separate and separately testable.

## Consequences

### Positive

- Fixes both live bugs, and the class rather than two shapes of it. The Ridgeline Mobile pair now classifies
  NEAR_DUPLICATE at 0.75 — below `SCORE_STRONG`, so it surfaces for review rather than
  auto-merging, which is the right outcome for a genuinely ambiguous pair.
- **Auto-merge hazards on the live book go from 5 to 0.** The one remaining ≥0.80 pair (a travel booking,
  1.00) shares a ref, so there is no collision to scope — L0 identity already says it is one
  event, and merging it is correct.
- No auto-merge floor is needed. An earlier draft added one; the measurement showed 16 of the 18
  hazards are same-emitter (still vetoed by construction) and the other 2 are unresolvable (still
  vetoed by failing closed), so the floor would only have added review friction to the one
  cross-emitter case auto-merge handles correctly.
- One exemption instead of two, and it is namespace-general: `ofx`, `simplefin` and friends get
  the same treatment as soon as their emitters are populated.

### Negative

- **The 85-of-345 unresolved refs keep today's behaviour.** A cross-emitter duplicate hiding among
  refs whose snapshot was never stashed or has since been pruned stays missed. That bucket shrinks
  toward zero for future writes (stamped at the choke) but is not zero today.
- Non-email authoritative namespaces have no emitter resolver yet, so they veto on every distinct
  ref — same as today, but now for an explicit reason rather than by accident. Two banks' OFX
  exports of one movement will veto until `ofx` emitters are populated. The 2026-08-27 exemption
  covered that case; this does not, until the follow-up lands.
- Adds a per-write lookup against `email_snapshots` at both ledger write chokes. Best-effort and
  exception-guarded, but it is a new dependency of the write path on an ingest-side store.
- `66/66` is one book over two months. It is strong evidence that same-emitter vetoing is safe
  *here*, not proof it holds for a bank that batches two charges into one alert email.

### Follow-ups

- Populate emitters for non-email authoritative namespaces (`ofx` → the account/institution the
  file was imported for; `simplefin`/`plaid` → the connection id). Until then those namespaces
  fail closed.
- Backfill or re-stash the 85 email refs with no snapshot, to shrink the unresolved bucket.
- **Transaction time, not just date.** All three score-1.00 hazards are same-day pairs; date has
  no discriminating power there and time separates them decisively. The source data already
  carries it (the Harborview Bank alert behind the Ridgeline Mobile row reads `Transaction Time 12:47:41`) and ingest
  discards it. Needs a schema change, a timezone model tied to the account, and per-source
  extraction — its own piece of work. Positive signal only: close times evidence sameness, but
  distant times are not proof of distinctness (auth vs capture can differ by hours).
- The two live cross-emitter duplicates on the book (Ridgeline Mobile; and the pre-existing travel-booking / remittance
  shared-ref collisions) are the owner's to merge.

## Alternatives considered

- **Drop `email` from `AUTHORITATIVE_ID_NAMESPACES`** — rejected twice, and the measurement says
  why: it removes the veto for all 58 same-emitter pairs, 16 of which are ≥0.80 and every one of
  which is a real, distinct transaction. Under `auto_high` + L3 that is a silent merge of two real
  charges, which is worse than the duplicate being fixed.
- **Keep the veto but never auto-merge a pair that only escaped it** (an auto-merge floor) —
  rejected on evidence. Safe, but the numbers show the hazard is entirely same-emitter or
  unresolvable, both of which still veto; the floor would add a flag threading through `Candidate`
  and `combine()`, a new invariant to document, and review friction on the one case (a
  cross-emitter transfer at 0.90) where auto-merge is right.
- **Remove the veto entirely and let every layer run** (the owner's stronger proposal — a distinct
  id cannot *prove* distinctness) — epistemically cleaner, empirically expensive: 69 review items
  to catch one real duplicate, 68 of them same-emitter false positives, plus 18 real rows exposed
  to auto-merge. The insight is preserved exactly where it bites — the cross-emitter and
  unresolvable cases — and declined where the premise actually holds.
- **Resolve the emitter at match time instead of storing it** — rejected on cost: the engine
  compares ~58k pairs on this book, and a per-pair store lookup (or threading a preloaded map
  through `check`, `merge_transactions` and `match_review`) is far worse than one lookup per write.
- **Keep the route exemption alongside emitter-scoping** — rejected: it exempts same-route
  transfers regardless of emitter, which is what left two genuine same-day wallet payments
  auto-merge-eligible. It earns nothing that emitters do not, for email.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (owner drove the reframing: "the same
  source should never veto — it can say *matched*, but it can't say *not matched*")
- **Lane / branch:** `main`, single lane
- **Alternatives weighed:** drop `email` from the allowlist; auto-merge floor; remove the veto
  outright; resolve emitters at match time; keep the route exemption
- **Driving artifacts:** ADR `2026-08-27-a-transfer-has-two-counterparties-and-both-notify`
  (superseded); live automation runs `run_1111aaaa2222` / `run_3333bbbb4444`;
  `tests/test_transfer_notified_by_both_banks.py`
- **Why this way (one line):** a namespace is not an issuer — `email` has hundreds of senders — so
  the veto must test the emitter, and fail closed when it cannot establish one.

## Amendment 2026-11-16 — a content-hash ref is not an id

**Context.** A bank connector files a row the bank gave no id under the connector's own
authoritative namespace, keyed by a hash of its content (`providers.derived_ref`: account, date,
amount, currency, frozen wording → `derived:<16 hex>`, with a `#2`, `#3` suffix for repeats in one
read). `is_authoritative_source` promises "native per-event identifiers (not content hashes)", but
`_collision_sources` read every ref under the namespace, so two different hashes counted as two
distinct ids. The same payment hashes to a new key whenever anything it hashes shifts — an Enable
Banking pending row booking on another day, a bank re-wording a line, the 0.1.50 description
change — and the veto then let the second copy be written with no human in the loop: the exact
"false positive → silent double-count" this ADR names as the costly direction.

**Decision.** `_collision_sources` skips a content-hash ref (`core.txn_sources.is_content_hash_ref`,
prefix `CONTENT_HASH_REF_PREFIX`, which `providers.DERIVED_PREFIX` now aliases). A pair whose only
refs under a namespace are hashes is scored by the ordinary layers. A native id beside a hash still
collides as before.

**Consequences.**
- A shifted hash is caught: refused at the write choke for Lucius (with the usual
  `allow_duplicate` / `add_transaction_refs` guidance), and a "possible duplicate" review item in
  the AI-free bank sync, never a second booking.
- Two identical charges in one read still book as two: one sync run is one `batch_id`
  (`_same_feed`), and they carry distinct numbered refs. Two identical charges that arrive in
  SEPARATE reads are now asked about instead of booked silently — accepted: nothing from the bank
  tells them apart, and asking is this ADR's cheap direction.
- Such a pair is never AUTO-merged, whatever the policy: `matching.rests_on_content_hashes` stops
  `match_review`'s judge from merging it even under `auto_high` with an LLM "same" — two identical
  charges from separate syncs may be two real ones, and only the owner can tell.
- Enable Banking: a row with an `entry_reference` is also known by its content hash
  (`with_hash_aliases`), so a row booked before the bank gave it one is not booked again — but only
  when no other row in that account's read shares the hash. For two identical charges the hash
  can't say which booking is which; those fall to the duplicate check, which asks.
- Checked against Actual Budget, Sure, Wealthfolio and Firefly III (2026-11-16): all hash content
  when the bank gives no id; only Actual also fuzzy-matches a shifted row (±7 days, same amount),
  and those without a counter lose the second of two identical charges.

Tests: `tests/test_content_hash_refs_reach_dedup.py`.
