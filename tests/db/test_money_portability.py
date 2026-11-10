"""Money core joins portable export/import, ``/api/reset`` and seeding (plan 3, task 6).

Extends ``tests/db/test_portability_db_bridge.py`` (the plan-2 reference-data bridge) to the
six money files — ``accounts.json``, ``transactions.json``, ``txn_audit.json``,
``import_batches.json``, ``statements.json``, ``merge_journal.json`` — sourced from
``backend.db.money_io`` via ``backend.db.portable_bridge``. Two properties that make the money
bridge different from the reference-data one (see ``portable_bridge.py``'s own docstring):

1. UNDOABLE — the Plan-2 no-hook ruling on a bridged bundle restore is explicitly scoped to
   reference data; money is the OPPOSITE ruling (ADR'd in T8). ``test_money_bundle_import_via_
   api_is_undoable`` proves this end to end: import through the real ``/api/data/import`` router (the
   ``begin_turn``/``end_turn`` wrap in ``api/portability.py``), ``ai.checkpoint.preview()`` shows
   the touched db: tables, ``restore()`` reverts every row.
2. CROSS-FILE FK COUPLING — ``transactions.from_account_id``/``to_account_id`` are ``ON DELETE
   RESTRICT`` onto ``accounts.id``, so the six are one referential unit, wiped/imported via
   ``portable_bridge.apply_money_bundle`` rather than the generic per-file loop.
   ``test_reimport_replaces_existing_money_data_entirely_fk_safe`` exercises the FULL
   wipe-old-then-insert-new sequence against PRE-EXISTING, referentially-linked data (not just
   an empty database) — the scenario the dedicated group orchestration exists for.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.core import portable
from backend.db import money_io, portable_bridge
from backend.db import tables as t
from backend.db.engine import get_engine
from backend.services import portability as svc

# ── Fixtures — deliberately cross-referential (acc_a.opening_txn_id -> txn_open;
# txn_split -> acc_a; both directions the cross-file FK actually enforces) ────────────

ACCOUNTS = [
    {
        "id": "acc_a",
        "name": "Checking",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": "txn_open",
        "archived": False,
    },
    {
        "id": "acc_b",
        "name": "Savings",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": None,
        "archived": False,
    },
]

TRANSACTIONS = [
    {
        "id": "txn_open",
        "date": "2026-01-01",
        "kind": "adjustment",
        "category": None,
        "from_account_id": None,
        "to_account_id": "acc_a",
        "from_amount": None,
        "from_currency": None,
        "to_amount": 1000.0,
        "to_currency": "SGD",
        "base_amount": 1000.0,
        "base_currency": "SGD",
        "fx_rate": None,
        "source": "manual",
        "description": "Starting Balance",
        "splits": None,
        "external_refs": [],
        "cleared": True,
        "deleted": False,
    },
    {
        "id": "txn_split",
        "date": "2026-01-05",
        "kind": "expense",
        "category": None,
        "from_account_id": "acc_a",
        "to_account_id": None,
        "from_amount": 50.0,
        "from_currency": "SGD",
        "to_amount": None,
        "to_currency": None,
        "base_amount": 50.0,
        "base_currency": "SGD",
        "fx_rate": 1.0,
        "source": "manual",
        "description": "Groceries",
        "splits": [
            {"category": "food", "amount": 30.0, "memo": "produce"},
            {"category": "household", "amount": 20.0, "memo": ""},
        ],
        "external_refs": [
            {"source": "csv", "ref": "row-1"},
            {"source": None, "ref": "bare-ref"},
        ],
        "cleared": True,
        "deleted": False,
    },
]

TXN_AUDIT = [
    {
        "id": "evt1",
        "txn_id": "txn_split",
        "op": "create",
        "ts": "2026-01-05T00:00:01Z",
        "actor": "user",
        "surface": "web",
    },
]

IMPORT_BATCHES = [
    {
        "id": "imp1",
        "created_at": "2026-01-05T00:00:00Z",
        "description": "CSV import",
        "source_account_id": "acc_a",
        "transaction_count": 1,
        "status": "active",
    },
]

STATEMENTS = [
    {
        "id": "money_portability_stmt_rows",
        "created_at": "2026-01-04T00:00:00Z",
        "filename": "jan.pdf",
        "mime": "application/pdf",
        "size_bytes": 2048,
        "file_hash": "hash1",
        "file_path": "statements/stmt.pdf",
        "kind": "pdf_pages",
        "parsed_rows": [
            {"date": "2026-01-05", "amount": 50.0, "direction": "debit", "description": "Groceries"}
        ],
        "parsed_at": "2026-01-04T01:00:00Z",
        "account_id": "acc_a",
        "batch_ids": ["imp1"],
    },
    {
        # the DEAD-key case: `import_batch_ids`, never renamed to `batch_ids`.
        "id": "money_portability_stmt_dead_key",
        "created_at": "2026-01-03T00:00:00Z",
        "filename": "feb.csv",
        "mime": "text/csv",
        "size_bytes": 512,
        "file_hash": "hash2",
        "file_path": "statements/feb.csv",
        "kind": "text",
        "parsed_rows": None,
        "parsed_at": None,
        "account_id": None,
        "import_batch_ids": ["imp_old"],
    },
]

MERGE_JOURNAL = [
    {
        "absorbed_id": "txn_absorbed",
        "canonical_id": "txn_split",
        "created_at": "2026-01-06T00:00:00Z",
    },
]

# A second, entirely non-overlapping money dataset — for the reimport-replaces and
# undo tests, which need to prove the group transitions from one FK-linked set to
# another, not merely from empty to populated.
ACCOUNTS_V2 = [
    {
        "id": "acc_new",
        "name": "New Account",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": None,
        "archived": False,
    },
]
TRANSACTIONS_V2 = [
    {
        "id": "txn_new",
        "date": "2026-03-01",
        "kind": "income",
        "category": None,
        "from_account_id": None,
        "to_account_id": "acc_new",
        "from_amount": None,
        "from_currency": None,
        "to_amount": 500.0,
        "to_currency": "SGD",
        "base_amount": 500.0,
        "base_currency": "SGD",
        "fx_rate": 1.0,
        "source": "manual",
        "description": "New income",
        "splits": None,
        "external_refs": [],
        "cleared": True,
        "deleted": False,
    },
]


def _seed_all(conn) -> None:
    money_io.import_accounts(conn, ACCOUNTS)
    money_io.import_transactions(conn, TRANSACTIONS)
    money_io.import_txn_audit(conn, TXN_AUDIT)
    money_io.import_import_batches(conn, IMPORT_BATCHES)
    money_io.import_statements(conn, STATEMENTS)
    money_io.import_merge_journal(conn, MERGE_JOURNAL)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors test_portability_db_bridge.py's own helper."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries = {}
        for name, data in files.items():
            zf.writestr(name, data)
            entries[name] = {"sha256": _sha(data)}
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": entries}),
        )
    return buf.getvalue()


# ── Registry — the money six join BRIDGED alongside the reference-data three ─────────


def test_money_six_are_in_the_real_manifest_and_bridge():
    from backend.paths import (
        ACCOUNTS_JSON,
        IMPORT_BATCHES_JSON,
        MERGE_JOURNAL_JSON,
        STATEMENTS_JSON,
        TRANSACTIONS_JSON,
        TXN_AUDIT_JSON,
    )

    names = {p.name for p in portable.PORTABLE_FILES}
    for path in (
        ACCOUNTS_JSON,
        TRANSACTIONS_JSON,
        TXN_AUDIT_JSON,
        IMPORT_BATCHES_JSON,
        STATEMENTS_JSON,
        MERGE_JOURNAL_JSON,
    ):
        assert path.name in names, f"{path.name} missing from PORTABLE_FILES"
        assert path.name in portable_bridge.BRIDGED
        assert path.name in portable_bridge.MONEY_FILES


# ── Export: DB-sourced, under the original filenames, byte-compatible ────────────────


def test_export_bundle_contains_all_six_money_files_db_sourced(db_isolated):
    with get_engine().begin() as conn:
        _seed_all(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert set(portable_bridge.MONEY_FILES) <= names
        assert json.loads(zf.read("accounts.json")) == ACCOUNTS
        assert json.loads(zf.read("transactions.json")) == TRANSACTIONS
        assert json.loads(zf.read("txn_audit.json")) == TXN_AUDIT
        assert json.loads(zf.read("import_batches.json")) == IMPORT_BATCHES
        assert json.loads(zf.read("statements.json")) == STATEMENTS
        assert json.loads(zf.read("merge_journal.json")) == MERGE_JOURNAL
        accounts_bytes = zf.read("accounts.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly
    # (json.dumps(..., ensure_ascii=False, indent=2), .encode("utf-8")) — same proof
    # test_portability_db_bridge.py makes for instruments.json.
    with get_engine().connect() as conn:
        expected = json.dumps(money_io.export_accounts(conn), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
    assert accounts_bytes == expected


# ── Import: old-bundle restore, children exploded, parsed files, dead-key normalize ──


def test_old_bundle_import_lands_rows_children_exploded_parsed_written_dead_key_normalized(
    db_isolated,
):
    """A hand-built bundle stands in for one exported from a pre-cutover (plain JSON file)
    instance — old and new bundles are handled identically, by filename alone."""
    bundle = _build_bundle(
        {
            "accounts.json": json.dumps(ACCOUNTS).encode(),
            "transactions.json": json.dumps(TRANSACTIONS).encode(),
            "txn_audit.json": json.dumps(TXN_AUDIT).encode(),
            "import_batches.json": json.dumps(IMPORT_BATCHES).encode(),
            "statements.json": json.dumps(STATEMENTS).encode(),
            "merge_journal.json": json.dumps(MERGE_JOURNAL).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= set(portable_bridge.MONEY_FILES)
    assert result["row_counts"]["accounts.json"] == len(ACCOUNTS)
    assert result["row_counts"]["transactions.json"] == len(TRANSACTIONS)
    assert result["row_counts"]["statements.json"] == len(STATEMENTS)

    with get_engine().connect() as conn:
        assert money_io.export_accounts(conn) == ACCOUNTS
        assert money_io.export_transactions(conn) == TRANSACTIONS
        assert money_io.export_txn_audit(conn) == TXN_AUDIT
        assert money_io.export_import_batches(conn) == IMPORT_BATCHES
        assert money_io.export_statements(conn) == STATEMENTS
        assert money_io.export_merge_journal(conn) == MERGE_JOURNAL

        # Children exploded: transaction_splits/transaction_external_refs match a fresh
        # explode of each transaction's own payload.
        money_io.verify_transaction_children(conn)
        split_rows = conn.execute(
            select(t.transaction_splits).where(t.transaction_splits.c.txn_id == "txn_split")
        ).all()
        assert len(split_rows) == 2
        ref_rows = conn.execute(
            select(t.transaction_external_refs).where(
                t.transaction_external_refs.c.txn_id == "txn_split"
            )
        ).all()
        assert len(ref_rows) == 2

        # Dead-key statement: `import_batch_ids` normalized into the promoted `batch_ids`
        # column; payload stays verbatim under whichever key the source row actually used.
        dead_key_row = conn.execute(
            select(t.statements_meta).where(
                t.statements_meta.c.id == "money_portability_stmt_dead_key"
            )
        ).one()
        assert dead_key_row.batch_ids == ["imp_old"]
        assert dead_key_row.payload.get("import_batch_ids") == ["imp_old"]
        assert "batch_ids" not in dead_key_row.payload

    # Parsed file written back out to disk (money_io's payload/parsed_rows split).
    parsed_path = money_io.statements_files_dir() / "money_portability_stmt_rows.parsed.json"
    assert parsed_path.exists()
    assert json.loads(parsed_path.read_text(encoding="utf-8")) == STATEMENTS[0]["parsed_rows"]
    parsed_path.unlink()  # tidy the per-test isolated statements dir


def test_reimport_replaces_existing_money_data_entirely_fk_safe(db_isolated):
    """The scenario apply_money_bundle's dedicated group orchestration exists for: OLD
    accounts (referenced by OLD transactions) are fully wiped and replaced by an entirely
    NEW, non-overlapping set — not just an import into a previously-empty database."""
    with get_engine().begin() as conn:
        money_io.import_accounts(conn, ACCOUNTS)
        money_io.import_transactions(conn, TRANSACTIONS)

    bundle = _build_bundle(
        {
            "accounts.json": json.dumps(ACCOUNTS_V2).encode(),
            "transactions.json": json.dumps(TRANSACTIONS_V2).encode(),
        }
    )
    result = svc.apply_import(bundle)

    assert result["row_counts"]["accounts.json"] == len(ACCOUNTS_V2)
    assert result["row_counts"]["transactions.json"] == len(TRANSACTIONS_V2)
    with get_engine().connect() as conn:
        assert money_io.export_accounts(conn) == ACCOUNTS_V2
        assert money_io.export_transactions(conn) == TRANSACTIONS_V2


def test_import_absent_money_file_wipes_that_store_to_empty(db_isolated):
    """Matches the non-bridged "absent portable file resets to empty" contract."""
    with get_engine().begin() as conn:
        money_io.import_merge_journal(conn, MERGE_JOURNAL)

    bundle = _build_bundle(
        {
            "accounts.json": json.dumps(ACCOUNTS).encode(),
            "transactions.json": json.dumps(TRANSACTIONS).encode(),
            # merge_journal.json intentionally absent.
        }
    )
    result = svc.apply_import(bundle)

    assert "merge_journal.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert money_io.export_merge_journal(conn) == []


def test_corrupt_money_bundle_import_rolls_back_atomically(db_isolated):
    """A transactions.json entry referencing a nonexistent account fails the real FK
    (transactions.from_account_id -> accounts.id) — the whole money-group transaction
    (INCLUDING the accounts wipe+reinsert, which by itself would have succeeded) rolls
    back: pre-import data survives completely untouched, not partially replaced. Proves
    the group is one atomic unit, not six independently-atomic files.

    ALSO the regression test for fix round 1's Important finding (empirically
    reproduced by the reviewer): a statement with a real parsed-rows file is seeded
    alongside accounts/transactions — statements.json is deliberately ABSENT from the
    corrupt bundle, so its row is only ever touched by apply_money_bundle's WIPE phase
    (which runs unconditionally for all six money files, regardless of which are
    present — see portable_bridge.apply_money_bundle's own docstring), never a
    reimport. Before the fix, that wipe phase unlinked the parsed file from disk
    IMMEDIATELY (inside the still-open transaction); the later transactions.json
    failure then rolled back the DB delete of the statement row, but the file stayed
    gone — a dangling parsed_rows_path that made StatementStore().all() raise
    FileNotFoundError for every caller, forever, not just for this one statement."""
    with get_engine().begin() as conn:
        money_io.import_accounts(conn, ACCOUNTS)
        money_io.import_transactions(conn, TRANSACTIONS)
        money_io.import_statements(conn, STATEMENTS)

    parsed_path = money_io.statements_files_dir() / "money_portability_stmt_rows.parsed.json"
    assert parsed_path.exists(), (
        "sanity: the parsed-rows file landed on disk before the failed import"
    )

    corrupt_txns = [
        TRANSACTIONS[0],
        {
            "id": "txn_bad",
            "date": "2026-02-01",
            "kind": "expense",
            "category": None,
            "from_account_id": "acc_does_not_exist",
            "to_account_id": None,
            "from_amount": 10.0,
            "from_currency": "SGD",
            "to_amount": None,
            "to_currency": None,
            "base_amount": 10.0,
            "base_currency": "SGD",
            "fx_rate": 1.0,
            "source": "manual",
            "description": "dangling ref",
            "splits": None,
            "external_refs": [],
            "cleared": True,
            "deleted": False,
        },
    ]
    bundle = _build_bundle(
        {
            "accounts.json": json.dumps(ACCOUNTS).encode(),
            "transactions.json": json.dumps(corrupt_txns).encode(),
            # statements.json intentionally absent — its row is touched ONLY by the
            # wipe phase (always runs for all six), never by a reimport, so this
            # proves the wipe phase itself defers deletion, not merely "a reimport
            # happened to restore the file somehow".
        }
    )

    with pytest.raises(IntegrityError):
        svc.apply_import(bundle)

    with get_engine().connect() as conn:
        assert money_io.export_accounts(conn) == ACCOUNTS
        assert money_io.export_transactions(conn) == TRANSACTIONS
        assert money_io.export_statements(conn) == STATEMENTS

    # FIX 1: a rolled-back transaction's filesystem side effects must never have
    # happened at all — deletes are deferred until after commit, so a rollback here
    # means nothing was ever unlinked.
    assert parsed_path.exists(), (
        "FIX 1 regression: the parsed-rows file was deleted despite the owning "
        "transaction rolling back — a dangling parsed_rows_path would brick "
        "StatementStore reads for this row permanently"
    )

    # FIX 2 (defense-in-depth): the store must be fully, normally readable — not just
    # "the file happens to still exist", but genuinely usable end to end.
    from backend.db.statement_store import StatementStore

    all_statements = StatementStore().all()
    matched = next(s for s in all_statements if s["id"] == "money_portability_stmt_rows")
    assert matched["parsed_rows"] == STATEMENTS[0]["parsed_rows"]

    parsed_path.unlink()  # tidy the per-test isolated statements dir


def test_reset_failure_after_statements_wipe_leaves_parsed_file_on_disk(db_isolated, monkeypatch):
    """The /api/reset counterpart to the corrupt-import test above: if a wipe step
    AFTER statements.json in _MONEY_WIPE_ORDER raises, reset_bridged_stores()'s whole
    transaction (including the statements_meta DELETE) rolls back — and since
    reset_bridged_stores() only unlinks wipe_all()'s returned paths AFTER that
    transaction has committed, the parsed file must still be on disk too. Cheaply
    constructible via the same monkeypatch-a-wipe-function technique
    test_export_aborts_loudly_when_a_bridged_export_fails (test_portability_db_bridge.py)
    already uses for the reference-data bridge."""
    with get_engine().begin() as conn:
        money_io.import_statements(conn, STATEMENTS)

    parsed_path = money_io.statements_files_dir() / "money_portability_stmt_rows.parsed.json"
    assert parsed_path.exists()

    def _boom(_conn):
        raise RuntimeError("simulated failure after statements wipe")

    # merge_journal.json wipes strictly AFTER statements.json in _MONEY_WIPE_ORDER,
    # forcing the failure to land after _wipe_statements_meta has already returned its
    # file-deletion list, inside the SAME transaction wipe_all() runs.
    monkeypatch.setitem(portable_bridge._WIPE_FNS, "merge_journal.json", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert money_io.export_statements(conn) == STATEMENTS

    assert parsed_path.exists(), (
        "FIX 1 regression (reset path): the parsed-rows file was deleted despite "
        "reset_bridged_stores()'s transaction rolling back"
    )
    parsed_path.unlink()


def test_reimport_same_statement_id_keeps_the_freshly_written_file(db_isolated):
    """Fix round 2 regression (Blocker, empirically reproduced by the reviewer): a bundle
    reimporting the SAME statement id with NEW parsed_rows content must not lose the
    freshly-written file. The bug fix round 1 introduced: the wipe phase collects the OLD
    statement's parsed_rows_path for deletion; the import phase's own
    ``money_io._statement_row`` then rewrites that EXACT deterministic path
    (``statements_parsed/<id>.json``) with the NEW content, inside the SAME transaction —
    reimporting the SAME backup twice (the primary real-world use of ``/api/data/import``) is
    exactly this shape. A post-commit unlink of the (stale) collected path would delete
    the file that was just correctly written, leaving the surviving DB row point at
    nothing — masked by fix round 1's own tolerant reinline (FIX 2) as
    ``parsed_rows=None`` with only a WARNING logged.

    Also proves the mirror image in the SAME bundle: a statement id NOT present in the
    reimported bundle (Y) is legitimately orphaned by the wipe and must STILL be deleted
    post-commit — the round-2 fix must not defeat round 1's own "orphans still get
    deleted" guarantee while closing this gap.
    """
    stmt_x_old = {
        "id": "money_portability_reimport_x",
        "created_at": "2026-01-01T00:00:00Z",
        "filename": "x.pdf",
        "mime": "application/pdf",
        "size_bytes": 100,
        "file_hash": "hashx1",
        "file_path": "statements/x.pdf",
        "kind": "pdf_pages",
        "parsed_rows": [{"amount": 1.0, "description": "old"}],
        "parsed_at": "2026-01-01T00:00:01Z",
        "account_id": None,
        "batch_ids": [],
    }
    stmt_y = {
        "id": "money_portability_reimport_y",
        "created_at": "2026-01-02T00:00:00Z",
        "filename": "y.pdf",
        "mime": "application/pdf",
        "size_bytes": 200,
        "file_hash": "hashy",
        "file_path": "statements/y.pdf",
        "kind": "pdf_pages",
        "parsed_rows": [{"amount": 2.0, "description": "y"}],
        "parsed_at": "2026-01-02T00:00:01Z",
        "account_id": None,
        "batch_ids": [],
    }
    with get_engine().begin() as conn:
        money_io.import_statements(conn, [stmt_x_old, stmt_y])

    path_x = money_io.statements_files_dir() / "money_portability_reimport_x.parsed.json"
    path_y = money_io.statements_files_dir() / "money_portability_reimport_y.parsed.json"
    assert path_x.exists()
    assert path_y.exists()
    assert json.loads(path_x.read_text()) == [{"amount": 1.0, "description": "old"}]

    stmt_x_new = {**stmt_x_old, "parsed_rows": [{"amount": 99.0, "description": "new"}]}
    bundle = _build_bundle(
        {
            "statements.json": json.dumps([stmt_x_new]).encode(),
            # money_portability_reimport_y intentionally absent -> legitimately orphaned.
        }
    )

    result = svc.apply_import(bundle)
    assert result["row_counts"]["statements.json"] == 1

    # X: reimported with the SAME id -> the freshly-written file must survive, holding
    # the NEW content, and the store must read it back correctly — never a
    # WARNING-masked None.
    assert path_x.exists(), (
        "FIX round 2 regression: reimporting the same statement id deleted the "
        "freshly-written parsed file"
    )
    assert json.loads(path_x.read_text()) == [{"amount": 99.0, "description": "new"}]

    from backend.db.statement_store import StatementStore

    all_statements = StatementStore().all()
    matched_x = next(s for s in all_statements if s["id"] == "money_portability_reimport_x")
    assert matched_x["parsed_rows"] == [{"amount": 99.0, "description": "new"}]

    # Y: the mirror image — legitimately orphaned (absent from the reimported bundle) ->
    # still deleted post-commit, proving the round-2 fix didn't defeat round 1's own
    # orphan-cleanup guarantee.
    assert not path_y.exists(), (
        "a statement id absent from the reimported bundle must still have its orphaned "
        "parsed file deleted post-commit"
    )

    path_x.unlink()  # tidy the per-test isolated statements dir (Y is already gone)


def test_importing_the_same_full_bundle_twice_round_trips_via_staged_temp_paths(db_isolated):
    """RFC §6 test case (c): a bundle imported TWICE IN A ROW with IDENTICAL bytes (the real
    "the user hit import again" case — not just a reimport with different content, which
    ``test_reimport_same_statement_id_keeps_the_freshly_written_file`` already covers) must
    round-trip cleanly both times. Proves ``money_io.import_statements``'s per-ROW staged
    temp path (``<final>.import-tmp-<8 hex chars>``, a FRESH ``uuid4`` suffix minted on
    EVERY call — never reused across import runs) never collides with itself and never
    leaves a stale temp file behind between the two runs."""
    stmt = {
        "id": "money_portability_twice_stmt",
        "created_at": "2026-01-01T00:00:00Z",
        "filename": "x.pdf",
        "mime": "application/pdf",
        "size_bytes": 100,
        "file_hash": "hash_twice",
        "file_path": "statements/x.pdf",
        "kind": "pdf_pages",
        "parsed_rows": [{"amount": 1.0, "description": "twice"}],
        "parsed_at": "2026-01-01T00:00:01Z",
        "account_id": None,
        "batch_ids": [],
    }
    bundle = _build_bundle({"statements.json": json.dumps([stmt]).encode()})

    result1 = svc.apply_import(bundle)
    assert result1["row_counts"]["statements.json"] == 1

    path = money_io.statements_files_dir() / "money_portability_twice_stmt.parsed.json"
    assert path.exists()
    assert json.loads(path.read_text()) == [{"amount": 1.0, "description": "twice"}]

    # Import the EXACT SAME bundle bytes a second time.
    result2 = svc.apply_import(bundle)
    assert result2["row_counts"]["statements.json"] == 1

    assert path.exists()
    assert json.loads(path.read_text()) == [{"amount": 1.0, "description": "twice"}]

    from backend.db.statement_store import StatementStore

    row = StatementStore().find_by_id("money_portability_twice_stmt")
    assert row["parsed_rows"] == [{"amount": 1.0, "description": "twice"}]

    # No stray staged temp file survives either run.
    leftovers = list(money_io.statements_files_dir().glob("*.import-tmp-*"))
    assert leftovers == []

    path.unlink()


def test_write_side_rollback_statement_sidecar_survives_a_later_step_failing(db_isolated):
    """Final review fix regression (Important, empirically reproduced): statement sidecar
    WRITES must also stage past commit — the WRITE-side mirror of fix rounds 1/2's DELETE-
    side deferral. Neither existing test covers this shape: the fix-round-1 rollback test's
    corrupt entry is in transactions.json (BEFORE statements.json in _MONEY_IMPORT_ORDER),
    so import_statements' write path never even runs before that failure; the fix-round-2
    test never fails at all (a pure success case).

    Here: seed a live statement stmt_x with sidecar content A. Import a bundle reusing
    stmt_x with NEW content B, PLUS a merge_journal row missing absorbed_id — merge_journal
    is LAST in _MONEY_IMPORT_ORDER, so it fails (a NOT NULL primary-key violation at INSERT
    — passes inspect_bundle's shape check, since merge_journal rows aren't shape-validated
    there) strictly AFTER import_statements has already run for stmt_x. Pre-fix,
    import_statements wrote B straight to the LIVE sidecar path; the merge_journal failure
    then rolled the DB row back to A while the file stayed at B — silently serving the
    wrong financial data under stmt_x's restored identity, masked by fix round 1's own
    tolerant reinline (a WARNING only fires for a MISSING/corrupt file, never for one that
    exists and parses — which a wrongly-overwritten file always does).
    """
    stmt_x_a = {
        "id": "money_portability_writeside_x",
        "created_at": "2026-01-01T00:00:00Z",
        "filename": "x.pdf",
        "mime": "application/pdf",
        "size_bytes": 100,
        "file_hash": "hashA",
        "file_path": "statements/x.pdf",
        "kind": "pdf_pages",
        "parsed_rows": [{"amount": 1.0, "description": "A"}],
        "parsed_at": "2026-01-01T00:00:01Z",
        "account_id": None,
        "batch_ids": [],
    }
    with get_engine().begin() as conn:
        money_io.import_statements(conn, [stmt_x_a])

    path_x = money_io.statements_files_dir() / "money_portability_writeside_x.parsed.json"
    assert path_x.exists()
    assert json.loads(path_x.read_text()) == [{"amount": 1.0, "description": "A"}]

    stmt_x_b = {**stmt_x_a, "parsed_rows": [{"amount": 99.0, "description": "B"}]}
    # absorbed_id (the merge_journal PRIMARY KEY) is missing -> NOT NULL violation at INSERT.
    poisoned_merge_journal = [{"canonical_id": "some_txn", "created_at": "2026-01-01T00:00:00Z"}]

    bundle = _build_bundle(
        {
            "statements.json": json.dumps([stmt_x_b]).encode(),
            "merge_journal.json": json.dumps(poisoned_merge_journal).encode(),
        }
    )

    with pytest.raises(IntegrityError):
        svc.apply_import(bundle)

    # (1) it raises — asserted above via pytest.raises.

    # (2) DB row stmt_x fully restored — parsed_at + the WHOLE row equal the pre-import A.
    with get_engine().connect() as conn:
        restored = money_io.export_statements(conn)
    matched = next(s for s in restored if s["id"] == "money_portability_writeside_x")
    assert matched["parsed_at"] == "2026-01-01T00:00:01Z"
    assert matched == stmt_x_a

    # (3) the sidecar file STILL holds content A on disk — never touched by the failed
    # import's B, despite import_statements having run (and, pre-fix, written B) before
    # the later merge_journal failure.
    assert path_x.exists()
    assert json.loads(path_x.read_text()) == [{"amount": 1.0, "description": "A"}], (
        "FINAL REVIEW regression: the sidecar file was overwritten with the failed "
        "import's content despite the owning DB row correctly rolling back to A"
    )

    # (4) StatementStore().all() serves A — never B, never a WARNING-degraded None.
    from backend.db.statement_store import StatementStore

    served = StatementStore().find_by_id("money_portability_writeside_x")
    assert served is not None
    assert served["parsed_rows"] == [{"amount": 1.0, "description": "A"}]

    path_x.unlink()


# ── Undoable money imports (plan 3 task 6's binding requirement) ─────────────────────


def _money_import_client(tmp_path, monkeypatch):
    """TestClient wired the same way tests/test_portability_api.py's own `client`
    fixture is, but ALSO narrowed to just profile + the money six (real Path identities,
    so .name resolves through portable_bridge.BRIDGED/MONEY_FILES correctly)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.ai import checkpoint as ckpt
    from backend.api import portability as api
    from backend.paths import (
        ACCOUNTS_JSON,
        IMPORT_BATCHES_JSON,
        MERGE_JOURNAL_JSON,
        STATEMENTS_JSON,
        TRANSACTIONS_JSON,
        TXN_AUDIT_JSON,
    )

    data = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    blobs.mkdir(parents=True)
    index = tmp_path / "checkpoints" / "index.json"

    profile = data / "profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({"name": "Alex", "base_currency": "SGD"}))

    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [
            profile,
            ACCOUNTS_JSON,
            TRANSACTIONS_JSON,
            TXN_AUDIT_JSON,
            IMPORT_BATCHES_JSON,
            STATEMENTS_JSON,
            MERGE_JOURNAL_JSON,
        ],
    )
    # No PORTABLE_DIRS/SESSIONS_DIR narrowing needed: sessions are DB rows now
    # (Wave 5E, JSON->DB — see core.portable.PORTABLE_DIRS's own docstring), and this
    # test only exercises the money-six bundle import, never session portability.
    monkeypatch.setattr(portable, "PORTABLE_DIRS", [])
    monkeypatch.setattr(svc, "_ALLOWED_FILENAMES", {p.name for p in portable.PORTABLE_FILES})

    monkeypatch.setattr(ckpt, "DATA_DIR", data)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    client.headers.update({"X-Requested-With": "XMLHttpRequest"})
    return client, ckpt


def test_money_bundle_import_via_api_is_undoable(db_isolated, tmp_path, monkeypatch):
    """import a money bundle inside the API path -> preview() shows db:transactions
    rows -> restore() reverts the import (rows back to pre-import state)."""
    from backend.core import io as core_io

    prev_hook = core_io._write_hook
    prev_db_hook = core_io._db_write_hook
    try:
        client, ckpt = _money_import_client(tmp_path, monkeypatch)
        ckpt.register_write_hook(ckpt._snapshot_active)
        ckpt.register_db_write_hook(ckpt._snapshot_db_active)

        with get_engine().begin() as conn:
            money_io.import_accounts(conn, ACCOUNTS)
            money_io.import_transactions(conn, TRANSACTIONS)

        bundle = _build_bundle(
            {
                "accounts.json": json.dumps(ACCOUNTS_V2).encode(),
                "transactions.json": json.dumps(TRANSACTIONS_V2).encode(),
            }
        )

        r = client.post(
            "/api/data/import",
            files={"file": ("b.zip", bundle, "application/zip")},
            data={"replace": "true"},
        )
        assert r.status_code == 200
        cp_id = r.json()["undo_checkpoint_id"]
        assert cp_id, "a money import must mint an undo checkpoint"

        # New data landed.
        with get_engine().connect() as conn:
            assert money_io.export_accounts(conn) == ACCOUNTS_V2
            assert money_io.export_transactions(conn) == TRANSACTIONS_V2

        # preview() shows db:transactions / db:accounts rows for this checkpoint.
        pv = ckpt.preview(cp_id)
        db_files = {c["file"] for c in pv["changes"]}
        assert "db:transactions" in db_files
        assert "db:accounts" in db_files

        # restore() reverts the import — rows back to pre-import state.
        ckpt.restore(cp_id, mode="data")
        with get_engine().connect() as conn:
            assert money_io.export_accounts(conn) == ACCOUNTS
            assert money_io.export_transactions(conn) == TRANSACTIONS
    finally:
        core_io.register_write_hook(prev_hook)
        core_io.register_db_write_hook(prev_db_hook)


def test_money_bundle_import_never_undoes_the_audit_trail(db_isolated, tmp_path, monkeypatch):
    """txn_audit fires the hook (money_io.import_txn_audit, unconditionally, like its five
    siblings) but ai.checkpoint._EXCLUDED_TABLES already makes that a structural no-op
    (task 4's ruling) — a money-import undo must not resurrect/erase audit rows either
    way. Proves the exclusion holds THROUGH the new bridged import path, not just through
    the live ledger chokes task 4 tested it against."""
    from backend.core import io as core_io

    prev_hook = core_io._write_hook
    prev_db_hook = core_io._db_write_hook
    try:
        client, ckpt = _money_import_client(tmp_path, monkeypatch)
        ckpt.register_write_hook(ckpt._snapshot_active)
        ckpt.register_db_write_hook(ckpt._snapshot_db_active)

        with get_engine().begin() as conn:
            money_io.import_accounts(conn, ACCOUNTS)
            money_io.import_transactions(conn, TRANSACTIONS)
            money_io.import_txn_audit(conn, TXN_AUDIT)

        bundle = _build_bundle(
            {
                "accounts.json": json.dumps(ACCOUNTS_V2).encode(),
                "transactions.json": json.dumps(TRANSACTIONS_V2).encode(),
                # txn_audit.json absent -> wiped to empty by the import.
            }
        )
        r = client.post(
            "/api/data/import",
            files={"file": ("b.zip", bundle, "application/zip")},
            data={"replace": "true"},
        )
        cp_id = r.json()["undo_checkpoint_id"]

        with get_engine().connect() as conn:
            assert money_io.export_txn_audit(conn) == []  # wiped by the import itself

        entry = ckpt.find_checkpoint(cp_id)
        assert "txn_audit" not in (entry.get("db_tables") or []), (
            "txn_audit must never appear in a checkpoint's db_tables — the exclusion is "
            "structural (ai.checkpoint._EXCLUDED_TABLES), not merely unexercised here"
        )

        ckpt.restore(cp_id, mode="data")
        with get_engine().connect() as conn:
            # Restore reverts accounts/transactions but — correctly — does NOT restore
            # the wiped txn_audit rows either (never captured in the first place). It only
            # APPENDS its own "revert" event per transaction it put back.
            assert money_io.export_accounts(conn) == ACCOUNTS
            after = money_io.export_txn_audit(conn)
            assert not {e["id"] for e in after} & {e["id"] for e in TXN_AUDIT}
            assert {e["op"] for e in after} <= {"revert"}
    finally:
        core_io.register_write_hook(prev_hook)
        core_io.register_db_write_hook(prev_db_hook)


# ── /api/reset — money tables wiped in the same transaction, backup captures them ────


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors test_portability_db_bridge.py's own helper,
    narrowed to profile + the money six."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import paths as bpaths
    from backend.api import setup as setup_api
    from backend.paths import (
        ACCOUNTS_JSON,
        IMPORT_BATCHES_JSON,
        MERGE_JOURNAL_JSON,
        STATEMENTS_JSON,
        TRANSACTIONS_JSON,
        TXN_AUDIT_JSON,
    )

    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    profile = data / "profile.json"
    watchlist = data / "watchlist.json"
    profile.write_text(json.dumps({"name": "Alex"}))

    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [
            profile,
            ACCOUNTS_JSON,
            TRANSACTIONS_JSON,
            TXN_AUDIT_JSON,
            IMPORT_BATCHES_JSON,
            STATEMENTS_JSON,
            MERGE_JOURNAL_JSON,
        ],
    )
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)
    monkeypatch.setattr(bpaths, "WATCHLIST_JSON", watchlist)

    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app), data


def test_reset_wipes_money_tables_backup_contains_pre_reset_rows_identity_intact(
    db_isolated, tmp_path, monkeypatch
):
    """Regression test (plan 3 task 6 binding requirement): post-migration seeded money
    rows -> reset -> backup dir contains transactions.json etc. with the pre-reset rows,
    tables empty after, identity intact. Also proves the money six wipe in the SAME one
    transaction as the pre-existing bridged wipe (evidence/price_history/instruments) —
    reset_bridged_stores() is called exactly once, unchanged."""
    with get_engine().begin() as conn:
        _seed_all(conn)

    parsed_path = money_io.statements_files_dir() / "money_portability_stmt_rows.parsed.json"
    assert parsed_path.exists(), "sanity: the parsed-rows file landed on disk before reset"

    with get_engine().connect() as conn:
        users_before = conn.execute(select(func.count()).select_from(t.users)).scalar_one()
        books_before = conn.execute(select(func.count()).select_from(t.books)).scalar_one()
        members_before = conn.execute(select(func.count()).select_from(t.book_members)).scalar_one()
        cutovers_before = conn.execute(
            select(func.count()).select_from(t.json_cutovers)
        ).scalar_one()
        write_log_before = conn.execute(select(func.count()).select_from(t.write_log)).scalar_one()

    client, data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]
    assert json.loads((backup_dir / "accounts.json").read_text()) == ACCOUNTS
    assert json.loads((backup_dir / "transactions.json").read_text()) == TRANSACTIONS
    assert json.loads((backup_dir / "txn_audit.json").read_text()) == TXN_AUDIT
    assert json.loads((backup_dir / "import_batches.json").read_text()) == IMPORT_BATCHES
    assert json.loads((backup_dir / "statements.json").read_text()) == STATEMENTS
    assert json.loads((backup_dir / "merge_journal.json").read_text()) == MERGE_JOURNAL

    with get_engine().connect() as conn:
        assert money_io.export_accounts(conn) == []
        assert money_io.export_transactions(conn) == []
        assert money_io.export_txn_audit(conn) == [], (
            "txn_audit WIPES on reset — reset means reset, it is not portable-exempt "
            "(distinct from its SEPARATE checkpoint-undo exclusion)"
        )
        assert money_io.export_import_batches(conn) == []
        assert money_io.export_statements(conn) == []
        assert money_io.export_merge_journal(conn) == []
        assert (
            conn.execute(select(func.count()).select_from(t.transaction_splits)).scalar_one() == 0
        )
        assert (
            conn.execute(select(func.count()).select_from(t.transaction_external_refs)).scalar_one()
            == 0
        )
        # write_log/json_cutovers/identity are migration/checkpoint machinery, never
        # wiped by reset — unchanged by the money six joining the bridge.
        assert (
            conn.execute(select(func.count()).select_from(t.json_cutovers)).scalar_one()
            == cutovers_before
        )
        assert (
            conn.execute(select(func.count()).select_from(t.write_log)).scalar_one()
            == write_log_before
        )
        assert conn.execute(select(func.count()).select_from(t.users)).scalar_one() == users_before
        assert conn.execute(select(func.count()).select_from(t.books)).scalar_one() == books_before
        assert (
            conn.execute(select(func.count()).select_from(t.book_members)).scalar_one()
            == members_before
        )

    # Statements parsed files: reset removes them with the rows.
    assert not parsed_path.exists()


def test_reset_endpoint_wipes_money_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    """API-level proof — a corrupt/inconsistent DB state must never survive a reset."""
    with get_engine().begin() as conn:
        money_io.import_accounts(conn, ACCOUNTS)
        money_io.import_transactions(conn, TRANSACTIONS)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert money_io.export_accounts(conn) == []
        assert money_io.export_transactions(conn) == []
