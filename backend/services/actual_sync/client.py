"""The only module that imports ``actual`` (lazily, inside functions; a guard test enforces it).

``open_copy`` downloads one budget into a fresh owner-only directory under ``CACHE_DIR/actual_sync``,
checks it (migrations before they run, columns after), hands back a ``Copy``, and ALWAYS removes
the directory when the block ends. ``list_budgets`` lists what the saved sign-in can see. Both are
gated by the ``actual_budget_sync`` flag and refused behind the gateway, whatever ``enabled`` says:
the generic connector PATCH can set ``enabled`` without either check.

Everything a person can read from here is ours: an exception from ``actualpy`` or ``httpx`` can
carry the server address or a password, so it is classified and replaced, never shown. The
downloaded file is capped (``MAX_DOWNLOAD_BYTES``) both on the wire and unpacked. Every request has
a real timeout, and a download a time limit of its own, so no server can hold a run forever.
"""

from __future__ import annotations

import functools
import logging
import re
import shutil
import sqlite3
import ssl
import sys
import threading
import time
import uuid
import zipfile
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import httpx

from backend.core import platform_fs
from backend.core.errors import ExternalServiceError, OpenNetWorthError, ValidationError
from backend.core.pinned_http import https_context
from backend.db.actual_budget_config_store import ActualBudgetConfigStore, plain_http_remote
from backend.services import actual_flag
from backend.services.actual_sync import compat, shape, sql_guard
from backend.services.actual_sync import rows as reader
from backend.services.actual_sync.log_guard import keep_address_out_of_logs
from backend.services.ynab_migration import mapping
from backend.services.ynab_sync.client import Stopped

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=15.0)
DOWNLOAD_SECONDS = 600.0  # a read timeout covers one chunk; a trickle could go on for ever
CLOCK_MARGIN = timedelta(minutes=10)  # how far behind a person's device clock a read still sees

_active: set[str] = set()
_active_lock = threading.Lock()

_BUDGET_FILES = frozenset({"db.sqlite", "metadata.json"})

_TOO_LARGE = "This Actual file is too large to read safely."
_NOT_A_BUDGET = "This Actual file holds more than a budget, so it wasn't read."
_PLAIN_HTTP = (
    "This Actual server is reached over plain http, which isn't encrypted. "
    "Anyone on that network could read the budget as it downloads."
)
_GONE = "That budget is no longer on the Actual server. Pick it again in Connectors."
_TOO_SLOW = "This Actual file took too long to download."


def work_root() -> Path:
    from backend.core.paths import CACHE_DIR

    return CACHE_DIR / "actual_sync"


# ── the working directory ───────────────────────────────────────────────────


def _new_directory() -> Path:
    root = work_root()
    root.mkdir(parents=True, exist_ok=True)
    platform_fs.restrict_to_owner(root)
    path = root / f"run-{uuid.uuid4().hex}"
    path.mkdir()
    platform_fs.restrict_to_owner(path)
    with _active_lock:
        _active.add(str(path))
    return path


def _remove(path: Path) -> None:
    """Delete a run's directory. Windows holds a file open a moment after its engine closes."""
    try:
        for attempt in range(10 if sys.platform == "win32" else 1):
            try:
                shutil.rmtree(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                if attempt == (9 if sys.platform == "win32" else 0):
                    logger.warning("could not remove Actual Budget copy %s", path.name)
                    return
                time.sleep(0.2)
    finally:
        with _active_lock:
            _active.discard(str(path))


def sweep_leftovers() -> int:
    """Delete every copy a crashed run left behind (boot). A run still going in this process is
    left alone. Returns how many entries were removed."""
    root = work_root()
    if not root.is_dir():
        return 0
    removed = 0
    for entry in root.iterdir():
        with _active_lock:
            if str(entry) in _active:
                continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            logger.warning("could not sweep Actual Budget leftover %s", entry.name)
    return removed


# ── the checked client class ─────────────────────────────────────────────────


def _read_capped(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise ExternalServiceError(_TOO_LARGE)
    body = bytearray()
    stop_at = time.monotonic() + DOWNLOAD_SECONDS
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise ExternalServiceError(_TOO_LARGE)
        if time.monotonic() > stop_at:
            raise ExternalServiceError(_TOO_SLOW)
    return bytes(body)


def _check_archive(source) -> None:
    """Only a budget and its metadata, and not too much of them. A journal file beside the database
    would be replayed when it opens, and a journal can name a file for SQLite to delete."""
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
    except zipfile.BadZipFile:
        return  # the library raises its own error for this
    if any(info.filename not in _BUDGET_FILES for info in members):
        raise ExternalServiceError(_NOT_A_BUDGET)
    if sum(info.file_size for info in members) > MAX_DOWNLOAD_BYTES:
        raise ExternalServiceError(_TOO_LARGE)


@functools.lru_cache(maxsize=8)
def checked_class(base: type) -> type:
    """``base`` (``actual.Actual``) with three overrides: the migrations a snapshot still needs
    are checked BEFORE they run (``actualpy`` records one as done even when it ran only part of
    it) and run confined to the copy (``sql_guard``), the columns this app reads and writes are
    checked after, and the download and unzip are checked for size and content (``actualpy``
    buffers a download whole, unpacks whatever it holds, and has no cap)."""

    class Checked(base):  # type: ignore[valid-type, misc]
        def run_migrations(self, migration_files):
            """In place of the library's loop, whose connection can reach any file. The rest of
            what its method does, the reflection at the end, stays."""
            from actual.database import reflect_model
            from actual.exceptions import ActualError
            from actual.migrations import js_migration_statements

            db = self.data_dir / "db.sqlite"
            sql_guard.run_migrations(db, migration_files, self.data_file, js_migration_statements)
            if self.engine is None:
                raise ActualError("Engine not initialized.")
            self._database_metadata = reflect_model(self.engine)
            with closing(sqlite3.connect(db)) as conn:
                compat.check_schema(conn)

        def download_user_file(self, file_id):
            from actual.api.models import Endpoints

            with self._requests_session.stream(
                "GET", Endpoints.DOWNLOAD_USER_FILE.value, headers=self.headers(file_id)
            ) as response:
                response.raise_for_status()
                return _read_capped(response)

        def import_zip(self, file_bytes):
            _check_archive(file_bytes)
            if hasattr(file_bytes, "seek"):
                file_bytes.seek(0)
            return super().import_zip(file_bytes)

        _reads_from = None

        def sync(self):
            """Asks from a little before this copy began, not from the clock. ``commit`` stamps our
            messages and moves the clock past them, and the server answers only what is stamped
            later, so an edit stamped just before ours and arriving just after would stay out of
            this copy, and a later batch would write over it unseen. What comes back twice is
            replayed in timestamp order, so each cell ends where the server's does."""
            clock = self._hulc_client
            if self._reads_from is None:
                changes = super().sync()
                ts = self._hulc_client.ts
                self._reads_from = ts - CLOCK_MARGIN if ts else None
                return changes
            asked = type(clock)(clock.client_id, 0, self._reads_from)
            self._hulc_client = asked
            try:
                return super().sync()
            finally:
                if self._hulc_client is asked:
                    self._hulc_client = clock

    return Checked


# ── errors a person can read ────────────────────────────────────────────────


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and exc not in out:
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


class SignInRefused(Stopped):
    """The server turned the saved password or token away."""


def _plain_error(exc: Exception) -> Stopped:
    """Ours, in plain words, from whatever the library raised. Never ``str(exc)``: it can hold
    the server address or a password. It ends a run, whatever status it names."""
    from actual import exceptions as ax

    chain = _chain(exc)
    logger.info("Actual Budget request failed (%s)", type(exc).__name__)

    def has(*types: type) -> bool:
        return any(isinstance(e, types) for e in chain)

    status = next((e for e in chain if isinstance(e, httpx.HTTPStatusError)), None)
    code = status.response.status_code if status else None
    # actualpy's login raises AuthorizationError for any status above 400, so the status is in its text.
    auth = next((e for e in chain if isinstance(e, ax.AuthorizationError)), None)
    if auth:
        said = str(auth)
        found = re.search(r"HTTP error '(\d+)'", said)
        code = int(found[1]) if found else code
        if "Could not validate password on login" in said:
            code = 401
    if code in (401, 403):
        return SignInRefused("Actual refused the sign-in. Check its password or token.")
    elif has(ax.UnknownFileId):
        text = _GONE
    elif has(ax.ActualDecryptionError, ax.ActualEncryptionError):
        text = "This budget is encrypted, and its encryption password is missing or wrong."
    elif has(ssl.SSLError):
        text = (
            "The Actual server's certificate could not be verified. "
            "Use a certificate this computer trusts, or plain http on a private network."
        )
    elif code:
        text = f"The Actual server answered with an error (HTTP {code})."
    elif has(httpx.TransportError, TimeoutError):
        text = "Could not reach the Actual server. Check the address and that it is running."
    else:
        text = f"Actual could not read this budget ({type(exc).__name__})."
    return Stopped(text)


def _plainly(call, *args):
    """``call(*args)``, with anything the libraries raise replaced by our own error. Ours is
    raised AFTER the ``except`` block ends, so no ``__context__`` keeps the original (and the
    address its text can hold) alive on the new one."""
    failure = None
    try:
        return call(*args)
    except OpenNetWorthError:
        raise
    except Exception as exc:
        failure = _plain_error(exc)
    raise failure


# ── connecting ──────────────────────────────────────────────────────────────


def _prepare(store: ActualBudgetConfigStore | None) -> tuple[ActualBudgetConfigStore, dict]:
    actual_flag.require()
    store = store or ActualBudgetConfigStore()
    store.require_available()
    creds = store.credentials()
    if not creds["server_url"] or not (creds["password"] or creds["token"]):
        raise ValidationError("Add the Actual server address and a password or token first.")
    return store, creds


def _build(creds: dict, directory: Path | None = None):
    """A client signed in to the server. The password signs in when there is one; the token is
    for servers that do not take a password."""
    try:
        import actual
    except ImportError:
        actual = None
    if actual is None:
        raise ExternalServiceError("Actual support isn't installed in this build.")

    url = creds["server_url"]
    keep_address_out_of_logs(url)
    kwargs: dict[str, Any] = {
        "base_url": url,
        "password": creds["password"] or None,
        "token": None if creds["password"] else creds["token"],
        "cert": https_context() if url.lower().startswith("https:") else True,
        "encryption_password": creds["encryption_password"] or None,
        "timeout": TIMEOUT,
    }
    if directory is not None:
        kwargs["data_dir"] = str(directory)
    return checked_class(actual.Actual)(**kwargs)


def _note_sign_in(store: ActualBudgetConfigStore, refused: bool) -> None:
    """Never in the way of the read's own outcome."""
    try:
        store.note_sign_in(refused)
    except Exception:
        logger.warning("could not note the Actual Budget sign-in", exc_info=False)


@contextmanager
def _sign_in_noted(store: ActualBudgetConfigStore) -> Iterator[None]:
    try:
        yield
    except SignInRefused:
        _note_sign_in(store, refused=True)
        raise
    _note_sign_in(store, refused=False)


def _list_files(creds: dict) -> list:
    with _build(creds) as session:
        return session.list_user_files().data


def list_budgets(store: ActualBudgetConfigStore | None = None) -> list[dict]:
    """What the saved sign-in can see: ``{"id" (the cloud file id), "name", "currency", "encrypted"}``.
    ``currency`` is ``None`` here: it lives inside each budget, which is not downloaded to list it."""
    store, creds = _prepare(store)
    with _sign_in_noted(store):
        files = _plainly(_list_files, creds)
    return [
        {
            "id": f.file_id,
            "name": mapping.clean(f.name, 120),
            "currency": None,
            "encrypted": bool(f.encrypt_key_id),
        }
        for f in files
        if not f.deleted
    ]


# ── a copy ──────────────────────────────────────────────────────────────────


@dataclass
class Copy:
    """One downloaded budget. Valid only inside ``open_copy``'s block."""

    directory: Path
    name: str
    encrypted: bool
    currency: str | None
    server_version: str | None
    mode: str
    metadata: dict
    actual: Any = field(repr=False)  # the live client; reached through the methods below
    notices: list[str] = field(default_factory=list)  # things to show once, never the address

    def _read_rows(self) -> shape.Rows:
        with closing(reader.connect(self.directory)) as conn:
            return reader.read_rows(conn, self.mode)

    def rows(self) -> shape.Rows:
        return _plainly(self._read_rows)

    def plan(self) -> dict:
        return shape.plan_from_rows(self.rows())

    @property
    def session(self) -> Any:
        """The library's session on this copy: what a write changes before ``commit``."""
        return self.actual.session

    @property
    def db(self) -> Any:
        """The library's table models."""
        from actual import database

        return database

    def pull(self) -> None:
        """Bring in what the server has. Never over uncommitted edits: replaying its messages has
        no timestamp check, so it would write straight over them."""
        _plainly(self.actual.sync)

    def commit(self) -> None:
        _plainly(self.actual.commit)

    def plainly(self, call, *args) -> Any:
        """``call(*args)``, with whatever the library raises replaced by a plain error."""
        return _plainly(call, *args)


def plain_http_notice(url: str) -> list[str]:
    """A warning when the address is plain http to anything but this computer. Allowed, not hidden."""
    return [_PLAIN_HTTP] if plain_http_remote(url) else []


def _server_version(session) -> str | None:
    try:
        return session.info().build.version
    except Exception:
        return None


def _download(session, creds: dict, budget_id: str, directory: Path) -> Copy:
    file = next((f for f in session.list_user_files().data if f.file_id == budget_id), None)
    if file is None or file.deleted:
        raise ExternalServiceError(_GONE)
    session.set_file(file)
    session.download_budget(creds["encryption_password"] or None)
    metadata = _read_metadata(directory)
    with closing(reader.connect(directory)) as conn:
        mode = compat.budget_mode(conn, metadata)
        currency = reader.read_currency(conn)
    return Copy(
        directory=directory,
        name=mapping.clean(file.name, 120),
        encrypted=bool(file.encrypt_key_id),
        currency=currency,
        server_version=_server_version(session),
        mode=mode,
        metadata=metadata,
        actual=session,
        notices=plain_http_notice(creds["server_url"]),
    )


def _read_metadata(directory: Path) -> dict:
    import json

    try:
        data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def open_copy(store: ActualBudgetConfigStore | None, budget_id: str) -> Iterator[Copy]:
    """Download ``budget_id`` (the cloud file id) afresh, and remove the copy when the block
    ends, whatever happens in it."""
    store, creds = _prepare(store)
    directory = _new_directory()
    try:
        with _sign_in_noted(store):  # signing in is part of building the client
            session = _plainly(_build, creds, directory)
        with session:
            with _sign_in_noted(store):
                copy = _plainly(_download, session, creds, budget_id, directory)
            yield copy
    finally:
        _remove(directory)
