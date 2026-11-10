"""Unit tests for the account-aggregation STATUS/discovery tool surface.

RFC ``connectors-as-data-sources`` (2026-09-05) retired ``connector_sync`` and every
``<provider>_sync_now`` tool — a bank/broker connector is a READ-ONLY data source now; the
only way its rows reach the ledger is a Lucius turn calling its ``<provider>_
fetch_transactions``/``alpaca_fetch_positions``/``ibkr_fetch_activity`` tool
(``tests/test_connector_fetch_tools.py``) and then ``add_transactions``. This file keeps
what survived that retirement:

  - ``get_connector_status`` / ``<provider>_get_status`` — status is still useful, still has
    nothing to do with fetching
  - ``search_institutions``

Tests verify:
  * Shape of returned JSON (required keys present)
  * Secret-hiding invariants (no credentials appear in public output)
  * proposed_change is always None (these are all reads)
  * search_institutions federates across providers

Offline, isolated to tmp_path; no LLM / network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.scripts.tool_renames import RENAMES as _RENAMES

# ── helpers ──────────────────────────────────────────────────────────────────────────


def call(tool: str, **inp):
    """Run execute_tool synchronously; return (parsed_dict, proposed_change)."""
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def status_of(provider: str):
    """One provider's own public status, through its promoted ``<provider>_get_status`` tool."""
    body, proposed = call(f"{provider}_get_status")
    return body["providers"][provider], proposed


# ── path isolation fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def simplefin_paths(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SIMPLEFIN_CONFIG_JSON", tmp_path / "simplefin_config.json")
    yield tmp_path


@pytest.fixture()
def plaid_paths(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "PLAID_CONFIG_JSON", tmp_path / "plaid_config.json")
    yield tmp_path


@pytest.fixture()
def lunch_money_paths(tmp_path, monkeypatch):
    yield tmp_path


@pytest.fixture()
def ynab_paths(tmp_path, monkeypatch):
    yield tmp_path


@pytest.fixture()
def enable_banking_paths(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(
        paths, "ENABLE_BANKING_CONFIG_JSON", tmp_path / "enable_banking_config.json"
    )
    yield tmp_path


@pytest.fixture()
def yodlee_paths(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "YODLEE_CONFIG_JSON", tmp_path / "yodlee_config.json")
    yield tmp_path


@pytest.fixture()
def all_agg_paths(tmp_path, monkeypatch):
    """Patch every aggregation config path — used for search_institutions which may
    load multiple stores internally."""
    import backend.paths as paths

    monkeypatch.setattr(paths, "SIMPLEFIN_CONFIG_JSON", tmp_path / "simplefin_config.json")
    monkeypatch.setattr(paths, "PLAID_CONFIG_JSON", tmp_path / "plaid_config.json")
    monkeypatch.setattr(
        paths, "ENABLE_BANKING_CONFIG_JSON", tmp_path / "enable_banking_config.json"
    )
    monkeypatch.setattr(paths, "YODLEE_CONFIG_JSON", tmp_path / "yodlee_config.json")
    yield tmp_path


@pytest.fixture()
def every_provider_path(all_agg_paths, tmp_path, monkeypatch):
    """all_agg_paths plus the two broker stores — the full nine."""
    import backend.paths as paths

    monkeypatch.setattr(paths, "ALPACA_CONFIG_JSON", tmp_path / "alpaca_config.json")
    monkeypatch.setattr(paths, "IBKR_CONFIG_JSON", tmp_path / "ibkr_config.json")
    yield tmp_path


# ── simplefin_get_status ─────────────────────────────────────────────────────────────


def test_simplefin_get_status_unconfigured_shape(simplefin_paths):
    body, proposed = status_of("simplefin")
    assert proposed is None
    assert body["configured"] is False
    assert body["enabled"] is False
    assert "has_access_url" in body
    assert "last_synced_at" in body


def test_simplefin_get_status_hides_access_url(simplefin_paths):
    """The SimpleFIN access URL (and its embedded HTTP-basic creds) must not appear in the
    public output."""
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    store = SimpleFinConfigStore()
    store.update({"access_url": "https://user:TOPSECRET@bridge.example/sf"})

    body, _ = status_of("simplefin")
    blob = json.dumps(body)
    assert "TOPSECRET" not in blob
    assert body["has_access_url"] is True


def test_simplefin_get_status_proposed_is_none(simplefin_paths):
    _, proposed = status_of("simplefin")
    assert proposed is None


def test_promoted_simplefin_get_status_tool_hides_access_url_too(simplefin_paths):
    """Mirrors test_promoted_plaid_get_status_tool_hides_secret_too: the promoted
    simplefin_get_status tool reaches the identical providers_service.status([key]) call,
    but pin it directly too — a future change that shapes the promoted handler's own
    output independently must not silently reopen this."""
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    store = SimpleFinConfigStore()
    store.update({"access_url": "https://user:TOPSECRET@bridge.example/sf"})

    body, proposed = call("simplefin_get_status")
    assert proposed is None
    blob = json.dumps(body)
    assert "TOPSECRET" not in blob
    assert body["providers"]["simplefin"]["has_access_url"] is True


# ── plaid_get_status ─────────────────────────────────────────────────────────────────


def test_plaid_get_status_unconfigured_shape(plaid_paths):
    body, proposed = status_of("plaid")
    assert proposed is None
    assert body["configured"] is False
    assert body["enabled"] is False
    assert "has_secret" in body
    assert "has_access_token" in body
    assert "environment" in body


def test_plaid_get_status_hides_secret(plaid_paths):
    """Plaid secret must not appear in the public view; client_id is safe to echo."""
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    # The Plaid store's secret field is named "secret" (not "client_secret")
    store.update({"client_id": "client123", "secret": "TOPLEVEL_SECRET"})

    body, _ = status_of("plaid")
    blob = json.dumps(body)
    assert "TOPLEVEL_SECRET" not in blob
    assert body["has_secret"] is True
    assert body["client_id"] == "client123"


def test_plaid_get_status_proposed_is_none(plaid_paths):
    _, proposed = status_of("plaid")
    assert proposed is None


def test_promoted_plaid_get_status_tool_hides_secret_too(plaid_paths):
    """Security-review coverage gap (2026-08-13): the secret-hiding sweeps above all go
    through the BULK get_connector_status tool. The promoted plaid_get_status tool reaches
    the identical providers_service.status([key]) call, but pin it directly too — a
    future change that shapes the promoted handler's own output independently must not
    silently reopen this."""
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"client_id": "client123", "secret": "TOPLEVEL_SECRET"})

    body, proposed = call("plaid_get_status")
    assert proposed is None
    blob = json.dumps(body)
    assert "TOPLEVEL_SECRET" not in blob
    assert body["providers"]["plaid"]["has_secret"] is True
    assert body["providers"]["plaid"]["client_id"] == "client123"


# ── lunch_money_get_status ──────────────────────────────────────────────────────────


def test_lunch_money_get_status_unconfigured_shape(lunch_money_paths):
    body, proposed = status_of("lunch_money")
    assert proposed is None
    assert body["configured"] is False
    assert body["enabled"] is False
    assert "has_access_token" in body


def test_lunch_money_get_status_hides_access_token(lunch_money_paths):
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore

    store = LunchMoneyConfigStore()
    store.update({"access_token": "TOPSECRET_LM_TOKEN"})

    body, _ = status_of("lunch_money")
    blob = json.dumps(body)
    assert "TOPSECRET_LM_TOKEN" not in blob
    assert body["has_access_token"] is True


def test_lunch_money_get_status_proposed_is_none(lunch_money_paths):
    _, proposed = status_of("lunch_money")
    assert proposed is None


def test_promoted_lunch_money_get_status_tool_hides_token_too(lunch_money_paths):
    """Mirrors test_promoted_plaid_get_status_tool_hides_secret_too: the promoted
    lunch_money_get_status tool reaches the identical providers_service.status([key])
    call, but pin it directly too — a future change that shapes the promoted handler's
    own output independently must not silently reopen this."""
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore

    store = LunchMoneyConfigStore()
    store.update({"access_token": "TOPSECRET_LM_TOKEN"})

    body, proposed = call("lunch_money_get_status")
    assert proposed is None
    blob = json.dumps(body)
    assert "TOPSECRET_LM_TOKEN" not in blob
    assert body["providers"]["lunch_money"]["has_access_token"] is True


# ── ynab_get_status ──────────────────────────────────────────────────────────────────


def test_ynab_get_status_unconfigured_shape(ynab_paths):
    body, proposed = status_of("ynab")
    assert proposed is None
    assert body["configured"] is False
    assert body["enabled"] is False
    assert "has_access_token" in body
    assert "budget_id" in body


def test_ynab_get_status_hides_access_token(ynab_paths):
    from backend.db.ynab_config_store import YnabConfigStore

    store = YnabConfigStore()
    store.update({"access_token": "TOPSECRET_YNAB_TOKEN"})

    body, _ = status_of("ynab")
    blob = json.dumps(body)
    assert "TOPSECRET_YNAB_TOKEN" not in blob
    assert body["has_access_token"] is True


def test_ynab_get_status_proposed_is_none(ynab_paths):
    _, proposed = status_of("ynab")
    assert proposed is None


def test_promoted_ynab_get_status_tool_hides_token_too(ynab_paths):
    """Mirrors test_promoted_lunch_money_get_status_tool_hides_token_too: the promoted
    ynab_get_status tool reaches the identical providers_service.status([key]) call, but
    pin it directly too."""
    from backend.db.ynab_config_store import YnabConfigStore

    store = YnabConfigStore()
    store.update({"access_token": "TOPSECRET_YNAB_TOKEN"})

    body, proposed = call("ynab_get_status")
    assert proposed is None
    blob = json.dumps(body)
    assert "TOPSECRET_YNAB_TOKEN" not in blob
    assert body["providers"]["ynab"]["has_access_token"] is True


# ── enable_banking_get_status ────────────────────────────────────────────────────────


def test_enable_banking_get_status_unconfigured_shape(enable_banking_paths):
    body, proposed = status_of("enable_banking")
    assert proposed is None
    assert body["configured"] is False
    assert body["enabled"] is False
    assert "has_credentials" in body
    assert "has_session" in body
    assert "application_id" in body


def test_enable_banking_get_status_hides_private_key(enable_banking_paths):
    """The Enable Banking private key must not appear in the public output."""
    from backend.db.enable_banking_config_store import EnableBankingConfigStore

    store = EnableBankingConfigStore()
    # The field is named "private_key" (not "private_key_pem") in DEFAULTS
    store.update({"application_id": "app-001", "private_key": "-----BEGIN PRIVATEKEY-----"})

    body, _ = status_of("enable_banking")
    blob = json.dumps(body)
    assert "PRIVATEKEY" not in blob
    assert body["application_id"] == "app-001"
    assert body["has_credentials"] is True


def test_enable_banking_get_status_proposed_is_none(enable_banking_paths):
    _, proposed = status_of("enable_banking")
    assert proposed is None


# ── Yodlee: DISABLED, not merely unconfigured (2026-08-14) ───────────────────────────
#
# Enterprise-only pricing with no public self-serve tier makes Yodlee impractical for this
# app's actual self-hosted users — hidden from every AI-facing discovery/dispatch surface,
# code kept fully intact for a future re-enable. See
# docs/adr/2026-08-14-hide-yodlee-enterprise-only-pricing.md.


def test_yodlee_get_status_tool_is_refused_on_direct_dispatch(yodlee_paths):
    """Still a REGISTERED tool (so re-enabling is a one-line flip) — but dispatch refuses
    it before the handler / config store are ever reached, even called by its exact name."""
    body, proposed = call("yodlee_get_status")
    assert proposed is None
    assert "error" in body
    assert "yodlee" in body["error"].lower()


def test_connector_status_bulk_omits_yodlee_by_default(yodlee_paths):
    """The bulk read (providers omitted = every OFFERED provider) no longer enumerates
    Yodlee — it is withdrawn, not merely unconfigured."""
    body, proposed = call("get_connector_status")
    assert proposed is None
    assert "yodlee" not in body["connectors"]


def test_connector_status_bulk_refuses_yodlee_by_name(yodlee_paths):
    body, _ = call("get_connector_status", providers=["yodlee"])
    assert "error" in body


# ── search_institutions ──────────────────────────────────────────────────────────────


def test_search_institutions_returns_required_keys(all_agg_paths, monkeypatch):
    """Result always has institutions, providers, and manual_import keys."""
    from backend.services import account_linking

    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: {"institutions": [], "providers": [], "manual_import": True},
    )
    body, proposed = call("search_institutions")
    assert proposed is None
    assert "institutions" in body
    assert "providers" in body
    assert "manual_import" in body


def test_search_institutions_manual_import_always_true(all_agg_paths, monkeypatch):
    """manual_import must always be True — it's the honest fallback."""
    from backend.services import account_linking

    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: {"institutions": [], "providers": [], "manual_import": True},
    )
    body, _ = call("search_institutions", country="US", query="Meridian Bank")
    assert body["manual_import"] is True


def test_search_institutions_passes_country_and_query(all_agg_paths, monkeypatch):
    """country and query inputs are forwarded to the service."""
    from backend.services import account_linking

    captured = {}
    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: (
            captured.update(query=q, country=c)
            or {"institutions": [], "providers": [], "manual_import": True}
        ),
    )
    call("search_institutions", country="GB", query="Flux")
    assert captured["country"] == "GB"
    assert captured["query"] == "Flux"


def test_search_institutions_strips_whitespace_from_inputs(all_agg_paths, monkeypatch):
    """Leading/trailing whitespace is stripped from country and query."""
    from backend.services import account_linking

    captured = {}
    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: (
            captured.update(query=q, country=c)
            or {"institutions": [], "providers": [], "manual_import": True}
        ),
    )
    call("search_institutions", country="  DE  ", query="  Sparkasse  ")
    assert captured["country"] == "DE"
    assert captured["query"] == "Sparkasse"


def test_search_institutions_no_args_defaults_empty(all_agg_paths, monkeypatch):
    """Calling search_institutions with no inputs passes empty strings."""
    from backend.services import account_linking

    captured = {}
    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: (
            captured.update(query=q, country=c)
            or {"institutions": [], "providers": [], "manual_import": True}
        ),
    )
    call("search_institutions")
    assert captured["country"] == ""
    assert captured["query"] == ""


def test_search_institutions_proposed_is_none(all_agg_paths, monkeypatch):
    """search_institutions is read-only — proposed_change must be None."""
    from backend.services import account_linking

    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: {"institutions": [], "providers": [], "manual_import": True},
    )
    _, proposed = call("search_institutions")
    assert proposed is None


def test_search_institutions_surfaces_service_institutions(all_agg_paths, monkeypatch):
    """Institutions returned by the service are passed through in the response."""
    from backend.services import account_linking

    fake_banks = [{"id": "bank-1", "name": "Meridian Bank", "provider": "plaid"}]
    monkeypatch.setattr(
        account_linking,
        "search_institutions",
        lambda q, c: {"institutions": fake_banks, "providers": ["plaid"], "manual_import": True},
    )
    body, _ = call("search_institutions", country="US", query="Meridian Bank")
    assert body["institutions"] == fake_banks
    assert "plaid" in body["providers"]


# ── the merged surface: providers arg, aliases, errors ────────────────────────────────


def test_status_defaults_to_every_provider(every_provider_path):
    """Omitting `providers` checks them all — the read is cheap and local."""
    from backend.services.aggregation.providers import PROVIDER_KEYS

    body, proposed = call("get_connector_status")
    assert proposed is None
    rows = body["connectors"]
    assert set(PROVIDER_KEYS) <= set(rows)
    # One uniform row per connector — identity and state, never the provider's own feed detail.
    for key in PROVIDER_KEYS:
        assert set(rows[key]) == {"id", "name", "enabled", "status"}
        assert rows[key]["status"] == "not_configured"


def test_status_no_provider_leaks_a_secret(every_provider_path):
    """Whole-surface sweep: the merged read must not widen the secret-hiding contract."""
    blob = json.dumps(call("get_connector_status")[0]).lower()
    for marker in ("secret", "token", "password", "private_key", "access_url"):
        # The *flags* (has_secret / has_access_url) are fine; a VALUE is not.
        assert f'"{marker}": "' not in blob


def test_unknown_provider_reports_every_bad_key_at_once(every_provider_path):
    """One round-trip fixes a whole malformed list."""
    body, _ = call("get_connector_status", providers=["monzo", "flux"])
    assert "monzo" in body["error"] and "flux" in body["error"]


def test_status_reports_a_broken_store_without_blinding_the_rest(every_provider_path, monkeypatch):
    from backend.services.aggregation import providers as providers_service

    broken = providers_service.PROVIDERS["plaid"]
    monkeypatch.setattr(
        type(broken),
        "public_status",
        lambda self: (
            (_ for _ in ()).throw(RuntimeError("bad config"))
            if self.key == "plaid"
            else {"configured": False}
        ),
    )
    plaid, _ = call("plaid_get_status")
    assert plaid["providers"]["plaid"]["status"] == "error"
    enable_banking, _ = call("enable_banking_get_status")
    assert "configured" in enable_banking["providers"]["enable_banking"]


# ── the promoted per-provider status tools (2026-08-13, RFC §B3) ─────────────────────
#
# <provider>_get_status is a REAL, separately-registered tool — not an alias of
# get_connector_status. Each has its OWN schema (no `providers` param: nothing to pass, the
# provider is the tool's own identity) and dispatches to the exact same
# providers_service.status([key]) call the bulk tool uses.

_PROMOTED_STATUS_TOOLS = {
    "simplefin_get_status": "simplefin",
    "plaid_get_status": "plaid",
    "lunch_money_get_status": "lunch_money",
    "ynab_get_status": "ynab",
    "enable_banking_get_status": "enable_banking",
    "yodlee_get_status": "yodlee",
    "alpaca_get_status": "alpaca",
    "ibkr_get_status": "ibkr",
}


def test_every_promoted_status_tool_dispatches_scoped_to_its_own_provider(every_provider_path):
    """Each promoted tool reads exactly its own provider — never widened to every one.

    Yodlee, IBKR Flex and Alpaca excluded: DISABLED, refused before dispatch reaches the handler at
    all — see the dedicated "Yodlee: DISABLED" section above.
    """
    for name, provider in _PROMOTED_STATUS_TOOLS.items():
        if provider in ("yodlee", "ibkr", "alpaca"):
            continue
        body, proposed = call(name)
        assert proposed is None, name
        assert set(body["providers"]) == {provider}, name


def test_every_promoted_status_tool_takes_no_arguments(every_provider_path):
    """No `providers` param survives the promotion — the provider IS the tool identity,
    so a caller that (mistakenly) passes one gets it ignored, not honored as an override."""
    body, _ = call("plaid_get_status", providers=["enable_banking"])
    assert set(body["providers"]) == {"plaid"}


def test_every_promoted_tool_is_offered_to_the_model():
    """These are REAL, offered tools — not aliases hidden from the schema list. (Whether
    they're in the default/CORE offer or gated by `connector=` behind find_tools/the
    catalog is a SEPARATE, presentation-layer concern — see
    tests/test_connector_tool_discovery_gating.py; TOOL_SCHEMAS/build_tool_schemas() is
    the full registry, never filtered.)"""
    from backend.ai.tools.registry import build_tool_schemas

    offered = {t["name"] for t in build_tool_schemas()}
    assert set(_PROMOTED_STATUS_TOOLS) <= offered


def test_promoted_status_tools_are_not_aliases():
    """A promoted name resolves to ITSELF, not to get_connector_status — and it is a member
    of registered_tool_names(), the set an alias is explicitly excluded from."""
    from backend.ai.tools.registry import registered_tool_names

    live = set(registered_tool_names())
    for name in _PROMOTED_STATUS_TOOLS:
        assert name not in _RENAMES, name
        assert name in live, name


def test_each_promoted_status_tool_schema_takes_no_parameters():
    """No `providers` array, no parameters at all — the provider is the tool identity."""
    from backend.ai.tools.registry import build_tool_schemas

    schemas = {t["name"]: t["input_schema"] for t in build_tool_schemas()}
    for name in _PROMOTED_STATUS_TOOLS:
        assert schemas[name].get("properties") == {}, name
        assert schemas[name].get("required") == [], name


def test_each_promoted_status_tool_is_a_pure_read():
    from backend.ai.tools.registry import mutated_domains

    for name in _PROMOTED_STATUS_TOOLS:
        assert mutated_domains(name) == (), name


def test_bulk_status_tool_still_works_alongside_the_promoted_ones():
    """The bulk read is a kept, documented convenience — not retired by the promotion."""
    from backend.ai.tools.registry import build_tool_schemas

    offered = {t["name"] for t in build_tool_schemas()}
    assert "get_connector_status" in offered


def test_bulk_status_tool_carries_no_connector_tag():
    """A single connector id doesn't fit a tool spanning every provider — the bulk tool
    is deliberately left untagged (see connectors.py's module docstring), which also
    means it's never gated out of discovery, unlike its per-provider twin."""
    from backend.ai.tools.registry import tool_connector

    assert tool_connector("get_connector_status") is None


def test_a_read_never_declares_domains(every_provider_path):
    from backend.ai.tools.registry import mutated_domains

    async def run():
        await execute_tool("get_connector_status", {"providers": ["plaid"]})
        return mutated_domains("get_connector_status")

    assert asyncio.run(run()) == ()


# ── retired tool names never resolve again (RFC connectors-as-data-sources, no aliases) ──


@pytest.mark.parametrize(
    "name",
    [
        "connector_sync",
        "simplefin_sync_now",
        "plaid_sync_now",
        "lunch_money_sync_now",
        "lunch_flow_sync_now",
        "ynab_sync_now",
        "enable_banking_sync_now",
        "yodlee_sync_now",
        "alpaca_sync_now",
        "ibkr_sync_now",
    ],
)
def test_retired_sync_tool_no_longer_resolves(name):
    from backend.ai.tools.registry import registered_tool_names

    assert name not in registered_tool_names()
    body, proposed = call(name)
    assert proposed is None
    assert "error" in body
