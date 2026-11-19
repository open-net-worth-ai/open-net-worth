"""The shared ``book-transactions`` skill (2026-11-13): the bank-sync templates and the
email-logging skill book through it, so categories and payee hygiene are written once.

- every bank-sync template names it, and it ships;
- an install still holding the email skill's previous, unedited body gets the new one, which
  points at the shared rules — an edited copy is left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.db import skill_store
from backend.db.skill_store import STARTER_SKILLS, SkillStore, parse_skill_md

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "frontend/src/components/tabs/automations/automationTemplates.ts"
OLD_EMAIL_SKILL = Path(__file__).parent / "fixtures/log_transactions_from_email_2026_11_12.md"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_store, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    (tmp_path / "skills").mkdir()
    return tmp_path


def _sync_instructions() -> dict[str, str]:
    src = TEMPLATES.read_text("utf-8")
    out = {}
    for m in re.finditer(r"key: 'sync_(\w+)',", src):
        start = src.index("instructions:\n", m.end())
        end = src.index("',\n", start) + 1
        out[m.group(1)] = "".join(re.findall(r"'((?:[^'\\]|\\.)*)'", src[start:end]))
    return out


def test_every_bank_sync_books_through_the_shared_skill_and_it_ships():
    shipped = {s["id"] for s in STARTER_SKILLS}
    assert "book-transactions" in shipped
    banks = {
        "simplefin",
        "plaid",
        "lunch_money",
        "lunch_flow",
        "ynab",
        "enable_banking",
        "gocardless",
    }
    syncs = _sync_instructions()
    for key in banks:
        assert "book-transactions skill" in syncs[key], key
        assert "category" not in syncs[key].lower(), f"{key} restates booking rules"
    # Broker syncs book positions, not transactions — untouched.
    assert "book-transactions" not in syncs["alpaca"] + syncs["ibkr"]


def test_the_shared_skill_holds_the_rules_the_templates_used_to():
    body = next(s for s in STARTER_SKILLS if s["id"] == "book-transactions")["body"]
    # categorise every row, creating a general category only when none fits
    assert "create_categories" in body and "Uncategorized only when" in body
    assert "is not a fit" in body  # a catch-all like Other never is
    assert "payee_minted" in body  # durable payees
    # what the sync templates said before the move
    assert "isn't linked is a question" in body
    assert "brought in line" in body and "changed that field by hand, which always wins" in body
    assert "Never delete" in body and "withdrew" in body


def test_an_unedited_old_email_skill_is_refreshed_to_point_at_the_shared_rules(isolated):
    _meta, old_body = parse_skill_md(OLD_EMAIL_SKILL.read_text("utf-8"))
    current = next(s for s in STARTER_SKILLS if s["id"] == "log-transactions-from-email")
    s = SkillStore()
    s.create({**current, "body": old_body.strip(), "author": "builtin"})

    assert s.refresh_superseded_starters() == ["log-transactions-from-email"]
    body = s.find_by_id("log-transactions-from-email")["body"].strip()
    assert "book-transactions" in body and body == current["body"].strip()


def test_an_edited_email_skill_is_left_alone(isolated):
    _meta, old_body = parse_skill_md(OLD_EMAIL_SKILL.read_text("utf-8"))
    current = next(s for s in STARTER_SKILLS if s["id"] == "log-transactions-from-email")
    s = SkillStore()
    mine = old_body.strip() + "\n\nAlso tag every row #email."
    s.create({**current, "body": mine, "author": "builtin"})
    assert s.refresh_superseded_starters() == []
    assert s.find_by_id("log-transactions-from-email")["body"].strip() == mine
