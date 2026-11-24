"""AlertStore CRUD + validation (ADR 0014 — standalone ticker-keyed price alerts)."""

from __future__ import annotations

import re

import pytest

from backend.core.errors import ValidationError
from backend.db.alert_store import AlertStore, alert_cache_key


@pytest.fixture
def store(db_isolated):
    return AlertStore()


def test_create_stamps_id_created_at_and_uppercases_ticker(store):
    a = store.create({"ticker": "aapl", "op": ">=", "value": 150, "note": "trim"})
    assert a["id"]
    assert a["created_at"]
    assert a["ticker"] == "AAPL"  # uppercased
    assert a["op"] == ">=" and a["value"] == 150.0 and a["note"] == "trim"
    assert isinstance(a["value"], float)  # always float → stable monitor cache key


def test_new_alert_id_is_short_prefixed_hex(store):
    a = store.create({"ticker": "ACME", "op": ">=", "value": 10})
    assert re.fullmatch(r"alrt_[0-9a-f]{12}", a["id"])


def test_value_is_coerced_to_float(store):
    a = store.create({"ticker": "MSFT", "op": "<=", "value": 200})
    assert a["value"] == 200.0 and isinstance(a["value"], float)


@pytest.mark.parametrize("bad_op", ["==", "=>", "", "approx"])
def test_invalid_op_rejected(store, bad_op):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": bad_op, "value": 1})


def test_blank_ticker_rejected(store):
    with pytest.raises(ValidationError):
        store.create({"ticker": "  ", "op": ">=", "value": 1})


@pytest.mark.parametrize("bad", ["abc", None, float("inf"), float("nan")])
def test_non_finite_value_rejected(store, bad):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": bad})


def test_many_alerts_per_ticker(store):
    store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    store.create({"ticker": "AAPL", "op": "<=", "value": 100})
    rows = store.for_ticker("aapl")  # case-folded lookup
    assert {r["op"] for r in rows} == {">=", "<="}
    assert len(rows) == 2


def test_by_ticker_groups(store):
    store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    store.create({"ticker": "MSFT", "op": "<=", "value": 100})
    grouped = store.by_ticker()
    assert set(grouped) == {"AAPL", "MSFT"}
    assert len(grouped["AAPL"]) == 1


def test_update_revalidates_and_whitelists(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    updated = store.update(a["id"], {"value": 160, "note": "raise", "ticker": "HACK"})
    assert updated["value"] == 160.0 and updated["note"] == "raise"
    assert updated["ticker"] == "AAPL"  # ticker NOT in the update whitelist
    with pytest.raises(ValidationError):
        store.update(a["id"], {"op": "bad"})


def test_update_missing_returns_none(store):
    assert store.update("nope", {"value": 1}) is None


def test_create_defaults_cooldown_fields(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    assert a["on_trigger"] == "cooldown"
    assert a["cooldown_minutes"] == 1440
    assert a["enabled"] is True
    assert a["last_fired_at"] is None


def test_create_accepts_disable_on_trigger(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150, "on_trigger": "disable"})
    assert a["on_trigger"] == "disable"


def test_create_accepts_zero_cooldown_minutes(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150, "cooldown_minutes": 0})
    assert a["cooldown_minutes"] == 0


@pytest.mark.parametrize("bad_on_trigger", ["always", "", "off", "silence"])
def test_invalid_on_trigger_rejected(store, bad_on_trigger):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": 1, "on_trigger": bad_on_trigger})


@pytest.mark.parametrize("bad_cooldown", [-1, "abc", None])
def test_invalid_cooldown_minutes_rejected(store, bad_cooldown):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": 1, "cooldown_minutes": bad_cooldown})


def test_update_allows_cooldown_fields(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    updated = store.update(
        a["id"],
        {
            "on_trigger": "disable",
            "cooldown_minutes": 60,
            "enabled": False,
            "last_fired_at": "2026-08-25T00:00:00+00:00",
        },
    )
    assert updated["on_trigger"] == "disable"
    assert updated["cooldown_minutes"] == 60
    assert updated["enabled"] is False
    assert updated["last_fired_at"] == "2026-08-25T00:00:00+00:00"


def test_delete(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    assert store.delete(a["id"]) is True
    assert store.for_ticker("AAPL") == []
    assert store.delete(a["id"]) is False  # idempotent miss


# ── name / kind / channels ─────────────────────────────────────────────────────────────


def test_create_defaults_name_kind_channels(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    assert a["name"] == ""
    assert a["kind"] == "price"
    assert a["channels"] == []
    assert "notify" not in a


def test_create_keeps_channels_well_formed(store):
    a = store.create(
        {
            "ticker": "AAPL",
            "op": ">=",
            "value": 5,
            "name": "  Stop-loss zone  ",
            "kind": "move",
            "channels": [" email ", "telegram", "email", "in_app", ""],
        }
    )
    assert a["name"] == "Stop-loss zone"  # stripped
    assert a["kind"] == "move"
    assert a["channels"] == ["email", "telegram"]  # trimmed, de-duplicated, in-app implied


@pytest.mark.parametrize("bad_value", [0, -1, 100.1, 101])
def test_move_kind_rejects_value_out_of_range(store, bad_value):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": bad_value, "kind": "move"})


def test_move_kind_accepts_boundary_100(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 100, "kind": "move"})
    assert a["value"] == 100.0


def test_price_kind_does_not_bound_value(store):
    # A price alert's value has no percent-style upper bound — only move alerts do.
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 5000, "kind": "price"})
    assert a["value"] == 5000.0


@pytest.mark.parametrize("bad_kind", ["percent", "", "PRICE", "level"])
def test_invalid_kind_rejected(store, bad_kind):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": 1, "kind": bad_kind})


@pytest.mark.parametrize("bad_channels", ["telegram", [1], {"telegram": True}])
def test_malformed_channels_rejected(store, bad_channels):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": 1, "channels": bad_channels})


def test_name_over_64_chars_rejected(store):
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": 1, "name": "x" * 65})


def test_name_at_64_chars_accepted(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 1, "name": "x" * 64})
    assert a["name"] == "x" * 64


def test_update_allows_name_kind_channels(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150, "channels": ["telegram"]})
    updated = store.update(a["id"], {"name": "Stop-loss zone", "kind": "price", "channels": []})
    assert updated["name"] == "Stop-loss zone"
    assert updated["kind"] == "price"
    assert updated["channels"] == []


def test_update_revalidates_value_when_kind_flips_to_move(store):
    # 150 is a perfectly fine PRICE, but not a valid MOVE percent (>100).
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    with pytest.raises(ValidationError):
        store.update(a["id"], {"kind": "move"})


def test_update_revalidates_value_against_existing_kind(store):
    # Already a move alert; patching just the value must still respect the move bound.
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 5, "kind": "move"})
    with pytest.raises(ValidationError):
        store.update(a["id"], {"value": 150})


def test_update_kind_and_value_together_is_consistent(store):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    updated = store.update(a["id"], {"kind": "move", "value": 5})
    assert updated["kind"] == "move" and updated["value"] == 5.0


@pytest.mark.parametrize("bad_kind", ["bogus", ""])
def test_update_invalid_kind_rejected(store, bad_kind):
    a = store.create({"ticker": "AAPL", "op": ">=", "value": 150})
    with pytest.raises(ValidationError):
        store.update(a["id"], {"kind": bad_kind})


# ── alert_cache_key ──────────────────────────────────────────────────────────────────────


def test_alert_cache_key_price_is_unchanged_shape():
    assert alert_cache_key({"op": ">=", "value": 100.0, "kind": "price"}) == ">=100.0"
    # A legacy row with no "kind" at all behaves exactly like a price alert.
    assert alert_cache_key({"op": ">=", "value": 100.0}) == ">=100.0"


def test_alert_cache_key_move_gets_a_distinct_suffix():
    assert alert_cache_key({"op": ">=", "value": 5.0, "kind": "move"}) == ">=5.0%"


def test_alert_cache_key_price_and_move_never_collide_on_the_same_number():
    price_key = alert_cache_key({"op": ">=", "value": 5.0, "kind": "price"})
    move_key = alert_cache_key({"op": ">=", "value": 5.0, "kind": "move"})
    assert price_key != move_key
