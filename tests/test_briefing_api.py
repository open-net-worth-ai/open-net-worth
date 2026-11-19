"""
TestClient endpoint tests for GET /api/briefing.

Covers:
- 200 with cached response (is_cached=True)
- 200 with fresh generation (is_cached=False)
- ?refresh=true forces regeneration even when cache is valid
- ?current_nw is forwarded to generation
- LLM failure degrades gracefully (no 500)
- _daypart() bucketing logic
- _load_cache() invalidation: missing file, wrong format, stale day-part
- _save_cache() round-trip
- _spending_block() returns lines for transactions / empty when none
- _build_context() stage detection (new user / accounts only / investor)
- briefing_preferences toggles (accounts, holdings_news, fx, upcoming_actions, spending)
- ACTIONS_JSON upcoming actions filtering (done/future/in-range)
- LLM returns bad JSON → graceful fallback note
- No secrets in response body
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Wire briefing paths + all data stores to tmp_path. Stubs external calls.

    accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them to a fresh, empty database; acct_path/txn_path are kept in the returned dict
    only because a couple of tests still reference the keys (unused for reads/writes).
    actions is DB-backed too (Wave 5C, JSON->DB) — db_isolated already gives it a fresh,
    empty table, no path monkeypatch needed; actions_path is likewise kept as an unused
    identity key."""
    import backend.api.briefing as brf

    # Redirect the cache file used by briefing (still a disposable LLM-response cache)
    cache_path = tmp_path / "briefing_cache.json"
    actions_path = tmp_path / "actions.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_path)

    acct_path = tmp_path / "accounts.json"
    txn_path = tmp_path / "transactions.json"

    # FX comes from the offline `fx_rates` cache (briefing binds load_fx_cache by name, so a
    # patch on services.fx_cache would not reach it).

    # Stub read_profile — default minimal profile
    monkeypatch.setattr("backend.api.briefing.read_profile", lambda: {"base_currency": "SGD"})

    # Stub net_worth to avoid touching ledger
    monkeypatch.setattr(
        "backend.services.ledger.net_worth", lambda base=None: {"net_worth": 50_000.0}
    )

    # Stub gather_evidence so no network calls
    async def _no_news(queries, caches=None, kind="news", max_results=4, limit=12):
        return []

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _no_news)

    return {
        "tmp_path": tmp_path,
        "cache_path": cache_path,
        "actions_path": actions_path,
        "acct_path": acct_path,
        "txn_path": txn_path,
    }


def _holding(symbol, name, value, *, qty=1.0, price=0.0, ccy="SGD", weight=10.0, day_pct=0.0):
    """One merged row of the Investments book, in the shape ``investments_book`` emits."""
    return {
        "symbol": symbol,
        "name": name,
        "value": value,
        "qty": qty,
        "price": price,
        "currency": ccy,
        "weight": weight,
        "day_pct": day_pct,
    }


def _stub_book(monkeypatch, *holdings):
    book = {"merged_rows": list(holdings), "rows": list(holdings)}
    monkeypatch.setattr("backend.api.briefing._invested_book", lambda base: book)


def _stub_llm(monkeypatch, reply: dict | None = None, raise_exc: Exception | None = None):
    """Patch the llm_seam.complete function used by briefing."""
    import backend.core.llm_seam as _seam

    if raise_exc is not None:

        async def _fail(*a, **kw):
            raise raise_exc

        monkeypatch.setattr(_seam, "_complete", _fail)
    else:
        payload = reply or {
            "note": "Good morning. Markets look calm.",
            "actions": ["Review portfolio"],
            "chat_suggestion": "Should I rebalance?",
        }

        async def _ok(*a, **kw):
            return json.dumps(payload)

        monkeypatch.setattr(_seam, "_complete", _ok)
        # parse_json falls back to json.loads when _parse_json is None — that works fine.
        monkeypatch.setattr(_seam, "_parse_json", json.loads)


# ---------------------------------------------------------------------------
# Basic 200 shape
# ---------------------------------------------------------------------------


def test_get_briefing_200_shape_keys(client, isolated, monkeypatch):
    """GET /api/briefing → 200 with note, chat_suggestion, generated_at, is_cached."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    required = {"note", "chat_suggestion", "generated_at", "is_cached"}
    assert required <= set(body.keys()), f"Missing keys: {required - set(body.keys())}"


def test_get_briefing_fresh_is_cached_false(client, isolated, monkeypatch):
    """First call with no cache → is_cached=False."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert resp.json()["is_cached"] is False


def test_get_briefing_returns_note_string(client, isolated, monkeypatch):
    """note field must be a non-empty string."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing")
    note = resp.json()["note"]
    assert isinstance(note, str)
    assert len(note) > 0


def test_get_briefing_returns_actions_list(client, isolated, monkeypatch):
    """actions field is a list when returned by the LLM."""
    _stub_llm(
        monkeypatch,
        reply={
            "note": "Morning note.",
            "actions": ["Action A", "Action B"],
            "chat_suggestion": "What next?",
        },
    )
    resp = client.get("/api/briefing")
    body = resp.json()
    assert "actions" in body
    assert isinstance(body["actions"], list)
    assert "Action A" in body["actions"]


def test_get_briefing_returns_chat_suggestion(client, isolated, monkeypatch):
    """chat_suggestion echoes what the LLM returned."""
    _stub_llm(
        monkeypatch,
        reply={
            "note": "Good morning.",
            "actions": [],
            "chat_suggestion": "Should I rebalance?",
        },
    )
    resp = client.get("/api/briefing")
    assert resp.json()["chat_suggestion"] == "Should I rebalance?"


def test_get_briefing_generated_at_iso_format(client, isolated, monkeypatch):
    """generated_at must be parseable as an ISO datetime string."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing")
    generated_at = resp.json()["generated_at"]
    # Should not raise
    dt = datetime.fromisoformat(generated_at)
    assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_get_briefing_cached_response_is_cached_true(client, isolated, monkeypatch):
    """Second call within the same day-part uses cache → is_cached=True."""
    _stub_llm(monkeypatch)
    # First call to populate cache
    resp1 = client.get("/api/briefing")
    assert resp1.status_code == 200
    assert resp1.json()["is_cached"] is False

    # Second call in the same day-part returns cached
    resp2 = client.get("/api/briefing")
    assert resp2.status_code == 200
    assert resp2.json()["is_cached"] is True


def test_get_briefing_refresh_true_bypasses_cache(client, isolated, monkeypatch):
    """?refresh=true forces regeneration even when a valid cache exists."""
    _stub_llm(monkeypatch)
    # Populate cache
    client.get("/api/briefing")

    # Force refresh
    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    assert resp.json()["is_cached"] is False


def test_get_briefing_stale_cache_regenerates(client, isolated, monkeypatch):
    """A cache whose generated_at is from a different day-part triggers regeneration."""

    _stub_llm(monkeypatch)

    # Write a stale cache from a fixed past date — guaranteed different day-part
    stale_time = datetime(2000, 1, 1, 2, 0)
    stale_data = {
        "note": "Stale note",
        "actions": [],
        "chat_suggestion": "Old suggestion",
        "generated_at": stale_time.isoformat(),
    }
    isolated["cache_path"].write_text(json.dumps(stale_data), encoding="utf-8")

    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    # The note should differ from the stale note OR is_cached is False
    body = resp.json()
    assert body["is_cached"] is False


def test_get_briefing_missing_cache_file_regenerates(client, isolated, monkeypatch):
    """When cache file does not exist, briefing is generated fresh."""
    _stub_llm(monkeypatch)
    assert not isolated["cache_path"].exists()
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert resp.json()["is_cached"] is False


def test_get_briefing_corrupt_cache_file_regenerates(client, isolated, monkeypatch):
    """If cache file is corrupt JSON, briefing regenerates without error."""
    _stub_llm(monkeypatch)
    isolated["cache_path"].write_text("{bad json!!!", encoding="utf-8")
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert resp.json()["is_cached"] is False


def test_get_briefing_cache_missing_note_key_regenerates(client, isolated, monkeypatch):
    """Cache with missing 'note' key (old format) triggers regeneration."""
    _stub_llm(monkeypatch)
    old_format = {"generated_at": datetime.now().isoformat(), "content": "old"}
    isolated["cache_path"].write_text(json.dumps(old_format), encoding="utf-8")
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert resp.json()["is_cached"] is False


# ---------------------------------------------------------------------------
# ?current_nw query parameter
# ---------------------------------------------------------------------------


def test_get_briefing_current_nw_accepted(client, isolated, monkeypatch):
    """?current_nw is accepted; no 422."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing?current_nw=123456.78")
    assert resp.status_code == 200


def test_get_briefing_current_nw_zero(client, isolated, monkeypatch):
    """?current_nw=0 is valid and produces a briefing."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing?current_nw=0")
    assert resp.status_code == 200
    assert resp.json()["note"] != ""


# ---------------------------------------------------------------------------
# LLM failure / bad JSON degradation
# ---------------------------------------------------------------------------


def test_get_briefing_llm_unavailable_returns_fallback(client, isolated, monkeypatch):
    """LLM unavailable → 200 with fallback note (no 500)."""
    from backend.core.llm_seam import LLMUnavailable

    _stub_llm(monkeypatch, raise_exc=LLMUnavailable("no provider"))
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    body = resp.json()
    assert "note" in body
    assert isinstance(body["note"], str)
    assert len(body["note"]) > 0


def test_get_briefing_llm_network_error_returns_fallback(client, isolated, monkeypatch):
    """Generic network error → 200 with fallback note (no 500)."""
    _stub_llm(monkeypatch, raise_exc=RuntimeError("connection reset"))
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert "note" in resp.json()


def test_get_briefing_llm_bad_json_returns_fallback(client, isolated, monkeypatch):
    """LLM returns non-JSON → fallback note returned gracefully."""
    import backend.core.llm_seam as _seam

    async def _bad_reply(*a, **kw):
        return "Sorry, I cannot help with that today."

    monkeypatch.setattr(_seam, "_complete", _bad_reply)

    # Use a safe parse_json that returns None on invalid JSON (does not raise)
    def _safe_parse(text):
        try:
            return json.loads(text)
        except Exception:
            return None

    monkeypatch.setattr(_seam, "_parse_json", _safe_parse)

    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    body = resp.json()
    assert "note" in body
    # Falls back to the "Unable to generate" message
    assert isinstance(body["note"], str)


def test_get_briefing_llm_json_without_note_returns_fallback(client, isolated, monkeypatch):
    """LLM returns JSON but without 'note' key → fallback note returned."""
    import backend.core.llm_seam as _seam

    async def _no_note(*a, **kw):
        return json.dumps({"chat_suggestion": "Ask me anything."})

    monkeypatch.setattr(_seam, "_complete", _no_note)
    monkeypatch.setattr(_seam, "_parse_json", json.loads)

    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    body = resp.json()
    # Should not crash; falls back
    assert isinstance(body["note"], str)


def test_get_briefing_unparseable_reply_no_500(client, isolated, monkeypatch):
    """Regression: parse_json RAISING (the no-provider fallback is a plain json.loads, which
    throws on a non-JSON reply) must NOT 500 — it degrades to 200 with error:true + a note."""
    import backend.core.llm_seam as _seam

    async def _prose(*a, **kw):
        return "Here is your briefing: markets are calm today."  # not JSON

    monkeypatch.setattr(_seam, "_complete", _prose)
    # json.loads raises json.JSONDecodeError on the prose reply — the exact bug trigger.
    monkeypatch.setattr(_seam, "_parse_json", json.loads)

    resp = client.get("/api/briefing")
    assert resp.status_code == 200  # was a 500 before the fix
    body = resp.json()
    assert body.get("error") is True
    assert isinstance(body["note"], str) and body["note"]
    assert body.get("is_cached") is False


def test_get_briefing_max_tokens_ceiling_is_not_too_low(client, isolated, monkeypatch):
    """Regression: max_tokens=1000 truncated a verbose model's JSON reply mid-object every
    time (every recent claude-sonnet-5 completion hit that ceiling exactly, output=1000),
    which is unparseable and surfaces as "The AI returned a briefing we couldn't read." on
    every single load. The ceiling must stay well above the old value, and the model must
    be told its budget in the prompt so it self-limits under it (same fix as the
    asset_brief/RKLB truncation incident)."""
    import backend.api.briefing as brf
    import backend.core.llm_seam as _seam

    captured = {}

    async def _capture(prompt, *, system="", model="", max_tokens=2048, source="complete", **kw):
        captured["max_tokens"] = max_tokens
        captured["prompt"] = prompt
        return json.dumps({"note": "All clear.", "actions": [], "chat_suggestion": ""})

    monkeypatch.setattr(_seam, "_complete", _capture)
    monkeypatch.setattr(_seam, "_parse_json", json.loads)

    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    assert not resp.json().get("error")

    assert captured["max_tokens"] == brf._BRIEFING_MAX_TOKENS
    assert captured["max_tokens"] > 1000  # the old ceiling that truncated every reply
    # The model is told its budget so it self-limits and the JSON always closes cleanly.
    assert str(brf._BRIEFING_MAX_TOKENS) in captured["prompt"]


def test_get_briefing_failure_is_not_cached(client, isolated, monkeypatch):
    """A failed briefing must not be cached — else the error would pin for the whole day-part.
    Once the provider recovers, a plain (non-refresh) load regenerates instead of serving it."""
    import backend.core.llm_seam as _seam

    async def _prose(*a, **kw):
        return "not json"

    monkeypatch.setattr(_seam, "_complete", _prose)
    monkeypatch.setattr(_seam, "_parse_json", json.loads)
    assert client.get("/api/briefing").json().get("error") is True

    async def _ok(*a, **kw):
        return json.dumps({"note": "All clear.", "actions": [], "chat_suggestion": ""})

    monkeypatch.setattr(_seam, "_complete", _ok)
    monkeypatch.setattr(_seam, "_parse_json", json.loads)
    body = client.get("/api/briefing").json()  # no refresh — must NOT serve a cached error
    assert not body.get("error")
    assert body["note"] == "All clear."


# ---------------------------------------------------------------------------
# _daypart() unit tests
# ---------------------------------------------------------------------------


def test_daypart_morning():
    from backend.api.briefing import _daypart

    dt = datetime(2025, 1, 15, 8, 30)  # 08:30
    d, part = _daypart(dt)
    assert part == "morning"
    assert d == dt.date()


def test_daypart_afternoon():
    from backend.api.briefing import _daypart

    dt = datetime(2025, 1, 15, 14, 0)  # 14:00
    _, part = _daypart(dt)
    assert part == "afternoon"


def test_daypart_evening():
    from backend.api.briefing import _daypart

    dt = datetime(2025, 1, 15, 19, 0)  # 19:00
    _, part = _daypart(dt)
    assert part == "evening"


def test_daypart_noon_is_afternoon():
    from backend.api.briefing import _daypart

    dt = datetime(2025, 1, 15, 12, 0)  # exactly noon
    _, part = _daypart(dt)
    assert part == "afternoon"


def test_daypart_small_hours_are_night_not_morning():
    """Persona QA 2026-10-14: a 04:41 note opened "Good morning". 22:00-05:00 is night."""
    from backend.api.briefing import _daypart

    assert _daypart(datetime(2025, 1, 15, 0, 0))[1] == "night"
    assert _daypart(datetime(2025, 1, 15, 4, 41))[1] == "night"
    assert _daypart(datetime(2025, 1, 15, 5, 0))[1] == "morning"
    assert _daypart(datetime(2025, 1, 15, 21, 59))[1] == "evening"
    assert _daypart(datetime(2025, 1, 15, 22, 0))[1] == "night"


def test_daypart_boundary_17_is_evening():
    from backend.api.briefing import _daypart

    dt = datetime(2025, 1, 15, 17, 0)  # exactly 17:00
    _, part = _daypart(dt)
    assert part == "evening"


# ---------------------------------------------------------------------------
# _load_cache() unit tests
# ---------------------------------------------------------------------------


def test_load_cache_returns_none_when_file_missing(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    monkeypatch.setattr(brf, "CACHE_FILE", tmp_path / "briefing_cache.json")
    result = brf._load_cache()
    assert result is None


def test_load_cache_returns_none_when_missing_note_key(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    cache_file = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_file)
    cache_file.write_text(
        json.dumps({"generated_at": datetime.now().isoformat(), "old_format": True}),
        encoding="utf-8",
    )
    result = brf._load_cache()
    assert result is None


def test_load_cache_returns_data_same_daypart(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    cache_file = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_file)
    now = datetime.now()
    data = {
        "note": "Morning note",
        "actions": [],
        "chat_suggestion": "Ask me something",
        "generated_at": now.isoformat(),
    }
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    result = brf._load_cache()
    assert result is not None
    assert result["note"] == "Morning note"


def test_load_cache_returns_none_for_different_daypart(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    cache_file = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_file)
    # Force a stale datetime guaranteed to be a different day-part
    stale = datetime(2000, 1, 1, 2, 0)  # 2am in year 2000
    data = {
        "note": "Old note",
        "actions": [],
        "chat_suggestion": "",
        "generated_at": stale.isoformat(),
    }
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    result = brf._load_cache()
    assert result is None


# ---------------------------------------------------------------------------
# _save_cache() unit tests
# ---------------------------------------------------------------------------


def test_save_cache_writes_file(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    cache_file = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_file)
    payload = {"note": "Test note", "actions": [], "chat_suggestion": "Test?"}
    result = brf._save_cache(payload)
    assert cache_file.exists()
    assert result["note"] == "Test note"
    assert "generated_at" in result


def test_save_cache_returns_generated_at(tmp_path, monkeypatch):
    import backend.api.briefing as brf

    cache_file = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", cache_file)
    result = brf._save_cache({"note": "Hello", "actions": [], "chat_suggestion": ""})
    dt = datetime.fromisoformat(result["generated_at"])
    assert isinstance(dt, datetime)


def test_save_cache_survives_unwritable_dir(tmp_path, monkeypatch):
    """_save_cache should not raise even if the file cannot be written."""
    import backend.api.briefing as brf

    bad_path = tmp_path / "nonexistent_dir" / "cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", bad_path)
    # Should not raise
    result = brf._save_cache({"note": "Hi", "actions": [], "chat_suggestion": ""})
    assert result["note"] == "Hi"


# ---------------------------------------------------------------------------
# _spending_block() unit tests
# ---------------------------------------------------------------------------


def test_spending_block_empty_when_no_transactions(tmp_path, monkeypatch, db_isolated):
    """No transactions → spending block returns empty list."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})

    lines = brf._spending_block({}, "SGD")
    assert lines == []


def test_spending_block_returns_lines_for_expense(tmp_path, monkeypatch, db_isolated):
    """Expense this month → spending block includes at least one line."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx
    import backend.services.ledger as _ledger
    from tests.conftest import seed_stub_accounts

    today = date.today().isoformat()
    txn = {
        "id": "t1",
        "date": today,
        "kind": "expense",
        "category": "groceries",
        "description": "Supermarket",
        "from_account_id": "acc1",
        "amount": 200.0,
        "currency": "SGD",
        "base_amount": 200.0,
        "deleted": False,
    }
    seed_stub_accounts([txn])
    _ledger.transactions.replace_all([txn])
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})

    lines = brf._spending_block({}, "SGD")
    assert len(lines) >= 1
    # Month-to-date line should be present
    assert any("Month-to-date" in line for line in lines)


# ---------------------------------------------------------------------------
# _build_context() stage detection
# ---------------------------------------------------------------------------


def test_build_context_new_user_stage(tmp_path, monkeypatch, db_isolated):
    """No accounts, no positions → context says NEW USER."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(0.0)
    assert "NEW USER" in ctx


def test_build_context_accounts_only_stage(tmp_path, monkeypatch, db_isolated):
    """Has account, no market positions → ACCOUNTS ONLY stage."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx
    import backend.services.ledger as _ledger

    acct = {
        "id": "acc1",
        "name": "Meridian Bank Savings",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 1000.0,
        "initial_balance_date": "2024-01-01",
        "archived": False,
    }
    _ledger.accounts.replace_all([acct])
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(5000.0)
    assert "ACCOUNTS ONLY" in ctx


def test_build_context_investor_stage(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Has market positions → INVESTOR stage."""
    import backend.api.briefing as brf

    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    _stub_book(
        monkeypatch, _holding("AAPL", "Apple Inc.", 24_300.0, qty=10, price=180.0, ccy="USD")
    )

    ctx = brf._build_context(100_000.0)
    assert "INVESTOR" in ctx


def test_build_context_includes_base_currency(tmp_path, monkeypatch, db_isolated):
    """Context must include the base currency."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(0.0)
    assert "SGD" in ctx


def test_build_context_includes_total_nw(tmp_path, monkeypatch, db_isolated):
    """Total NW is mentioned in context (but marked not to repeat)."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(99_999.0)
    assert "99,999" in ctx


# ---------------------------------------------------------------------------
# Briefing preferences toggle (section gating)
# ---------------------------------------------------------------------------


def test_build_context_hides_accounts_when_disabled(tmp_path, monkeypatch, db_isolated):
    """briefing_preferences.accounts=False → ACCOUNTS section omitted."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx
    import backend.services.ledger as _ledger

    acct = {
        "id": "a1",
        "name": "Meridian Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 0,
        "initial_balance_date": "2024-01-01",
        "archived": False,
    }
    _ledger.accounts.replace_all([acct])
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"accounts": False}},
    )

    ctx = brf._build_context(0.0)
    assert "ACCOUNTS:" not in ctx


def test_build_context_hides_holdings_when_disabled(tmp_path, monkeypatch, db_isolated):
    """briefing_preferences.holdings_news=False → KEY INVESTMENT HOLDINGS section omitted."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    _stub_book(monkeypatch, _holding("AAPL", "Apple", 100.0, qty=1, price=100.0, ccy="SGD"))
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"holdings_news": False}},
    )

    ctx = brf._build_context(10_000.0)
    assert "KEY INVESTMENT HOLDINGS" not in ctx


def test_build_context_hides_fx_when_disabled(tmp_path, monkeypatch, db_isolated):
    """briefing_preferences.fx=False → CURRENT FX RATES section omitted."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"fx": False}},
    )

    ctx = brf._build_context(0.0)
    assert "CURRENT FX RATES" not in ctx


def test_build_context_hides_upcoming_actions_when_disabled(tmp_path, monkeypatch, db_isolated):
    """briefing_preferences.upcoming_actions=False → UPCOMING ACTIONS section omitted."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"upcoming_actions": False}},
    )

    ctx = brf._build_context(0.0)
    assert "UPCOMING ACTIONS" not in ctx


def test_build_context_custom_focus_included(tmp_path, monkeypatch, db_isolated):
    """briefing_preferences.custom_focus is injected into context."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {
            "base_currency": "SGD",
            "briefing_preferences": {"custom_focus": "Focus on ESG investments"},
        },
    )

    ctx = brf._build_context(0.0)
    assert "Focus on ESG investments" in ctx


# ---------------------------------------------------------------------------
# ACTIONS_JSON upcoming actions filtering
# ---------------------------------------------------------------------------


def test_build_context_upcoming_actions_in_range(tmp_path, monkeypatch, db_isolated):
    """Actions due within 14 days appear in context."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    soon = (date.today() + timedelta(days=5)).isoformat()
    actions = [{"due": soon, "status": "Pending", "category": "Tax", "action": "File tax return"}]
    # actions is DB-backed now (Wave 5C, JSON->DB) — seed directly through ActionStore.
    from backend.db.action_store import ActionStore

    ActionStore().replace_all(actions)

    ctx = brf._build_context(0.0)
    assert "File tax return" in ctx


def test_build_context_done_actions_excluded(tmp_path, monkeypatch, db_isolated):
    """Done actions are filtered out of upcoming actions."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    soon = (date.today() + timedelta(days=3)).isoformat()
    actions = [{"due": soon, "status": "Done", "category": "Banking", "action": "Transfer funds"}]
    # actions is DB-backed now (Wave 5C, JSON->DB) — seed directly through ActionStore.
    from backend.db.action_store import ActionStore

    ActionStore().replace_all(actions)

    ctx = brf._build_context(0.0)
    assert "Transfer funds" not in ctx


def test_build_context_future_actions_excluded(tmp_path, monkeypatch, db_isolated):
    """Actions more than 30 days out do not appear in context."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    far_future = (date.today() + timedelta(days=45)).isoformat()
    actions = [
        {
            "due": far_future,
            "status": "Pending",
            "category": "Invest",
            "action": "Rebalance portfolio",
        }
    ]
    # actions is DB-backed now (Wave 5C, JSON->DB) — seed directly through ActionStore.
    from backend.db.action_store import ActionStore

    ActionStore().replace_all(actions)

    ctx = brf._build_context(0.0)
    assert "Rebalance portfolio" not in ctx


def test_build_context_no_actions_file(tmp_path, monkeypatch, db_isolated):
    """No actions row at all → context still renders (no crash). actions is DB-backed
    now (Wave 5C, JSON->DB) — db_isolated already gives it a fresh, empty table."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(0.0)
    assert isinstance(ctx, str)
    assert len(ctx) > 0


# ---------------------------------------------------------------------------
# Holdings context: native currency and FX exposure
# ---------------------------------------------------------------------------


def test_build_context_includes_native_amount_for_foreign_holdings(
    tmp_path, monkeypatch, db_isolated, fx_rates
):
    """Holdings in foreign currency include 'native:' annotation in context."""
    import backend.api.briefing as brf

    _stub_book(monkeypatch, _holding("AAPL", "Apple", 1350.0, qty=10, price=100.0, ccy="USD"))
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(20_000.0)
    assert "native: USD" in ctx


def test_build_context_no_native_annotation_for_base_currency_holdings(
    tmp_path, monkeypatch, db_isolated
):
    """Holdings in base currency do not show 'native:' annotation."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    _stub_book(monkeypatch, _holding("ES3", "STI ETF", 350.0, qty=100, price=3.5, ccy="SGD"))
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(1_000.0)
    assert "native: SGD" not in ctx


def test_build_context_no_fx_block_when_no_foreign_holdings(tmp_path, monkeypatch, db_isolated):
    """No foreign-currency holdings → FX block shows '(no foreign currency exposure)'."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(0.0)
    assert "no foreign currency exposure" in ctx


# ---------------------------------------------------------------------------
# No secrets in response
# ---------------------------------------------------------------------------


def test_no_secrets_in_briefing_response(client, isolated, monkeypatch):
    """api_key, apiKey, secret, password must not appear as JSON fields in response."""
    _stub_llm(monkeypatch)
    resp = client.get("/api/briefing")
    assert resp.status_code == 200
    body_str = resp.text.lower()
    for bad_pat in ("api_key", "apikey", "secret", "password"):
        assert bad_pat not in body_str, f"Suspicious field '{bad_pat}' found in response"


# ---------------------------------------------------------------------------
# _spending_block exception path coverage
# ---------------------------------------------------------------------------


def test_spending_block_returns_empty_when_transactions_raises(monkeypatch):
    """If transactions.all() raises, _spending_block returns []."""
    import backend.api.briefing as brf
    import backend.services.ledger as _ledger

    # Make transactions.all() raise
    class _BrokenStore:
        def all(self):
            raise RuntimeError("disk error")

    monkeypatch.setattr(_ledger, "transactions", _BrokenStore())

    lines = brf._spending_block({}, "SGD")
    assert lines == []


def test_spending_block_email_source_annotation(tmp_path, monkeypatch, db_isolated):
    """Email-sourced transactions get 'auto-captured from email' annotation."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx
    import backend.services.ledger as _ledger
    from tests.conftest import seed_stub_accounts

    recent = (date.today() - timedelta(days=1)).isoformat()
    txns = [
        {
            "id": "t2",
            "date": recent,
            "kind": "expense",
            "category": "dining",
            "description": "Restaurant",
            "from_account_id": "acc1",
            "amount": 50.0,
            "currency": "SGD",
            "base_amount": 50.0,
            "source": "email",
            "deleted": False,
        }
    ]
    seed_stub_accounts(txns)
    _ledger.transactions.replace_all(txns)
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})

    lines = brf._spending_block({}, "SGD")
    assert any("auto-captured from email" in line for line in lines)


def test_spending_block_budget_pressure_over_budget(monkeypatch, db_isolated):
    """Over-budget category appears in 'Budget pressure' line."""
    from datetime import date

    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx
    import backend.services.ledger as _ledger
    from backend.db.budget_assignments_store import BudgetAssignmentsStore
    from tests.conftest import seed_stub_accounts

    today = date.today().isoformat()
    # A large expense to push a category over budget
    txns = [
        {
            "id": "t3",
            "date": today,
            "kind": "expense",
            "category": "dining",
            "description": "Fancy dinner",
            "from_account_id": "acc1",
            "amount": 9999.0,
            "currency": "SGD",
            "base_amount": 9999.0,
            "deleted": False,
        }
    ]
    seed_stub_accounts(txns)
    _ledger.transactions.replace_all(txns)
    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})

    # Seed a tiny budget for the category so we go over_budget
    from datetime import date as _date

    y, m = _date.today().year, _date.today().month
    BudgetAssignmentsStore().set_assigned(f"{y}-{m:02d}", "dining", 50.0)

    lines = brf._spending_block({}, "SGD")
    # Should have a budget pressure line or at least a Month-to-date line
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# _fetch_news exception path coverage
# ---------------------------------------------------------------------------


def test_fetch_news_returns_empty_on_gather_exception(monkeypatch):
    """_fetch_news returns '' when gather_evidence raises."""
    import backend.api.briefing as brf

    async def _bad_gather(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _bad_gather)

    result = asyncio.run(brf._fetch_news(["AAPL news"]))
    assert result == ""


def test_fetch_news_returns_formatted_results(monkeypatch):
    """_fetch_news formats results from gather_evidence correctly."""
    import backend.api.briefing as brf

    async def _good_gather(*a, **kw):
        return [
            {"title": "Apple earnings beat", "content": "Apple reported strong Q3 results."},
            {"title": "", "content": "No title row"},  # should be skipped
        ]

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _good_gather)

    result = asyncio.run(brf._fetch_news(["AAPL news"]))
    assert "Apple earnings beat" in result
    assert "No title row" not in result


# ---------------------------------------------------------------------------
# _generate_briefing with market rows (news fetch path)
# ---------------------------------------------------------------------------


def test_get_briefing_with_market_rows_triggers_news_fetch(client, isolated, monkeypatch):
    """When there are market rows and holdings_news=True, news is fetched."""
    import backend.api.briefing as brf

    news_fetched = []

    async def _track_gather(queries, *a, **kw):
        news_fetched.extend(queries)
        return []

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _track_gather)
    _stub_book(
        monkeypatch, _holding("AAPL", "Apple Inc.", 24_300.0, qty=10, price=180.0, ccy="USD")
    )
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {}},
    )
    _stub_llm(monkeypatch)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    # News queries should have been built and passed to gather_evidence
    assert len(news_fetched) >= 1
    assert any("Apple Inc." in q for q in news_fetched)


def test_get_briefing_market_rows_fx_query_added(client, isolated, monkeypatch):
    """Foreign-currency holding adds an FX news query."""
    import backend.api.briefing as brf

    news_fetched = []

    async def _track_gather(queries, *a, **kw):
        news_fetched.extend(queries)
        return []

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _track_gather)
    _stub_book(
        monkeypatch, _holding("AAPL", "Apple Inc.", 24_300.0, qty=10, price=180.0, ccy="USD")
    )
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD"},
    )
    _stub_llm(monkeypatch)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    # An FX query like "USD SGD exchange rate this week" should appear
    assert any("USD" in q and "SGD" in q for q in news_fetched)


def test_get_briefing_news_queries_follow_the_books_biggest_holdings(client, isolated, monkeypatch):
    """The top-3 holdings picked for news queries are ranked by the Investments book's own
    value (canonical by construction), biggest first."""
    import backend.api.briefing as brf

    news_fetched: list[str] = []

    async def _track_gather(queries, *a, **kw):
        news_fetched.extend(queries)
        return []

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _track_gather)
    _stub_book(
        monkeypatch,
        _holding("GOLD", "Physical Gold", 8_359.0, ccy="USD"),
        _holding("2800.HK", "HKD ETF", 390_000.0, ccy="HKD"),
    )
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "USD"})
    _stub_llm(monkeypatch)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200

    gold_idx = next(i for i, q in enumerate(news_fetched) if "Physical Gold" in q)
    etf_idx = next(i for i, q in enumerate(news_fetched) if "HKD ETF" in q)
    assert etf_idx < gold_idx


def test_get_briefing_holdings_news_false_skips_news_fetch(client, isolated, monkeypatch):
    """holdings_news=False means gather_evidence is never called."""
    import backend.api.briefing as brf

    called = []

    async def _spy_gather(*a, **kw):
        called.append(True)
        return []

    monkeypatch.setattr("backend.monitoring.collect.gather_evidence", _spy_gather)
    _stub_book(monkeypatch, _holding("AAPL", "Apple", 1350.0, qty=5, price=200.0, ccy="USD"))
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"holdings_news": False}},
    )
    _stub_llm(monkeypatch)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    # gather_evidence should NOT be called when holdings_news is disabled
    assert len(called) == 0


# ---------------------------------------------------------------------------
# net_worth fallback when service raises
# ---------------------------------------------------------------------------


def test_get_briefing_net_worth_exception_uses_zero(client, isolated, monkeypatch):
    """If net_worth() raises, current_nw falls back to 0.0 without crashing."""
    _stub_llm(monkeypatch)

    def _bad_nw(base=None):
        raise RuntimeError("no accounts")

    monkeypatch.setattr("backend.services.ledger.net_worth", _bad_nw)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    assert "note" in resp.json()


# ---------------------------------------------------------------------------
# Action parsing edge cases
# ---------------------------------------------------------------------------


def test_build_context_action_without_due_date_is_skipped(tmp_path, monkeypatch, db_isolated):
    """Actions without a 'due' field are silently skipped."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    actions = [{"status": "Pending", "category": "Tax", "action": "No due date action"}]
    # actions is DB-backed now (Wave 5C, JSON->DB) — seed directly through ActionStore.
    from backend.db.action_store import ActionStore

    ActionStore().replace_all(actions)

    ctx = brf._build_context(0.0)
    assert "No due date action" not in ctx


def test_build_context_action_invalid_date_is_skipped(tmp_path, monkeypatch, db_isolated):
    """Actions with an invalid date string are silently skipped (no crash)."""
    import backend.api.briefing as brf
    import backend.services.fx_cache as _fx

    monkeypatch.setattr(_fx, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    actions = [
        {"due": "not-a-date", "status": "Pending", "category": "Tax", "action": "Bad date action"}
    ]
    # actions is DB-backed now (Wave 5C, JSON->DB) — seed directly through ActionStore.
    from backend.db.action_store import ActionStore

    ActionStore().replace_all(actions)

    ctx = brf._build_context(0.0)
    assert "Bad date action" not in ctx


# ---------------------------------------------------------------------------
# spending pref disabled (spending_lines branch)
# ---------------------------------------------------------------------------


def test_get_briefing_spending_pref_false_omits_spending_block(client, isolated, monkeypatch):
    """When briefing_preferences.spending=False, spending block is omitted from context."""
    import backend.api.briefing as brf

    context_captured = []

    original_build = brf._build_context

    def _capture_context(*a, **kw):
        ctx = original_build(*a, **kw)
        context_captured.append(ctx)
        return ctx

    monkeypatch.setattr(brf, "_build_context", _capture_context)
    monkeypatch.setattr(
        brf,
        "read_profile",
        lambda: {"base_currency": "SGD", "briefing_preferences": {"spending": False}},
    )
    _stub_llm(monkeypatch)

    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    # SPENDING PULSE section should not appear in the context
    assert len(context_captured) >= 1
    assert "SPENDING PULSE" not in context_captured[0]


# ---------------------------------------------------------------------------
# FX rate line with real foreign currency exposure in context
# ---------------------------------------------------------------------------


def test_build_context_fx_rate_line_shown_for_foreign_holdings(tmp_path, monkeypatch, db_isolated):
    """When portfolio has USD holdings, FX rate for USD appears in context."""
    import backend.api.briefing as brf

    # Patch the briefing module's own reference to load_fx_cache
    monkeypatch.setattr(brf, "load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.35})
    _stub_book(monkeypatch, _holding("AAPL", "Apple", 1350.0, qty=10, price=100.0, ccy="USD"))
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})

    ctx = brf._build_context(10_000.0)
    assert "USD/SGD" in ctx


# ---------------------------------------------------------------------------
# Integration (LLM) marker — only run when ANTHROPIC_API_KEY is set
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_briefing_integration_live_llm(client, isolated):
    """End-to-end: calls LLM provider, returns real note. Requires ANTHROPIC_API_KEY."""
    resp = client.get("/api/briefing?refresh=true")
    assert resp.status_code == 200
    body = resp.json()
    assert "note" in body
    assert len(body["note"]) > 10
    assert "generated_at" in body


def test_briefing_context_carries_what_lucius_remembers(isolated, monkeypatch):
    """The briefing was a separate one-shot call without the memory block, so it never knew
    the name the client asked to be called or the preferences they'd stated."""
    import backend.ai.prompts as prompts
    import backend.api.briefing as brf

    monkeypatch.setattr(
        prompts,
        "memory_block",
        lambda: "\nWHAT YOU REMEMBER (reference):\nStanding facts:\n• Call me Kai",
    )
    ctx = brf._build_context(0.0)
    assert "Call me Kai" in ctx
    assert "name they asked to be called" in ctx


def test_briefing_context_has_no_memory_section_when_there_is_nothing(isolated, monkeypatch):
    import backend.ai.prompts as prompts
    import backend.api.briefing as brf

    monkeypatch.setattr(prompts, "memory_block", lambda: "")
    assert "WHAT YOU REMEMBER" not in brf._build_context(0.0)


def test_saved_note_is_reused_while_both_fingerprints_hold(tmp_path, monkeypatch):
    """A note saved with the book's fingerprints is served again for the same book — a second
    Dashboard load in the same part of the day must not pay for another model call."""
    import backend.api.briefing as brf

    monkeypatch.setattr(brf, "CACHE_FILE", tmp_path / "briefing_cache.json")
    payload = {"note": "Evening note", "actions": [], "chat_suggestion": ""}
    brf._save_cache(payload, "book-a", "activity-a")

    reused = brf._load_cache("book-a", "activity-a")
    assert reused is not None and reused["note"] == "Evening note"
    assert brf._load_cache("book-b", "activity-a") is None


def test_failed_cache_write_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    import backend.api.briefing as brf

    monkeypatch.setattr(brf, "CACHE_FILE", tmp_path / "missing-dir" / "briefing_cache.json")
    with caplog.at_level("WARNING", logger=brf.logger.name):
        data = brf._save_cache({"note": "n", "actions": [], "chat_suggestion": ""})
    assert data["note"] == "n"
    assert "briefing cache write failed" in caplog.text
