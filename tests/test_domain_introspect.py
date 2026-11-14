"""Tests for ``describe`` — the domain inspectability primitive.

The load-bearing claim is that every shape ``describe`` reports is DERIVED from the code
that enforces it. These tests attack that claim from three sides:

  1. Agreement — the derived shape is compared against the hand-written tool schema for
     11 ops across 5 chokes. Where they disagree, the disagreement is PINNED with the
     verified explanation, so the register is a living record rather than a silent
     preference for either side. A new disagreement fails the test.
  2. Derivation — a fixture choke is written to disk, described, then EDITED; the output
     must follow the edit with no other change anywhere. This is what distinguishes a real
     derivation from a table that happens to be right today.
  3. Robustness — unknown targets, path traversal, and output bounds.
"""

from __future__ import annotations

import json

import pytest

from backend.services import domain_introspect as di
from backend.services import domain_introspect_ast as ast_engine


@pytest.fixture(autouse=True)
def _clear_source_cache():
    ast_engine.clear_cache()
    yield
    ast_engine.clear_cache()


# ── 1. Discovery ─────────────────────────────────────────────────────────────────────


def test_discovers_every_write_choke_from_source():
    """Every choke is found by CONVENTION (``apply_*_change``), not by a list.

    Was 10 until the Agent, lesson and scheduled-task chokes went with their products
    (2026-08-02); `automation` is the one that replaced all three. `receipt`
    (``services.receipts.apply_receipt_change``) joined 2026-08-16 with the
    cross-channel-attachments RFC's Phase 4 (ReceiptStore). `owned_asset`
    (``services.owned_assets.apply_owned_asset_change``) joined 2026-09-06 with the
    Assets tab domain (ADR 2026-09-06-assets-tab-faithful-port). `followed_portfolio`
    (``services.followed_portfolios.service.apply_followed_portfolio_change``) joined
    2026-11-10 (ADR 2026-11-10-followed-portfolios-mirror-through-the-broker-mcp)."""
    chokes = ast_engine.discover_chokes()
    families = {c.family for c in chokes}
    assert families == {
        "alert",
        "automation",
        "budget",
        "equity",
        "followed_portfolio",
        "income",
        "lot",
        "option",
        "owned_asset",
        "position",
        "receipt",
    }
    # 56 with the Agent, lesson and scheduled-task chokes; 49 once those retired and
    # `automation` replaced them (2026-08-02); 53 once `receipt` added its first 4 ops
    # (2026-08-16); 54 once `receipt` grew its `delete` op (documented in
    # services/receipts.py's own module docstring and CLAUDE.md's receipts choke entry —
    # REFUSED while any transaction link exists unless force=true, never a silent op); 64
    # once `owned_asset` joined with its 10 ops (create/update/add_mark/dispose/delete/
    # set_photo/add_doc/update_doc/remove_doc/create_category); 65 once `budget` grew
    # `envelope_note` (2026-09-26 — update_envelopes' note rides the budget choke); 62 once
    # `owned_asset` dispatches everything but create/create_category/delete through its
    # `_ASSET_OPS` handler table, which the AST scan cannot see (so only those 3 are found
    # now), and `lot` gained amend, dispose_amend and dispose_undo; 66 once
    # `followed_portfolio` joined with follow/refresh/unfollow/set_ticker (2026-11-10); 67 once
    # `income` gained `amend` (a dividend corrected from its holding's Dividends table, 2026-11-11).
    assert sum(len(c.ops) for c in chokes) == 67


def test_discriminator_split_is_eight_type_three_op():
    """8 families discriminate on ``type``, 3 on ``op``.

    ``services.options`` assigns ``op = change.get("type")`` — the variable is named ``op``
    but the KEY is ``type``. Trusting the variable name would misreport 5 ops, so this
    pins that the extractor reads the subscript key. `owned_asset` joined the ``op`` side
    2026-09-06 (``apply_owned_asset_change`` dispatches on ``change["op"]``, matching
    `automation`'s own shape, not `options`'s misleadingly-named variable)."""
    chokes = ast_engine.discover_chokes()
    by_disc = {c.family: c.discriminator for c in chokes}
    assert sorted(f for f, d in by_disc.items() if d == "type") == [
        "alert",
        "budget",
        "equity",
        "income",
        "lot",
        "option",
        "position",
        "receipt",
    ]
    assert sorted(f for f, d in by_disc.items() if d == "op") == [
        "automation",
        "followed_portfolio",
        "owned_asset",
    ]
    assert by_disc["option"] == "type"


# ── 2. Agreement with the hand-written schemas ───────────────────────────────────────

# tool name -> derived op name, one op per tool. (update_envelopes is not paired: one call
# feeds two ops — budget.envelope_rename for name/colour, budget.envelope_note for the note.)
_PAIRS = [
    ("assign_budget", "budget.assign"),
    ("move_money", "budget.move_money"),
    ("create_envelope", "budget.envelope_create"),
    ("merge_envelopes", "budget.envelope_merge"),
    ("set_envelope_targets", "budget.set_target"),
    ("acquire_lots", "lot.acquire"),
    ("dispose_lots", "lot.dispose"),
    ("open_option", "option.open"),
    ("record_vest", "equity.vest_apply"),
    ("add_positions", "position.add"),
]

# Every field the CODE reads that its tool schema never offers, with the verdict from
# reading both sides. These are findings, not noise — pinned so a new one trips the test.
_CODE_ONLY = {
    # The tool exposes this as ``type`` and renames it at the wrapper
    # (ai/tools/planning.py maps type -> target_type). Translation, not a gap.
    "budget.set_target": {"target_type"},
    # Genuine gaps: the choke accepts these, no tool offers them.
    # account_id x instrument_id find-or-create the position when position_id is absent
    # (services.lots._ensure_acquire_position — the quote page's manual lot); acquire_lots
    # always names an existing position_id. ``source`` is the provenance tag every lot write
    # stamps, defaulting to "manual".
    "lot.acquire": {"account_id", "instrument_id", "source", "txn_id"},
    "lot.dispose": {"txn_id", "source"},
    "option.open": {"quote_currency"},
    # units_withheld closed 2026-07-19: propose_vest/record_vest now offer it (informational
    # RSU withholding, mirrors the REST VestApply field the AI surface was missing).
    "equity.vest_apply": {"source"},
    # DELIBERATELY not offered, and must stay that way: `_grant_managed_ok` is the internal
    # seam the equity choke uses to create a grant's OWN second position for an instrument
    # (its unvested row, or its pending-release row beside an already-delivered liquid one),
    # bypassing the duplicate-holding guard. Exposing it to a caller would hand the model a
    # switch that turns off the guard against double-counted holdings. Mirrors the
    # `_grant_managed_ok` kwarg `update_position` already keeps off the tool surface.
    # ADR 2026-07-31-split-position-bucket.
    "position.add": {"_grant_managed_ok"},
}

# Fields the code demonstrably REFUSES without, that the schema does not mark required.
# A model obeying only the schema can construct a call the validator rejects.
_ENFORCED_NOT_DECLARED = {
    # Required-only-in-SINGLE-form (2026-07-19, planning pluralization). These stayed
    # enforced by the choke for the one-item path, but the schema can no longer mark them
    # required at the ROOT because a caller may instead send the batch wrapper
    # (`assignments` / `moves` / `targets`), where each item carries its own copy. The
    # divergence is real and worth pinning: a model reading only the root `required` list
    # sees them as optional. It is NOT reachable as a broken call — omitting both the
    # single fields and the wrapper is refused either way — but if the schema ever gains
    # a oneOf/anyOf root form, this pin should shrink rather than grow.
    "budget.assign": {"amount", "envelope_id", "month"},
    "budget.move_money": {"amount", "from_envelope_id", "month", "to_envelope_id"},
    "budget.set_target": {"amount", "due_day", "due_weekday", "envelope_id"},
    # Same class, extended by the 2026-07-19d dual-shape cleanup: these three tools used to
    # declare their BARE single-item fields required at the root. That root copy is no longer
    # advertised (schema_of_batch_only), so the root `required` names the batch wrapper
    # instead — exactly the planning shape above, and exactly what `update_transactions`
    # (the reference) declares. The bare form still WORKS; it is simply no longer described,
    # so the fields move from "declared required" to this register.
    # ``position_id`` left this register: an acquire may instead name account_id x
    # instrument_id and have the position found or created (see _CODE_ONLY).
    "lot.acquire": {"quantity"},
    # position.add: instrument_id is genuinely required. quantity, cost_basis and fees are
    # flagged because _apply_add's validators (_validate_add_cost / _refuse_add) refuse a
    # non-finite or negative value inside a try/except — but every one is read with a default
    # (`change.get(...) or 0`), so OMITTING them is valid: quantity 0 makes the empty shell the
    # lots are recorded against (ADR 2026-10-07-a-position-quantity-is-derived-from-its-records).
    # Conditional validation, the same class as lot.dispose's transfer_to_account_id.
    "position.add": {"cost_basis", "fees", "instrument_id", "quantity"},
    "lot.dispose": {
        "disposal_fees",
        "disposed_at",
        "lot_selection",
        "proceeds_currency",
        "proceeds_per_unit",
        # position_id/quantity joined 2026-07-19d for the reason in the comment above.
        "position_id",
        "quantity",
        # transfer_to_account_id (2026-09-04, one-Transfer-type-with-a-destination-label):
        # Optional at the schema level (a disposal need not name a destination at all — a
        # sale or write-off has none), but services.lots._apply_dispose validates it the
        # moment it IS supplied (must pair with is_taxable=false, proceeds_per_unit=0, no
        # proceeds_account_id) — enough conditional enforcement for the AST scan to flag it.
        # Pre-existing drift, unrelated to the account-taxonomy vocabulary; not reachable as
        # a broken call, since omitting it entirely is always valid.
        "transfer_to_account_id",
    },
    # fmv_per_unit joined 2026-07-19 (propose_vest/record_vest merge, harness reliability
    # program): the schema can no longer mark it required at the root because the FIRST
    # (previewing) call may omit it — record_vest._build_vest_change prefills it from price
    # history — while the eventual WRITE (equity_service.apply_equity_change) still
    # requires a real value. Not reachable as a broken call: a direct/unattended call with
    # no fmv_per_unit and no price history to prefill from is refused by the choke itself.
    "equity.vest_apply": {"fmv_currency", "fmv_per_unit", "price_paid_per_unit"},
    # option.open joined 2026-07-19 (propose_open_option/open_option merge, same
    # program): every field on ApplyOpenOptionInput is now Optional at the SCHEMA level,
    # because the merged tool's OWN redemption path re-derives the fully-resolved change
    # from stored args (see open_option) rather than requiring the caller to restate
    # a complete contract on every call shape (a confirm_token-only call has none of
    # these). The choke (_validate_open) still refuses a real open missing any of them.
    "option.open": {
        "account_id",
        "contracts",
        "expiry",
        "option_type",
        "side",
        "strike",
        "underlying_instrument_id",
    },
}

# The MIRROR direction: fields the schema declares required that the code never enforces.
# Mostly benign (requiredness is often domain knowledge the choke does not check), but one
# of these was worth knowing — a quantity-less acquire/add silently booked ZERO units,
# because both read it as ``float(change.get("quantity") or 0)``.
#
# ``lot.acquire`` closed 2026-07-19: ``_apply_acquire`` (services/lots.py) now refuses a
# non-positive/non-finite quantity before creating the lot — the AST introspection SEES
# this (the guard lives in the choke itself), so it drops out of this pinned register.
#
# ``position.add`` stays pinned: ``apply_position_change``'s "add" branch deliberately still
# accepts quantity=0 at the SERVICE layer — an internal shell-position-then-lots pattern
# (equity grants' locked→liquid sibling; also directly exercised by
# tests/test_lot_position_add_regression.py::test_lot_acquire_stamps_fx_rate_and_base) — so
# the fix instead lives one layer up, in the AI tools that are the only realistic source of
# an external caller's mistake: ``propose_position_add``/``add_positions``
# (ai/tools/portfolio.py) both now refuse a non-positive/non-finite quantity before ever
# reaching the choke — invisible to an AST scan of the service function itself.
_DECLARED_NOT_ENFORCED = {
    "budget.envelope_create": {"name"},
    # The batch WRAPPER key is what the root `required` now names (2026-07-19d dual-shape
    # cleanup — see the mirror entries in _ENFORCED_NOT_DECLARED). The choke runs per ITEM
    # and so never reads the wrapper, exactly like the _TOOL_ONLY wrapper pins below.
    # `quantity` DROPPED OFF position.add here in the same pass: it was pinned because the
    # schema declared it required while the service accepts quantity=0 (the internal
    # shell-position pattern; add_positions itself still refuses a non-positive quantity one
    # layer up). The schema no longer declares it required at the root, so there is nothing
    # left to diverge — the pin is retired rather than carried, which is the point of
    # re-deriving these registers instead of appending to them.
    "position.add": {"adds"},
    "lot.acquire": {"acquisitions"},
    "lot.dispose": {"disposals"},
}

# Fields the TOOL offers that the underlying choke op will never read directly (P4
# tool-pluralization sweep, harness reliability program: "make single-entity tools
# accept batches"). Each is a BATCH wrapper param — the tool unwraps it into N
# independent single-item calls into the SAME op, so the op's own AST-derived field set
# never sees the key literally, even though every item inside it maps 1:1 onto the op's
# real fields (verified: acquire_lots/dispose_lots/add_positions's batch
# loops call the identical single-item choke path per item — see backend/ai/tools/lots.py
# and backend/ai/tools/portfolio.py). A genuine new divergence, pinned per this file's own
# convention rather than silently loosening the assertion for every tool.
#
# ``confirm_token`` used to be listed here too — every confirm-gated tool advertised it. No
# tool does any more (ADR 2026-08-25-one-confirm-mechanism-for-every-tool): approval is
# resolved server-side by ``tool_use_id`` and the parameter is gone from every schema, so
# there is nothing for the choke to not-read.
_TOOL_ONLY = {
    "lot.acquire": {"acquisitions"},
    "lot.dispose": {"disposals"},
    "position.add": {"adds"},
    # "underlying" is an alias for "underlying_instrument_id" (either is accepted —
    # open_option resolves whichever is present), not a batch wrapper; grouped here
    # since the shape of the divergence (tool offers a key the choke never reads by that
    # name) is identical.
    "option.open": {"underlying"},
    # Planning batch wrappers (2026-07-19). The tool layer unwraps these and calls
    # services.planning.apply_budget_change ONCE PER ITEM, so the choke legitimately never
    # sees the wrapper key even though every field inside it is real and is read.
    "budget.assign": {"assignments"},
    "budget.move_money": {"moves"},
    "budget.set_target": {"targets"},
}


def _schemas() -> dict:
    import backend.ai.tools  # noqa: F401  (registers every tool)
    from backend.ai.tools.registry import build_tool_schemas

    return {s["name"]: s for s in build_tool_schemas()}


def _ops() -> dict:
    return {op.name: op for c in ast_engine.discover_chokes() for op in c.ops}


def _offered_fields(schema: dict) -> set:
    """Every field the tool actually offers — INCLUDING inside a batch array's items.

    A batch tool advertises its per-item fields one level down, in ``<array>.items
    .properties``, and since the 2026-07-19d dual-shape cleanup that is the ONLY place it
    advertises them (``schema_of_batch_only`` un-advertises the duplicate root-level copy —
    the root copy still WORKS, it is simply no longer described).

    Reading only the root would therefore report ``assign_budget`` as not offering
    ``amount``/``envelope_id``/``month``, which is false — they are offered, nested. Pinning
    that as a divergence would be recording a lie to keep a test green; teaching the test
    where the fields live keeps the real assertion (no dead params, no unread reads) intact
    on both sides. The wrapper key itself is still a genuine tool-only divergence, and stays
    pinned in ``_TOOL_ONLY``.
    """
    props = schema.get("properties") or {}
    out = set(props) - {"type"}
    for spec in props.values():
        items = spec.get("items") or {}
        if not items.get("properties"):
            for branch in spec.get("anyOf") or []:
                if isinstance(branch, dict) and "array" in str(branch.get("type")):
                    items = branch.get("items") or {}
                    break
        out |= set(items.get("properties") or {})
    return out - {"type"}


@pytest.mark.parametrize("tool_name,op_name", _PAIRS)
def test_derived_fields_match_hand_written_schema(tool_name, op_name):
    """The derived field set matches the schema's, except for a pinned register of
    verified divergences."""
    schema = _schemas()[tool_name]["input_schema"]
    op = _ops()[op_name]
    offered = _offered_fields(schema)
    derived = {f.name for f in op.fields}

    # Nothing the schema offers should be unread by the code — that would be a dead param
    # the model is invited to send into a void. Except a pinned BATCH wrapper param
    # (_TOOL_ONLY): the tool unwraps it into N calls of this SAME op, so the op itself
    # never reads the wrapper key even though every item field inside it is real.
    assert offered - derived == _TOOL_ONLY.get(op_name, set()), (
        f"{tool_name} offers keys {op_name} never reads: {sorted(offered - derived)} "
        f"(pinned: {sorted(_TOOL_ONLY.get(op_name, set()))})"
    )
    assert derived - offered == _CODE_ONLY.get(op_name, set()), (
        f"{op_name} reads keys {tool_name} does not offer: {sorted(derived - offered)} "
        f"(pinned: {sorted(_CODE_ONLY.get(op_name, set()))})"
    )


@pytest.mark.parametrize("tool_name,op_name", _PAIRS)
def test_code_enforced_fields_the_schema_does_not_declare_required(tool_name, op_name):
    """Pins the required-side disagreements in the direction that can BREAK a caller."""
    schema = _schemas()[tool_name]["input_schema"]
    op = _ops()[op_name]
    declared = set(schema.get("required") or ()) - {"type"}
    enforced = {f.name for f in op.required}
    assert enforced - declared == _ENFORCED_NOT_DECLARED.get(op_name, set())


@pytest.mark.parametrize("tool_name,op_name", _PAIRS)
def test_schema_declared_required_fields_are_derived_too(tool_name, op_name):
    """The MIRROR assertion — the one that catches under-reporting.

    Without this, the agreement suite is one-directional: it only notices when the code
    demands MORE than the schema, so a derivation that silently found nothing passed
    vacuously. That is exactly what happened — a raise-only detector reported
    ``option.open`` as having zero required fields while ``_validate_open`` was refusing
    five of them by RETURNING an error string, and an earlier version of this very test
    enshrined that bug as a documented property of the domain."""
    schema = _schemas()[tool_name]["input_schema"]
    op = _ops()[op_name]
    declared = set(schema.get("required") or ()) - {"type"}
    enforced = {f.name for f in op.required}
    assert declared - enforced == _DECLARED_NOT_ENFORCED.get(op_name, set()), (
        f"{tool_name} declares {sorted(declared - enforced)} required but the derivation "
        f"does not find them enforced in {op_name}"
    )


def test_the_most_validated_op_is_fully_recovered():
    """``option.open`` is the domain's most-validated write and refuses ONLY by returning
    error strings, never by raising — the case that proves refusal detection is not
    raise-only."""
    enforced = {f.name for f in _ops()["option.open"].required}
    assert {"option_type", "side", "strike", "expiry", "contracts"} <= enforced
    constraints = " ".join(_ops()["option.open"].constraints)
    assert "missing required field" in constraints


# ── 3. The derivation follows the code ───────────────────────────────────────────────

_FIXTURE_V1 = '''
"""A fixture choke."""


def apply_widget_change(change: dict) -> dict:
    ctype = change.get("type")
    if ctype == "polish":
        widget_id = change.get("widget_id")
        if not widget_id:
            raise ValidationError("widget_id is required")
        return {"ok": True, "shine": change.get("shine", "matte")}
    raise ValidationError(f"unknown widget change type: {ctype!r}")
'''

_FIXTURE_V2 = '''
"""A fixture choke, with a SECOND accepted type and a new field."""


def apply_widget_change(change: dict) -> dict:
    ctype = change.get("type")
    if ctype == "polish":
        widget_id = change.get("widget_id")
        if not widget_id:
            raise ValidationError("widget_id is required")
        return {"ok": True, "shine": change.get("shine", "matte"), "wax": change.get("wax")}
    if ctype == "discard":
        reason = change.get("reason")
        if not reason:
            raise ValidationError("reason is required to discard a widget")
        return {"ok": True}
    raise ValidationError(f"unknown widget change type: {ctype!r}")
'''


def test_derivation_follows_a_changed_choke_with_no_other_edit(tmp_path):
    """Change the choke's accepted types; ``describe`` must change with it.

    This is the test that separates a derivation from a lucky table: NOTHING here edits
    describe, the extractor, or any schema — only the fixture's own source."""
    src = tmp_path / "widgets.py"
    src.write_text(_FIXTURE_V1)

    v1 = di.describe("ops", root=str(tmp_path))
    assert [o["op"] for o in v1["ops"]] == ["widget.polish"]

    detail = di.describe("ops/widget.polish", root=str(tmp_path))
    assert [f["name"] for f in detail["required"]] == ["widget_id"]
    assert {f["name"] for f in detail["accepted"]} == {"shine"}
    assert detail["example"] == {"type": "polish", "widget_id": "…"}
    assert "widget_id is required" in detail["constraints"]

    # The ONLY edit in this test.
    src.write_text(_FIXTURE_V2)
    ast_engine.clear_cache()

    v2 = di.describe("ops", root=str(tmp_path))
    assert {o["op"] for o in v2["ops"]} == {"widget.discard", "widget.polish"}

    polished = di.describe("ops/widget.polish", root=str(tmp_path))
    assert "wax" in {f["name"] for f in polished["accepted"]}, "a new field must appear"

    discard = di.describe("ops/widget.discard", root=str(tmp_path))
    assert [f["name"] for f in discard["required"]] == ["reason"]
    assert "reason is required to discard a widget" in discard["constraints"]


def test_a_removed_field_stops_being_described(tmp_path):
    """The inverse: deleting a read removes it, so the output cannot outlive the code."""
    src = tmp_path / "widgets.py"
    src.write_text(_FIXTURE_V2)
    assert "wax" in {
        f["name"] for f in di.describe("ops/widget.polish", root=str(tmp_path))["accepted"]
    }

    src.write_text(_FIXTURE_V1)
    ast_engine.clear_cache()
    assert "wax" not in {
        f["name"] for f in di.describe("ops/widget.polish", root=str(tmp_path))["accepted"]
    }


# ── 4. Unknown targets, traversal, bounds ────────────────────────────────────────────


@pytest.mark.parametrize(
    "target",
    ["nonsense", "op", "viewz", "ops/budgt.asign", "ops/zzz", "views/not_a_tool"],
)
def test_unknown_target_never_returns_an_empty_result(target):
    out = di.describe(target, views=[{"name": "get_net_worth", "input_schema": {}}])
    assert out.get("error")
    assert out.get("did_you_mean"), f"{target} produced no suggestions"


def test_unknown_target_suggestions_are_relevant():
    out = di.describe("ops/budgt.asign")
    assert any("budget" in s for s in out["did_you_mean"])


@pytest.mark.parametrize(
    "target",
    [
        "ops/../../../etc/passwd",
        "../../etc/passwd",
        "views/../../../secrets",
        "ops/%2e%2e%2f",
        "ops/\\..\\..\\windows",
    ],
)
def test_path_traversal_targets_are_inert(target):
    """The target is a NAME, never a path. Traversal input can only miss."""
    known = {op.name for c in ast_engine.discover_chokes() for op in c.ops} | {"ops", "views"}
    out = di.describe(target, views=[{"name": "get_net_worth", "input_schema": {}}])
    assert "error" in out
    # A target is matched against KNOWN NAMES, never joined onto a path: whatever comes
    # back can only be drawn from the name universe, so traversal input has nothing to reach.
    assert set(out.get("did_you_mean") or ()) <= known | {"get_net_worth"}
    assert "source" not in out


def test_prefix_target_returns_the_whole_family():
    out = di.describe("ops/budget.")
    # 21 with envelope_note (2026-09-26); 20 once envelope_restore retired the same day
    # (ADR 2026-09-26-deleting-an-envelope-deletes-it — a delete is now for real, with a
    # server-held Undo token instead of an apply_budget_change op).
    assert out["count"] == 20
    assert all(o["op"].startswith("budget.") for o in out["ops"])


def test_q_matches_field_names_not_just_op_names():
    """A search for a FIELD finds the ops that take it."""
    out = di.describe("ops", q="envelope_id")
    names = {o["op"] for o in out["ops"]}
    assert "budget.assign" in names
    assert "budget.envelope_delete" in names


def test_a_bare_q_searches_instead_of_returning_the_index():
    """Answering a question with the table of contents is the non-answer this primitive
    exists to remove."""
    views = [{"name": "get_upcoming_vests", "input_schema": {}}]
    out = di.describe(q="vest", views=views)
    assert out["query"] == "vest"
    # release_apply is part of the vest lifecycle (it delivers a vest's shares), and
    # position.add now carries a `vesting_state` field (ADR 2026-07-31-split-position-bucket)
    # — both are genuine "vest" hits, not noise.
    assert {o["op"] for o in out["ops"]} == {
        "equity.vest_apply",
        "equity.vest_undo",
        "equity.release_apply",
        "position.add",
    }
    assert "get_upcoming_vests" in out["views"]
    assert "families" not in out


def test_a_bare_q_that_matches_nothing_still_suggests():
    out = di.describe(q="zzzznope", views=[{"name": "get_net_worth", "input_schema": {}}])
    assert out["error"]
    assert out["did_you_mean"]


def test_output_stays_bounded():
    """A 55-op domain must not dump 55 full shapes; the per-op detail carries the weight."""
    views = [
        {"name": f"get_thing_{i}", "description": "x" * 400, "input_schema": {}} for i in range(300)
    ]
    for target in ("", "ops", "views", "ops/budget."):
        size = len(json.dumps(di.describe(target, views=views)))
        assert size < 6000, f"describe({target!r}) returned {size} chars"
    assert len(json.dumps(di.describe("ops/budget.assign"))) < 2000


def test_every_op_carries_a_worked_example_and_a_source():
    for choke in ast_engine.discover_chokes():
        for op in choke.ops:
            detail = di._op_detail(op)
            assert detail["example"][op.discriminator] == op.value
            assert ":" in detail["source"]


# ── 5. Tool registration ─────────────────────────────────────────────────────────────


def test_tool_is_registered_as_a_read():
    import backend.ai.tools  # noqa: F401
    from backend.ai.tools.registry import mutating_tool_domains, registered_tool_names

    assert "describe" in registered_tool_names()
    assert "describe" not in mutating_tool_domains(), "describe is a read; it must not mutate"


@pytest.mark.asyncio
async def test_tool_dispatch_returns_json():
    import backend.ai.tools  # noqa: F401
    from backend.ai.tools.registry import dispatch

    raw, proposed = await dispatch("describe", {"target": "ops/budget.assign"})
    assert proposed is None
    payload = json.loads(raw)
    assert payload["op"] == "budget.assign"
    assert payload["example"]["type"] == "assign"


@pytest.mark.asyncio
async def test_tool_views_come_from_the_registry_read_set():
    from backend.ai.tools.describe import _views

    names = {v["name"] for v in _views()}
    assert "get_net_worth" in names
    assert "describe" not in names, "describe must not list itself as a view"
    assert "add_transaction" not in names, "a writer must never be listed as a view"
