"""Security-gate fix wave (plan 3 task 9) — at-rest posture + error hygiene on the engine.

FR-I3: the sqlite database file (and its -wal/-shm sidecars) are chmod'd 0o600 on connect —
financial data must not sit world-readable at 0644, matching auth.json's posture.
FR-M5: create_engine(hide_parameters=True) keeps bound SQL VALUES out of every DBAPIError
str() — that string reaches both the LLM context (ai/assistant.py) and the boot log
(migrate.py); a leaked "[parameters: (...)]" tail would carry row data into both.

Isolated tmp database throughout — the live tree is never touched.
"""

import os
import stat

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_sqlite_db_file_is_owner_only_after_connect(tmp_path, monkeypatch):
    """A fresh engine, once connected, leaves its sqlite file at mode 0o600 (FR-I3). The
    connect event fires the chmod; -wal/-shm are checked too when a write created them."""
    from backend.db import engine as eng

    db_path = tmp_path / "posture.db"
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{db_path}")
    eng.dispose_engine()
    try:
        engine = eng.get_engine()
        with engine.begin() as conn:
            # A write forces WAL creation, so -wal/-shm exist to be checked as well.
            conn.execute(text("CREATE TABLE _posture_probe (x INTEGER)"))
            conn.execute(text("INSERT INTO _posture_probe VALUES (1)"))
        assert db_path.exists()
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = tmp_path / f"posture.db{suffix}"
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600, suffix
    finally:
        eng.dispose_engine()


def test_integrity_error_does_not_leak_sql_parameters(db_isolated):
    """A duplicate-PK insert raises IntegrityError whose str() no longer embeds the bound row
    VALUES (FR-M5). Portable across dialects (duplicate PK on any backend)."""
    from backend.db import money_io
    from backend.db.engine import unit_of_work
    from tests.conftest import seed_stub_accounts

    row = {
        "id": "dup_row",
        "kind": "expense",
        "date": "2026-01-01",
        "from_account_id": "acc",
        "from_amount": "1.0",
    }
    seed_stub_accounts([row])
    with unit_of_work() as conn:
        money_io.import_transactions(conn, [row])

    with pytest.raises(IntegrityError) as excinfo:
        with unit_of_work() as conn:
            money_io.import_transactions(conn, [row])

    message = str(excinfo.value)
    assert "[parameters:" not in message, message
    assert "hide_parameters" in message  # SQLAlchemy's replacement marker
