"""Security-gate fix wave (plan 3 task 9) — money-bundle import hardening.

FR-C1 (CRITICAL): an untrusted ``statements.json`` row's ``id`` is verbatim bundle JSON that
becomes a filesystem path component (``FILES_DIR/statements/<id>.parsed.json`` — 2026-08-22
consolidate-derived-data-storage RFC; this used to be ``DATA_DIR/statements_parsed/<id>.json``).
The review proved end-to-end that a bundle whose id was ``"../auth"`` rewrote ``auth.json``
(inbound-MCP Bearer takeover) and an absolute id escaped ``DATA_DIR`` entirely. The write must
be REFUSED, with ``auth.json`` byte- AND mode-preserved, and a malicious bundle rejected at
inspect time.

FR-M7 (Minor): an untrusted bundle amount ``"1e400"`` -> ``inf`` / ``"NaN"`` -> ``NaN`` / a raw
non-numeric string must be rejected on import, never survive verbatim into ``payload``.

Every test runs in an ISOLATED tmp DATA_DIR / STATEMENTS_FILES_DIR (monkeypatched) plus an
isolated tmp database (``db_isolated``) — the live ``~/.open-net-worth`` tree is never touched.
"""

import io
import json
import os
import stat
import zipfile
from hashlib import sha256

import pytest

from backend.core import paths as core_paths
from backend.core.errors import ValidationError
from backend.db import money_io
from backend.db.engine import unit_of_work
from backend.services import portability as svc


@pytest.fixture
def isolated_parsed_dir(tmp_path, monkeypatch):
    """Repoint ``core.paths.STATEMENTS_FILES_DIR`` at a throwaway tree (the ONE binding
    every consumer now reads through ``core.paths.statements_files_dir()`` — security review
    fix round 2, Fix 3) and seed TWO canaries, mode 0600:

      - ``data_dir/auth.json`` — a real, sensitive file the exploit must never touch
        regardless of exactly where an escaped path would land (the id gate
        (``STATEMENT_ID_RE.fullmatch``) refuses ``"../auth"`` before ANY path is built, so
        this is a belt-and-suspenders proof, not a test of the escaped path's literal
        resolution).
      - ``data_dir/files/auth.parsed.json`` — fix round 2, Fix 4: the id gate's actual
        current escape target. ``parsed_dir`` is ``data_dir/files/statements`` in THIS
        layout, so ``id="../auth"`` resolves to ``parsed_dir/../auth.parsed.json`` ==
        ``data_dir/files/auth.parsed.json`` — NOT ``data_dir/auth.json``. Before this fixture
        seeded this second canary, the test's byte/mode-preservation assertions on
        ``data_dir/auth.json`` passed UNCONDITIONALLY regardless of whether the id-gate
        (layer 1) even ran, because an escape landing at the REAL current target would never
        touch the OLD canary's path either way — the assertions could not tell "the escape
        was refused" apart from "the escape succeeded, just not at the path we happened to
        be watching". Asserting this second canary is never CREATED closes that gap: it is
        the one file an actual (hypothetical, layer-1-bypassing) escape would write to.

    Returns ``(data_dir, parsed_dir, auth_path, escape_target_path)``."""
    data_dir = tmp_path / "data"
    parsed_dir = data_dir / "files" / "statements"
    parsed_dir.mkdir(parents=True)
    monkeypatch.setattr(core_paths, "STATEMENTS_FILES_DIR", parsed_dir)
    auth = data_dir / "auth.json"
    auth.write_text(
        json.dumps(
            {"api_token": "REAL-TOKEN", "session_signing_key": "REAL", "auth_disabled": False}
        )
    )
    os.chmod(auth, 0o600)
    escape_target = data_dir / "files" / "auth.parsed.json"
    return data_dir, parsed_dir, auth, escape_target


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A minimal, well-formed bundle (correct per-entry sha256) — mirrors
    test_money_portability.py's own helper. The attacker controls the bytes AND the manifest,
    so the sha always matches; only the row-shape gate can catch a malicious entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries = {}
        for name, data in files.items():
            zf.writestr(name, data)
            entries[name] = {"sha256": sha256(data).hexdigest()}
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": entries}),
        )
    return buf.getvalue()


# ── FR-C1 layer 1 — the hard stop in money_io._statement_row ─────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_traversal_statement_id_refused_auth_untouched(isolated_parsed_dir, db_isolated):
    """The reviewer's primary exploit: id ``"../auth"`` with ``parsed_rows`` -> rewrite
    auth.json. import_statements must REFUSE; auth.json stays byte-identical AND mode 0600;
    nothing is written into the parsed dir; AND — fix round 2, Fix 4 — the id gate's actual
    CURRENT escape target (``data_dir/files/auth.parsed.json``, not ``data_dir/auth.json``
    given today's directory nesting) is never created either. This second assertion is what
    makes the test detect an escape actually SUCCEEDING, rather than only detecting "some
    ValidationError was raised, from either the id-gate or the resolved-containment layer" —
    see the fixture's own docstring for why the old assertions alone could not tell those
    apart."""
    _data_dir, parsed_dir, auth, escape_target = isolated_parsed_dir
    before_bytes = auth.read_bytes()
    before_mode = stat.S_IMODE(auth.stat().st_mode)
    assert before_mode == 0o600
    assert not escape_target.exists(), "test setup bug: the escape-target canary already exists"

    exploit = [{"id": "../auth", "parsed_rows": {"api_token": "PWNED", "auth_disabled": True}}]
    with unit_of_work() as conn:
        with pytest.raises(ValidationError):
            money_io.import_statements(conn, exploit)

    assert auth.read_bytes() == before_bytes, "auth.json content was modified"
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600, "auth.json mode was downgraded"
    assert list(parsed_dir.iterdir()) == [], "a sidecar was written despite the refusal"
    assert not escape_target.exists(), (
        "the id gate's ACTUAL current escape target was created — the exploit succeeded"
    )


def test_absolute_path_statement_id_refused(isolated_parsed_dir, db_isolated, tmp_path):
    """An absolute id escapes DATA_DIR entirely (pathlib: an absolute right operand REPLACES
    the left). import_statements must REFUSE, and no file may appear at that path."""
    _data_dir, _parsed_dir, _auth, _escape_target = isolated_parsed_dir
    outside = tmp_path / "outside" / "onw_abs_pwn"  # absolute, outside the data dir
    exploit = [{"id": str(outside), "parsed_rows": {"pwned": True}}]

    with unit_of_work() as conn:
        with pytest.raises(ValidationError):
            money_io.import_statements(conn, exploit)

    assert not (tmp_path / "outside" / "onw_abs_pwn.parsed.json").exists()


@pytest.mark.parametrize(
    "bad_id",
    ["../auth", "a/b", "with space", "x" * 65, "", "trailing\n", "semi;colon", "/abs"],
)
def test_unsafe_statement_ids_rejected_before_any_write(bad_id, isolated_parsed_dir, db_isolated):
    """The id gate is airtight across the shape of unsafe inputs — traversal, embedded slash,
    absolute, whitespace, oversize (>64), empty, and control/punctuation ids all refuse and
    leave the parsed dir empty. (A bare ``".."`` is deliberately NOT here: it is a safe,
    CONTAINED filename body — it would write ``...json`` INSIDE the dir, never escape it.)"""
    _data_dir, parsed_dir, _auth, escape_target = isolated_parsed_dir
    row = [{"id": bad_id, "parsed_rows": {"x": 1}}]
    with unit_of_work() as conn:
        with pytest.raises(ValidationError):
            money_io.import_statements(conn, row)
    assert list(parsed_dir.iterdir()) == []
    assert not escape_target.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_legit_statement_id_round_trips(isolated_parsed_dir, db_isolated):
    """A legit id still imports and round-trips (parsed_rows re-inlined verbatim), and its
    sidecar lands mode 0600 (FR-I3)."""
    _data_dir, parsed_dir, _auth, _escape_target = isolated_parsed_dir
    row = {"id": "stmt_abc123", "filename": "x.pdf", "parsed_rows": {"rows": [{"a": 1}]}}
    with unit_of_work() as conn:
        n = money_io.import_statements(conn, [row])
        assert n == 1
        exported = money_io.export_statements(conn)

    assert exported == [row]
    sidecar = parsed_dir / "stmt_abc123.parsed.json"
    assert sidecar.exists()
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


# ── FR-C1 layer 2 — inspect-time rejection (read-only; writes nothing) ───────────────


def test_inspect_bundle_rejects_traversal_statement_id():
    """A malicious money bundle is rejected at inspect time — before apply ever writes a row.
    inspect_bundle writes NOTHING, so this needs no isolated dirs."""
    bundle = _build_bundle(
        {"statements.json": json.dumps([{"id": "../auth", "parsed_rows": {"x": 1}}]).encode()}
    )
    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("statement id" in e and "../auth" in e for e in report["errors"]), report["errors"]


def test_inspect_bundle_rejects_non_list_money_file():
    """A money file that isn't a JSON list of objects is rejected at inspect time too."""
    bundle = _build_bundle({"accounts.json": json.dumps({"not": "a list"}).encode()})
    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("accounts.json" in e for e in report["errors"]), report["errors"]


def test_inspect_bundle_accepts_legit_money_files():
    """A well-formed money bundle still passes the new shape gate (no false positives)."""
    bundle = _build_bundle(
        {"statements.json": json.dumps([{"id": "stmt_ok_1", "parsed_rows": None}]).encode()}
    )
    report = svc.inspect_bundle(bundle)
    # sha/JSON/shape all fine — the only reason ok could be False is a schema mismatch, which
    # _build_bundle sets correctly. Assert no statement-id error slipped in.
    assert not any("statement id" in e for e in report["errors"]), report["errors"]


# ── FR-M7 — reject non-finite / non-numeric money on import ──────────────────────────


@pytest.mark.parametrize("bad", ["1e400", "NaN", "abc"])
def test_non_finite_transaction_amount_refused(bad, db_isolated):
    from tests.conftest import seed_stub_accounts

    row = {
        "id": "t_bad",
        "kind": "expense",
        "date": "2026-01-01",
        "from_account_id": "acc",
        "from_amount": bad,
    }
    seed_stub_accounts([row])
    with unit_of_work() as conn:
        with pytest.raises(ValidationError):
            money_io.import_transactions(conn, [row])


def test_non_finite_split_amount_refused(db_isolated):
    from tests.conftest import seed_stub_accounts

    row = {
        "id": "t_split",
        "kind": "expense",
        "date": "2026-01-01",
        "from_account_id": "acc",
        "from_amount": "10.0",
        "splits": [{"amount": "1e400", "category": None}],
    }
    seed_stub_accounts([row])
    with unit_of_work() as conn:
        with pytest.raises(ValidationError):
            money_io.import_transactions(conn, [row])


def test_normal_transaction_amount_imports(db_isolated):
    from tests.conftest import seed_stub_accounts

    row = {
        "id": "t_ok",
        "kind": "expense",
        "date": "2026-01-01",
        "from_account_id": "acc",
        "from_amount": "12.34",
    }
    seed_stub_accounts([row])
    with unit_of_work() as conn:
        n = money_io.import_transactions(conn, [row])
    assert n == 1
