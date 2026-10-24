---
title: Email connector-tag security review — ladder default, pause reconciliation, capability-toggle refusal, stale-permission cleanup
status: Accepted
date: 2026-08-17
deciders: security-reviewer (Claude) + senior-engineer implementation lane (Claude)
---

# Email connector-tag security review — ladder default, pause reconciliation, capability-toggle refusal, stale-permission cleanup

> **Note:** the principle (the ladder shows the level that will fire) stands; the exempt list it mentions is gone — see [2026-10-11-tool-defaults-follow-what-a-tool-does](2026-10-11-tool-defaults-follow-what-a-tool-does.md).

## Context

A security review of commit `81cbd7b8` (`docs/adr/
2026-08-17-read-shaped-exemption-survives-connector-tagging.md` — tagging `send_email`/
`search_messages`/`get_messages` with `connector="email"` so the Email connector's own
permission ladder actually governs them) found the ownership fix real but incomplete: two
blocking gaps and several smaller ones, all against runnable probes rather than theory. All
were fixed in the same pass; this ADR records the four decisions that are genuinely hard to
reverse or non-obvious, mirroring `docs/adr/2026-08-17-connector-remove-security-hardening.md`'s
own shape for a multi-finding review pass.

## Decisions

### 1. A forward-only migration deletes six stale `tool_permissions` rows instead of migrating them

Before commit `81cbd7b8`, `PUT /api/connectors/email/permissions/{tool}` already wrote
decisions keyed `connector_id="email"` for these three tools, but dispatch resolved their
owner as `core` — so an `email`-owned row was written but never consulted, and any
pre-existing `core`-owned row (a shared bucket, not specific to email) was consulted but not
really "about" email. The ownership fix makes BOTH of these rows real for the first time:
an `email`-owned `always_allow` an operator clicked and forgot about (it visibly did nothing
at the time) now fires for real on `send_email` — an irreversible action — with zero new
operator action; a `core`-owned `blocked`, or worse a `core`-owned undecided row under YOLO
(`core:*` → `always_allow`), degrades to permissive with no operator action either.

**Decision:** revision `0043` deletes exactly the six possible keys
(`core:send_email`, `core:search_messages`, `core:get_messages`, `email:send_email`,
`email:search_messages`, `email:get_messages`) rather than attempting to migrate them
forward. Neither key was ever simultaneously WRITTEN-TO and ENFORCED before the fix, so
nothing the operator actually experienced as "this tool now runs / never runs" is being
taken away — re-granting is one click. `downgrade()` is a documented no-op: there is no
prior state worth reconstructing.

**Alternative rejected:** migrating the `email`-owned row forward as the new decision (it
was, after all, something an operator clicked). Rejected because "the operator clicked
something" is not the same claim as "the operator decided this tool should run
unattended" — the click happened under a UI that, at the time, never actually enforced it,
so the operator had no way to observe or correct a mistaken click. Silently activating it
now is exactly the privilege-escalation-with-no-new-consent shape ADR
`2026-08-14-one-permission-store-for-every-tool` and its siblings exist to prevent.

### 2. `services.connectors` gets a third accepted `connectors/` import exception, to reconcile the `core.channels` seam on every email-connector write

`send_email`'s own pause-check (added in `81cbd7b8`) reads `ChannelConfigStore["email"].
enabled` directly — correct. But `notify`'s email leg reaches the SAME SMTP config through
the SEPARATE `core.channels` registry (`register_channel`/`registered_channels`), populated
only by `register_if_active()` — called at boot and by `PUT /api/channels/email`, but NEVER
by `services.connectors.set_connector_enabled`/`set_capability_enabled`, the choke both the
REST `PATCH /api/connectors/email` route and the AI `set_connector_enabled` tool use.
Verified live: pausing the connector via that choke left the seam registered, so
`notify(channels=["email"])` still sent real mail while `send_email` correctly refused.

**Decision:** `services.connectors._reconcile_email_channel()` — register-if-active, else
unregister — runs after every `EMAIL_ID` write in both `set_connector_enabled` and
`set_capability_enabled`, mirroring `api.channels._reconcile_seam`'s own logic exactly. This
requires a THIRD documented exception to this module's "never `api`/`ai`/`mcp`" import rule:
`services.connectors` now also imports `backend.connectors.email_out.channel`. Verified
ai-free by inspection (its only module-level import is `core.channels`; its deferred
imports reach only `backend.db` and the leaf `backend.connectors.msgfmt`), so this cannot
become a `services → ai` edge via the layers contract's transitive check — the same
narrow-safety argument the module's existing second exception
(`connectors.session_map`) already makes for a different reason.

**Alternative rejected (the RFC's own fallback, (b)):** gate `_send()`/`send_email`'s
underlying `_smtp_send` on `is_active` instead of reconciling the seam. Rejected as strictly
worse: it still leaves the seam itself lying about what's registered (`GET
/api/notify-channels` would keep listing `email` as live while paused), and duplicates the
active-check logic a second place instead of keeping ONE authority (`register_if_active`)
for "is this channel really reachable right now."

### 3. `set_connector_enabled`'s `capability=` parameter is refused outright for the three comms connectors, not merely reconciled

Finding #2's fix closes the seam gap, but a SEPARATE, already-model-reachable escape hatch
remained: `set_connector_enabled(connector_id="email", capability="send", enabled=true)`
flips exactly the flag `send_email`'s own pause-check reads AND (after fix #2) the flag the
seam reconciliation reads — so a coerced/injected model (untrusted inbox content reaching an
Automation is the concrete threat model this whole tool surface already assumes) could
re-enable sending on a connector the OPERATOR paused, with no operator action, by making one
ordinary approve-tier tool call that never even needs to name the whole-connector toggle.

**Decision:** refuse a `capability=` toggle outright, in both directions, for `email`,
`webhook` and `telegram` — in the AI tool handler (`ai/tools/connectors.py`), not in the
shared `services.connectors.set_capability_enabled`, which stays reachable for a human
operator through the generic `PATCH /api/connectors/{id}` route (mirrors the existing
`DISABLED_CONNECTORS` refusal's own shape and reasoning for the same "refuse in the AI-tool
layer, not the shared service" split). Costs Lucius nothing it could otherwise legitimately
do: these three connectors' `read`/`send`/`notify`/`interactive` capabilities all collapse
onto the SAME whole-connector `enabled` flag anyway (`set_capability_enabled`'s own
docstring), so only the whole-connector toggle (`capability` omitted) — exactly like every
other connector's Pause/Resume — remains reachable.

**Alternative rejected:** rely on fix #2 alone (the seam would still reconcile correctly
even after a capability-scoped re-enable, so `notify` would work again — but `send_email`'s
own check reads the store flag directly, and would ALSO start firing again with no operator
action, seam or no seam). Fix #2 closes the *registration* gap; it does nothing to stop the
*store flag itself* from being flipped by an ordinary tool call. Both fixes are required and
address different mechanisms.

### 4. The read-shaped exemption (`GATE_EXEMPT_READ_SHAPED`) moves from `ai/` to `services.tool_permissions`, so the permission ladder's DISPLAYED default can match dispatch's ACTUAL default

`ai.tools._confirm_gate.effective_level` resolves an undecided connector-owned tool's
default through two rules in order: the curated read-shaped exemption (`search_messages`/
`get_messages` et al → `always_allow` regardless of owner, ADR
`2026-08-17-read-shaped-exemption-survives-connector-tagging`), then YOLO (the reserved
`core:*` row substitutes `always_allow` for an otherwise-cautious default). Email's own
permission ladder (`services.connectors._decorate`'s `_level` helper, consumed by BOTH the
Connectors detail page's UI and the model-facing `get_connector_permissions` tool) instead
fell back to the raw `tool_permissions.DEFAULT_LEVEL` ("needs_approval") for an undecided
tool — so an undecided `search_messages` showed "Needs approval" on screen while dispatch
ran it with no card at all. Two records answering the same question differently, with no
way for the user to tell which one was real — this app has fixed this exact failure shape
before (the whole reason `tool_grants`/`connector_tool_permissions` were merged into one
store, ADR `2026-08-14-one-permission-store-for-every-tool`).

**Decision:** `GATE_EXEMPT_READ_SHAPED` — the curated frozenset itself, not merely a
re-derivation of it — moves to `backend/services/tool_permissions.py`. `ai._confirm_gate`
now imports it from there (an ordinary top-of-file import; every existing
`policy.GATE_EXEMPT_READ_SHAPED` reference, including in tests, keeps working unchanged
since Python attribute lookup on a module doesn't care whether the name was defined locally
or imported). `services.connectors._decorate`'s `_level` helper now reimplements
`effective_level`'s connector-owned branch directly — an explicit stored decision wins;
failing that, the exemption; failing that, YOLO (read once per `list_connectors()` call, not
once per tool, since that function sits under `enabled_connector_ids()`, consulted on every
AI tool catalog build). It cannot literally CALL `effective_level` — that function lives in
`ai/`, which `services` may not import (the dependency rule) — so this is a deliberate,
tier-legal reimplementation of one branch of it, not a shared call.

**Alternative rejected:** special-case `search_messages`/`get_messages` by name inside
`services.connectors._level` instead of relocating the shared set. Rejected for the same
reason the sibling ADR gave when it faced the identical choice: the exemption is already a
reviewed, curated set, and a second, narrower allowlist next to it would drift from the
first the next time a `GATE_EXEMPT_READ_SHAPED` member gains a `connector=` tag — the
general form (one set, two consumers) is more honest than two allowlists claiming to agree.

**Alternative rejected:** leave the set in `ai/` and have `services.connectors` duplicate
its literal membership as a second frozenset. Rejected outright — this is the exact
two-copies-that-can-drift shape the codebase has already paid for once (`tool_grants` vs.
`connector_tool_permissions`, the SAME failure mode finding #4 itself is another instance
of) and CLAUDE.md's "no backward-compat shims / no second list to drift" convention argues
against on its own.

## Consequences

### Positive
- A pre-existing click on Email's own ladder that never took effect cannot silently start
  firing the moment the ownership bug is fixed, in either direction (allow or block).
- Pausing Email now stops BOTH ways this app can send mail (`send_email` directly, and
  `notify`'s email leg through the seam) — verified by a regression test that asserts SMTP
  is never contacted while paused, mirroring the existing `send_email` version of that test.
- The capability-scoped bypass of a whole-connector Pause is closed for all three comms
  connectors, not just email.
- The Connectors detail page and the model-facing `get_connector_permissions` tool now show
  the SAME level dispatch will actually use, for every undecided connector-owned tool — not
  only the two tools this specific incident happened to involve.

### Negative
- `services/connectors.py` now carries a third documented import exception past its stated
  "never api/ai/mcp" rule — one more thing a future reader of that module's docstring has to
  hold in mind, though each exception is independently narrow and independently justified.
- `_level`'s reimplementation of `effective_level`'s connector-owned branch is a SECOND
  place that logic exists (the first being `effective_level` itself) — accepted because the
  two cannot be unified across the `services`/`ai` layering boundary without either an
  inversion seam heavier than this fix warrants, or moving dispatch itself down a layer.
  Both `_level` and `effective_level` now import the SAME underlying
  `GATE_EXEMPT_READ_SHAPED`/`DEFAULT_LEVEL`/`yolo_enabled` primitives from
  `services.tool_permissions`, which is what keeps the two resolutions from silently
  drifting apart again — only the ORCHESTRATION of those primitives is duplicated, not the
  policy data itself.

## Agent provenance

- **Decided by:** senior-engineer implementation lane (Claude), acting on a security
  review's findings against commit `81cbd7b8` on this same branch.
- **Lane / branch:** `fix/email-config-unify`, worktree `.claude/worktrees/email-config-unify`.
- **Driving artifact:** the Email connector config-unification task; this ADR is the direct
  follow-up to `docs/adr/2026-08-17-read-shaped-exemption-survives-connector-tagging.md`
  (finding #4 here narrows a gap that ADR's own fix left open) and to
  `docs/adr/2026-08-17-connector-remove-security-hardening.md` (the sibling review pass this
  one mirrors in shape).
- **Why this way (one line):** an ownership fix that makes previously-inert rows and
  previously-mismatched displays suddenly REAL must not, as a side effect, hand out
  permission nobody actually gave or hide a Pause that should hold.
