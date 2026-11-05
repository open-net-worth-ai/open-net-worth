"""SQL a server sends runs where it can reach the copy and nothing else: a chosen server is trusted
with a budget, not with the book.

The migrations run on real SQLite. The server is a stub: first a small ``Base``, then the real
``actual.Actual`` over an in-process ``httpx`` transport. Every name and number is made up.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx
import pytest

from backend.core.errors import ExternalServiceError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services.actual_sync import client, compat, sql_guard
from tests.test_actual_client_io import FILES, PASSWORD, URL, make_db, never_leaks

actual = pytest.importorskip("actual")
create_engine = pytest.importorskip("sqlalchemy").create_engine


@pytest.fixture
def outside(tmp_path) -> Path:
    """A database standing in for the book, in another folder from the copy."""
    path = tmp_path / "elsewhere" / "book.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ledger (amount)")
    conn.execute("INSERT INTO ledger VALUES (100)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def copy(tmp_path) -> Path:
    directory = tmp_path / "run"
    directory.mkdir()
    make_db(directory / "db.sqlite")
    return directory


def stub(directory: Path, served: dict[str, str | bytes]):
    """``checked_class`` over a server that answers ``served``, noting what it was asked for."""

    class Base:
        data_dir = directory
        engine = create_engine(f"sqlite:///{directory}/db.sqlite")
        asked: list[str] = []

        def data_file(self, file):
            self.asked.append(file)
            body = served[file]
            return body.encode() if isinstance(body, str) else body

    return client.checked_class(Base)()


def recorded(directory: Path) -> list[int]:
    conn = sqlite3.connect(directory / "db.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT id FROM __migrations__ ORDER BY id")]
    finally:
        conn.close()


def book_holds(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT amount FROM ledger").fetchall()
    finally:
        conn.close()


def refused(session, file: str, *secrets: str) -> ExternalServiceError:
    with pytest.raises(ExternalServiceError) as caught:
        session.run_migrations([file])
    error = caught.value
    assert error.__context__ is None and error.__cause__ is None
    assert "wasn't read" in error.message or "doesn't recognise" in error.message
    for secret in ("ATTACH", "VACUUM", "PRAGMA", *secrets):
        assert secret.lower() not in str(error).lower()
    return error


# ── what a migration can no longer reach ─────────────────────────────────────


def test_attaching_another_database_is_refused_and_it_stays_as_it_was(copy, outside):
    file = "migrations/9000000000001_a.sql"
    body = f"ATTACH '{outside}' AS o; DELETE FROM o.ledger;"
    before = outside.read_bytes()
    refused(stub(copy, {file: body}), file, str(outside))
    assert outside.read_bytes() == before
    assert book_holds(outside) == [(100,)]
    assert 9000000000001 not in recorded(copy)


def test_attaching_a_new_file_never_creates_it(copy, tmp_path):
    made = tmp_path / "elsewhere-new.db"
    file = "migrations/9000000000002_a.sql"
    body = f"ATTACH '{made}' AS o; CREATE TABLE o.t (a);"
    refused(stub(copy, {file: body}), file, str(made))
    assert not made.exists()


def test_vacuum_into_another_file_never_creates_it(copy, tmp_path):
    made = tmp_path / "vacuumed.db"
    file = "migrations/9000000000003_a.sql"
    refused(stub(copy, {file: f"VACUUM INTO '{made}';"}), file, str(made))
    assert not made.exists()


def test_vacuum_into_after_closing_a_transaction_is_refused_too(copy, tmp_path):
    made = tmp_path / "vacuumed.db"
    file = "migrations/9000000000004_a.sql"
    body = f"BEGIN TRANSACTION; COMMIT; VACUUM INTO '{made}';"
    refused(stub(copy, {file: body}), file, str(made))
    assert not made.exists()


@pytest.mark.parametrize(
    "statement",
    ["PRAGMA writable_schema=ON;", "PRAGMA trusted_schema=ON;", "DETACH DATABASE temp;"],
)
def test_pragmas_and_detach_are_refused(copy, statement):
    file = "migrations/9000000000005_a.sql"
    refused(stub(copy, {file: statement}), file)
    assert 9000000000005 not in recorded(copy)


def test_a_javascript_migration_runs_on_the_same_connection(copy, outside):
    file = "migrations/9000000000006_prefs.js"
    body = f"db.execQuery(`ATTACH '{outside}' AS o; DELETE FROM o.ledger;`);"
    refused(stub(copy, {file: body}), file, str(outside))
    assert book_holds(outside) == [(100,)]


def test_a_later_statement_in_the_script_is_refused_too(copy, outside):
    file = "migrations/9000000000007_a.sql"
    body = f"CREATE TABLE fine (a); ATTACH '{outside}' AS o; DELETE FROM o.ledger;"
    refused(stub(copy, {file: body}), file, str(outside))
    assert book_holds(outside) == [(100,)]


def test_a_body_that_isnt_text_is_refused_plainly(copy):
    file = "migrations/9000000000008_a.sql"
    refused(stub(copy, {file: b"\xff\xfe\x00"}), file)


def test_a_broken_migration_is_refused_plainly(copy):
    file = "migrations/9000000000009_a.sql"
    refused(stub(copy, {file: "CREATE TABLE ledger_nope ("}), file)


@pytest.mark.parametrize(
    "file",
    [
        "migrations/deeper/9000000000010_a.sql",
        "migrations-x/9000000000010_a.sql",
        "migrations/1 OR 1=1_a.sql",
    ],
)
def test_a_file_name_of_any_other_shape_is_refused_before_it_is_fetched(copy, file):
    session = stub(copy, {file: "CREATE TABLE fine (a);"})
    refused(session, file)
    assert session.asked == []


# ── what still works ─────────────────────────────────────────────────────────

ORDINARY = """
BEGIN TRANSACTION;
CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT, tombstone INTEGER DEFAULT 0);
ALTER TABLE accounts ADD COLUMN bank TEXT;
CREATE INDEX tags_name ON tags(name);
CREATE TEMPORARY TABLE scratch (id TEXT);
INSERT INTO scratch SELECT id FROM accounts;
UPDATE accounts SET bank = 'ACME' WHERE id IN (SELECT id FROM scratch);
DROP TABLE scratch;
CREATE VIEW v_accounts AS SELECT id, COALESCE(bank, name) AS shown FROM accounts;
ANALYZE;
COMMIT;
"""


def test_an_ordinary_migration_applies_and_is_recorded(copy):
    file = "migrations/9000000000020_add_tags.sql"
    session = stub(copy, {file: ORDINARY})
    session.run_migrations(["default-db.sqlite-skipped", file])
    assert session.asked == [file]
    assert recorded(copy) == [9000000000020]
    conn = sqlite3.connect(copy / "db.sqlite")
    try:
        assert conn.execute("SELECT bank FROM accounts").fetchall() == [("ACME",)]
        assert conn.execute("SELECT shown FROM v_accounts").fetchall() == [("ACME",)]
    finally:
        conn.close()
    assert "tags" in session._database_metadata.tables, "the library's reflection still runs"


def test_a_migration_already_recorded_is_neither_fetched_nor_run(copy):
    file = "migrations/9000000000021_add_tags.sql"
    conn = sqlite3.connect(copy / "db.sqlite")
    conn.execute("INSERT INTO __migrations__ (id) VALUES (9000000000021)")
    conn.commit()
    conn.close()
    session = stub(copy, {})
    session.run_migrations([file])
    assert session.asked == []


def test_each_migration_runs_once_and_in_turn(copy):
    first = "migrations/9000000000022_one.sql"
    second = "migrations/9000000000023_two.sql"
    served = {first: "CREATE TABLE one (a);", second: "INSERT INTO one VALUES (1);"}
    session = stub(copy, served)
    session.run_migrations([first, second])
    session.run_migrations([first, second])
    assert session.asked == [first, second]
    assert recorded(copy) == [9000000000022, 9000000000023]


def test_the_number_is_read_from_the_name():
    assert compat.migration_number("migrations/1723665565000_prefs.js") == 1723665565000


def test_the_refusal_is_the_fixed_sentence(copy, outside):
    file = "migrations/9000000000030_a.sql"
    body = f"ATTACH '{outside}' AS o;"
    error = refused(stub(copy, {file: body}), file, str(outside))
    assert error.message == sql_guard._REFUSED


# ── the whole way in, on the real class ──────────────────────────────────────


@pytest.fixture
def store(db_isolated):
    s = ActualBudgetConfigStore()
    s.update({"enabled": True, "server_url": URL, "password": PASSWORD})
    return s


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "actual_sync"
    monkeypatch.setattr(client, "work_root", lambda: root)
    return root


def budget_zip(tmp_path: Path, extra: dict[str, bytes] | None = None) -> bytes:
    source = tmp_path / "source.sqlite"
    make_db(source)
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE messages_clock (id INTEGER PRIMARY KEY, clock TEXT)")
    conn.commit()
    conn.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("db.sqlite", source.read_bytes())
        z.writestr("metadata.json", json.dumps({"budgetType": "rollover"}))
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return buf.getvalue()


def serve(monkeypatch, budget: bytes, migrations: dict[str, str]):
    """An Actual server, as far as opening a budget goes."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/account/login"):
            return httpx.Response(200, json={"status": "ok", "data": {"token": "tok"}})
        if path.endswith("/account/validate"):
            return httpx.Response(200, json={"status": "ok", "data": {"validated": True}})
        if path.endswith("/sync/list-user-files"):
            return httpx.Response(200, json=FILES)
        if path.endswith("/sync/download-user-file"):
            return httpx.Response(200, content=budget)
        if path.endswith("/data-file-index.txt"):
            return httpx.Response(200, text="\n".join(["default-db.sqlite", *migrations]))
        if path.endswith("/sync/sync"):
            return httpx.Response(200, content=b"")
        for file, body in migrations.items():
            if path.endswith(f"/data/{file}"):
                asked.append(file)
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return asked


def test_the_real_class_opens_a_budget_through_the_guard(store, work, tmp_path, monkeypatch):
    file = "migrations/9000000000040_add_tags.sql"
    asked = serve(monkeypatch, budget_zip(tmp_path), {file: "CREATE TABLE tags (id TEXT);"})
    with client.open_copy(store, "f1") as opened:
        assert opened.name == "ACME Home" and opened.currency == "USD"
        assert recorded(opened.directory) == [9000000000040]
    assert asked == [file]
    assert list(work.iterdir()) == []


def test_the_real_class_refuses_an_attach_and_leaves_nothing(
    store, work, tmp_path, monkeypatch, outside
):
    file = "migrations/9000000000041_a.sql"
    body = f"ATTACH '{outside}' AS o; DELETE FROM o.ledger;"
    serve(monkeypatch, budget_zip(tmp_path), {file: body})
    with pytest.raises(ExternalServiceError) as caught:
        with client.open_copy(store, "f1"):
            pytest.fail("the body must not run")
    never_leaks(caught.value)
    assert str(outside) not in str(caught.value) and "ATTACH" not in str(caught.value)
    assert book_holds(outside) == [(100,)]
    assert list(work.iterdir()) == []


def test_the_real_class_refuses_vacuum_into_and_leaves_nothing(store, work, tmp_path, monkeypatch):
    made = tmp_path / "vacuumed.db"
    file = "migrations/9000000000042_a.sql"
    serve(monkeypatch, budget_zip(tmp_path), {file: f"VACUUM INTO '{made}';"})
    with pytest.raises(ExternalServiceError) as caught:
        with client.open_copy(store, "f1"):
            pytest.fail("the body must not run")
    never_leaks(caught.value)
    assert not made.exists()
    assert list(work.iterdir()) == []


# ── what a downloaded file may hold ──────────────────────────────────────────


@pytest.mark.parametrize("member", ["db.sqlite-journal", "db.sqlite-wal", "notes/readme.txt"])
def test_a_file_holding_more_than_a_budget_is_refused_before_it_is_unpacked(
    store, work, tmp_path, monkeypatch, member
):
    serve(monkeypatch, budget_zip(tmp_path, {member: b"x"}), {})
    with pytest.raises(ExternalServiceError) as caught:
        with client.open_copy(store, "f1"):
            pytest.fail("the body must not run")
    never_leaks(caught.value)
    assert "more than a budget" in caught.value.message
    assert list(work.iterdir()) == []
