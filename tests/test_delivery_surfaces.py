"""The surfaces around the delivery decider: the briefing's ``get_untold_news`` read, the
``delivery_instructions`` setting (REST and the Lucius tool), and the reset wipe."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.services import news_delivery as nd
from backend.services import watchlist as wl
from backend.services.news_delivery import apply_delivery_change as change


def call(tool: str, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


def submit(**over):
    return change(
        "submit",
        candidate={
            "source": "analysis",
            "route_key": "news",
            "story_id": over.pop("story_id", "s1"),
            "subject": "asset:ins_acme",
            "label": "Acme Corp",
            "headline": "Acme agrees a takeover",
            "why": "Acme accepted a bid.",
            "legacy_message": "Analysis — Acme Corp",
            **over,
        },
    )


def test_get_untold_news_is_empty_when_nothing_was_held_back(db_isolated):
    assert call("get_untold_news") == {"items": []}


@pytest.mark.flags(intel_delivery_decider=True)
def test_get_untold_news_lists_waiting_items_and_flags_what_was_already_told(db_isolated):
    waiting = submit(story_id="w")
    change("decide", delivery_id=waiting["id"], decision="later")
    told = submit(story_id="t", headline="Acme bid told")
    change("mark_told", delivery_id=told["id"], told_via="now", message_sent="Sam, bid.")
    skipped = submit(story_id="k")
    change("decide", delivery_id=skipped["id"], decision="skip")

    items = call("get_untold_news")["items"]
    assert [(i["headline"], i["already_told"]) for i in items] == [
        ("Acme agrees a takeover", False),
        ("Acme bid told", True),
    ]
    assert items[1]["told_at"] and "told_at" not in items[0]


@pytest.mark.flags(intel_delivery_decider=True)
def test_get_untold_news_is_bounded_and_sanitized(db_isolated):
    for n in range(20):
        row = submit(story_id=f"s{n}", why="a«b»\x07c")
        change("decide", delivery_id=row["id"], decision="later")
    items = call("get_untold_news")["items"]
    assert len(items) == 15
    assert items[0]["why"] == "abc"


@pytest.mark.flags(intel_delivery_decider=False)
def test_get_untold_news_is_empty_while_the_flag_is_off(db_isolated):
    row = submit()
    change("decide", delivery_id=row["id"], decision="later")
    assert call("get_untold_news") == {"items": []}


def test_get_untold_news_never_mutates():
    from backend.ai.tools._write_classification import READ_ONLY_TOOLS

    assert "get_untold_news" in READ_ONLY_TOOLS


def test_delivery_instructions_are_trimmed_and_capped_by_the_one_validator():
    assert wl.validate_ai_settings({"delivery_instructions": "  No upgrades.  "}) == {
        "delivery_instructions": "No upgrades."
    }
    long = wl.validate_ai_settings({"delivery_instructions": "x" * 5000})
    assert len(long["delivery_instructions"]) == wl.DELIVERY_INSTRUCTIONS_MAX_CHARS
    assert wl.validate_ai_settings({"delivery_instructions": ""}) == {"delivery_instructions": ""}


def test_the_instructions_default_to_empty_in_both_config_defaults():
    from backend.db import watchlist_store

    assert wl._DEFAULT_CONFIG["delivery_instructions"] == ""
    assert watchlist_store._DEFAULT_CONFIG["delivery_instructions"] == ""


def test_lucius_can_set_and_clear_the_instructions(db_isolated):
    out = call("update_monitoring_config", delivery_instructions="No analyst upgrades.")
    assert out["ok"] and out["config"]["delivery_instructions"] == "No analyst upgrades."
    assert wl.read_watchlist()["config"]["delivery_instructions"] == "No analyst upgrades."
    out = call("update_monitoring_config", delivery_instructions="")
    assert out["config"]["delivery_instructions"] == ""


def test_the_instructions_patch_over_rest(db_isolated, quiet_app_boot):
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        response = client.patch(
            "/api/watchlist/config", json={"delivery_instructions": " Not at night. "}
        )
    assert response.status_code == 200
    assert response.json()["delivery_instructions"] == "Not at night."


def test_a_reset_wipes_the_ledger(db_isolated):
    from backend.db import portable_bridge

    submit()
    assert len(nd.pending()) == 1
    assert "news_deliveries.json" in portable_bridge._WIPE_FNS
    from backend.db.engine import get_engine

    with get_engine().begin() as conn:
        portable_bridge._wipe_news_deliveries(conn)
    assert nd.pending() == []


@pytest.mark.parametrize("table", ["news_deliveries"])
def test_the_ledger_is_checkpoint_excluded(table):
    from backend.ai.checkpoint._policy import _EXCLUDED_TABLES

    assert table in _EXCLUDED_TABLES


def test_update_monitoring_config_waits_for_the_users_approval():
    from backend.ai.tools._confirm_gate import is_gated_tool

    assert is_gated_tool("update_monitoring_config")
