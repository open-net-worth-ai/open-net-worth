"""The account-aggregation PROVIDER TABLE — one row per external data source.

Ten providers are REGISTERED (eight bank aggregators — SimpleFIN, Plaid, Lunch Money,
Lunch Flow, YNAB, Enable Banking, GoCardless, Yodlee — plus two brokers; the ones whose
catalog kind is ``disabled`` are not offered). Every offered provider is a READ-ONLY DATA SOURCE (RFC
``connectors-as-data-sources``): a Lucius turn calls its fetch tool
(``<provider>_fetch_transactions`` / ``alpaca_fetch_positions`` / ``ibkr_fetch_activity``),
which reads this table for (a) the provider's own ``fetch_window`` function, (b) its config
store (dormant gate + status), (c) its default lookback window, and (d) a handful of
operational facts a caller needs to be told about (region, credential shape, what a fetch
actually pulls). There is no scheduled poll and no code path that books a row without that
Lucius turn — booking happens through the ordinary ``add_transactions``/position tools, in
``backend.services.connector_fetch``, never here.

**Yodlee is disabled (2026-08-14, ``disabled=True`` on its catalog kind)** — enterprise-only pricing with no public
self-serve tier makes it impractical for this app's actual self-hosted users (ADR
`2026-08-14-hide-yodlee-enterprise-only-pricing`). Its row, provider module, and config
store are untouched and fully functional; ``disabled`` only drops it out of
``PROVIDER_KEYS`` — the offered/resolvable set ``get_connector_status`` validates against — so
the bulk tool stops enumerating and stops accepting it. The row stays in ``PROVIDERS``
itself (never deleted) so the promoted ``yodlee_get_status``/``yodlee_fetch_transactions``
tool registrations, which index it directly by key, keep importing; both are hidden from
discovery AND refused at dispatch by ``ai.tools._connector_gate.DISABLED_CONNECTORS``
instead. Re-enabling Yodlee end to end is flipping this one field back to ``False`` plus
that one frozenset.

This module is that table, plus the two loops over it (``status``/``resolve``). It
deliberately owns NO fetch logic — every provider's own ``fetch_window`` module function
does the wire protocol + normalization; this table only routes to it by key.

Callables are referenced by ``(module, attribute)`` and imported on demand rather than at
module load: the tool layer has always lazy-imported these providers (each pulls httpx and
a provider SDK surface), and a shared table must not turn nine lazy imports into nine eager
ones at boot.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.services.connector_catalog import CATALOG

logger = logging.getLogger(__name__)


def derived_ref(
    provider_account_id: str, date: str, amount: float, currency: str, description: str
) -> str:
    """The fallback ref for a provider row with NO stable id of its own (RFC §A step 3) —
    a content hash, never dropped. Shared so all nine provider ``fetch_window``
    implementations hash the same five fields the same way; two providers hashing
    differently would be harmless (refs never cross providers) but pointless drift."""
    key = f"{provider_account_id}|{date}|{amount}|{currency}|{description}"
    return "derived:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class FetchWindow:
    """What a provider's ``fetch_window(store, since, before, account, *, accounts_only)``
    returns — the raw shape ``connector_fetch.fetch()`` normalizes into draft rows.

    ``accounts`` is every provider account the credential can see (not just ones with
    activity in the window) — ``[{provider_account_id, name, mask, currency, balance}]``,
    already in the COMMON shape (each provider's own ``fetch_window`` normalizes as it
    fetches). ``rows`` is likewise already normalized: ``[{external_refs, provider_account_id,
    date, amount, currency, description, memo, pending, supersedes_ref, raw}]`` — ``raw`` is
    the ORIGINAL provider record (the exact dict the wire response carried for that row),
    kept so ``connector_snapshots.stash_rows`` can persist real evidence a transaction drawer
    later shows, never a re-serialization of the normalized fields. ``complete`` is False
    only when the provider's own paging/response said there was more the call didn't return
    (used to gate the vanished-row check in ``connector_snapshots.note_missing`` — a
    truncated fetch must never conclude "the missing rows are gone").

    ``warnings`` is a human-readable caution list (e.g. Lunch Flow's polarity-smell-test
    tripwire) — ``connector_fetch.fetch()`` folds these into the store's ``last_caution``
    field via a plain ``store.update()`` (silently a no-op for a provider whose store has no
    such key). ``extra`` is a provider-specific display-only field bag (e.g. Alpaca's
    ``cash``/``buying_power`` read-out) applied the same no-op-safe way. Neither is money
    math — both are advisory/display only.
    """

    accounts: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    complete: bool = True
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Provider:
    """One external data source.

    ``note`` is the provider's OPERATIONAL FACTS — the things that are genuinely not true
    of its siblings (region/regulatory regime, what credentials linking it needs, what a
    fetch actually pulls and where it lands). It is rendered into the fetch tools'
    descriptions so every tool teaches the model what it's calling.

    ``lookback_days`` is the DEFAULT window a fetch tool uses when the caller passes no
    ``since`` (RFC §A step 1): wide enough that a late-posted row or a bank-side correction
    inside it is still seen on the next ordinary fetch, without the caller having to know
    to ask for it. Banks/Yodlee/IBKR: 14. Alpaca: 0 (a positions+cash SNAPSHOT — there is no
    date window to speak of).

    ``disabled`` is a policy withdrawal, not a configuration state — "not offered", never
    "not yet configured" (that is what ``public_status()`` already reports). It is read off
    the connector's kind in ``services.connector_catalog``, the one place it is declared. A
    disabled provider stays fully wired (module, config store, REST routes) and simply drops
    out of ``PROVIDER_KEYS``, the resolvable/offered set every lookup in this module
    validates against.
    """

    key: str
    label: str
    note: str
    lookback_days: int
    _fetch: tuple[str, str]
    _store: tuple[str, str]
    _classify: tuple[str, str]
    # The ``source`` this provider's transaction ``external_refs`` carry, when it isn't the
    # provider key. Enable Banking's rows have always been tagged "enablebanking"; renaming
    # that would stop booked rows matching the dedup floor and re-book them, so every
    # "already booked?" lookup reads it from here instead.
    _ref_source: str = ""

    @property
    def ref_source(self) -> str:
        return self._ref_source or self.key

    @property
    def disabled(self) -> bool:
        return CATALOG[self.key].disabled

    def _resolve(self, ref: tuple[str, str]) -> Any:
        module, attr = ref
        return getattr(importlib.import_module(module), attr)

    def store(self) -> Any:
        """A fresh instance of this provider's config store."""
        return self._resolve(self._store)()

    def fetch_window(
        self,
        *,
        since: str,
        before: str,
        account: Optional[str],
        accounts_only: bool = False,
        plan_id: Optional[str] = None,
    ) -> FetchWindow:
        """Call this provider's own ``fetch_window(store, since, before, account, *,
        accounts_only)`` — the ONE place ``connector_fetch.fetch()``/``provider_accounts()``
        reach into provider-specific code. Raises on a transport/auth failure — the caller
        classifies it via ``classify_error``."""
        # ``plan_id`` is YNAB's alone (which plan to read); no other provider takes it.
        extra = {"plan_id": plan_id} if plan_id else {}
        return self._resolve(self._fetch)(
            self.store(), since, before, account, accounts_only=accounts_only, **extra
        )

    def account_balances(self) -> dict:
        """The provider's accounts with their balances, no transactions — ``{accounts,
        complete}``. A provider module may offer a cheaper ``fetch_balances(store)`` (Plaid's
        free ``/accounts/get``); otherwise this is the accounts-only ``fetch_window`` the link
        picker already uses. Raises like ``fetch_window``."""
        module = importlib.import_module(self._fetch[0])
        reader = getattr(module, "fetch_balances", None)
        if reader is not None:
            return reader(self.store())
        from backend.core.user_time import user_today

        today = user_today().isoformat()
        window = self.fetch_window(since=today, before=today, account=None, accounts_only=True)
        return {"accounts": list(window.accounts), "complete": bool(window.complete)}

    def classify_error(self, exc: Exception) -> str:
        """This provider's own exception → status taxonomy (``auth_error`` |
        ``rate_limited`` | ``connect_error`` | ``error`` | a provider-specific status like
        YNAB's ``needs_budget_selection``) — each provider module keeps its own, since the
        exception shapes its wire client raises differ."""
        return self._resolve(self._classify)(exc)

    def public_status(self) -> dict:
        """The provider's non-secret config + fetch status (no credentials, ever)."""
        return self.store().public()


# Ordered: bank aggregators by region, then the two brokers.
PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            key="simplefin",
            label=CATALOG["simplefin"].label,
            note="US/CA banks + cards; linked by claiming a one-time setup token.",
            lookback_days=14,
            _fetch=("backend.services.aggregation.simplefin", "fetch_window"),
            _classify=("backend.services.aggregation.simplefin", "classify_error"),
            _store=("backend.db", "SimpleFinConfigStore"),
        ),
        Provider(
            key="plaid",
            label=CATALOG["plaid"].label,
            note="US/CA banks; credentials + a linked Item; sandbox or production.",
            lookback_days=14,
            _fetch=("backend.services.aggregation.plaid", "fetch_window"),
            _classify=("backend.services.aggregation.plaid", "classify_error"),
            _store=("backend.db", "PlaidConfigStore"),
        ),
        Provider(
            key="lunch_money",
            label=CATALOG["lunch_money"].label,
            note="A personal token.",
            lookback_days=14,
            _fetch=("backend.services.aggregation.lunch_money", "fetch_window"),
            _classify=("backend.services.aggregation.lunch_money", "classify_error"),
            _store=("backend.db", "LunchMoneyConfigStore"),
        ),
        Provider(
            key="lunch_flow",
            label=CATALOG["lunch_flow"].label,
            note=(
                "OAuth bank-link across 30+ countries; a developer client_id/secret pair "
                "plus a linked bank."
            ),
            lookback_days=14,
            _fetch=("backend.services.aggregation.lunch_flow", "fetch_window"),
            _classify=("backend.services.aggregation.lunch_flow", "classify_error"),
            _store=("backend.db.lunch_flow_config_store", "LunchFlowConfigStore"),
        ),
        Provider(
            key="ynab",
            label=CATALOG["ynab"].label,
            note="A personal token; it may see several plans — name one with plan_id (ynab_list_plans).",
            lookback_days=14,
            _fetch=("backend.services.aggregation.ynab", "fetch_window"),
            _classify=("backend.services.aggregation.ynab", "classify_error"),
            _store=("backend.db", "YnabConfigStore"),
        ),
        Provider(
            key="enable_banking",
            label=CATALOG["enable_banking"].label,
            note="EU PSD2; an application id + private key + a per-bank/country session.",
            lookback_days=14,
            _fetch=("backend.services.aggregation.enable_banking", "fetch_window"),
            _classify=("backend.services.aggregation.enable_banking", "classify_error"),
            _store=("backend.db", "EnableBankingConfigStore"),
            _ref_source="enablebanking",
        ),
        Provider(
            key="gocardless",
            label=CATALOG["gocardless"].label,
            note=(
                "EU/UK PSD2; existing GoCardless accounts only. Banks may allow 4 reads per "
                "account per day: on 'rate_limited', wait for the reset it names."
            ),
            lookback_days=14,
            _fetch=("backend.services.aggregation.gocardless", "fetch_window"),
            _classify=("backend.services.aggregation.gocardless", "classify_error"),
            _store=("backend.db", "GoCardlessConfigStore"),
        ),
        Provider(
            key="yodlee",
            label="Yodlee (Envestnet)",
            note="Global; API credentials + a linked user account; sandbox or production.",
            lookback_days=14,
            _fetch=("backend.services.aggregation.yodlee", "fetch_window"),
            _classify=("backend.services.aggregation.yodlee", "classify_error"),
            _store=("backend.db.yodlee_config_store", "YodleeConfigStore"),
        ),
        Provider(
            key="alpaca",
            label=CATALOG["alpaca"].label,
            note=(
                "Broker; a positions + cash SNAPSHOT (not a date-windowed transaction "
                "feed); paper or live; read-only — this connector never places orders."
            ),
            lookback_days=0,
            _fetch=("backend.services.aggregation.alpaca", "fetch_window"),
            _classify=("backend.services.aggregation.alpaca", "classify_error"),
            _store=("backend.db", "AlpacaConfigStore"),
        ),
        Provider(
            key="ibkr",
            label="Interactive Brokers (Flex Web Service)",
            note=(
                "Broker, via the Flex Web Service (Flex token + query id, format must be XML); "
                "positions plus trades and cash events, windowed client-side over the report; "
                "equities via yfinance, options/futures manual-priced; two-step polling is "
                "slow; the token cannot place orders."
            ),
            lookback_days=14,
            _fetch=("backend.services.aggregation.ibkr", "fetch_window"),
            _classify=("backend.services.aggregation.ibkr", "classify_error"),
            _store=("backend.db.ibkr_config_store", "IbkrConfigStore"),
        ),
    )
}

#: The OFFERED/resolvable providers — every registered key EXCEPT one withdrawn via
#: ``Provider.disabled`` (Yodlee). ``resolve()`` validates against this, not ``PROVIDERS``,
#: so a disabled provider is refused the exact same way a genuinely unknown key is.
PROVIDER_KEYS: tuple[str, ...] = tuple(p.key for p in PROVIDERS.values() if not p.disabled)


class UnknownProvider(ValueError):
    """Raised with a message that NAMES the valid providers — the caller is usually a
    model that guessed a plausible-sounding key and can fix itself in one round-trip."""

    def __init__(self, bad: list[str]) -> None:
        self.bad = bad
        super().__init__(
            f"unknown connector provider(s): {', '.join(sorted(bad))}. "
            f"Valid providers are: {', '.join(PROVIDER_KEYS)}."
        )


def resolve(keys: list[str]) -> list[Provider]:
    """Providers for ``keys``, in the table's canonical order, deduped.

    Raises ``UnknownProvider`` listing EVERY bad key at once (not just the first), so one
    correction round-trip can fix a whole malformed list.
    """
    wanted = {str(k or "").strip().lower() for k in keys}
    wanted.discard("")
    bad = sorted(wanted - set(PROVIDER_KEYS))
    if bad:
        raise UnknownProvider(bad)
    return [PROVIDERS[k] for k in PROVIDER_KEYS if k in wanted]


def status(keys: list[str]) -> dict:
    """``{provider_key: public_status}`` for each requested provider.

    A store that fails to read is reported as an error ENTRY rather than taking the whole
    call down — one broken provider config must never hide the others.
    """
    out: dict[str, dict] = {}
    for provider in resolve(keys):
        try:
            out[provider.key] = provider.public_status()
        except Exception as exc:  # noqa: BLE001 — one bad config never blinds the rest
            logger.warning("get_connector_status: %s failed to read: %s", provider.key, exc)
            out[provider.key] = {"status": "error", "error": str(exc)}
    return out


def ref_source_of(provider_key: str) -> str:
    """The ``external_refs`` source a provider's booked rows carry (its key, unless it says
    otherwise) — what every "is this row already booked?" lookup must match on."""
    provider = PROVIDERS.get(provider_key) if isinstance(PROVIDERS, dict) else None
    return provider.ref_source if provider is not None else provider_key
