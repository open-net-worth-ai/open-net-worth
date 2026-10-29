"""Connector tools — one bulk status read, an enable/disable toggle, PLUS a first-class,
separately-schema'd status tool per data-source provider.

**History (RFC ``.claude/rfcs/2026-08-10-connectors-architecture.md`` §B3,
``connectors-as-data-sources`` 2026-09-05).** Sixteen per-provider tools
(``get_<provider>_status`` / ``sync_<provider>_now``, eight providers) were consolidated
into ``connector_status``/``connector_sync``, then (2026-08-13) the survivors were promoted
back to first-class ``<provider>_get_status``/``<provider>_sync_now`` tools so per-tool
permissions stayed name-keyed. **``connector_sync`` and every ``<provider>_sync_now`` tool
are RETIRED (2026-09-05, no aliases — ADR ``2026-08-25-tool-names-are-final``)**: a bank or
broker connector is a READ-ONLY data source now. The only way a provider's rows reach the
ledger is a Lucius turn calling that provider's fetch tool
(``<provider>_fetch_transactions`` in ``ai/tools/connector_fetch.py``,
``alpaca_fetch_positions``/``ibkr_fetch_activity`` in ``ai/tools/broker_fetch.py``) and then
the ordinary ``add_transactions``/position tools — never a hidden scheduler job, never a
bulk "sync everything" call. ``connector_status``/``<provider>_get_status`` were UNCHANGED —
reading a connector's configured/enabled/last-fetched state is still useful, and still has
nothing to do with fetching or booking a row.

**Discovery gating (``connector=``, RFC §B2).** Each per-provider tool below is tagged
``connector=<provider key>`` — the exact id ``services.connectors.list_connectors()`` uses
for that provider (``"plaid"``, ``"enable_banking"``, …). ``ai.tools.catalog`` /
``ai.tools._groups`` read that tag to leave the tool OUT of the flat catalog and OUT of
``find_tools`` results/loads until the user has actually enabled that connector — so
``ibkr_get_status`` isn't offered to every install, only ones with IBKR turned on. The SAME
tag is the permission table's owner key, so a level set on this connector gates these tools
at dispatch (``registry.tool``'s ``connector=`` docstring covers both roles). A disabled
provider's own handler still reports ``status: "disabled"`` rather than being hard-blocked
by the tag.

``get_connector_status`` (``connector_status`` until 2026-10-10, when it grew from the aggregators
to EVERY connector the Connectors screen lists — email, chat channels, price sources, web search,
MCP servers) deliberately carries NO ``connector=`` tag — no single id fits it, and gating it
off would remove the one path that still works before anything is configured.

``set_connector_enabled`` stays separate for the same reason plus a second one: its
discriminator is a DIFFERENT, open namespace (chat channels, web-search providers, MCP
servers — not just the aggregation providers), and toggling standing automation is neither
a status read nor a data pull. It also carries no ``connector=`` tag — it is how a user (or
Lucius, with confirmation) turns a connector ON in the first place, so gating it on
"already enabled" would make it unreachable exactly when it's needed.

**Yodlee (2026-08-14) is DISABLED, not removed** — enterprise-only pricing makes it
impractical for this app's actual self-hosted users (ADR
``2026-08-14-hide-yodlee-enterprise-only-pricing``). ``yodlee_get_status`` stays registered
below, byte-identical, so re-enabling is a one-line flip elsewhere — but unlike every OTHER
connector-tagged tool here, it is the one exception to the "discovery only" rule two
paragraphs up: ``ai.tools._connector_gate.DISABLED_CONNECTORS`` hides it from discovery
UNCONDITIONALLY (even if the store's ``enabled`` flag is somehow true) AND
``registry.dispatch`` refuses it outright before the handler ever runs (same for
``yodlee_fetch_transactions`` in ``connector_fetch.py``). ``get_connector_status`` drops every
``DISABLED_CONNECTORS`` member from the connectors it reports and accepts, so Yodlee is
neither offered nor accepted there either.

**``set_connector_enabled`` ALSO refuses ``enabled=True`` for a ``DISABLED_CONNECTORS``
member (2026-08-14, follow-up fix).** This tool carries no ``connector=`` tag (see above
— its id space is open, not just the aggregation providers), so the ``registry.dispatch``
short-circuit two paragraphs up never sees it: a call naming ``connector_id="yodlee"``
resolves to the plain ``set_connector_enabled`` tool, not to ``yodlee_get_status``, and
would otherwise sail past every Yodlee guard in this file straight into
``services.connectors.set_connector_enabled`` — which flips ``YodleeConfigStore.enabled``.
On an instance with pre-existing valid Yodlee credentials this was a real path to standing,
unattended fetches via one ordinary approve-tier tool call — except there is no more
unattended fetch mechanism for it to arm (fetching only ever happens inside a turn), so the
residual risk this closes is narrower than it once was; the refusal stays because a human
choosing to re-enable something they already set up is still a different, legitimate action
from the AI doing it unprompted (the ADR's own stated exception, preserved via the REST
route). Disabling (``enabled=False``) is never refused — turning Yodlee OFF can't resume
anything.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.untrusted import sanitize_untrusted
from backend.services.aggregation.providers import PROVIDERS


def _requested(input_data: dict) -> list[str]:
    """The connector ids a ``get_connector_status`` call asked for.

    Accepts the declared ``connectors`` array — and, so calls written before it covered every
    connector keep working, the old ``providers`` plus the singular slips ``connector`` /
    ``provider`` — as an array, a bare string or a JSON-encoded array (the coercions models
    routinely need — see the validation note in ``registry``).
    """
    raw = None
    for key in ("connectors", "providers", "connector", "provider"):
        raw = input_data.get(key)
        if raw is not None:
            break
    if isinstance(raw, str):
        text = raw.strip()
        try:
            decoded = json.loads(text)
            raw = decoded if isinstance(decoded, list) else text
        except ValueError:
            raw = [part.strip() for part in text.split(",")] if "," in text else [text]
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw]
    return []


def _status_row(view) -> dict:
    """One connector's state for the model — the same projection as ``_toggle_result``:
    identity and state, NEVER the config (hosts, addresses, endpoints and MCP commands carry
    credentials). ``error`` is the redacted last error, present only while the connector is
    failing; a local (stdio) MCP server's is withheld entirely — its text can trace back to a
    spawned child's own stderr, and the store's scrub is the suspenders, not the only belt."""
    row = {
        "id": view.id,
        "name": sanitize_untrusted(view.name or "", max_len=200),
        "enabled": view.enabled,
        "status": view.status,
    }
    stdio = view.kind == "mcp" and view.config.get("transport") == "stdio"
    if view.error and not stdio:
        row["error"] = sanitize_untrusted(view.error, max_len=300)
    return row


def _connector_status(requested: list[str]) -> dict:
    """ONE read over the view the Connectors screen renders (``list_connectors``)."""
    from backend.ai.tools._connector_gate import DISABLED_CONNECTORS
    from backend.services import connectors as connectors_service

    views = [v for v in connectors_service.list_connectors() if v.id not in DISABLED_CONNECTORS]
    known = [v.id for v in views]
    unknown = [cid for cid in requested if cid not in known]
    if unknown:
        return {
            "error": f"unknown connector id(s): {', '.join(unknown)}",
            "known_connector_ids": known,
        }
    wanted = set(requested or known)
    return {"connectors": {v.id: _status_row(v) for v in views if v.id in wanted}}


class ConnectorStatusInput(ToolInput):
    """Schema-of-record for ``get_connector_status`` (see ``schema_from_model``)."""

    connectors: Optional[List[str]] = Field(
        default=None,
        description=(
            "Connector ids as Connectors shows them: 'email', 'telegram', 'webhook', "
            "'plaid', 'yahoo_finance', 'web_search:tavily', 'mcp:<server>'… "
            "Omit for all."
        ),
    )


@tool(
    "get_connector_status",
    description=(
        "Is a connector set up and working? Reports every connector (or the ones named): "
        "enabled, status (not_configured/configured/active/error) and, when failing, the "
        "error. Check this instead of asking the user whether something is connected. Never "
        "returns secrets or configuration. The user connects things in Connectors, never you; "
        "fetching a feed's data is a separate tool (plaid_fetch_transactions, …)."
    ),
    input_schema=schema_of(ConnectorStatusInput, tool_name="get_connector_status"),
)
async def get_connector_status(input_data: dict):
    result = await asyncio.to_thread(_connector_status, _requested(input_data))
    return json.dumps(result, ensure_ascii=False), None


def _toggle_result(view) -> dict:
    """The connector state a toggle reports — identity and state, NEVER the config blob.

    This used to be ``view.model_dump()``, which carries the connector's public config.
    "Public" there means "no secret VALUE from the store" — it does not mean "safe to hand a
    language model". For an external MCP server that config is ``url`` / ``command`` /
    ``args`` / ``endpoint``, and a credential inside those is the vendor's own documented
    quick start (``npx … postgresql://user:pw@host/db``, ``https://host/sse?api_key=…``);
    for a native one it is IMAP hosts, webhook URLs and Telegram chat ids. ``list_mcp_servers``
    hands the model the ``mcp:<id>`` ids that address exactly those servers, so this was
    one call away from being the same leak that tool was fixed for.

    None of it answers the question the tool was asked — "did the toggle take, and what is the
    connector's state now?" — so the projection below is the whole useful answer. The user
    reads the config in Connectors, where it belongs.
    """
    return {
        "id": view.id,
        # An MCP server's name is pre-filled from what the server called itself, so it is
        # third-party text; sanitized for the same reason every other server string is.
        "name": sanitize_untrusted(view.name or "", max_len=200),
        "kind": view.kind,
        "enabled": view.enabled,
        "status": view.status,
    }


class SetConnectorEnabledInput(ToolInput):
    """Schema-of-record for ``set_connector_enabled`` (see ``schema_from_model``)."""

    connector_id: str = Field(description="One of the fixed connector ids listed above.")
    enabled: bool = Field(description="True to enable, False to disable.")


@tool(
    "set_connector_enabled",
    description=(
        "Enable or disable a connector. Connector ids are fixed: 'email', 'webhook', "
        "'telegram', 'simplefin', 'plaid', 'lunch_money', 'lunch_flow', 'ynab', "
        "'enable_banking', plus 'web_search:<provider>' and "
        "'mcp:<server_id>'. Returns the connector's id, name, kind, enabled flag and status — "
        "never its configuration (endpoints, hosts, commands and credentials are read by the "
        "user in Connectors, not by you). "
        "APPROVE-TIER: confirm with the user BEFORE toggling — this changes standing "
        "configuration (e.g. turning on a bank/broker connector or a chat channel)."
    ),
    input_schema=schema_of(SetConnectorEnabledInput, tool_name="set_connector_enabled"),
    mutates=("config",),
)
async def set_connector_enabled(input_data: dict):
    from backend.ai.tools._connector_gate import DISABLED_CONNECTORS
    from backend.core.errors import OpenNetWorthError
    from backend.services import connectors as connectors_service

    connector_id = str(input_data.get("connector_id") or "").strip()
    if not connector_id:
        return json.dumps(
            {"ok": False, "error": "connector_id is required"}, ensure_ascii=False
        ), None
    enabled = bool(input_data.get("enabled"))

    # The AI-tool-only half of the DISABLED_CONNECTORS refusal (see the module docstring's
    # "set_connector_enabled ALSO refuses..." note) — checked BEFORE the service call, so
    # a re-enable attempt never flips the store even transiently. Never touches
    # services.connectors.set_connector_enabled itself: that function is shared with the
    # REST PATCH route, which a human operator must still be able to use.
    if enabled and connector_id in DISABLED_CONNECTORS:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"the '{connector_id}' connector is disabled in this build (not "
                    "merely unconfigured) — Lucius cannot enable it. An operator can "
                    "still re-enable an already-configured instance from Connectors in "
                    "the app's own Settings."
                ),
            },
            ensure_ascii=False,
        ), None

    try:
        view = connectors_service.set_connector_enabled(connector_id, enabled)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}, ensure_ascii=False), None
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None

    # The REST PATCH route (api/connectors_api.py) best-effort hot-reloads the live
    # Telegram bot after this same write — this tool must honor the identical contract, or
    # Lucius can truthfully report "connector": {"enabled": false} while the bot keeps
    # running unattended (security review finding). Deferred import: ai/tools/ may reach
    # into backend.connectors directly (no service layer exists for the bot's own
    # lifecycle), same accepted-exception shape as its existing config-store imports.
    if connector_id == connectors_service.TELEGRAM_ID:
        from backend.connectors.telegram import bot as telegram_bot

        await telegram_bot.reload_best_effort()

    return (
        json.dumps({"ok": True, "connector": _toggle_result(view)}, ensure_ascii=False),
        None,
    )


# ── the promoted per-provider status tools ──────────────────────────────────────────────
#
# One status read per PROVIDERS entry, dispatching to the exact same
# providers_service.status([key]) call the bulk tool above uses — see the module docstring
# for why these are first-class (permission isolation per provider). ``ToolInput`` with no
# fields at all: the provider is fixed by the tool's own identity, so there is nothing left
# to pass.


class _NoArgsInput(ToolInput):
    """Shared empty-schema MODEL for every promoted per-provider status tool below —
    nothing to pass, the provider is the tool identity itself. Each ``@tool`` still calls
    ``schema_of(_NoArgsInput, tool_name=…)`` separately under its OWN name (rather than
    hoisting one shared schema dict) so every promoted tool is individually recorded in
    ``derived_schemas()`` — the same audit trail a hand-migrated tool gets."""


async def _provider_status(key: str) -> tuple[str, None]:
    from backend.services.aggregation import providers as providers_service

    result = await asyncio.to_thread(providers_service.status, [key])
    return json.dumps({"providers": result}, ensure_ascii=False), None


@tool(
    "simplefin_get_status",
    description=(
        "Check the SimpleFIN connector: whether it's configured and enabled, and when it "
        "was last fetched. Never returns secrets. Configured and linked by the user in the "
        "UI (Connectors → SimpleFIN), never by you. To pull data, call "
        f"simplefin_fetch_transactions. {PROVIDERS['simplefin'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="simplefin_get_status"),
    connector="simplefin",
)
async def simplefin_get_status(input_data: dict):
    return await _provider_status("simplefin")


@tool(
    "lunch_money_get_status",
    description=(
        "Check the Lunch Money connector: whether it's configured and enabled, and when "
        "it was last fetched. Never returns secrets. Configured and linked by the user in "
        "the UI (Connectors → Lunch Money), never by you. To pull data, call "
        f"lunch_money_fetch_transactions. {PROVIDERS['lunch_money'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="lunch_money_get_status"),
    connector="lunch_money",
)
async def lunch_money_get_status(input_data: dict):
    return await _provider_status("lunch_money")


@tool(
    "lunch_flow_get_status",
    description=(
        "Check the Lunch Flow connector: whether it's configured and enabled, and when it "
        "was last fetched. Never returns secrets. Configured and linked by the user in the "
        "UI (Connectors → Lunch Flow), never by you. To pull data, call "
        f"lunch_flow_fetch_transactions. {PROVIDERS['lunch_flow'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="lunch_flow_get_status"),
    connector="lunch_flow",
)
async def lunch_flow_get_status(input_data: dict):
    return await _provider_status("lunch_flow")


@tool(
    "ynab_get_status",
    description=(
        "Check the YNAB connector: whether it's configured and enabled, and when it was "
        "last fetched. Never returns secrets. Configured and linked by the user in the UI "
        f"(Connectors → YNAB), never by you. To pull data, call ynab_fetch_transactions. "
        f"{PROVIDERS['ynab'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="ynab_get_status"),
    connector="ynab",
)
async def ynab_get_status(input_data: dict):
    return await _provider_status("ynab")


@tool(
    "plaid_get_status",
    description=(
        "Check the Plaid connector: whether it's configured and enabled, and when it was "
        "last fetched. Never returns secrets. Configured and linked by the user in the UI "
        f"(Connectors → Plaid), never by you. To pull data, call plaid_fetch_transactions. "
        f"{PROVIDERS['plaid'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="plaid_get_status"),
    connector="plaid",
)
async def plaid_get_status(input_data: dict):
    return await _provider_status("plaid")


@tool(
    "enable_banking_get_status",
    description=(
        "Check the Enable Banking connector: whether it's configured and enabled, and "
        "when it was last fetched. Never returns secrets. Configured and linked by the "
        "user in the UI (Connectors → Enable Banking), never by you. To pull data, call "
        f"enable_banking_fetch_transactions. {PROVIDERS['enable_banking'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="enable_banking_get_status"),
    connector="enable_banking",
)
async def enable_banking_get_status(input_data: dict):
    return await _provider_status("enable_banking")


@tool(
    "yodlee_get_status",
    description=(
        "Check the Yodlee connector: whether it's configured and enabled, and when it was "
        "last fetched. Never returns secrets. Configured and linked by the user in the UI "
        f"(Connectors → Yodlee), never by you. {PROVIDERS['yodlee'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="yodlee_get_status"),
    connector="yodlee",
)
async def yodlee_get_status(input_data: dict):
    return await _provider_status("yodlee")


@tool(
    "alpaca_get_status",
    description=(
        "Check the Alpaca connector: whether it's configured and enabled, and when it was "
        "last fetched. Never returns secrets. Configured and linked by the user in the UI "
        f"(Connectors → Alpaca), never by you. To pull data, call alpaca_fetch_positions. "
        f"{PROVIDERS['alpaca'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="alpaca_get_status"),
    connector="alpaca",
)
async def alpaca_get_status(input_data: dict):
    return await _provider_status("alpaca")


@tool(
    "ibkr_get_status",
    description=(
        "Check the Interactive Brokers (Flex Web Service) connector: whether it's "
        "configured and enabled, and when it was last fetched. Never returns secrets. "
        "Configured and linked by the user in the UI (Connectors → IBKR), never by you. "
        f"To pull data, call ibkr_fetch_activity. {PROVIDERS['ibkr'].note}"
    ),
    input_schema=schema_of(_NoArgsInput, tool_name="ibkr_get_status"),
    connector="ibkr",
)
async def ibkr_get_status(input_data: dict):
    return await _provider_status("ibkr")
