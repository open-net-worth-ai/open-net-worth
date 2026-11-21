"""S2 — the tool error contract.

Three behaviors, one per slice of the hardening item:

  PART 1  the tool_result block the model sees carries ``is_error: true`` when the
          result is the structured ``{"error": ...}`` shape, and carries NO such key
          on success. The signal already existed (it fed the log line and the audit
          row) but was thrown away before reaching the model.
  PART 2  ``registry.dispatch`` type-validates ``input_data`` against the tool's own
          schema and returns a POINTER-PRECISE error without entering the handler —
          while still accepting everything the handlers deliberately coerce.
  PART 3  a rejected reference carries its RECOVERY PATH (closest existing values +
          the tool that lists them), not just the refusal.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.assistant import _tool_error_message, _tool_result_block
from backend.ai.tools.registry import dispatch, validation_error
from backend.core.errors import ValidationError, did_you_mean, recovery_hint
from backend.services import categories as categories_service
from backend.services import ledger as svc


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch, db_isolated):
    """Every store the create choke touches, redirected into tmp_path. Base = SGD.

    CategoryStore is constructed per call inside services.categories, so the PATH
    constant is what has to move, not an instance attribute. accounts/transactions are
    DB-backed now (plan 3 task 4) — db_isolated redirects them to a fresh, empty database.
    """
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    return tmp_path


# --- PART 1: is_error on the tool_result block ----------------------------------------


def test_error_result_sets_is_error_flag():
    block = _tool_result_block("tu_1", json.dumps({"error": "no such account"}))
    assert block["is_error"] is True
    assert block["content"] == '{"error": "no such account"}'
    assert block["tool_use_id"] == "tu_1"


def test_successful_result_has_no_is_error_key():
    """Absent, not False — an explicit false is noise on every successful call."""
    block = _tool_result_block("tu_2", json.dumps({"ok": True, "id": "txn_1"}))
    assert "is_error" not in block


def test_non_json_result_is_not_an_error():
    block = _tool_result_block("tu_3", "plain text result")
    assert "is_error" not in block


def test_is_error_can_be_forced_for_a_plain_text_refusal():
    """The tripwire drop is a hard refusal in plain text — it has no error dict to detect."""
    block = _tool_result_block("tu_4", "that tool is now unavailable", is_error=True)
    assert block["is_error"] is True


def test_forced_false_suppresses_detection():
    """A proposed/deduped call replaces the content with a control message; the executed
    call's error flag must not ride along."""
    block = _tool_result_block("tu_5", json.dumps({"error": "x"}), is_error=False)
    assert "is_error" not in block


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"error": "boom"}', "boom"),
        ('{"ok": false, "error": "handler said no"}', "handler said no"),
        ('{"ok": true}', None),
        ('{"status": "ok", "error": null, "accounts": []}', None),
        ('{"error": ""}', None),
        ("not json", None),
        ("[1, 2, 3]", None),
        ('"just a string"', None),
    ],
)
def test_tool_error_message_detection(text, expected):
    assert _tool_error_message(text) == expected


def test_stored_transcript_with_is_error_still_rehydrates(monkeypatch):
    """A transcript written after this change must still replay through the frontend view
    rebuild — rehydrate_messages reads only tool_use_id/content, so the extra key is inert.
    This is the downstream-compatibility pin for the new block shape."""
    from backend.ai import assistant

    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_1", "name": "get_net_worth", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": '{"error": "boom"}',
                    "is_error": True,
                }
            ],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: history)
    messages = assistant.rehydrate_messages("sess")
    tool_blocks = [b for m in messages for b in m.get("blocks", []) if b.get("type") == "tool"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0]["result"] == '{"error": "boom"}'


# --- PART 2: dispatch-level type validation -------------------------------------------


def _dispatch(tool, **inp):
    text, proposed = asyncio.run(dispatch(tool, inp))
    return json.loads(text), proposed


def test_wrong_scalar_type_is_rejected_with_a_pointer():
    """The canonical case: a non-numeric string where a number belongs, located exactly."""
    err = validation_error("add_transactions", {"transactions": [{"amount": "twelve dollars"}]})
    assert err is not None
    assert "expected number, got str at transactions[0].amount" in err


def test_pointer_is_precise_inside_nested_arrays():
    err = validation_error(
        "add_transactions",
        {"transactions": [{"splits": [{"amount": 10.0}, {"amount": "not a number"}]}]},
    )
    assert "at transactions[0].splits[1].amount" in err


def test_validation_failure_never_enters_the_handler():
    """A refusal must be total — no partial write, and the standard error shape so the
    assistant loop flags it is_error."""
    res, proposed = _dispatch("add_transactions", transactions=[{"amount": "twelve dollars"}])
    assert proposed is None
    assert "expected number" in res["error"]


def test_all_errors_are_reported_together():
    """One round-trip should be enough to fix everything that is wrong."""
    err = validation_error("add_transactions", {"transactions": [{"amount": "abc", "tags": 5}]})
    assert "at transactions[0].amount" in err and "at transactions[0].tags" in err


def test_valid_input_passes_validation():
    assert (
        validation_error(
            "add_transactions", {"transactions": [{"amount": 12.5, "currency": "SGD"}]}
        )
        is None
    )


def test_unknown_tool_still_returns_the_sentinel():
    res, _ = _dispatch("no_such_tool_at_all", x=1)
    assert res["error"] == "unknown tool: no_such_tool_at_all"


# The coercions the handlers genuinely perform — validation must not out-strict them.
# Each of these broke a real test when enforced strictly; they are the contract.


def test_explicit_null_is_treated_as_omitted():
    """`field=None` means "not supplied" throughout this codebase, never "wrong type"."""
    assert validation_error("simulate_retirement", {"events": None}) is None


def test_null_against_a_oneof_branch_is_accepted():
    """oneOf degrades to anyOf: a null satisfies every relaxed branch, which under real
    oneOf semantics would fail as 'valid under each'."""
    assert validation_error("set_plan_baseline", {"baseline": None}) is None


def test_stringified_object_is_accepted():
    """Models routinely stringify object args, so an object param accepts a JSON string.
    Pinned on create_automation's `trigger` since schedule_task retired with Scheduled
    Tasks (2026-08-02)."""
    assert validation_error("create_automation", {"trigger": '{"kind":"manual"}'}) is None


def test_unparseable_string_for_an_object_is_still_rejected():
    """The leniency has a floor: a string that is not JSON at all cannot be coerced, and
    must be reported against the field by name rather than passed through."""
    err = validation_error("create_automation", {"trigger": "not json at all"})
    assert "expected object, got str at trigger" in err


def test_numeric_string_is_accepted():
    """_shared does abs(float(amount)), so "12.50" works today and must keep working."""
    assert validation_error("add_transactions", {"transactions": [{"amount": "12.50"}]}) is None


def test_undeclared_properties_are_allowed():
    """The propose→apply replay paths dispatch a stored change dict verbatim, carrying
    its type/display bookkeeping, into schemas that declare additionalProperties: false."""
    assert (
        validation_error(
            "add_positions",
            {"type": "add", "display": "Add 10 AAPL", "reason": "user asked"},
        )
        is None
    )


def test_stale_required_lists_are_not_enforced():
    """add_transactions' per-item required list names the anchor-based single-leg shape;
    a two-leg row (from_account_id/from_amount) is equally valid, and category is optional by
    invariant — enforcing required would refuse real write paths."""
    assert (
        validation_error(
            "add_transactions",
            {"transactions": [{"date": "2026-01-01", "from_account_id": "a1", "from_amount": 5.0}]},
        )
        is None
    )


def test_enum_violations_are_left_to_the_handler():
    """get_returns coerces an unknown period to 1Y by design."""
    assert validation_error("get_returns", {"period": "banana"}) is None


def test_a_property_named_like_a_keyword_survives_relaxation():
    """The schema walk must not treat user-chosen property names as keywords."""
    from backend.ai.tools.registry import _permissive

    relaxed = _permissive(
        {
            "type": "object",
            "required": ["a"],
            "properties": {"required": {"type": "string"}, "format": {"type": "number"}},
        }
    )
    assert "required" not in relaxed
    assert set(relaxed["properties"]) == {"required", "format"}


# --- PART 3: prescriptive errors at the boundary --------------------------------------


def test_did_you_mean_ranks_by_distance():
    assert did_you_mean("grocery", ["Groceries", "Rent", "Transport"]) == ["Groceries"]


def test_did_you_mean_is_bounded():
    pool = ["Food", "Fuel", "Fun", "Fees", "Film"]
    assert len(did_you_mean("Fo", pool, limit=3)) <= 3


def test_did_you_mean_on_an_empty_book():
    assert did_you_mean("anything", []) == []


def test_recovery_hint_names_the_closest_values_and_the_tool():
    hint = recovery_hint("grocery", ["Groceries", "Rent"], tool_hint="list_categories")
    assert "Groceries" in hint and "list_categories" in hint


def test_recovery_hint_lists_candidates_when_nothing_is_close():
    hint = recovery_hint("zzzzz", ["Rent", "Fuel"], tool_hint="list_accounts")
    assert "Rent" in hint and "Fuel" in hint


def test_recovery_hint_handles_an_empty_book():
    hint = recovery_hint("x", [], tool_hint="list_categories")
    assert "None exist yet" in hint and "list_categories" in hint


def test_recovery_hint_caps_a_long_roster():
    hint = recovery_hint("zzzzz", [f"Account {i}" for i in range(40)], tool_hint="list_accounts")
    assert "more)" in hint


def test_unknown_category_error_carries_the_fix(isolated_ledger):
    """The whole point: the error contains what the caller needs to self-correct."""
    categories_service.create_category("Groceries", kind="expense")
    acct = svc.accounts.create({"name": "Cash", "kind": "asset", "type": "cash", "currency": "SGD"})
    with pytest.raises(ValidationError) as exc:
        svc.create_transaction(
            {
                "date": "2026-01-01",
                "kind": "expense",
                "description": "x",
                "category": "grocery",
                "from_account_id": acct["id"],
                "from_amount": 5.0,
            }
        )
    msg = str(exc.value)
    assert "Unknown category 'grocery'" in msg
    assert "Groceries" in msg
    assert "list_categories" in msg


def test_unknown_account_error_lists_real_account_names(isolated_ledger):
    """An account id is opaque, so the roster of NAMES is the actionable part."""
    a = svc.accounts.create(
        {"name": "Meridian Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    with pytest.raises(ValidationError) as exc:
        svc.create_transaction(
            {
                "date": "2026-01-01",
                "kind": "transfer",
                "description": "x",
                "from_account_id": a["id"],
                "from_amount": 5.0,
                "to_account_id": "acc_does_not_exist",
                "to_amount": 5.0,
            }
        )
    msg = str(exc.value)
    assert "does not exist" in msg
    assert "Meridian Savings" in msg
    assert "list_accounts" in msg


def test_an_empty_error_slot_is_not_a_failure_in_telemetry_either():
    from backend.ai.tools.call_log import classify_error

    assert classify_error('{"status": "ok", "error": null, "accounts": []}') is None
    assert classify_error('{"error": "boom"}') == "tool_error"
