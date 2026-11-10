"""The statement match pass narrows each row's candidate pool by date and counts reference
tokens once per pass — the verdicts must be byte-identical to the brute-force scan it replaced.

Every comparison here runs the OPTIMIZED engine against an in-test brute-force reference (each
row classified against the WHOLE pool, reference-token frequencies counted over the whole
comparison scope) on seeded synthetic books built to hit the edges the narrowing touches: undated
and unparseable dates, rows exactly at the date cap, a policy that tightens the cap, shared and
over-frequent reference tokens, same-ref exclusions, cross-currency pairs, transfers and echoes.
"""

from __future__ import annotations

import copy
import random
from datetime import date, timedelta

import pytest

from backend.core.txn_sources import ext_ref_pairs, primary_pair
from backend.services import import_anomalies, matching
from backend.services import ledger as ledger_service
from backend.services.import_common import RowResult

_ACCOUNTS = ["acc_card", "acc_bank", "acc_wallet"]
_WORDS = ["coffee", "grocer", "metro", "rideshare", "cinema", "bakery", "pharmacy", "books"]
_SHARED_REFS = ["FT26061500000001", "77700011122", "HOTLINE68881234"]
_RATES = {"USD": 1.0, "EUR": 0.92, "SGD": 1.35}
_ROUTES = frozenset({frozenset(("acc_bank", "acc_card")), frozenset(("acc_bank", "acc_wallet"))})
_ANCHOR = date(2026, 6, 15)


def _rand_date(rng: random.Random) -> str | None:
    roll = rng.random()
    if roll < 0.03:
        return None
    if roll < 0.05:
        return "not-a-date"
    return (_ANCHOR + timedelta(days=rng.randrange(-40, 40))).isoformat()


def _rand_row(rng: random.Random, i: int, *, ledger: bool) -> dict:
    kind = rng.choices(
        ["expense", "income", "transfer", "adjustment", "investment"], [60, 15, 15, 4, 6]
    )[0]
    acct = rng.choice(_ACCOUNTS)
    ccy = rng.choice(["USD"] * 8 + ["EUR", "SGD"])
    amount = rng.choice([4.2, 9.4, 9.65, 12.0, 60.36, 100.0, 101.5, 0.5, 250.0, 1250.0])
    desc = " ".join(rng.sample(_WORDS, rng.randrange(1, 4)))
    if rng.random() < 0.4:
        desc += " " + (
            rng.choice(_SHARED_REFS) if rng.random() < 0.5 else f"REF{rng.randrange(10**9)}"
        )
    row: dict = {
        "date": _rand_date(rng),
        "kind": kind,
        "description": desc,
        "source": rng.choice(["ofx_import", "manual", "email", "csv_import"]),
    }
    if kind in ("expense", "investment"):
        row.update(from_account_id=acct, from_amount=amount, from_currency=ccy)
    elif kind in ("income", "adjustment"):
        row.update(to_account_id=acct, to_amount=amount, to_currency=ccy)
    else:
        dest = rng.choice([a for a in _ACCOUNTS if a != acct])
        row.update(
            from_account_id=acct,
            to_account_id=dest,
            from_amount=amount,
            to_amount=amount,
            from_currency=ccy,
            to_currency=ccy,
        )
    if rng.random() < 0.1:
        row.update(original_amount=amount * 26000, original_currency="VND")
    if rng.random() < 0.5:
        row["external_refs"] = [
            {"source": rng.choice(["ofx", "email", "manual"]), "ref": f"R{rng.randrange(60)}"}
        ]
    if ledger:
        row["id"] = f"txn_{i:05d}"
        if rng.random() < 0.3:
            row["category"] = "cat_000000000001"
        if rng.random() < 0.3:
            row["payee_id"] = f"payee_{rng.randrange(5)}"
        if rng.random() < 0.2:
            row["batch_id"] = f"batch_{rng.randrange(3)}"
    return row


def _book(seed: int, n_ledger: int, n_rows: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    ledger = [_rand_row(rng, i, ledger=True) for i in range(n_ledger)]
    rows = []
    for i in range(n_rows):
        if ledger and rng.random() < 0.6:
            # An echo of a ledger row, sometimes shifted to exactly the date cap or past it.
            src = rng.choice(ledger)
            row = {k: copy.deepcopy(v) for k, v in src.items() if k not in ("id", "batch_id")}
            d = matching.parse_date(src.get("date"))
            if d is not None and rng.random() < 0.5:
                shift = rng.choice(
                    [1, 3, matching.FETCH_WINDOW_DAYS, matching.FETCH_WINDOW_DAYS + 1]
                )
                row["date"] = (d + timedelta(days=rng.choice([-1, 1]) * shift)).isoformat()
            if rng.random() < 0.3:
                row["description"] = row["description"].upper() + " MPOS"
            if rng.random() < 0.5:
                # Its own statement ref: the source row stays IN the draft's comparison scope.
                row["external_refs"] = [{"source": "ofx", "ref": f"S{seed}-{i}"}]
        else:
            row = _rand_row(rng, i, ledger=False)
        rows.append(row)
    return ledger, rows


def _sig(cands: list[matching.Candidate]) -> list[tuple]:
    return [
        (c.id, id(c.txn), c.klass, c.score, c.fidelity, tuple(c.reasons), c.echo_side, c.weak)
        for c in cands
    ]


def _brute_force_batch(rows, ledger_pool, *, policy, weak_tier):
    """The pre-narrowing ``_classify_batch_pairs``: every row against the whole pool + siblings."""
    stamp = f"_match_batch_{id(rows)}"
    ref_freq = matching._reference_token_frequencies([*rows, *ledger_pool])
    originals = [row.get("batch_id") for row in rows]
    for row in rows:
        row["batch_id"] = stamp
    try:
        out = []
        for idx, row in enumerate(rows):
            pool = list(ledger_pool) + [sib for j, sib in enumerate(rows) if j != idx]
            scored = [
                c
                for c in (
                    matching._classify(
                        row,
                        cand,
                        _RATES,
                        _ROUTES,
                        ref_freq=ref_freq,
                        policy=policy,
                        weak_tier=weak_tier,
                    )
                    for cand in pool
                )
                if c
            ]
            scored.sort(
                key=lambda c: (-c.score, c.fidelity, matching._date_gap(row.get("date"), c))
            )
            out.append(scored)
        return out
    finally:
        for row, original in zip(rows, originals):
            if original is not None:
                row["batch_id"] = original
            else:
                row.pop("batch_id", None)


def _brute_force_check(row, pool, *, policy=matching.MatchPolicy()):
    ref_freq = matching._reference_token_frequencies([row, *pool])
    scored = [
        c
        for c in (
            matching._classify(row, cand, _RATES, _ROUTES, ref_freq=ref_freq, policy=policy)
            for cand in pool
        )
        if c
    ]
    scored.sort(key=lambda c: (-c.score, c.fidelity, matching._date_gap(row.get("date"), c)))
    return scored


@pytest.fixture(autouse=True)
def _usd_base(monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")


_POLICIES = [
    matching.MatchPolicy(),
    matching.MatchPolicy(max_date_drift_days=2),
    matching.MatchPolicy(max_date_drift_days=0, amount_tolerance_pct=0.005),
]


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("policy", _POLICIES)
@pytest.mark.parametrize("weak_tier", [False, True])
def test_batch_classification_equals_brute_force(seed, policy, weak_tier):
    ledger, rows = _book(seed, n_ledger=160, n_rows=70)
    expected = _brute_force_batch(rows, ledger, policy=policy, weak_tier=weak_tier)
    got = matching._classify_batch_pairs(
        rows, ledger, _RATES, _ROUTES, policy=policy, weak_tier=weak_tier
    )
    assert [_sig(rm.candidates) for rm in got] == [_sig(c) for c in expected]
    assert all("batch_id" not in r for r in rows)  # the preview stamp is still cleaned up


@pytest.mark.parametrize("seed", range(4))
def test_match_batch_end_to_end_equals_brute_force_pool(seed, monkeypatch):
    """Through ``match_batch`` (assignment, promotion, banding): narrowing changes nothing
    downstream either."""
    ledger, rows = _book(seed + 100, n_ledger=160, n_rows=70)
    ctx = matching.BatchContext(
        complete=True,
        window=(
            (_ANCHOR - timedelta(days=30)).isoformat(),
            (_ANCHOR + timedelta(days=30)).isoformat(),
        ),
        account_ids=frozenset({"acc_card"}),
    )

    def _run():
        return matching.match_batch(
            copy.deepcopy(rows),
            context=ctx,
            profile=matching.SCAN,
            candidates=copy.deepcopy(ledger),
            rates=_RATES,
            known_routes=_ROUTES,
            weak_tier=True,
        )

    got = _run()
    monkeypatch.setattr(
        matching,
        "_classify_batch_pairs",
        lambda rows, pool, rates, routes, *, policy, weak_tier: [
            matching.RowMatch(index=i, candidates=c)
            for i, c in enumerate(
                _brute_force_batch(rows, pool, policy=policy, weak_tier=weak_tier)
            )
        ],
    )
    expected = _run()

    def _view(rms):
        return [
            (
                rm.band,
                rm.promoted,
                rm.assigned and (rm.assigned.id, rm.assigned.klass, rm.assigned.score),
                tuple(rm.alternatives),
                rm.intra_batch,
                [(c.id, c.klass, c.score, c.fidelity) for c in rm.candidates],
            )
            for rm in rms
        ]

    assert _view(got) == _view(expected)


@pytest.mark.parametrize("seed", range(5))
def test_check_with_scope_frequencies_equals_brute_force(seed):
    """``check(candidates=…, ref_freq=…)`` — a caller that narrowed the pool but hands the
    WHOLE scope's token counts — ranks exactly like ``check`` over the whole pool."""
    ledger, rows = _book(seed + 200, n_ledger=200, n_rows=40)
    for row in rows:
        expected = _brute_force_check(row, ledger)
        near = matching._DatedPool(ledger).near(
            matching.parse_date(row.get("date")), matching.FETCH_WINDOW_DAYS
        )
        freq = matching._reference_token_frequencies([row, *ledger])
        got = matching.check(
            row,
            profile=matching.SCAN,
            candidates=near,
            rates=_RATES,
            known_routes=_ROUTES,
            ref_freq=freq,
        )
        assert _sig(got.candidates) == _sig(expected)


class _FakeTxns:
    """The two reads ``detect_anomalies`` makes, over an in-memory synthetic ledger."""

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def query_txns(self, date_from=None, date_to=None):
        return [
            copy.deepcopy(t)
            for t in self.rows
            if t.get("date") and date_from <= t["date"] <= date_to
        ]

    def all(self):
        return copy.deepcopy(self.rows)


class _FakeAccounts:
    def all(self):
        return []


@pytest.mark.parametrize("seed", range(6))
def test_detect_anomalies_check_calls_equal_brute_force(seed, monkeypatch):
    """Every per-draft engine call ``detect_anomalies`` makes — narrowed pool, incremental token
    counts — returns exactly what the brute-force call over ``ledger pool + prior drafts``
    (minus same-ref rows) returned before narrowing, and the flags it attaches are unchanged."""
    ledger, drafts = _book(seed + 300, n_ledger=220, n_rows=60)
    for row in drafts:
        # A same-ref twin in the ledger exercises the hard-dedup exclusion — and, printing the
        # draft's own reference token, the token count the exclusion must subtract.
        key = primary_pair(row)
        if key[1] and not any(key in ext_ref_pairs(t) for t in ledger):
            ledger.append({**copy.deepcopy(row), "id": f"twin_{len(ledger)}"})
    monkeypatch.setattr(ledger_service, "transactions", _FakeTxns(ledger))
    monkeypatch.setattr(ledger_service, "accounts", _FakeAccounts())
    monkeypatch.setattr("backend.services.fx_cache.load_fx_cache", lambda: _RATES)
    monkeypatch.setattr(matching, "known_transfer_routes", lambda: _ROUTES)

    real_check = matching.check
    pools: list[list[dict]] = []
    seen: list[dict] = []
    mismatches: list[int] = []

    def _spy(draft, **kwargs):
        got = real_check(draft, **kwargs)
        key = primary_pair(draft)
        full = [t for t in (pools[0] + seen) if not (key[1] and key in ext_ref_pairs(t))]
        if _sig(got.candidates) != _sig(_brute_force_check(draft, full)):
            mismatches.append(len(seen))
        seen.append(draft)
        return got

    real_query = _FakeTxns.query_txns

    def _query(self, date_from=None, date_to=None):
        out = real_query(self, date_from, date_to)
        pools.append(out)
        return out

    monkeypatch.setattr(_FakeTxns, "query_txns", _query)
    monkeypatch.setattr(matching, "check", _spy)
    results = [RowResult(line=i + 1, draft=copy.deepcopy(d)) for i, d in enumerate(drafts)]
    import_anomalies.detect_anomalies(results)
    assert len(seen) == len(drafts)
    assert mismatches == []


def test_match_pass_reads_the_base_currency_once_per_pass(monkeypatch):
    """The base currency is a profile READ (a DB query); it was re-read for every classified
    pair and every history row — 118k queries for one 400-row preview."""
    import backend.core.fx as core_fx

    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return "USD"

    monkeypatch.setattr(ledger_service, "base_currency", _counting)
    monkeypatch.setattr(core_fx, "base_currency", _counting)
    ledger, rows = _book(7, n_ledger=200, n_rows=80)
    matching.match_batch(
        rows,
        context=matching.BatchContext(),
        profile=matching.SCAN,
        candidates=ledger,
        rates=_RATES,
        known_routes=_ROUTES,
        weak_tier=True,
    )
    assert calls["n"] <= 2

    calls["n"] = 0
    monkeypatch.setattr(ledger_service, "transactions", _FakeTxns(ledger))
    monkeypatch.setattr(ledger_service, "accounts", _FakeAccounts())
    monkeypatch.setattr("backend.services.fx_cache.load_fx_cache", lambda: _RATES)
    monkeypatch.setattr(matching, "known_transfer_routes", lambda: _ROUTES)
    results = [RowResult(line=i + 1, draft=d) for i, d in enumerate(rows)]
    import_anomalies.detect_anomalies(results)
    assert calls["n"] <= 2 + len(rows)  # once per pass + at most once per engine call
