---
title: The desktop runtime ships pure-Python packages as one archive
status: Accepted
date: 2026-11-02
deciders: repo owner
---

# 2026-11-02. The desktop runtime ships pure-Python packages as one archive

## Context

After **Restart to Update** the app quits and Squirrel.Mac's installer (ShipIt) verifies the
staged bundle's signature, moves it into place and relaunches. ShipIt's own log
(`~/Library/Caches/app.opennetworth.desktop.ShipIt/ShipIt_stderr.log`) shows a normal install is
about 12 s, but on a busy, disk-bound machine one took 1.5 to 4 minutes: "Beginning installation"
17:35:11, "Moving bundle" 17:38:02 (signature verification of every file), launched 17:39:24.
Its cost is per file, and the bundle held 15,428 files / 702 MB, of which 15,158 were the bundled
CPython runtime (`openai` 1,889, `anthropic` 1,864, `babel` 1,113, `pandas` 644, `tzdata` 627,
`pytz` 615, `numpy` 498, `openpyxl` 365 ...; 2,119 `__pycache__` files). Windows' Squirrel and the
Linux packages unpack the same file set. `codesign --verify --deep --strict` of the installed app
takes about 5 s when the disk cache is warm; the minutes are the cold, contended per-file work.

## Decision

- **One archive for packages that are safe in one.** `scripts/build_desktop_runtime.py` lists
  them in `ZIPPED_PACKAGES` and `scripts/runtime_zip.py` packs them into
  `site-packages/onw-packages.zip`, put on `sys.path` by `onw-packages.pth` (a relative entry,
  resolved against the folder holding the `.pth`, so it survives relocation and works the same on
  macOS, Windows and Linux). No launcher, environment variable or `runtime.ts` change: the backend
  is still `python -m backend.cli.desktop`, and any child that runs the runtime's interpreter
  sees the archive too.
- **Source AND bytecode go in.** The signed app is read-only to Python, so nothing may depend on
  a `__pycache__` being written. Each module is stored as its `.py` plus an unchecked-hash `.pyc`
  compiled by an interpreter of the runtime's minor version (the runtime's own interpreter, or a
  host 3.12 when cross-building the Windows runtime). zipimport prefers the `.pyc` and, being
  hash-based and unchecked, never compares it with a zip timestamp. The source stays for
  `inspect`/tracebacks. Entries are timestamped 1980-01-01 and sorted: the archive is reproducible.
  Folders get explicit entries: zipimport finds a folder with no `__init__.py` (for example
  `anthropic/_decoders`) only through its own entry.
- **The criterion is "no compiled module, no file read beside `__file__`".** zipimport serves
  imports, `importlib.resources` and `loader.get_data`, not `open(dirname(__file__) + ...)`. The
  list is an explicit allowlist, not a scan: a new dependency stays plain files until someone
  checks it. `runtime_zip.py` refuses a package holding a `.so`/`.pyd`/`.dylib`, a symlink or a
  stray `.pyc`; the build fails on a listed package that is missing or still loose; the packaged
  import smoke imports every listed package and asserts it came from the archive.
- **`tzdata` is zipped, `pytz` is not.** `zoneinfo` reads `tzdata` through
  `importlib.resources`, which zipimport serves (verified with `zoneinfo.reset_tzpath([])`, so no
  system tz database could answer, including `available_timezones()`); Windows has no system
  database, so `tzdata` must work from the archive. `pytz` opens its zone files by path from
  `__file__` and falls back to `pkg_resources`, which is not installed, so it stays files. It is
  required by `pandas` and `dateparser`; dropping either tz library is out of scope.
- **Pruned as well:** build-time files wheels carry that the import system never reads
  (`.pyi`, `py.typed`, `.h`, `.hpp`, `.c`, `.cpp`, `.pyx`, `.pxd`, `.pxi`, `.a`), `python/include`,
  the console scripts in `python/bin` (their shebangs name the build machine; only the
  interpreter links stay), `RECORD`/`INSTALLER`/`REQUESTED`/`direct_url.json` in every
  `dist-info`, and `WHEEL` once the cross-build tag check has read it. `METADATA`,
  `entry_points.txt` and licence files stay (`importlib.metadata`, redistribution terms). On
  Windows the stdlib pruning that already applied on POSIX now also applies to `python/Lib`
  (`idlelib`, `lib2to3`, `tkinter`, `test`, ...) and to `python/tcl`; its folder layout had
  hidden it from the old glob.

## What stays plain files, and why

- **Everything compiled** (`numpy`, `pandas`, `pydantic_core`, `lxml`, `cryptography`, `PIL`,
  `pymupdf`, `sqlalchemy`'s extensions, `uvloop` ...): zipimport cannot load a shared library.
- **`backend`** (2,298 files, the largest remaining group): it is code AND data. The bundled
  frontend/docs (`_bundled`, served by `StaticFiles`), the Alembic scripts and templates
  (`ScriptDirectory` lists a directory), and prompts are opened by path. A package cannot be half
  in an archive, so it stays whole. Splitting data out of the package is a separate refactor.
- **`babel`** (1,100 files): `localedata` finds `locale-data/*.dat` with `os.path`/`os.listdir`.
  Its only consumer, `courlan`, parses an arbitrary language code from a URL, so no locale
  can be called unused.
- **`pytz`, `dateutil`, `certifi`, `alembic`, `justext`, `jsonschema(_specifications)`,
  `trafilatura`, `tld`, `opentelemetry`** (data files, `pkg_resources`, or entry-point loading
  that was not worth proving for a few dozen files).
- **`dist-info` folders**: `importlib.metadata` reads them as directories.
- **The pre-compiled `.pyc` beside the files that stay**: the build's import smoke writes them
  (`-I` ignores `PYTHONDONTWRITEBYTECODE`), so a first launch does not compile ~1,000 modules or
  write into the signed bundle.

## Result (macOS arm64, unsigned/ad-hoc build)

| | before | after |
|---|---|---|
| runtime files | 15,162 | 7,514 |
| app files (`find -type f`) | ~15,430 | 8,082 |
| runtime / app bytes | 388 MB / ~702 MB | 360 MB / 669 MB |

Windows runtime (cross-built, not executed here): 6,224 files.

## Consequences

- A package's files no longer exist on disk: tools that browse the installed runtime see
  `onw-packages.zip`. Support steps should unzip it rather than expect source files.
- A dependency bump that makes a listed package ship a compiled module or a data file fails the
  build (`runtime_zip.py`) instead of misbehaving at run time; the fix is to remove it from the
  list.
- Follow-ups deliberately left for the owner: splitting `backend`'s data from its code, replacing
  `courlan`'s `babel` use, and a measured ShipIt install on a loaded machine from a signed,
  notarized build (only an unsigned build was available when this was decided).
