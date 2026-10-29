"""The connector kind catalog: kinds, pausability, notify channels, secret fields and set-up rules."""

from __future__ import annotations

import pytest

from backend.services.connector_catalog import CATALOG


def test_catalog_lists_every_kind_the_migration_creates():
    assert set(CATALOG) == {
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
        "web_search",
        "mcp",
    }
    assert all(k.kind == key for key, k in CATALOG.items())


def test_only_the_price_sources_cannot_be_paused():
    assert {k for k, v in CATALOG.items() if not v.pausable} == {"yahoo_finance", "coingecko"}


def test_only_search_and_mcp_have_many_instances():
    assert {k for k, v in CATALOG.items() if v.multi} == {"web_search", "mcp"}


def test_notify_channels():
    assert {k: v.notify_channel for k, v in CATALOG.items() if v.notify_channel} == {
        "email": "email",
        "telegram": "telegram",
        "webhook": "webhook",
    }


@pytest.mark.parametrize(
    "kind, revealable, hidden",
    [
        ("email", {"password"}, set()),
        ("webhook", {"url", "secret"}, set()),
        ("plaid", {"secret"}, {"access_token", "item_id", "link_token", "items"}),
        ("lunch_flow", {"client_secret"}, {"access_token", "refresh_token"}),
        ("enable_banking", {"private_key"}, {"session_id"}),
        ("yodlee", {"secret", "password"}, {"cobrand_session_token", "user_session_token"}),
        ("simplefin", set(), {"access_url"}),
        ("coingecko", {"api_key"}, set()),
        ("web_search", {"api_key"}, set()),
        (
            "mcp",
            {"url", "command", "args", "env", "auth_header", "token"},
            {"oauth_client_secret", "oauth_access_token", "oauth_refresh_token"},
        ),
    ],
)
def test_machine_minted_secrets_are_not_revealable(kind, revealable, hidden):
    entry = CATALOG[kind]
    assert set(entry.revealable_fields) == revealable
    assert {f.name for f in entry.secret_fields} == revealable | hidden
    assert all(entry.secret(name) is not None for name in hidden)


def test_a_kind_without_secrets_has_none():
    assert CATALOG["yahoo_finance"].secret_fields == ()
    assert CATALOG["yahoo_finance"].secret("api_key") is None


def test_a_hidden_secret_is_never_surfaced_exported_or_revealable():
    """Telegram's chat→conversation map is stored with the bot token but is a different thing:
    no ``has_sessions`` flag, never carried by Move, never read back."""
    telegram = CATALOG["telegram"]
    assert telegram.secret("sessions").hidden is True
    for names in (telegram.surfaced_fields, telegram.exported_fields, telegram.revealable_fields):
        assert "sessions" not in names
        assert "bot_token" in names


def test_minted_secrets_are_surfaced_and_moved_but_never_revealed():
    plaid = CATALOG["plaid"]
    assert "access_token" in plaid.surfaced_fields
    assert "access_token" in plaid.exported_fields
    assert "access_token" not in plaid.revealable_fields


def test_builtins_are_one_per_single_kind_and_one_per_search_provider():
    from backend.services.connector_catalog import builtins

    ids = [b.id for b in builtins()]
    assert len(ids) == len(set(ids))
    single = [k for k, v in CATALOG.items() if not v.multi]
    assert ids[: len(single)] == single
    assert [i for i in ids if i.startswith("web_search:")] == [
        f"web_search:{p}"
        for p in ("duckduckgo", "tavily", "perplexity", "serper", "google_pse", "firecrawl")
    ]
    assert "mcp" not in ids


def test_kind_of_maps_every_id_family():
    from backend.services.connector_catalog import kind_of

    assert kind_of("plaid") is CATALOG["plaid"]
    assert kind_of("web_search:tavily") is CATALOG["web_search"]
    assert kind_of("mcp:github") is CATALOG["mcp"]
    assert kind_of("web_search") is None  # a kind name, not an instance
    assert kind_of("nope") is None


def _row(kind: str, config: dict | None = None, **row) -> dict:
    return {
        "id": kind,
        "kind": kind,
        "name": kind,
        "enabled": True,
        "config": config or {},
        "state": {},
        **row,
    }


@pytest.mark.parametrize(
    "kind, config, have, expected",
    [
        ("plaid", {"client_id": "c"}, {"secret", "access_token"}, True),
        ("plaid", {"client_id": "c"}, {"secret"}, False),  # no linked Item yet
        ("plaid", {}, {"secret", "access_token"}, False),  # no client id
        ("yodlee", {"client_id": "c", "username": "u"}, {"secret", "password"}, True),
        ("yodlee", {"client_id": "c"}, {"secret", "password"}, False),
        ("ibkr", {"flex_query_id": "1"}, {"flex_token"}, True),
        ("ibkr", {}, {"flex_token"}, False),
        ("enable_banking", {"application_id": "a"}, {"private_key", "session_id"}, True),
        ("enable_banking", {"application_id": "a"}, {"private_key"}, False),
        ("alpaca", {}, {"api_key_id", "secret_key"}, True),
        ("alpaca", {}, {"api_key_id"}, False),
        ("webhook", {}, {"url"}, True),
        ("webhook", {}, {"secret"}, False),
        ("telegram", {}, {"bot_token"}, True),
        ("telegram", {}, set(), False),
        ("email", {"address": "a@x", "imap_host": "h"}, {"password"}, True),
        ("email", {"address": "a@x", "smtp_host": "h"}, {"password"}, True),
        ("email", {"address": "a@x"}, {"password"}, False),
        ("email", {"imap_host": "h"}, {"password"}, False),
        ("yahoo_finance", {}, set(), True),
        ("coingecko", {}, set(), True),
        ("mcp", {"transport": "http"}, {"url"}, True),
        ("mcp", {"transport": "stdio"}, {"url"}, False),
        ("mcp", {"transport": "stdio"}, {"command"}, True),
    ],
)
def test_the_set_up_rule_of_each_kind(kind, config, have, expected):
    assert CATALOG[kind].configured(_row(kind, config), frozenset(have)) is expected


def test_the_web_search_set_up_rule_follows_whether_the_provider_needs_a_key():
    configured = CATALOG["web_search"].configured
    ddg = _row("web_search", id="web_search:duckduckgo")
    tavily = _row("web_search", id="web_search:tavily")
    assert configured(ddg, frozenset()) is True
    assert configured(tavily, frozenset()) is False
    assert configured(tavily, frozenset({"api_key"})) is True


def test_every_secret_a_facade_store_writes_is_declared_by_its_kind(db_isolated):
    from backend.connectors import session_map
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.connector_store import ConnectorStore
    from backend.db.email_config_store import EmailConfigStore
    from backend.db.market_data_config_store import MarketDataConfigStore
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.search_config_store import SearchConfigStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.models.mcp_client import McpOAuth, McpServerConfig

    EmailConfigStore().update({"password": "p", "address": "a@x"})
    ChannelConfigStore().update({"url": "https://h.test", "secret": "s"})
    TelegramConfigStore().update({"bot_token": "123:abc"})
    session_map.get_or_create_session("telegram", "1")
    SearchConfigStore().update_provider("tavily", {"api_key": "k"})
    MarketDataConfigStore().update({"coingecko_api_key": "k"})
    McpClientStore().create(
        McpServerConfig(
            id="srv",
            url="https://x.test",
            command="npx",
            args=["a"],
            env={"K": "v"},
            token="t",
            auth_header="X-Key",
            auth_type="oauth",
            oauth=McpOAuth(
                issuer="https://i.test",
                resource="https://x.test",
                authorization_endpoint="https://i.test/a",
                token_endpoint="https://i.test/t",
                client_id="c",
                client_secret="cs",
                access_token="at",
                refresh_token="rt",
            ),
        )
    )

    store = ConnectorStore()
    for connector_id, kind in [
        ("email", "email"),
        ("webhook", "webhook"),
        ("telegram", "telegram"),
        ("web_search:tavily", "web_search"),
        ("coingecko", "coingecko"),
        ("mcp:srv", "mcp"),
    ]:
        stored = set(store.secrets(connector_id))
        assert stored, connector_id
        assert stored <= {f.name for f in CATALOG[kind].secret_fields}, connector_id
    assert "sessions" in store.secrets("telegram")


def test_each_kinds_config_keys_are_the_settings_its_store_writes_to_config():
    """``config_keys`` is what Move keeps from a received row. It must name exactly the
    settings the kind's facade store files under ``connectors.config`` — a key missing here is
    silently dropped by a move, a key extra here is a field nothing reads."""
    from backend.db.alpaca_config_store import AlpacaConfigStore
    from backend.db.channel_config_store import DEFAULTS as WEBHOOK_DEFAULTS
    from backend.db.email_config_store import DEFAULTS as EMAIL_DEFAULTS
    from backend.db.enable_banking_config_store import EnableBankingConfigStore
    from backend.db.ibkr_config_store import IbkrConfigStore
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore
    from backend.db.plaid_config_store import PlaidConfigStore
    from backend.db.simplefin_config_store import SimpleFinConfigStore
    from backend.db.telegram_config_store import CONFIG_DEFAULTS as TELEGRAM_DEFAULTS
    from backend.db.ynab_config_store import YnabConfigStore
    from backend.db.yodlee_config_store import YodleeConfigStore

    for store in (
        SimpleFinConfigStore,
        PlaidConfigStore,
        LunchMoneyConfigStore,
        LunchFlowConfigStore,
        YnabConfigStore,
        EnableBankingConfigStore,
        YodleeConfigStore,
        AlpacaConfigStore,
        IbkrConfigStore,
    ):
        assert set(CATALOG[store.KIND].config_keys) == set(store.CONFIG_KEYS), store.KIND
    assert set(CATALOG["email"].config_keys) == set(EMAIL_DEFAULTS)
    assert set(CATALOG["webhook"].config_keys) == set(WEBHOOK_DEFAULTS)
    assert set(CATALOG["telegram"].config_keys) == set(TELEGRAM_DEFAULTS)
    assert set(CATALOG["web_search"].config_keys) == {"provider", "default", "cx"}
    assert CATALOG["coingecko"].config_keys == ("tier",)
    assert CATALOG["yahoo_finance"].config_keys == ("primary_for_major_crypto",)


def test_a_token_minted_by_the_senders_authorization_is_never_moved():
    flow = CATALOG["lunch_flow"]
    assert flow.exported_fields == ("client_secret",)
    assert {"access_token", "refresh_token"} <= set(flow.surfaced_fields)
    assert flow.moved_state == ()


def test_only_the_retired_connectors_are_withdrawn_by_policy():
    """Yodlee (enterprise-only pricing), IBKR Flex and the Alpaca data source (both retired
    2026-10-29 for the vendor's own MCP server) — code kept, none offered."""
    assert {k for k, v in CATALOG.items() if v.disabled} == {"yodlee", "ibkr", "alpaca"}
    from backend.ai.tools._connector_gate import DISABLED_CONNECTORS
    from backend.services.aggregation.providers import PROVIDER_KEYS, PROVIDERS

    assert DISABLED_CONNECTORS == frozenset({"yodlee", "ibkr", "alpaca"})
    assert "yodlee" in PROVIDERS and "yodlee" not in PROVIDER_KEYS
    assert "ibkr" in PROVIDERS and "ibkr" not in PROVIDER_KEYS
    assert "alpaca" in PROVIDERS and "alpaca" not in PROVIDER_KEYS
