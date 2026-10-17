---
title: Each phone-shortcut trigger is its own source, and captures run one at a time
status: Accepted
date: 2026-10-17
deciders: repo owner
---

# 2026-10-17. Each phone-shortcut trigger is its own source, and captures run one at a time

## Context

Both iPhone shortcuts (the Apple Pay tap and the finance-notification one) sent
`source: ios-shortcut`, with refs `wallet-tap:<time>` and `notif:<time>`. `ios-shortcut` is an
authoritative namespace, so two distinct refs under it read as two real events, and with no
emitter recorded the veto in `matching._same_source_event_veto` fired before the pair was
scored ([2026-09-02](2026-09-02-the-emitter-is-the-unit-of-identity-not-the-namespace.md)).

A card that runs the Apple Pay shortcut AND whose bank app notifies reports every purchase
twice. On 2026-10-15 two such purchases were each booked twice. Replayed against the book,
the duplicate check found NO candidate with the refs attached and the right one without them.

One pair also arrived 7 seconds apart. Capture turns ran in parallel and take 20–50 s, so
neither turn could see the other's row, and the pre-write check in `create_transaction` sits
outside the write lock.

## Decision

- **One namespace per trigger.** The Apple Pay shortcuts send `ios-wallet`; the notification
  shortcut sends `ios-notification`. Both are authoritative, so two real taps (or two alerts
  from one app) stay two events. A tap and a notification are different namespaces, so the
  pair is scored like any other and the duplicate check stops the second booking.
- **A notification ref names its app**: `notif:<time>@<app>`. `core.txn_sources.ref_emitter`
  reads the app as the emitter (casefolded), so the card's bank and the merchant's app
  announcing one purchase are compared too. A ref without an app stays unresolved, the veto's
  safe direction.
- `ios-shortcut` stays authoritative, for the rows it already stamped and for shortcuts not
  yet reinstalled.
- **Capture turns run one at a time** (`ai/capture.py`, a per-loop lock around each attempt), so
  every turn sees everything booked before it. A turn still running after
  `QUEUE_HOLD_SECONDS` (180 s) lets the next one start but is not cancelled — a turn cut off
  mid-write is worse than a rare race.

## Rejected

- **Read the trigger from the ref prefix under one `ios-shortcut` namespace.** It needs no
  reinstall, but adds a per-namespace parsing rule for something a source already expresses.
  With one user, reinstalling once is cheap.
- **Serialize only the write.** The check-then-write window would close, but Lucius's own
  ledger reads earlier in the turn would still miss the parallel booking.

## Consequences

- The shortcuts must be re-signed (`make ios-shortcuts-sign`) and reinstalled. Until then the
  phone keeps sending `ios-shortcut` and the old behaviour stays.
- A tap and a notification with different merchant wording ("7-Eleven" vs
  "7-ELEVEN-EXAMPLEPLACE PLA") score in the review band, not the strong one. `add_transactions`
  still stops and hands the match back to Lucius; it is not refused outright.
- A burst of captures waits in line; past 50 s the phone gets "Still logging this in the
  background" and the outcome as a notification, as before.

## Amendment (2026-10-17, same day)

The rejected prefix rule is adopted for `ios-shortcut` refs ONLY. Reinstalling fixes new
captures but not the rows already booked: a tap/notification pair under `ios-shortcut` still
read as one unresolved emitter, so the merge choke refused it even after the owner confirmed it,
and no caller can reach its override. `core.txn_sources.ref_emitter` now reads `wallet-tap` or
`notif` from a legacy ref's prefix as its emitter; any other prefix stays unresolved. The new
namespaces are unchanged.
