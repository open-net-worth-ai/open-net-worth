#!/usr/bin/env python3
"""Give this repo a release entry for each one published in the releases repo.

Everything is built and published in open-net-worth-releases, which left this repo's own Releases
box empty for anyone reading the code. An entry here carries the same notes, links to the downloads
and points at the commit it was built from. It holds no files and nothing reads it: updates, the
installers and the image all use the releases repo.

Tags carry no `v`. A `vX.Y.Z` tag here starts .github/workflows/release.yml, a second real release.

Usage: mirror_releases.py [--only vX.Y.Z] [--dry-run]   (needs `gh`, and GITHUB_REPOSITORY to write)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

RELEASES_REPO = "open-net-worth-ai/open-net-worth-releases"
THIS_REPO = "open-net-worth-ai/open-net-worth"
# Releases older than this repo's history name commits it doesn't have, and a tag needs a commit.
FIRST_COMMIT = "c3043fdc45ae2ca18fe9d964d9a1e4122f8382eb"
HISTORY_STARTS = "2026-10-15T14:09:38Z"
_PACE_SECONDS = 1.0

Release = dict[str, Any]
Gh = Callable[..., "subprocess.CompletedProcess[str]"]


class MirrorError(Exception):
    pass


def mirror_tag(tag: str) -> str:
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        raise MirrorError(f"{tag} is not a vX.Y.Z tag")
    return match.group(1)


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in mirror_tag(tag).split("."))


def source_commit(body: str | None) -> str | None:
    found = re.findall(r"^Source: ([0-9a-f]{40})\s*$", body or "", re.MULTILINE)
    return found[-1] if found else None


def choose_target(release: Release, commit_exists: Callable[[str], bool]) -> tuple[str, bool]:
    """The commit to tag, and whether it is exactly what this release was built from."""
    commit = source_commit(release.get("body"))
    if commit and commit_exists(commit):
        return commit, True
    if release["published_at"] < HISTORY_STARTS:
        return FIRST_COMMIT, False
    raise MirrorError(f"{release['tag_name']} names no commit in this repo: {commit}")


def render_notes(release: Release, exact: bool) -> str:
    version = mirror_tag(release["tag_name"])
    lines = [
        f"**Download {version} from the [releases page]({release['html_url']}).** "
        f"Published {release['published_at'][:10]}.",
    ]
    if not exact:
        lines.append(
            f"Version {version} is older than this repository's history, so the source archives "
            "here are the repository's first commit, not this version's code."
        )
    return "\n\n".join([*lines, (release.get("body") or "").strip()]).strip() + "\n"


def to_mirror(
    published: Iterable[Release], existing: set[str], only: str | None = None
) -> list[Release]:
    """Oldest first, so the entries are created in the order the versions came out."""
    wanted = [
        release
        for release in published
        if not release.get("draft")
        and not release.get("prerelease")
        and mirror_tag(release["tag_name"]) not in existing
        and only in (None, release["tag_name"])
    ]
    return sorted(wanted, key=lambda release: version_key(release["tag_name"]))


def newest_tag(published: Iterable[Release]) -> str:
    live = [r["tag_name"] for r in published if not r.get("draft") and not r.get("prerelease")]
    return max(live, key=version_key)


def _gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, check=False, timeout=120
    )


def _list_releases(repo: str, gh: Gh) -> list[Release]:
    result = gh("api", "--paginate", f"repos/{repo}/releases", "--jq", ".[]")
    if result.returncode != 0:
        raise MirrorError(f"can't list the releases of {repo}: {result.stderr.strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _create(release: Release, repo: str, target: str, exact: bool, latest: bool, gh: Gh) -> None:
    result = gh(
        "release",
        "create",
        mirror_tag(release["tag_name"]),
        "--repo",
        repo,
        "--title",
        release["name"],
        "--target",
        target,
        "--notes-file",
        "-",
        f"--latest={'true' if latest else 'false'}",
        stdin=render_notes(release, exact),
    )
    if result.returncode != 0:
        raise MirrorError(f"{release['tag_name']}: {result.stderr.strip()}")


def mirror(repo: str, only: str | None, dry_run: bool, gh: Gh = _gh) -> int:
    published = _list_releases(RELEASES_REPO, gh)
    existing = {release["tag_name"] for release in _list_releases(repo, gh)}
    newest = newest_tag(published)
    failures = 0

    def commit_exists(commit: str) -> bool:
        return gh("api", f"repos/{repo}/commits/{commit}", "--silent").returncode == 0

    for release in to_mirror(published, existing, only):
        tag = release["tag_name"]
        try:
            target, exact = choose_target(release, commit_exists)
            where = target[:8] if exact else f"{target[:8]} (first commit)"
            print(f"{mirror_tag(tag)} -> {where}{' latest' if tag == newest else ''}")
            if not dry_run:
                _create(release, repo, target, exact, tag == newest, gh)
                time.sleep(_PACE_SECONDS)
        except MirrorError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="mirror just this tag of the releases repo, e.g. v0.1.40")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo and not args.dry_run:
        parser.error("GITHUB_REPOSITORY is required to write")
    try:
        return mirror(repo or THIS_REPO, args.only or None, args.dry_run)
    except MirrorError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
