"""TransactionStore — DB-backed (plan 3 task 4). Full public-surface parity: append,
bulk_append, query_by_batch, query_txns' whole filter matrix, has_external_ref/
by_external_ref (the SQL-optimized pair, parity-tested against a Python scan),
select_where/bulk_delete_where (shared predicate), soft_delete, set_cleared,
mark_cleared_reconciled, bulk_set_deleted/bulk_soft_delete, make_transaction/_norm_*
helpers, plus the inherited-JsonStore CRUD surface. Also covers the two load-bearing,
non-obvious behaviors this store's rewrite depends on: the ID-COLLISION-TOLERANT
insert/upsert (update_transaction's rebuild-and-swap trick) and splits/external_refs
child-table explosion + checkpoint before-images per table.
"""

from __future__ import annotations

from backend.core.txn_sources import ext_ref_pairs
from backend.db.transaction_store import (
    TransactionStore,
    _norm_ext_refs,
    _norm_splits,
    make_transaction,
    txn_text,
)


def _txn(id=None, **kw):
    data = {"date": "2026-01-01", "kind": "expense", "from_amount": 10.0, "from_currency": "SGD"}
    if id is not None:
        data["id"] = id
    data.update(kw)
    return data


# ── make_transaction / _norm_* helpers (pure, byte-identical) ───────────────────────


def test_make_transaction_mints_id_and_normalizes():
    row = make_transaction({"date": "2026-01-01", "kind": "bogus", "from_amount": "-5"})
    assert row["id"].startswith("txn_")
    assert row["kind"] == "expense"  # unknown kind defaults
    assert row["from_amount"] == 5.0  # abs magnitude
    assert row["description"] == ""
    assert row["deleted"] is False


def test_make_transaction_honors_explicit_id():
    row = make_transaction({"id": "txn_fixed", "date": "2026-01-01"})
    assert row["id"] == "txn_fixed"


def test_norm_splits_drops_incomplete_lines_and_none_when_empty():
    assert _norm_splits(None) is None
    assert _norm_splits([]) is None
    assert (
        _norm_splits([{"category": "food"}]) is None
    )  # missing amount -> dropped -> empty -> None
    out = _norm_splits([{"category": "food", "amount": -5.0}, {"category": "x"}])
    assert out == [{"category": "food", "amount": 5.0, "memo": ""}]


def test_norm_ext_refs_drops_empty_ref_preserves_none_source():
    out = _norm_ext_refs(
        [{"source": None, "ref": "abc"}, {"ref": ""}, {"source": "csv", "ref": "1"}]
    )
    assert out == [{"source": None, "ref": "abc"}, {"source": "csv", "ref": "1"}]


def test_txn_text_reads_description_never_memo():
    assert txn_text({"description": "Coffee", "memo": "personal note"}) == "Coffee"
    assert txn_text({}) == ""


# ── append / bulk_append / find_by_id / all ──────────────────────────────────────────


def test_append_normalizes_via_make_transaction(db_isolated):
    store = TransactionStore()
    row = store.append(_txn(description="  Coffee  ".strip()))
    assert row["id"].startswith("txn_")
    assert store.find_by_id(row["id"])["description"] == "Coffee"


def test_bulk_append_normalizes_each_row(db_isolated):
    store = TransactionStore()
    rows = store.bulk_append([_txn(description="a"), _txn(description="b")])
    assert len(rows) == 2
    assert {r["description"] for r in rows} == {"a", "b"}
    assert store.count() == 2


def test_bulk_append_empty_is_noop(db_isolated):
    assert TransactionStore().bulk_append([]) == []


def test_all_returns_in_seq_order(db_isolated):
    store = TransactionStore()
    ids = [store.append(_txn(id=f"t{i}"))["id"] for i in range(5)]
    assert [t["id"] for t in store.all()] == ids


# ── splits + external_refs child-table explosion ─────────────────────────────────────


def test_splits_round_trip_through_payload_and_child_table(db_isolated):
    store = TransactionStore()
    row = store.append(
        _txn(
            from_amount=50.0,
            splits=[
                {"category": "food", "amount": 30.0},
                {"category": "transport", "amount": 20.0},
            ],
        )
    )
    fetched = store.find_by_id(row["id"])
    assert fetched["splits"] == [
        {"category": "food", "amount": 30.0, "memo": ""},
        {"category": "transport", "amount": 20.0, "memo": ""},
    ]
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import transaction_splits

    with get_engine().connect() as conn:
        rows = conn.execute(
            select(transaction_splits)
            .where(transaction_splits.c.txn_id == row["id"])
            .order_by(transaction_splits.c.idx)
        ).all()
    # PLAN 3 TASK 5 (revision 0008): `amount` is now Text, repr(float)-exact — the live
    # TransactionStore write path (`store.append`) goes through money_io's builders, which
    # default to `money_as_text=True`. `fetched["splits"]` above reads `payload` (untouched,
    # still raw JSON floats); only this promoted-column read sees the text encoding.
    assert [(r.category, r.amount) for r in rows] == [("food", "30.0"), ("transport", "20.0")]


def test_external_refs_round_trip_through_payload_and_child_table(db_isolated):
    store = TransactionStore()
    row = store.append(
        _txn(external_refs=[{"source": "csv", "ref": "row-1"}, {"source": None, "ref": "bare"}])
    )
    fetched = store.find_by_id(row["id"])
    assert fetched["external_refs"] == [
        {"source": "csv", "ref": "row-1"},
        {"source": None, "ref": "bare"},
    ]


def test_update_replaces_children_deterministically(db_isolated):
    """Editing a transaction's splits re-explodes the child table — old lines gone,
    new lines present. ``update_by_id`` is a BLIND merge (``{**item, **changes}``, matching
    the base ``JsonStore.update_by_id`` this replaces — see json_store.py) so the payload's
    ``splits`` come back exactly as given, with no normalization/default-filling; only the
    CHILD TABLE explosion (``money_io._explode_children``) fills a missing ``memo`` with
    ``""``, and only for that table's own columns — not the payload of record."""
    store = TransactionStore()
    row = store.append(_txn(from_amount=50.0, splits=[{"category": "food", "amount": 50.0}]))
    updated = store.update_by_id(
        row["id"],
        {
            "splits": [
                {"category": "transport", "amount": 20.0},
                {"category": "food", "amount": 30.0},
            ]
        },
    )
    assert updated["splits"] == [
        {"category": "transport", "amount": 20.0},
        {"category": "food", "amount": 30.0},
    ]
    assert store.find_by_id(row["id"])["splits"] == updated["splits"]


def test_delete_cascades_children(db_isolated):
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import transaction_external_refs, transaction_splits

    store = TransactionStore()
    row = store.append(
        _txn(
            splits=[{"category": "food", "amount": 10.0}],
            external_refs=[{"source": "csv", "ref": "r1"}],
        )
    )
    assert store.delete(row["id"]) is True
    with get_engine().connect() as conn:
        assert (
            conn.execute(
                select(transaction_splits).where(transaction_splits.c.txn_id == row["id"])
            ).fetchall()
            == []
        )
        assert (
            conn.execute(
                select(transaction_external_refs).where(
                    transaction_external_refs.c.txn_id == row["id"]
                )
            ).fetchall()
            == []
        )


# ── ID-COLLISION-TOLERANT insert (update_transaction's rebuild-and-swap trick) ───────


def test_append_tolerates_colliding_id_replacing_in_place(db_isolated):
    """The load-bearing finding: services.ledger.update_transaction relies on being able
    to append() a row whose id ALREADY exists (it will clean up with delete+upsert right
    after) — this must never raise, unlike a bare duplicate-PK INSERT would."""
    store = TransactionStore()
    original = store.append(_txn(id="txn_x", description="original"))
    rebuilt = store.append({**original, "description": "rebuilt"})
    assert rebuilt["id"] == "txn_x"
    assert store.count() == 1  # NOT two rows
    assert store.find_by_id("txn_x")["description"] == "rebuilt"


def test_the_full_update_transaction_dance_ends_with_one_row_at_the_end(db_isolated):
    """append(colliding id) -> delete(id) -> upsert(rebuilt-with-same-id): the exact
    sequence services.ledger.update_transaction runs. Must end with exactly one row,
    correct content, moved to the end of insertion order (a new/current max seq) —
    matching the old JsonStore-era observable behavior exactly."""
    store = TransactionStore()
    a = store.append(_txn(id="a", description="A"))
    store.append(_txn(id="b", description="B"))
    rebuilt = store.append({**a, "description": "A-edited"})  # collides with "a"
    store.delete(rebuilt["id"])
    rebuilt["id"] = "a"
    store.upsert(rebuilt)
    assert store.count() == 2
    assert store.find_by_id("a")["description"] == "A-edited"
    assert store.find_by_id("b")["description"] == "B"
    assert [t["id"] for t in store.all()] == ["b", "a"]  # "a" moved to the end


def test_bulk_insert_does_not_tolerate_collision(db_isolated):
    """Unlike single-row insert/upsert, bulk_insert is a strict raw multi-row INSERT —
    no real caller ever seeds it a colliding id, so a collision should raise."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    store = TransactionStore()
    store.append(_txn(id="dup"))
    with pytest.raises(IntegrityError):
        store.bulk_insert([make_transaction(_txn(id="dup"))])


# ── has_external_ref / by_external_ref — SQL vs scan parity ──────────────────────────


def _scan_by_external_ref(store: TransactionStore, source, ref):
    """The Python-scan reference implementation (mirrors the pre-task-4 store) used to
    prove SQL/scan parity — NOT a copy of the store's own logic (that would be
    tautological), independently re-derived from ext_ref_pairs + the documented
    contract (first non-deleted row, file/seq order)."""
    if not ref:
        return None
    target = (source, ref)
    for t in store.all():
        if not t.get("deleted") and target in ext_ref_pairs(t):
            return t
    return None


def test_by_external_ref_matches_scan_including_null_source_duplicate_gap(db_isolated):
    store = TransactionStore()
    store.append(_txn(id="t1", external_refs=[{"source": "csv", "ref": "row-1"}]))
    store.append(_txn(id="t2", external_refs=[{"source": None, "ref": "bare"}]))
    # The documented NULL-source dedup gap: two rows can legally share (None, ref) —
    # the unique index can't catch it (SQL NULL<>NULL), only the Python tuple-compare
    # scan enforces it. by_external_ref must still agree with the scan (both return the
    # FIRST match in seq order) even in this adversarial case.
    store.append(_txn(id="t3", external_refs=[{"source": None, "ref": "bare"}]))
    store.append(
        _txn(id="t4", deleted=True, external_refs=[{"source": "csv", "ref": "deleted-ref"}])
    )

    for source, ref in [
        ("csv", "row-1"),
        (None, "bare"),
        ("csv", "deleted-ref"),
        ("csv", "missing"),
        (None, "missing"),
    ]:
        scanned = _scan_by_external_ref(store, source, ref)
        found = store.by_external_ref(source, ref)
        scanned_id = scanned["id"] if scanned else None
        found_id = found["id"] if found else None
        assert found_id == scanned_id, f"mismatch for ({source!r}, {ref!r})"


def test_has_external_ref_true_false_and_blank_ref(db_isolated):
    store = TransactionStore()
    store.append(_txn(external_refs=[{"source": "csv", "ref": "row-1"}]))
    assert store.has_external_ref("csv", "row-1") is True
    assert store.has_external_ref("csv", "missing") is False
    assert store.has_external_ref(None, "row-1") is False  # different source
    assert store.has_external_ref("csv", "") is False
    assert store.has_external_ref("csv", None) is False


def test_by_external_ref_excludes_deleted_but_merge_tombstone_covered_by_canonical(db_isolated):
    """F13 identity contract: a user-deleted row frees its ref; a merge-tombstoned row's
    ref is covered by the canonical row's OWN external_refs (not by the tombstone)."""
    store = TransactionStore()
    store.append(
        _txn(id="deleted-user", deleted=True, external_refs=[{"source": "csv", "ref": "freed"}])
    )
    assert store.by_external_ref("csv", "freed") is None

    store.append(
        _txn(
            id="absorbed",
            deleted=True,
            merged_into="canon",
            external_refs=[{"source": "csv", "ref": "absorbed-ref"}],
        )
    )
    store.append(_txn(id="canon", external_refs=[{"source": "csv", "ref": "absorbed-ref"}]))
    owner = store.by_external_ref("csv", "absorbed-ref")
    assert owner["id"] == "canon"


# ── query_txns — full filter matrix ───────────────────────────────────────────────────


def _seed_query_fixture(store):
    rows = [
        {
            "id": "a",
            "date": "2024-01-05",
            "kind": "expense",
            "category": "transport",
            "description": "Uber to airport",
            "tags": ["reimbursable"],
            "from_account_id": "meridian",
            "from_amount": 30.0,
            "payee_id": "pay_1",
            "batch_id": "batch-1",
        },
        {
            "id": "b",
            "date": "2024-02-10",
            "kind": "expense",
            "category": "food",
            "description": "Grab dinner with UBER eats voucher",
            "tags": [],
            "from_account_id": "beacon",
            "from_amount": 18.0,
        },
        {
            "id": "c",
            "date": "2024-03-01",
            "kind": "income",
            "category": "income",
            "description": "Monthly salary",
            "tags": ["payroll"],
            "to_account_id": "meridian",
            "to_amount": 5000.0,
            "payee_id": "pay_2",
        },
        {
            "id": "d",
            "date": "2024-03-15",
            "kind": "transfer",
            "category": None,
            "description": "Move to savings",
            "tags": ["reimbursable"],
            "from_account_id": "meridian",
            "from_amount": 1000.0,
            "to_account_id": "beacon",
            "to_amount": 1000.0,
        },
        {
            "id": "deleted",
            "date": "2024-01-01",
            "kind": "expense",
            "category": "food",
            "description": "Uber stale",
            "from_account_id": "meridian",
            "from_amount": 9.0,
            "deleted": True,
        },
        {
            "id": "split-row",
            "date": "2024-04-01",
            "kind": "expense",
            "category": None,
            "description": "Split purchase",
            "from_account_id": "meridian",
            "from_amount": 40.0,
            "splits": [
                {"category": "food", "amount": 20.0},
                {"category": "transport", "amount": 20.0},
            ],
        },
    ]
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts(rows)
    for r in rows:
        store.insert(make_transaction(r))
    return {r["id"] for r in rows}


def test_query_txns_excludes_deleted_by_default(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    ids = {t["id"] for t in store.query_txns()}
    assert "deleted" not in ids
    assert "deleted" in {t["id"] for t in store.query_txns(include_deleted=True)}


def test_query_txns_kind_single_and_multi(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(kind="income")} == {"c"}
    assert {t["id"] for t in store.query_txns(kind=["income", "transfer"])} == {"c", "d"}


def test_query_txns_category_matches_split_lines_too(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    # split-row carries top-level category=None but a split line has category="food".
    assert "split-row" in {t["id"] for t in store.query_txns(category="food")}
    assert {t["id"] for t in store.query_txns(category=["transport"])} >= {"a", "split-row"}


def test_query_txns_account_id_matches_either_leg(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(account_id="meridian")} == {
        "a",
        "c",
        "d",
        "split-row",
    }


def test_query_txns_account_ids_or_filter(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(account_ids=["beacon"])} == {"b", "d"}


def test_query_txns_payee_id_single_and_multi(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(payee_id="pay_1")} == {"a"}
    assert {t["id"] for t in store.query_txns(payee_id=["pay_1", "pay_2"])} == {"a", "c"}


def test_query_txns_date_range_inclusive(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    ids = {t["id"] for t in store.query_txns(date_from="2024-02-01", date_to="2024-03-15")}
    assert ids == {"b", "c", "d"}


def test_query_txns_q_matches_description_category_case_insensitive_and_blank_is_noop(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(q="uber")} == {"a", "b"}
    assert {t["id"] for t in store.query_txns(q="transport")} >= {"a"}
    # An all-whitespace q must not filter anything — same result as no q at all.
    assert {t["id"] for t in store.query_txns(q="   ")} == {t["id"] for t in store.query_txns()}


def test_query_txns_tag_membership_case_insensitive(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_txns(tag="Reimbursable")} == {"a", "d"}
    assert {t["id"] for t in store.query_txns(tag=["reimbursable", "payroll"])} == {"a", "c", "d"}
    assert store.query_txns(tag="nonexistent") == []


def test_query_txns_filters_and_combined(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    out = store.query_txns(q="uber", kind="expense", category="food")
    assert {t["id"] for t in out} == {"b"}


def test_query_by_batch(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    assert {t["id"] for t in store.query_by_batch("batch-1")} == {"a"}
    assert store.query_by_batch("no-such-batch") == []


def test_query_by_batch_matches_a_full_scan_through_deletes_and_batch_moves(db_isolated):
    store = TransactionStore()
    store.bulk_append(
        [
            _txn("b1", batch_id="imp-1"),
            _txn("b2", batch_id="imp-1"),
            _txn("b3", batch_id="imp-2"),
            _txn("b4"),
        ]
    )
    store.bulk_soft_delete(["b2"])
    store.update_by_id("b3", {"batch_id": "imp-1"})

    def scan(batch, include_deleted):
        rows = [t for t in store.all() if t.get("batch_id") == batch]
        return rows if include_deleted else [t for t in rows if not t.get("deleted")]

    for batch in ("imp-1", "imp-2", "missing"):
        for include_deleted in (True, False):
            assert store.query_by_batch(batch, include_deleted=include_deleted) == scan(
                batch, include_deleted
            )


def test_query_by_batch_reads_only_the_batch_not_the_whole_ledger(db_isolated, monkeypatch):
    # The Statements list asks once per import; decoding every transaction each time was most
    # of its cost (2026-10-26).
    store = TransactionStore()
    store.bulk_append([_txn("c1", batch_id="imp-9"), _txn("c2")])

    def no_full_read(*_a, **_k):
        raise AssertionError("query_by_batch must not decode the whole ledger")

    monkeypatch.setattr(store, "all", no_full_read)
    assert [t["id"] for t in store.query_by_batch("imp-9")] == ["c1"]


# ── select_where / bulk_delete_where — shared predicate, protected_ids ───────────────


def test_select_where_and_bulk_delete_where_share_the_same_rows(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    selected = store.select_where(kind="expense", protected_ids=frozenset())
    selected_ids = {t["id"] for t in selected}
    n = store.bulk_delete_where(kind="expense", protected_ids=frozenset())
    assert n == len(selected_ids)
    for tid in selected_ids:
        assert store.find_by_id(tid)["deleted"] is True


def test_bulk_delete_where_spares_protected_ids(db_isolated):
    store = TransactionStore()
    _seed_query_fixture(store)
    n = store.bulk_delete_where(kind="expense", protected_ids=frozenset({"a"}))
    assert store.find_by_id("a")["deleted"] is False
    assert n < len({t["id"] for t in store.query_txns(kind="expense", include_deleted=True)})


def test_bulk_delete_where_requires_a_real_filter_match_only(db_isolated):
    """No implicit whole-ledger wipe — this store method has no built-in guard (the
    choke ledger.delete_transactions_where owns the 'at least one filter' refusal), but
    passing narrow filters here must only ever touch matching rows."""
    store = TransactionStore()
    _seed_query_fixture(store)
    n = store.bulk_delete_where(batch_id="batch-1", protected_ids=frozenset())
    assert n == 1
    assert store.find_by_id("a")["deleted"] is True
    assert store.find_by_id("b")["deleted"] is False


# ── soft_delete / set_cleared / mark_cleared_reconciled / bulk_set_deleted ────────────


def test_soft_delete(db_isolated):
    store = TransactionStore()
    row = store.append(_txn())
    updated = store.soft_delete(row["id"])
    assert updated["deleted"] is True
    assert store.soft_delete("nope") is None


def test_set_cleared(db_isolated):
    store = TransactionStore()
    row = store.append(_txn(cleared=False))
    updated = store.set_cleared(row["id"], True)
    assert updated["cleared"] is True
    assert store.find_by_id(row["id"])["cleared"] is True


def test_mark_cleared_reconciled_only_touches_cleared_legs_on_the_account(db_isolated):
    from tests.conftest import seed_stub_accounts

    store = TransactionStore()
    seed_stub_accounts([{"from_account_id": "acc1"}, {"from_account_id": "acc2"}])
    store.append(_txn(id="a", from_account_id="acc1", cleared=True))
    store.append(_txn(id="b", from_account_id="acc1", cleared=False))  # not cleared
    store.append(_txn(id="c", from_account_id="acc2", cleared=True))  # different account
    n = store.mark_cleared_reconciled("acc1", "2026-02-01")
    assert n == 1
    assert store.find_by_id("a")["reconciled_at"] == "2026-02-01"
    # "b"/"c" were never touched — make_transaction() never seeds a "reconciled_at" key
    # at all (matching the original store), so it's genuinely ABSENT, not None.
    assert store.find_by_id("b").get("reconciled_at") is None
    assert store.find_by_id("c").get("reconciled_at") is None
    # idempotent: re-running with the SAME date touches nothing more
    assert store.mark_cleared_reconciled("acc1", "2026-02-01") == 0


def test_bulk_set_deleted_and_bulk_soft_delete(db_isolated):
    store = TransactionStore()
    store.append(_txn(id="a"))
    store.append(_txn(id="b"))
    affected, not_found = store.bulk_set_deleted(["a", "missing"], deleted=True)
    assert affected == ["a"]
    assert not_found == ["missing"]
    assert store.find_by_id("a")["deleted"] is True

    deleted, nf = store.bulk_soft_delete(["b"])
    assert deleted == ["b"]
    assert store.find_by_id("b")["deleted"] is True


def test_bulk_set_deleted_affected_order_matches_file_order_not_input_order(db_isolated):
    store = TransactionStore()
    store.append(_txn(id="a"))
    store.append(_txn(id="b"))
    store.append(_txn(id="c"))
    affected, _ = store.bulk_set_deleted(["c", "a", "b"], deleted=True)
    assert affected == ["a", "b", "c"]  # seq/insertion order, not input order


# ── generic CRUD parity (upsert/update_by_id/bulk_patch/replace_all) ─────────────────


def test_upsert_keeps_seq_on_replace(db_isolated):
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import transactions as transactions_table

    store = TransactionStore()
    store.append(_txn(id="t0"))
    store.append(_txn(id="t1"))
    with get_engine().connect() as conn:
        seq_before = conn.execute(
            select(transactions_table.c.seq).where(transactions_table.c.id == "t1")
        ).scalar_one()
    store.upsert(make_transaction({"id": "t1", "date": "2026-02-01", "description": "renamed"}))
    with get_engine().connect() as conn:
        seq_after = conn.execute(
            select(transactions_table.c.seq).where(transactions_table.c.id == "t1")
        ).scalar_one()
    assert seq_after == seq_before
    assert store.find_by_id("t1")["description"] == "renamed"


def test_bulk_patch_atomic_multi_row(db_isolated):
    store = TransactionStore()
    store.append(_txn(id="a"))
    store.append(_txn(id="b"))
    patched = store.bulk_patch(
        {"a": {"cleared": True}, "b": {"cleared": True}, "missing": {"x": 1}}
    )
    assert set(patched) == {"a", "b"}
    assert store.find_by_id("a")["cleared"] is True
    assert store.find_by_id("b")["cleared"] is True


def test_replace_all_wipes_and_renumbers_incl_children(db_isolated):
    store = TransactionStore()
    store.append(_txn(id="old", splits=[{"category": "food", "amount": 5.0}]))
    new_rows = [make_transaction({"id": "new", "date": "2026-01-01"})]
    store.replace_all(new_rows)
    assert store.find_by_id("old") is None
    assert [t["id"] for t in store.all()] == ["new"]

    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import transaction_splits

    with get_engine().connect() as conn:
        assert conn.execute(select(transaction_splits)).fetchall() == []


def test_replace_all_empty_wipes_table(db_isolated):
    store = TransactionStore()
    store.append(_txn())
    store.replace_all([])
    assert store.all() == []


# ── checkpoint hook wiring — per table, on the real ai.checkpoint registry ───────────


def test_write_fires_checkpoint_hook_for_parent_and_children(db_isolated):
    from backend.ai import checkpoint as cp

    store = TransactionStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        row = store.append(_txn(splits=[{"category": "food", "amount": 10.0}]))
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "transactions" in entry["db_tables"]
    assert "transaction_splits" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    assert store.find_by_id(row["id"]) is None


def test_conn_composition_atomic_with_caller(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import transactions as transactions_table

    store = TransactionStore()
    with locked_unit_of_work(transactions_table) as conn:
        store.append(_txn(id="t1"), conn=conn)
    assert store.find_by_id("t1") is not None
