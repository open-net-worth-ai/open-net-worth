"""The only module that imports ``actual`` (lazily, inside functions; a guard test enforces it).

``open_copy`` downloads one budget into a fresh owner-only directory under ``CACHE_DIR/actual_sync``,
checks it (migrations before they run, columns after), hands back a ``Copy``, and ALWAYS removes
the directory when the block ends. ``list_budgets`` lists what the saved sign-in can see. Both are
gated by the ``actual_budget_sync`` flag and refused behind the gateway, whatever ``enabled`` says:
the generic connector PATCH can set ``enabled`` without either check.

Everything a person can read from here is ours: an exception from ``actualpy`` or ``httpx`` can
carry the server address or a password, so it is classified and replaced, never shown. The
downloaded file is capped (``MAX_DOWNLOAD_BYTES``) both on the wire and unpacked.
"""

from __future__ import annotations

import functools
import ipaddress
import logging
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
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx

from backend.core import platform_fs
from backend.core.errors import ExternalServiceError, OpenNetWorthError, ValidationError
from backend.core.pinned_http import https_context
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import actual_flag
from backend.services.actual_sync import compat, shape
from backend.services.actual_sync import rows as reader
from backend.services.ynab_migration import mapping

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024

_active: set[str] = set()
_active_lock = threading.Lock()

_TOO_LARGE = "This Actual Budget file is too large to read safely."
_PLAIN_HTTP = (
    "This Actual Budget server is reached over plain http, which isn't encrypted. "
    "Anyone on that network could read the budget as it downloads."
)
_GONE = "That budget is no longer on the Actual Budget server. Pick it again in Connectors."


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
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise ExternalServiceError(_TOO_LARGE)
    return bytes(body)


def _check_unpacked_size(source) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        return  # the library raises its own error for this
    if total > MAX_DOWNLOAD_BYTES:
        raise ExternalServiceError(_TOO_LARGE)


@functools.lru_cache(maxsize=8)
def checked_class(base: type) -> type:
    """``base`` (``actual.Actual``) with three overrides: the migrations a snapshot still needs
    are checked BEFORE they run (``actualpy`` records one as done even when it ran only part of
    it), the columns this app reads and writes are checked after, and the download and unzip are
    size-capped (``actualpy`` buffers a download whole and has no cap)."""

    class Checked(base):  # type: ignore[valid-type, misc]
        def run_migrations(self, migration_files):
            db = self.data_dir / "db.sqlite"
            with closing(sqlite3.connect(db)) as conn:
                compat.check_pending(compat.pending_names(conn, migration_files))
            super().run_migrations(migration_files)
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
            _check_unpacked_size(file_bytes)
            if hasattr(file_bytes, "seek"):
                file_bytes.seek(0)
            return super().import_zip(file_bytes)

    return Checked


# ── errors a person can read ────────────────────────────────────────────────


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and exc not in out:
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def _plain_error(exc: Exception) -> ExternalServiceError:
    """Ours, in plain words, from whatever the library raised. Never ``str(exc)``: it can hold
    the server address or a password."""
    from actual import exceptions as ax

    chain = _chain(exc)

    def has(*types: type) -> bool:
        return any(isinstance(e, types) for e in chain)

    status = next((e for e in chain if isinstance(e, httpx.HTTPStatusError)), None)
    if has(ax.AuthorizationError) or (status and status.response.status_code in (401, 403)):
        text = "Actual Budget refused the sign-in. Check the password or token in Connectors."
    elif has(ax.UnknownFileId):
        text = _GONE
    elif has(ax.ActualDecryptionError, ax.ActualEncryptionError):
        text = "This budget is encrypted, and its encryption password is missing or wrong."
    elif has(ssl.SSLError):
        text = (
            "The Actual Budget server's certificate could not be verified. "
            "Use a certificate this computer trusts, or plain http on a private network."
        )
    elif status:
        text = (
            f"The Actual Budget server answered with an error (HTTP {status.response.status_code})."
        )
    elif has(httpx.TransportError, TimeoutError):
        text = "Could not reach the Actual Budget server. Check the address and that it is running."
    else:
        text = f"Actual Budget could not read this budget ({type(exc).__name__})."
    logger.info("Actual Budget request failed (%s)", type(exc).__name__)
    return ExternalServiceError(text)


@contextmanager
def _plain() -> Iterator[None]:
    try:
        yield
    except OpenNetWorthError:
        raise
    except Exception as exc:
        raise _plain_error(exc) from None


# ── connecting ──────────────────────────────────────────────────────────────


def _prepare(store: ActualBudgetConfigStore | None) -> tuple[ActualBudgetConfigStore, dict]:
    actual_flag.require()
    store = store or ActualBudgetConfigStore()
    store.require_available()
    creds = store.credentials()
    if not creds["server_url"] or not (creds["password"] or creds["token"]):
        raise ValidationError("Add the Actual Budget server address and a password or token first.")
    return store, creds


def _build(creds: dict, directory: Path | None = None):
    """A client signed in to the server. The password signs in when there is one; the token is
    for servers that do not take a password."""
    try:
        import actual
    except ImportError:
        raise ExternalServiceError("Actual Budget support isn't installed in this build.") from None

    url = creds["server_url"]
    kwargs: dict[str, Any] = {
        "base_url": url,
        "password": creds["password"] or None,
        "token": None if creds["password"] else creds["token"],
        "cert": https_context() if url.lower().startswith("https:") else True,
        "encryption_password": creds["encryption_password"] or None,
    }
    if directory is not None:
        kwargs["data_dir"] = str(directory)
    return checked_class(actual.Actual)(**kwargs)


def list_budgets(store: ActualBudgetConfigStore | None = None) -> list[dict]:
    """What the saved sign-in can see: ``{"id" (the cloud file id), "name", "currency", "encrypted"}``.
    ``currency`` is ``None`` here: it lives inside each budget, which is not downloaded to list it."""
    _, creds = _prepare(store)
    with _plain():
        with _build(creds) as session:
            files = session.list_user_files().data
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
    actual: Any = field(repr=False)  # the live client, for the writes of a later stage
    notices: list[str] = field(default_factory=list)  # things to show once, never the address

    def rows(self) -> shape.Rows:
        with closing(reader.connect(self.directory)) as conn:
            return reader.read_rows(conn, self.mode)

    def plan(self) -> dict:
        return shape.plan_from_rows(self.rows())


def plain_http_notice(url: str) -> list[str]:
    """A warning when the address is plain http to anything but this computer. Allowed, not hidden."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "http" or host == "localhost" or host.endswith(".localhost"):
        return []
    try:
        if ipaddress.ip_address(host).is_loopback:
            return []
    except ValueError:
        pass
    return [_PLAIN_HTTP]


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
    _, creds = _prepare(store)
    directory = _new_directory()
    try:
        with _plain():
            session = _build(creds, directory)
        with session:
            with _plain():
                copy = _download(session, creds, budget_id, directory)
            yield copy
    finally:
        _remove(directory)
