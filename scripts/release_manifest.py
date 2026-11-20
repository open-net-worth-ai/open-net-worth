"""Release manifest + checksums for one release's assets directory.

    python3 scripts/release_manifest.py generate --version 0.2.0 --commit <sha> \
        --image-digest sha256:<64 hex> --assets release/
    python3 scripts/release_manifest.py check release/

`generate` writes `manifest.json` (version, source commit, the image digest the portal rolls
agents to, every asset with its sha256, and the compatibility contracts) and then `SHA256SUMS`
over every file in the directory, the manifest included. Both are signed afterwards by
`release_sign.sh`. `check` re-derives the hashes from the files on disk and compares them with
both documents, so the release verify step catches a truncated or swapped asset.

The manifest schema (consumed by `onw-portal release-agents`) is in RELEASING.md.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _agent_contracts() -> dict[str, int]:
    """Load the contracts by path: version.py is stdlib-only, importing `backend.core` is not, and
    the release runner has no app dependencies installed."""
    spec = importlib.util.spec_from_file_location(
        "onw_version", ROOT / "backend" / "core" / "version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AGENT_CONTRACTS


AGENT_CONTRACTS = _agent_contracts()

IMAGE_REPO = "ghcr.io/open-net-worth-ai/open-net-worth"
HANDOFF_CONTRACT = 1  # desktop <-> portal sign-in hand-off (not an agent surface)
MANIFEST = "manifest.json"
SUMS = "SHA256SUMS"
SIGNATURE_SUFFIX = ".minisig"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

SERVER_ASSETS = ("install-server.sh", "docker-compose.yml")


def required_desktop_assets(version: str) -> tuple[str, ...]:
    return (
        "Open-Net-Worth-mac-arm64.dmg",
        f"Open-Net-Worth-darwin-arm64-{version}.zip",
        "Open-Net-Worth-windows-x64-setup.exe",
        "RELEASES",
        f"OpenNetWorth-{version}-full.nupkg",
        "Open-Net-Worth-linux-x86_64.AppImage",
        "Open-Net-Worth-linux-amd64.deb",
        "install.sh",
        "install.ps1",
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path) -> dict:
    return {"name": path.name, "sha256": sha256_of(path), "size": path.stat().st_size}


def _signed_inputs(directory: Path) -> list[Path]:
    """Every release file except the checksum list itself and signatures."""
    # By name: sorting Paths folds case on Windows, which would reorder the signed list per OS.
    return sorted(
        (
            p
            for p in directory.iterdir()
            if p.is_file() and p.name != SUMS and not p.name.endswith(SIGNATURE_SUFFIX)
        ),
        key=lambda p: p.name,
    )


def build_manifest(directory: Path, version: str, commit: str, image_digest: str) -> dict:
    if not SEMVER.match(version):
        raise SystemExit(f"version must be X.Y.Z, got {version!r}")
    if not COMMIT.match(commit):
        raise SystemExit(f"source commit must be a full 40-hex sha, got {commit!r}")
    if not DIGEST.match(image_digest):
        raise SystemExit(f"image digest must be sha256:<64 hex>, got {image_digest!r}")
    present = {p.name: p for p in directory.iterdir() if p.is_file()}
    missing = [n for n in (*required_desktop_assets(version), *SERVER_ASSETS) if n not in present]
    wheels = [n for n in present if n.endswith(".whl")]
    sdists = [n for n in present if n.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        missing.append("exactly one wheel and one sdist")
    elif not wheels[0].startswith(f"open_net_worth-{version}-"):
        raise SystemExit(f"wheel {wheels[0]} does not carry version {version}")
    if missing:
        raise SystemExit("release is missing: " + ", ".join(missing))
    desktop = required_desktop_assets(version)
    return {
        "version": version,
        "source_commit": commit,
        "image": {"repo": IMAGE_REPO, "digest": image_digest, "tags": [version]},
        "desktop": {"assets": [_entry(present[n]) for n in desktop]},
        "python": {"wheel": _entry(present[wheels[0]]), "sdist": _entry(present[sdists[0]])},
        "server": {"assets": [_entry(present[n]) for n in SERVER_ASSETS]},
        "contracts": {**AGENT_CONTRACTS, "handoff": HANDOFF_CONTRACT},
        "min_portal_contracts": {
            "gateway": AGENT_CONTRACTS["gateway"],
            "handoff": HANDOFF_CONTRACT,
        },
    }


def write_sums(directory: Path) -> str:
    text = "".join(f"{sha256_of(p)}  {p.name}\n" for p in _signed_inputs(directory))
    (directory / SUMS).write_text(text, newline="\n")
    return text


def generate(directory: Path, version: str, commit: str, image_digest: str) -> None:
    for stale in (MANIFEST, SUMS):
        (directory / stale).unlink(missing_ok=True)
    manifest = build_manifest(directory, version, commit, image_digest)
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    write_sums(directory)


def _manifest_entries(manifest: dict) -> list[dict]:
    return [
        *manifest["desktop"]["assets"],
        manifest["python"]["wheel"],
        manifest["python"]["sdist"],
        *manifest["server"]["assets"],
    ]


def check(directory: Path) -> list[str]:
    """Return every disagreement between the files on disk, the manifest and SHA256SUMS."""
    problems: list[str] = []
    manifest = json.loads((directory / MANIFEST).read_text())
    for entry in _manifest_entries(manifest):
        path = directory / entry["name"]
        if not path.is_file():
            problems.append(f"manifest lists {entry['name']} but it is not in the release")
        elif sha256_of(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
            problems.append(f"{entry['name']} does not match the manifest")
    listed: dict[str, str] = {}
    for line in (directory / SUMS).read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    for path in _signed_inputs(directory):
        if listed.get(path.name) != sha256_of(path):
            problems.append(f"{path.name} does not match {SUMS}")
    for name in listed:
        if not (directory / name).is_file():
            problems.append(f"{SUMS} lists {name} but it is not in the release")
    if not DIGEST.match(manifest["image"]["digest"]):
        problems.append("manifest image digest is malformed")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--version", required=True)
    gen.add_argument("--commit", required=True)
    gen.add_argument("--image-digest", required=True)
    gen.add_argument("--assets", required=True, type=Path)
    chk = sub.add_parser("check")
    chk.add_argument("assets", type=Path)
    args = parser.parse_args(argv)
    if args.command == "generate":
        generate(args.assets, args.version, args.commit, args.image_digest)
        sys.stdout.write(f"wrote {MANIFEST} and {SUMS} in {args.assets}\n")
        return 0
    problems = check(args.assets)
    for problem in problems:
        sys.stderr.write(f"ERROR: {problem}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
