"""``external_refs[].emitter`` — resolved server-side, stamped at the write choke, backfilled.

The match engine's identity invariant stopped trusting a NAMESPACE to mean one issuer (ADR
2026-09-02, ``matching._same_source_event_veto``). It trusts the EMITTER instead, and reads it
off the stored ref — so the value has to be there, has to come from what we actually received
rather than from whatever a caller asserts, and has to reach the rows that were already booked.

Three properties, one per place the value can go wrong:
  - ``email_snapshots.emitter_for`` resolves the sender, and degrades to None rather than raising.
  - the ledger write chokes stamp it before the store write, so it lands in the row's payload.
  - the backfill migration reaches every pre-existing row, and is reversible.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import sqlalchemy as sa

from backend.core.email_ids import internal_email_id
from backend.services import email_snapshots as snapshots
from backend.services import ledger


def _load_migration():
    """The revision's module name starts with a digit, so it cannot be imported by name."""
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend/db/alembic/versions/202609021430_backfill_external_ref_emitter.py"
    )
    spec = importlib.util.spec_from_file_location("_backfill_emitter_rev", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill = _load_migration()

SENDER = '"Bank" <alerts@bank.example>'


def _seed_accounts():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc_1"}])


def _stash(raw_id: str, sender: str = SENDER) -> str:
    snapshots.stash_snapshots(
        [
            {
                "message_id": raw_id,
                "from": sender,
                "subject": "Transaction Alert",
                "date": "2026-09-01",
                "body_text": "b",
            }
        ]
    )
    return internal_email_id(raw_id)


def _txn(ref: str, **over):
    return {
        "date": "2026-09-01",
        "description": "RIDGELINE MOBILE",
        "from_account_id": "acc_1",
        "from_amount": 24.40,
        "from_currency": "SGD",
        "kind": "expense",
        "external_refs": [{"source": "email", "ref": ref}],
        **over,
    }


def _emitters(row):
    return [e.get("emitter") for e in row["external_refs"]]


# ── emitter_for ────────────────────────────────────────────────────────────────


def test_emitter_for_resolves_the_stashed_sender(db_isolated):
    ref = _stash("<m-1@x>")

    assert snapshots.emitter_for("email", ref) == SENDER


def test_emitter_for_is_none_when_nothing_resolves(db_isolated):
    """Unresolved is a real state, not an error — the veto reads it as "may be the same emitter"
    and refuses to loosen, which is the safe direction."""
    assert snapshots.emitter_for("email", internal_email_id("<never-stashed@x>")) is None
    assert snapshots.emitter_for("email", "") is None
    assert snapshots.emitter_for(None, "anything") is None


def test_emitter_for_ignores_non_email_sources(db_isolated):
    """Other namespaces have their own notion of an emitter; this resolver speaks only for email
    and must not claim a sender it cannot possibly know."""
    _stash("<m-1@x>")

    assert snapshots.emitter_for("ofx", internal_email_id("<m-1@x>")) is None


def test_emitter_for_survives_a_broken_snapshot_store(db_isolated, monkeypatch):
    """Best-effort by contract: this runs inside the ledger write choke, and a lookup failure
    must degrade to unresolved rather than take a transaction write down with it."""
    monkeypatch.setattr(
        snapshots, "_find_snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert snapshots.emitter_for("email", "anything") is None


# ── stamped at the write choke ─────────────────────────────────────────────────


def test_create_transaction_stamps_the_emitter(db_isolated):
    _seed_accounts()
    ref = _stash("<m-1@x>")

    stored = ledger.create_transaction(_txn(ref))

    assert _emitters(stored) == [SENDER]


def test_bulk_create_transactions_stamps_the_emitter(db_isolated):
    _seed_accounts()
    a, b = _stash("<m-1@x>"), _stash("<m-2@x>", "receipts@merchant.example")

    created, _ = ledger.bulk_create_transactions([_txn(a), _txn(b)], allow_duplicate=True)

    assert [_emitters(r) for r in created] == [[SENDER], ["receipts@merchant.example"]]


def test_an_unresolvable_ref_books_without_an_emitter(db_isolated):
    """The key is omitted rather than stored as None, so "we never looked" and "we looked and
    found nothing" stay one absent key — both mean unresolved, neither is a wildcard."""
    _seed_accounts()

    stored = ledger.create_transaction(_txn(internal_email_id("<never-stashed@x>")))

    assert "emitter" not in stored["external_refs"][0]


def test_a_caller_supplied_emitter_is_never_overwritten(db_isolated):
    """A merge absorbs refs that were each resolved at their own creation time; re-resolving
    against today's snapshot store would silently rewrite that history."""
    _seed_accounts()
    ref = _stash("<m-1@x>")
    row = _txn(ref)
    row["external_refs"][0]["emitter"] = "resolved-earlier@x"

    stored = ledger.create_transaction(row)

    assert _emitters(stored) == ["resolved-earlier@x"]


def test_a_failing_resolver_does_not_fail_the_write(db_isolated, monkeypatch):
    """The whole point of stamping best-effort: an unstamped row is a conservative match
    decision, a lost row is a lost transaction."""
    _seed_accounts()
    monkeypatch.setattr(
        snapshots, "emitter_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    stored = ledger.create_transaction(_txn(_stash("<m-1@x>")))

    assert stored["id"]
    assert "emitter" not in stored["external_refs"][0]


def test_stamping_does_not_mutate_the_caller_s_input(db_isolated):
    """``create_transaction`` builds its row as ``{**data, ...}`` — a SHALLOW copy — so the refs
    list and its dicts belong to the caller. Stamping in place would write the emitter back into
    an argument the caller is still holding."""
    _seed_accounts()
    row = _txn(_stash("<m-1@x>"))
    submitted = row["external_refs"]

    stored = ledger.create_transaction(row)

    assert _emitters(stored) == [SENDER]
    assert submitted == [{"source": "email", "ref": internal_email_id("<m-1@x>")}]


def test_the_model_cannot_assert_an_emitter(db_isolated):
    """Security pin. The veto TRUSTS the emitter to decide whether two msgids are two payments,
    so a model-supplied one could suppress a real duplicate flag or lift the veto on two real
    charges. ``_txn_data_from_ai`` rebuilds every entry as exactly ``{source, ref}``, dropping
    the key — this asserts that stays true, because the drop is silent if it ever stops."""
    from backend.ai.tools._shared import _txn_data_from_ai

    _seed_accounts()
    data = _txn_data_from_ai(
        {
            "date": "2026-09-01",
            "description": "RIDGELINE MOBILE",
            "amount": 24.40,
            "currency": "SGD",
            "account_id": "acc_1",
            "external_refs": [
                {"source": "email", "ref": "r1", "emitter": "forged@attacker.example"}
            ],
        },
        default_date="2026-09-01",
    )

    assert data["external_refs"] == [{"source": "email", "ref": "r1"}]


# ── the backfill migration ─────────────────────────────────────────────────────


def _migration_db():
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(sa.text("CREATE TABLE email_snapshots (id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(sa.text("CREATE TABLE transactions (id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(
        sa.text(
            "CREATE TABLE transaction_external_refs "
            "(ref_id TEXT PRIMARY KEY, source TEXT, ref TEXT, payload TEXT)"
        )
    )
    conn.execute(
        sa.text("INSERT INTO email_snapshots VALUES ('r-known', :p)"),
        {"p": json.dumps({"from_addr": SENDER})},
    )
    refs = [{"source": "email", "ref": "r-known"}, {"source": "email", "ref": "r-missing"}]
    conn.execute(
        sa.text("INSERT INTO transactions VALUES ('t1', :p)"),
        {"p": json.dumps({"id": "t1", "external_refs": refs})},
    )
    for i, entry in enumerate(refs):
        conn.execute(
            sa.text("INSERT INTO transaction_external_refs VALUES (:i, :s, :r, :p)"),
            {"i": f"ref{i}", "s": entry["source"], "r": entry["ref"], "p": json.dumps(entry)},
        )
    return conn


def _txn_refs(conn):
    row = conn.execute(sa.text("SELECT payload FROM transactions WHERE id='t1'")).scalar_one()
    return json.loads(row)["external_refs"]


def _child_payloads(conn):
    return [
        json.loads(p)
        for (p,) in conn.execute(
            sa.text("SELECT payload FROM transaction_external_refs ORDER BY ref_id")
        )
    ]


def test_backfill_stamps_both_payloads(monkeypatch):
    """``transactions.payload`` is the round-trip source of truth and the child table holds each
    entry verbatim — ``verify_transaction_children`` asserts they agree, so both are rewritten."""
    conn = _migration_db()
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()

    assert [e.get("emitter") for e in _txn_refs(conn)] == [SENDER, None]
    assert [e.get("emitter") for e in _child_payloads(conn)] == [SENDER, None]


def test_backfill_leaves_an_unresolvable_ref_alone(monkeypatch):
    """No snapshot means no claim — the key stays absent and the veto keeps failing closed for
    that row, rather than the migration inventing a sender."""
    conn = _migration_db()
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()

    assert "emitter" not in _txn_refs(conn)[1]


def test_backfill_is_idempotent(monkeypatch):
    conn = _migration_db()
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()
    before = _txn_refs(conn)
    backfill.upgrade()

    assert _txn_refs(conn) == before


def test_backfill_downgrade_strips_the_key(monkeypatch):
    conn = _migration_db()
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()
    backfill.downgrade()

    assert all("emitter" not in e for e in _txn_refs(conn))
    assert all("emitter" not in e for e in _child_payloads(conn))


def test_backfill_no_ops_on_a_book_with_no_snapshots(monkeypatch):
    """A fresh install has nothing to join against — the migration must be a clean no-op, not a
    scan that rewrites every row's payload for nothing."""
    conn = _migration_db()
    conn.execute(sa.text("DELETE FROM email_snapshots"))
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)
    before = _txn_refs(conn)

    backfill.upgrade()

    assert _txn_refs(conn) == before
