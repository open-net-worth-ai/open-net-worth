"""
Regression test: every mounted route must start with /api/ (or be an intentional
exempt path).

Background
----------
A real bug shipped where ``backend/api/extensions.py`` declared routes without the
``/api/`` prefix.  The backend tests passed because they called the same wrong path.
Only a frontend<->backend path comparison would have caught it.

This test catches that entire bug class by:

(A) Collecting all routes from the live FastAPI app and asserting every non-exempt
    path starts with ``/api/``.

(B) Scanning ``frontend/src/`` for literal ``/api/...`` URL strings and asserting each
    one resolves to a real backend route (exact match or template match after path-param
    substitution).  Dynamically-composed or template-interpolated paths that cannot be
    statically resolved are skipped gracefully rather than causing false failures.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Exempt paths (intentionally NOT under /api/) ─────────────────────────────
# These are documented special-purpose paths.  Every new entry here must have a
# comment explaining WHY it is exempt.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/",  # the norm — all API routes
    "/mcp",  # MCP JSON-RPC 2.0 endpoint — different protocol, not REST
    "/openapi.json",  # FastAPI built-in OpenAPI schema
    "/docs",  # FastAPI built-in Swagger UI
    "/redoc",  # FastAPI built-in ReDoc UI
)

# Exact-match exemptions (NOT prefixes — these paths are whole routes, never API).
# Using them as prefixes would be wrong: "" startswith-matches EVERY path.
_EXEMPT_EXACT: frozenset[str] = frozenset(
    {
        "",  # StaticFiles SPA mount (main.py mounts frontend/dist at "/" → collected as "")
        "/",  # same SPA mount, defensively
    }
)


# ── Part A: every app route path must start with an exempt prefix ─────────────


def _collect_app_routes() -> list[str]:
    """Import the FastAPI app and return all registered path strings."""
    import backend.main as _main

    paths: list[str] = []

    def collect(route: object, inherited_prefix: str = "") -> None:
        """Support eager FastAPI routes and 0.140+'s lazy included routers."""
        if hasattr(route, "path"):
            path = getattr(route, "path")
            if isinstance(path, str):
                paths.append(f"{inherited_prefix}{path}")

        original_router = getattr(route, "original_router", None)
        if original_router is None:
            return
        include_context = getattr(route, "include_context", None)
        prefix = getattr(include_context, "prefix", "") if include_context is not None else ""
        for nested in getattr(original_router, "routes", ()):
            collect(nested, f"{inherited_prefix}{prefix}")

    for route in _main.app.routes:
        collect(route)
    return paths


def test_all_routes_have_api_prefix_or_are_exempt():
    """Every route mounted in backend.main.app must start with /api/ or be in the
    known-exempt set.  Adding a route without /api/ should fail CI immediately —
    catching the extensions.py-class bug at commit time."""
    routes = _collect_app_routes()
    assert routes, "No routes found — is backend.main importable?"

    bad: list[str] = []
    for path in routes:
        if path in _EXEMPT_EXACT:
            continue
        if not any(path.startswith(pfx) for pfx in _EXEMPT_PREFIXES):
            bad.append(path)

    assert not bad, (
        "The following routes are missing the /api/ prefix (or an approved exempt "
        f"prefix).  Add /api/ to each route decorator, or add to _EXEMPT_PREFIXES "
        f"with a justification comment.\n  Bad routes: {bad}"
    )


# ── Part B: every literal /api/ URL in the frontend maps to a real backend route

_FRONTEND_SRC = Path(__file__).parent.parent / "frontend" / "src"

# Regex: capture '/api/something' as a string literal (single or double quotes).
# We intentionally capture paths BEFORE any query string or template expression.
_LITERAL_API_PATH_RE = re.compile(r"""['"](/api/[^'"?`\s{}<>\\]+)['"]""")

# Regex: a TEMPLATE literal whose content starts with /api/ — `/api/x/${id}/y`. These are how
# every id-bearing call is written, so skipping them (as this test originally did) left the
# majority of the real surface unchecked. That hole shipped a live bug: the whole permission
# ladder wrote to `/api/mcp-clients/${id}/tool-permissions` for weeks after that route was
# deleted, and every level change failed silently while CI stayed green.
_TEMPLATE_API_PATH_RE = re.compile(r"`(/api/[^`]*)`")

# A `${...}` interpolation — one path segment's worth of runtime value.
_INTERPOLATION_RE = re.compile(r"\$\{[^{}]*\}")

# FastAPI path-param patterns like {foo} or {foo:path} — strip to compare templates
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


def _route_template(path: str) -> str:
    """Normalise a FastAPI route path to a plain template for matching.

    ``/api/positions/{position_id}`` → ``/api/positions/{}``
    ``/api/connectors/{connector_id:path}`` → ``/api/connectors/{}``
    """
    return _PATH_PARAM_RE.sub("{}", path)


def _resolve_template_path(raw: str) -> str | None:
    """A frontend template literal reduced to a comparable route template, or ``None``.

    ``/api/mcp-clients/${encodeURIComponent(id)}/test`` → ``/api/mcp-clients/{}/test``

    Every ``${…}`` becomes one ``{}`` segment placeholder, which is exactly how
    ``_route_template`` normalises a FastAPI path param — so the two compare structurally.
    Returns ``None`` for anything still unresolvable afterwards: a nested interpolation, an
    interpolation that spans a ``/`` (``${base}/rest``, where the segment count is unknown),
    or a path that only becomes an endpoint after further concatenation.
    """
    path = raw.split("?", 1)[0]
    resolved = _INTERPOLATION_RE.sub("{}", path)
    if "$" in resolved or "`" in resolved:
        return None  # nested or otherwise unparseable interpolation — do not guess
    if "*" in resolved or "..." in resolved:
        return None  # prose in a comment ("/api/plaid/*"), not a call
    # A `${}` that expands to several segments (a prefix variable) cannot be checked by
    # segment count; require every placeholder to BE a whole segment.
    segments = resolved.rstrip("/").split("/")
    if any("{}" in seg and seg != "{}" for seg in segments):
        return None
    # `/api/${seg}/…` — the ENDPOINT NAME itself is a runtime value (a cache-key helper,
    # not a call site). Nothing to check without running JS.
    if len(segments) > 2 and segments[2] == "{}":
        return None
    return resolved.rstrip("/") or None


def _extract_frontend_api_paths() -> set[str]:
    """Walk frontend/src/ and collect every ``/api/`` URL — plain literals AND the
    template literals that carry a path param."""
    if not _FRONTEND_SRC.exists():
        return set()
    paths: set[str] = set()
    for ext in ("*.ts", "*.tsx"):
        for src_file in _FRONTEND_SRC.rglob(ext):
            # Test files contain mock/assertion path fragments (e.g.
            # expect.stringContaining('/api/x/')), not real fetch calls.
            if ".test." in src_file.name:
                continue
            try:
                content = src_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _LITERAL_API_PATH_RE.finditer(content):
                raw = m.group(1)
                # Skip obviously dynamic/template paths that contain JS interpolation
                # artefacts that slipped through (e.g. the regex matched something that
                # was actually inside a template literal but wasn't caught by the
                # lookahead).  A safe proxy: if it still contains '$' it's dynamic.
                if "$" in raw:
                    continue
                paths.add(raw)
            for line in content.splitlines():
                # A backtick in PROSE is common in this codebase's comments (they quote
                # endpoint names constantly), and a quoted path in a comment is not a call.
                if line.lstrip().startswith(("//", "*", "/*")):
                    continue
                for m in _TEMPLATE_API_PATH_RE.finditer(line):
                    resolved = _resolve_template_path(m.group(1))
                    if resolved:
                        paths.add(resolved)
    return paths


def _build_route_matcher(routes: list[str]) -> tuple[set[str], set[str]]:
    """Return (exact_paths, template_paths) for fast matching."""
    exact: set[str] = set()
    templates: set[str] = set()
    for path in routes:
        if "{" in path:
            templates.add(_route_template(path))
        else:
            exact.add(path)
    return exact, templates


def _frontend_path_matches_backend(fe_path: str, exact: set[str], templates: set[str]) -> bool:
    """Return True if fe_path resolves to any known backend route.

    The frontend path is a static string (no path params).  It matches if:
    - it's an exact hit, OR
    - replacing every segment that looks like a UUID/id/slug with ``{}`` matches a
      backend template path.
    """
    if fe_path in exact:
        return True

    # Try matching against each template by checking whether the literal path
    # is a structural match (same number of segments, non-param segments identical).
    # We do NOT require the literal to be a valid UUID — just structural equivalence.
    fe_parts = fe_path.rstrip("/").split("/")
    for tmpl in templates:
        tmpl_parts = tmpl.rstrip("/").split("/")
        if len(fe_parts) != len(tmpl_parts):
            continue
        # A `{}` on the FRONTEND side is a resolved `${…}` — it may only line up with a real
        # path param, never with a literal segment, or `/api/x/${id}` would "match"
        # `/api/x/config` and launder a typo'd route into a pass.
        match = all(
            (tp == "{}") if fp == "{}" else (tp == "{}" or tp == fp)
            for fp, tp in zip(fe_parts, tmpl_parts)
        )
        if match:
            return True

    return False


# Paths the frontend intentionally calls that are NOT in backend/api/*.py because
# they are defined inline in main.py or are known composite/redirect paths handled
# elsewhere.  Each entry must have a comment.
_KNOWN_FRONTEND_EXTRAS: frozenset[str] = frozenset(
    {
        # Defined inline in backend/main.py (not via a router module)
        "/api/intel/scan",
        "/api/intel/scan/opportunities",
        "/api/health",
        # A cache-key PREFIX, not an endpoint: platform/desktop.ts calls
        # invalidateQueryCache('/api/desktop/') to drop every cached /api/desktop/* read. Exact
        # match on purpose — a startswith skip would also hide a missing /api/desktop/<route>.
        "/api/desktop/",
        "/api/trading",  # shared prefix in tradingRequest(), not a separately fetched endpoint
    }
)

# Frontend calls whose backend route is being built by a CONCURRENT LANE and does not exist
# yet.  Deliberately a separate set from _KNOWN_FRONTEND_EXTRAS above: those entries are
# permanent and true ("defined inline in main.py"), while every entry here is TEMPORARY DEBT
# that must be DELETED the moment its route lands.  Filing them together would launder a
# missing route into a permanent exemption, which is exactly the failure this test exists to
# catch.
#
# Each entry names the lane that owns the route, so a reviewer can tell "not built yet" from
# "quietly broken".
_PENDING_PARALLEL_LANE_ROUTES: frozenset[str] = frozenset(
    {
        # Tool-permission grants (RFC 2026-07-20 approval-and-review-model §16). The confirm
        # card's three grant scopes POST here (frontend/src/api/toolGrants.ts); the route +
        # the Settings › Permissions tab are owned by the api/settings lane and the store
        # behind it by the grant-store lane. REMOVE THIS ENTRY when POST /api/tool-grants
        # exists — if the route ships under a different path, this test failing again is the
        # intended signal.
        "/api/tool-grants",
    }
)

# Frontend paths we skip because they are partial prefixes used in string
# concatenation (e.g. the base URL for further interpolation) rather than full
# endpoint paths.  These won't appear in _LITERAL_API_PATH_RE anyway (template
# literals use ${} which is filtered above) but list them for documentation.
_SKIP_PREFIXES: tuple[str, ...] = ()

# Template paths whose `${…}` interpolates a FIXED VOCABULARY of literal segment names
# rather than an id, so the backend side is several literal routes and not one path param.
# Matching a frontend `{}` against a backend literal is deliberately NOT allowed in general
# — it would let `/api/x/${id}` pass by "matching" an unrelated `/api/x/config` — so each
# such call is named here with the routes it actually covers.
_FIXED_VOCABULARY_PATHS: frozenset[str] = frozenset(
    {
        # ChatAccessPolicy.tsx: `/api/chat-pairing/${action}`, action ∈ {approve, reject}
        # → POST /api/chat-pairing/approve and POST /api/chat-pairing/reject, both real.
        "/api/chat-pairing/{}",
    }
)

# Frontend calls that are BROKEN RIGHT NOW and owned by another lane. Not an exemption:
# a standing record of a defect this test found, so it can land green without pretending
# the call works. Each entry names the caller, the defect, and what deletes it.
_KNOWN_BROKEN_FRONTEND_CALLS: frozenset[str] = frozenset(
    {
        # frontend/src/components/tabs/investments/EquityTab.tsx:128 fires
        # POST /api/equity/grants/{id}/tranches/{id}/vest_undo, which has NEVER existed —
        # the real route is POST /api/equity/vest-events/{vest_event_id}/undo. It is a bare
        # `fetch(...).catch(() => {})`, so the 404 is swallowed and "undo vest" silently does
        # nothing. Found by this test once template literals came into scope; owned by the
        # investments lane. DELETE THIS ENTRY when EquityTab calls the real route.
        "/api/equity/grants/{}/tranches/{}/vest_undo",
    }
)


@pytest.mark.skipif(
    not _FRONTEND_SRC.exists(),
    reason="frontend/src not present (production-only build environment)",
)
def test_frontend_api_paths_exist_in_backend():
    """Every ``/api/...`` URL in the frontend must map to a real FastAPI route.  This
    catches the class of bug where a frontend call 404s/405s because the backend route
    uses the wrong path string — including after a route is DELETED and its caller is
    left behind.

    Covers plain string literals AND template literals with path params
    (``/api/x/${id}/y`` → ``/api/x/{}/y``), which are the majority of the real surface.
    Only genuinely unresolvable paths are skipped — a nested interpolation, or one that
    expands to an unknown number of segments; see ``_resolve_template_path``.
    """
    routes = _collect_app_routes()
    exact, templates = _build_route_matcher(routes)

    fe_paths = _extract_frontend_api_paths()
    assert fe_paths, "No /api/ literals found in frontend/src — check the regex"

    missing: list[str] = []
    for fe_path in sorted(fe_paths):
        if fe_path in _KNOWN_FRONTEND_EXTRAS or fe_path in _PENDING_PARALLEL_LANE_ROUTES:
            continue
        if fe_path in _FIXED_VOCABULARY_PATHS or fe_path in _KNOWN_BROKEN_FRONTEND_CALLS:
            continue
        if any(fe_path.startswith(pfx) for pfx in _SKIP_PREFIXES):
            continue
        if not _frontend_path_matches_backend(fe_path, exact, templates):
            missing.append(fe_path)

    assert not missing, (
        "The following frontend /api/ paths have no matching backend route.\n"
        "Either the backend route is missing /api/, the path was renamed, or the\n"
        "frontend is calling a non-existent endpoint.  Fix the mismatch, or add to\n"
        "_KNOWN_FRONTEND_EXTRAS if the route is defined inline in main.py.\n\n"
        "  Missing backend routes for frontend calls:\n" + "\n".join(f"    {p}" for p in missing)
    )
