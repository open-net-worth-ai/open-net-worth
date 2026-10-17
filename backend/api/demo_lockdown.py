"""Routes a PUBLIC demo (``DEMO_MODE``) refuses, whoever is signed in.

A demo hands every visitor the operator (published password, or no password at all under
``DEMO_OPEN_ACCESS``), so each visitor IS the operator. A visitor may do anything to the demo's own
fabricated book; nothing that reaches past it. Two rules, both inert unless ``settings.demo_mode``:

1. **Writes are an allowlist.** A POST/PUT/PATCH/DELETE is refused unless its path is under one of
   ``_WRITABLE`` — the book's own domains (ledger, planning, investments, assets, equity, …) and a
   visitor's own conversations. A route nobody classified is therefore refused, not open: adding a
   router cannot silently widen the demo. ``tests/test_demo_route_classification.py`` additionally
   fails on any mutating route not in its reviewed snapshot, so a new route under an allowed
   prefix is reviewed too.
2. **``_LOCKED`` is refused regardless of method or the allowlist** — the reads a visitor must not
   have, and the named families the ADRs call out. What it covers:

   - reading a stored secret back (the model-provider key, a connector credential);
   - changing where the instance's AI credentials are sent — a visitor who repoints the
     OpenRouter ``base_url`` at their own server receives the key in the next request's
     ``Authorization`` header, so hiding the key is not enough on its own;
   - anything that runs code or a process on the host: the Builder (a coding agent with a shell),
     external MCP servers (``stdio`` is arbitrary local code), extensions, the in-browser
     Terminal, the desktop app's folder grants, the inbound ``/mcp`` endpoint;
   - changing or removing the password, any auth setting, or reading or rotating the instance API
     token (and the phone-shortcut setup code and setup page, which show that token);
   - the book-move protocol, ``/api/reset``, backup and import/export of the whole book — each
     replaces or carries off the one book every visitor shares;
   - automations (create, edit, run: they spend tokens unattended and can send data out),
     skills, memory, notification channels, the monitoring schedule (``/api/watchlist/config``), the match judge's model and cap
     and every provider/connector/integration config;
   - the host's logs and filesystem probes, turn checkpoints (they carry other visitors'
     conversations and restore the shared book), and the QA routes.

The matching store-level guards (``ModelConfigStore`` writes, the code-execution and
script-skill switches in ``app_config_store``, the Builder service) cover the same ground for
Lucius tools and direct callers, which never pass through these routes.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from backend.api import demo_visitor
from backend.core.config import get_settings

_WRITE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ANY = frozenset({"GET", "HEAD", *_WRITE})

_Rule = tuple[frozenset[str], re.Pattern[str]]


def _rules(methods: frozenset[str], *patterns: str) -> tuple[_Rule, ...]:
    return tuple((methods, re.compile(p)) for p in patterns)


_LOCKED: tuple[_Rule, ...] = (
    # secrets, credentials, auth state
    *_rules(
        _ANY,
        r"^/api/model-config/providers/[^/]+/reveal-key$",
        r"^/api/connectors/[^/]+/secret/[^/]+$",
        r"^/api/shortcuts/(setup|file|setup-code)(/[^/]+)?$",
        r"^/api/auth/api-token(/rotate)?$",
    ),
    *_rules(
        _WRITE,
        r"^/api/model-config(/.*)?$",
        r"^/api/preferred-models$",
        r"^/api/auth/(change|remove)-password$",
        r"^/api/auth/settings$",
        r"^/api/shortcuts/prepare$",
        r"^/api/(connectors|tool-permissions)(/.*)?$",
        r"^/api/search/config(/.*)?$",
        r"^/api/config(/.*)?$",
    ),
    # code and processes on the host
    *_rules(
        _ANY,
        r"^/api/builder(/.*)?$",
        r"^/api/terminal(/.*)?$",
        r"^/api/desktop(/.*)?$",
        r"^/api/mcp-oauth(/.*)?$",
        r"^/api/mcp-clients/oauth(/.*)?$",
        r"^/mcp(/.*)?$",
    ),
    *_rules(
        _WRITE, r"^/api/mcp-clients(/.*)?$", r"^/api/extensions(/.*)?$", r"^/api/skills(/.*)?$"
    ),
    # the shared book as a whole
    *_rules(
        _ANY,
        r"^/api/move(/.*)?$",
        r"^/api/backup(/.*)?$",
        r"^/api/data/(export|import)(/.*)?$",
    ),
    *_rules(_WRITE, r"^/api/reset$", r"^/api/demo/seed$"),
    # Delete permanently: no undo, and every visitor shares the one fabricated book.
    *_rules(_WRITE, r"^/api/transactions/purge$"),
    # spend and outbound reach
    *_rules(
        _WRITE,
        r"^/api/automations(/.*)?$",
        r"^/api/memory(/.*)?$",
        r"^/api/channels(/.*)?$",
        r"^/api/chat-pairing(/.*)?$",
        r"^/api/cost/(budget|log)$",
        r"^/api/intel/(scan|sources)(/.*)?$",
        r"^/api/(alpaca|ibkr|plaid|enable-banking|lunch-flow|lunch-money|simplefin|yodlee|ynab"
        r"|telegram|email|market-data|integrations|account-linking)(/.*)?$",
        r"^/api/setup-status/complete$",
        r"^/api/watchlist/config$",
        r"^/api/matches/config$",
    ),
    *_rules(_ANY, r"^/api/lunch-flow/oauth(/.*)?$"),
    # the host and other visitors
    *_rules(
        _ANY,
        r"^/api/logs$",
        r"^/api/qa(/.*)?$",
        r"^/api/setup-status/(data-location|inspect-path)$",
        r"^/api/checkpoints(/.*)?$",
        r"^/api/artifacts$",
    ),
)

# The book's own domains. A write outside these is refused (rule 1 above).
_WRITABLE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^/api/(action-status|actions/[^/]+/status)$",
        r"^/api/alerts(/[^/]+)?$",
        r"^/api/assets(/.*)?$",
        r"^/api/auth/(login|logout)$",
        r"^/api/targets(/[^/]+)?$",
        r"^/api/capture$",
        r"^/api/(chat|sessions)(/.*)?$",
        r"^/api/commitments(/.*)?$",
        r"^/api/dashboard/layout$",
        r"^/api/equity(/.*)?$",
        r"^/api/(income|options|lots|positions|disposals|instruments|rebalance|recurring)(/.*)?$",
        r"^/api/intel/(assets|signals)(/.*)?$",
        r"^/api/(accounts|categories|institutions|transactions|payees|matches)(/.*)?$",
        r"^/api/mentions/resolve$",
        r"^/api/(inbox|notification-log)(/.*)?$",
        r"^/api/(plan|planning)(/.*)?$",
        r"^/api/profile(/.*)?$",
        # File ingestion is open (owner decision 2026-10-08): the demo exists to show statement
        # import, receipts and Documents working, and every visitor knows the book is public.
        # Size limits are the app's own; parsing spends the visitor's and the instance's budget.
        r"^/api/uploads?(/.*)?$",
        r"^/api/statements(/.*)?$",
        r"^/api/vault(/.*)?$",
        r"^/api/watchlists?(/.*)?$",
    )
)

DEMO_LOCKED = HTTPException(
    status_code=403,
    detail={"code": "demo_locked", "message": "This is disabled on the public demo."},
)


def is_demo_locked(method: str, path: str) -> bool:
    """True when ``method path`` is refused on a demo instance (regardless of demo mode)."""
    if any(method in methods and pattern.match(path) for methods, pattern in _LOCKED):
        return True
    return method in _WRITE and not any(pattern.match(path) for pattern in _WRITABLE)


def refuse_on_demo(conn: HTTPConnection) -> None:
    """App-wide dependency: while ``DEMO_MODE`` is on, 403 a locked route, then scope and meter
    the rest per visitor (``demo_visitor.guard``); else allow.

    Takes the connection, not the request, so it also guards a WebSocket handshake (the
    Terminal), which has no HTTP method of its own and counts as a GET."""
    settings = get_settings()
    if not settings.demo_mode:
        return
    method = conn.scope.get("method", "GET")
    if is_demo_locked(method, conn.url.path):
        raise DEMO_LOCKED
    demo_visitor.guard(conn)
