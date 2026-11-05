"""The Actual Budget client seam: a fresh copy per run that is always removed, plain errors that
never carry the address or a password, the size cap, and the gateway / flag refusals.

The ``actual`` package's ``Actual`` class is faked (the network is not touched); the cap tests run
the real class over an in-process ``httpx`` transport. One test is skipped unless
``ACTUAL_TEST_URL`` points at a throwaway server. Every name and number is made up.
"""

from __future__ import annotations

import ast
import io
import logging
import os
import sqlite3
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.core.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services.actual_sync import client, writes
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync.client import Budget
from tests.flags import flags
from tests.test_actual_compat import SCHEMA

actual = pytest.importorskip("actual")
from actual import exceptions as actual_exc  # noqa: E402

HOST = "acme-actual.example.test"
URL = f"http://{HOST}:5006"
PASSWORD = "PASSWORD-SENTINEL-9"
TOKEN = "TOKEN-SENTINEL-9"
ENC = "ENCRYPTION-SENTINEL-9"


@pytest.fixture
def store(db_isolated):
    s = ActualBudgetConfigStore()
    s.update({"server_url": URL, "password": PASSWORD})
    return s


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "actual_sync"
    monkeypatch.setattr(client, "work_root", lambda: root)
    return root


@pytest.fixture
def behind_gateway(monkeypatch):
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)


def make_db(path: Path, currency: str | None = "USD") -> None:
    conn = sqlite3.connect(path)
    for table, columns in SCHEMA.items():
        conn.execute(f"CREATE TABLE {table} ({', '.join(columns.split())})")
    conn.execute("CREATE TABLE __migrations__ (id INT PRIMARY KEY NOT NULL)")
    conn.execute("INSERT INTO accounts VALUES ('a1', 'ACME Checking', 0, 0, 0, 1)")
    conn.execute("INSERT INTO category_groups VALUES ('g1', 'Usual', 0, 1, 0, 0)")
    conn.execute("INSERT INTO categories VALUES ('c1', 'Food', 0, 'g1', 1, 0, 0)")
    conn.execute("INSERT INTO category_mapping VALUES ('c1', 'c1')")
    conn.execute("INSERT INTO payees VALUES ('p1', 'Corner Market', NULL, 0, NULL)")
    conn.execute("INSERT INTO payee_mapping VALUES ('p1', 'p1')")
    conn.execute(
        "INSERT INTO transactions (id, isParent, isChild, acct, category, amount, description, "
        "date, starting_balance_flag, tombstone, cleared, reconciled) "
        "VALUES ('t1', 0, 0, 'a1', 'c1', -2599, 'p1', 20261013, 0, 0, 1, 0)"
    )
    conn.execute("INSERT INTO zero_budgets VALUES ('202610-c1', 202610, 'c1', 50000, 0)")
    if currency:
        conn.execute("INSERT INTO preferences VALUES ('defaultCurrencyCode', ?)", (currency,))
    conn.commit()
    conn.close()


def dto(file_id="f1", name="ACME Home", key=None, deleted=0):
    return SimpleNamespace(
        file_id=file_id, name=name, encrypt_key_id=key, deleted=deleted, group_id="g-" + file_id
    )


class FakeActual:
    """Stands in for ``actual.Actual``: records how it was built and writes a small budget."""

    session = SimpleNamespace(new=(), dirty=(), info={})

    instances: list["FakeActual"] = []
    files = [dto()]
    build_error: Exception | None = None
    download_error: Exception | None = None
    version = "26.11.0"

    def __init__(self, **kwargs):
        if FakeActual.build_error:
            raise FakeActual.build_error
        self.kwargs = kwargs
        self.data_dir = Path(kwargs["data_dir"]) if kwargs.get("data_dir") else None
        self.closed = False
        self.file = None
        self.engine_open = False
        FakeActual.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def info(self):
        return SimpleNamespace(build=SimpleNamespace(version=FakeActual.version))

    def list_user_files(self):
        return SimpleNamespace(data=list(FakeActual.files))

    def set_file(self, file):
        self.file = file

    def download_budget(self, encryption_password=None):
        self.encryption_password = encryption_password
        if FakeActual.download_error:
            raise FakeActual.download_error
        (self.data_dir / "metadata.json").write_text('{"budgetType":"rollover"}')
        make_db(self.data_dir / "db.sqlite")


@pytest.fixture
def fake(monkeypatch):
    FakeActual.instances = []
    FakeActual.files = [dto()]
    FakeActual.build_error = None
    FakeActual.download_error = None
    monkeypatch.setattr(actual, "Actual", FakeActual)
    return FakeActual


def never_leaks(error: BaseException) -> None:
    text = f"{error} {error!r} {getattr(error, 'message', '')}"
    for secret in (HOST, PASSWORD, TOKEN, ENC, "5006", "http://"):
        assert secret not in text
    assert error.__cause__ is None
    assert error.__context__ is None


# ── the copy ─────────────────────────────────────────────────────────────────


def test_open_copy_reads_the_plan_and_removes_the_directory(store, work, fake):
    with client.open_copy(store, "f1") as copy:
        assert copy.name == "ACME Home" and copy.currency == "USD"
        assert copy.server_version == "26.11.0" and copy.encrypted is False
        assert copy.mode == "envelope"
        directory = copy.directory
        assert directory.parent == work and directory.is_dir()
        plan = copy.plan()
        assert [a["name"] for a in plan["accounts"]] == ["ACME Checking"]
        assert plan["transactions"][0]["amount"] == -25990
        assert plan["months"][0]["categories"][0]["budgeted"] == 500000
    assert not directory.exists()
    assert fake.instances[0].closed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_the_directory_is_owner_only(store, work, fake):
    with client.open_copy(store, "f1") as copy:
        assert stat.S_IMODE(copy.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(work.stat().st_mode) == 0o700


def test_temp_dir_removed_on_success_and_failure(store, work, fake):
    with client.open_copy(store, "f1") as copy:
        ok_dir = copy.directory
    assert not ok_dir.exists()

    seen = []
    with pytest.raises(RuntimeError, match="our own bug"):
        with client.open_copy(store, "f1") as copy:
            seen.append(copy.directory)
            raise RuntimeError("our own bug")
    assert not seen[0].exists()

    fake.download_error = actual_exc.ActualError("boom")
    with pytest.raises(ExternalServiceError):
        with client.open_copy(store, "f1"):
            pytest.fail("the body must not run")
    assert list(work.iterdir()) == []


def test_a_refused_snapshot_leaves_nothing_behind(store, work, fake):
    fake.download_error = ExternalServiceError("This budget needs refreshing in Actual first.")
    with pytest.raises(ExternalServiceError, match="refreshing"):
        with client.open_copy(store, "f1"):
            pass
    assert list(work.iterdir()) == []


def test_the_encrypted_budget_gets_the_encryption_password(store, work, fake):
    store.update({"encryption_password": ENC})
    fake.files = [dto(key="k1")]
    with client.open_copy(store, "f1") as copy:
        assert copy.encrypted is True
    assert fake.instances[0].encryption_password == ENC
    assert fake.instances[0].kwargs["encryption_password"] == ENC


@pytest.mark.parametrize(
    "address,warned",
    [
        ("http://acme-actual.example.test:5006", True),
        ("http://192.168.1.20:5006", True),
        ("http://localhost:5006", False),
        ("http://127.0.0.1:5006", False),
        ("http://[::1]:5006", False),
        ("https://acme-actual.example.test", False),
    ],
)
def test_plain_http_to_another_machine_warns_but_is_allowed(
    db_isolated, work, fake, address, warned
):
    s = ActualBudgetConfigStore()
    s.update({"server_url": address, "password": PASSWORD})
    with client.open_copy(s, "f1") as copy:
        assert bool(copy.notices) is warned
        for notice in copy.notices:
            never_leaks(ExternalServiceError(notice))


def test_the_password_signs_in_when_both_are_saved(store, work, fake):
    store.update({"password": PASSWORD, "token": TOKEN})
    with client.open_copy(store, "f1"):
        pass
    kwargs = fake.instances[0].kwargs
    assert kwargs["password"] == PASSWORD and kwargs["token"] is None


def test_a_token_alone_signs_in(db_isolated, work, fake):
    s = ActualBudgetConfigStore()
    s.update({"server_url": URL, "token": TOKEN})
    with client.open_copy(s, "f1"):
        pass
    kwargs = fake.instances[0].kwargs
    assert kwargs["token"] == TOKEN and kwargs["password"] is None


def test_the_budget_is_found_by_its_cloud_file_id_not_its_name(store, work, fake):
    fake.files = [dto("f1", "ACME Home"), dto("f2", "ACME Home")]
    with client.open_copy(store, "f2"):
        pass
    assert fake.instances[0].file.file_id == "f2"


def test_https_context_passed_as_cert(db_isolated, work, fake, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(client, "https_context", lambda: sentinel)
    s = ActualBudgetConfigStore()
    s.update({"server_url": f"https://{HOST}", "password": PASSWORD})
    with client.open_copy(s, "f1"):
        pass
    assert fake.instances[0].kwargs["cert"] is sentinel
    client.list_budgets(s)
    assert fake.instances[1].kwargs["cert"] is sentinel


def test_plain_http_keeps_the_default_verification(store, work, fake, monkeypatch):
    monkeypatch.setattr(client, "https_context", lambda: pytest.fail("not for plain http"))
    with client.open_copy(store, "f1"):
        pass
    assert fake.instances[0].kwargs["cert"] is True


def test_nothing_connects_without_a_saved_sign_in(db_isolated, work, fake):
    s = ActualBudgetConfigStore()
    with pytest.raises(ValidationError):
        client.list_budgets(s)
    s.update({"server_url": URL})
    with pytest.raises(ValidationError):
        with client.open_copy(s, "f1"):
            pass
    assert fake.instances == []


def test_refused_behind_gateway(store, work, fake, behind_gateway):
    with pytest.raises(ConflictError):
        client.list_budgets(store)
    with pytest.raises(ConflictError):
        with client.open_copy(store, "f1"):
            pass
    assert fake.instances == [] and not work.exists()


def test_refused_while_the_flag_is_off(store, work, fake):
    with flags(actual_budget_sync=False):
        with pytest.raises(NotFoundError):
            client.list_budgets(store)
        with pytest.raises(NotFoundError):
            with client.open_copy(store, "f1"):
                pass
    assert fake.instances == []


# ── the budget list ─────────────────────────────────────────────────────────


def test_list_budgets(store, fake):
    fake.files = [
        dto("f1", "ACME «Home»"),
        dto("f2", "ACME Vault", key="k"),
        dto("f3", "Deleted one", deleted=1),
    ]
    assert client.list_budgets(store) == [
        {"id": "f1", "name": "ACME Home", "currency": None, "encrypted": False},
        {"id": "f2", "name": "ACME Vault", "currency": None, "encrypted": True},
    ]


# ── plain errors ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error,words",
    [
        (
            actual_exc.AuthorizationError("Could not validate password on login."),
            "password",
        ),
        (httpx.ConnectError(f"All connection attempts failed for {URL}"), "reach"),
        (httpx.ConnectTimeout(f"timed out {URL}"), "reach"),
        (httpx.ReadTimeout(f"timed out {URL}"), "reach"),
        (
            httpx.HTTPStatusError(
                f"500 {URL}",
                request=httpx.Request("GET", URL),
                response=httpx.Response(500, request=httpx.Request("GET", URL)),
            ),
            "500",
        ),
        (actual_exc.ActualDecryptionError(f"File is encrypted {ENC}"), "encryption password"),
        (actual_exc.InvalidZipFile(f"bad zip {URL}"), "could not read"),
        (actual_exc.ActualError(f"anything {URL} {PASSWORD}"), "could not read"),
        (ValueError(f"{URL} {PASSWORD}"), "could not read"),
        (OSError(f"disk {URL}"), "could not read"),
    ],
)
def test_wrong_password_plain_error(store, work, fake, error, words):
    fake.build_error = error
    for call in (
        lambda: client.list_budgets(store),
        lambda: client.open_copy(store, "f1").__enter__(),
    ):
        with pytest.raises(ExternalServiceError) as err:
            call()
        never_leaks(err.value)
        assert words in err.value.message.lower() or words in str(err.value).lower()


def _login_error(status: int) -> actual_exc.AuthorizationError:
    """What actualpy raises for a login answered with ``status`` (any above 400)."""
    return actual_exc.AuthorizationError(f"Server returned an HTTP error '{status}': '{URL}'")


@pytest.mark.parametrize(
    "status,refused",
    [(401, True), (403, True), (404, False), (429, False), (500, False)],
)
def test_only_a_401_or_403_login_is_a_refused_sign_in(store, work, fake, status, refused):
    fake.build_error = _login_error(status)
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    never_leaks(err.value)
    if refused:
        assert err.value.message == "Actual refused the sign-in. Check its password or token."
        assert store.public()["last_status"] == "auth_error"
    else:
        assert err.value.message == f"The Actual server answered with an error (HTTP {status})."
        assert store.public()["last_status"] != "auth_error"


def test_a_login_failure_that_names_no_status_isnt_a_refused_sign_in(store, work, fake):
    fake.build_error = actual_exc.AuthorizationError(f"Something went wrong on login: {URL}")
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    never_leaks(err.value)
    assert err.value.message == "Actual could not read this budget (AuthorizationError)."
    assert store.public()["last_status"] != "auth_error"


def test_server_down_plain_error(store, work, fake):
    fake.build_error = httpx.ConnectError(f"[Errno 61] Connection refused {URL}")
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    never_leaks(err.value)
    assert "reach" in str(err.value).lower() and "address" in str(err.value).lower()


def test_a_certificate_failure_says_so(store, work, fake):
    import ssl

    inner = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED " + URL)
    outer = httpx.ConnectError(f"{URL}")
    outer.__cause__ = inner
    fake.build_error = outer
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    never_leaks(err.value)
    assert "certificate" in str(err.value).lower()


def test_budget_gone_plain_error(store, work, fake):
    fake.files = [dto("other")]
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pass
    never_leaks(err.value)
    assert "no longer" in str(err.value).lower()
    fake.files = [dto("f1", deleted=1)]
    with pytest.raises(ExternalServiceError):
        with client.open_copy(store, "f1"):
            pass
    assert list(work.iterdir()) == []


def test_unknown_file_from_the_library_is_the_same_plain_error(store, work, fake):
    fake.download_error = actual_exc.UnknownFileId(f"Could not find {URL}")
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pass
    never_leaks(err.value)
    assert "no longer" in str(err.value).lower()


# ── sweep ────────────────────────────────────────────────────────────────────


def test_sweep_removes_leftovers(work):
    work.mkdir(parents=True)
    (work / "run-old").mkdir()
    (work / "run-old" / "db.sqlite").write_bytes(b"decrypted")
    (work / "stray.txt").write_text("x")
    assert client.sweep_leftovers() == 2
    assert list(work.iterdir()) == []


def test_sweep_with_nothing_there_is_fine(work):
    assert client.sweep_leftovers() == 0


def test_sweep_leaves_a_running_copy_alone(store, work, fake):
    with client.open_copy(store, "f1") as copy:
        (work / "run-old").mkdir()
        assert client.sweep_leftovers() == 1
        assert copy.directory.is_dir()
    assert list(work.iterdir()) == []


# ── the size cap (the real class over an in-process transport) ──────────────

FILES = {
    "status": "ok",
    "data": [
        {"deleted": 0, "fileId": "f1", "groupId": "g1", "name": "ACME Home", "encryptKeyId": None}
    ],
}


def serve(monkeypatch, download):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/account/login"):
            return httpx.Response(200, json={"status": "ok", "data": {"token": "tok"}})
        if path.endswith("/account/validate"):
            return httpx.Response(200, json={"status": "ok", "data": {"validated": True}})
        if path.endswith("/sync/list-user-files"):
            return httpx.Response(200, json=FILES)
        if path.endswith("/sync/download-user-file"):
            return download()
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


def chunks(count, size=400):
    for _ in range(count):
        yield b"x" * size


def test_oversized_download_refused(store, work, monkeypatch):
    monkeypatch.setattr(client, "MAX_DOWNLOAD_BYTES", 1000)
    serve(monkeypatch, lambda: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pytest.fail("the body must not run")
    never_leaks(err.value)
    assert "too large" in str(err.value).lower()
    assert list(work.iterdir()) == []


def test_oversized_download_without_a_length_is_stopped_by_bytes(store, work, monkeypatch):
    monkeypatch.setattr(client, "MAX_DOWNLOAD_BYTES", 1000)
    sent = []

    def body():
        for piece in chunks(100):
            sent.append(1)
            yield piece

    serve(monkeypatch, lambda: httpx.Response(200, content=body()))
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pass
    assert "too large" in str(err.value).lower()
    assert len(sent) < 10


def test_a_small_download_is_not_refused_for_size(store, work, monkeypatch):
    serve(monkeypatch, lambda: httpx.Response(200, content=b"not a zip"))
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pass
    assert "too large" not in str(err.value).lower()
    never_leaks(err.value)


def test_a_zip_that_unpacks_larger_than_the_cap_is_refused_before_unpacking(
    store, work, monkeypatch
):
    monkeypatch.setattr(client, "MAX_DOWNLOAD_BYTES", 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("db.sqlite", b"0" * 50_000)
    assert len(buf.getvalue()) < 1000
    serve(monkeypatch, lambda: httpx.Response(200, content=buf.getvalue()))
    with pytest.raises(ExternalServiceError) as err:
        with client.open_copy(store, "f1"):
            pass
    assert "too large" in str(err.value).lower()
    assert list(work.iterdir()) == []


def test_no_log_record_carries_the_address(db_isolated, work, monkeypatch, caplog):
    """httpx logs each request's full URL at INFO, userinfo and all."""
    real_client = httpx.Client
    s = ActualBudgetConfigStore()
    s.update(
        {"server_url": "http://alice:hunter2@acme-actual.example.test:5006", "password": PASSWORD}
    )
    serve(monkeypatch, lambda: httpx.Response(404))
    caplog.set_level(logging.DEBUG)
    assert client.list_budgets(s)
    with pytest.raises(ExternalServiceError):
        with client.open_copy(s, "f1"):
            pass
    with real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as other:
        other.get("http://other.example.test/x")
    seen = [r.getMessage() for r in caplog.records]
    for secret in ("hunter2", "alice", "acme-actual", "5006", PASSWORD):
        assert not [m for m in seen if secret in m]
    assert [m for m in seen if "other.example.test" in m], "other hosts still log"


def test_the_cap_is_512_mib():
    assert client.MAX_DOWNLOAD_BYTES == 512 * 1024 * 1024


# ── migrations are checked before they run ──────────────────────────────────


def test_checked_before_migrations_run(tmp_path):
    ran = []

    class Base:
        data_dir = tmp_path

        def run_migrations(self, migration_files):
            ran.append(list(migration_files))

    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.execute("CREATE TABLE __migrations__ (id INT PRIMARY KEY NOT NULL)")
    conn.commit()
    conn.close()
    checked = client.checked_class(Base)
    with pytest.raises(ExternalServiceError):
        checked().run_migrations(["db.sqlite", "migrations/1632571489012_remove_cache.js"])
    assert ran == []


def test_a_safe_pending_migration_runs_and_then_the_schema_is_checked(tmp_path):
    ran = []

    class Base:
        data_dir = tmp_path

        def run_migrations(self, migration_files):
            ran.append(list(migration_files))

    make_db(tmp_path / "db.sqlite")
    files = ["db.sqlite", "migrations/1723665565000_prefs.js"]
    client.checked_class(Base)().run_migrations(files)
    assert ran == [files]

    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.execute("ALTER TABLE accounts DROP COLUMN offbudget")
    conn.commit()
    conn.close()
    with pytest.raises(ExternalServiceError, match="accounts"):
        client.checked_class(Base)().run_migrations(files)


def test_the_checked_class_is_a_real_subclass_of_whatever_actual_is(fake):
    assert issubclass(client.checked_class(actual.Actual), FakeActual)


# ── the guard ───────────────────────────────────────────────────────────────


def test_only_the_client_imports_actual():
    root = Path(client.__file__).resolve().parents[2]
    offenders = []
    for path in root.rglob("*.py"):
        if path == Path(client.__file__).resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            if any(n == "actual" or n.startswith("actual.") for n in names):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


# ── a real local server (opt in) ────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("ACTUAL_TEST_URL"), reason="set ACTUAL_TEST_URL to a throwaway server"
)
def test_a_real_server_end_to_end(db_isolated, work):
    s = ActualBudgetConfigStore()
    s.update(
        {
            "server_url": os.environ["ACTUAL_TEST_URL"],
            "password": os.environ.get("ACTUAL_TEST_PASSWORD", ""),
            "encryption_password": os.environ.get("ACTUAL_TEST_ENCRYPTION_PASSWORD", ""),
        }
    )
    budgets = client.list_budgets(s)
    assert budgets
    wanted = os.environ.get("ACTUAL_TEST_BUDGET") or budgets[0]["id"]
    with client.open_copy(s, wanted) as copy:
        plan = copy.plan()
        assert plan["accounts"] and plan["months"]
        assert copy.server_version
        directory = copy.directory
    assert not directory.exists()


# ── what a sync uses of a copy ──


def test_every_connection_has_real_timeouts(store, work, fake):
    with client.open_copy(store, "f1"):
        timeout = FakeActual.instances[0].kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect and timeout.read and timeout.write and timeout.pool


def test_a_download_that_trickles_on_is_stopped(monkeypatch):
    moments = iter([0.0, 1.0, client.DOWNLOAD_SECONDS + 1])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(moments))

    class Trickle:
        headers: dict = {}

        def iter_bytes(self):
            while True:
                yield b"x"

    with pytest.raises(ExternalServiceError) as stopped:
        client._read_capped(Trickle())
    assert "too long" in stopped.value.message


def test_a_reset_budget_is_found_by_its_file_id_under_its_new_group(store, work, fake):
    FakeActual.files = [dto(name="ACME Home")]
    FakeActual.files[0].group_id = "group-after-reset"
    with client.open_copy(store, "f1"):
        assert FakeActual.instances[0].file.group_id == "group-after-reset"


@pytest.mark.parametrize("method", ["pull", "commit"])
def test_sync_and_commit_fail_plainly(store, work, fake, method):
    leak = httpx.ConnectError(f"{URL} refused {PASSWORD}")

    def boom():
        raise leak

    with client.open_copy(store, "f1") as copy:
        copy.actual.sync = boom
        copy.actual.commit = boom
        with pytest.raises(ExternalServiceError) as caught:
            getattr(copy, method)()
    never_leaks(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_an_error_naming_a_status_ends_a_run(store, work, fake):
    response = httpx.Response(404, request=httpx.Request("POST", URL))
    leak = httpx.HTTPStatusError(URL, request=response.request, response=response)

    def boom():
        raise leak

    with client.open_copy(store, "f1") as copy:
        copy.actual.sync = boom
        with pytest.raises(ynab_client.Stopped) as caught:
            copy.pull()
    assert "HTTP 404" in caught.value.message
    assert not ynab_client.rejected(caught.value) and not ynab_client.not_found(caught.value)


def test_reading_a_copy_that_was_swept_is_a_plain_error(store, work, fake):
    with client.open_copy(store, "f1") as copy:
        (copy.directory / "db.sqlite").unlink()
        (copy.directory / "db.sqlite").mkdir()
        with pytest.raises(ExternalServiceError) as caught:
            copy.rows()
        (copy.directory / "db.sqlite").rmdir()
    never_leaks(caught.value)
    assert not isinstance(caught.value, sqlite3.Error)


def test_people_read_actual_not_actual_budget():
    names = ("client.py", "compat.py", "shape.py", "shape_values.py", "writes.py", "apply_rows.py")
    folder = Path(client.__file__).parent
    for name in names:
        tree = ast.parse((folder / name).read_text("utf-8"))
        logged = {
            id(arg)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("info", "warning")
            for arg in node.args
        }
        docs = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
        }
        said = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in logged | docs
        ]
        assert [line for line in said if "Actual Budget" in line] == [], name


def test_a_sync_run_removes_its_copy_on_success_and_failure(store, work, fake):
    """Through the real ``open_copy``, the way a run opens its client."""
    with writes.open_sync_client("f1", Budget(10)) as made:
        directory = made.copy.directory
        assert directory.is_dir()
    assert not directory.exists()

    def boom():
        raise httpx.ConnectError(f"{URL} refused {PASSWORD}")

    with pytest.raises(ynab_client.Stopped) as caught:
        with writes.open_sync_client("f1", Budget(10)) as made:
            directory = made.copy.directory
            made.copy.actual.sync = boom
            made.copy.pull()
    never_leaks(caught.value)
    assert not directory.exists() and list(work.iterdir()) == []


def test_a_sync_run_that_cannot_connect_leaves_no_copy(store, work, fake):
    FakeActual.build_error = httpx.ConnectError(f"{URL} refused {PASSWORD}")
    with pytest.raises(ExternalServiceError) as caught:
        with writes.open_sync_client("f1", Budget(10)):
            raise AssertionError("no client without a connection")
    never_leaks(caught.value)
    assert list(work.iterdir()) == []


# ── a refused sign-in on the connector ───────────────────────────────────────


@pytest.fixture
def refused(monkeypatch):
    def refuse(_creds):
        raise actual_exc.AuthorizationError(
            f"Server returned an HTTP error '401': '{URL} {PASSWORD}'"
        )

    monkeypatch.setattr(client, "_list_files", refuse)


def _allowed(monkeypatch):
    monkeypatch.setattr(client, "_list_files", lambda _creds: [])


def test_a_refused_read_marks_the_connector_with_the_word_only(store, refused):
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    assert err.value.message == "Actual refused the sign-in. Check its password or token."
    public = store.public()
    assert public["last_status"] == "auth_error"
    assert public["last_error"] is None and public["last_synced_at"] is None


def test_the_next_good_read_clears_it(store, refused, monkeypatch):
    with pytest.raises(ExternalServiceError):
        client.list_budgets(store)
    _allowed(monkeypatch)
    assert client.list_budgets(store) == []
    assert store.public()["last_status"] == "never_run"


def test_a_good_read_leaves_any_other_status_alone(store, monkeypatch):
    store.record_status("error", error="made up")
    _allowed(monkeypatch)
    client.list_budgets(store)
    assert store.public()["last_status"] == "error"
    assert store.public()["last_error"] == "made up"


def test_a_server_thats_down_isnt_a_refused_sign_in(store, monkeypatch):
    def down(_creds):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(client, "_list_files", down)
    with pytest.raises(ExternalServiceError):
        client.list_budgets(store)
    assert store.public()["last_status"] == "never_run"


def test_opening_a_copy_clears_it_too(store, work, fake):
    store.note_sign_in(refused=True)
    with client.open_copy(store, "f1"):
        pass
    assert store.public()["last_status"] == "never_run"


def test_opening_a_copy_refused_marks_it(store, work, fake, monkeypatch):
    def refuse(*_a):
        raise _login_error(401)

    monkeypatch.setattr(client, "_download", refuse)
    with pytest.raises(ExternalServiceError):
        with client.open_copy(store, "f1"):
            pass
    assert store.public()["last_status"] == "auth_error"


def test_failing_to_note_it_doesnt_hide_the_refusal(store, refused, monkeypatch):
    def broken(self, *_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(ActualBudgetConfigStore, "_record", broken)
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    assert "refused the sign-in" in err.value.message
