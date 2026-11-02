"""Pack pure-Python packages into one zip for the desktop runtime's sys.path.

Standalone and stdlib-only: the build runs it with an interpreter of the runtime's minor version,
because the bytecode it stores must match the one that will import it. The shipped app is signed and
read-only to Python, so nothing may rely on a __pycache__ being written beside the source: each
module is stored as its source AND an unchecked-hash .pyc (zipimport prefers the .pyc, never
compares it with a zip timestamp, so it is used as-is).
"""

from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

NATIVE_SUFFIXES = (".so", ".pyd", ".dylib", ".dll")
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)  # reproducible archives


def package_files(site: Path, name: str) -> list[Path]:
    """The files of one top-level package or module, refusing anything a zip cannot serve."""
    root = site / name
    module = site / f"{name}.py"
    if root.is_dir():
        files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    elif module.is_file():
        files = [module]
    else:
        raise ValueError(f"cannot zip {name}: not found in {site}")
    for path in files:
        if path.is_symlink() or path.suffix in NATIVE_SUFFIXES or path.suffix == ".pyc":
            raise ValueError(f"cannot zip {name}: {path.relative_to(site)} must stay a plain file")
    return files


def add_file(archive: zipfile.ZipFile, path: Path, arcname: str, scratch: Path) -> None:
    info = zipfile.ZipInfo(arcname, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, path.read_bytes())
    if path.suffix != ".py":
        return
    compiled = scratch / "module.pyc"
    py_compile.compile(
        str(path),
        cfile=str(compiled),
        dfile=arcname,
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    pyc = zipfile.ZipInfo(f"{arcname}c", ZIP_EPOCH)
    pyc.compress_type = zipfile.ZIP_DEFLATED
    pyc.external_attr = 0o644 << 16
    archive.writestr(pyc, compiled.read_bytes())


def add_directories(archive: zipfile.ZipFile, directories: set[str]) -> None:
    """zipimport sees a folder without an __init__.py (a namespace portion) only through its own entry."""
    for name in sorted(directories):
        info = zipfile.ZipInfo(f"{name}/", ZIP_EPOCH)
        info.external_attr = (0o755 << 16) | 0x10
        archive.writestr(info, b"")


def zip_packages(site: Path, archive_path: Path, packages: list[str]) -> int:
    """Move `packages` out of `site` into `archive_path`; returns how many files that removed."""
    removed = 0
    directories: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="onw-zip-") as scratch:
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name in sorted(packages):
                for path in package_files(site, name):
                    arcname = path.relative_to(site).as_posix()
                    directories.update(
                        parent.as_posix() for parent in Path(arcname).parents if parent.name
                    )
                    add_file(archive, path, arcname, Path(scratch))
                    removed += 1
            add_directories(archive, directories)
    for name in packages:
        if (site / name).is_dir():
            shutil.rmtree(site / name)
        else:
            (site / f"{name}.py").unlink()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("packages", nargs="+")
    args = parser.parse_args()
    print(
        f"zipped {zip_packages(args.site, args.archive, args.packages)} files with Python {sys.version.split()[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
