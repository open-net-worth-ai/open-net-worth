"""
AI tool-dispatch tests for the Planning (envelope-budgeting) tools (PLAN).

Offline + isolated: the assignments / groups / transactions stores, FX, and profile are
monkeypatched into tmp_path; execute_tool is async, driven via asyncio.run. No LLM, no
network. Covers the get_budget_month read, an assign propose→apply round-trip through the
single apply_budget_change choke point, and the headless-allowlist invariant.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool, registry
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database. No accounts
    seeded: these tests assert zero-cash semantics (RTA 0)."""
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # The envelope service holds its OWN store instances — isolate them too. Full-split (RFC
    # envelope-category-full-split.md §Backend 3) deleted the pseudo-row fallback, so this
    # file's bare category keys need REAL 1:1 envelopes to be assignable. budget_assignments
    # is DB-backed now (plan 4a task 4) — db_isolated already covers it. profile is DB-backed
    # too (Wave 5) — no more PROFILE_JSON module attr on services.budgets to monkeypatch.
    ProfileStore().set_raw({"base_currency": "SGD"})
    envelopes_service.ensure_envelopes_migrated()
    return tmp_path


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_planning_test"


@pytest.fixture(autouse=True)
def _card_capable(monkeypatch):
    """These tests are about the propose→confirm flow, so they need a surface that can SHOW a
    card. ``_proposed`` used to return a proposal unconditionally; it now asks
    ``should_preview_write()`` like every other gated tool, and a bare unit test has no cost
    surface bound — which reads as unattended, i.e. write immediately. Patching it True is
    the turn-loop condition these tests were always assuming."""
    # Still yields to a REDEMPTION — a ✓ must write, not draw another card. That is the
    # real predicate's own first rule, and stubbing it away would make these tests pass
    # against a loop.
    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def _confirm_tool(change: dict) -> str:
    """The tool that staged this card — the one a ✓ resolves.

    A confirm is no longer "call apply_budget_change with the change dict": the server
    re-runs the STAGING tool on the args it stored, so the card's own ``grant_tool`` names
    what to call and the dict itself is inert (ADR
    2026-08-25-one-confirm-mechanism-for-every-tool)."""
    return change["grant_tool"]


def write_via_tool(**change):
    """The TOOL-surface write: a service error comes back as ``{"ok": False, "error": …}``,
    never as a raise. That contract moved into the shared writer when ``apply_budget_change``
    was deleted; ``apply_now`` below is the raw service, for arranging state."""
    from backend.ai.tools import _budget_confirm

    text, proposed = _budget_confirm.write(change)
    return json.loads(text), proposed


def apply_now(**change):
    """Arrange state through the write choke directly.

    Setup is not the thing under test: these used to call ``apply_budget_change``, which is
    deleted (the propose tool writes on approval now — ADR
    2026-08-25-one-confirm-mechanism-for-every-tool). Routing setup through a confirm card
    would make every arrangement a two-step dance for no coverage."""
    from backend.services import planning as _svc

    return _svc.apply_budget_change(change), None


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def _env(key: str) -> str:
    """Resolve a category key to its (fixture-minted, 1:1) envelope id — the ref every money
    write now takes (docs/adr/2026-07-05-envelope-id-native.md)."""
    return envelopes_service.env_of()[key]


def test_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    # The apply twin is deleted — a propose tool writes on approval (ADR
    # 2026-08-25-one-confirm-mechanism-for-every-tool).
    assert "apply_budget_change" not in names
    assert {
        "get_budget_month",
        "assign_budget",
        "move_money",
        "set_envelope_targets",
        "create_envelope_group",
        "rename_envelope_groups",
        "reorder_envelope_groups",
        "delete_envelope_groups",
        "move_envelopes_to_group",
        "reorder_envelopes_in_group",
        "auto_assign",
        # envelope lifecycle (PLAN — audit + first-class tools). restore_envelope and
        # list_archived_envelopes are RETIRED (ADR 2026-09-26-deleting-an-envelope-deletes-it)
        # — delete_envelope hard-deletes for real now, with a server-held Undo instead.
        "list_envelopes",
        "create_envelope",
        "update_envelopes",
        "merge_envelopes",
        "link_categories",
        "unlink_categories",
        "delete_envelope",
    } <= names
    assert "restore_envelope" not in names
    assert "list_archived_envelopes" not in names


def test_get_budget_month_read(isolated):
    body, proposed = call("get_budget_month", month="2026-06")
    assert proposed is None  # read → no proposed_change
    assert body["month"] == "2026-06"
    assert body["ready_to_assign"] == 0.0


def test_get_budget_month_defaults_current(isolated):
    body, _ = call("get_budget_month")
    assert len(body["month"]) == 7


def test_assign_is_propose_only(isolated):
    body, proposed = call(
        "assign_budget", month="2026-06", envelope_id=_env("cat_13095ba70000"), amount=800
    )
    assert body is None
    assert proposed is not None
    assert proposed["type"] == "assign"
    assert proposed["display"]["op"] == "Assign"
    # propose writes NOTHING
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["assigned"] == 0.0


def test_assign_apply_round_trip_through_choke_point(isolated):
    # 1) propose
    _, proposed = call(
        "assign_budget", month="2026-06", envelope_id=_env("cat_13095ba70000"), amount=800
    )
    # 2) apply (the confirm-card path) — echoes the change back, incl. carrier fields
    body, p2 = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert p2 is None
    assert body["ok"] is True and body["assigned"] == 800.0
    # 3) reflected in the table
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["assigned"] == 800.0


def test_apply_unknown_category_returns_error(isolated):
    body, _ = write_via_tool(type="assign", month="2026-06", envelope_id="nope", amount=1)
    assert body["ok"] is False and "error" in body


def test_set_weekly_target_propose_and_apply(isolated):
    body, proposed = call(
        "set_envelope_targets", envelope_id=_env("cat_13095ba70000"), type="weekly", amount=100
    )
    assert body is None
    assert proposed["type"] == "set_target"
    assert proposed["target_type"] == "weekly"
    assert proposed["display"]["op"] == "Set target"
    # propose writes nothing
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["target"] is None
    # apply (the confirm-card path)
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target"]["type"] == "weekly"


def test_set_yearly_target_apply(isolated):
    _, proposed = call(
        "set_envelope_targets", envelope_id=_env("cat_c19c4c0f0000"), type="yearly", amount=1200
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target"]["type"] == "yearly"


def test_snooze_target_propose_and_apply(isolated):
    # set a target first
    _, p = call(
        "set_envelope_targets", envelope_id=_env("cat_13095ba70000"), type="monthly", amount=800
    )
    call(_confirm_tool(p), confirm_token=p["tool_use_id"])
    # snooze it (no type, just snooze_until)
    body, proposed = call(
        "set_envelope_targets", envelope_id=_env("cat_13095ba70000"), snooze_until="2026-06"
    )
    assert body is None
    assert proposed["display"]["op"] == "Snooze target"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target"]["snooze_until"] == "2026-06"
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["status"] == "snoozed"


def test_set_target_unknown_type_apply_errors(isolated):
    _, proposed = call(
        "set_envelope_targets", envelope_id=_env("cat_13095ba70000"), type="biweekly", amount=100
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is False and "error" in out


def _seed_group(name, cats):
    """An envelope group holding the 1:1 envelopes of ``cats`` in order (a group lists no
    categories — envelopes are placed into it)."""
    grp, _ = apply_now(type="group_create", name=name)
    gid = grp["group"]["id"]
    for cat in cats:
        apply_now(type="move_category", category=_env(cat), group_id=gid)
    return gid


def test_create_envelope_group_proposal_takes_no_categories(isolated):
    _, proposed = call("create_envelope_group", name="Fun", categories=["cat_13095ba70000"])
    assert proposed["type"] == "group_create"
    assert "categories" not in proposed
    assert "categories" not in proposed["display"]


def test_reorder_categories_in_group_propose_and_apply(isolated):
    gid = _seed_group("Essentials", ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"])
    # propose
    body, proposed = call(
        "reorder_envelopes_in_group",
        group_id=gid,
        envelope_ids=[_env("cat_6ea706940000"), _env("cat_13095ba70000"), _env("cat_db5642ab0000")],
    )
    assert body is None
    assert proposed["type"] == "reorder_categories"
    assert proposed["display"]["op"] == "Reorder envelopes"
    # propose writes nothing — order unchanged
    table, _ = call("get_budget_month", month="2026-06")
    grp_now = next(g for g in table["groups"] if g["id"] == gid)
    assert [c["category_ids"][0] for c in grp_now["categories"]] == [
        "cat_13095ba70000",
        "cat_db5642ab0000",
        "cat_6ea706940000",
    ]
    # apply (the confirm-card path) routes through the choke point
    out, p2 = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert p2 is None
    assert out["ok"] is True and out["categories"] == [
        _env("cat_6ea706940000"),
        _env("cat_13095ba70000"),
        _env("cat_db5642ab0000"),
    ]
    table2, _ = call("get_budget_month", month="2026-06")
    grp2 = next(g for g in table2["groups"] if g["id"] == gid)
    assert [c["category_ids"][0] for c in grp2["categories"]] == [
        "cat_6ea706940000",
        "cat_13095ba70000",
        "cat_db5642ab0000",
    ]


def test_auto_assign_is_propose_only(isolated):
    # seed a prior-month assignment so 'assigned_last_month' has something to propose
    apply_now(
        type="assign",
        month="2026-05",
        envelope_id=_env("cat_13095ba70000"),
        amount=500,
    )
    body, proposed = call("auto_assign", mode="assigned_last_month", month="2026-06")
    assert body is None
    assert proposed is not None
    assert proposed["type"] == "auto_assign"
    assert proposed["mode"] == "assigned_last_month"
    assert proposed["display"]["op"] == "Auto-assign"
    assert proposed["display"]["steps_count"] >= 1
    # propose writes NOTHING — food still 0 in June
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["assigned"] == 0.0


def test_auto_assign_apply_round_trip(isolated):
    # seed May so June has a prior month to copy
    apply_now(
        type="assign",
        month="2026-05",
        envelope_id=_env("cat_13095ba70000"),
        amount=800,
    )
    apply_now(
        type="assign",
        month="2026-05",
        envelope_id=_env("cat_db5642ab0000"),
        amount=200,
    )
    _, proposed = call("auto_assign", mode="assigned_last_month", month="2026-06")
    out, p2 = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert p2 is None
    assert out["ok"] is True
    assert out["applied"] >= 1
    # verify the values were actually written
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["assigned"] == 800.0
    transport = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_db5642ab0000" in c["category_ids"]
    )
    assert transport["assigned"] == 200.0


def test_move_category_to_group_carries_index(isolated):
    gid = _seed_group("Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    call("get_budget_month", month="2026-06")  # seed Others
    _, proposed = call(
        "move_envelopes_to_group", envelope_ids=[_env("cat_6ea706940000")], group_id=gid, index=1
    )
    assert proposed["index"] == 1
    call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    table, _ = call("get_budget_month", month="2026-06")
    grp_now = next(g for g in table["groups"] if g["id"] == gid)
    assert [c["category_ids"][0] for c in grp_now["categories"]] == [
        "cat_13095ba70000",
        "cat_6ea706940000",
        "cat_db5642ab0000",
    ]


# ── Target deadline fields (handoff delta #13) ────────────────────────────────────


def test_set_weekly_target_with_due_weekday(isolated):
    _, proposed = call(
        "set_envelope_targets",
        envelope_id=_env("cat_13095ba70000"),
        type="weekly",
        amount=100,
        due_weekday=5,
    )
    assert proposed["due_weekday"] == 5
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target"]["due_weekday"] == 5
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["target"]["due_weekday"] == 5


def test_set_monthly_target_with_due_day(isolated):
    _, proposed = call(
        "set_envelope_targets",
        envelope_id=_env("cat_13095ba70000"),
        type="monthly",
        amount=800,
        due_day=-1,
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target"]["due_day"] == -1


def test_set_target_due_field_mismatch_errors(isolated):
    _, proposed = call(
        "set_envelope_targets",
        envelope_id=_env("cat_13095ba70000"),
        type="weekly",
        amount=100,
        due_day=15,
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is False and "error" in out


# ── RTA as a move endpoint (handoff delta #9) ──────────────────────────────────────


def test_apply_move_money_from_rta_funds_target_uncapped(isolated):
    out, _ = apply_now(
        type="move_money",
        month="2026-06",
        from_envelope_id="__rta",
        to_envelope_id=_env("cat_13095ba70000"),
        amount=50,
    )
    assert out["ok"] is True and out["to_assigned"] == 50.0
    table, _ = call("get_budget_month", month="2026-06")
    assert table["ready_to_assign"] == -50.0  # no RTA cap, matches assign


def test_apply_move_money_to_rta_releases_source_capped(isolated):
    write_via_tool(
        type="assign",
        month="2026-06",
        envelope_id=_env("cat_13095ba70000"),
        amount=30,
    )
    out, _ = write_via_tool(
        type="move_money",
        month="2026-06",
        from_envelope_id=_env("cat_13095ba70000"),
        to_envelope_id="__rta",
        amount=100,
    )
    assert out["ok"] is False and "error" in out  # capped — can't release more than assigned


def test_apply_move_money_both_rta_errors(isolated):
    out, _ = write_via_tool(
        type="move_money",
        month="2026-06",
        from_envelope_id="__rta",
        to_envelope_id="__rta",
        amount=10,
    )
    assert out["ok"] is False and "error" in out


# ── Envelope delete / restore (handoff delta #2/#3) ────────────────────────────────


def test_apply_envelope_delete_hard_deletes_for_real(isolated):
    """ADR 2026-09-26-deleting-an-envelope-deletes-it: envelope_delete hard-deletes the row and
    hands back a server-held undo_token — there is no envelope_restore op any more (Undo lives
    in the UI's own pill / services.envelopes.undo_delete_envelope, not a budget-choke op)."""
    created, _ = apply_now(type="envelope_create", name="Emergency fund")
    envelope_id = created["envelope"]["id"]
    apply_now(
        type="assign",
        month="2026-06",
        envelope_id=envelope_id,
        amount=500,
    )

    deleted, _ = apply_now(type="envelope_delete", envelope_id=envelope_id)
    assert deleted["ok"] is True
    assert deleted["undo_token"]
    assert deleted["zeroed_assignments"] == {"2026-06": 500.0}
    assert envelopes_service.envelope_store.find_by_id(envelope_id) is None

    undo = envelopes_service.undo_delete_envelope(deleted["undo_token"])
    assert undo["ok"] is True
    assert undo["envelope"]["archived"] is False
    assert undo["envelope"]["category_ids"] == []


# ── First-class envelope lifecycle tools (audit + upgrade) ─────────────────────────
# list_envelopes is a plain read (proposed_change=None), mirroring get_budget_month. The
# writes (create/update/merge_envelopes, link/unlink_categories, delete_envelope) are
# APPROVE-TIER thin wrappers around the matching envelope_* apply_budget_change type — same
# propose→apply shape as assign_budget et al. restore_envelope / list_archived_envelopes are
# RETIRED (ADR 2026-09-26-deleting-an-envelope-deletes-it) — delete_envelope hard-deletes for
# real now, with a server-held Undo instead of an archive-and-restore round trip.


def test_list_envelopes_read(isolated):
    body, proposed = call("list_envelopes")
    assert proposed is None  # read → no proposed_change
    assert isinstance(body["envelopes"], list)
    keys = {e["id"] for e in body["envelopes"]}
    assert len(keys) >= 1  # migration seeds a 1:1 envelope per budgeted category


def test_list_envelopes_excludes_deleted(isolated):
    """A deleted envelope is gone for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) —
    not even ``include_archived=true`` brings it back; that flag now only ever surfaces rows
    archived BEFORE this change shipped."""
    created, _ = apply_now(type="envelope_create", name="Throwaway")
    envelope_id = created["envelope"]["id"]
    apply_now(type="envelope_delete", envelope_id=envelope_id)

    body, _ = call("list_envelopes")
    assert envelope_id not in {e["id"] for e in body["envelopes"]}

    body_all, _ = call("list_envelopes", include_archived=True)
    assert envelope_id not in {e["id"] for e in body_all["envelopes"]}


def test_create_envelope_propose_and_apply(isolated):
    body, proposed = call("create_envelope", name="Emergency fund", color="#3b86dd")
    assert body is None
    assert proposed["type"] == "envelope_create"
    assert proposed["display"]["op"] == "Create envelope"
    # propose writes nothing
    before, _ = call("list_envelopes")
    assert not any(e["name"] == "Emergency fund" for e in before["envelopes"])

    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True
    assert out["envelope"]["name"] == "Emergency fund"
    assert out["envelope"]["color"] == "#3b86dd"
    assert out["envelope"]["category_ids"] == []


def test_update_envelopes_renames_one_through_a_card(isolated):
    created, _ = apply_now(type="envelope_create", name="Old name")
    envelope_id = created["envelope"]["id"]

    body, proposed = call(
        "update_envelopes", updates=[{"envelope_id": envelope_id, "name": "New name"}]
    )
    assert body is None
    assert proposed["type"] == "envelope_rename"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["envelope"]["name"] == "New name"


def test_merge_envelopes_propose_and_apply(isolated):
    source, _ = apply_now(type="envelope_create", name="Source")
    target, _ = apply_now(type="envelope_create", name="Target")
    source_id, target_id = source["envelope"]["id"], target["envelope"]["id"]

    body, proposed = call(
        "merge_envelopes", source_envelope_id=source_id, target_envelope_id=target_id
    )
    assert body is None
    assert proposed["type"] == "envelope_merge"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["target_envelope_id"] == target_id

    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert envelopes_service.envelope_store.find_by_id(source_id) is None


def test_link_category_propose_and_apply(isolated):
    # A freshly created category is genuinely unlinked (envelope ⊥ category — creating a
    # category mints no envelope), unlike the pre-migrated 'cat_13095ba70000'/'cat_c19c4c0f0000' rows. An
    # opaque id is minted server-side now (add_category no longer takes a caller-chosen
    # `key`) — capture it from the create response.
    created, _ = call("create_categories", label="Concert Tickets", kind="expense")
    cat_id = created["id"]
    pot, _ = apply_now(type="envelope_create", name="Getaway fund")
    pot_id = pot["envelope"]["id"]

    body, proposed = call("link_categories", envelope_id=pot_id, category_ids=[cat_id])
    assert body is None
    assert proposed["type"] == "envelope_add_category"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["envelope"]["category_ids"] == [cat_id]


def test_link_category_refuses_already_linked(isolated):
    created, _ = call("create_categories", label="Concert Tickets", kind="expense")
    cat_id = created["id"]
    pot, _ = apply_now(type="envelope_create", name="Getaway fund")
    other, _ = apply_now(type="envelope_create", name="Other pot")
    _, first_link = call(
        "link_categories", envelope_id=pot["envelope"]["id"], category_ids=[cat_id]
    )
    first_out, _ = call(_confirm_tool(first_link), confirm_token=first_link["tool_use_id"])
    assert first_out["ok"] is True

    _, proposed = call(
        "link_categories", envelope_id=other["envelope"]["id"], category_ids=[cat_id]
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is False and "error" in out  # already belongs to 'pot'


def test_unlink_category_propose_and_apply(isolated):
    envelopes, _ = call("list_envelopes")
    food_env = next(
        e
        for e in envelopes["envelopes"]
        if [c["id"] for c in e["linked_categories"]] == ["cat_13095ba70000"]
    )

    body, proposed = call(
        "unlink_categories", envelope_id=food_env["id"], category_ids=["cat_13095ba70000"]
    )
    assert body is None
    assert proposed["type"] == "envelope_split_category"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["category_id"] == "cat_13095ba70000"

    # the envelope survives, now zero-category (unlink is NOT delete)
    after, _ = call("list_envelopes")
    # Listed without include_archived, so it is live — just zero-category now.
    row = next(e for e in after["envelopes"] if e["id"] == food_env["id"])
    assert row["linked_categories"] == []


def test_delete_envelope_propose_and_apply(isolated):
    created, _ = apply_now(type="envelope_create", name="Emergency fund")
    envelope_id = created["envelope"]["id"]
    apply_now(
        type="assign",
        month="2026-06",
        envelope_id=envelope_id,
        amount=200,
    )

    body, proposed = call("delete_envelope", envelope_id=envelope_id)
    assert body is None
    assert proposed["type"] == "envelope_delete"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True
    assert out["zeroed_assignments"] == {"2026-06": 200.0}

    active, _ = call("list_envelopes")
    assert envelope_id not in {e["id"] for e in active["envelopes"]}


# ── Batch fields (2026-07-19, "act on ONE thing should accept MANY") ──────────────
# Budgeting a month means assigning to every envelope — this proves the whole book assigns
# in ONE call instead of one call per envelope.


def _all_envelope_ids() -> list[str]:
    return [e["id"] for e in envelopes_service.all_envelopes() if not e.get("archived")]


def test_assign_budget_batch_assigns_a_whole_months_budget_in_one_call(isolated):
    env_ids = _all_envelope_ids()
    assert len(env_ids) >= 10  # the fixture's default migrated book — a realistic "whole month"

    body, proposed = call(
        "assign_budget",
        month="2026-06",
        assignments=[{"envelope_id": eid, "amount": 100 + i} for i, eid in enumerate(env_ids)],
    )
    assert body is None
    assert proposed["type"] == "assign_batch"
    assert proposed["display"]["op"] == "Assign (batch)"
    assert proposed["display"]["count"] == len(env_ids)
    assert len(proposed["items"]) == len(env_ids)
    assert all(item["type"] == "assign" for item in proposed["items"])
    # propose writes NOTHING
    table, _ = call("get_budget_month", month="2026-06")
    assert table["ready_to_assign"] == 0.0

    # ONE confirm call books every row
    out, p2 = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert p2 is None
    assert out["ok"] is True
    assert out["count"] == len(env_ids)
    assert out["applied"] == len(env_ids)
    assert all(r["ok"] for r in out["results"])

    table, _ = call("get_budget_month", month="2026-06")
    assigned_by_env = {
        c["envelope_id"]: c["assigned"] for g in table["groups"] for c in g["categories"]
    }
    for i, eid in enumerate(env_ids):
        assert assigned_by_env[eid] == 100 + i


def test_assign_budget_batch_partial_failure_reports_which_rows_failed(isolated):
    good_id = _env("cat_13095ba70000")
    _, proposed = call(
        "assign_budget",
        month="2026-06",
        assignments=[
            {"envelope_id": good_id, "amount": 300},
            {"envelope_id": "env_does_not_exist", "amount": 50},
        ],
    )
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True  # the batch call itself succeeds — per-row status is inside
    assert out["count"] == 2
    assert out["applied"] == 1
    assert out["results"][0]["ok"] is True
    assert out["results"][1]["ok"] is False
    assert "error" in out["results"][1]
    # the good row still booked despite the bad one
    table, _ = call("get_budget_month", month="2026-06")
    food = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food["assigned"] == 300.0


def test_assign_budget_single_form_unchanged_when_no_batch_field(isolated):
    # Byte-identical single-item behavior — no `assignments` present.
    _, proposed = call(
        "assign_budget", month="2026-06", envelope_id=_env("cat_13095ba70000"), amount=800
    )
    assert proposed["type"] == "assign"
    assert "items" not in proposed


def test_move_money_batch_propose_and_apply(isolated):
    a, b = _env("cat_13095ba70000"), _env("cat_db5642ab0000")
    apply_now(type="assign", month="2026-06", envelope_id=a, amount=100)
    apply_now(type="assign", month="2026-06", envelope_id=b, amount=50)

    body, proposed = call(
        "move_money",
        month="2026-06",
        moves=[
            {"from_envelope_id": a, "to_envelope_id": "__rta", "amount": 20},
            {"from_envelope_id": b, "to_envelope_id": "__rta", "amount": 10},
        ],
    )
    assert body is None
    assert proposed["type"] == "move_money_batch"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 2

    table, _ = call("get_budget_month", month="2026-06")
    # Starting from 0 cash: assigning 100+50 drives RTA to -150 (no cash backing it, same as
    # any single assign); releasing 20+10 back to RTA recovers 30 of that.
    assert table["ready_to_assign"] == -120.0


def test_set_envelope_target_batch_propose_and_apply(isolated):
    a, b = _env("cat_13095ba70000"), _env("cat_db5642ab0000")
    body, proposed = call(
        "set_envelope_targets",
        targets=[
            {"envelope_id": a, "type": "monthly", "amount": 500},
            {"envelope_id": b, "type": "yearly", "amount": 1200},
        ],
    )
    assert body is None
    assert proposed["type"] == "set_target_batch"
    assert proposed["display"]["op"] == "Set target (batch)"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 2
    table, _ = call("get_budget_month", month="2026-06")
    by_env = {c["envelope_id"]: c for g in table["groups"] for c in g["categories"]}
    assert by_env[a]["target"]["type"] == "monthly"
    assert by_env[b]["target"]["type"] == "yearly"


def test_rename_envelope_group_batch_propose_and_apply(isolated):
    g1, _ = apply_now(type="group_create", name="Group One")
    g2, _ = apply_now(type="group_create", name="Group Two")
    body, proposed = call(
        "rename_envelope_groups",
        groups=[
            {"group_id": g1["group"]["id"], "name": "Renamed One"},
            {"group_id": g2["group"]["id"], "name": "Renamed Two"},
        ],
    )
    assert body is None
    assert proposed["type"] == "group_rename_batch"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 2
    table, _ = call("get_budget_month", month="2026-06")
    names = {g["id"]: g["name"] for g in table["groups"]}
    assert names[g1["group"]["id"]] == "Renamed One"
    assert names[g2["group"]["id"]] == "Renamed Two"


def test_rename_envelope_groups_single_propose_and_apply_real_edit(isolated):
    # A real single-field edit stays byte-identical: still previews as a plain "group_rename"
    # proposed_change (never a no_op), and applies exactly as before.
    g1, _ = apply_now(type="group_create", name="Solo Group")
    body, proposed = call("rename_envelope_groups", group_id=g1["group"]["id"], name="Renamed Solo")
    assert body is None
    assert proposed["type"] == "group_rename"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True
    table, _ = call("get_budget_month", month="2026-06")
    names = {g["id"]: g["name"] for g in table["groups"]}
    assert names[g1["group"]["id"]] == "Renamed Solo"


def test_rename_envelope_groups_single_no_op_when_no_name_or_collapsed(isolated):
    # Only group_id, no name, no collapsed — nothing this call could change. Must be
    # reported honestly as a no_op, not staged as a card that would echo the group back
    # unchanged once confirmed.
    g1, _ = apply_now(type="group_create", name="Untouched Group")
    body, proposed = call("rename_envelope_groups", group_id=g1["group"]["id"])
    assert proposed is None
    assert body["ok"] is True
    assert body["no_op"] is True
    assert body["group_id"] == g1["group"]["id"]
    table, _ = call("get_budget_month", month="2026-06")
    names = {g["id"]: g["name"] for g in table["groups"]}
    assert names[g1["group"]["id"]] == "Untouched Group"


def test_rename_envelope_groups_batch_all_no_op(isolated):
    g1, _ = apply_now(type="group_create", name="Group A")
    g2, _ = apply_now(type="group_create", name="Group B")
    body, proposed = call(
        "rename_envelope_groups",
        groups=[{"group_id": g1["group"]["id"]}, {"group_id": g2["group"]["id"]}],
    )
    assert proposed is None
    assert body["ok"] is True
    assert body["no_op"] is True
    assert set(body["group_ids"]) == {g1["group"]["id"], g2["group"]["id"]}
    table, _ = call("get_budget_month", month="2026-06")
    names = {g["id"]: g["name"] for g in table["groups"]}
    assert names[g1["group"]["id"]] == "Group A"
    assert names[g2["group"]["id"]] == "Group B"


def test_rename_envelope_groups_batch_partial_no_op_stages_only_the_real_row(isolated):
    g1, _ = apply_now(type="group_create", name="Keep Same")
    g2, _ = apply_now(type="group_create", name="Change Me")
    body, proposed = call(
        "rename_envelope_groups",
        groups=[
            {"group_id": g1["group"]["id"]},  # no name/collapsed — a no-op row
            {"group_id": g2["group"]["id"], "name": "Changed"},
        ],
    )
    assert body is None
    # The dropped no-op row rides on the CHANGE now: a staged call has no result text to
    # carry it, so the card shows it and the approval's own result echoes it back.
    assert proposed["no_op_group_ids"] == [g1["group"]["id"]]
    assert proposed["display"]["no_op_group_ids"] == [g1["group"]["id"]]
    assert proposed["type"] == "group_rename_batch"
    assert [i["group_id"] for i in proposed["items"]] == [g2["group"]["id"]]
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 1
    assert out["no_op_group_ids"] == [g1["group"]["id"]]
    table, _ = call("get_budget_month", month="2026-06")
    names = {g["id"]: g["name"] for g in table["groups"]}
    assert names[g1["group"]["id"]] == "Keep Same"
    assert names[g2["group"]["id"]] == "Changed"


def test_delete_envelope_group_batch_propose_and_apply(isolated):
    g1, _ = apply_now(type="group_create", name="Throwaway One")
    g2, _ = apply_now(type="group_create", name="Throwaway Two")
    body, proposed = call(
        "delete_envelope_groups", group_ids=[g1["group"]["id"], g2["group"]["id"]]
    )
    assert body is None
    assert proposed["type"] == "group_delete_batch"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 2
    table, _ = call("get_budget_month", month="2026-06")
    ids = {g["id"] for g in table["groups"]}
    assert g1["group"]["id"] not in ids
    assert g2["group"]["id"] not in ids


def test_move_envelopes_to_group_moves_several_to_the_same_destination(isolated):
    grp, _ = apply_now(type="group_create", name="Essentials")
    gid = grp["group"]["id"]
    call("get_budget_month", month="2026-06")  # seed Others
    a, b = _env("cat_13095ba70000"), _env("cat_db5642ab0000")
    body, proposed = call("move_envelopes_to_group", envelope_ids=[a, b], group_id=gid)
    assert body is None
    assert proposed["type"] == "move_category_batch"
    out, _ = call(_confirm_tool(proposed), confirm_token=proposed["tool_use_id"])
    assert out["ok"] is True and out["applied"] == 2
    table, _ = call("get_budget_month", month="2026-06")
    grp_now = next(g for g in table["groups"] if g["id"] == gid)
    ids_in_group = {c["envelope_id"] for c in grp_now["categories"]}
    assert {a, b} <= ids_in_group


# ── The confirm card names what it touches (display only) ─────────────────────────────────
# Budget cards used to carry only ids, so a live card read "Assign to env_4c9a…". The staged
# display now carries each envelope / group name beside its id; the args approval re-runs do
# not change.


def _envelope_name(envelope_id: str) -> str:
    return envelopes_service.envelope_store.find_by_id(envelope_id)["name"]


def test_assign_card_names_the_envelope(isolated):
    eid = _env("cat_db5642ab0000")
    _, card = call("assign_budget", envelope_id=eid, amount=100)
    assert card["display"]["envelope_id"] == eid
    assert card["display"]["envelope_name"] == _envelope_name(eid)


def test_a_move_from_ready_to_assign_names_both_ends(isolated):
    eid = _env("cat_db5642ab0000")
    _, card = call("move_money", from_envelope_id="__rta", to_envelope_id=eid, amount=50)
    assert card["display"]["from_envelope_name"] == "Ready to Assign"
    assert card["display"]["to_envelope_name"] == _envelope_name(eid)


def test_batch_rows_and_group_lists_are_named_in_order(isolated):
    a, b = _env("cat_db5642ab0000"), _env("cat_13095ba70000")
    _, card = call(
        "assign_budget",
        assignments=[{"envelope_id": a, "amount": 10}, {"envelope_id": b, "amount": 20}],
    )
    assert [r["envelope_name"] for r in card["display"]["rows"]] == [
        _envelope_name(a),
        _envelope_name(b),
    ]
    gid = _seed_group("Fun", ["cat_db5642ab0000"])
    _, card = call("delete_envelope_groups", group_ids=[gid, "grp_gone"])
    # An id that no longer resolves keeps its place (shown as the id) so the lists align.
    assert card["display"]["group_names"] == ["Fun", "grp_gone"]


def test_an_unknown_envelope_gets_no_invented_name(isolated):
    _, card = call("assign_budget", envelope_id="env_nonexistent", amount=5)
    assert "envelope_name" not in card["display"]


def test_names_never_reach_the_args_an_approval_runs(isolated):
    from backend.db.pending_plan_store import PendingPlanStore

    eid = _env("cat_db5642ab0000")
    call("move_money", from_envelope_id="__rta", to_envelope_id=eid, amount=50)
    staged = PendingPlanStore().get_pending(_TOOL_USE_ID)
    assert not any(k.endswith("_name") for k in staged["args"])
    assert staged["outcome"]["to_envelope_name"] == _envelope_name(eid)


# A real interactive budget write is staged by the DISPATCH gate (budget tools don't stage
# their own card), so the card shows the call's arguments — envelope ids. The named-args preview
# adapter is what puts names on THAT card; it must cover every budget-writing tool.

_BUDGET_WRITE_TOOLS = (
    "assign_budget",
    "move_money",
    "set_envelope_targets",
    "create_envelope_group",
    "rename_envelope_groups",
    "reorder_envelope_groups",
    "delete_envelope_groups",
    "move_envelopes_to_group",
    "reorder_envelopes_in_group",
    "auto_assign",
    "create_envelope",
    "update_envelopes",
    "merge_envelopes",
    "link_categories",
    "unlink_categories",
    "delete_envelope",
)


def test_every_budget_write_tool_previews_names_on_its_gate_card():
    from backend.ai.tools._budget_confirm import NAMED_ARGS_PREVIEW

    missing = [
        t for t in _BUDGET_WRITE_TOOLS if registry.tool_preview_adapter(t) is not NAMED_ARGS_PREVIEW
    ]
    assert missing == []


# The old permission gate's card; permissions_v2 (default on) shows a tool_permission card instead.
@pytest.mark.flags(permissions_v2=False)
def test_the_gate_card_a_user_sees_names_the_envelope(isolated, monkeypatch):
    from backend.ai.tools import _confirm_gate

    monkeypatch.setattr(_confirm_gate, "should_gate", lambda name, level=None: True)
    a, b = _env("cat_db5642ab0000"), _env("cat_13095ba70000")
    _, card = call(
        "assign_budget",
        assignments=[{"envelope_id": a, "amount": 25}, {"envelope_id": b, "amount": 5}],
    )
    assert card["type"] == "tool_permission"
    preview = card["display"]["preview"]
    assert preview["kind"] == "named_args"
    assert [r["envelope_name"] for r in preview["data"]["assignments"]] == [
        _envelope_name(a),
        _envelope_name(b),
    ]
    # The stored args an approval runs are the caller's own — no names in them.
    assert card["display"]["arguments"]["assignments"][0] == {"envelope_id": a, "amount": 25}


def test_a_name_never_blocks_an_approval():
    from backend.ai.tools._budget_confirm import NAMED_ARGS_PREVIEW

    assert NAMED_ARGS_PREVIEW.validate({"kind": "named_args", "data": {}}) is None


def test_move_to_group_names_the_envelope_and_the_group(isolated):
    gid = _seed_group("Fun", [])
    eid = _env("cat_db5642ab0000")
    _, card = call("move_envelopes_to_group", envelope_ids=[eid], group_id=gid)
    # The budget display keeps its pre-envelope key: `category` holds the ENVELOPE id.
    assert card["display"]["category"] == eid
    assert card["display"]["category_name"] == _envelope_name(eid)
    assert card["display"]["group_name"] == "Fun"


def test_a_link_card_names_real_categories(isolated):
    from backend.ai.tools._budget_confirm import NAMED_ARGS_PREVIEW
    from backend.services import categories as categories_service

    row = categories_service.create_category("Coffee Beans")
    preview = NAMED_ARGS_PREVIEW.build(
        {"envelope_id": _env("cat_db5642ab0000"), "category_ids": [row["id"], "cat_gone"]}
    )
    assert preview["data"]["category_names"] == ["Coffee Beans", "cat_gone"]
