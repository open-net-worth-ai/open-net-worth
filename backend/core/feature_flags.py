"""Feature flags — the ONE place a flag is declared, and the order its value is resolved in.

A flag is a boolean that selects between two COMPLETE code paths at a single seam, so a feature
big enough to need one can ship dark and be switched on by the owner. Off means exactly the old
behaviour, running the old code (ADR ``2026-10-25-feature-flags-off-means-the-old-code``; the
developer rules are in ``docs/FEATURE_FLAGS.md``).

Leaf module, stdlib + ``core`` only. It holds the declarations and the pure resolution order;
the stored value lives in ``app_config`` and is read by ``services.feature_flags``, which is
the accessor every caller uses (``enabled(name)``, named after OpenFeature's boolean
evaluation so a later swap stays cheap). Code below ``services`` that needs a flag takes the
choice from its caller — the seam always sits above it.

Resolution, first match wins:

1. a test override (``override()``, used by the pytest helpers) — tests only;
2. the environment, ``ONW_FLAG_<NAME>`` = ``1``/``0``/``true``/``false`` — set when the app
   starts, by whoever runs it: dev, CI, QA and the public demo's deploy;
3. the public demo (``DEMO_MODE``) — the declared default; a stored choice is never read there,
   since every demo visitor is the operator;
4. the owner's stored choice (Settings → Developer → Experimental);
5. the declared default.

An unknown name RAISES (``UnknownFeatureFlagError``), never reads as ``False``.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Literal, Mapping, Optional

from backend.core.errors import UnknownFeatureFlagError, ValidationError

_log = logging.getLogger(__name__)

FlagKind = Literal["release", "kill_switch"]
FlagSource = Literal["test", "demo", "env", "stored", "default"]

ENV_PREFIX = "ONW_FLAG_"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_KINDS = ("release", "kill_switch")
_TRUE = {"1", "true"}
_FALSE = {"0", "false"}


@dataclass(frozen=True)
class Flag:
    """One declared flag. ``title`` is its label in Settings — written out, acronyms cased
    (``MCP``, not ``Mcp``); ``name`` is the fixed code key. ``description`` is shown to the owner — plain words, what turning it
    on changes. ``remove_by`` is when the old path and the flag must be gone (CI fails after)."""

    name: str
    title: str
    description: str
    default: bool
    owner: str
    added: date
    remove_by: date
    kind: FlagKind = "release"


# THE REGISTRY. Declare a flag here and nowhere else; retire it by deleting its entry in the
# same PR that deletes its old code path. The test suite declares its own example flag
# (``tests/flags.py``) on top of these.
FLAGS: tuple[Flag, ...] = (
    Flag(
        name="claude_cli_stable_tool_prompt",
        title="Stable tool prompt on the Claude Code CLI",
        description=(
            "With Lucius on the Claude Code CLI, a tool Lucius loads mid-conversation is "
            "introduced in the conversation instead of in its instructions, so the CLI keeps "
            "its running process and prompt cache. A turn that loads a tool then reads the "
            "cached prompt instead of writing ~40K tokens to cache again: faster, and less of "
            "your plan's usage. On by default. Off: every tool load restarts the CLI with the "
            "full tool list, as before. More in Help: Models and cost control."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 18),
        remove_by=date(2027, 2, 28),
        kind="release",
    ),
    Flag(
        name="permissions_v2",
        title="Permissions v2",
        description=(
            "A new permissions model. Each tool has one setting: Always allow, Ask or Block. "
            "In Auto mode a model judges each Ask call and only stops you when it is unsure. "
            "Approval cards answer Approve, Always allow or Deny. Automations get their own "
            "Always allowed and Blocked tool lists. Settings > Permissions lists the built-in "
            "tools, and a connector's tools are set on its own page. The YOLO and "
            "unattended-script switches are retired while this is on. "
            "Details: Lucius > Permissions v2."
        ),
        default=True,
        owner="owner",
        added=date(2026, 10, 31),
        remove_by=date(2027, 1, 31),
        kind="release",
    ),
    Flag(
        name="search_reads_results",
        title="Web search reads its results",
        description=(
            "Web search reads the pages behind its top three results and hands Lucius the "
            "passages that match your search, instead of only the search engine's snippet. "
            "A search takes a few seconds longer and returns more text; with Tavily or "
            "Firecrawl each page read is billed by the provider. The separate X search tool "
            "is hidden (Lucius searches X through an ordinary search), and an Automation that "
            "allows X search allows web search. Off: searches return snippets only, as before. "
            "More in Help: Web search reads its results (Experimental)."
        ),
        default=True,
        owner="owner",
        added=date(2026, 10, 31),
        remove_by=date(2027, 1, 31),
        kind="release",
    ),
    Flag(
        name="followed_portfolios",
        title="Followed portfolios",
        description=(
            "Copy Trading in Intelligence: follow a famous fund or a House member, see what they "
            "hold, and copy their trades with Lucius."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 10),
        remove_by=date(2027, 2, 28),
        kind="release",
    ),
    Flag(
        name="intel_delivery_decider",
        title="Intel delivery timing",
        description=(
            "Lucius decides which Intel news reaches you now, which waits for a roundup, "
            "and which stays in the Intel tab."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 2),
        remove_by=date(2027, 1, 31),
        kind="release",
    ),
    Flag(
        name="ynab_import_and_sync",
        title="YNAB import and sync",
        description=(
            "Import a whole YNAB plan, then keep it and this book in step on a schedule: from "
            "YNAB, to YNAB or both ways. Adds the Data Sync tab in Automations."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 2),
        remove_by=date(2027, 2, 1),
    ),
    Flag(
        name="allow_brokerage_total_valuation",
        title="Brokerage total valuation",
        description=(
            "Investment and retirement accounts are valued by Brokerage total or Live holdings "
            "(Edit account → Value this account by). A typed balance is the account's total, "
            "and holdings you add later are carved out of it instead of counting twice. A "
            "Brokerage total account linked through Plaid, SimpleFIN, Yodlee, Alpaca or YNAB "
            "follows the total that connector reports, read about every 6 hours without "
            "Lucius. Off: the account is worth its holdings plus its balance, as before."
        ),
        default=False,
        owner="owner",
        added=date(2026, 11, 10),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="actual_budget_sync",
        title="Actual Budget sync",
        description=(
            "Connect an Actual server, import a whole budget and keep it and this book "
            "in step on a schedule. Shares the Data Sync tab in Automations with YNAB."
        ),
        default=False,
        owner="owner",
        added=date(2026, 11, 5),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="voice_calls",
        title="Voice calls",
        description=(
            "Talk to Lucius out loud: a Call button in the chat starts a voice call with your "
            "microphone. Set it up in Connectors → Voice calls."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 3),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="price_alert_lucius_run",
        title="Price alerts run Lucius",
        description=(
            "When a price alert fires, Lucius looks into it before you hear about it: it follows "
            "the alert's instructions by searching the web and X. A crash-watch alert on a holding "
            "gets the same search for why it fell. It can delay an alert by up to two minutes and "
            "uses your search quota and AI model. Off: you get the quick alert only."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 6),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="bank_data_sync",
        title="Bank sync without AI",
        description=(
            "Book a bank connector's transactions without AI. On the GoCardless, Enable Banking, "
            "Plaid or SimpleFIN connector, choose Sync without AI: each linked account you tick gets the "
            "history the bank serves, then new rows on a schedule. Changes the bank makes "
            "afterwards wait for you in a review list. Bank syncs join the Data Sync tab in "
            "Automations. Off: only a Lucius Automation books bank rows, as before."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 13),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="inline_visuals",
        title="Inline visuals in chat",
        description=(
            "Lucius can answer with a small chart, figures or a timeline inside its reply when "
            "a picture says it better: a trend, a breakdown, a comparison. In those, every "
            "number comes straight from the app's own data. For anything else he draws the "
            "picture himself, with numbers he writes in from what he read, so check one that "
            "matters. Off: replies are text and tables only."
        ),
        default=True,
        owner="owner",
        added=date(2026, 11, 17),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="mcp_oauth",
        title="MCP OAuth sign-in",
        description=(
            "Connect claude.ai, Claude on your phone, Claude Desktop's custom connectors and "
            "ChatGPT to your book over the internet. They sign in with OAuth: you type your "
            "password on a consent page and choose read-only or read and change. Needs a public "
            "HTTPS address (Settings > MCP), a password, and \"Don't ask for the password on "
            'this computer" off. Settings > MCP lists connected apps and revokes them. Off: '
            "only the API token opens /mcp, as before."
        ),
        default=False,
        owner="owner",
        added=date(2026, 11, 16),
        remove_by=date(2027, 3, 1),
    ),
    Flag(
        name="paper_trading",
        title="Paper Trading",
        description=(
            "Paper Trading: try trades with pretend money at real market prices, in "
            "Intelligence → Paper Trading or through Lucius, for shares, funds, crypto, metals, "
            "currencies, indices and assets you price yourself. A paper portfolio never touches "
            "your accounts, net worth or budget, and never sends an order to a broker. Off: the "
            "tab is hidden, Lucius's paper tools refuse, and paper portfolios are kept."
        ),
        default=False,
        owner="owner",
        added=date(2026, 11, 15),
        remove_by=date(2027, 5, 15),
    ),
    Flag(
        name="trading_agents",
        title="TradingAgents",
        description=(
            "Multi-agent stock research from the asset page and through Lucius: analysts, a "
            "bull and bear debate, a trader, a risk debate and a portfolio manager end in a "
            "five-step rating (Buy to Sell) with written reports. It runs the open-source "
            "TradingAgents project in its own environment, and every model call goes through "
            "your own AI provider with spend caps you set; it never places an order. Choose "
            "what it may know about your holdings: your investments, only the stock being "
            "analysed, or nothing. Off: the connector, the card and Lucius's tools are hidden "
            "and past runs are kept."
        ),
        default=False,
        owner="owner",
        added=date(2026, 11, 19),
        remove_by=date(2027, 5, 19),
    ),
)


def build_registry(flags: tuple[Flag, ...]) -> dict[str, Flag]:
    """Validate a declaration list and index it by name. Raises on any malformed entry."""
    registry: dict[str, Flag] = {}
    for flag in flags:
        if not _NAME_RE.match(flag.name):
            raise ValidationError(f"feature flag name {flag.name!r} must be snake_case")
        if flag.name in registry:
            raise ValidationError(f"feature flag {flag.name!r} is declared twice")
        if flag.kind not in _KINDS:
            raise ValidationError(f"feature flag {flag.name!r} has unknown kind {flag.kind!r}")
        if not flag.title.strip() or not flag.description.strip() or not flag.owner.strip():
            raise ValidationError(
                f"feature flag {flag.name!r} needs a title, a description and an owner"
            )
        if flag.remove_by <= flag.added:
            raise ValidationError(f"feature flag {flag.name!r}: remove_by must follow added")
        registry[flag.name] = flag
    return registry


REGISTRY: dict[str, Flag] = build_registry(FLAGS)

_test_overrides: dict[str, bool] = {}


def get(name: str) -> Flag:
    flag = REGISTRY.get(name)
    if flag is None:
        raise UnknownFeatureFlagError(f"unknown feature flag {name!r}", name=name)
    return flag


def all_flags() -> list[Flag]:
    """Newest first (by ``added``), then by name — the order Settings shows them in."""
    by_name = sorted(REGISTRY.values(), key=lambda f: f.name)
    return sorted(by_name, key=lambda f: f.added, reverse=True)


def env_var(name: str) -> str:
    return ENV_PREFIX + name.upper()


def env_value(name: str, environ: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    """The ``ONW_FLAG_<NAME>`` override, or None when unset. A value that is not
    1/0/true/false is logged and IGNORED — one bad line in a compose file must not take every
    flag (and the Experimental list) down with it. Tests choose flags with ``--flag``, which
    does refuse a bad value."""
    raw = (environ if environ is not None else os.environ).get(env_var(name))
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    _log.warning("%s=%r ignored: use 1, 0, true or false", env_var(name), raw)
    return None


@dataclass(frozen=True)
class Resolution:
    value: bool
    source: FlagSource


def resolve(
    name: str,
    *,
    stored: Optional[bool],
    demo: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> Resolution:
    """The value of ``name`` and where it came from, in the order the module docstring lists."""
    flag = get(name)
    if name in _test_overrides:
        return Resolution(_test_overrides[name], "test")
    from_env = env_value(name, environ)
    if from_env is not None:
        return Resolution(from_env, "env")
    if demo:
        return Resolution(flag.default, "demo")
    if stored is not None:
        return Resolution(bool(stored), "stored")
    return Resolution(flag.default, "default")


@contextmanager
def override(**values: bool) -> Iterator[None]:
    """Force flags for the duration of a ``with`` block — the test seam behind the pytest
    helpers. It beats every other source, the demo included. Process-wide, not context-local,
    so a ``TestClient`` request served on another thread sees it too."""
    for name in values:
        get(name)
    saved = dict(_test_overrides)
    _test_overrides.update({k: bool(v) for k, v in values.items()})
    try:
        yield
    finally:
        _test_overrides.clear()
        _test_overrides.update(saved)


def expired(today: date, flags: Optional[list[Flag]] = None) -> list[Flag]:
    """Flags whose ``remove_by`` has passed — the CI lint fails on any."""
    return [f for f in (flags if flags is not None else all_flags()) if f.remove_by < today]
