"""The desktop runtime ships pure-Python packages as one archive (ADR 2026-11-02) and prunes build-only files."""

import subprocess
import sys

import pytest

from scripts.build_desktop_runtime import ZIPPED_PACKAGES, prune_runtime
from scripts.runtime_zip import zip_packages


def test_prune_drops_headers_stubs_scripts_and_install_bookkeeping(tmp_path):
    site = tmp_path / "python" / "lib" / "python3.12" / "site-packages"
    package = site / "pkg"
    package.mkdir(parents=True)
    for name in ("__init__.py", "__init__.pyi", "py.typed", "api.h", "fast.pyx", "fast.pxd"):
        (package / name).write_text("", encoding="utf-8")
    dist = site / "pkg-1.0.dist-info"
    dist.mkdir()
    for name in ("METADATA", "RECORD", "INSTALLER", "REQUESTED", "direct_url.json", "LICENSE"):
        (dist / name).write_text("", encoding="utf-8")
    (tmp_path / "python" / "include" / "python3.12").mkdir(parents=True)
    bin_dir = tmp_path / "python" / "bin"
    bin_dir.mkdir()
    for name in ("python3.12", "pip3", "mako-render", "python3.12-config"):
        (bin_dir / name).write_text("", encoding="utf-8")
    bin_dir.joinpath("python3").symlink_to("python3.12")
    prune_runtime(tmp_path)
    assert sorted(p.name for p in package.iterdir()) == ["__init__.py"]
    assert sorted(p.name for p in dist.iterdir()) == ["LICENSE", "METADATA"]
    assert not (tmp_path / "python" / "include").exists()
    assert sorted(p.name for p in bin_dir.iterdir()) == ["python3", "python3.12"]


def test_prune_removes_the_windows_stdlib_extras_and_tcl_data(tmp_path):
    lib = tmp_path / "python" / "Lib"
    for name in ("idlelib", "lib2to3", "tkinter"):
        (lib / name).mkdir(parents=True)
    (lib / "os.py").write_text("", encoding="utf-8")
    (tmp_path / "python" / "tcl" / "tcl8.6").mkdir(parents=True)
    prune_runtime(tmp_path)
    assert (lib / "os.py").exists()
    assert not any((lib / name).exists() for name in ("idlelib", "lib2to3", "tkinter"))
    assert not (tmp_path / "python" / "tcl").exists()


def _site_with_packages(root):
    site = root / "site-packages"
    (site / "purepkg" / "inner").mkdir(parents=True)
    (site / "purepkg" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (site / "purepkg" / "inner" / "mod.py").write_text("NAME = 'inner'\n", encoding="utf-8")
    (site / "nsdata" / "zones").mkdir(
        parents=True
    )  # a folder with no __init__.py, like anthropic/_decoders
    (site / "nsdata" / "__init__.py").write_text("", encoding="utf-8")
    (site / "nsdata" / "zones" / "mod.py").write_text("OK = True\n", encoding="utf-8")
    (site / "nsdata" / "data.txt").write_text("hello", encoding="utf-8")
    (site / "single.py").write_text("ONE = 1\n", encoding="utf-8")
    (site / "purepkg" / "__pycache__").mkdir()
    (site / "purepkg" / "__pycache__" / "stale.cpython-312.pyc").write_bytes(b"x")
    return site


def test_zipped_packages_import_from_the_archive_alone(tmp_path):
    site = _site_with_packages(tmp_path)
    archive = site / "onw-packages.zip"
    zip_packages(site, archive, ["purepkg", "nsdata", "single"])
    assert [p.name for p in site.iterdir()] == ["onw-packages.zip"]
    script = (
        f"import sys; sys.path.append({str(archive)!r})\n"
        "import importlib.resources as r, purepkg, single, nsdata.zones.mod as z, purepkg.inner.mod as m\n"
        "assert purepkg.VALUE == 7 and single.ONE == 1 and z.OK and m.NAME == 'inner'\n"
        "assert 'onw-packages.zip' in purepkg.__file__\n"
        "assert r.files('nsdata').joinpath('data.txt').read_text() == 'hello'\n"
        "import zipfile; names = zipfile.ZipFile(purepkg.__loader__.archive).namelist()\n"
        "assert 'purepkg/__init__.pyc' in names and not any('__pycache__' in n for n in names)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_the_archive_is_reproducible(tmp_path):
    first = _site_with_packages(tmp_path / "one")
    second = _site_with_packages(tmp_path / "two")
    zip_packages(first, first / "a.zip", ["purepkg"])
    zip_packages(second, second / "a.zip", ["purepkg"])
    assert (first / "a.zip").read_bytes() == (second / "a.zip").read_bytes()


@pytest.mark.parametrize("filename", ["_speedups.so", "_native.pyd", "libx.dylib"])
def test_a_package_with_a_compiled_module_cannot_be_zipped(tmp_path, filename):
    site = tmp_path / "site-packages"
    (site / "native").mkdir(parents=True)
    (site / "native" / "__init__.py").write_text("", encoding="utf-8")
    (site / "native" / filename).write_bytes(b"\x00")
    with pytest.raises(ValueError, match="plain file"):
        zip_packages(site, tmp_path / "out.zip", ["native"])
    assert (site / "native").exists()


def test_a_missing_package_fails_the_build_instead_of_shipping_loose(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    with pytest.raises(ValueError, match="not found"):
        zip_packages(site, tmp_path / "out.zip", ["ghost"])


def test_the_zip_list_is_unique_and_keeps_the_packages_that_read_files_beside_themselves():
    assert len(set(ZIPPED_PACKAGES)) == len(ZIPPED_PACKAGES)
    # open() on a path next to __file__ cannot be served by zipimport: these stay plain files.
    assert not {
        "babel",
        "pytz",
        "certifi",
        "alembic",
        "dateutil",
        "backend",
        "pandas",
        "sqlalchemy",
    } & set(ZIPPED_PACKAGES)
