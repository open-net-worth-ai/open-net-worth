"""REST smoke for the unified connectors API.

TestClient on a bare app with ONLY connectors_api.router mounted (no dependency on
backend.main), against an isolated database. Verifies the list/get shape, the
secret-never-returned invariant, and enable/disable/remove delegation.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):
    # PATCH/DELETE now best-effort hot-reload the live Telegram bot for connector_id ==
    # "telegram" (see connectors_api._reload_telegram_if_needed) — a non-primary boot
    # never runs the poller, which is exactly what a test process is. Without this a
    # telegram-enabling test here would spawn a real background connect-with-retry
    # supervisor against api.telegram.org with a fake token (test_telegram_bot_lifecycle.py
    # covers that lifecycle directly, with `bot._connect` mocked — this file is not that).
    monkeypatch.setenv("ONW_DISABLE_TELEGRAM", "1")

    app = FastAPI()
    from backend.api.connectors_api import router

    app.include_router(router)
    return TestClient(app)


def test_list_has_builtins_never_500(client):
    res = client.get("/api/connectors")
    assert res.status_code == 200
    ids = {c["id"] for c in res.json()["connectors"]}
    web_search_ids = {i for i in ids if i.startswith("web_search:")}
    assert len(web_search_ids) == 6  # one card per web-search provider (incl. key-free DuckDuckGo)
    assert ids - web_search_ids == {
        "email",
        "webhook",
        "telegram",
        "simplefin",
        "plaid",
        "lunch_money",
        "lunch_flow",
        "ynab",
        "enable_banking",
        "yodlee",
        "alpaca",
        "ibkr",
        "yahoo_finance",
        "coingecko",
    }


def test_get_email_is_one_connector_with_one_secret_flag_only(client):
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "imap_host": "imap.x",
            "smtp_host": "smtp.x",
            "address": "u@x.com",
            "password": "the-secret",
        }
    )

    res = client.get("/api/connectors/email")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "email"
    assert body["secrets_public"] == {"has_password": True}
    assert body["config"]["address"] == "u@x.com"
    assert not {"capabilities", "managed_by", "transport", "provider_kind"} & set(body)
    assert "the-secret" not in json.dumps(body)


def test_get_unknown_is_404(client):
    res = client.get("/api/connectors/does-not-exist")
    assert res.status_code == 404


def test_patch_enable_delegates_and_returns_view(client):
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"url": "https://hook.x"})
    res = client.patch("/api/connectors/webhook", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["enabled"] is True
    assert ChannelConfigStore().get()["enabled"] is True


def test_patch_enables_and_disables_email_as_a_whole(client):
    """Email has ONE enabled flag: the whole provider is on or off."""
    res = client.patch("/api/connectors/email", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["enabled"] is True

    res = client.patch("/api/connectors/email", json={"enabled": False})
    assert res.json()["enabled"] is False


def test_patch_accepts_enabled_only(client):
    """The per-capability toggle is gone: an unknown body field is simply not part of the model."""
    res = client.patch("/api/connectors/email", json={"capability": "read"})
    assert res.status_code == 422  # `enabled` is required


def test_patch_missing_enabled_is_422(client):
    res = client.patch("/api/connectors/webhook", json={})
    assert res.status_code == 422


def test_patch_telegram_enable_delegates_to_store(client):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "123:abc"})
    res = client.patch("/api/connectors/telegram", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["enabled"] is True
    assert TelegramConfigStore().get()["enabled"] is True
    # no token leak in the view
    assert "123:abc" not in json.dumps(res.json())


def test_patch_yodlee_enable_still_works_via_rest_despite_ai_tool_refusal(client):
    """The DISABLED_CONNECTORS refusal added to set_connector_enabled
    (backend/ai/tools/connectors.py) is scoped to the AI-TOOL path only — this route
    hits services.connectors.set_connector_enabled directly, the same shared function,
    and must stay fully unaffected. Per the ADR's own stated intent ("already-configured
    instances kept visible... a human choosing to re-enable something they already set
    up is a different, legitimate action from the AI doing it unprompted"), a human
    operator using Settings/Connectors can still flip Yodlee on."""
    res = client.patch("/api/connectors/yodlee", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["enabled"] is True


def test_get_mcp_connector_by_path_id(client):
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test", enabled=True))
    res = client.get("/api/connectors/mcp:srv")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "mcp"
    assert body["config"]["transport"] == "http"


def test_detail_fields_ride_the_view(client):
    """The presentational short/long_description + tools fields serialize on the unified view
    (detail-view lane) — no extra endpoint/fetch."""
    res = client.get("/api/connectors/web_search:tavily")
    assert res.status_code == 200
    body = res.json()
    assert body["short_description"]
    assert body["long_description"]
    assert [t["name"] for t in body["tools"]] == ["tavily_search", "tavily_fetch_page"]
    assert all(t["source"] == "native" for t in body["tools"])
    # telegram is a channel connector — its own SEND tool, RECEIVE stays transport-only
    tg = client.get("/api/connectors/telegram").json()
    assert [t["name"] for t in tg["tools"]] == ["telegram_send_message"]
    assert tg["short_description"]


# ── DELETE /api/connectors/{id} — a real, credential-clearing remove ─────────────


def test_delete_native_connector_clears_credentials_and_disables(client):
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"enabled": True, "client_id": "cid", "secret": "shh", "access_token": "tok"})

    res = client.delete("/api/connectors/plaid")
    assert res.status_code == 200
    assert res.json() == {"deleted": "plaid"}

    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["secret"] == ""
    assert cfg["access_token"] == ""
    assert client.get("/api/connectors/plaid").json()["secrets_public"]["has_secret"] is False


def test_delete_unknown_connector_is_404(client):
    res = client.delete("/api/connectors/does-not-exist")
    assert res.status_code == 404


def test_patch_always_on_connector_is_422(client):
    res = client.patch("/api/connectors/yahoo_finance", json={"enabled": False})
    assert res.status_code == 422
    assert client.get("/api/connectors/yahoo_finance").json()["enabled"] is True


def test_delete_yahoo_finance_is_422_no_credential_to_clear(client):
    res = client.delete("/api/connectors/yahoo_finance")
    assert res.status_code == 422
    assert "powers price lookups" in res.json()["detail"]


def test_delete_coingecko_is_422_too_even_though_it_holds_a_key(client):
    """Reversed 2026-08-22: CoinGecko used to let Remove clear just its saved key. It keeps
    running unconditionally like Yahoo Finance, so Remove refuses for both — the saved key
    survives untouched, and the refusal names Edit configuration as the real path."""
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update({"coingecko_api_key": "cg-secret"})

    res = client.delete("/api/connectors/coingecko")
    assert res.status_code == 422
    assert "Edit configuration" in res.json()["detail"]
    assert client.get("/api/connectors/coingecko").json()["secrets_public"] == {"has_api_key": True}


def test_delete_email_erases_the_row_and_its_password(client):
    from backend.db.connector_store import ConnectorStore
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "enabled": True,
            "imap_host": "imap.x",
            "smtp_host": "smtp.x",
            "address": "u@x.com",
            "password": "the-shh",
        }
    )

    res = client.delete("/api/connectors/email")
    assert res.status_code == 200
    assert res.json() == {"deleted": "email"}

    assert ConnectorStore().get("email") is None
    assert ConnectorStore().secrets("email") == {}
    assert EmailConfigStore().get()["enabled"] is False
    assert EmailConfigStore().credentials(require_enabled=False) is None
    assert client.get("/api/connectors/email").json()["secrets_public"] == {"has_password": False}


def test_delete_webhook_clears_its_url_and_secret(client):
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"enabled": True, "url": "https://hook.x", "secret": "whsec"})

    res = client.delete("/api/connectors/webhook")
    assert res.status_code == 200

    cfg = ChannelConfigStore().get()
    assert cfg["enabled"] is False
    assert cfg["secret"] == ""
    assert cfg["url"] == ""
    assert client.get("/api/connectors/webhook").json()["secrets_public"] == {
        "has_url": False,
        "has_secret": False,
    }


def test_delete_web_search_provider_clears_just_that_one(client):
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    store.update_provider("tavily", {"enabled": True, "api_key": "tavily-key"})
    store.update_provider("serper", {"enabled": True, "api_key": "serper-key"})

    res = client.delete("/api/connectors/web_search:tavily")
    assert res.status_code == 200
    assert res.json() == {"deleted": "web_search:tavily"}

    cfg = store.get()
    assert cfg["providers"]["tavily"]["enabled"] is False
    assert cfg["providers"]["tavily"]["api_key"] == ""
    # the other provider is untouched
    assert cfg["providers"]["serper"]["enabled"] is True
    assert cfg["providers"]["serper"]["api_key"] == "serper-key"


def test_delete_web_search_unknown_provider_is_404(client):
    res = client.delete("/api/connectors/web_search:not-a-real-provider")
    assert res.status_code == 404


def test_delete_mcp_connector_via_generic_route_delegates_to_remove_mcp_server(client):
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test", enabled=True))
    res = client.delete("/api/connectors/mcp:srv")
    assert res.status_code == 200
    assert McpClientStore().find("srv") is None


# ── the Telegram bot reload (PATCH enable/disable + DELETE) ──────────────────────
#
# `_reload_telegram_if_needed` is exercised here with `reload_best_effort` MOCKED — the
# real one is covered by test_telegram_bot_lifecycle.py, with `bot._connect` mocked
# instead, so nothing in this suite ever opens a real socket to api.telegram.org.


@pytest.fixture
def reload_spy(monkeypatch):
    import backend.connectors.telegram.bot as telegram_bot

    calls: list[str] = []

    async def fake_reload_best_effort() -> bool:
        calls.append("reloaded")
        return True

    monkeypatch.setattr(telegram_bot, "reload_best_effort", fake_reload_best_effort)
    return calls


def test_patch_telegram_enable_triggers_reload(client, reload_spy):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "123:abc"})
    res = client.patch("/api/connectors/telegram", json={"enabled": True})
    assert res.status_code == 200
    assert reload_spy == ["reloaded"]


def test_patch_telegram_disable_also_triggers_reload(client, reload_spy):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "123:abc", "enabled": True})
    res = client.patch("/api/connectors/telegram", json={"enabled": False})
    assert res.status_code == 200
    assert reload_spy == ["reloaded"]


def test_patch_telegram_disable_reports_reloaded_false_when_the_bot_didnt_confirm(
    client, monkeypatch
):
    """Finding 5 (PATCH half): the response threads through whatever
    ``reload_best_effort`` actually reports — a caller must be able to tell a
    stale-but-200 response apart from a genuinely reloaded one."""
    import backend.connectors.telegram.bot as telegram_bot
    from backend.db.telegram_config_store import TelegramConfigStore

    async def fake_reload_best_effort() -> bool:
        return False

    monkeypatch.setattr(telegram_bot, "reload_best_effort", fake_reload_best_effort)

    TelegramConfigStore().update({"bot_token": "123:abc", "enabled": True})
    res = client.patch("/api/connectors/telegram", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["reloaded"] is False


# ── DELETE /api/connectors/telegram — stop-before-reset ordering (security review
# 2026-08-17, findings 1/2a) ─────────────────────────────────────────────────────
#
# The DELETE route no longer goes through `reload_best_effort` at all for Telegram —
# it calls `bot.stop()` DIRECTLY and gates the credential reset on its result, so
# these mock `bot.stop()` rather than reusing `reload_spy` (which mocks the
# unrelated `reload_best_effort`).


def test_delete_telegram_stops_the_bot_first_then_clears_credentials(client, monkeypatch):
    import backend.connectors.telegram.bot as telegram_bot
    from backend.db.telegram_config_store import TelegramConfigStore

    stop_calls: list[str] = []

    async def fake_stop() -> bool:
        stop_calls.append("stopped")
        return True

    monkeypatch.setattr(telegram_bot, "stop", fake_stop)

    TelegramConfigStore().update({"bot_token": "123:abc", "enabled": True})
    res = client.delete("/api/connectors/telegram")
    assert res.status_code == 200
    assert res.json()["reloaded"] is True
    assert stop_calls == ["stopped"]
    assert TelegramConfigStore().get()["bot_token"] == ""


def test_delete_telegram_refuses_and_keeps_credentials_when_stop_cannot_be_confirmed(
    client, monkeypatch
):
    """Finding 2(a): if `bot.stop()` can't confirm the live process is down, the
    credential reset must NOT proceed — erasing the token first would risk a
    still-live bot losing its allowlist while it keeps running unreachable."""
    import backend.connectors.telegram.bot as telegram_bot
    from backend.db.telegram_config_store import TelegramConfigStore

    async def fake_stop() -> bool:
        return False

    monkeypatch.setattr(telegram_bot, "stop", fake_stop)

    TelegramConfigStore().update({"bot_token": "123:abc", "enabled": True})
    res = client.delete("/api/connectors/telegram")
    assert res.status_code == 502
    # The credential was NOT wiped — the whole point of the ordering fix.
    assert TelegramConfigStore().get()["bot_token"] == "123:abc"
    assert TelegramConfigStore().get()["enabled"] is True


def test_patch_other_connector_never_touches_the_telegram_bot(client, reload_spy):
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"url": "https://hook.x"})
    res = client.patch("/api/connectors/webhook", json={"enabled": True})
    assert res.status_code == 200
    assert reload_spy == []


def test_delete_reachable_through_the_full_app_not_shadowed_by_permissions_routes(
    db_isolated, monkeypatch
):
    """The new DELETE route is mounted on the SAME ``connectors_router`` object as the
    already-pinned GET/PATCH (``test_tool_permissions_routes.py``'s own regression guard),
    registered on the real app AFTER the connector-scoped permissions/secret routers — so it
    inherits their ordering fix automatically. Proven here against ``backend.main.app``
    itself, not just this file's bare-router fixture, since a route can behave differently
    once every other router is mounted alongside it."""
    monkeypatch.setenv("ONW_DISABLE_TELEGRAM", "1")
    from backend.db.plaid_config_store import PlaidConfigStore
    from backend.main import app

    PlaidConfigStore().update({"enabled": True, "secret": "shh"})
    full_client = TestClient(app)

    res = full_client.delete("/api/connectors/plaid")
    assert res.status_code == 200
    assert PlaidConfigStore().get()["secret"] == ""

    # The connector-scoped permissions DELETE (a DIFFERENT path shape — trailing
    # /permissions) must still resolve to ITS OWN handler, not this new bare-id route.
    perm_res = full_client.delete("/api/connectors/plaid/permissions")
    assert perm_res.status_code == 200
    assert "default_level" in perm_res.json()


# ── GET /api/connectors/{id}/accounts (RFC connectors-as-data-sources §E) ──────────


def test_get_connector_accounts_404_for_unknown_connector(client):
    res = client.get("/api/connectors/not-a-real-provider/accounts")
    assert res.status_code == 404


def test_get_connector_accounts_empty_when_nothing_linked(client):
    """Plaid is unconfigured in this fixture (no client_id/secret) -- the route must say
    so honestly (C-B2: not_configured, not a bare "ok" with an empty list a caller could
    mistake for "configured but genuinely has zero accounts")."""
    res = client.get("/api/connectors/plaid/accounts")
    assert res.status_code == 200
    assert res.json() == {
        "status": "not_configured",
        "provider_label": "Plaid",
        "accounts": [],
    }


def test_get_connector_accounts_returns_the_linked_block_even_when_not_configured(client):
    """C-B2: an account linked before the provider went quiet (here: never configured at
    all) still shows up in the picker -- the LINKED view is ALWAYS merged in, regardless
    of provider status."""
    from backend.services import ledger as svc

    svc.accounts.insert(
        {
            "id": "acc_1",
            "name": "Checking",
            "kind": "asset",
            "currency": "USD",
            "external_ids": [
                {
                    "source": "plaid",
                    "ref": "plaid-acc-1",
                    "name": "Plaid Checking",
                    "mask": "4242",
                    "linked_at": "2026-09-01T00:00:00+00:00",
                }
            ],
        }
    )

    res = client.get("/api/connectors/plaid/accounts")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "not_configured"
    assert body["provider_label"] == "Plaid"
    assert body["accounts"] == [
        {
            "provider_account_id": "plaid-acc-1",
            "name": "Plaid Checking",
            "mask": "4242",
            "currency": None,
            "balance": None,
            "account_type": None,
            "linked_account_id": "acc_1",
        }
    ]


def test_get_connector_accounts_ok_status_passes_through_the_service_result(client, monkeypatch):
    """The route is a thin pass-through onto services.connector_fetch.provider_accounts --
    when the provider call succeeds, its own accounts (with linked_account_id already
    resolved from Account.external_ids, and provider_label) reach the caller unchanged."""
    from backend.services import connector_fetch

    def _fake_provider_accounts(connector_id):
        assert connector_id == "plaid"
        return {
            "status": "ok",
            "provider_label": "Plaid",
            "accounts": [
                {
                    "provider_account_id": "plaid-acc-1",
                    "name": "Checking",
                    "mask": "4242",
                    "currency": "USD",
                    "balance": "1000.00",
                    "linked_account_id": "acc_1",
                },
                {
                    "provider_account_id": "plaid-acc-2",
                    "name": "Savings",
                    "mask": "9999",
                    "currency": "USD",
                    "balance": "500.00",
                    "linked_account_id": None,
                },
            ],
        }

    monkeypatch.setattr(connector_fetch, "provider_accounts", _fake_provider_accounts)

    res = client.get("/api/connectors/plaid/accounts")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["provider_label"] == "Plaid"
    assert [a["provider_account_id"] for a in body["accounts"]] == [
        "plaid-acc-1",
        "plaid-acc-2",
    ]
    assert body["accounts"][0]["linked_account_id"] == "acc_1"
    assert body["accounts"][1]["linked_account_id"] is None


def test_get_connector_accounts_route_wins_over_the_generic_connector_id_route(client):
    """Regression guard for the registration-order fix: `/plaid/accounts` must NOT be
    swallowed into `GET /{connector_id:path}` as connector_id="plaid/accounts"."""
    res = client.get("/api/connectors/plaid/accounts")
    assert res.status_code == 200
    assert "accounts" in res.json()
