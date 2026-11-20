"""C9 — the bundle carries every file tree (skills, attachments, receipts, statement sources,
generated documents), streams them, and restores them; old bundles without files still import."""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile

import pytest

from backend.core import portable
from backend.core.errors import ValidationError
from backend.services import portability as svc
from backend.services import portability_blobs as blobs


@pytest.fixture
def trees(db_isolated, tmp_path, monkeypatch):
    dirs = {
        name: tmp_path / "live" / name
        for name in ("skills", "attachments", "receipts", "statements", "artifacts")
    }
    for d in dirs.values():
        d.mkdir(parents=True)
    monkeypatch.setattr(portable, "portable_trees", lambda: dirs)
    return dirs


def _seed(trees):
    (trees["skills"] / "my-skill").mkdir()
    (trees["skills"] / "my-skill" / "SKILL.md").write_text("# skill")
    (trees["attachments"] / "att_1.json").write_text('{"x": 1}')
    (trees["receipts"] / "rcpt_1.bin").write_bytes(b"\x00\x01binary")
    (trees["statements"] / "stmt1.pdf").write_bytes(b"%PDF source")
    (trees["statements"] / "stmt1.parsed.json").write_text("[]")
    (trees["artifacts"] / "art_1.html").write_text("<p>doc</p>")


def _snapshot(trees):
    return {
        f"{n}/{p.relative_to(d).as_posix()}": p.read_bytes()
        for n, d in trees.items()
        for p in d.rglob("*")
        if p.is_file()
    }


def test_default_trees_cover_every_file_directory():
    assert set(portable.portable_trees()) >= {
        "skills",
        "attachments",
        "receipts",
        "statements",
        "artifacts",
    }


def test_round_trip_restores_every_file(trees):
    _seed(trees)
    before = {k: v for k, v in _snapshot(trees).items() if not k.endswith(".parsed.json")}

    bundle = svc.build_export_bundle()
    names = zipfile.ZipFile(io.BytesIO(bundle)).namelist()
    assert "blobs/skills/my-skill/SKILL.md" in names
    assert "blobs/statements/stmt1.pdf" in names
    assert not any(n.endswith(".parsed.json") for n in names)

    for d in trees.values():
        for p in sorted(d.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    result = svc.apply_import(bundle)

    after = {k: v for k, v in _snapshot(trees).items()}
    assert after == before
    assert result["blob_files"] == len(before)


def test_import_replaces_files_not_in_the_bundle_but_keeps_parsed_sidecars(trees):
    _seed(trees)
    bundle = svc.build_export_bundle()
    (trees["attachments"] / "stray.json").write_text("stale")
    (trees["statements"] / "stmt-new.parsed.json").write_text("[1]")

    svc.apply_import(bundle)

    assert not (trees["attachments"] / "stray.json").exists()
    assert (trees["statements"] / "stmt-new.parsed.json").exists()


def test_old_bundle_without_files_leaves_existing_files_alone(trees):
    _seed(trees)
    old = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(svc.build_export_bundle())) as src,
        zipfile.ZipFile(old, "w") as dst,
    ):
        manifest = json.loads(src.read("manifest.json"))
        for name in src.namelist():
            if name.startswith("blobs/") or name == "manifest.json":
                continue
            dst.writestr(name, src.read(name))
        manifest.pop("blob_trees")
        manifest["entries"] = {k: v for k, v in manifest["entries"].items() if v["kind"] != "blob"}
        dst.writestr("manifest.json", json.dumps(manifest))

    svc.apply_import(old.getvalue())

    assert (trees["attachments"] / "att_1.json").exists()


def _tamper(bundle: bytes, mutate) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bundle)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, mutate(name, data))
    return out.getvalue()


def test_corrupt_file_aborts_before_anything_changes(trees):
    _seed(trees)
    bundle = svc.build_export_bundle()
    bad = _tamper(bundle, lambda n, d: b"tampered" if n == "blobs/receipts/rcpt_1.bin" else d)
    (trees["attachments"] / "att_1.json").write_text("local edit")

    with pytest.raises(ValidationError):
        svc.apply_import(bad)

    assert (trees["attachments"] / "att_1.json").read_text() == "local edit"
    assert not [p for p in (trees["attachments"].parent).iterdir() if ".import-" in p.name]


def test_inspect_flags_corrupt_file(trees):
    _seed(trees)
    bad = _tamper(
        svc.build_export_bundle(), lambda n, d: b"x" if n == "blobs/skills/my-skill/SKILL.md" else d
    )
    report = svc.inspect_bundle(bad)
    assert not report["ok"]
    assert any("sha256 mismatch" in e for e in report["errors"])


@pytest.mark.parametrize(
    "name",
    [
        "blobs/skills/../../etc/passwd",
        "blobs/skills//x",
        "blobs/unknown-tree/x",
        "blobs/skills/a\\b",
        "blobs/skills",
    ],
)
def test_unsafe_blob_names_are_refused(trees, name):
    assert blobs.parse_blob_name(name, trees) is None
    bundle = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(svc.build_export_bundle())) as src,
        zipfile.ZipFile(bundle, "w") as dst,
    ):
        for n in src.namelist():
            dst.writestr(n, src.read(n))
        # ZipInfo's constructor rewrites os.sep to "/", which would launder "a\\b" into "a/b" on Windows.
        evil = zipfile.ZipInfo("evil")
        evil.filename = name
        dst.writestr(evil, b"evil")
    report = svc.inspect_bundle(bundle.getvalue())
    assert not report["ok"]


def test_symlinks_are_not_exported(trees, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (trees["receipts"] / "link.txt").symlink_to(secret)
    names = zipfile.ZipFile(io.BytesIO(svc.build_export_bundle())).namelist()
    assert "blobs/receipts/link.txt" not in names


def test_export_and_import_work_from_files_on_disk(trees, tmp_path):
    _seed(trees)
    path = svc.export_bundle_to_temp_file()
    try:
        assert svc.inspect_bundle(path)["ok"]
        with path.open("rb") as f:
            svc.apply_import(f)
    finally:
        path.unlink()
    assert (trees["artifacts"] / "art_1.html").read_text() == "<p>doc</p>"


def test_boot_sweeps_only_stale_export_temp_files(tmp_path, monkeypatch):
    import os

    from backend.core import paths

    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path)
    old, fresh, other = (
        tmp_path / "export-old.zip",
        tmp_path / "export-new.zip",
        tmp_path / "keep.zip",
    )
    for f in (old, fresh, other):
        f.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    os.utime(other, (two_hours_ago, two_hours_ago))

    assert svc.sweep_stale_exports() == 1
    assert not old.exists() and fresh.exists() and other.exists()


def test_import_never_writes_or_deletes_through_links_in_the_live_tree(trees, tmp_path):
    _seed(trees)
    bundle = svc.build_export_bundle()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("mine")
    (trees["attachments"] / "linked-dir").symlink_to(outside, target_is_directory=True)
    receipts = trees["receipts"]
    shutil.rmtree(receipts)
    receipts.symlink_to(outside, target_is_directory=True)

    svc.apply_import(bundle)

    assert (outside / "precious.txt").read_text() == "mine"
    assert not (outside / "rcpt_1.bin").exists()  # nothing was written through the old link
    assert not receipts.is_symlink() and (receipts / "rcpt_1.bin").read_bytes() == b"\x00\x01binary"
    assert not (trees["attachments"] / "linked-dir").exists()
