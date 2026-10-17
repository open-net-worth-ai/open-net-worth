"""Delete permanently erases a transaction's contents; an ordinary delete still keeps them.

ADR 2026-10-17-delete-permanently-erases-a-transactions-contents. The privacy audit found a
deleted transaction's full before-image (date, amount, account, memo) in ``txn_audit`` forever,
its display details in ``rejected_identities``, and its payee left behind — after a soft delete
AND after an account force-delete. The ordinary delete stays soft on purpose (Undo, checkpoint
restore and the re-import guard read it); this pins the explicit erase.

The load-bearing test plants a marker in every free-text field, edits the row (so the audit
trail holds before/after images), purges it, then searches EVERY column of EVERY table — and the
raw SQLite file — for the marker.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Text as SAText
from sqlalchemy import cast, inspect, select

from backend.core.errors import ConflictError, PreviouslyDeletedError
from backend.db import tables
from backend.db.engine import get_engine
from backend.db.payload_store import BookPayloadTableStore, PayloadTableStore
from backend.services import ledger as L
from backend.services import transaction_purge as P

MARK = "ZZPURGEMARK"
H = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})


def _txn(memo=f"{MARK} therapy session", payee=f"{MARK} Clinic", refs=None) -> dict:
    data = {
        "kind": "expense",
        "date": "2026-10-01",
        "description": payee,
        "memo": memo,
        "from_account_id": "acc_chk",
        "from_amount": 42.42,
        "source": "manual",
    }
    if refs:
        data["external_refs"] = refs
    return L.create_transaction(data)


def _where_is(marker: str) -> list[str]:
    """Every (table.column) in the database holding ``marker``."""
    engine = get_engine()
    found = []
    with engine.connect() as conn:
        for name in inspect(engine).get_table_names():
            table = tables.metadata.tables.get(name)
            if table is None:
                continue
            for column in table.c:
                hit = conn.execute(
                    select(table).where(cast(column, SAText).like(f"%{marker}%")).limit(1)
                ).first()
                if hit is not None:
                    found.append(f"{name}.{column.name}")
    return found


def _client():
    from backend.main import app

    return TestClient(app)


def test_an_ordinary_delete_still_keeps_the_row_for_undo():
    row = _txn()
    L.bulk_delete_transactions([row["id"]])
    assert L.transactions.find_by_id(row["id"])["deleted"]
    assert any(spot.startswith("transactions.") for spot in _where_is(MARK))


def test_delete_permanently_leaves_the_contents_nowhere():
    row = _txn()
    L.update_transaction(row["id"], {"memo": f"{MARK} edited follow-up"})
    res = _client().post("/api/transactions/purge", json={"ids": [row["id"]]}, headers=H)
    assert res.status_code == 200, res.text
    assert res.json()["purged"] == [row["id"]]
    assert L.transactions.find_by_id(row["id"]) is None
    assert _where_is(MARK) == []
    db_file = get_engine().url.database
    if db_file and db_file != ":memory:":
        with open(db_file, "rb") as fh:
            assert MARK.encode() not in fh.read()


def test_the_history_keeps_only_that_it_was_deleted_permanently():
    row = _txn()
    P.purge_transactions([row["id"]])
    from backend.db.transaction_audit_store import TransactionAuditStore

    events = TransactionAuditStore().query(txn_id=row["id"])
    assert events, "the trail itself survives"
    for event in events:
        assert set(event) <= {
            "id",
            "txn_id",
            "op",
            "ts",
            "actor",
            "surface",
            "source",
            "session_id",
            "sender_id",
            "intent",
            "automation",
            "automation_id",
            "before",
            "after",
            "note",
        }
        assert not event.get("before") and not event.get("after")
    assert any("Deleted permanently" in (e.get("note") or "") for e in events)


def test_a_soft_deleted_row_can_be_purged_later():
    row = _txn()
    L.bulk_delete_transactions([row["id"]])
    P.purge_transactions([row["id"]])
    assert _where_is(MARK) == []


def test_the_reimport_guard_survives_without_the_details():
    ref = [{"source": "email", "ref": "msg-zz-1"}]
    row = _txn(refs=ref)
    P.purge_transactions([row["id"]])
    from backend.db.rejected_identity_store import RejectedIdentityStore

    (record,) = RejectedIdentityStore().all()
    assert record["ref"] == "msg-zz-1" and record["txn_id"] == row["id"]
    assert not {"date", "description", "amount", "currency"} & set(record)
    with pytest.raises(PreviouslyDeletedError):
        _txn(memo="again", payee="again", refs=ref)


def test_the_starting_balance_is_still_refused():
    opening = L.create_transaction(
        {
            "kind": "income",
            "date": "2026-01-01",
            "description": "Starting Balance",
            "to_account_id": "acc_chk",
            "to_amount": 10.0,
            "source": "manual",
            "external_refs": [{"source": "opening", "ref": "acc_chk"}],
        }
    )
    L.accounts.update_by_id("acc_chk", {"opening_txn_id": opening["id"]})
    with pytest.raises(ConflictError, match="Starting Balance"):
        P.purge_transactions([opening["id"]])


def test_a_row_an_investment_fact_points_at_is_refused_untouched():
    row = _txn()
    BookPayloadTableStore(tables.lots).insert({"id": "lot_zz", "txn_id": row["id"]})
    with pytest.raises(ConflictError, match="lots"):
        P.purge_transactions([row["id"]])
    assert L.transactions.find_by_id(row["id"]) is not None


def test_no_checkpoint_can_bring_it_back():
    row = _txn()
    wl = tables.write_log
    with get_engine().begin() as conn:
        conn.execute(
            wl.insert().values(
                checkpoint_id="cp_zz",
                seq=0,
                table_name="transactions",
                pk=row["id"],
                before_payload={"id": row["id"], "memo": MARK},
            )
        )
    P.purge_transactions([row["id"]])
    with get_engine().connect() as conn:
        image = conn.execute(
            select(wl.c.before_payload).where(wl.c.checkpoint_id == "cp_zz")
        ).scalar()
    assert image is None


def test_a_merge_tombstone_goes_with_its_canonical():
    keep = _txn(memo=f"{MARK} a", payee=f"{MARK} Clinic")
    dup = _txn(memo=f"{MARK} b", payee=f"{MARK} Clinic")
    L.merge_transactions(keep["id"], dup["id"], override=True)
    result = P.purge_transactions([keep["id"]])
    assert set(result["purged"]) == {keep["id"], dup["id"]}
    assert _where_is(MARK) == []


def test_a_configured_payee_is_kept_and_an_untouched_one_goes():
    from backend.services import payee_registry

    plain = _txn(payee=f"{MARK} Plain")
    shaped = _txn(payee="Shaped Payee")
    payee_registry.add_alias(shaped["payee_id"], "shaped alias text")
    P.purge_transactions([plain["id"], shaped["id"]])
    assert payee_registry.require_payee(shaped["payee_id"])["name"] == "Shaped Payee"
    with pytest.raises(Exception):
        payee_registry.require_payee(plain["payee_id"])


def test_its_email_snapshot_goes_unless_another_row_holds_that_message():
    from backend.db.email_snapshot_store import EmailSnapshotStore

    snapshots = EmailSnapshotStore()
    snapshots.stash({"message_id": "msg-zz-2", "body_text": MARK, "referenced": True})
    row = _txn(refs=[{"source": "email", "ref": "msg-zz-2"}])
    P.purge_transactions([row["id"]])
    assert snapshots.get("msg-zz-2") is None


def test_force_deleting_an_account_erases_its_transactions_the_same_way():
    L.accounts.insert({"id": "acc_old", "name": "Old", "kind": "asset", "currency": "SGD"})
    L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-10-02",
            "description": f"{MARK} old shop",
            "memo": MARK,
            "from_account_id": "acc_old",
            "from_amount": 5.0,
            "source": "manual",
        }
    )
    assert L.delete_account("acc_old", force=True)
    assert _where_is(MARK) == []


def test_the_route_wants_the_xhr_header_and_is_locked_in_the_demo():
    from backend.api.demo_lockdown import is_demo_locked

    row = _txn()
    assert _client().post("/api/transactions/purge", json={"ids": [row["id"]]}).status_code == 403
    assert is_demo_locked("POST", "/api/transactions/purge")


def test_a_payee_an_automation_still_names_is_kept():
    from backend.services import payee_registry

    row = _txn(payee="Named By A Rule")
    store = (
        BookPayloadTableStore(tables.automations)
        if "book_id" in tables.automations.c
        else PayloadTableStore(tables.automations)
    )
    store.insert({"id": "auto_zz", "trigger": {"payee_id": row["payee_id"]}})
    P.purge_transactions([row["id"]])
    assert payee_registry.require_payee(row["payee_id"])["name"] == "Named By A Rule"


def test_a_checkpoint_copy_of_the_reimport_record_loses_its_details_too():
    """A payload-table image is the stored row — ``txn_id`` sits inside its ``payload`` — so the
    scrub must look there, or a restore would put the date and amount back."""
    ref = [{"source": "email", "ref": "msg-zz-3"}]
    row = _txn(refs=ref)
    L.bulk_delete_transactions([row["id"]])
    from backend.db.rejected_identity_store import RejectedIdentityStore

    (record,) = RejectedIdentityStore().all()
    wl = tables.write_log
    with get_engine().begin() as conn:
        stored = conn.execute(
            select(tables.rejected_identities).where(
                tables.rejected_identities.c.id == record["id"]
            )
        ).first()
        image = {k: v for k, v in stored._mapping.items()}
        conn.execute(
            wl.insert().values(
                checkpoint_id="cp_rj",
                seq=0,
                table_name="rejected_identities",
                pk=record["id"],
                before_payload=image,
            )
        )
    P.purge_transactions([row["id"]])
    with get_engine().connect() as conn:
        kept = conn.execute(
            select(wl.c.before_payload).where(wl.c.checkpoint_id == "cp_rj")
        ).scalar()
    assert kept["payload"]["ref"] == "msg-zz-3"
    assert not {"date", "description", "amount", "currency"} & set(kept["payload"])


def _image(checkpoint_id: str):
    wl = tables.write_log
    with get_engine().connect() as conn:
        return conn.execute(
            select(wl.c.before_payload).where(wl.c.checkpoint_id == checkpoint_id)
        ).scalar()


def _plant_image(checkpoint_id: str, table_name: str, pk: str, image: dict) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            tables.write_log.insert().values(
                checkpoint_id=checkpoint_id,
                seq=0,
                table_name=table_name,
                pk=pk,
                before_payload=image,
            )
        )


def test_no_checkpoint_can_bring_back_the_email_or_the_payee_it_erased():
    """An Undo of the turn that booked it replays every image that turn took — the email
    snapshot it promoted and the payee it created included."""
    from backend.db.email_snapshot_store import EmailSnapshotStore

    EmailSnapshotStore().stash({"message_id": "msg-zz-4", "body_text": MARK, "referenced": True})
    row = _txn(refs=[{"source": "email", "ref": "msg-zz-4"}])
    _plant_image("cp_em", "email_snapshots", "msg-zz-4", {"id": "msg-zz-4", "body": MARK})
    _plant_image("cp_py", "payees", row["payee_id"], {"id": row["payee_id"], "name": MARK})
    P.purge_transactions([row["id"]])
    assert _image("cp_em") is None and _image("cp_py") is None
    assert _where_is(MARK) == []


def test_its_bank_snapshot_goes_and_another_rows_stays():
    from backend.db.connector_snapshot_store import ConnectorSnapshotStore

    snapshots = ConnectorSnapshotStore()
    snapshots.insert({"key": "simplefin:zz-1", "raw": {"memo": MARK}, "referenced": True})
    snapshots.insert({"key": "simplefin:zz-2", "raw": {"memo": "kept"}, "referenced": True})
    gone = _txn(refs=[{"source": "simplefin", "ref": "zz-1"}])
    _txn(memo="other", payee="Other", refs=[{"source": "simplefin", "ref": "zz-2"}])
    P.purge_transactions([gone["id"]])
    assert snapshots.get("simplefin", "zz-1") is None
    assert snapshots.get("simplefin", "zz-2") is not None
    assert _where_is(MARK) == []


def test_an_email_snapshot_stored_under_its_raw_message_id_goes_too():
    """Before the internal-id scheme a snapshot was keyed by the raw Message-ID; the booked ref
    is its internal id (``email_snapshots._find_snapshot``)."""
    from backend.core.email_ids import internal_email_id

    raw = "<legacy-zz@example.com>"
    PayloadTableStore(tables.email_snapshots).insert(
        {"id": "legacy-zz@example.com", "raw_message_id": raw, "body_text": MARK}
    )
    row = _txn(refs=[{"source": "email", "ref": internal_email_id(raw)}])
    P.purge_transactions([row["id"]])
    assert _where_is(MARK) == []


def test_a_merge_tombstone_a_fact_names_refuses_the_purge():
    keep = _txn(memo=f"{MARK} a", payee=f"{MARK} Clinic")
    dup = _txn(memo=f"{MARK} b", payee=f"{MARK} Clinic")
    L.merge_transactions(keep["id"], dup["id"], override=True)
    BookPayloadTableStore(tables.lots).insert({"id": "lot_tomb", "txn_id": dup["id"]})
    with pytest.raises(ConflictError, match="lots"):
        P.purge_transactions([keep["id"]])
    assert L.transactions.find_by_id(keep["id"]) is not None


def test_force_deleting_an_account_erases_another_accounts_tombstone_folded_into_it():
    L.accounts.insert({"id": "acc_old", "name": "Old", "kind": "asset", "currency": "SGD"})
    keep = L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-10-01",
            "description": f"{MARK} Clinic",
            "memo": f"{MARK} a",
            "from_account_id": "acc_old",
            "from_amount": 42.42,
            "source": "manual",
        }
    )
    dup = _txn(memo=f"{MARK} b")
    L.merge_transactions(keep["id"], dup["id"], override=True)
    assert L.delete_account("acc_old", force=True)
    assert L.transactions.find_by_id(dup["id"]) is None
    assert _where_is(MARK) == []


def test_secure_delete_is_on_for_the_purge_and_restored_after():
    with get_engine().connect() as conn:
        if conn.dialect.name != "sqlite":
            pytest.skip("SQLite pragma")
        before = conn.exec_driver_sql("PRAGMA secure_delete").scalar()
        with P.secure_delete(conn):
            assert conn.exec_driver_sql("PRAGMA secure_delete").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA secure_delete").scalar() == before


def test_each_table_is_read_once_whatever_the_number_of_rows():
    """An account force-delete erases thousands of rows under the ledger lock: the scans per
    table stay the same however many transactions go, never one per transaction id."""
    import unittest.mock as mock

    real = P._naming

    def scans(count: int) -> dict[str, int]:
        rows = [_txn(memo=f"{MARK} {count} {i}", payee=f"{MARK} Clinic") for i in range(count)]
        calls: dict[str, int] = {}

        def counting(conn, name, tokens, column="payload"):
            calls[name] = calls.get(name, 0) + 1
            return real(conn, name, tokens, column)

        with mock.patch.object(P, "_naming", counting):
            P.purge_transactions([r["id"] for r in rows])
        return calls

    assert scans(1) == scans(12)
