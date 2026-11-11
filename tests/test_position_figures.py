"""A position's quantity is derived from its records — one writer, never edited independently.

ADR 2026-10-07-a-position-quantity-is-derived-from-its-records. Synthetic data only.

Every bug in this family came from a path that kept its own copy of a position's quantity: a
lots read that seeded a placeholder lot (KSTL, 2026-10-07), a removal that left lots held, a
move that left lots behind under a settlement wallet, ± arithmetic at vest/park/undo. These
tests pin the records each kind of position follows, and — as a property over random books —
that no sequence of writes can make the stored figure disagree with them.
"""

from __future__ import annotations

import pathlib
import random
import re

import pytest

from backend.core.errors import DataIntegrityError, OpenNetWorthError, ValidationError
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore, vesting_state_of
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as equity_service
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import position_figures
from backend.services import positions as positions_service

_SGD = lambda *a, **k: "SGD"  # noqa: E731
_ID = lambda a, c, b=None, rates=None: round(float(a or 0), 2)  # noqa: E731


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(equity_service, "grants", GrantStore())
    monkeypatch.setattr(equity_service, "vest_events", VestEventStore())
    monkeypatch.setattr(equity_service, "instruments", InstrumentStore())
    monkeypatch.setattr(equity_service, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    for module in (equity_service, lots_service, ledger_service):
        monkeypatch.setattr(module, "base_currency", _SGD)
    monkeypatch.setattr("backend.core.fx.base_currency", _SGD)
    monkeypatch.setattr("backend.services.equity_release.base_currency", _SGD)
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr("backend.services.ledger.to_base", _ID)
    monkeypatch.setattr(equity_service, "to_base", _ID)
    return tmp_path


def _instrument(symbol: str = "ACME") -> dict:
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "asset_class": "equity",
            "quote_currency": "SGD",
            "price_source": "manual",
            "price_source_id": symbol,
        }
    )


def _account(name: str = "Broker") -> dict:
    return AccountStore().create(
        {"name": name, "kind": "asset", "type": "investment", "currency": "SGD"}
    )


def _grant(inst, acct, *, release_required: bool) -> dict:
    equity_service.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "grant_date": "2024-01-01",
            "total_units": 600.0,
            "release_required": release_required,
            "tranches": [
                {"vest_date": "2025-01-01", "units": 200.0},
                {"vest_date": "2025-07-01", "units": 200.0},
                {"vest_date": "2026-01-01", "units": 200.0},
            ],
        }
    )
    return GrantStore().all()[-1]


def _vest(grant_id: str, tranche_id: str, **extra) -> dict:
    return equity_service.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant_id,
            "tranche_id": tranche_id,
            "fmv_per_unit": 30.0,
            "fmv_currency": "SGD",
            **extra,
        }
    )


def _qty(position_id: str) -> float:
    return float(PositionStore().find_by_id(position_id)["quantity"])


def _assert_records_hold() -> None:
    """The invariant itself: every position's stored figures equal what its records imply,
    every live lot sits under its position's account, and an owned position's quantity is
    Σ its open lots."""
    assert position_figures.check_parity() == []
    positions = {p["id"]: p for p in PositionStore().all()}
    grant_rows = {
        g.get(k) for g in GrantStore().all() for k in ("locked_position_id", "pending_position_id")
    }
    for lot in LotStore().all():
        pos = positions.get(lot["position_id"])
        if pos is not None:
            assert lot["account_id"] == pos["account_id"], lot
    for pos in positions.values():
        if pos["id"] in grant_rows or vesting_state_of(pos) == "unvested":
            continue
        held = sum(lot["remaining_qty"] for lot in LotStore().open_lots_for_position(pos["id"]))
        assert float(pos["quantity"]) == pytest.approx(held), pos


# ── Vest / release / undo ─────────────────────────────────────────────────────────


def test_one_step_vest_and_undo_follow_the_grant_and_the_lot(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)
    locked = grant["locked_position_id"]
    assert _qty(locked) == 600.0

    ev = _vest(grant["id"], grant["tranches"][0]["id"])
    liquid = LotStore().find_by_id(ev["lot_id"])["position_id"]
    assert _qty(locked) == 400.0
    assert _qty(liquid) == 200.0
    _assert_records_hold()

    equity_service.apply_equity_change({"type": "vest_undo", "vest_event_id": ev["vest_event_id"]})
    assert _qty(locked) == 600.0
    assert _qty(liquid) == 0.0
    _assert_records_hold()


def test_two_step_vest_parks_then_release_delivers_then_undo_restores(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=True)
    locked = grant["locked_position_id"]

    ev = _vest(grant["id"], grant["tranches"][0]["id"])
    pending = GrantStore().find_by_id(grant["id"])["pending_position_id"]
    assert (_qty(locked), _qty(pending)) == (400.0, 200.0)
    assert LotStore().lots_for_position(pending) == [], "parked units are not lots"
    assert PositionStore().find_by_id(pending)["cost_basis"] == pytest.approx(30.0)
    _assert_records_hold()

    released = equity_service.apply_equity_change(
        {"type": "release_apply", "vest_event_id": ev["vest_event_id"]}
    )
    liquid = released["position_id"]
    assert (_qty(locked), _qty(pending), _qty(liquid)) == (400.0, 0.0, 200.0)
    _assert_records_hold()

    equity_service.apply_equity_change({"type": "vest_undo", "vest_event_id": ev["vest_event_id"]})
    assert (_qty(locked), _qty(pending), _qty(liquid)) == (600.0, 0.0, 0.0)
    _assert_records_hold()


def test_undo_of_a_still_parked_vest_drains_the_pending_position(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=True)
    ev = _vest(grant["id"], grant["tranches"][0]["id"])
    pending = GrantStore().find_by_id(grant["id"])["pending_position_id"]

    equity_service.apply_equity_change({"type": "vest_undo", "vest_event_id": ev["vest_event_id"]})

    assert _qty(pending) == 0.0
    assert PositionStore().find_by_id(pending)["cost_basis"] is None
    assert _qty(grant["locked_position_id"]) == 600.0
    _assert_records_hold()


def test_a_partial_vest_leaves_the_remainder_unvested(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)

    _vest(grant["id"], grant["tranches"][0]["id"], units=50.0)

    assert _qty(grant["locked_position_id"]) == 550.0
    _assert_records_hold()


def test_cancelling_a_grant_forfeits_its_unvested_units(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)
    ev = _vest(grant["id"], grant["tranches"][0]["id"])

    equity_service.apply_equity_change({"type": "grant_cancel", "grant_id": grant["id"]})

    assert _qty(grant["locked_position_id"]) == 0.0
    liquid = LotStore().find_by_id(ev["lot_id"])["position_id"]
    assert _qty(liquid) == 200.0, "shares already vested are the holder's"
    _assert_records_hold()


def test_editing_a_grants_tranches_moves_its_unvested_units(isolated):
    """Until now a tranche edit left the locked position's quantity stale."""
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)
    tranches = [{**t} for t in grant["tranches"]]
    tranches[2]["units"] = 50.0

    equity_service.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "tranches": tranches}
    )

    assert _qty(grant["locked_position_id"]) == 450.0
    _assert_records_hold()


# ── Adds, edits, moves, removals ─────────────────────────────────────────────────


def test_an_undated_add_is_an_opening_balance_dated_today(isolated):
    inst, acct = _instrument(), _account()

    res = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 12}
    )

    lot = LotStore().find_by_id(res["lot_id"])
    assert (lot["kind"], lot["acquired_at"]) == ("opening", user_today().isoformat())
    assert "set it on this lot" in res["note"]
    assert _qty(res["position_id"]) == 12.0
    _assert_records_hold()


def test_an_add_whose_lot_fails_leaves_no_empty_shell(isolated, monkeypatch):
    inst, acct = _instrument(), _account()
    monkeypatch.setattr(lots_service, "apply_lot_change", lambda change: {"error": "boom"})

    res = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 5}
    )

    assert res == {"error": "boom"}
    assert PositionStore().all() == []


def test_no_figure_is_editable_through_the_position_choke(isolated):
    inst, acct = _instrument(), _account()
    pid = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 5}
    )["position_id"]

    for change in ({"quantity": 6}, {"cost_basis": 1.0}, {"cost_currency": "USD"}):
        with pytest.raises(ValidationError, match="derived from its records"):
            positions_service.apply_position_change(
                {"type": "update", "position_id": pid, "changes": change}
            )
    with pytest.raises(ValidationError, match="grant"):
        positions_service.update_position(pid, {"vesting_state": "unvested"})
    with pytest.raises(DataIntegrityError):
        PositionStore().touch(pid, {"quantity": 6})
    assert _qty(pid) == 5.0


def test_moving_a_position_moves_every_lot_with_it(isolated):
    inst, broker, wallet = _instrument(), _account("Broker"), _account("Broker (USD)")
    pid = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": wallet["id"],
            "quantity": 10,
            "cost_basis": 5.0,
            "acquired_at": "2026-01-02",
        }
    )["position_id"]
    lots_service.apply_lot_change(
        {"type": "acquire", "position_id": pid, "quantity": 4, "acquired_at": "2026-02-02"}
    )
    lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": 10,
            "proceeds_per_unit": 6.0,
            "proceeds_currency": "SGD",
            "method": "fifo",
            "disposed_at": "2026-03-01",
        }
    )  # the first lot is now closed — it must move too

    positions_service.update_position(pid, {"account_id": broker["id"]})

    lots = LotStore().lots_for_position(pid, include_closed=True)
    assert len(lots) == 2
    assert {lot["account_id"] for lot in lots} == {broker["id"]}
    _assert_records_hold()


def test_removing_a_position_leaves_nothing_counted(isolated):
    inst, acct = _instrument(), _account()
    pid = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 3}
    )["position_id"]

    assert positions_service.apply_position_change({"type": "remove", "position_id": pid})["ok"]

    assert LotStore().all() == []
    _assert_records_hold()


def test_a_legacy_holding_keeps_its_figures_through_a_vest_into_it(isolated):
    """A pre-lots holding (as revision 202610111200 leaves it: one opening lot of exactly
    its figures) that a vest lands on keeps its own shares and basis beside the vest's."""
    inst, acct = _instrument(), _account()
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 7, "cost_basis": 10}
    )
    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "original_qty": 7,
            "unit_cost": 10.0,
            "cost_currency": "SGD",
            "kind": "opening",
        }
    )
    grant = _grant(inst, acct, release_required=False)

    _vest(grant["id"], grant["tranches"][0]["id"])

    assert _qty(pos["id"]) == 207.0
    assert PositionStore().find_by_id(pos["id"])["cost_basis"] == pytest.approx(
        (7 * 10 + 200 * 30) / 207
    )
    _assert_records_hold()


# ── Boot reconcile ───────────────────────────────────────────────────────────────


def test_reconcile_corrects_planted_drift_and_tells_the_user(isolated):
    from backend.db import NotificationLogStore

    inst, acct = _instrument(), _account()
    pid = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 8}
    )["position_id"]
    PositionStore().update_by_id(pid, {"quantity": 99.0})  # a raw write / an old backup

    drift = position_figures.reconcile()

    assert [d["position_id"] for d in drift] == [pid]
    assert _qty(pid) == 8.0
    notes = [n for n in NotificationLogStore().all() if n.get("trigger") == "data_repair"]
    assert "ACME 99 → 8" in notes[-1]["summary"]
    assert position_figures.reconcile() == []  # idempotent


# ── Returns: an opening balance is not a purchase at an old cost ─────────────────


def test_a_dated_opening_lot_enters_returns_at_market_value(isolated, monkeypatch):
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import returns

    inst = _instrument("OPEN")
    PriceHistoryStore().record("OPEN", 80.0, day="2026-09-10", source="manual")
    LotStore().create(
        {
            "position_id": "pos-x",
            "instrument_id": inst["id"],
            "account_id": "acc-x",
            "acquired_at": "2026-09-10",
            "original_qty": 100,
            "unit_cost": 50.0,  # bought years ago at 50 — not money spent this month
            "cost_currency": "SGD",
            "kind": "opening",
        }
    )

    flows, unknown = returns.tagged_cash_flows("2026-09-01", "2026-09-30", "SGD", rates={"SGD": 1})

    assert flows == [("2026-09-10", -8000.0, inst["id"])]
    assert unknown is False


# ── Guards: one writer ──────────────────────────────────────────────────────────


def test_only_position_figures_writes_the_figures():
    backend = pathlib.Path(__file__).resolve().parents[1] / "backend"
    writers = sorted(
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if re.search(r"\.write_figures\(", path.read_text()) and path.name != "position_store.py"
    )
    assert writers == ["services/position_figures.py"]


def test_only_the_position_service_creates_position_rows():
    backend = pathlib.Path(__file__).resolve().parents[1] / "backend"
    creators = sorted(
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if re.search(r"PositionStore\(\)\.create\(", path.read_text())
    )
    assert creators == ["services/positions.py"]


# ── Property: no sequence of writes can make the figure disagree with its records ──


class _Book:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.insts = [_instrument("AAA"), _instrument("BBB")]
        self.accts = [_account("Broker A"), _account("Broker B")]
        self.grants = [
            _grant(self.insts[1], self.accts[0], release_required=False),
            _grant(self.insts[1], self.accts[1], release_required=True),
        ]
        self.day = 0

    def date(self) -> str:
        self.day += 1
        return f"2026-{1 + (self.day // 28) % 12:02d}-{1 + self.day % 28:02d}"

    def owned(self) -> list[dict]:
        grant_rows = {
            g.get(k)
            for g in GrantStore().all()
            for k in ("locked_position_id", "pending_position_id")
        }
        return [p for p in PositionStore().all() if p["id"] not in grant_rows]

    def pick(self, items):
        return self.rng.choice(items) if items else None

    # Each op is one user/agent write through its choke; refusals are legitimate outcomes.
    def add(self):
        dated = self.rng.random() < 0.5
        positions_service.apply_position_change(
            {
                "type": "add",
                "instrument_id": self.pick(self.insts)["id"],
                "account_id": self.pick(self.accts)["id"],
                "quantity": self.rng.randint(1, 50),
                "cost_basis": self.rng.choice([None, 10.0, 12.5]),
                **({"acquired_at": self.date()} if dated else {}),
            }
        )

    def acquire(self):
        pos = self.pick(self.owned())
        if pos:
            lots_service.apply_lot_change(
                {
                    "type": "acquire",
                    "position_id": pos["id"],
                    "quantity": self.rng.randint(1, 20),
                    "unit_cost": 11.0,
                    "cost_currency": "SGD",
                    "acquired_at": self.date(),
                    "allow_duplicate": True,
                }
            )

    def dispose(self):
        pos = self.pick([p for p in self.owned() if float(p["quantity"]) > 0])
        if pos:
            lots_service.apply_lot_change(
                {
                    "type": "dispose",
                    "position_id": pos["id"],
                    "quantity": self.rng.randint(1, int(float(pos["quantity"])) or 1),
                    "proceeds_per_unit": 13.0,
                    "proceeds_currency": "SGD",
                    "method": "fifo",
                    "disposed_at": self.date(),
                    "allow_duplicate": True,
                }
            )

    def undo_dispose(self):
        ev = self.pick(RealizedEventStore().query_events())
        if ev:
            lots_service.apply_lot_change({"type": "dispose_undo", "event_id": ev["id"]})

    def delete_lot(self):
        lot = self.pick([lot for lot in LotStore().all() if lot.get("kind") != "grant"])
        if lot:
            lots_service.apply_lot_change({"type": "delete", "lot_id": lot["id"]})

    def amend_lot(self):
        lot = self.pick(LotStore().all())
        if lot:
            lots_service.apply_lot_change(
                {"type": "amend", "lot_id": lot["id"], "quantity": self.rng.randint(1, 30)}
            )

    def move(self):
        pos = self.pick(PositionStore().all())
        if pos:
            positions_service.update_position(
                pos["id"], {"account_id": self.pick(self.accts)["id"]}
            )

    def remove(self):
        pos = self.pick(self.owned())
        if pos:
            positions_service.apply_position_change({"type": "remove", "position_id": pos["id"]})

    def vest(self):
        grant = GrantStore().find_by_id(self.pick(self.grants)["id"])
        tranche = self.pick([t for t in grant["tranches"] if not t.get("vested")])
        if tranche and grant["status"] == "active":
            _vest(grant["id"], tranche["id"], units=self.rng.choice([None, 25.0]) or None)

    def release(self):
        ev = self.pick(VestEventStore().query_pending_releases())
        if ev:
            equity_service.apply_equity_change({"type": "release_apply", "vest_event_id": ev["id"]})

    def undo_vest(self):
        ev = self.pick(VestEventStore().all())
        if ev:
            equity_service.apply_equity_change({"type": "vest_undo", "vest_event_id": ev["id"]})

    def cancel(self):
        if self.rng.random() < 0.15:
            grant = self.pick(self.grants)
            equity_service.apply_equity_change({"type": "grant_cancel", "grant_id": grant["id"]})


_OPS = [
    "add",
    "acquire",
    "acquire",
    "dispose",
    "dispose",
    "undo_dispose",
    "delete_lot",
    "amend_lot",
    "move",
    "remove",
    "vest",
    "vest",
    "release",
    "undo_vest",
    "cancel",
]


@pytest.mark.parametrize("seed", range(12))
def test_no_sequence_of_writes_breaks_the_records_invariant(isolated, seed):
    rng = random.Random(seed)
    book = _Book(rng)
    _assert_records_hold()
    for _ in range(40):
        op = rng.choice(_OPS)
        try:
            getattr(book, op)()
        except OpenNetWorthError:
            pass  # a refused write is a legitimate outcome — it must leave the book intact
        _assert_records_hold()


# ── Review findings (2026-10-08) ─────────────────────────────────────────────────


def test_an_add_whose_lot_raises_leaves_no_empty_shell(isolated, monkeypatch):
    inst, acct = _instrument(), _account()

    def _boom(change):
        raise RuntimeError("lot write failed")

    monkeypatch.setattr(lots_service, "apply_lot_change", _boom)

    with pytest.raises(RuntimeError, match="lot write failed"):
        positions_service.apply_position_change(
            {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 5}
        )
    assert PositionStore().all() == []


def test_a_negative_add_is_refused_and_empty_adds_reuse_one_shell(isolated):
    inst, acct = _instrument(), _account()
    add = {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"]}

    assert "positive" in positions_service.apply_position_change({**add, "quantity": -5})["error"]
    first = positions_service.apply_position_change({**add, "quantity": 0, "idempotency_key": "k"})
    again = positions_service.apply_position_change({**add, "quantity": 0, "idempotency_key": "k"})

    assert first["position_id"] == again["position_id"]
    assert len(PositionStore().all()) == 1 and LotStore().all() == []


def test_a_hand_made_unvested_row_with_no_grant_is_never_zeroed(isolated):
    """Its stored number is its only record (add/update could make one before 2026-10-08)."""
    inst, acct = _instrument(), _account()
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 37,
            "vesting_state": "unvested",
        }
    )

    assert position_figures.reconcile() == []
    assert position_figures.rederive(pos["id"]) == {}
    assert _qty(pos["id"]) == 37.0


def test_a_tranche_edit_keeps_vest_state_and_never_recounts_vested_units(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)
    _vest(grant["id"], grant["tranches"][0]["id"])
    stored = GrantStore().find_by_id(grant["id"])["tranches"]
    # The shape a model sends: ids, dates and units only — no vested flags.
    edit = [{"id": t["id"], "vest_date": t["vest_date"], "units": t["units"]} for t in stored]
    edit[2]["units"] = 150.0

    equity_service.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "tranches": edit}
    )

    assert _qty(grant["locked_position_id"]) == 350.0  # 200 + 150 unvested, not 550
    _assert_records_hold()


def test_a_vested_tranche_cannot_be_resized_dropped_or_flagged_vested_by_an_edit(isolated):
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=False)
    _vest(grant["id"], grant["tranches"][0]["id"])
    stored = GrantStore().find_by_id(grant["id"])["tranches"]
    base = [{"id": t["id"], "vest_date": t["vest_date"], "units": t["units"]} for t in stored]

    with pytest.raises(ValidationError, match="undo its vest"):
        equity_service.apply_equity_change(
            {
                "type": "grant_update",
                "grant_id": grant["id"],
                "tranches": [{**base[0], "units": 1}, *base[1:]],
            }
        )
    with pytest.raises(ValidationError, match="undo its vest"):
        equity_service.apply_equity_change(
            {"type": "grant_update", "grant_id": grant["id"], "tranches": base[1:]}
        )
    flagged = [base[0], {**base[1], "vested": True}, base[2]]
    equity_service.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "tranches": flagged}
    )
    assert _qty(grant["locked_position_id"]) == 400.0, "only a recorded vest makes units vested"
    _assert_records_hold()


def test_only_an_unvested_lot_less_row_can_be_adopted_by_a_grant(isolated):
    inst, acct = _instrument(), _account()
    owned = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acct["id"], "quantity": 9}
    )["position_id"]

    with pytest.raises(ValidationError, match="can be adopted"):
        equity_service.apply_equity_change(
            {
                "type": "grant_create",
                "instrument_id": inst["id"],
                "total_units": 100.0,
                "tranches": [{"vest_date": "2027-01-01", "units": 100.0}],
                "locked_position_id": owned,
            }
        )
    assert _qty(owned) == 9.0
    _assert_records_hold()


def test_a_funded_opening_lot_is_a_purchase_at_cost_in_returns(isolated):
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import returns

    inst = _instrument("FUND")
    PriceHistoryStore().record("FUND", 80.0, day="2026-09-10", source="manual")
    TransactionStore().upsert(
        {
            "id": "txn-cash-out",
            "date": "2026-09-10",
            "kind": "investment",
            "source": "manual",
            "description": "Share purchase",
        }
    )
    LotStore().create(
        {
            "position_id": "pos-y",
            "instrument_id": inst["id"],
            "account_id": "acc-y",
            "acquired_at": "2026-09-10",
            "original_qty": 100,
            "unit_cost": 50.0,
            "cost_currency": "SGD",
            "kind": "opening",
            "txn_id": "txn-cash-out",  # real money left an account for it
        }
    )

    flows, _ = returns.tagged_cash_flows("2026-09-01", "2026-09-30", "SGD", rates={"SGD": 1})

    assert flows == [("2026-09-10", -5000.0, inst["id"])]


def test_a_move_onto_an_existing_holding_is_refused_with_the_transfer_route(isolated):
    """Found 2026-10-09 (a KSTL release moved to IBKR, which already held KSTL): the move left
    TWO liquid positions of one instrument in one account. Shares landing on an existing
    holding are a transfer, and the refusal says how to record it."""
    inst, a, b = _instrument(), _account("A"), _account("B")
    add = lambda acct: positions_service.apply_position_change(  # noqa: E731
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 5,
            "acquired_at": "2025-01-02",
        }
    )["position_id"]
    pa, pb = add(a), add(b)

    with pytest.raises(ValidationError, match="record a transfer"):
        positions_service.update_position(pa, {"account_id": b["id"]})

    assert PositionStore().find_by_id(pa)["account_id"] == a["id"]
    in_b = [p for p in PositionStore().all() if p["account_id"] == b["id"]]
    assert [p["id"] for p in in_b] == [pb]
    _assert_records_hold()


def test_moving_a_grant_moves_its_awaiting_delivery_units_too(isolated):
    """Until 2026-10-09 a grant's account move relocated the unvested row but left the
    vested-not-yet-delivered row at the old custodian."""
    inst, old, new = _instrument(), _account("Old Custodian"), _account("New Custodian")
    grant = _grant(inst, old, release_required=True)
    _vest(grant["id"], grant["tranches"][0]["id"])
    grant = GrantStore().find_by_id(grant["id"])

    equity_service.apply_equity_change(
        {"type": "grant_update", "grant_id": grant["id"], "account_id": new["id"]}
    )

    for key in ("locked_position_id", "pending_position_id"):
        assert PositionStore().find_by_id(grant[key])["account_id"] == new["id"], key
    assert _qty(grant["pending_position_id"]) == 200.0
    _assert_records_hold()


def test_a_lot_on_a_grant_holding_is_reported_at_boot_and_left_alone(isolated, caplog):
    """2026-10-11: the old lots read seeded a lot onto a pending-release holding. Migration
    202610111300 removes that exact shape; any other lot found there is reported, not deleted,
    and moves no figure (the holding follows its vests)."""
    inst, acct = _instrument(), _account()
    grant = _grant(inst, acct, release_required=True)
    _vest(grant["id"], grant["tranches"][0]["id"])
    pending = GrantStore().find_by_id(grant["id"])["pending_position_id"]
    LotStore().create(
        {
            "position_id": pending,
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "kind": "opening",
            "original_qty": 200.0,
            "remaining_qty": 150.0,
        }
    )

    with caplog.at_level("WARNING", logger="backend.services.position_figures"):
        assert position_figures.reconcile() == []

    (stray,) = position_figures.stray_grant_lots()
    assert stray["position_id"] == pending
    assert any("sits on grant holding" in r.getMessage() for r in caplog.records)
    assert _qty(pending) == 200.0
    assert LotStore().lots_for_position(pending), "reported, never deleted"
