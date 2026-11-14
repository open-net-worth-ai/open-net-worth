---
title: Guides wait while the person is in the middle of something
status: Accepted
date: 2026-11-14
deciders: repo owner + Claude
---

# Guides wait while the person is in the middle of something

## Context

Every guide (a screen introduction, a Lucius tour or tip) starts through one gate,
`intro/tourTarget.ts`, which judged the screen free when no dialog was on it. Anything mid-way
WITHOUT a dialog looked idle. Back from a bank's consent, the app spends a second or two finishing
the link on the bare Connectors list before the connector's page opens; Lucius's "Lucius sees this
screen" tip started in that gap and the page then opened on top of it (owner test, 2026-11-14,
seen twice). Patching that one flow would leave the same gap for every deep link and every flow
still to come.

## Decision

Busy is declared, not guessed from the DOM:

1. **A visit that arrived with a purpose shows no guide.** If the page was LOADED from a deep link
   or a provider's return — the bank callback path, or a `connector`, `txn`, `batch`, `state`,
   `code` or `ref` parameter — the person came to do that one thing. The load address is read
   from the browser's navigation entry, not the current address: the app tidies those
   parameters away before the guide code may even load.
2. **A flow that is mid-way holds guides.** `holdGuides()` when it starts, the returned function
   when it is done; guides wait 1.5 s more, so a dialog that opens as the flow ends comes first.
   The connector screen holds them while it finishes an Enable Banking or GoCardless return.

`guidesHeld()` is checked beside `modalOpen()` by every guide (`FeatureIntro`, `OnceTour`). A new
guide kind checks it too; a new flow that leaves the screen bare while it works holds guides.

## Consequences

- A guide never lands in the middle of a bank link, a deep link to a transaction, or a filtered
  register; it waits, still unseen, for an ordinary visit.
- A visit opened from a link shows no guide at all, even after its task is done. Accepted: the
  next ordinary visit shows it.
