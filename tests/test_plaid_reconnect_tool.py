"""Lucius can hand the user a Plaid sign-in link that renews ONE linked bank — update mode on
that bank's own token, so no account link breaks. The tool resolves the bank by name, asks
before running, and never reaches a new-bank link or a credential."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core.errors import ValidationError
from backend.db.plaid_config_store import PlaidConfigStore, bank_key
from backend.services.aggregation import plaid as svc
from backend.services.aggregation import plaid_reconnect


@pytest.fixture(autouse=True)
def isolate(db_isolated):
    """Fresh, isolated DB per test."""


@pytest.fixture
def store():
    s = PlaidConfigStore()
    s.update({"client_id": "cid", "secret": "skey", "enabled": True})
    return s


def _item(item_id: str, name: str, **extra) -> dict:
    return {
        "item_id": item_id,
        "access_token": f"access-{item_id}",
        "institution_id": f"ins_{item_id}",
        "institution_name": name,
        **extra,
    }


class _Resp:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def calls(monkeypatch):
    seen: list = []

    def fake_post(url, json=None, timeout=None):
        seen.append((url.split(".plaid.com", 1)[1], json))
        return _Resp({"link_token": "lt-1", "hosted_link_url": "https://hosted.plaid.example/lt-1"})

    monkeypatch.setattr(svc.httpx, "post", fake_post)
    return seen


def test_reconnect_by_name_opens_update_mode_on_that_banks_token(store, calls):
    store.add_item(_item("i1", "Wells Fargo"))
    store.add_item(_item("i2", "Chase"))
    store.mark_reconnect(needed={bank_key("i2")}, ok=set())

    result = plaid_reconnect.start_reconnect("chase")

    path, body = calls[0]
    assert path == "/link/token/create"
    assert body["access_token"] == "access-i2"
    assert "products" not in body and "update" not in body
    assert result == {
        "bank": "Chase",
        "link": "https://hosted.plaid.example/lt-1",
        "expiration": None,
        "needed_reconnect": True,
    }
    assert store.pending_link_bank() == bank_key("i2")
    assert "access-i2" not in json.dumps(result)  # the token never reaches the model


def test_the_users_own_name_and_plaids_both_resolve(store, calls):
    store.add_item(_item("i1", "Chase", custom_name="Joint Chase"))
    assert plaid_reconnect.start_reconnect("Joint Chase")["bank"] == "Joint Chase"
    assert plaid_reconnect.start_reconnect("CHASE")["bank"] == "Joint Chase"


def test_no_bank_named_picks_the_one_that_needs_it(store, calls):
    store.add_item(_item("i1", "Wells Fargo"))
    store.add_item(_item("i2", "Chase"))
    store.mark_reconnect(needed={bank_key("i1")}, ok=set())
    assert plaid_reconnect.start_reconnect("")["bank"] == "Wells Fargo"


def test_no_bank_named_and_several_candidates_asks_which(store, calls):
    store.add_item(_item("i1", "Wells Fargo"))
    store.add_item(_item("i2", "Chase"))
    with pytest.raises(ValidationError, match="Which bank.*Wells Fargo.*Chase"):
        plaid_reconnect.start_reconnect("")
    assert calls == []


def test_an_unknown_bank_is_refused_naming_the_linked_ones(store, calls):
    store.add_item(_item("i1", "Wells Fargo"))
    with pytest.raises(ValidationError, match="Linked banks: Wells Fargo"):
        plaid_reconnect.start_reconnect("Citi")
    assert calls == []


def test_two_banks_with_the_same_name_are_ambiguous(store, calls):
    store.add_item(_item("i1", "Chase"))
    store.add_item(_item("i2", "Chase"))
    with pytest.raises(ValidationError, match="More than one"):
        plaid_reconnect.start_reconnect("Chase")


def test_nothing_linked_says_to_link_on_the_plaid_page(store, calls):
    with pytest.raises(ValidationError, match="link one on the Plaid page"):
        plaid_reconnect.start_reconnect("Chase")


def test_the_tool_is_plaid_scoped_a_write_and_asks_by_default(store, calls):
    from backend.ai.tools import registry
    from backend.ai.tools._confirm_gate import _default_level
    from backend.services.tool_permissions import DEFAULT_LEVEL, GATE_EXEMPT_READ_SHAPED

    spec = registry._REGISTRY["plaid_reconnect_bank"]
    assert spec.connector == "plaid"
    assert spec.mutates == ("config",)
    assert "plaid_reconnect_bank" not in GATE_EXEMPT_READ_SHAPED
    assert _default_level("plaid_reconnect_bank", "plaid") == DEFAULT_LEVEL != "always_allow"


def test_the_tool_returns_the_link_for_the_named_bank(store, calls):
    from backend.ai.tools.plaid import plaid_reconnect_bank

    store.add_item(_item("i1", "Chase"))
    text, _ = asyncio.run(plaid_reconnect_bank({"bank": "Chase"}))
    assert json.loads(text)["link"] == "https://hosted.plaid.example/lt-1"
