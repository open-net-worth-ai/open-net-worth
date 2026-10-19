# A run cannot write the policy that governs runs

- **Date:** 2026-08-04
- **Status:** Accepted — the `AUTOMATION_POLICY_TOOLS` guard it introduced was removed 2026-10-11 (`2026-10-11-permissions-are-one-value-per-tool`, item 6): the four tools follow the mode, and the Auto judge is told an Automation call's consequence in a trusted line.
- **Areas:** `backend/ai/tools/_confirm_gate.py`, `backend/ai/tools/automations.py`

## Context

An Automation carries an owner-selected approval mode: `manual` gates every ungranted
approval-tier call, `auto` gates only the hardline tier, `skip` gates nothing. The hardline
tier (`delete_transactions_where`, `apply_payee_rules`) is documented as unreachable
unattended — "no config, no override flag turns this back on".

Two independent security reviews of this branch found the same escalation, and it holds
because **writing an Automation is itself an ordinary, non-hardline write**:

1. An `auto`-mode Automation gets `allow` for `create_automation` — no card.
2. It creates an Automation with `approval="skip"`, then calls `run_automation`.
3. The child run binds `mode="skip"` and clears the hardline tier.

A filtered bulk delete of the ledger, reached with no card ever shown. The input driving
step 1 is untrusted by construction: these are the Automations built to ingest email, and
the seeded ones ship `approval: "auto"`. The same call can instead rewrite a *different*,
`manual`-mode Automation's mode — crossing the owner's boundary permanently.

`enforce_unattended_write` is not a backstop here. It returns immediately unless
`set_unattended(True)` was called, and the Automation path never calls it, so
`automation_gate_verdict` was the only barrier.

A second, narrower hole fed the same outcome: `update_automations` forwarded every key
except `id`/`execution` to the service, and `_EDITABLE_FIELDS` accepts `allowed` — the
standing permission list (**renamed `always_allowed` on 2026-08-27**, revision 0061, and the
`approval` field named throughout this ADR became `permission_mode` at revision 0062 the same
day — see `2026-08-27-automation-permission-fields-are-named-for-what-they-do`; the behaviour
below is unchanged, only the two keys' names). `allowed` is absent from the tool schema, but the schema was never
the defence: `NestedInput` emits no `additionalProperties` and dispatch strips that keyword
regardless. `allowed` is checked ahead of everything in the gate, so smuggling one key
granted a permission the UI only ever writes from a real approval tap.

## Decision

**The tools that write Automations always gate inside an Automation run** — regardless of
mode, and regardless of a standing local grant. `AUTOMATION_POLICY_TOOLS` =
`{create_automation, update_automations, delete_automations, run_automation}`.

**`update_automations` whitelists the fields an AI-originated update may write.**
`_AI_UPDATABLE_FIELDS`; `allowed` is not in it and is writable only by
`grant_automation_tool`, from an approval tap.

## Why this shape, and not the alternatives

Both reviewers proposed making `is_hardline_blocked` unconditional — hardline gates even in
`skip`. **Rejected.** `skip` covering the hardline tier is deliberate and documented; it is
the owner's stated choice on an Automation the owner configured, and overturning it would
silently break a mode they selected. The defect was never that `skip` exists. It was that a
*run* could mint or rewrite `skip` for itself. Fixing authorship fixes the escalation and
leaves the owner's authority intact.

The narrower "make `run_automation` refuse when an approval context is already bound" was
also rejected as insufficient on its own: it blocks step 3 while leaving step 2 — the
persistent rewrite of another Automation's mode — wide open.

The cost is real and accepted: an `auto`-mode Automation that legitimately wants to create
an Automation now pauses for a card. That is the correct trade. Self-modification is exactly
the operation whose blast radius cannot be verified after the fact, which is the same test
that put the hardline tier where it is.

## Consequences

- An Automation can still *read* and *propose* Automations freely; only the writes gate.
- `skip` remains reachable only from the owner's own configuration surface (UI/REST).
- Regression tests: `tests/test_automation_approvals.py` —
  `test_a_run_can_never_write_the_policy_that_governs_runs` (all 4 tools × all 3 modes, with
  the tool also listed as an Always-allowed grant) and
  `test_the_model_cannot_smuggle_a_standing_permission_through_an_update`.

## Agent provenance

Found by two independent `security-reviewer` agents on the pre-merge gate for
`feature/automations-redesign`; neither was prompted at this function specifically. The fix
diverges from both reviewers' recommended remedy, for the reason recorded above.
