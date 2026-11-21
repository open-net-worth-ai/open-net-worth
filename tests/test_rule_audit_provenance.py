"""How an Automation's categorization is attributed in the audit trail.

Until 2026-08-02 a categorization rule was a DECLARATIVE row applied inside
``create_transaction``: the row was born with the rule's category, and the create event
carried an ``automation_applied`` block naming the rule, the category it replaced and why.
Thirteen tests pinned that (override of an explicit category, fill of a blank one, tag-only
matches not counting as a category decision, per-row stamping in bulk, splits and
adjustments never stamped, the edit path never emitting it, and a failure in provenance
never breaking the create).

Rules are now event-triggered Automations. Nothing is applied at create time — the row
commits, ``txn`` fires, and a Lucius run calls ``update_transactions``. So the create event
has no rule to name, and attribution moved to where the write actually happens: the audit
scope on the UPDATE, which the runner binds for every write a run makes.

These pin that surviving attribution. The create-time block is not re-tested because it can
no longer be produced — ``active_transaction_transforms()`` returns nothing and the store
refuses to create a transform.

``tests/test_automation_audit_provenance.py`` was deleted with the same change. Its six
tests covered the write-side half of the identical mechanism: usage counted only after the
transaction COMMITS (never on a preview), a bulk create preloading the automation set once
and recording each committed match, the LAST decider owning the provenance when several
matched, and a blocked category action counting as a match without claiming it. All six
describe a transform pipeline that no longer runs.
"""

from __future__ import annotations

import pytest

from backend.core.audit_context import clear_audit_scope, set_audit_scope
from backend.services import audit


@pytest.fixture
def env(db_isolated):
    """Hermetic audit + ledger stores. TransactionAuditStore is DB-backed (plan 3 task
    4) — db_isolated redirects it (audit._audit) to a fresh, empty per-test database."""
    yield


def _scoped_write(op: str, txn_id: str, **scope):
    token = set_audit_scope(**scope)
    try:
        audit.record_txn_event(op, txn_id)
    finally:
        clear_audit_scope(token)


def test_an_update_made_by_an_automation_run_names_that_automation(env):
    """The attribution that replaced the create-time block: any write a run makes carries
    the Automation's id and name, because the runner binds them for the whole turn."""
    _scoped_write(
        "update",
        "txn_1",
        actor="ai",
        surface="automation",
        session_id="automation-run-run_1",
        automation="meridian quickcash paylite → family's_proxy_loan",
        automation_id="rule_f3fb7bec3968",
        automation_run_id="run_1",
    )

    event = audit.trace("txn_1")["events"][-1]
    assert event["automation_id"] == "rule_f3fb7bec3968"
    assert event["automation"] == "meridian quickcash paylite → family's_proxy_loan"
    assert event["surface"] == "automation"


def test_the_run_is_identified_so_the_write_can_be_traced_to_one_run(env):
    """Not just WHICH Automation — which RUN, so a row's category can be traced back to the
    conversation that set it."""
    _scoped_write(
        "update",
        "txn_2",
        actor="ai",
        surface="automation",
        session_id="automation-run-run_2",
        automation="A",
        automation_id="auto_1",
        automation_run_id="run_2",
    )

    event = audit.trace("txn_2")["events"][-1]
    assert event["session_id"] == "automation-run-run_2"


def test_an_ordinary_write_carries_no_automation_attribution(env):
    """The fields are sparse — a hand edit must not look like an Automation's work."""
    _scoped_write("update", "txn_3", actor="user", surface="web")

    event = audit.trace("txn_3")["events"][-1]
    assert event.get("automation_id") is None
    assert event.get("automation") is None


def test_a_create_no_longer_carries_a_rule_block(env):
    """The create path has no rule to name: nothing is applied before the row commits."""
    _scoped_write("create", "txn_4", actor="user", surface="web")

    event = audit.trace("txn_4")["events"][-1]
    assert "automation_applied" not in event


@pytest.mark.flags(transaction_rules=False)
def test_the_declarative_transform_engine_is_unreachable_while_rules_are_off():
    """With ``transaction_rules`` off nothing on disk IS a transform and the service refuses to
    create one, so the engine stays a dormant path (ADR 2026-11-21-rules-are-a-visible-kind-of-
    automation brings it back behind the flag). The store itself only ever holds Lucius or a Rule."""
    from backend.core.errors import ValidationError
    from backend.db.automation_store import AutomationStore
    from backend.services.automations.service import apply_automation_change
    from backend.services.automations.transaction_events import active_transaction_transforms

    assert active_transaction_transforms() == []
    draft = {
        "name": "x",
        "instructions": "y",
        "trigger": {"kind": "event", "event": "txn", "when": {}, "limit": "each"},
        "execution": {"kind": "transaction_transform", "set": {"category": "cat_x"}},
    }
    with pytest.raises(ValidationError, match="Lucius"):
        apply_automation_change({"op": "create", "automation": draft})
    with pytest.raises(ValueError, match="must run Lucius"):
        AutomationStore().create({**draft, "execution": {"kind": "shell"}})
