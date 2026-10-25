"""scripts/release_notes.py — a release's "what changed" notes from its commit subjects."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / "scripts" / "release_notes.py"
)
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)


def test_groups_features_fixes_and_polish_in_that_order():
    notes = rn.render(
        [
            "fix(intel): close Watchlists & monitors to /intel, not history.back()",
            "style(ui): switches turn blue when on",
            "feat(memory): edit a remembered fact in place",
            "copy(accounts): drop the Accounts overview subtitle",
        ]
    )
    assert notes == (
        "## New\n\n- **Memory:** Edit a remembered fact in place\n\n"
        "## Fixes\n\n- **Intel:** Close Watchlists & monitors to /intel, not history.back()\n\n"
        "## Polish\n\n- **UI:** Switches turn blue when on\n"
        "- **Accounts:** Drop the Accounts overview subtitle"
    )


def test_internal_commits_are_left_out():
    notes = rn.render(
        [
            "docs(adr): memory keeps only what has no other home",
            "ci(release): smoke the macOS app by its new executable name",
            "refactor(ledger): split the resolver",
            "Merge pull request #12 from x/y",
            'Revert "feat(ui): something"',
            "fix: an unscoped fix",
        ]
    )
    assert notes == "## Fixes\n\n- An unscoped fix"


def test_nothing_user_facing_renders_empty():
    assert rn.render(["docs: tidy", "test: cover a branch", ""]) == ""


def test_a_repeated_subject_is_listed_once():
    notes = rn.render(["fix(ui): one thing", "fix(ui): one thing"])
    assert notes.count("One thing") == 1
