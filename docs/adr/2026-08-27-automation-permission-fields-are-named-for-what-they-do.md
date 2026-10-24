# An Automation's permission fields are named for what they do

_Status: Accepted · 2026-08-27_
_Covers two renames made the same day: `allowed` → `always_allowed` (revision 0061) and
`approval` → `permission_mode` (revision 0062)._
_Related: `2026-08-04-a-run-cannot-write-the-policy-that-governs-runs`,
`2026-08-14-one-permission-store-for-every-tool`,
`2026-08-25-one-confirm-mechanism-for-every-tool`_

## Context

An Automation carries a list of permission descriptors under the key `allowed`. The name says
*"these are the tools this Automation is allowed to use."* That is not what it does.

The list has exactly one consumer:

```
automation["allowed"]
  → automation_runner_adapter._canonical_always_allowed_tools()
  → AutomationApprovalContext.always_allowed_tools
  → _confirm_gate.automation_gate_verdict() returns "allow"
```

It decides whether the owner is **asked**, never **what may be called**. An Automation with
`allowed: []` can still call every tool in the app — it is simply asked first. Reachability is a
different mechanism entirely (the caller's tool offer), and this list has never fed it.

So the name was wrong in the direction that matters: **`allowed: []` reads as a lockdown and is
not one.** A reader hardening an Automation by emptying the list would believe they had
restricted it and would have changed nothing except how often they get a card.

The evidence that this was already causing friction is in the tree:

- `automation_runner_adapter.py` carried a comment explaining the confusion rather than fixing
  it — *"`allowed` governs APPROVAL, never REACHABILITY"* — written after a 2026-08-09 change
  (`d2b1d631`) read the list as a capability ceiling and broke every Automation the next
  morning: empty offer + unattended meant every tool refused, and runs still reported Completed.
- The UI never used the name. `AutomationDetail.tsx` has labelled the control **"Always
  allowed"** since it was built.
- The in-memory field on the context object was already renamed `allowed_tools` →
  `always_allowed_tools` earlier the same day, for this exact reason: `allowed_tools` collides
  with a playbook's `allowed_tools`, which genuinely IS a reachability list.

## Decision

**Rename the persisted key `allowed` → `always_allowed`**, everywhere: the domain model, the
`AutomationRunnerRequest` seam, the store's canonical row, the REST payloads, the service's
validation and its five system-automation seeds, the frontend type and its four call sites.

This is the persisted half of the rename whose in-memory half already landed. Both halves now
say the same thing, and the storage name matches the label the UI has always shown.

Revision **0061** rewrites the key on every existing row. It renames the key even when the list
is EMPTY (25 of this instance's 27 live rows): leaving an empty `allowed` behind would strand a
key nothing reads, and `AutomationStore._canonical` writes `always_allowed` on the row's next
save regardless — so a skipped row would silently carry both spellings. `downgrade()` is exact
and lossless.

**No compatibility layer.** Nothing reads the old key after this change — no dual-read, no
alias. A rename that leaves both spellings live is the ambiguity, not the fix.

## Consequences

- A reader who empties the list now reads "nothing is always-allowed", which is true, instead
  of "nothing is allowed", which never was.
- The four one-shot Automation migration scripts under `backend/scripts/` are NOT updated. They
  are frozen, receipt-guarded, already-applied artifacts that operate on the pre-rename shape by
  definition; revision 0061 runs after them and normalizes whatever they produced. This matches
  how the codebase already treats the "frozen legacy migrations" those scripts themselves cite.
- `tests/db/test_configs_cutover.py`'s exact-round-trip fixture moves to the new spelling. That
  test guards cutover FIDELITY; a deliberate later rename would make it fail for a reason that
  is not a fidelity problem. The rename's own end-to-end proof — including a legacy payload
  through a real `command.upgrade`, and a full up/down cycle — lives in
  `tests/db/test_automation_always_allowed_migration.py`.

## Agent provenance

Raised by the owner while reviewing the in-memory rename: *"but is it for always_allowed or
not? if it's always_allowed, then better to have this as always_allowed."* I had argued against
this rename an hour earlier, on the grounds that an Automation has no reachability field beside
it to confuse it with. That reasoning was wrong — the confusion is not with a sibling field, it
is that the name describes a mechanism the list does not implement. Corrected and applied.


---

## Second rename, same day: `approval` → `permission_mode`

The other half of the pair had the same defect. `approval` is a standing three-way policy —
`manual` asks on every approval-tier call, `auto` proceeds except on hardline operations whose
target set cannot be verified after execution, `skip` proceeds regardless. The name describes
the thing being decided rather than the setting, so it reads as a per-run outcome ("was this
approved?") instead of a mode.

Naming one field `approval` and the other `always_allowed` also hid that they are two halves of
ONE decision: `permission_mode` is the blanket policy, `always_allowed` is the per-tool
exceptions carved out of it by real approval taps. Neither ever widens what may be CALLED.

**Renamed `approval` → `permission_mode`**, everywhere the other one was, plus three places
`always_allowed` did not reach:

- **The AI tool surface.** `approval` is a model-facing parameter on `create_automation`,
  `update_automations` and `preview_automation`, so this changes the argument name Lucius is
  told to send — not only what is on disk. (`always_allowed` is deliberately absent from the
  tool schema: only `grant_automation_tool`, from a real tap, writes it.)
- **The inference layer.** `services/automations/inference.py` asks a model to fill a JSON
  draft; the field is in that schema and named in the prompt. `_EXPLICIT_APPROVAL_RE` keeps its
  name — it matches the English word a PERSON types ("skip approvals"), which has not changed.
- **The demo generator.** `backend/scripts/demo/` is live code (ADR
  `2026-08-23-the-demo-book-is-generated-not-a-fixture`), unlike the frozen one-shot migration
  scripts beside it, so it was updated.

The UI control keeps its name (`AutomationApprovalControl`) and its "Approval behavior" label:
a person still calls this "approval behaviour", and the defect was in the DATA name, not the
words on screen. Its value TYPE moved with the field (`AutomationApprovalPolicy` →
`PermissionMode`).

`tests/test_policy_tools_are_complete.py` derives the self-escalation guard set from this
parameter's presence, so it moved with the rename and still finds all three policy-writing
tools.


---

## Third decision, same day: the precedence ladder

The rename above made a latent question answerable, so the owner answered it: **what wins when
a tool's stored level and an Automation's own settings disagree?**

The ladder, in order, as implemented in `automation_gate_verdict`:

| # | Rule | Beats everything below |
|---|---|---|
| 0 | `blocked` | refused in `registry.dispatch`, before the gate is consulted at all |
| 1 | `AUTOMATION_POLICY_TOOLS` *(removed 2026-10-11)* | always gate, every mode, every grant — a run must not author the rules that govern runs |
| 2 | the Automation's own `always_allowed` | a human tapped *Always* on a card for THIS Automation |
| 3 | an explicitly STORED `needs_approval` | outranks the `permission_mode` |
| 4 | `permission_mode` (`manual`/`auto`/`skip`) | the blanket default |

**3 over 4 is `deny > ask > allow`** — an explicit *ask me* is not overridable by a blanket
*stop asking me*, exactly as `blocked` already was not. Before this, setting a tool to
"Needs approval" in Settings had NO effect on a `skip` Automation, which made that control
quietly meaningless for the runs it most mattered for.

**2 over 3 is where this deliberately parts company with Claude Code's model**, in which an
`ask` rule beats an `allow` rule and specificity never breaks the tie. Here specificity DOES
break it. The reverse was implemented first and rejected once its truth table was put in front
of the owner: with the global level winning, tapping *Always* on a confirm card inside a run
wrote a grant that then did nothing, with no feedback saying so. A per-Automation grant is more
specific, more recent, and made with the card open showing exactly what would run; the global
level is a standing default set elsewhere, at another time, about every caller at once. A
control that silently does nothing is a worse failure than a narrow grant.

Only rows 3 and 4 changed behaviour. On the owner's own instance the change is inert today —
all 28 stored rows are `always_allow`, so there is no explicit *ask* for row 3 to fire on, and
every Automation's verdicts are byte-identical (verified against the live database).

`_explicitly_asks` keys on `stored_tool_level`, never `effective_level`: `needs_approval` is
ALSO the default for an undecided tool, so keying on the effective level would fire row 3 for
almost every tool and silently turn `skip`/`auto` into no-ops that still appeared in the UI.
`tests/test_automation_approvals.py::test_an_UNDECIDED_tool_is_untouched_by_the_rule` is the
guard on exactly that.
