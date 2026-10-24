---
title: The read-shaped-exemption applies regardless of a tool's connector owner
status: Superseded in part by 2026-10-11-tool-defaults-follow-what-a-tool-does
date: 2026-08-17
deciders: senior-engineer implementation lane (Claude)
---

# The read-shaped-exemption applies regardless of a tool's connector owner

> **Superseded in part** by [2026-10-11-tool-defaults-follow-what-a-tool-does](2026-10-11-tool-defaults-follow-what-a-tool-does.md): the hand-kept GATE_EXEMPT_READ_SHAPED allowlist (deleted; reads simply run by default, a read that only refreshes a screen declares refreshes=).

## Context

`send_email`, `search_messages` and `get_messages` (`backend/ai/tools/email_ingest.py`) were
untagged — no `@tool(connector=...)`. That was itself a bug: a decision made through the Email
connector's own "Tool permissions" ladder (`PUT /api/connectors/email/permissions/{tool}`, which
stores under `connector_id="email"` verbatim, taken from the URL) was never actually consulted at
dispatch, because `ai/tools/_confirm_gate.py::effective_level` resolved the untagged tools' owner
as the reserved `core` bucket instead. An operator who clicked "Always allow" on `send_email` from
Connectors → Email kept seeing a confirm card on every real call; a "Blocked" set the same way did
not actually block it. Tagging all three `connector="email"` fixes this — write and read paths now
agree on the owner (see `docs/adr/2026-08-17-connector-remove-security-hardening.md`'s "Known
gaps" bullet for the closed Remove-permission-clearing half of the same story).

`search_messages`/`get_messages` needed one more thing to tag safely. They are members of
`GATE_EXEMPT_READ_SHAPED` (`backend/ai/_confirm_gate.py`) — a curated, reviewed set of tools
whose only "write" is bookkeeping (a snapshot, a cursor), never a user-meaningful mutation. That
set exists because of a real owner incident (2026-07-21): "why is fetch_emails required
permissions? reading your own inbox is a read." Before this change, the exemption was consulted
ONLY inside `_core_default` — the default-level computation for a `core`-owned (untagged) tool.
`effective_level`'s general rule for a CONNECTOR-owned tool is uniform: `DEFAULT_LEVEL`
(`needs_approval`), read or write, no exceptions — `services/tool_permissions.py`'s own docstring
states this as settled: "No tiering by tool type inside a connector, no exceptions."

Tagging `search_messages`/`get_messages` under that unmodified rule would have flipped their
UNDECIDED default from `always_allow` to `needs_approval` as a side effect of a change that was
only supposed to be about OWNERSHIP — reopening the exact 2026-07-21 incident on every fresh
install, and breaking the explicit regression test `tests/test_resolve_pending_tool_use.py::
test_real_writes_still_gate_read_shaped_tools_dont_send_email_gates` ("read-shaped exemption must
still hold").

## Decision

`ai/tools/_confirm_gate.py::effective_level`'s undecided-default computation now runs through a
new `_default_level(tool_name, owner)` that checks `GATE_EXEMPT_READ_SHAPED` FIRST, before the
owner branch:

```python
def _default_level(tool_name: str, owner: str) -> str:
    if tool_name in GATE_EXEMPT_READ_SHAPED:
        return "always_allow"
    return DEFAULT_LEVEL if owner != CORE_CONNECTOR_ID else _core_default(tool_name)
```

For a `core`-owned tool this is byte-identical to before (`_core_default` already returned
`always_allow` for an exempt tool, via `is_gated_tool`'s own `GATE_EXEMPT_READ_SHAPED` check). For
a connector-owned tool it is new: an exempt tool now reads `always_allow` there too. Today that
affects exactly two tools (`search_messages`/`get_messages` — the only members of
`GATE_EXEMPT_READ_SHAPED` that also carry a `connector=` tag).

**This is not "no tiering by tool type inside a connector, no exceptions" reopened.** That settled
rule is about a hard floor for a DANGEROUS action — a trade tool, a payment tool — never being
exempted from asking regardless of its category; it is a floor against UNDER-caution. This change
runs the opposite direction: an ALREADY-reviewed, ALREADY-curated bookkeeping-write (not a
"tool type", a specific, individually-traced allowlist) stays exempt when its owner tag changes
for a reason that has nothing to do with how dangerous the call is. `send_email` — a real write
with an irreversible external effect — is NOT in `GATE_EXEMPT_READ_SHAPED` and is unaffected by
this change; it still defaults to `needs_approval` under its new `email` owner, exactly as it did
under `core`.

An explicit stored decision still wins over this default in either direction — `effective_level`
checks `_stored_level` before ever reaching `_default_level`, unchanged.

## Alternatives considered

- **Tag `send_email` only; leave `search_messages`/`get_messages` untagged.** Rejected — it would
  leave the exact bug this pass exists to fix (write path and read path disagreeing on the owner)
  open for two of the Email connector's three tools, and leave `clear_connector_permissions
  ("email")` on Remove still a no-op for them.
- **Tag all three and accept the regression** (every fresh install's first `search_messages`/
  `get_messages` call shows a confirm card until the operator picks "Always"). Rejected — this is
  a real, owner-documented incident being silently reopened, not a cosmetic one-time click; the
  regression test exists specifically to prevent it from happening again by accident.
- **Widen `GATE_EXEMPT_READ_SHAPED`'s consultation into `effective_level` unconditionally, for
  ANY owner default computation** (this ADR's actual choice) vs. **special-casing `search_messages`
  /`get_messages` by name in `_default_level`**. Chose the general form: the exemption is already a
  reviewed, curated set (not a heuristic), so consulting it uniformly is more honest than adding a
  second, narrower allowlist that would drift from the first the next time a `GATE_EXEMPT_READ_SHAPED`
  member picks up a `connector=` tag.

## Consequences

### Positive
- `send_email`/`search_messages`/`get_messages` all resolve to a real, correct `email` owner —
  the Email connector's own ladder and Settings › Permissions agree, and Remove genuinely clears
  all three (`tests/test_connectors_service.py::
  test_remove_connector_clears_all_three_email_tools_via_real_owner_resolution`).
- The 2026-07-21 "reading your own inbox needs no approval" product decision survives the
  ownership fix, pinned by new regression coverage (`tests/test_connector_tool_discovery_gating.py`
  ::`test_email_tools_default_levels_are_unchanged_by_the_tag`).
- The fix is general: the NEXT `GATE_EXEMPT_READ_SHAPED` member that later gains a `connector=`
  tag inherits the same safety automatically, with nothing to remember.

### Negative
- `effective_level`'s docstring and `services/tool_permissions.py`'s "settled decisions" section
  now need a reader to hold two rules in mind for a connector-owned tool's default (uniform
  `needs_approval`, EXCEPT the pre-existing curated exemption) rather than one. Documented in both
  places' docstrings, not left implicit.

## Agent provenance

- **Decided by:** senior-engineer implementation lane (Claude), in response to a coordinator
  instruction (mid-task) to also tag `search_messages`/`get_messages` with `connector="email"`.
  The tagging-only version of that instruction was verified against the live test suite BEFORE
  implementing, found to break `test_real_writes_still_gate_read_shaped_tools_dont_send_email_gates`,
  and this exemption-widening was designed as the narrowest fix that satisfies both the ownership
  goal and the pre-existing product decision, rather than either silently breaking the test or
  declining the instruction.
- **Lane / branch:** `fix/email-config-unify`, worktree `.claude/worktrees/email-config-unify`.
- **Driving artifact:** the Email config-unification task; `docs/adr/
  2026-08-17-connector-remove-security-hardening.md`'s "Known gaps" bullet (updated in the same
  change) is the security-review thread this closes.
- **Why this way (one line):** ownership and caution-level are two different questions — fixing
  who a decision is filed under must never, as a side effect, change whether an undecided call
  needs a human click.
