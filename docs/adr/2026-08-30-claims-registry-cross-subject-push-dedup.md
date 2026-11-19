# `claims` — a cross-subject push-dedup registry (owner override of the RFC's "NO-GO")

_Status: Accepted · 2026-08-30_
_Related: `docs/rfc/intel-signals-data-model.md` §4 (the RFC this ADR overrides), ADR
`2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation` ("Problem A" — the same-tick
pairing this registry extends across ticks), ADR
`2026-08-30-intel-signals-splits-into-stories-timeline-entries-digests` (the `intel_stories`
schema/identity-fix this table builds on)_

## Context

`docs/rfc/intel-signals-data-model.md` §4 recommended **NO-GO** on a `claims`/`group_key`
cross-subject dedup table, for three stated reasons: (1) ADR
`2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation` already shipped a narrower
version of this (`_find_linked_pairs`, pairing a topic row with its linked ticker's asset row
**only when both are pending in the same sweep tick**) and its own Correction round explicitly
warned that a looser scope "would reintroduce exactly the kind of unbounded, hard-to-reason-
about cross-subject write the prior ADR's Correction round was written to guard against"; (2)
some of the observed "10+ tickers with 3-14 rows" fragmentation is legitimate — multiple price
alerts on one ticker are not duplicates of anything; (3) this is a single-user, self-hosted app
where the sweep tick is already the natural serialization point, so the multi-writer-
coordination payoff a `claims` table usually buys doesn't apply here.

**The owner reviewed the RFC's summary and overrode the recommendation**, backed by a direct,
evidenced incident from the live production database: a ticker's topic-side signal ("<Company>
M&A") and its own asset-side signal each independently pushed an external alert about the exact
same real corporate development (a formal tender offer), **~10 hours apart, on the same
night** — two pushes for one fact. `_find_linked_pairs`'s same-tick-only scope cannot catch this
by construction: the topic row and the asset row were processed on two DIFFERENT sweep ticks,
so they were never both "pending in the same tick" at once.

The RFC's caution is legitimate and shapes this decision's scope, not just its existence: a
price-alert-type story and an M&A-topic-type story sharing a ticker are genuinely different
concerns (a price threshold crossing vs. a corporate development) and must never be conflated
just because they share a subject.

## Decision

We will build a `claims` table (`backend/db/tables.py`, migration `202608301800_claims_registry`)
and a service layer (`backend/services/claims.py`) that lets two independently-processed
`intel_stories` rows recognize when they are about the SAME real-world fact, and suppress a
second story's push as a duplicate of one already communicated.

**Schema** (`ClaimsStore`, `backend/db/claims_store.py`): `id`, `group_key`, `statement`,
`status` (`active`/`superseded`/`flagged` — reusing `signal_timeline_entries`'
`status`/`superseded`/`confirmed_at` vocabulary rather than inventing a parallel one),
`duplicate_of_claim_id`, `contradicts_claim_id`, `sources[]` (server-built
`analyst_prompt.source_dicts`, never model-chosen — the same grounding stance ADR
`2026-08-28-sourced-and-corroborated-intel-signal-updates` established), `story_ids[]` (real
`intel_stories` ids, never ticker strings), `materiality`, `decision_relevant`, `created_at`,
`confirmed_at`, `pushed_at`. No FK to `intel_stories` — `story_ids` is an opaque id list, the
same loose-coupling convention `instrument_id` already uses on that table; a claim outlives any
one story.

**`group_key_for(story_type, instrument_ids)`** (`backend.services.claims`) is the identity
function. It is deliberately conservative in two independent dimensions:

1. **Type gate.** Only `asset`/`topic`/`risk`/`opportunity`/`macro` (the news/development-
   tracking types the analyst/news pipeline feeds) ever resolve a group_key.
   `price_alert`/`daily_digest`/`monitoring` NEVER do, unconditionally — this is what makes the
   RFC's own scoping caution hold structurally: a price-alert row and an M&A-topic row sharing
   an instrument can never collapse into the same claim, because the price-alert side's own
   `group_key_for` call returns `None` before instrument identity is even considered.
2. **Exactly-one-instrument gate.** The caller resolves whatever instrument id(s) a specific row
   is "about" (an asset row's own `instrument_id`; a topic row's linked instrument, resolved via
   the SAME `analyst._linked_instrument_ids` lookup `_find_linked_pairs`'s same-tick pairing
   already uses — reused, not reinvented, so same-tick and cross-tick dedup can never disagree
   about what a topic row is "about"; a risk/macro row's `affected_instrument_ids`) and passes
   the list in. Zero ids → ungrouped. More than one distinct id (a topic linked to several
   tickers, a risk finding spanning a whole sector/region/currency) → also ungrouped, never
   guessed — the same conservative posture `_linked_instrument_ids` itself already takes on an
   ambiguous ticker match ("skipped — never guessed").

**The push-decision hookup** is a single new gate appended to `analyst._push_decision`'s
existing chain (SIXTH condition, checked after the existing grounded gate and before the
staleness gate): when the caller supplies a `group_key` and
`services.claims.is_recent_duplicate_push(group_key)` is true — an active claim for this key was
pushed within `_CLAIM_PUSH_FRESHNESS_HOURS` (24h) — the push is suppressed with reason
`"duplicate_claim"`, logged the same way every other suppression reason already is. `group_key`
is resolved once per call site (`_process_row`'s new `_claim_group_key_for_row` helper;
`_process_linked_pair` reuses its already-resolved `asset_iid`, since a linked pair is by
construction about the same instrument) and threaded through as a new keyword-only parameter —
every existing 4-positional-argument call site is unaffected. When a push DOES fire,
`services.claims.apply_claim_push` records or refreshes the claim (creates it on the first push
for a group_key; on a repeat, refreshes `statement`/`sources`/`pushed_at` and appends the new
story's id to `story_ids`) in the SAME `ClaimsStore.record_push` call that already atomically
checked for an existing row — mirroring `IntelStoriesStore.upsert`'s own
read-then-decide-under-lock shape, needing no `locked_unit_of_work` composition since it only
ever touches the one `claims` table.

**Freshness window: 24h, not `_STALE_PUSH_HOURS` (6h).** `_STALE_PUSH_HOURS` answers a different
question — "is this push now too late to be worth sending at all." Reusing it here would fail to
catch the proven incident itself (the topic/asset pushes were ~10h apart, past a 6h window).
24h instead mirrors this pipeline's existing "how long is a fact still fresh" convention —
`services.evidence._DEFAULT_TTL_HOURS` and `monitoring.price_monitor._NEWS_CONTEXT_MAX_AGE_HOURS`
both already treat a day as that horizon.

## Consequences

### Positive
- The proven incident (two independently-processed signals pushing about the same real fact,
  hours apart, across sweep ticks) is now closed — regression test:
  `tests/test_claims_service.py::test_second_signals_push_is_suppressed_as_duplicate_of_an_already_pushed_claim`.
- The RFC's own conflation concern is closed structurally, not by convention — a price alert can
  never resolve a group_key, so it can never be suppressed (or used to suppress) anything on the
  news side, proven by
  `tests/test_claims_service.py::test_a_price_alert_type_row_is_never_suppressed_by_an_unrelated_claim`.
- `analyst.py`'s diff is genuinely minimal: one new keyword-only parameter, one new gate line,
  one new helper function, and the write call sits right beside the existing `_push_analysis`
  call at both call sites — no rewrite of the existing gate chain or the linked-pair mechanics.

### Negative
- **A known, accepted simplification**: the gate is group_key + time only, with no secondary
  content-overlap check between the new push's own text and the existing claim's `statement`.
  Two GENUINELY DIFFERENT news developments about the same company arriving within the 24h
  window (e.g., an earnings beat, then separately a CEO resignation) would also collapse under
  this mechanism as currently built. Judged acceptable for the same reason the RFC itself
  reasoned about scope: the proven incident is the SAME-fact case, and a story's own
  `duplicate_of_id`/materiality machinery (`_update_signal`) already suppresses a re-mention of
  the SAME fact within one story's own timeline before it ever reaches this gate — so two
  DIFFERENT stories BOTH independently reaching "materially new, grounded, decision-relevant" at
  roughly the same time is, in practice, overwhelmingly likely to be reacting to the same
  underlying arrival, not a coincidence. If real evidence of the false-collapse case surfaces,
  the natural follow-up is reusing `analyst._texts_overlap`/`core.news`'s content-overlap
  primitives as a second check — not rebuilding the mechanism.
- `duplicate_of_claim_id`/`contradicts_claim_id` are real schema columns but are NOT populated by
  anything in this change — no cross-claim contradiction detection was built. They exist so a
  future consumer has somewhere to write that relationship without a schema migration, mirroring
  how `intel_stories.affected_holdings` shipped ahead of every writer that would eventually
  populate it.
- `status` is always `"active"` from this write path — `"superseded"`/`"flagged"` are schema-only
  today, same reasoning as above.

### Follow-ups
- If the false-collapse risk (above) is ever observed for real, add a content-overlap check
  reusing `core.news`'s primitives, gated the same conservative way `group_key_for` already is.
- A future "why was this suppressed" surface (Intel tab UI, an AI tool) could read `claims` to
  show which story a suppressed push was a duplicate of — not built here; this change is the
  registry + the one read-only hookup only.

## Alternatives considered

- **Do nothing, per the RFC's NO-GO** — rejected by direct owner instruction, backed by the
  concrete production incident the RFC's own audit (grouped by raw ticker string, pre-identity-
  fix) didn't surface clearly enough to change the recommendation.
- **Widen `_find_linked_pairs`'s same-tick pairing to N-recent-ticks** (the RFC's own suggested
  smaller fix if the no-go were ever revisited) — rejected: it only helps the topic/asset LINKED
  case, and still requires both rows to independently exist and be re-paired; it does nothing for
  an `asset`+`risk` or `topic`+`macro` cross-subject duplicate, and it would still fire a full
  second LLM synthesis call before finding out the two rows were the same fact. A registry keyed
  on resolved instrument identity catches every combination of the five groupable types with one
  mechanism and one extra DB read, no second synthesis call.
- **A content-overlap-only check (no group_key/time), reusing `_linked_pair_evidence_relevant`'s
  shape directly at push time** — rejected as the PRIMARY mechanism: it would require holding
  onto and re-comparing raw evidence text across ticks (this pipeline's evidence reads are
  bounded/watermarked per subject, not a durable cross-subject corpus), a materially bigger
  change than a small keyed table. Left as a documented follow-up (see Consequences) rather than
  built now.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), on direct owner override of the RFC's own
  recommendation.
- **Lane / branch:** `feat/intel-data-model`, worktree `onw-intel-migration` — scoped strictly to
  new `claims`-related files plus the minimal `analyst._push_decision` hookup; the concurrent
  `intel_stories`/`signal_timeline_entries`/`daily_digests` schema-split work in the same
  worktree was explicitly out of this lane's scope.
- **Alternatives weighed:** do nothing (RFC's own NO-GO); widen the existing same-tick pairing;
  a content-overlap-only check with no keyed registry — see "Alternatives considered" above.
- **Driving artifacts:** `docs/rfc/intel-signals-data-model.md` §4 (the recommendation being
  overridden); ADR `2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation` (the
  same-tick mechanism this extends); the owner-reported production incident (a topic-side and
  asset-side signal both pushing the same tender-offer news ~10h apart).
- **Why this way (one line):** a small, type-and-instrument-gated keyed table closes the proven
  cross-tick duplicate-push incident with one new DB table and a two-line addition to the
  existing push-decision gate chain, while making the RFC's own conflation concern structurally
  impossible rather than merely discouraged.

## Amendment (2026-11-12): the delivery decider judges repeats

`news-fact:<instrument_id>` identifies a COMPANY, not a fact, so for 24 hours after one push every
later push about the same company was suppressed as `duplicate_claim`, whatever it said. Live
incident: an after-hours "board may oust the CEO" alert took the key, and the analysis that read
the confirmed resignation the next session, during a −13% crash, was muted.

With `intel_delivery_decider` on, `analyst._push_decision` skips this gate
(`analyst._decider_judges_repeats`, reading `delivery.delivery_active`, the flag's one seam). The
decider is shown what the person was told in the last 48 hours and is asked to treat several items
about one event as one, so "same fact or new development" becomes its judgement, per ADR
`2026-11-02-intel-decides-news-delivery-code-keeps-safety-rails`. The claim is still WRITTEN on
every push, and with the flag off (or unreadable) the gate holds exactly as before. Alternative
rejected: a content-overlap check on the claim's statement. That is still a guess at sameness,
and the decider already has the told items in front of it.

Driving artifact: `tests/test_crash_alert_news.py`.
