"""scripts/mirror_releases.py: this repo's Releases box, filled without starting a real release."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mirror_releases", ROOT / "scripts" / "mirror_releases.py"
)
mirror_releases = importlib.util.module_from_spec(spec)
sys.modules["mirror_releases"] = mirror_releases
spec.loader.exec_module(mirror_releases)

SHA = "a" * 40


def release(
    tag="v9.9.9", published="2026-11-06T01:00:00Z", body=f"Notes\n\nSource: {SHA}", **extra
):
    return {
        "tag_name": tag,
        "name": f"Open Net Worth {tag[1:]} Beta",
        "html_url": f"https://github.com/synthetic/releases/tag/{tag}",
        "published_at": published,
        "body": body,
        **extra,
    }


class FakeGh:
    def __init__(self, published, existing=(), missing_commits=(), fail_create=()):
        self.published = published
        self.existing = existing
        self.missing_commits = missing_commits
        self.fail_create = fail_create
        self.created = []

    def __call__(self, *args, stdin=None):
        if args[0] == "release":
            self.created.append((args, stdin))
            code = 1 if args[2] in self.fail_create else 0
            return subprocess.CompletedProcess(args, code, "", "boom" if code else "")
        endpoint = next(arg for arg in args if arg.startswith("repos/"))
        if "/commits/" in endpoint:
            code = 1 if endpoint.rsplit("/", 1)[1] in self.missing_commits else 0
            return subprocess.CompletedProcess(args, code, "", "")
        rows = (
            self.published
            if endpoint.startswith(f"repos/{mirror_releases.RELEASES_REPO}")
            else [{"tag_name": tag} for tag in self.existing]
        )
        return subprocess.CompletedProcess(args, 0, "\n".join(json.dumps(r) for r in rows), "")


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(mirror_releases, "_PACE_SECONDS", 0)


def flag(args, name):
    return args[args.index(name) + 1]


def test_a_tag_here_can_never_start_the_real_release_workflow():
    assert mirror_releases.mirror_tag("v0.1.40") == "0.1.40"
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in workflow


@pytest.mark.parametrize("tag", ["0.1.40", "v1.2", "v1.2.3-rc1", "nightly"])
def test_an_unexpected_tag_is_refused_rather_than_guessed(tag):
    with pytest.raises(mirror_releases.MirrorError):
        mirror_releases.mirror_tag(tag)


def test_the_newest_is_created_last_and_is_the_only_one_marked_latest():
    gh = FakeGh([release("v0.1.10"), release("v0.1.9")])

    assert mirror_releases.mirror("synthetic/repo", None, False, gh) == 0

    assert [args[2] for args, _ in gh.created] == ["0.1.9", "0.1.10"]
    assert "--latest=false" in gh.created[0][0]
    assert "--latest=true" in gh.created[1][0]


def test_only_a_missing_entry_is_created():
    gh = FakeGh([release("v0.1.10"), release("v0.1.9")], existing=["0.1.10"])

    mirror_releases.mirror("synthetic/repo", None, False, gh)

    assert [args[2] for args, _ in gh.created] == ["0.1.9"]
    assert "--latest=false" in gh.created[0][0]  # an older backfill must not take over the badge


def test_drafts_and_prereleases_stay_out():
    gh = FakeGh(
        [release("v0.1.3", draft=True), release("v0.1.2", prerelease=True), release("v0.1.1")]
    )

    mirror_releases.mirror("synthetic/repo", None, False, gh)

    assert [args[2] for args, _ in gh.created] == ["0.1.1"]
    assert "--latest=true" in gh.created[0][0]


def test_only_limits_the_run_to_one_release_and_keeps_its_latest_badge():
    gh = FakeGh([release("v0.1.10"), release("v0.1.9")])

    mirror_releases.mirror("synthetic/repo", "v0.1.10", False, gh)

    assert [args[2] for args, _ in gh.created] == ["0.1.10"]
    assert "--latest=true" in gh.created[0][0]


def test_an_entry_points_at_the_commit_it_was_built_from_and_links_the_downloads():
    gh = FakeGh([release()])

    mirror_releases.mirror("synthetic/repo", None, False, gh)

    args, notes = gh.created[0]
    assert flag(args, "--target") == SHA
    assert flag(args, "--title") == "Open Net Worth 9.9.9 Beta"
    assert "[releases page](https://github.com/synthetic/releases/tag/v9.9.9)" in notes
    assert "Published 2026-11-06" in notes
    assert "Notes" in notes
    assert "first commit" not in notes


@pytest.mark.parametrize("body", [f"Notes\n\nSource: {SHA}", "Notes with no source line", None])
def test_a_release_older_than_the_history_says_its_source_is_not_that_version(body):
    gh = FakeGh(
        [release(published="2026-10-02T16:32:43Z", body=body)],
        missing_commits=[SHA],
    )

    assert mirror_releases.mirror("synthetic/repo", None, False, gh) == 0

    args, notes = gh.created[0]
    assert flag(args, "--target") == mirror_releases.FIRST_COMMIT
    assert "not this version's code" in notes


def test_a_newer_release_with_no_usable_commit_fails_instead_of_being_mislabelled():
    gh = FakeGh(
        [release("v0.1.41", body="Notes"), release("v0.1.40")],
    )

    assert mirror_releases.mirror("synthetic/repo", None, False, gh) == 1

    assert [args[2] for args, _ in gh.created] == ["0.1.40"]  # the rest still go through


def test_a_failed_create_is_reported_and_the_rest_still_run():
    gh = FakeGh([release("v0.1.2"), release("v0.1.1")], fail_create=["0.1.1"])

    assert mirror_releases.mirror("synthetic/repo", None, False, gh) == 1

    assert [args[2] for args, _ in gh.created] == ["0.1.1", "0.1.2"]


def test_a_dry_run_writes_nothing():
    gh = FakeGh([release()])

    assert mirror_releases.mirror("synthetic/repo", None, True, gh) == 0

    assert gh.created == []
