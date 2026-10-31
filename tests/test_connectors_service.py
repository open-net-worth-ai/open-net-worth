"""The connectors service: the list built from the ``connectors`` table + the catalog.

The view is rendered by ONE generic builder from a connector's row and its catalog kind. These
tests pin that the view faithfully reflects the rows (through the facade stores that write
them), that NO secret value leaks (only ``has_<field>`` flags), and that Pause/Resume/Remove
act on the row and clear what they must.
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import NotFoundError, ValidationError


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    """Every test runs against a fresh, migrated, isolated database."""
    return db_isolated


def _svc():
    from backend.services import connectors as connectors_service

    return connectors_service


# ── default / empty state ──────────────────────────────────────────────────────


def test_empty_state_has_builtin_transports_no_mcp():
    svc = _svc()
    views = svc.list_connectors()
    ids = {v.id for v in views}
    # Email + webhook + telegram + simplefin + plaid + lunch_money + lunch_flow + ynab +
    # enable_banking + yodlee + alpaca + ibkr + yahoo_finance + coingecko are always-present
    # built-in transports; web search is ONE connector PER provider ("web_search:<id>", 6 of
    # them: DuckDuckGo, Tavily, Perplexity, Serper, Google PSE, Firecrawl); no servers → no
    # mcp connector.
    web_search_ids = {i for i in ids if i.startswith("web_search:")}
    assert len(web_search_ids) == 6
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
    email = next(v for v in views if v.id == "email")
    assert email.kind == "email"
    assert email.enabled is False
    assert email.status == "not_configured"
    assert email.error is None
    assert email.pausable is True
    assert email.secrets_public == {"has_password": False}
    # Telegram is present even with no token, disabled, not configured. Its hidden chat map
    # (``sessions``) is never reported as a flag.
    tg = next(v for v in views if v.id == "telegram")
    assert tg.enabled is False
    assert tg.status == "not_configured"
    assert tg.secrets_public == {"has_bot_token": False}
    sf = next(v for v in views if v.id == "simplefin")
    assert sf.kind == "simplefin"
    assert sf.status == "not_configured"
    assert sf.secrets_public == {"has_access_url": False}
    pl = next(v for v in views if v.id == "plaid")
    assert pl.kind == "plaid"
    assert pl.status == "not_configured"
    assert pl.secrets_public == {
        "has_secret": False,
        "has_access_token": False,
        "has_item_id": False,
        "has_link_token": False,
        "has_items": False,
    }
    eb = next(v for v in views if v.id == "enable_banking")
    assert eb.kind == "enable_banking"
    assert eb.status == "not_configured"
    assert eb.secrets_public == {"has_private_key": False, "has_session_id": False}
    # The price sources are always present, always on, and cannot be paused.
    for cid in ("yahoo_finance", "coingecko"):
        view = next(v for v in views if v.id == cid)
        assert (view.enabled, view.pausable, view.status) == (True, False, "active")
    # DuckDuckGo is on without a row; a keyed provider is off until it has a key.
    ddg = next(v for v in views if v.id == "web_search:duckduckgo")
    assert ddg.enabled is True and ddg.status == "active"
    assert next(v for v in views if v.id == "web_search:tavily").enabled is False


# ── SimpleFIN connector view + enable delegation ─────────────────────────────────


def test_simplefin_view_reflects_store_and_hides_access_url():
    svc = _svc()
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    SimpleFinConfigStore().update(
        {"enabled": True, "access_url": "https://user:hunter2-creds@bridge.example/sf"}
    )
    sf = svc.get_connector("simplefin")
    assert sf.enabled is True
    assert sf.status == "active"
    assert sf.secrets_public == {"has_access_url": True}
    # No secret VALUE (the access URL, embedded creds included) leaks anywhere in the view.
    dumped = json.dumps(sf.model_dump())
    assert "hunter2-creds" not in dumped
    assert "bridge.example" not in dumped


def test_simplefin_enable_disable_delegates_to_store():
    svc = _svc()
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    SimpleFinConfigStore().update({"access_url": "https://u:p@bridge.example/sf"})
    svc.set_connector_enabled("simplefin", True)
    assert SimpleFinConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("simplefin", False)
    assert SimpleFinConfigStore().get()["enabled"] is False


# ── Plaid connector view + enable delegation ─────────────────────────────────────


def test_plaid_view_reflects_store_and_hides_secret():
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "hunter2-secret",
            "access_token": "tok-cr3ds",
        }
    )
    pl = svc.get_connector("plaid")
    assert pl.enabled is True
    assert pl.status == "active"
    assert pl.secrets_public == {
        "has_secret": True,
        "has_access_token": True,
        "has_item_id": False,
        "has_link_token": False,
        "has_items": False,
    }
    assert pl.config["client_id"] == "cid"
    # No secret VALUE (secret / access token) leaks anywhere in the serialized view.
    dumped = json.dumps(pl.model_dump())
    assert "hunter2-secret" not in dumped
    assert "tok-cr3ds" not in dumped


def test_plaid_enable_disable_delegates_to_store():
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "cid", "secret": "skey", "access_token": "tok-1"})
    svc.set_connector_enabled("plaid", True)
    assert PlaidConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("plaid", False)
    assert PlaidConfigStore().get()["enabled"] is False


# ── Lunch Money connector view + enable delegation ────────────────────────────────


def test_lunch_money_view_reflects_store_and_hides_token():
    svc = _svc()
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore

    LunchMoneyConfigStore().update({"enabled": True, "access_token": "lm-tok-cr3ds"})
    lm = svc.get_connector("lunch_money")
    assert lm.kind == "lunch_money"
    assert lm.pausable is True
    assert lm.enabled is True
    assert lm.status == "active"
    assert lm.secrets_public == {"has_access_token": True}
    # No secret VALUE leaks anywhere in the serialized view.
    dumped = json.dumps(lm.model_dump())
    assert "lm-tok-cr3ds" not in dumped


def test_lunch_money_enable_disable_delegates_to_store():
    svc = _svc()
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore

    LunchMoneyConfigStore().update({"access_token": "lm-tok"})
    svc.set_connector_enabled("lunch_money", True)
    assert LunchMoneyConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("lunch_money", False)
    assert LunchMoneyConfigStore().get()["enabled"] is False


# ── YNAB connector view + enable delegation ────────────────────────────────────────


def test_ynab_view_reflects_store_and_hides_token():
    svc = _svc()
    from backend.db.ynab_config_store import YnabConfigStore

    YnabConfigStore().update(
        {
            "enabled": True,
            "access_token": "ynab-tok-cr3ds",
            "budget_id": "b1",
            "budget_name": "Personal",
        }
    )
    yn = svc.get_connector("ynab")
    assert yn.kind == "ynab"
    assert yn.pausable is True
    assert yn.enabled is True
    assert yn.status == "active"
    assert yn.secrets_public == {"has_access_token": True}
    assert yn.config["budget_id"] == "b1"
    assert yn.config["budget_name"] == "Personal"
    # No secret VALUE leaks anywhere in the serialized view.
    dumped = json.dumps(yn.model_dump())
    assert "ynab-tok-cr3ds" not in dumped


def test_ynab_enable_disable_delegates_to_store():
    svc = _svc()
    from backend.db.ynab_config_store import YnabConfigStore

    YnabConfigStore().update({"access_token": "ynab-tok"})
    svc.set_connector_enabled("ynab", True)
    assert YnabConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("ynab", False)
    assert YnabConfigStore().get()["enabled"] is False


# ── email = ONE connector: one row, one flag, one password ───────────────────────


def test_email_is_one_connector_with_one_enabled_flag_and_one_secret_flag():
    svc = _svc()
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "enabled": True,
            "address": "me@example.com",
            "password": "the-secret",
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
        }
    )

    email = svc.get_connector("email")
    assert email.config["imap_host"] == "imap.example.com"
    assert email.config["smtp_host"] == "smtp.example.com"
    assert email.config["address"] == "me@example.com"
    assert email.secrets_public == {"has_password": True}
    assert email.enabled is True
    assert email.status == "active"

    # NO secret value leaks anywhere in the serialized view.
    assert "the-secret" not in json.dumps(email.model_dump())


def test_a_paused_email_with_saved_credentials_is_configured_not_unconfigured():
    svc = _svc()
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {"enabled": False, "address": "me@example.com", "password": "p", "imap_host": "imap.x"}
    )
    email = svc.get_connector("email")
    assert email.enabled is False
    assert email.status == "configured"


# ── webhook ──────────────────────────────────────────────────────────────────────


def test_webhook_connector_secret_flags_and_no_url_in_the_view():
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update(
        {"enabled": True, "url": "https://hook.example.com/T0/xyz", "secret": "hook-secret"}
    )
    wh = svc.get_connector("webhook")
    assert wh.enabled is True
    assert not {"url", "secret"} & set(wh.config)
    assert wh.secrets_public == {"has_url": True, "has_secret": True}
    blob = json.dumps(wh.model_dump())
    assert "hook-secret" not in blob
    assert "hook.example.com" not in blob


# ── one connector per MCP server ─────────────────────────────────────────────────


def test_mcp_server_becomes_a_connector_with_its_screen_fields_and_no_token_leak():
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="gmail",
            name="Gmail MCP",
            url="https://mcp.example.com",
            auth_type="bearer",
            token="mcp-token",
            enabled=True,
            tool_allowlist=["search"],
        )
    )
    views = svc.list_connectors()
    mcp = next(v for v in views if v.id == "mcp:gmail")
    assert mcp.kind == "mcp"
    assert mcp.name == "Gmail MCP"
    assert mcp.enabled is True
    assert mcp.config["transport"] == "http"
    assert mcp.config["auth_type"] == "bearer"
    assert mcp.config["url"] == "https://mcp.example.com"
    assert mcp.config["endpoint"] == "https://mcp.example.com"
    assert mcp.secrets_public["has_token"] is True
    assert mcp.secrets_public["has_url"] is True
    assert "mcp-token" not in json.dumps(mcp.model_dump())


# ── telegram is store-backed (config + enable/disable from the UI, no token leak) ─


def test_telegram_reflects_store_config_no_token_leak():
    svc = _svc()
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update(
        {
            "enabled": True,
            "bot_token": "123:abc",
            "allowed_chat_ids": ["111", "222"],
        }
    )
    tg = svc.get_connector("telegram")
    assert tg.kind == "telegram"
    assert tg.pausable is True
    assert tg.enabled is True
    assert tg.status == "active"
    assert tg.secrets_public == {"has_bot_token": True}
    # Non-secret config surfaces; the raw token NEVER does.
    assert tg.config["allowed_chat_ids"] == ["111", "222"]
    assert "webhook_url" not in tg.config  # removed 2026-08-22
    assert "sessions" not in tg.config and "has_sessions" not in tg.secrets_public
    assert "123:abc" not in json.dumps(tg.model_dump())


def test_telegram_enable_disable_delegates_to_store():
    svc = _svc()
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "123:abc"})
    svc.set_connector_enabled("telegram", True)
    assert TelegramConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("telegram", False)
    assert TelegramConfigStore().get()["enabled"] is False


# ── remove_connector (real, credential-clearing delete — not a mere disable) ─────


def test_remove_connector_clears_secrets_that_update_would_only_hide():
    """``update()`` treats a blank secret as "keep the saved value" — the exact opposite
    of what Remove needs. ``remove_connector`` must bypass that and genuinely erase every
    secret field, across a store with MULTIPLE secrets (Plaid: client secret + the
    separately-minted access token)."""
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "shh",
            "access_token": "tok",
            "environment": "production",
        }
    )
    assert store.get()["secret"] == "shh"  # sanity: it really was saved

    svc.remove_connector("plaid")

    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["secret"] == ""
    assert cfg["access_token"] == ""
    assert cfg["client_id"] == ""
    assert cfg["environment"] == "sandbox"  # back to DEFAULTS, not just secrets
    view = svc.get_connector("plaid")
    assert view.enabled is False
    assert view.secrets_public["has_secret"] is False
    assert view.status == "not_configured"


@pytest.mark.parametrize(
    "connector_id,store_path,secret_field",
    [
        ("simplefin", "backend.db.simplefin_config_store.SimpleFinConfigStore", "access_url"),
        (
            "lunch_money",
            "backend.db.lunch_money_config_store.LunchMoneyConfigStore",
            "access_token",
        ),
        ("lunch_flow", "backend.db.lunch_flow_config_store.LunchFlowConfigStore", "client_secret"),
        ("ynab", "backend.db.ynab_config_store.YnabConfigStore", "access_token"),
        (
            "enable_banking",
            "backend.db.enable_banking_config_store.EnableBankingConfigStore",
            "private_key",
        ),
        ("yodlee", "backend.db.yodlee_config_store.YodleeConfigStore", "secret"),
        ("alpaca", "backend.db.alpaca_config_store.AlpacaConfigStore", "secret_key"),
        ("ibkr", "backend.db.ibkr_config_store.IbkrConfigStore", "flex_token"),
    ],
)
def test_remove_connector_resets_every_native_aggregation_store(
    connector_id, store_path, secret_field
):
    svc = _svc()
    import importlib

    module_name, cls_name = store_path.rsplit(".", 1)
    store_cls = getattr(importlib.import_module(module_name), cls_name)
    store = store_cls()
    store.update({"enabled": True, secret_field: "super-secret-value"})
    assert store.get()[secret_field] == "super-secret-value"

    svc.remove_connector(connector_id)

    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg[secret_field] == ""
    assert svc.get_connector(connector_id).enabled is False


def test_remove_connector_telegram_resets_bot_token_and_access_policy():
    svc = _svc()
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    store.update(
        {
            "enabled": True,
            "bot_token": "123:abc",
            "allowed_chat_ids": ["1", "2"],
            "access_policy": "pairing",
        }
    )
    svc.remove_connector("telegram")

    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["bot_token"] == ""
    assert cfg["allowed_chat_ids"] == []
    # NOT "allowlist" (DEFAULTS' own value) — security review finding 2: allowlist + an
    # empty allowed_chat_ids is Telegram's own documented "empty allowlist = open (dev
    # mode)" contract, exactly fail-OPEN for a connector whose credential was just erased.
    # "disabled" is enforced independently of allowed_chat_ids at both live gates
    # (chat.run.handle_inbound / telegram.handlers._policy_allows_turn).
    assert cfg["access_policy"] == "disabled"
    assert svc.get_connector("telegram").secrets_public == {"has_bot_token": False}


def test_remove_connector_telegram_forgets_sessions_but_not_other_connectors():
    """Finding 10: a stale ``telegram:<chat_id> → session-uuid`` mapping must not survive
    a credential wipe — a freshly re-added bot would otherwise silently resume whatever
    conversation state that chat id had before. Every OTHER connector's session mapping
    (a hypothetical future chat connector) is untouched."""
    svc = _svc()
    from backend.connectors import session_map

    session_map.get_or_create_session("telegram", "111")
    session_map.set_effort("telegram", "111", "high")
    session_map.get_or_create_session("email", "222")

    svc.remove_connector("telegram")

    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "123:abc"})  # re-add
    # A fresh session is minted — the OLD uuid is gone, not silently reused.
    new_session = session_map.get_or_create_session("telegram", "111")
    assert new_session  # a session id exists
    assert session_map.get_effort("telegram", "111") == "low"  # back to default, not "high"
    # The other connector's own mapping survived untouched.
    assert session_map.list_user_ids("email") == ["222"]


# ── security review 2026-08-17: findings 1 + 8 ────────────────────────────────────


def test_remove_connector_clears_tool_permissions_for_a_native_connector():
    """Finding 1: an `always_allow` grant on a native connector's own tool (e.g.
    `plaid_sync_now`, owned by connector id "plaid") must NOT survive Remove — otherwise
    reconfiguring the same connector id against a DIFFERENT underlying account inherits a
    standing unattended-fire permission nobody re-vetted for the new connection."""
    svc = _svc()
    from backend.services import tool_permissions

    tool_permissions.set_tool_permission("plaid", "plaid_sync_now", "always_allow")
    assert tool_permissions.get_tool_permission("plaid", "plaid_sync_now") == "always_allow"

    svc.remove_connector("plaid")

    assert tool_permissions.get_tool_permission("plaid", "plaid_sync_now") == "needs_approval"
    assert tool_permissions.get_connector_permissions("plaid") == {}


def test_remove_connector_clears_tool_permissions_for_a_web_search_provider():
    from backend.services import tool_permissions

    svc = _svc()
    tool_permissions.set_tool_permission("web_search:tavily", "web_search", "always_allow")

    svc.remove_connector("web_search:tavily")

    assert (
        tool_permissions.get_tool_permission("web_search:tavily", "web_search") == "needs_approval"
    )


def test_remove_connector_clears_tool_permissions_for_email_and_webhook():
    from backend.services import tool_permissions

    svc = _svc()
    tool_permissions.set_tool_permission("email", "email_send_message", "always_allow")
    tool_permissions.set_tool_permission("webhook", "notify", "blocked")

    svc.remove_connector("email")
    svc.remove_connector("webhook")

    assert tool_permissions.get_tool_permission("email", "email_send_message") == "needs_approval"
    assert tool_permissions.get_tool_permission("webhook", "notify") == "needs_approval"


def test_remove_connector_clears_all_three_email_tools_via_real_owner_resolution():
    """Closes a documented LOW-severity gap (docs/adr/2026-08-17-connector-remove-security-
    hardening.md, 'Known gaps'): before email_send_message/email_search_messages/email_get_messages carried
    @tool(connector="email"), a decision made via the APP-WIDE Settings > Permissions route
    (which auto-resolves the owner from the tool's OWN tag rather than trusting a caller-
    supplied one) landed under the shared `core` bucket and was NOT cleared by removing the
    Email connector. Resolving the owner the same way that route does — never a hand-typed
    "email" — is the point of this test."""
    from backend.ai.tools._connector_gate import permission_owner
    from backend.services import tool_permissions

    svc = _svc()
    for tool in ("email_send_message", "email_search_messages", "email_get_messages"):
        tool_permissions.set_tool_permission(permission_owner(tool), tool, "always_allow")
        assert permission_owner(tool) == "email"

    svc.remove_connector("email")

    for tool in ("email_send_message", "email_search_messages", "email_get_messages"):
        assert tool_permissions.stored_tool_level("email", tool) is None
        # And nothing was ever (or is now) hiding under the unrelated core bucket either.
        assert tool_permissions.stored_tool_level("core", tool) is None


def _linked_account(source: str, ref: str) -> dict:
    """A synthetic account carrying a provider link on ``Account.external_ids`` — the
    identity link's home since RFC connectors-as-data-sources §D (it moved off the nine
    provider config stores' own ``account_map`` blobs entirely, so there is no longer a
    per-store map for ``remove_connector`` to preserve or clear)."""
    from backend.services import accounts as accounts_service

    return accounts_service.create_account(
        {
            "name": f"{source} checking",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0,
            "external_ids": [{"source": source, "ref": ref}],
        }
    )


def _external_ids(account_id: str) -> list[dict]:
    from backend.db.account_store import AccountStore

    return AccountStore().find_by_id(account_id)["external_ids"]


def test_remove_connector_does_not_touch_linked_accounts_plaid():
    """Finding 8, re-homed: the provider-account identity link is not this store's
    concern any more (``Account.external_ids``, RFC connectors-as-data-sources §D) —
    clearing IT on Remove would auto-create a DUPLICATE ledger account for every provider
    account the next time the SAME connector is reconfigured, splitting transaction
    history. `remove_connector` must erase every actual credential while leaving every
    linked account's `external_ids` entry untouched — it operates purely on the config
    store, which no longer holds any identity mapping to accidentally clear."""
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"enabled": True, "secret": "shh", "access_token": "tok"})
    account = _linked_account("plaid", "plaid_acct_1")

    svc.remove_connector("plaid")

    cfg = store.get()
    assert cfg["secret"] == ""  # the actual credential IS erased
    assert cfg["access_token"] == ""
    assert _external_ids(account["id"])[0]["ref"] == "plaid_acct_1"  # the link survives


def test_remove_connector_does_not_touch_linked_accounts_simplefin():
    svc = _svc()
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    store = SimpleFinConfigStore()
    store.update({"enabled": True, "access_url": "https://user:pw@bridge.example/sf"})
    account = _linked_account("simplefin", "sf_acct_1")

    svc.remove_connector("simplefin")

    cfg = store.get()
    assert cfg["access_url"] == ""
    assert _external_ids(account["id"])[0]["ref"] == "sf_acct_1"  # the link survives


def test_remove_connector_ynab_does_not_touch_linked_accounts():
    """YNAB's own `budget_id` IS this store's concern (not an identity link) and must
    still be cleared on Remove — only the provider-account link, which lives elsewhere
    now, must survive."""
    svc = _svc()
    from backend.db.ynab_config_store import YnabConfigStore

    store = YnabConfigStore()
    store.update({"enabled": True, "access_token": "tok", "budget_id": "b1"})
    account = _linked_account("ynab", "ynab_acct_1")

    svc.remove_connector("ynab")

    cfg = store.get()
    assert cfg["access_token"] == ""
    assert cfg["budget_id"] is None
    assert _external_ids(account["id"])[0]["ref"] == "ynab_acct_1"  # the link survives


def test_remove_connector_lunch_flow_preserves_accounts_meta_when_provider_revoke_fails(
    monkeypatch,
):
    """The revoke-then-reset path (finding 3): when the best-effort provider-side revoke
    for a connection FAILS, its local ``accounts_meta`` institution bookkeeping must
    survive too — the grant is presumably still live at Lunch Flow, so the institution it
    names is still real (``LunchFlowConfigStore.reset()``'s own docstring). The
    provider-account IDENTITY link lives on ``Account.external_ids`` now, not here — this
    test only pins what ``reset()`` itself controls. The revoke call is monkeypatched to
    fail WITHOUT any real network call — never exercise a live third-party endpoint from a
    test."""
    svc = _svc()
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore

    store = LunchFlowConfigStore()
    store.update({"enabled": True, "client_id": "cid", "client_secret": "shh"})
    store.set_tokens(access_token="tok", refresh_token="rtok", expires_at=None, user_id="u1")
    store.record_account_meta("lf_1", connection_id=100, institution_name="Example Bank")

    import backend.services.aggregation.lunch_flow as lunch_flow_service

    def _boom(connection_id):
        raise ConnectionError("simulated: could not reach Lunch Flow")

    monkeypatch.setattr(lunch_flow_service, "disconnect_connection", _boom)

    svc.remove_connector("lunch_flow")  # the revoke fails — non-fatal, reset proceeds anyway

    cfg = store.get()
    assert cfg["client_secret"] == ""
    assert cfg["access_token"] == ""
    # The institution bookkeeping survives the failed revoke attempt.
    assert "lf_1" in store.accounts_meta()


def test_remove_connector_lunch_flow_revokes_each_connection_then_resets(monkeypatch):
    """The success counterpart: a WORKING revoke is actually called, once per linked
    connection, before the local reset — proving the revoke is really wired into Remove
    (not just present as an unused primitive, finding 3)."""
    svc = _svc()
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore

    store = LunchFlowConfigStore()
    store.update({"enabled": True, "client_id": "cid", "client_secret": "shh"})
    store.set_tokens(access_token="tok", refresh_token="rtok", expires_at=None, user_id="u1")
    store.record_account_meta("lf_1", connection_id=100, institution_name="Bank A")
    store.record_account_meta("lf_2", connection_id=200, institution_name="Bank B")

    import backend.services.aggregation.lunch_flow as lunch_flow_service

    revoked: list[int] = []

    def _fake_disconnect(connection_id):
        revoked.append(connection_id)
        store.forget_connection(connection_id)
        return {"ok": True, "connection_id": connection_id, "accounts_unlinked": 1}

    monkeypatch.setattr(lunch_flow_service, "disconnect_connection", _fake_disconnect)

    svc.remove_connector("lunch_flow")

    assert sorted(revoked) == [100, 200]
    cfg = store.get()
    assert cfg["client_secret"] == ""
    assert store.accounts_meta() == {}  # both connections were actually revoked + forgotten


def test_remove_connector_email_erases_the_one_row_and_its_password():
    """Email is ONE row (settings, enabled flag and password) — removing it erases all of it."""
    svc = _svc()
    from backend.db.connector_store import ConnectorStore
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "enabled": True,
            "address": "u@x.com",
            "password": "the-shh",
            "imap_host": "imap.x",
            "smtp_host": "smtp.x",
        }
    )

    svc.remove_connector("email")

    assert ConnectorStore().get("email") is None
    assert ConnectorStore().secrets("email") == {}
    email_cfg = EmailConfigStore().get()
    assert email_cfg["enabled"] is False
    assert email_cfg["imap_host"] == ""
    assert email_cfg["smtp_host"] == ""

    view = svc.get_connector("email")
    assert view.enabled is False
    assert view.secrets_public == {"has_password": False}


def test_remove_connector_email_leaves_webhook_untouched():
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.email_config_store import EmailConfigStore

    ChannelConfigStore().update({"enabled": True, "url": "https://hook.x", "secret": "whsec"})
    EmailConfigStore().update({"enabled": True, "smtp_host": "smtp.x", "password": "smtp-shh"})

    svc.remove_connector("email")

    webhook_cfg = ChannelConfigStore().get()
    assert webhook_cfg["enabled"] is True
    assert webhook_cfg["secret"] == "whsec"
    assert webhook_cfg["url"] == "https://hook.x"


def test_remove_connector_webhook_clears_url_and_secret_and_leaves_email_untouched():
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.email_config_store import EmailConfigStore

    ChannelConfigStore().update({"enabled": True, "url": "https://hook.x", "secret": "whsec"})
    EmailConfigStore().update({"enabled": True, "smtp_host": "smtp.x", "password": "smtp-shh"})

    svc.remove_connector("webhook")

    webhook_cfg = ChannelConfigStore().get()
    assert webhook_cfg["enabled"] is False
    assert webhook_cfg["secret"] == ""
    assert webhook_cfg["url"] == ""

    email_cfg = EmailConfigStore().get()
    assert email_cfg["enabled"] is True
    assert EmailConfigStore().public()["has_password"] is True

    view = svc.get_connector("webhook")
    assert view.enabled is False
    assert view.secrets_public == {"has_url": False, "has_secret": False}


def test_remove_connector_web_search_provider_clears_just_that_one():
    """Each web-search provider is its own connector card, but all of them share ONE
    SearchConfigStore row — removing one must not touch a sibling provider's key."""
    svc = _svc()
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    store.update_provider("tavily", {"enabled": True, "api_key": "tavily-key"})
    store.update_provider("serper", {"enabled": True, "api_key": "serper-key"})
    store.set_default("tavily")

    svc.remove_connector("web_search:tavily")

    cfg = store.get()
    assert cfg["providers"]["tavily"]["enabled"] is False
    assert cfg["providers"]["tavily"]["api_key"] == ""
    assert cfg["providers"]["serper"]["enabled"] is True
    assert cfg["providers"]["serper"]["api_key"] == "serper-key"
    # the stale default_provider selection is cleared, not left dangling on a wiped provider
    assert cfg.get("default_provider") != "tavily"

    view = svc.get_connector("web_search:tavily")
    assert view.enabled is False
    assert view.secrets_public == {"has_api_key": False}


def test_remove_connector_web_search_clears_non_secret_extra_fields_too():
    """Google PSE's ``cx`` (a non-secret extra field, not a key) must also be cleared — a
    stale search-engine ID left behind after 'removing' the connector would be a half-reset."""
    svc = _svc()
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    store.update_provider("google_pse", {"enabled": True, "api_key": "pse-key", "cx": "abc123"})

    svc.remove_connector("web_search:google_pse")

    cfg = store.get()["providers"]["google_pse"]
    assert cfg["enabled"] is False
    assert cfg["api_key"] == ""
    assert cfg.get("cx", "") == ""


def test_remove_connector_web_search_keyfree_default_is_actually_disabled():
    """DuckDuckGo is enabled-BY-DEFAULT whenever no ``enabled`` key is stored at all — so
    Remove must write an EXPLICIT ``enabled: False``, not just drop the provider's entry,
    or the "removal" would silently re-enable it via that same default."""
    svc = _svc()
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    assert store.is_enabled("duckduckgo") is True  # sanity: on by default

    svc.remove_connector("web_search:duckduckgo")

    assert store.is_enabled("duckduckgo") is False
    assert svc.get_connector("web_search:duckduckgo").enabled is False


def test_remove_connector_web_search_unknown_provider_id_is_not_found():
    """An unmodeled provider id never appears in ``list_connectors()`` (it is derived from
    the fixed catalog, not stored data), so the generic 404 gate catches it before
    ``remove_connector``'s own dispatch runs."""
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.remove_connector("web_search:not-a-real-provider")


def test_remove_connector_unknown_id_raises_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.remove_connector("nope")


def test_remove_connector_delegates_mcp_ids_to_remove_mcp_server():
    """An ``mcp:<id>`` connector id routes to the SAME revoke-then-delete-then-clear-
    permissions helper the dedicated MCP delete route uses — no separate, divergent path."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig
    from backend.services import tool_permissions

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test", enabled=True))
    tool_permissions.set_tool_permission("mcp:srv", "some_tool", "always_allow")

    svc.remove_connector("mcp:srv")

    assert McpClientStore().find("srv") is None
    assert tool_permissions.get_tool_permission("mcp:srv", "some_tool") == "needs_approval"


def test_remove_mcp_server_unknown_id_raises_not_found():
    from backend.services import connector_catalog

    with pytest.raises(NotFoundError):
        connector_catalog.remove_mcp_server("nope")


# ── enable/disable delegates to the underlying store ─────────────────────────────


def test_set_connector_enabled_delegates_to_webhook_store():
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"url": "https://hook.example.com"})
    svc.set_connector_enabled("webhook", True)
    assert ChannelConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("webhook", False)
    assert ChannelConfigStore().get()["enabled"] is False


def test_set_connector_enabled_email_toggles_the_one_flag():
    svc = _svc()
    from backend.db.email_config_store import EmailConfigStore

    svc.set_connector_enabled("email", True)
    assert EmailConfigStore().get()["enabled"] is True
    svc.set_connector_enabled("email", False)
    assert EmailConfigStore().get()["enabled"] is False


def test_set_connector_enabled_delegates_to_mcp_store():
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test"))
    svc.set_connector_enabled("mcp:srv", True)
    assert McpClientStore().find("srv").enabled is True


def test_enabling_a_never_saved_connector_creates_its_row_disabled_then_enables_it():
    svc = _svc()
    from backend.db.connector_store import ConnectorStore

    assert ConnectorStore().get("web_search:tavily") is None
    svc.set_connector_enabled("web_search:tavily", True)
    row = ConnectorStore().get("web_search:tavily")
    assert row["enabled"] is True and row["kind"] == "web_search"
    assert row["config"] == {"provider": "tavily", "default": False}


# ── error paths ──────────────────────────────────────────────────────────────────


def test_unknown_connector_raises_not_found():
    svc = _svc()
    with pytest.raises(NotFoundError):
        svc.get_connector("nope")
    with pytest.raises(NotFoundError):
        svc.set_connector_enabled("nope", True)


# ── notifiable (alert) channels — derived from the connector model ────────────────


def test_notifiable_channels_lists_delivery_capable_only_with_availability():
    """telegram/webhook/email-send are notify channels; an MCP server (tools) is not. Each
    reports availability from the connector's configured+enabled state."""
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.models.mcp_client import McpServerConfig

    # Telegram configured + enabled → available.
    TelegramConfigStore().update({"enabled": True, "bot_token": "123:abc"})
    # Webhook configured but DISABLED → present but not available.
    ChannelConfigStore().update({"enabled": False, "url": "https://hook.test"})
    # An MCP server must NOT appear (tools, not a delivery channel).
    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test", enabled=True))

    chans = svc.notifiable_channels()
    by_id = {c["id"]: c for c in chans}
    # telegram/webhook/email-send are notify channels; no mcp, no in_app.
    assert set(by_id) == {"telegram", "webhook", "email"}
    assert by_id["telegram"]["available"] is True
    assert by_id["webhook"]["available"] is False  # configured but disabled
    assert by_id["email"]["available"] is False  # nothing set up
    assert all("label" in c for c in chans)


_IMAP_ONLY = {
    "enabled": True,
    "address": "me@example.com",
    "password": "pw",
    "imap_host": "imap.example.com",
}


def test_an_imap_only_mailbox_is_not_an_available_alert_channel():
    """Email alerts go out over SMTP. A mailbox with only IMAP is "set up" (status active) but
    its alerts would be silently dropped, so it must not validate as a deliverable channel."""
    from backend.db.email_config_store import EmailConfigStore

    svc = _svc()
    EmailConfigStore().update(_IMAP_ONLY)

    assert svc.get_connector("email").status == "active"
    assert {c["id"]: c["available"] for c in svc.notifiable_channels()}["email"] is False


def test_an_email_channel_is_available_exactly_when_it_is_registered():
    from backend.connectors.email_out.channel import register_if_active
    from backend.core.channels import unregister_channel
    from backend.db.email_config_store import EmailConfigStore

    svc = _svc()
    store = EmailConfigStore()
    store.update({**_IMAP_ONLY, "smtp_host": "smtp.example.com"})

    assert {c["id"]: c["available"] for c in svc.notifiable_channels()}["email"] is True
    assert register_if_active() is True

    store.update({"enabled": False})  # paused
    assert {c["id"]: c["available"] for c in svc.notifiable_channels()}["email"] is False
    assert register_if_active() is False
    unregister_channel("email")


def test_known_channel_ids_includes_in_app_and_modeled_externals():
    svc = _svc()
    ids = svc.known_channel_ids()
    # in_app is always valid; the three built-in external transports are always modeled.
    assert {"in_app", "telegram", "webhook", "email"} <= ids
    assert "mcp:srv" not in ids


# ── detail view: short/long description + the per-connector tool bundle ───────────


def test_native_views_carry_descriptions_and_known_tool_names():
    """Every native connector gets a short + long description; its ``tools`` are the EXACT
    registry tool ids (so the detail view never names a tool Lucius can't call)."""
    from backend.ai.tools import TOOL_SCHEMAS

    registry = {t["name"] for t in TOOL_SCHEMAS}
    svc = _svc()
    by_id = {v.id: v for v in svc.list_connectors()}

    expected = {
        "email": ["email_search_messages", "email_get_messages", "email_send_message"],
        # Each aggregation provider gets its OWN first-class, separately-schema'd
        # status/sync pair (promoted 2026-08-13, RFC
        # .claude/rfcs/2026-08-10-connectors-architecture.md §B3) — never the generic
        # bulk `get_connector_status`/`connector_sync` pair, which has no single owning
        # connector and is therefore never listed on any one connector's detail page.
        "simplefin": [
            "simplefin_get_status",
            "simplefin_fetch_transactions",
            "simplefin_list_accounts",
        ],
        "plaid": ["plaid_get_status", "plaid_fetch_transactions", "plaid_list_accounts"],
        "lunch_money": [
            "lunch_money_get_status",
            "lunch_money_fetch_transactions",
            "lunch_money_list_accounts",
        ],
        "lunch_flow": [
            "lunch_flow_get_status",
            "lunch_flow_fetch_transactions",
            "lunch_flow_list_accounts",
        ],
        "ynab": ["ynab_get_status", "ynab_fetch_transactions", "ynab_list_accounts"],
        "enable_banking": [
            "enable_banking_get_status",
            "enable_banking_fetch_transactions",
            "enable_banking_list_accounts",
        ],
        "yodlee": ["yodlee_get_status", "yodlee_fetch_transactions", "yodlee_list_accounts"],
        "alpaca": ["alpaca_get_status", "alpaca_fetch_positions"],
        "ibkr": ["ibkr_get_status", "ibkr_fetch_activity"],
        # Each web-search provider card is its OWN id-scoped entry too (``_decorate``'s
        # id-before-kind lookup, same fix as short/long description below) — never the
        # generic bulk `web_search`/`fetch_page` pair, which every card used to show
        # identically before that lookup existed.
        "web_search:duckduckgo": ["duckduckgo_search", "duckduckgo_fetch_page"],
        "web_search:tavily": ["tavily_search", "tavily_fetch_page"],
        "web_search:perplexity": ["perplexity_search", "perplexity_fetch_page"],
        "web_search:serper": ["serper_search", "serper_fetch_page"],
        "web_search:google_pse": ["google_pse_search", "google_pse_fetch_page"],
        "web_search:firecrawl": ["firecrawl_search", "firecrawl_fetch_page"],
        # Telegram/webhook each carry their own SEND tool (retired the shared, untagged
        # `notify` 2026-08-20) — RECEIVE stays transport-only, no tool.
        "telegram": ["telegram_send_message"],
        "webhook": ["webhook_send_message"],
    }
    for cid, names in expected.items():
        view = by_id[cid]
        assert view.short_description, f"{cid} missing short_description"
        assert view.long_description, f"{cid} missing long_description"
        assert [t.name for t in view.tools] == names
        # PARITY: each named tool is a real, Lucius-callable registry id; all native.
        for t in view.tools:
            assert t.source == "native"
            assert t.name in registry, f"{cid} names unknown tool {t.name}"


def test_web_search_providers_each_get_their_own_description():
    """Regression for the 2026-08-14 copy-drift fix: six web_search connectors share ONE
    kind, so ``connector_catalog.copy_for`` must look up the copy by ``id``
    before falling back to the family-level ``kind`` entry — otherwise every provider card
    (and the Browse & add catalog, which now reads this same field) would show the identical
    generic blurb instead of what actually distinguishes DuckDuckGo from Tavily from Serper."""
    svc = _svc()
    by_id = {v.id: v for v in svc.list_connectors()}
    provider_ids = [
        "web_search:duckduckgo",
        "web_search:tavily",
        "web_search:perplexity",
        "web_search:serper",
        "web_search:google_pse",
        "web_search:firecrawl",
    ]
    shorts = set()
    for pid in provider_ids:
        view = by_id[pid]
        assert view.short_description, pid
        assert view.long_description, pid
        # Never the generic family-level fallback — every provider has its own.
        assert view.short_description != "Web search and page reading for Lucius."
        shorts.add(view.short_description)
    assert len(shorts) == len(provider_ids), "every provider's short_description must be distinct"


def test_telegram_and_webhook_each_carry_their_own_send_tool():
    """Telegram/webhook are channels with a real SEND tool each (retired the shared,
    untagged `notify` 2026-08-20) — and still get a short_description either way."""
    svc = _svc()
    by_id = {v.id: v for v in svc.list_connectors()}
    expected = {"telegram": "telegram_send_message", "webhook": "webhook_send_message"}
    for cid, tool_name in expected.items():
        view = by_id[cid]
        assert [t.name for t in view.tools] == [tool_name]
        assert view.short_description


def test_mcp_view_maps_discovered_tools_as_mcp_source_no_token_leak():
    """An external MCP server's LIVE discovered_tools become ConnectorTool(source="mcp"); the
    descriptions ride along and no secret leaks."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="gmail", name="Gmail MCP", url="https://mcp.example.com", token="mcp-token"
        )
    )
    store.record_discovery(
        "gmail",
        [
            {"name": "search_messages", "description": "Search the inbox."},
            {"name": "send_message", "description": "Send an email."},
        ],
        status="ok",
    )
    mcp = svc.get_connector("mcp:gmail")
    assert [t.name for t in mcp.tools] == ["search_messages", "send_message"]
    assert all(t.source == "mcp" for t in mcp.tools)
    assert mcp.tools[0].description == "Search the inbox."
    assert mcp.short_description and mcp.long_description
    assert "mcp-token" not in json.dumps(mcp.model_dump())


def test_mcp_view_undiscovered_has_empty_tools():
    """A freshly-added MCP server (no /test-connect yet) has an empty tool list, not an error."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test"))
    assert svc.get_connector("mcp:srv").tools == []


def test_mcp_view_uses_the_servers_own_description_not_its_instructions():
    """A custom server's handshake ``serverInfo.description`` becomes its subtitle and
    description in place of the generic fallback. Its ``instructions`` are guidance for an AI,
    not a description — they stay in ``config.server_info_instructions`` for the detail page's
    fold, and never become the card's text."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig, ServerInfo

    store = McpClientStore()
    store.create(McpServerConfig(id="ibkr", url="https://mcp.example.com"))
    store.record_discovery(
        "ibkr",
        [{"name": "get_positions", "description": "List positions."}],
        status="ok",
        server_info=ServerInfo(
            title="Interactive Brokers",
            description="Trade and query positions at Interactive Brokers.",
            instructions="Always call get_positions before placing an order.",
        ),
    )
    view = svc.get_connector("mcp:ibkr")
    assert view.short_description == "Trade and query positions at Interactive Brokers."
    assert view.long_description == "Trade and query positions at Interactive Brokers."
    assert view.config["server_info_instructions"] == (
        "Always call get_positions before placing an order."
    )
    assert view.config["server_info_title"] == "Interactive Brokers"


def test_mcp_view_with_only_instructions_subtitles_with_their_first_sentence():
    """No description → the instructions' first sentence is the subtitle; the detail page's
    description stays generic, since the whole text is already in the fold."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig, ServerInfo

    store = McpClientStore()
    store.create(McpServerConfig(id="gem", url="https://mcp.example.com"))
    text = "This server reaches the Example exchange.\nCall whats_new first. Prices are v1.2."
    store.record_discovery("gem", [], status="ok", server_info=ServerInfo(instructions=text))
    view = svc.get_connector("mcp:gem")
    assert view.short_description == "This server reaches the Example exchange."
    assert "whats_new" not in view.long_description
    assert view.config["server_info_instructions"] == text


def test_mcp_view_shortens_a_long_first_sentence_at_a_word():
    from backend.services.connectors import _first_sentence

    subtitle = _first_sentence("Tools for " + "trading and positions, " * 12 + "end.")
    assert len(subtitle) <= 161 and subtitle.endswith("…")
    assert not subtitle[:-1].endswith((" ", ","))


def test_mcp_view_falls_back_to_generic_description_with_no_permission_talk():
    """No ``instructions`` reported → the generic ``mcp`` fallback, which must say nothing
    about the permission ladder — that explanation lives beside the ladder control itself,
    not duplicated into every custom server's description text."""
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="srv", url="https://mcp.example.com"))
    view = svc.get_connector("mcp:srv")
    assert view.long_description
    for word in ("permission", "approve", "approval", "allow", "watching"):
        assert word not in view.long_description.lower()


def test_mcp_status_is_the_shared_vocabulary_and_stdio_counts_as_configured():
    """QA D4 — an MCP server's ``status`` says what every other family's says.

    Two defects in one expression (``pub["last_status"] or _status(enabled, bool(url))``):
    the MCP-only ``last_status`` vocabulary (``ok``/``connect_error``/…) was returned verbatim
    into a field documented to hold ``active``/``configured``/``error``/``not_configured``, and
    the fallback tested ``url``, which is EMPTY for a stdio server — so a local server the user
    had just added and enabled reported ``not_configured`` however reachable it was.
    """
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="local", transport="stdio", command="npx", args=["-y", "@v/srv"], enabled=True
        )
    )
    assert svc.get_connector("mcp:local").status == "active"

    # A successful handshake does not swap in the connect-time vocabulary…
    store.record_discovery("local", [{"name": "a"}], status="ok")
    assert svc.get_connector("mcp:local").status == "active"
    # …and a failed one reads as the shared "error", like every other connector's does.
    store.record_discovery("local", [], status="connect_error", error="cannot reach")
    assert svc.get_connector("mcp:local").status == "error"
    # The raw connect status still rides in the config — it is the MCP-specific fact (auth
    # error vs. connect error) the detail page distinguishes on — and the error text is the
    # view's ``error``.
    view = svc.get_connector("mcp:local")
    assert view.config["last_status"] == "connect_error"
    assert view.error == "cannot reach"

    # Switched off is "configured", not an error to be solved (spec §1).
    store.update("local", {"enabled": False})
    assert svc.get_connector("mcp:local").status == "configured"


def test_the_view_no_longer_carries_a_per_connector_enforced_flag():
    """Superseded by the 2026-08-14 one-permission-store merge (ADR
    `2026-08-14-one-permission-store-for-every-tool`): `services.tool_permissions.
    is_enforced()` and the `ConnectorView.permissions_enforced` field it rode on are BOTH
    REMOVED, not merely changed. Before the merge, `get_tool_permission` had exactly one
    production caller (`mcp/client_manager`), so a level set on Plaid was recorded but
    gated nothing — `permissions_enforced` told the detail page's ladder which was which.
    `ai/tools/_confirm_gate.py::effective_level` is now the ONE dispatch-time read for
    every family, native and MCP alike, so there is no longer a weaker case to flag: a
    field that could only ever read one value is not information, so it is gone rather
    than hardcoded to `True` everywhere.
    """
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.connector import ConnectorView
    from backend.models.mcp_client import McpServerConfig

    assert "permissions_enforced" not in ConnectorView.model_fields

    McpClientStore().create(McpServerConfig(id="srv", url="https://x.test"))
    by_id = {v.id: v for v in svc.list_connectors()}
    for connector_id in ("mcp:srv", "email", "telegram", "plaid", "web_search:tavily"):
        assert not hasattr(by_id[connector_id], "permissions_enforced"), connector_id


# ── error status precedence (regression: Active + Error contradiction) ────────────


def test_connector_with_last_error_reports_error_status():
    """A connector that is enabled + configured BUT has a non-empty last_error MUST report
    status='error', not 'active'. This is the regression test for the header-badge /
    Status-row contradiction (both must show Error, not one Error + one Active)."""
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "psecret",
            "access_token": "tok-1",
            "last_status": "error",
            "last_error": "PLANNED_MAINTENANCE",
        }
    )
    pl = svc.get_connector("plaid")
    assert pl.status == "error", (
        f"expected 'error' but got '{pl.status}' — the header badge (from connectorHealth) "
        "would show Error while the Status row (from connector.status) would show Active"
    )


def test_connector_without_last_error_reports_active():
    """A connector that is enabled + configured with no last_error stays 'active'."""
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "psecret",
            "access_token": "tok-1",
        }
    )
    pl = svc.get_connector("plaid")
    assert pl.status == "active"


def test_connector_cleared_last_error_returns_to_active():
    """After a successful sync clears last_error the connector should revert to 'active'.

    Note: store.update() drops None values (to avoid accidentally blanking write-only
    secrets). A successful sync clears the error by writing an empty string — the _status()
    helper treats a falsy last_error (None or '') as no error.
    """
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "psecret",
            "access_token": "tok-1",
            "last_status": "error",
            "last_error": "Connection refused",
        }
    )
    assert svc.get_connector("plaid").status == "error"

    # Simulate successful sync clearing the error (empty string, not None — see store note).
    store.update({"last_status": "ok", "last_error": ""})
    assert svc.get_connector("plaid").status == "active"


def test_email_connector_with_last_error_reports_error_status():
    """The email connector (read leg) surfaces last_error → status='error' to guarantee
    the header badge and Status row agree."""
    svc = _svc()
    from backend.db.email_config_store import EmailConfigStore

    store = EmailConfigStore()
    store.update(
        {
            "enabled": True,
            "imap_host": "imap.gmail.com",
            "address": "me@example.com",
            "password": "secret",
        }
    )
    store.record_status("error", "EXAMINE command error: BAD [CANNOT] Invalid mailbox")
    email = svc.get_connector("email")
    assert email.status == "error"


def test_ibkr_connector_with_last_error_reports_error_status():
    """IBKR connector (the original bug reporter) with last_error → status='error'."""
    svc = _svc()
    from backend.db.ibkr_config_store import IbkrConfigStore

    IbkrConfigStore().update(
        {
            "enabled": True,
            "flex_token": "secret-token",
            "flex_query_id": "123456",
            "last_status": "error",
            "last_error": "IBKR Flex HTTP 401",
        }
    )
    ibkr = svc.get_connector("ibkr")
    assert ibkr.status == "error"


# ── enabled_connector_ids() — the AI tool discovery-gate's live enablement check ───────
# (RFC .claude/rfcs/2026-08-10-connectors-architecture.md §B2; ai/tools/_connector_gate.py
# is the consumer, exercised end-to-end in tests/test_connector_tool_discovery_gating.py —
# these pin the SERVICE method itself, in isolation from the AI-tool layer.)


def test_enabled_connector_ids_empty_by_default_except_duckduckgo():
    """A fresh install: no aggregation provider configured/enabled, but web_search's
    key-free DuckDuckGo default IS enabled out of the box."""
    svc = _svc()
    ids = svc.enabled_connector_ids()
    assert "plaid" not in ids
    assert "ibkr" not in ids
    assert "web_search:duckduckgo" in ids
    assert "web_search:tavily" not in ids


def test_enabled_connector_ids_reflects_a_toggled_data_source():
    svc = _svc()
    svc.set_connector_enabled("plaid", True)
    ids = svc.enabled_connector_ids()
    assert "plaid" in ids
    assert "ibkr" not in ids  # untouched

    svc.set_connector_enabled("plaid", False)
    assert "plaid" not in svc.enabled_connector_ids()


def test_enabled_connector_ids_reflects_a_toggled_web_search_provider():
    svc = _svc()
    svc.set_connector_enabled("web_search:tavily", True)
    ids = svc.enabled_connector_ids()
    assert "web_search:tavily" in ids
    assert "web_search:serper" not in ids  # untouched


def test_enabled_connector_ids_is_a_frozenset_of_every_enabled_connector():
    """Not just data sources/web search — ANY enabled connector's id shows up (email,
    webhook, telegram too), since the AI-tool gate consumes this generically."""
    svc = _svc()
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"enabled": True, "url": "https://example.com/hook"})
    ids = svc.enabled_connector_ids()
    assert isinstance(ids, frozenset)
    assert "webhook" in ids


# ── permission ladder DISPLAY matches EFFECTIVE dispatch (security review finding #4,
# ADR 2026-08-17-connector-ladder-shows-the-real-effective-default) ──────────────────────


def _tool_level(view, name: str) -> str:
    for t in view.tools:
        if t.name == name:
            return t.level
    raise AssertionError(f"{name} not in {[t.name for t in view.tools]}")


def test_undecided_read_shaped_tool_shows_always_allow_not_the_raw_default():
    """email_search_messages/email_get_messages are GATE_EXEMPT_READ_SHAPED — effective_level resolves
    an undecided one to always_allow. Before the fix, the ladder fell back to the raw
    DEFAULT_LEVEL ("needs_approval") instead, disagreeing with what actually dispatches."""
    svc = _svc()

    email = svc.get_connector("email")
    assert _tool_level(email, "email_search_messages") == "always_allow"
    assert _tool_level(email, "email_get_messages") == "always_allow"
    # email_send_message is NOT read-shaped — an undecided one still shows the cautious default.
    assert _tool_level(email, "email_send_message") == "needs_approval"


def test_an_explicit_stored_decision_still_wins_over_the_exemption():
    svc = _svc()
    from backend.services import tool_permissions

    tool_permissions.set_tool_permission("email", "email_search_messages", "blocked")

    email = svc.get_connector("email")
    assert _tool_level(email, "email_search_messages") == "blocked"


def test_yolo_armed_shows_always_allow_for_an_otherwise_undecided_write_tool():
    """YOLO (the reserved core:* row) substitutes always_allow for the cautious default at
    dispatch time (ai.tools._confirm_gate.effective_level) — the ladder must show the same
    thing, not the raw "needs_approval" default, or the two disagree exactly like the
    read-shaped case above."""
    svc = _svc()
    from backend.services import tool_permissions

    tool_permissions.set_tool_permission("core", "*", "always_allow")

    email = svc.get_connector("email")
    assert _tool_level(email, "email_send_message") == "always_allow"


def test_yolo_never_overrides_an_explicit_blocked_row():
    svc = _svc()
    from backend.services import tool_permissions

    tool_permissions.set_tool_permission("core", "*", "always_allow")
    tool_permissions.set_tool_permission("email", "email_send_message", "blocked")

    email = svc.get_connector("email")
    assert _tool_level(email, "email_send_message") == "blocked"


# ── market data (Yahoo Finance / CoinGecko) — the migration off the standalone "Data"
# settings panel into real connectors, managed_by="always_on" ────────────────────────────


def test_yahoo_finance_view_is_always_on_no_secret():
    svc = _svc()

    yahoo = svc.get_connector("yahoo_finance")
    assert yahoo.kind == "yahoo_finance"
    assert yahoo.pausable is False
    assert yahoo.enabled is True
    assert yahoo.status == "active"
    assert yahoo.secrets_public == {}
    cfg = yahoo.config
    assert cfg["primary_for_major_crypto"] is True
    assert "failure_streak" in cfg and "cooldown_until" in cfg
    # The connector's own direct-provider tool shows up here (2026-08-22) — the untagged,
    # cross-provider get_live_prices deliberately does NOT (it's not scoped to one connector).
    assert [t.name for t in yahoo.tools] == [
        "yahoo_get_ticker_prices",
        "yahoo_get_price_history",
    ]
    assert "get_live_prices" not in {t.name for t in yahoo.tools}


def test_coingecko_view_is_always_on_no_token_leak():
    svc = _svc()
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update({"coingecko_api_key": "cg-demo-secret"})

    coingecko = svc.get_connector("coingecko")
    assert coingecko.kind == "coingecko"
    assert coingecko.pausable is False
    assert coingecko.enabled is True
    assert coingecko.status == "active"
    assert coingecko.secrets_public == {"has_api_key": True}
    cfg = coingecko.config
    assert cfg["quota_limit"] == 25  # a saved Demo key raises the quota from 4 to 25
    assert "cg-demo-secret" not in json.dumps(coingecko.model_dump())
    assert [t.name for t in coingecko.tools] == [
        "coingecko_get_crypto_prices",
        "coingecko_get_price_history",
    ]
    assert "get_live_prices" not in {t.name for t in coingecko.tools}


def test_coingecko_quota_limit_is_4_with_no_key():
    svc = _svc()
    coingecko = svc.get_connector("coingecko")
    assert coingecko.config["quota_limit"] == 4


def test_coingecko_quota_limit_is_500_with_a_pro_key():
    svc = _svc()
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update(
        {"coingecko_api_key": "cg-pro-secret", "coingecko_api_key_tier": "pro"}
    )
    coingecko = svc.get_connector("coingecko")
    assert coingecko.config["quota_limit"] == 500


def test_yahoo_first_toggle_lives_under_yahoo_not_coingecko():
    """Owner's explicit placement (mid-conversation screenshot review): the
    'yahoo_primary_for_major_crypto' setting belongs on the Yahoo Finance connector's own
    config, not CoinGecko's — even though both read the same underlying store row."""
    svc = _svc()
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update({"yahoo_primary_for_major_crypto": False})

    yahoo = svc.get_connector("yahoo_finance")
    coingecko = svc.get_connector("coingecko")
    assert yahoo.config["primary_for_major_crypto"] is False
    assert "primary_for_major_crypto" not in coingecko.config


def test_always_on_connector_refuses_enable_disable():
    svc = _svc()

    with pytest.raises(ValidationError):
        svc.set_connector_enabled("yahoo_finance", False)
    with pytest.raises(ValidationError):
        svc.set_connector_enabled("coingecko", False)


def test_always_on_connector_stays_enabled_after_a_refused_write():
    """The refusal above must not have side-effected the store — the connector reads
    exactly as enabled after a refused PATCH as it did before it."""
    svc = _svc()
    with pytest.raises(ValidationError):
        svc.set_connector_enabled("yahoo_finance", False)
    assert svc.get_connector("yahoo_finance").enabled is True


def test_remove_yahoo_finance_refused_honestly_no_credential_to_clear():
    svc = _svc()

    with pytest.raises(ValidationError, match="powers price lookups"):
        svc.remove_connector("yahoo_finance")


def test_remove_coingecko_is_also_refused_even_though_it_holds_a_credential():
    """Reversed 2026-08-22 (owner's explicit decision): CoinGecko used to let Remove clear
    just its saved key. A connector this app runs unconditionally must not also offer a
    destructive action that reads as "this connector is gone" — so Remove refuses for
    CoinGecko exactly like it always has for Yahoo Finance, and the saved key survives
    untouched. Edit configuration is the real way to change it."""
    svc = _svc()
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update(
        {"coingecko_api_key": "cg-demo-secret", "yahoo_primary_for_major_crypto": False}
    )

    with pytest.raises(ValidationError, match="Edit configuration"):
        svc.remove_connector("coingecko")

    coingecko = svc.get_connector("coingecko")
    assert coingecko.secrets_public == {"has_api_key": True}
    yahoo = svc.get_connector("yahoo_finance")
    assert yahoo.config["primary_for_major_crypto"] is False


# ── the view shape ────────────────────────────────────────────────────────────────


def test_the_view_has_exactly_the_documented_fields():
    from backend.models.connector import ConnectorPatch, ConnectorView

    assert set(ConnectorView.model_fields) == {
        "id",
        "kind",
        "name",
        "enabled",
        "status",
        "error",
        "pausable",
        "config",
        "secrets_public",
        "short_description",
        "long_description",
        "tools",
    }
    assert set(ConnectorPatch.model_fields) == {"enabled"}


def test_error_is_the_redacted_last_error_and_only_while_the_status_is_error():
    svc = _svc()
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"enabled": True, "client_id": "c", "secret": "s", "access_token": "t"})
    assert svc.get_connector("plaid").error is None

    store.record_status("connect_error", error="GET https://user:hunter2@bank.example/x failed")
    plaid = svc.get_connector("plaid")
    assert plaid.status == "error"
    assert "bank.example" in plaid.error
    assert "hunter2" not in plaid.error

    # A paused connector is not "in error": the badge says Paused, not a problem to solve.
    store.update({"enabled": False})
    paused = svc.get_connector("plaid")
    assert (paused.status, paused.error) == ("configured", None)


# ── the generic builder is one read, not a read per connector ─────────────────────


def test_enabled_connector_ids_reads_neither_secrets_nor_permissions(monkeypatch):
    """The AI tool catalog calls this on every build; it must stay one table read."""
    svc = _svc()
    from backend.db.connector_store import ConnectorStore
    from backend.services import tool_permissions

    svc.set_connector_enabled("plaid", True)

    def _refuse(*args, **kwargs):
        raise AssertionError("enabled_connector_ids must not read secrets or permissions")

    monkeypatch.setattr(ConnectorStore, "secrets", _refuse)
    monkeypatch.setattr(ConnectorStore, "secret_field_map", _refuse)
    monkeypatch.setattr(tool_permissions, "get_all_permissions", _refuse)

    ids = svc.enabled_connector_ids()
    assert {"plaid", "yahoo_finance", "coingecko", "web_search:duckduckgo"} <= ids
    assert "ibkr" not in ids


def test_list_connectors_reads_secret_names_in_one_query(monkeypatch):
    svc = _svc()
    from backend.db.connector_store import ConnectorStore

    svc.set_connector_enabled("plaid", True)
    calls: list[str] = []
    real = ConnectorStore.secrets
    monkeypatch.setattr(
        ConnectorStore,
        "secrets",
        lambda self, cid, **kw: calls.append(cid) or real(self, cid, **kw),
    )
    svc.list_connectors()
    # Presence comes from the bulk query; the only value read is CoinGecko's quota tier.
    assert set(calls) <= {"coingecko"}


def test_mcp_servers_list_in_the_order_they_were_added():
    svc = _svc()
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    for server_id in ("zeta", "alpha", "mid"):
        McpClientStore().create(McpServerConfig(id=server_id, url="https://x.test"))
    ids = [v.id for v in svc.list_connectors() if v.kind == "mcp"]
    assert ids == ["mcp:zeta", "mcp:alpha", "mcp:mid"]
    # …after every built-in.
    all_ids = [v.id for v in svc.list_connectors()]
    assert all_ids.index("mcp:zeta") > all_ids.index("web_search:firecrawl")


def test_a_row_for_an_unknown_search_provider_or_kind_is_not_listed():
    svc = _svc()
    from backend.db.connector_store import ConnectorStore

    ConnectorStore().create("web_search:nope", "web_search", "Nope", enabled=True)
    ConnectorStore().create("mystery", "mystery", "Mystery", enabled=True)
    ids = {v.id for v in svc.list_connectors()}
    assert "web_search:nope" not in ids and "mystery" not in ids
