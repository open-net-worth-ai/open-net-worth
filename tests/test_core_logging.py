"""Unit tests for backend.core.logging (RFC P1-A1 §3.5).

The module is a stdlib-only leaf: no network, no app state, no live data.
Tests are fully hermetic — they reset and restore the ``backend`` logger's
handlers/level/propagation after each test so the test process's own logging
isn't polluted.

Run:
    pytest tests/test_core_logging.py -v
Coverage target: 90 %+
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
from contextlib import contextmanager

import pytest

import backend.core.logging as core_logging
from backend.core.logging import (
    _BACKUP_COUNT,
    _DEFAULT_FORMAT,
    _DEFAULT_LEVEL,
    _FILE_DATE_FMT,
    _FILE_FORMAT,
    _FILE_HANDLER_TAG,
    _MAX_BYTES,
    ROOT_LOGGER_NAME,
    add_file_handler,
    attach_uvicorn_handlers,
    configure_logging,
    get_logger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _clean_backend_logger():
    """Snapshot the backend logger, yield it, then restore it unconditionally.

    This prevents leaking test-added handlers into subsequent tests (or into the
    real logging used by other test modules in the same process).
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate

    try:
        yield logger
    finally:
        # Close any handlers we opened (important for RotatingFileHandler).
        for h in logger.handlers:
            if h not in original_handlers:
                h.close()
        logger.handlers = original_handlers
        logger.level = original_level
        logger.propagate = original_propagate


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


def test_get_logger_returns_stdlib_logger():
    log = get_logger("backend.test_mod")
    assert isinstance(log, logging.Logger)


def test_get_logger_name_matches():
    log = get_logger("backend.some.module")
    assert log.name == "backend.some.module"


def test_get_logger_same_instance_on_repeated_calls():
    """logging.getLogger is supposed to return the same instance for the same name."""
    a = get_logger("backend.singleton")
    b = get_logger("backend.singleton")
    assert a is b


# ---------------------------------------------------------------------------
# configure_logging — public constants
# ---------------------------------------------------------------------------


def test_default_level_is_info():
    assert _DEFAULT_LEVEL == logging.INFO


def test_default_format_contains_key_fields():
    """The format string must include levelname, name, and message."""
    assert "%(levelname)s" in _DEFAULT_FORMAT
    assert "%(name)s" in _DEFAULT_FORMAT
    assert "%(message)s" in _DEFAULT_FORMAT


def test_file_format_has_fixed_width_level():
    """_FILE_FORMAT uses %-8s to align level names."""
    assert "%(levelname)-8s" in _FILE_FORMAT


def test_file_date_fmt_is_iso8601_ish():
    assert _FILE_DATE_FMT == "%Y-%m-%dT%H:%M:%S"


def test_max_bytes_is_5_mb():
    assert _MAX_BYTES == 5 * 1024 * 1024


def test_backup_count_is_3():
    assert _BACKUP_COUNT == 3


def test_root_logger_name_is_backend():
    assert ROOT_LOGGER_NAME == "backend"


# ---------------------------------------------------------------------------
# configure_logging — behaviour
# ---------------------------------------------------------------------------


def test_configure_logging_sets_level_to_info_by_default():
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging()
        assert logger.level == logging.INFO


def test_configure_logging_adds_stream_handler_when_none():
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging()
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


def test_configure_logging_disables_propagation():
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging()
        assert logger.propagate is False


def test_configure_logging_accepts_int_level():
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging(level=logging.DEBUG)
        assert logger.level == logging.DEBUG


def test_configure_logging_accepts_string_level():
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging(level="WARNING")
        assert logger.level == logging.WARNING


def test_configure_logging_is_idempotent_handler_count():
    """Calling configure_logging twice must NOT double-add handlers."""
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging()
        count_after_first = len(logger.handlers)
        configure_logging()
        assert len(logger.handlers) == count_after_first


def test_configure_logging_stream_handler_uses_default_format():
    """The StreamHandler installed must use _DEFAULT_FORMAT."""
    with _clean_backend_logger() as logger:
        logger.handlers.clear()
        configure_logging()
        stream_handlers = [h for h in logger.handlers if type(h) is logging.StreamHandler]
        assert stream_handlers, "No StreamHandler found"
        fmt = stream_handlers[0].formatter._fmt  # type: ignore[union-attr]
        assert fmt == _DEFAULT_FORMAT


def test_configure_logging_level_updated_even_if_handler_already_present():
    """If handlers already exist, configure_logging still adjusts the level."""
    with _clean_backend_logger() as logger:
        # Pre-install a dummy handler.
        logger.addHandler(logging.NullHandler())
        configure_logging(level=logging.DEBUG)
        assert logger.level == logging.DEBUG


# ---------------------------------------------------------------------------
# attach_uvicorn_handlers
# ---------------------------------------------------------------------------


def test_attach_uvicorn_handlers_copies_handlers():
    """Handlers from the uvicorn logger should appear on the backend logger."""
    with _clean_backend_logger() as backend_logger:
        uv_logger = logging.getLogger("uvicorn")
        uv_original_handlers = list(uv_logger.handlers)

        # Install a known dummy handler on uvicorn so we can detect the copy.
        sentinel = logging.NullHandler()
        uv_logger.addHandler(sentinel)
        try:
            backend_logger.handlers.clear()
            attach_uvicorn_handlers()
            assert sentinel in backend_logger.handlers
        finally:
            uv_logger.handlers = uv_original_handlers


def _with_uvicorn_handler():
    """Install a handler on the ``uvicorn`` logger, as a real uvicorn boot would.

    Without one there is nothing to attach, and ``attach_uvicorn_handlers`` is a no-op by
    design (see ``test_attach_uvicorn_handlers_is_a_no_op_when_uvicorn_has_none``). These
    tests are about what it does when it DOES have work to do, so they must set it up.
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        uv = logging.getLogger("uvicorn")
        original = list(uv.handlers)
        uv.addHandler(logging.NullHandler())
        try:
            yield
        finally:
            uv.handlers = original

    return _ctx()


def test_attach_uvicorn_handlers_sets_level_info():
    with _clean_backend_logger() as backend_logger, _with_uvicorn_handler():
        backend_logger.handlers.clear()
        attach_uvicorn_handlers()
        assert backend_logger.level == logging.INFO


def test_attach_uvicorn_handlers_disables_propagation():
    with _clean_backend_logger() as backend_logger, _with_uvicorn_handler():
        backend_logger.handlers.clear()
        attach_uvicorn_handlers()
        assert backend_logger.propagate is False


def test_attach_uvicorn_handlers_is_a_no_op_when_uvicorn_has_none():
    """THE regression guard.

    Copying an empty handler list and then setting ``propagate = False`` left ``backend``
    with nowhere to send a record and no parent to fall back on — silently dropping every
    backend.* log for the life of the process. ``with TestClient(app)`` runs lifespan
    in-process without uvicorn's dictConfig, so one such test poisoned caplog for every
    later test in the session (~18 failures across turn_diagnostics, rss_poller, push_retry
    and the pricing warnings — all of which passed in isolation and only failed in the full
    suite, which is exactly what made it so hard to place).
    """
    with _clean_backend_logger() as backend_logger:
        uv = logging.getLogger("uvicorn")
        original = list(uv.handlers)
        uv.handlers = []
        own = logging.NullHandler()
        backend_logger.handlers = [own]
        backend_logger.propagate = True
        try:
            attach_uvicorn_handlers()
            assert backend_logger.handlers == [own], "it discarded the real handlers"
            assert backend_logger.propagate is True, "it cut the logger off from its parent"
        finally:
            uv.handlers = original


def test_attach_uvicorn_handlers_replaces_existing_handlers():
    """attach_uvicorn_handlers overwrites backend handlers with uvicorn's list."""
    with _clean_backend_logger() as backend_logger:
        # Pre-install two handlers.
        h1 = logging.NullHandler()
        h2 = logging.NullHandler()
        backend_logger.addHandler(h1)
        backend_logger.addHandler(h2)

        uv_logger = logging.getLogger("uvicorn")
        uv_original_handlers = list(uv_logger.handlers)
        uv_sentinel = logging.NullHandler()
        uv_logger.addHandler(uv_sentinel)
        try:
            attach_uvicorn_handlers()
            # h1 and h2 should be gone; the uvicorn sentinel should be present.
            assert h1 not in backend_logger.handlers
            assert h2 not in backend_logger.handlers
            assert uv_sentinel in backend_logger.handlers
        finally:
            uv_logger.handlers = uv_original_handlers


# ---------------------------------------------------------------------------
# add_file_handler
# ---------------------------------------------------------------------------


def test_add_file_handler_creates_rotating_handler(tmp_path):
    log_file = tmp_path / "test.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        rfh = [
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(rfh) == 1


def test_add_file_handler_creates_log_file(tmp_path):
    log_file = tmp_path / "test.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        assert log_file.exists()


def test_add_file_handler_creates_parent_directory(tmp_path):
    nested = tmp_path / "sub" / "dir" / "app.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=nested)
        assert nested.parent.is_dir()


def test_add_file_handler_tag_is_set(tmp_path):
    log_file = tmp_path / "tag.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        file_handlers = [
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers
        assert getattr(file_handlers[0], _FILE_HANDLER_TAG, False) is True


def test_add_file_handler_is_idempotent(tmp_path):
    """Calling add_file_handler twice must NOT add two RotatingFileHandlers."""
    log_file = tmp_path / "idempotent.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        count_after_first = len(
            [
                h
                for h in backend_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
        )
        add_file_handler(log_file=log_file)
        count_after_second = len(
            [
                h
                for h in backend_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
        )
        assert count_after_first == count_after_second == 1


def test_add_file_handler_rotating_params(tmp_path):
    """The RotatingFileHandler must use the module-level size/backup constants."""
    log_file = tmp_path / "params.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        rfh = next(
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert rfh.maxBytes == _MAX_BYTES
        assert rfh.backupCount == _BACKUP_COUNT


def test_add_file_handler_sets_debug_level(tmp_path):
    """The file handler must capture DEBUG+ (logger level governs the caller)."""
    log_file = tmp_path / "level.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        rfh = next(
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert rfh.level == logging.DEBUG


def test_add_file_handler_formatter_uses_file_format(tmp_path):
    log_file = tmp_path / "fmt.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        rfh = next(
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert rfh.formatter._fmt == _FILE_FORMAT  # type: ignore[union-attr]
        assert rfh.formatter.datefmt == _FILE_DATE_FMT  # type: ignore[union-attr]


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_add_file_handler_log_dir_permissions(tmp_path):
    """The log directory must be set to 0700 (owner-only)."""
    log_file = tmp_path / "perms" / "app.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        mode = os.stat(log_file.parent).st_mode
        # Only owner bits should be set (rwx = 0700).
        assert stat.S_IMODE(mode) == stat.S_IRWXU


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_add_file_handler_log_file_permissions(tmp_path):
    """The log file itself must be set to 0600 (owner read/write only)."""
    log_file = tmp_path / "perms" / "app.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        mode = os.stat(log_file).st_mode
        assert stat.S_IMODE(mode) == (stat.S_IRUSR | stat.S_IWUSR)


def test_add_file_handler_chmod_ioerror_does_not_raise(tmp_path, monkeypatch):
    """An OSError from os.chmod must be swallowed (best-effort on non-POSIX)."""
    log_file = tmp_path / "nochmod.log"

    call_count = {"n": 0}

    def _bad_chmod(path, mode):
        call_count["n"] += 1
        raise OSError("not supported on this platform")

    monkeypatch.setattr(core_logging.os, "chmod", _bad_chmod)
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        # Should not raise.
        add_file_handler(log_file=log_file)
    # We expect chmod to have been called at least once (for dir + file).
    assert call_count["n"] >= 1


def test_add_file_handler_utf8_encoding(tmp_path):
    """The RotatingFileHandler must use UTF-8 encoding."""
    log_file = tmp_path / "utf8.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        add_file_handler(log_file=log_file)
        rfh = next(
            h
            for h in backend_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert rfh.encoding == "utf-8"


def test_add_file_handler_writes_records(tmp_path):
    """After add_file_handler, emitting a record actually reaches the file."""
    log_file = tmp_path / "records.log"
    with _clean_backend_logger() as backend_logger:
        backend_logger.handlers.clear()
        backend_logger.setLevel(logging.DEBUG)
        add_file_handler(log_file=log_file)
        backend_logger.info("hello from test")
        # Flush to make sure the write is committed.
        for h in backend_logger.handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "hello from test" in content
