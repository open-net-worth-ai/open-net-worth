## What does this change do, and why?

<!-- The "why" matters more than the "what" here — link an issue if there is one. -->

## Which area does this touch?

- [ ] Ledger / net worth (core, hardening-locked — see `docs/launch/BOARD.md`)
- [ ] Budgeting (envelopes / Ready-to-Assign)
- [ ] Investments
- [ ] A deferred area (intel, tax, equity comp, FIRE, bank connectors) — marked "coming soon"
- [ ] AI / Lucius tool surface
- [ ] Infrastructure, docs, CI, or something else not in the list above

## Checklist

- [ ] I read [`CONTRIBUTING.md`](../CONTRIBUTING.md) and, for anything non-trivial,
      [`CLAUDE.md`](../CLAUDE.md).
- [ ] `make check` passes locally (lint → type → test → build).
- [ ] If this changes a memory prompt, the routing guide, the memory write choke or the model a
      memory pass runs on, I ran `make memory-eval REPEATS=3` (live, opt-in) and the gate passed —
      see [`docs/ai/MEMORY_EVAL.md`](../docs/ai/MEMORY_EVAL.md).
- [ ] New/changed behavior has a test (a bug fix includes a regression test that fails without
      the fix).
- [ ] I did **not** commit anything from my own financial data — no real account numbers,
      balances, statement content, screenshots, or personal profile fields. Reproductions use
      `make seed`'s example data.
- [ ] Docs are updated in the same PR if user-facing behavior changed (`docs/product/` and/or
      `CLAUDE.md`) — see `CONTRIBUTING.md`'s Definition of Done.
- [ ] If this touches money movement, auth, secrets, or external I/O, I've noted that below so a
      `/security-review`-style pass can happen before merge.
- [ ] If this touches `docs/product/`, I understand it's not docs-only: that corpus is fed to
      the AI assistant (Lucius calls `search_docs`/`read_doc` and is told to treat it as the
      source of truth) — it gets a code-level review, same as the areas above.
- [ ] If this is a UI change, it clears the [UI Quality Gate](../docs/UI_QUALITY_GATE.md)
      (acceptance file, `make ui-gate` clean, screenshots of empty/loading/error states).

## How was this tested?

<!-- Commands you ran, and what you verified — "make check is green" isn't enough on its own for
     anything that changes behavior a user would notice. -->

## Security-sensitive?

<!-- Money movement, auth, secrets, or external I/O (network fetch, email, MCP, file upload, AI
     tool surface)? Say what changed and why it's safe. Leave "N/A" if this genuinely doesn't
     touch any of those. -->
