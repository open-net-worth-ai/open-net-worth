"""A watched-folder grant anchors bytes to the granted directory descriptor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.cli import desktop_file

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="Only a directory descriptor survives a rename of its directory"
)
INSIDE = b"date,amount\nINSIDE,1\n"
OUTSIDE = b"date,amount\nOUTSIDE,900\n"


@pytest.fixture
def selection(tmp_path):
    folder = tmp_path / "Statements"
    outside = tmp_path / "Outside"
    folder.mkdir()
    outside.mkdir()
    (folder / "statement.csv").write_bytes(INSIDE)
    (outside / "statement.csv").write_bytes(OUTSIDE)
    directory, file = folder.stat(), (folder / "statement.csv").stat()
    request = {
        "directory": str(folder),
        "device": directory.st_dev,
        "inode": directory.st_ino,
        "name": "statement.csv",
        "file": {
            "device": file.st_dev,
            "inode": file.st_ino,
            "size": file.st_size,
            "modifiedAt": file.st_mtime_ns / 1_000_000,
        },
    }
    return folder, outside, request


def test_reads_regular_file_and_tolerates_node_millisecond_rounding(selection):
    _, _, request = selection
    request["file"]["modifiedAt"] += 0.0005
    assert desktop_file.read_watched_file(request) == ("statement.csv", INSIDE)


@POSIX_ONLY
def test_directory_swap_after_open_cannot_redirect_read(selection, monkeypatch):
    folder, outside, request = selection
    parked = folder.with_name("original-folder")
    real_open = os.open

    def swap_before_file_open(path, flags, **kwargs):
        if "dir_fd" in kwargs:
            folder.rename(parked)
            folder.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_file_open)
    assert desktop_file.read_watched_file(request) == ("statement.csv", INSIDE)


@POSIX_ONLY
def test_outside_inode_poisoned_during_candidate_stat_is_refused(selection, monkeypatch):
    folder, outside, request = selection
    poisoned = (outside / "statement.csv").stat()
    request["file"] = {
        "device": poisoned.st_dev,
        "inode": poisoned.st_ino,
        "size": poisoned.st_size,
        "modifiedAt": poisoned.st_mtime_ns / 1_000_000,
    }
    parked = folder.with_name("original-folder")
    real_open = os.open

    def swap_before_file_open(path, flags, **kwargs):
        if "dir_fd" in kwargs:
            folder.rename(parked)
            folder.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_file_open)
    with pytest.raises(desktop_file.WatchedFileError, match="file changed"):
        desktop_file.read_watched_file(request)


def test_replaced_directory_and_final_symlink_are_refused(selection):
    folder, outside, request = selection
    (folder / "statement.csv").unlink()
    (folder / "statement.csv").symlink_to(outside / "statement.csv")
    with pytest.raises(OSError):
        desktop_file.read_watched_file(request)
    folder.rename(folder.with_name("original"))
    folder.mkdir()
    (folder / "statement.csv").write_bytes(OUTSIDE)
    with pytest.raises(desktop_file.WatchedFileError, match="folder changed"):
        desktop_file.read_watched_file(request)


@pytest.mark.parametrize("name", ["../outside.csv", "/outside.csv", "a/b.csv", "a\\b.csv", ".."])
def test_filename_cannot_escape_granted_directory(selection, name):
    _, _, request = selection
    request["name"] = name
    with pytest.raises(desktop_file.WatchedFileError, match="filename"):
        desktop_file.read_watched_file(request)


def test_changed_file_identity_and_growth_are_refused(selection):
    folder, _, request = selection
    (folder / "statement.csv").write_bytes(INSIDE + b"GROWTH")
    with pytest.raises(desktop_file.WatchedFileError, match="file changed"):
        desktop_file.read_watched_file(request)


@pytest.mark.skipif(os.name != "nt", reason="Windows anchors by identity, not by descriptor")
def test_windows_refuses_a_directory_swapped_after_the_grant_check(selection):
    folder, outside, request = selection
    folder.rename(folder.with_name("original-folder"))
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(folder), str(outside)], check=True, capture_output=True
    )
    with pytest.raises(OSError):
        desktop_file.read_watched_file(request)


def test_cli_uses_private_pipe_and_bounded_header_then_bytes(selection):
    _, _, request = selection
    process = subprocess.Popen(
        [sys.executable, "-m", "backend.cli.desktop_file"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        header = json.loads(process.stdout.readline())
        assert header == {"type": "file", "size": len(INSIDE), "name": "statement.csv"}
        assert process.stdout.read(header["size"]) == INSIDE
        assert process.wait(timeout=5) == 0
        assert process.stderr.read() == b""
    finally:
        process.kill() if process.poll() is None else None
        process.wait(timeout=5)


# The host ends the helper's stdin only once it has stopped waiting, so the watcher's job is that
# the helper never outlives its host. Which thread sees the closed pipe first is the scheduler's
# call (main CI, 2026-11-02, the reader won), so each ordering is forced instead of raced.
_READ_NEVER_RETURNS = (
    "import sys, threading\n"
    "from backend.cli import desktop_file as helper\n"
    "helper.read_watched_file = lambda request: threading.Event().wait()\n"
    "sys.exit(helper.main())\n"
)
_WATCHER_NEVER_RUNS = (
    "import sys, threading\n"
    "from backend.cli import desktop_file as helper\n"
    "helper._exit_when_host_closes = lambda: threading.Event().wait()\n"
    "sys.exit(helper.main())\n"
)


def _run_after_host_leaves(code: str, request: dict) -> tuple[int, bytes]:
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.close()
        return process.wait(timeout=10), process.stdout.read()
    finally:
        process.kill() if process.poll() is None else None
        process.wait(timeout=5)


def test_cli_exits_when_host_pipe_closes_without_emitting_file(selection):
    _, _, request = selection
    assert _run_after_host_leaves(_READ_NEVER_RETURNS, request) == (1, b"")


def test_a_read_that_beats_the_watcher_emits_the_whole_file_and_exits(selection):
    _, _, request = selection
    code, output = _run_after_host_leaves(_WATCHER_NEVER_RUNS, request)
    header = json.dumps({"type": "file", "size": len(INSIDE), "name": "statement.csv"})
    assert (code, output) == (0, header.encode() + b"\n" + INSIDE)


@pytest.mark.parametrize("size", [0, desktop_file.MAX_FILE_BYTES + 1])
def test_file_size_is_bounded_before_read(selection, size):
    _, _, request = selection
    request["file"]["size"] = size
    with pytest.raises(desktop_file.WatchedFileError, match="smaller than 25 MB"):
        desktop_file.read_watched_file(request)


def test_helper_starts_without_third_party_packages(selection):
    """The host may spawn the helper with a bare interpreter: ``-S`` drops site-packages, so
    importing ``backend.core`` (settings, dotenv, data folders) would fail here."""
    _, _, request = selection
    process = subprocess.Popen(
        [sys.executable, "-S", "-m", "backend.cli.desktop_file"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()  # stdin stays open: the helper exits as soon as its host hangs up
        header = json.loads(process.stdout.readline())
        assert header["size"] == len(INSIDE)
        assert process.stdout.read(header["size"]) == INSIDE
        assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        process.kill() if process.poll() is None else None
        process.wait(timeout=5)
