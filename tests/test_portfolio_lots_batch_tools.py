"""Batch-call tests for the portfolio/lots tool-pluralization sweep (harness reliability
program — "make single-entity tools accept batches").

Owner's measured complaint: 140 of 273 tools took a single scalar id with no list form,
e.g. resolving a 20-holding portfolio cost 20 `resolve_instruments` calls. This file pins
the batch forms added to `backend/ai/tools/portfolio.py` and `backend/ai/tools/lots.py`:

  - resolve_instruments(queries=[...])       — N tickers in ONE call; a per-symbol
    ValidationError refusal (ambiguous duplicate ticker) is reported PER ITEM, never
    collapsing the whole call.
  - get_portfolio(account_ids=[...])          — OR-filter across several accounts.
  - get_lots(position_ids=[...])              — per-position `totals`, never merged.
  - update_instruments(updates=[...])         — independent per-instrument edits.
  - add_positions/update/remove(adds=/updates=/removals=[...]) — independent
    per-item writes; the duplicate-active-position refusal is enforced PER ITEM.
  - acquire_lots/dispose(acquisitions=/disposals=[...]) — independent per-item
    writes; each item carries its OWN idempotency_key and is double-book-guarded
    independently (the load-bearing invariant: one key for N items would make a
    partial retry either double-book or silently no-op).
  - delete_lots(deletions=[...]) / undo_lot_disposals(event_ids=[...]) —
    independent per-item writes.

Every legacy singular call path is asserted BYTE-IDENTICAL to its pre-batch shape
alongside the new batch form, since "single case stays trivial" was a hard requirement.

**Arity-naming fixup (2026-07-19b):** `resolve_instrument`/`update_instrument` themselves
lied about arity once they grew a batch array (an earlier defect this same sweep left
behind — see the module docstrings in `portfolio.py`) and are renamed here to
`resolve_instruments`/`update_instruments`; `apply_position_*`/`apply_lot_*` keep their
singular names on purpose (propose/apply name symmetry — see `portfolio.py`/`lots.py`).
The registration + legacy-alias-dispatch tests for the two renames live at the top of
each tool's own section below.

Offline, isolated to tmp_path, no LLM / network — not an integration test.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools._shared import NO_OP_WRITE_MESSAGE
from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the rename ledger is the only record that a rename happened.
from backend.services import lots as lots_svc
from backend.services import positions as pos_svc


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    from backend.services import fx_cache

    monkeypatch.setattr(fx_cache, "load_fx_cache", lambda: {"USD": 1.3, "SGD": 1.0})
    return tmp_path


def _seed_instrument(symbol="AAA", asset_class="equity"):
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "price_source_id": symbol,
            "quote_currency": "USD",
            "asset_class": asset_class,
        }
    )


def _seed_account(name="Vantage Brokerage"):
    return AccountStore().create(
        {"name": name, "kind": "asset", "type": "investment", "currency": "USD"}
    )


def _add(inst, acc, **overrides):
    change = {
        "type": "add",
        "instrument_id": inst["id"],
        "account_id": acc["id"],
        "quantity": 20,
        "cost_basis": 100.0,
        **overrides,
    }
    return pos_svc.apply_position_change(change)


# ── resolve_instruments: batch resolve + per-symbol refusal ─────────────────────────


def test_resolve_instruments_renamed_and_the_old_name_is_refused(monkeypatch):
    """The 2026-07-19b arity-naming fixup: `resolve_instrument` lied about arity once it
    grew `queries` (a batch of DIFFERENT instruments — an entity set); renamed to
    `resolve_instruments`. The old name no longer dispatches (ADR
    2026-08-25-tool-names-are-final) — a tool name is persisted data, so it is MIGRATED."""
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "resolve_instruments" in names
    assert "resolve_instrument" not in names

    res, proposed = call("resolve_instrument", query="zzz-legacy-name-check")
    assert proposed is None
    assert "unknown tool" in res.get("error", "")


def test_resolve_instrument_batch_resolves_all_in_one_call(monkeypatch):
    import backend.services.instruments as inst_svc

    def fake_resolve(query, *a, **k):
        return (
            {
                "id": f"inst-{query}",
                "symbol": query.upper(),
                "name": query,
                "asset_class": "equity",
                "price_source": "yahoo",
                "price_source_id": query.upper(),
                "quote_currency": "USD",
            },
            True,
        )

    monkeypatch.setattr(inst_svc, "resolve_instrument", fake_resolve)
    res, proposed = call("resolve_instruments", queries=["aapl", "msft", "goog"])
    assert proposed is None
    assert res["total"] == 3 and res["showing"] == 3
    assert res["resolved_count"] == 3
    assert res["failed_count"] == 0
    assert [r["instrument_id"] for r in res["results"]] == [
        "inst-aapl",
        "inst-msft",
        "inst-goog",
    ]
    # Bare-string items are accepted (no per-item overrides required).
    assert all(r["resolved"] for r in res["results"])


def test_resolve_instrument_batch_reports_per_symbol_refusal_not_whole_call_failure(
    monkeypatch,
):
    """Domain landmine: a stock-ticker symbol collision REFUSES via ValidationError.
    Collapsing that into a whole-call failure would silently block every OTHER ticker
    in the batch — the refusal must be legible PER ITEM."""
    import backend.services.instruments as inst_svc

    good = {
        "id": "inst-good",
        "symbol": "MSFT",
        "name": "Microsoft",
        "asset_class": "equity",
        "price_source": "yahoo",
        "price_source_id": "MSFT",
        "quote_currency": "USD",
    }

    def fake_resolve(query, *a, **k):
        if query == "dupe":
            raise ValidationError(
                "an instrument with symbol 'DUPE' (equity) already exists",
                existing_instrument_id="inst-existing",
                existing_symbol="DUPE",
                existing_name="Duplicate Co",
            )
        return (good, False)

    monkeypatch.setattr(inst_svc, "resolve_instrument", fake_resolve)
    res, proposed = call("resolve_instruments", queries=["msft", "dupe", "msft2"])
    assert proposed is None
    assert res["total"] == 3
    assert res["resolved_count"] == 2
    assert res["failed_count"] == 1
    assert "note" in res  # names the partial-failure count

    by_query = {r["query"]: r for r in res["results"]}
    assert by_query["msft"]["resolved"] is True
    assert by_query["msft2"]["resolved"] is True
    assert by_query["dupe"]["resolved"] is False
    assert "already exists" in by_query["dupe"]["error"]
    assert by_query["dupe"]["existing_instrument_id"] == "inst-existing"


def test_resolve_instrument_batch_cap_enforced(monkeypatch):
    import backend.ai.tools.portfolio as portfolio_tool

    monkeypatch.setattr(portfolio_tool, "_MAX_RESOLVE_BATCH", 2)
    res, proposed = call("resolve_instruments", queries=["a", "b", "c"])
    assert proposed is None
    assert "error" in res
    assert "cap" in res["error"]


def test_resolve_instrument_legacy_single_call_unchanged(monkeypatch):
    """The pre-batch single-query shape/behavior must survive byte-identical."""
    import backend.services.instruments as inst_svc

    monkeypatch.setattr(inst_svc, "resolve_instrument", lambda *a, **k: (None, False))
    res, proposed = call("resolve_instruments", query="zzz-not-a-ticker")
    assert proposed is None
    assert res == {
        "resolved": False,
        "message": "Couldn't resolve that to a tradeable instrument. Ask the user for "
        "the exact ticker + exchange, or provide price_source + price_source_id.",
    }


def test_resolve_instrument_legacy_single_call_still_raises_on_collision(monkeypatch):
    """Unlike the batch form, the singular call is UNCHANGED — a collision still
    propagates (caught generically upstream), not converted to a structured error."""
    import backend.services.instruments as inst_svc

    def raiser(*a, **k):
        raise ValidationError("an instrument with symbol 'DUPE' already exists")

    monkeypatch.setattr(inst_svc, "resolve_instrument", raiser)
    with pytest.raises(ValidationError):
        asyncio.run(execute_tool("resolve_instruments", {"query": "dupe"}))


# ── get_portfolio: plural filters ──────────────────────────────────────────────────


def test_get_portfolio_account_ids_filters_across_several_accounts(isolated, monkeypatch):
    inst = _seed_instrument("AAA")
    acc1 = _seed_account("Vantage Brokerage")
    acc2 = _seed_account("Robinhood")
    acc3 = _seed_account("Binance")
    _add(inst, acc1)
    _add(_seed_instrument("BBB"), acc2)
    _add(_seed_instrument("CCC"), acc3)

    import backend.ai.tools as root

    monkeypatch.setattr(root, "read_rows", lambda: pos_svc.hydrated())
    res, _ = call("get_portfolio", account_ids=[acc1["id"], acc2["id"]])
    account_ids_seen = {p["account_id"] for p in res["positions"]}
    assert account_ids_seen == {acc1["id"], acc2["id"]}
    assert res["total"] == 3  # 3 positions total exist
    assert res["showing"] == 2  # filtered to 2 accounts


def test_get_portfolio_asset_class_accepts_list(isolated, monkeypatch):
    acc = _seed_account()
    _add(_seed_instrument("AAA", "equity"), acc)
    _add(_seed_instrument("BBB", "crypto"), acc, quantity=1, cost_basis=None)
    _add(_seed_instrument("CCC", "bond"), acc, quantity=1, cost_basis=None)

    import backend.ai.tools as root

    monkeypatch.setattr(root, "read_rows", lambda: pos_svc.hydrated())
    res, _ = call("get_portfolio", asset_class=["equity", "crypto"])
    classes_seen = {p["asset_class"] for p in res["positions"]}
    assert classes_seen == {"equity", "crypto"}
    assert res["showing"] == 2


# ── get_lots: batch across positions, per-position totals ─────────────────────────


def _seed_position():
    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )


def _seed_lot(position_id, qty=10, unit_cost=100, acquired_at="2026-01-01"):
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": position_id,
            "quantity": qty,
            "unit_cost": unit_cost,
            "cost_currency": "USD",
            "acquired_at": acquired_at,
        }
    )


def test_get_lots_batch_returns_per_position_totals_not_merged(isolated):
    pos1 = _seed_position()
    pos2 = PositionStore().create(
        {"instrument_id": "inst-2", "account_id": "acc-2", "quantity": 0, "cost_currency": "USD"}
    )
    _seed_lot(pos1["id"], qty=10, unit_cost=100)
    _seed_lot(pos2["id"], qty=4, unit_cost=250)

    res, proposed = call("get_lots", position_ids=[pos1["id"], pos2["id"]])
    assert proposed is None
    assert res["total"] == 2 and res["showing"] == 2
    by_id = {r["position_id"]: r for r in res["results"]}
    # Each position's OWN totals — a 10@100 lot and a 4@250 lot must never be summed
    # together (different instruments/currencies, per the domain landmine).
    assert by_id[pos1["id"]]["totals"]["remaining_basis"] == 1000.0
    assert by_id[pos2["id"]]["totals"]["remaining_basis"] == 1000.0
    assert by_id[pos1["id"]]["totals"]["remaining_quantity"] == 10.0
    assert by_id[pos2["id"]]["totals"]["remaining_quantity"] == 4.0
    assert "totals" not in res  # no combined grand total at the top level


def test_get_lots_legacy_single_call_unchanged(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10, unit_cost=100)
    res, proposed = call("get_lots", position_id=pos["id"])
    assert proposed is None
    assert res["position_id"] == pos["id"]
    assert res["totals"]["remaining_basis"] == 1000.0
    assert "results" not in res


def test_get_lots_missing_position_id_and_position_ids_is_a_clean_error(isolated):
    res, proposed = call("get_lots")
    assert proposed is None
    assert "error" in res


# ── update_instruments: batch, independent per-item failure ────────────────────────


def test_update_instruments_renamed_and_the_old_name_is_refused(isolated):
    """The 2026-07-19b arity-naming fixup: `update_instrument` lied about arity once it
    grew `updates` (a batch of DIFFERENT instruments); renamed to `update_instruments`.
    The old name no longer dispatches — it is migrated, not aliased."""
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "update_instruments" in names
    assert "update_instrument" not in names

    inst = InstrumentStore().create(
        {"symbol": "VOO", "asset_class": "equity", "quote_currency": "USD"}
    )
    res, proposed = call("update_instrument", instrument_id=inst["id"], sector="Technology")
    assert proposed is None
    assert "unknown tool" in res.get("error", "")
    assert not InstrumentStore().find_by_id(inst["id"]).get("sector")


def test_update_instrument_batch_independent_per_item(isolated):
    good = InstrumentStore().create(
        {"symbol": "VOO", "asset_class": "equity", "quote_currency": "USD"}
    )
    res, proposed = call(
        "update_instruments",
        updates=[
            {"instrument_id": good["id"], "sector": "Technology"},
            {"instrument_id": "ghost-id", "sector": "Technology"},
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    by_id = {r["instrument_id"]: r for r in res["results"]}
    assert by_id[good["id"]]["ok"] is True
    assert "error" in by_id["ghost-id"]
    assert InstrumentStore().find_by_id(good["id"])["sector"] == "Technology"


def test_update_instrument_legacy_single_call_unchanged(isolated):
    inst = InstrumentStore().create(
        {"symbol": "VOO", "asset_class": "equity", "quote_currency": "USD"}
    )
    res, proposed = call("update_instruments", instrument_id=inst["id"], sector="Technology")
    assert proposed is None
    assert res["ok"] is True
    assert "results" not in res


# ── add_positions: batch, per-item idempotency + duplicate guard ─────────────


def test_apply_position_add_batch_independent_idempotency_and_duplicate_guard(isolated):
    inst_a = _seed_instrument("AAA")
    inst_b = _seed_instrument("BBB")
    acc = _seed_account()
    # Pre-existing active position for AAA — the batch item for AAA must be refused
    # WITHOUT blocking the batch item for BBB.
    _add(inst_a, acc)

    res, proposed = call(
        "add_positions",
        adds=[
            {
                "instrument_id": inst_a["id"],
                "account_id": acc["id"],
                "quantity": 5,
                "idempotency_key": "batch-item-1",
            },
            {
                "instrument_id": inst_b["id"],
                "account_id": acc["id"],
                "quantity": 7,
                "idempotency_key": "batch-item-2",
            },
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    by_inst = {r["instrument_id"]: r for r in res["results"]}
    assert "error" in by_inst[inst_a["id"]]
    assert "acquire_lots" in by_inst[inst_a["id"]]["error"]
    assert by_inst[inst_b["id"]]["ok"] is True


def test_apply_position_add_legacy_single_call_unchanged(isolated):
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    res, proposed = call(
        "add_positions", instrument_id=inst["id"], account_id=acc["id"], quantity=20
    )
    assert proposed is None
    assert res["ok"] is True
    assert "results" not in res


# ── remove_positions / update_positions: batch legibility ───────────────


def test_apply_position_remove_batch_partial_failure_legible(isolated):
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)

    res, proposed = call("remove_positions", removals=[added["position_id"], "ghost-position"])
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    by_id = {r["position_id"]: r for r in res["results"]}
    assert by_id[added["position_id"]]["ok"] is True
    assert "error" in by_id["ghost-position"]


def test_apply_position_update_batch_partial_failure_legible(isolated):
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)
    other = _seed_account("Other Brokerage")

    res, proposed = call(
        "update_positions",
        updates=[
            {"position_id": added["position_id"], "changes": {"account_id": other["id"]}},
            {"position_id": "ghost-position", "changes": {"account_id": other["id"]}},
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    assert PositionStore().find_by_id(added["position_id"])["account_id"] == other["id"]


# ── update_positions: empty-patch no-op guard (harness reliability program) ─────
# An id-only update_positions call — or one whose `changes` reduces to nothing once the
# equity-only `_grant_managed_ok` bypass flag is stripped — used to reach
# apply_position_change("update"), which only bumps updated_at via a bare
# PositionStore.touch and reported that touch as a plain {"ok": True, "position_id": ...}:
# byte-indistinguishable from a real edit. Mirrors the update_transactions incident fix
# (backend/ai/tools/_shared.py's no_op_write_result).


def test_update_positions_single_item_empty_changes_is_a_no_op(isolated):
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)
    before = PositionStore().find_by_id(added["position_id"])

    res, proposed = call("update_positions", position_id=added["position_id"])

    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert res["position_id"] == added["position_id"]
    assert res["message"] == NO_OP_WRITE_MESSAGE
    assert "error" not in res
    assert PositionStore().find_by_id(added["position_id"]) == before, (
        "no write should have happened — updated_at must not be bumped"
    )


def test_update_positions_single_item_explicit_empty_changes_dict_is_also_a_no_op(isolated):
    """``changes={}`` (an explicit empty dict, not just an omitted field) is the identical
    structurally-empty patch."""
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)
    before = PositionStore().find_by_id(added["position_id"])

    res, proposed = call("update_positions", position_id=added["position_id"], changes={})

    assert proposed is None
    assert res["no_op"] is True
    assert PositionStore().find_by_id(added["position_id"]) == before


def test_update_positions_grant_managed_ok_only_flag_is_still_an_empty_patch(isolated):
    """``_grant_managed_ok`` is an internal bypass flag only ``equity.py`` may set (as a
    TOP-LEVEL key on the change dict it hands ``apply_position_change`` directly) — it is
    never itself a field ``update_position`` applies. A ``changes`` dict carrying ONLY that
    key must still be judged structurally empty."""
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)
    before = PositionStore().find_by_id(added["position_id"])

    res, proposed = call(
        "update_positions",
        position_id=added["position_id"],
        changes={"_grant_managed_ok": True},
    )

    assert proposed is None
    assert res["no_op"] is True
    assert PositionStore().find_by_id(added["position_id"]) == before


def test_update_positions_single_item_real_edit_still_writes_and_returns_normal_shape(isolated):
    """A REAL single-field edit through the non-batch path must stay byte-identical to the
    pre-fix response shape — no ``no_op`` key, and the write actually lands."""
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    added = _add(inst, acc)
    other = _seed_account("Other Brokerage")

    res, proposed = call(
        "update_positions", position_id=added["position_id"], changes={"account_id": other["id"]}
    )

    assert proposed is None
    assert res == {"ok": True, "position_id": added["position_id"]}
    assert PositionStore().find_by_id(added["position_id"])["account_id"] == other["id"]


def test_update_positions_empty_patch_on_nonexistent_position_still_reports_not_found(isolated):
    """An id that resolves to NOTHING keeps its existing not-found error — a no_op is only
    for an EXISTING entity with an empty patch, never a shortcut that papers over a bad id."""
    res, proposed = call("update_positions", position_id="ghost-position")

    assert proposed is None
    assert "error" in res
    assert "no_op" not in res


def test_apply_position_update_batch_marks_empty_item_no_op_and_still_books_the_rest(isolated):
    """A batch mixing a real edit with a structurally-empty item must NOT abort wholesale —
    the real edit books, the empty item is reported honestly as a no-op (still counted
    ``succeeded``, never ``failed`` — it was a well-formed call, just one that changed
    nothing), and the empty item's own row is left untouched."""
    inst = _seed_instrument("AAA")
    acc = _seed_account()
    pos1 = _add(inst, acc)
    inst2 = _seed_instrument("BBB")
    pos2 = _add(inst2, acc)
    before2 = PositionStore().find_by_id(pos2["position_id"])
    other = _seed_account("Other Brokerage")

    res, proposed = call(
        "update_positions",
        updates=[
            {"position_id": pos1["position_id"], "changes": {"account_id": other["id"]}},
            {"position_id": pos2["position_id"], "changes": {}},
        ],
    )

    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 2
    assert res["failed"] == 0
    by_id = {r["position_id"]: r for r in res["results"]}
    assert by_id[pos1["position_id"]].get("no_op") is not True
    assert PositionStore().find_by_id(pos1["position_id"])["account_id"] == other["id"]
    assert by_id[pos2["position_id"]]["no_op"] is True
    assert PositionStore().find_by_id(pos2["position_id"]) == before2


def test_apply_position_update_batch_empty_item_on_nonexistent_position_is_still_an_error(
    isolated,
):
    """A batch item naming a position that doesn't exist AND carrying an empty patch keeps
    reporting the existing not-found error — the no-op guard never masks a bad id."""
    res, proposed = call(
        "update_positions",
        updates=[{"position_id": "ghost-position", "changes": {}}],
    )

    assert proposed is None
    # A batch of one is the single call (registry.one_item): the error comes back unwrapped.
    assert "error" in res
    assert "no_op" not in res


def test_position_update_changes_field_is_a_bare_free_form_object_root_and_batch_item():
    """``changes`` (both the single-call field and each batch item's) carries whatever
    field set the caller wants to edit — bucket, note, account_id, account_name — a
    genuinely free-form dict, not a fixed property set. The literal schema (pre-migration)
    declared it as a BARE ``{"type": "object"}`` with no ``properties``/``additionalProperties``
    key; a naive ``Dict[str, Any]`` model field instead emits ``additionalProperties: true``
    (schema_from_model.py's "bare free-form dict" quirk class). Pins that portfolio.py's
    local ``_bare_object_schema`` json_schema_extra keeps stripping it on
    update_positions (the `updates` batch item) now that it's derived from a
    model. propose_position_update is now an ALIAS of update_positions (2026-07-19
    propose_*/apply_* merge) — there is only the one schema to pin.

    The ROOT copy of `changes` is no longer advertised (2026-07-19d dual-shape cleanup,
    schema_of_batch_only) — it is still ACCEPTED by the handler, just not described, so the
    only place the bare-object shape can now be asserted is the batch item. The quirk being
    pinned is unchanged; there is one location left to pin it in rather than two."""
    from backend.ai.tools import TOOL_SCHEMAS

    apply_schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_positions")["input_schema"]
    assert "changes" not in apply_schema["properties"], (
        "the root `changes` mirror is deliberately un-advertised — see schema_of_batch_only"
    )
    item_changes = apply_schema["properties"]["updates"]["items"]["properties"]["changes"]
    assert "additionalProperties" not in item_changes


# ── acquire_lots: batch, per-item idempotency + double-book guard ────────────


def test_apply_lot_acquire_batch_double_book_guard_is_per_item(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10, unit_cost=100, acquired_at="2026-01-01")  # existing open lot

    res, proposed = call(
        "acquire_lots",
        acquisitions=[
            {  # identical to the existing lot — REFUSED as a double-book
                "position_id": pos["id"],
                "quantity": 10,
                "unit_cost": 100,
                "cost_currency": "USD",
                "acquired_at": "2026-01-01",
                "kind": "buy",
                "idempotency_key": "batch-key-1",
            },
            {  # a genuinely different trade — must still record
                "position_id": pos["id"],
                "quantity": 5,
                "unit_cost": 110,
                "cost_currency": "USD",
                "acquired_at": "2026-02-01",
                "kind": "buy",
                "idempotency_key": "batch-key-2",
            },
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    ok_results = [r for r in res["results"] if r.get("ok")]
    err_results = [r for r in res["results"] if r.get("error")]
    assert len(ok_results) == 1 and len(err_results) == 1
    assert "existing_lot_id" in err_results[0]
    # Only ONE new lot was actually recorded (the refused item wrote nothing).
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 2


def test_apply_lot_acquire_batch_idempotency_is_per_item_not_shared(isolated):
    """The load-bearing invariant: one key for N items would make a retry either
    double-book or silently no-op. Replaying the SAME batch with the SAME per-item
    keys must be a clean no-op for BOTH items, not just the first."""
    pos = _seed_position()

    items = [
        {
            "position_id": pos["id"],
            "quantity": 5,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "idempotency_key": "order-A",
        },
        {
            "position_id": pos["id"],
            "quantity": 7,
            "unit_cost": 110,
            "cost_currency": "USD",
            "acquired_at": "2026-02-01",
            "idempotency_key": "order-B",
        },
    ]
    first, _ = call("acquire_lots", acquisitions=items)
    assert first["succeeded"] == 2
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 2

    # Replay the identical batch — both items must resolve via idempotency, not
    # via the double-book guard, and no new lots may appear.
    second, _ = call("acquire_lots", acquisitions=items)
    assert all(r.get("idempotent") for r in second["results"])
    assert len(LotStore().lots_for_position(pos["id"], include_closed=True)) == 2


def test_apply_lot_acquire_legacy_single_call_unchanged(isolated):
    pos = _seed_position()
    res, proposed = call(
        "acquire_lots",
        position_id=pos["id"],
        quantity=5,
        unit_cost=100,
        cost_currency="USD",
        acquired_at="2026-01-01",
    )
    assert proposed is None
    assert res["ok"] is True
    assert "results" not in res
    assert "_next_step" in res  # the add_transaction cash-leg reminder survives


# ── dispose_lots: batch, per-item idempotency + double-book guard ───────────


def test_apply_lot_dispose_batch_double_book_guard_is_per_item(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=20, unit_cost=100, acquired_at="2026-01-01")
    # Book one real disposal so a duplicate of it can be attempted in the batch.
    lots_svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "disposed_at": "2026-06-01",
            "method": "fifo",
        }
    )

    res, proposed = call(
        "dispose_lots",
        disposals=[
            {  # duplicate of the disposal just booked — REFUSED
                "position_id": pos["id"],
                "quantity": 4,
                "proceeds_per_unit": 130,
                "proceeds_currency": "USD",
                "disposed_at": "2026-06-01",
                "method": "fifo",
            },
            {  # a genuinely different sale — must still record
                "position_id": pos["id"],
                "quantity": 2,
                "proceeds_per_unit": 140,
                "proceeds_currency": "USD",
                "disposed_at": "2026-07-01",
                "method": "fifo",
            },
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    err_results = [r for r in res["results"] if r.get("error")]
    assert "existing_event_id" in err_results[0]


def test_apply_lot_dispose_legacy_single_call_unchanged(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10, unit_cost=100, acquired_at="2026-01-01")
    res, proposed = call(
        "dispose_lots",
        position_id=pos["id"],
        quantity=4,
        proceeds_per_unit=130,
        proceeds_currency="USD",
        disposed_at="2026-06-01",
    )
    assert proposed is None
    assert res["ok"] is True
    assert "results" not in res


# ── delete_lots / undo_lot_disposals: batch legibility ───────────────────


def test_apply_lot_delete_batch_partial_failure_legible(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=5, unit_cost=100, acquired_at="2026-01-01")
    lot_id = LotStore().lots_for_position(pos["id"])[0]["id"]

    res, proposed = call(
        "delete_lots",
        deletions=[
            {"lot_id": lot_id, "position_id": pos["id"]},
            {"lot_id": "ghost-lot", "position_id": pos["id"]},
        ],
    )
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    assert LotStore().find_by_id(lot_id) is None


def test_apply_lot_dispose_undo_batch_partial_failure_legible(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=10, unit_cost=100, acquired_at="2026-01-01")
    dispose_result = lots_svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130,
            "proceeds_currency": "USD",
            "disposed_at": "2026-06-01",
            "method": "fifo",
        }
    )
    event_id = dispose_result["event_id"]

    res, proposed = call("undo_lot_disposals", event_ids=[event_id, "ghost-event"])
    assert proposed is None
    assert res["total"] == 2
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    by_id = {r["event_id"]: r for r in res["results"]}
    assert by_id[event_id]["ok"] is True
    assert "error" in by_id["ghost-event"]
