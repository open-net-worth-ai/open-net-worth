"""A cross-currency transfer whose two legs imply an impossible exchange rate is REFUSED
at the ledger write choke, whatever produced it.

WHY THIS EXISTS. The 2026-08-23 incident (₫300,000,000 booked as S$300,000,000 on an SGD
receivable) was caused by one caller — `ai/tools/_shared.py::_txn_data_from_ai` — splitting
a caller's `amount` from its `currency` across two legs. That mapping is fixed and pinned
by FIX A3 in `tests/test_currency_atomic.py`. This file is the OTHER half: the same shape
arriving from ANY caller (REST, CSV/OFX/QIF import, a future tool, a hand-built dict) is
refused at `resolve_transaction_row` — the resolution half both the preview and the write
run through — so the bug class is unrepresentable rather than fixed in one place.

WHY A RATE COMPARISON AND NOT AN EQUALITY CHECK. "Equal amounts in different currencies"
catches only an EXACT copy: a caller that copies-then-rounds, or converts with a rate wrong
by 1000x, sails past it. It is also permanently wrong for a genuinely 1:1 pair (a USD↔USDC
wallet transfer). Comparing the IMPLIED rate against the KNOWN one catches the whole
magnitude-error class and has no 1:1 blind spot.

WHY THE TOLERANCE IS AN ORDER OF MAGNITUDE. The bug class is 1,000x–20,000x. A real
transfer's error sources are a bank spread, a stale cache, or a historical transfer entered
at its own day's rate — percentages, not multiples. 10x sits far outside anything real and
far inside the bug, so the guard cannot fire on honest data.

WHY IGNORANCE NEVER REFUSES. `core.fx.to_base` passes an unknown pair through 1:1 by
design, so a conversion result cannot distinguish "the rate is 1.0" from "we have no rate".
`core.fx.known_rate` returns None for the latter, and the guard then declines to judge —
refusing on a currency we simply have no rate for would break legitimate writes for a
reason the user cannot act on.
"""

from __future__ import annotations

import pytest

from backend.core import fx as core_fx
from backend.core.errors import ImplausibleTransferAmountError, ValidationError
from backend.services import ledger as svc

# 1 SGD = 20,000 VND; 1 USD = 1.35 SGD. USDC is deliberately ABSENT (unknown pair).
_FX = {"SGD": 1.0, "VND": 1 / 20_000, "USD": 1.35}

_ACCOUNT_CURRENCIES = {
    "sgd-1": "SGD",
    "sgd-2": "SGD",
    "vnd-1": "VND",
    "usd-1": "USD",
    "usdc-1": "USDC",  # a currency the rates table has never heard of
}


class _AcctStore:
    def find_by_id(self, aid):
        if aid in _ACCOUNT_CURRENCIES:
            return {"id": aid, "name": aid, "currency": _ACCOUNT_CURRENCIES[aid]}
        return None

    def all(self):
        return [{"id": k, "name": k, "currency": v} for k, v in _ACCOUNT_CURRENCIES.items()]


@pytest.fixture
def patched(monkeypatch):
    """Install the rates table on `core.fx`'s loader hook DIRECTLY.

    Not by patching `services.fx_cache.load_fx_cache` (the obvious move, and what several
    older tests do): `tests/conftest.py::_reset_global_fx_and_profile` snapshots
    `core.fx._fx_rates_loader` before each test and restores it after, and on the FIRST test
    of a session that snapshot is None — `services.fx_cache` (which registers the real loader
    at import) has not necessarily been imported yet. It therefore pins None for the rest of
    the run, and `_load_rates()` falls back to `{base: 1.0}`, silently converting every
    non-base currency 1:1. Patching the hook itself is immune to that, and monkeypatch undoes
    it. See the note in the module docstring above.
    """
    monkeypatch.setattr(svc, "accounts", _AcctStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: dict(_FX))


def _transfer(from_id, from_amt, to_id, to_amt, **extra):
    return {
        "kind": "transfer",
        "date": "2026-08-21",
        "description": "transfer",
        "from_account_id": from_id,
        "from_amount": from_amt,
        "to_account_id": to_id,
        "to_amount": to_amt,
        **extra,
    }


def _resolve(data):
    row, _meta = svc.resolve_transaction_row(dict(data), mint_payee=False)
    return row


# ── the bug class ─────────────────────────────────────────────────────────────


def test_the_incident_shape_is_refused(patched):
    """₫300,000,000 out of an SGD account 'as' S$300,000,000: implied 1 SGD = 1 VND
    against a known 20,000 — refused, with both figures named."""
    with pytest.raises(ValidationError) as exc:
        _resolve(_transfer("sgd-1", 300_000_000.0, "vnd-1", 300_000_000.0))
    msg = str(exc.value)
    assert "300,000,000" in msg
    assert "SGD" in msg and "VND" in msg


def test_a_wrong_rate_short_of_exact_equality_is_also_refused(patched):
    """The equality check this guard replaced would MISS this: the legs are not equal,
    the conversion was simply done with a rate wrong by ~1000x."""
    with pytest.raises(ValidationError):
        _resolve(_transfer("sgd-1", 100.0, "vnd-1", 2_137.0))  # expected ≈ 2,000,000


def test_refusal_names_the_amount_the_leg_should_have_carried(patched):
    """A refusal the caller cannot act on is a worse outcome than the bad row."""
    with pytest.raises(ValidationError) as exc:
        _resolve(_transfer("sgd-1", 100.0, "vnd-1", 100.0))
    assert "2,000,000" in str(exc.value)


def test_the_guard_fires_on_the_preview_not_only_the_write(patched):
    """`resolve_transaction_row` is the preview's own path — a confirm card must never
    render a row the write would then refuse."""
    with pytest.raises(ValidationError):
        svc.resolve_transaction_row(
            _transfer("sgd-1", 300_000_000.0, "vnd-1", 300_000_000.0), mint_payee=False
        )


# ── the sibling guard: equal amounts on a pair that is NOT genuinely 1:1 ─────
#
# WHY THIS GUARD EXISTS. The 10x band above exists for the 1,000x class of bug and is far too
# wide to catch the SAME mistake on an ordinary fiat pair: a caller with the right rate sitting
# right in its own description/memo that never actually multiplies by it lands on an implied
# rate of 1.0, which for most fiat pairs (USD/SGD here is 1.35) is nowhere near 10x off and
# sails through unrefused. This is the "equal amounts" check the module docstring says was
# replaced — reintroduced, but scoped to fire ONLY when `known_rate` says the pair is NOT
# genuinely 1:1, so the case its removal was FOR (real amounts a legitimate 1:1 pair) is
# untouched.


def test_equal_amount_on_a_known_nonone_rate_pair_is_refused(patched):
    """100 USD -> 100 SGD: same number, but 1 USD = 1.35 SGD is known and not 1:1 — this is
    the source figure carried across the boundary unconverted, not a real transfer."""
    with pytest.raises(ImplausibleTransferAmountError) as exc:
        _resolve(_transfer("usd-1", 100.0, "sgd-1", 100.0))
    msg = str(exc.value)
    assert "100.00" in msg
    assert "USD" in msg and "SGD" in msg
    assert "135.00" in msg  # the amount it should have converted to


def test_force_transfer_rate_books_the_equal_amount_anyway(patched):
    """The rare genuine case (a stale quote, a peg, a fee-inclusive net settlement) must
    still be representable — same escape-hatch shape as `force` for duplicates."""
    row, _meta = svc.resolve_transaction_row(
        _transfer("usd-1", 100.0, "sgd-1", 100.0), mint_payee=False, force_transfer_rate=True
    )
    assert row["from_amount"] == 100.0
    assert row["to_amount"] == 100.0


def test_a_rate_within_two_percent_of_parity_is_never_refused():
    """A stablecoin's own tiny de-peg wobble must not trip this — only a pair whose rate is
    MEANINGFULLY away from 1.0 is judged."""
    from backend.services.ledger import _assert_transfer_amount_converted

    _assert_transfer_amount_converted(100.0, "USD", 100.0, "USDT", rates={"USD": 1.0, "USDT": 1.01})


def test_amounts_that_differ_are_never_judged_by_this_guard(patched):
    """This guard is narrower than the rate-magnitude one above on purpose: two DIFFERENT
    numbers are the other guard's job (or honest, within its band)."""
    row = _resolve(_transfer("usd-1", 100.0, "sgd-1", 134.99))
    assert row["to_amount"] == 134.99


# ── the edit path: a forced row must stay editable ───────────────────────────
#
# `update_transaction` rebuilds the row through `create_transaction`. Unlike the 10x guard —
# which has no bypass, so no stored row can ever violate it — this guard CAN be bypassed, so a
# forced parity row exists in the store and would be re-judged on every later edit. Legs the
# edit did not touch were already accepted by the write that stored them; only a leg the edit
# actually changes is judged fresh.


class _TxnStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def find_by_id(self, tid):
        return self._rows.get(tid)

    def by_external_ref(self, source, ref):
        from backend.core.txn_sources import ext_ref_pairs

        return next((r for r in self._rows.values() if (source, ref) in ext_ref_pairs(r)), None)

    def has_external_ref(self, source, ref):
        return self.by_external_ref(source, ref) is not None

    def insert(self, row, *, conn=None):
        return self.append(row)

    def append(self, row, *, conn=None):
        import uuid

        row = dict(row)
        if not row.get("id"):
            row["id"] = str(uuid.uuid4())
        self._rows[row["id"]] = row
        return row

    def upsert(self, row, *, conn=None):
        self._rows[row["id"]] = row
        return row

    def delete(self, tid, *, conn=None):
        return self._rows.pop(tid, None) is not None

    def transfer_routes(self):
        return frozenset(
            frozenset((t["from_account_id"], t["to_account_id"]))
            for t in self.all()
            if t.get("kind") == "transfer"
            and not t.get("deleted")
            and not t.get("merged_into")
            and t.get("from_account_id")
            and t.get("to_account_id")
        )

    def all(self):
        return list(self._rows.values())

    def query_txns(self, **_):
        return list(self._rows.values())

    def enabled_rules(self):
        return []


@pytest.fixture
def stored(patched, monkeypatch):
    monkeypatch.setattr(svc, "transactions", _TxnStore())
    import backend.services.corrections as corr

    monkeypatch.setattr(corr, "record_correction", lambda old, new: None)


def test_an_unrelated_edit_never_rejudges_a_forced_parity_row(stored):
    """Booked at parity on purpose (force_transfer_rate=True); a later note edit leaves both
    legs untouched, so it must not be refused for a shape the original write accepted."""
    row = svc.create_transaction(
        _transfer("usd-1", 100.0, "sgd-1", 100.0), force_transfer_rate=True
    )
    updated = svc.update_transaction(row["id"], {"description": "parity settlement (edited)"})
    assert updated["description"] == "parity settlement (edited)"
    assert updated["from_amount"] == 100.0
    assert updated["to_amount"] == 100.0


def test_an_edit_that_changes_a_leg_is_judged_fresh(stored):
    """A correct transfer edited INTO the bad shape is the same mistake arriving late — the
    guard fires exactly as it would on a create."""
    row = svc.create_transaction(_transfer("usd-1", 100.0, "sgd-1", 135.0))
    with pytest.raises(ImplausibleTransferAmountError):
        svc.update_transaction(row["id"], {"to_amount": 100.0})


def test_fixing_a_mis_booked_row_by_editing_to_amount_is_accepted(stored):
    """The agent's post-warning repair path: set to_amount to the converted figure. The legs
    now differ, so neither guard has anything to say."""
    row = svc.create_transaction(
        _transfer("usd-1", 100.0, "sgd-1", 100.0), force_transfer_rate=True
    )
    updated = svc.update_transaction(row["id"], {"to_amount": 135.0})
    assert updated["to_amount"] == 135.0


# ── everything honest still writes ───────────────────────────────────────────


def test_a_correct_cross_currency_transfer_is_accepted(patched):
    row = _resolve(_transfer("sgd-1", 100.0, "vnd-1", 2_000_000.0))
    assert row["from_amount"] == 100.0
    assert row["to_amount"] == 2_000_000.0


@pytest.mark.parametrize("to_amount", [1_800_000.0, 2_200_000.0, 1_000_000.0, 4_000_000.0])
def test_a_spread_or_a_stale_rate_is_never_refused(patched, to_amount):
    """10% off (spread), 2x off (a genuinely old historical rate) — all honest, all
    accepted. The guard must only ever fire on a magnitude error."""
    row = _resolve(_transfer("sgd-1", 100.0, "vnd-1", to_amount))
    assert row["to_amount"] == to_amount


def test_same_currency_legs_are_never_judged(patched):
    row = _resolve(_transfer("sgd-1", 100.0, "sgd-2", 100.0))
    assert row["to_amount"] == 100.0


def test_an_unknown_pair_is_accepted_not_refused(patched):
    """USDC is absent from the rates table. `to_base` would pass it through 1:1, which is
    indistinguishable from a real 1:1 pair — so the guard declines to judge rather than
    refusing a write for a reason the user cannot act on."""
    row = _resolve(_transfer("sgd-1", 100.0, "usdc-1", 100.0))
    assert row["to_amount"] == 100.0


def test_a_genuinely_one_to_one_pair_would_be_accepted(patched):
    """The equality check this replaced would refuse this forever. Pinned as the reason
    the guard is rate-based: a 1:1 stablecoin wallet transfer is legitimate."""
    row = _resolve(_transfer("sgd-1", 500.0, "usdc-1", 500.0))
    assert row["from_amount"] == 500.0


def test_a_missing_leg_amount_is_never_judged(patched):
    """An expense/income has one leg; there is no rate to imply."""
    row, _ = svc.resolve_transaction_row(
        {
            "kind": "expense",
            "date": "2026-08-21",
            "description": "lunch",
            "from_account_id": "vnd-1",
            "from_amount": 300_000.0,
        },
        mint_payee=False,
    )
    assert row["from_amount"] == 300_000.0


def test_a_zero_leg_is_never_judged(patched):
    """Zero implies no rate at all — dividing by it must not raise, and a zero-amount
    transfer is a legal (if odd) row."""
    row = _resolve(_transfer("sgd-1", 0.0, "vnd-1", 0.0))
    assert row["from_amount"] == 0.0


# ── the core helper the guard is built on ────────────────────────────────────


def test_known_rate_distinguishes_one_to_one_from_no_rate():
    from backend.core.fx import known_rate

    assert known_rate("SGD", "VND", _FX) == pytest.approx(20_000.0)
    assert known_rate("SGD", "SGD", _FX) == 1.0
    assert known_rate("SGD", "USDC", _FX) is None, "an absent currency must be None, not 1.0"
    assert known_rate("", "SGD", _FX) is None
