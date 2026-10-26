"""scripts/release_announce.py — a release's Discord announcement from its notes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "release_announce", ROOT / "scripts" / "release_announce.py"
)
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)

NOTES = """## New

- **Accounts:** The sidebar says which accounts need reconciling
- **AI:** List_accounts reports each account's cleared balance

## Fixes

- **Import:** Card charges dated just before a statement's period are linkable
- **Chat:** Wide tables wrap to fit

## Polish

- **Chat:** Your own photos sit in the user bubble's lane

[Compare](https://example.test/compare/a...b)

Source: 0123456789abcdef
"""
RELEASE_URL = "https://example.test/releases/tag/v0.1.31"


def _write(tmp_path, text):
    path = tmp_path / "notes.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_the_sections_release_notes_writes():
    sections = ra.parse_sections(NOTES)
    assert len(sections["New"]) == 2
    assert len(sections["Fixes"]) == 2
    assert sections["Polish"] == ["**Chat:** Your own photos sit in the user bubble's lane"]


def test_fallback_lists_new_then_fixes_without_the_scope_label():
    sections = {"New": ["**Accounts:** Reconcile from the sidebar"], "Fixes": ["Unscoped"]}
    assert ra.bullets_for(sections) == ["Reconcile from the sidebar", "Unscoped"]


def test_fallback_stops_at_the_bullet_limit():
    sections = {"Fixes": [f"Fix {n}" for n in range(ra.MAX_BULLETS + 3)]}
    assert len(ra.bullets_for(sections)) == ra.MAX_BULLETS


def test_payload_names_the_version_and_counts_the_rest():
    sections = ra.parse_sections(NOTES)
    payload = ra.build_payload("0.1.31", RELEASE_URL, ["One", "Two"], sections)
    embed = payload["embeds"][0]
    assert embed["title"] == "Open Net Worth 0.1.31 is out"
    assert embed["url"] == RELEASE_URL
    assert embed["description"].startswith("- One\n- Two\n")
    assert "In all: 2 fixes and 1 polish change, most in Chat and Import." in embed["description"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_the_fixes_line_names_the_busiest_areas():
    sections = {
        "Fixes": ["**Import:** a", "**Import:** b", "**Chat:** c", "**UI:** d", "**Plan:** e"],
        "Polish": ["**UI:** f", "unscoped"],
    }
    assert ra.fixes_line(sections) == (
        "In all: 5 fixes and 2 polish changes, most in Import, UI and Chat."
    )
    assert ra.fixes_line({"Fixes": ["unscoped"]}) == "In all: 1 fix."
    assert ra.fixes_line({"New": ["x"]}) == ""


def test_a_fixes_only_release_is_announced(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/hook")
    sent = []
    monkeypatch.setattr(ra, "post", lambda hook, payload: sent.append(payload))
    notes = _write(tmp_path, "## Fixes\n\n- **Import:** Card charges link to their statement\n")
    assert ra.main([str(notes), "--version", "0.1.32", "--release-url", RELEASE_URL]) == 0
    (payload,) = sent
    assert payload["embeds"][0]["description"].startswith(
        "- Card charges link to their statement\n\nIn all: 1 fix, most in Import."
    )


def test_a_release_with_nothing_user_facing_posts_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(ra, "post", lambda *_: (_ for _ in ()).throw(AssertionError("posted")))
    notes = _write(tmp_path, "Open Net Worth 0.1.33 desktop app.\n\nSource: abc\n")
    assert ra.main([str(notes), "--version", "0.1.33", "--release-url", RELEASE_URL]) == 0
    assert "no user-facing changes" in capsys.readouterr().out


def test_without_a_key_it_posts_the_new_lines_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/hook")
    sent = []
    monkeypatch.setattr(ra, "post", lambda hook, payload: sent.append((hook, payload)))
    ra.main([str(_write(tmp_path, NOTES)), "--version", "0.1.31", "--release-url", RELEASE_URL])
    ((hook, payload),) = sent
    assert hook == "https://example.test/hook"
    assert payload["embeds"][0]["description"].startswith(
        "- The sidebar says which accounts need reconciling\n"
    )


def test_dry_run_prints_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(ra, "post", lambda *_: (_ for _ in ()).throw(AssertionError("posted")))
    args = [str(_write(tmp_path, NOTES)), "--version", "0.1.31", "--release-url", RELEASE_URL]
    ra.main([*args, "--dry-run"])
    assert json.loads(capsys.readouterr().out)["embeds"][0]["title"].endswith("is out")


def test_the_release_highlights_are_the_bullets():
    notes = "## Highlights\n\n- Reconcile from the sidebar\n- Imports link card charges\n\n" + NOTES
    sections = ra.parse_sections(notes)
    assert ra.bullets_for(sections) == ["Reconcile from the sidebar", "Imports link card charges"]


def test_highlights_alone_are_not_a_reason_to_post(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ra, "post", lambda *_: (_ for _ in ()).throw(AssertionError("posted")))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/hook")
    notes = _write(tmp_path, "## Highlights\n\n- Something\n")
    assert ra.main([str(notes), "--version", "0.1.34", "--release-url", RELEASE_URL]) == 0
    assert "no user-facing changes" in capsys.readouterr().out
