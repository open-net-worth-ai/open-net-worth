"""scripts/release_notes.py — a release's "what changed" notes from its commit subjects."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


NOTES = "## New\n\n- **Memory:** Edit a remembered fact in place"


def _fake_anthropic(monkeypatch, response=None, error=None):
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        if error:
            raise error
        return response

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **_: client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return seen


def _answer(text, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


def test_highlights_go_on_top_of_the_full_list(monkeypatch):
    seen = _fake_anthropic(
        monkeypatch, _answer('{"highlights": ["Edit what Lucius remembers about you"]}')
    )
    assert rn.with_highlights(NOTES) == (
        "## Highlights\n\n- Edit what Lucius remembers about you\n\n" + NOTES
    )
    assert seen["model"] == "claude-sonnet-5-5"
    assert seen["messages"] == [{"role": "user", "content": NOTES}]
    assert seen["output_config"]["format"]["type"] == "json_schema"


def test_no_key_an_error_a_refusal_or_prose_leaves_the_notes_alone(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert rn.with_highlights(NOTES) == NOTES
    _fake_anthropic(monkeypatch, error=RuntimeError("boom"))
    assert rn.with_highlights(NOTES) == NOTES
    _fake_anthropic(monkeypatch, _answer('{"highlights": ["fine"]}', stop_reason="refusal"))
    assert rn.with_highlights(NOTES) == NOTES
    _fake_anthropic(monkeypatch, _answer("Sure! Here is what changed."))
    assert rn.with_highlights(NOTES) == NOTES


def test_only_a_usable_list_is_accepted():
    assert rn.parse_highlights('{"highlights": ["One", "- Two"]}') == ["One", "Two"]
    many = json.dumps({"highlights": [f"Item {n}" for n in range(rn.MAX_HIGHLIGHTS + 3)]})
    assert len(rn.parse_highlights(many)) == rn.MAX_HIGHLIGHTS
    assert rn.parse_highlights("- One\n- Two") is None
    assert rn.parse_highlights('{"highlights": []}') is None
    assert rn.parse_highlights('{"highlights": [1, 2]}') is None
    assert rn.parse_highlights(json.dumps({"highlights": ["x" * 201]})) is None


def test_without_the_flag_the_model_is_never_called(monkeypatch, capsys):
    _fake_anthropic(monkeypatch, error=AssertionError("called"))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("feat(memory): edit a fact\n"))
    assert rn.main([]) == 0
    assert "Highlights" not in capsys.readouterr().out


def test_nothing_user_facing_never_asks_for_highlights(monkeypatch, capsys):
    _fake_anthropic(monkeypatch, error=AssertionError("called"))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("docs: tidy\n"))
    assert rn.main(["--highlights"]) == 0
    assert capsys.readouterr().out == ""
