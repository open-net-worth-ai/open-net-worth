"""McpClientConnection — JSON-RPC 2.0 over HTTP to ONE external MCP server (RFC P3-MCP-CLIENT §2, S1).

The OUTBOUND mirror of ``backend/mcp/server.py``: where the server is the trusted
resource and the caller untrusted, here WE are the caller and the external server is
untrusted code. This connection speaks the same minimal JSON-RPC method set
(``initialize`` / ``tools/list`` / ``tools/call``) but as the CLIENT, over HTTP
(``urllib`` — NO ``mcp`` SDK, zero new dependency; the request/response envelope
shaping mirrors ``mcp/stdio_bridge``).

Robustness contract (RFC §6): a transport failure, an HTTP error status, a malformed
body, or a JSON-RPC error envelope from the server NEVER raises into the chat loop —
each surfaces as a clean ``McpCallError`` the manager turns into a fenced tool-error
string for the model. Every call is bounded by a per-call timeout so a wedged server
can't hang a turn.

Auth: the configured token is sent ONLY as the server's own auth header
(``Authorization: Bearer`` / a custom header / none) — we send the model's tool-call
args and that header, and nothing else. We never forward any Open Net Worth secret
(RFC §5.4 secret-exfil invariant). That header is also never replayed onto a redirect: a
3xx from the server is refused outright, never auto-followed (see the module comment
above ``_urlopen``), because ``urllib``'s default redirect handling would otherwise carry
``Authorization`` verbatim to whatever host the server's own ``Location`` header names.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal, Optional, Tuple

from backend.core.net_guard import UnsafeUrlError, resolve_owner_url
from backend.core.pinned_http import pinned_opener
from backend.core.redact_error import redact_error
from backend.core.untrusted import sanitize_untrusted
from backend.mcp.sse import parse_rpc_body
from backend.models.mcp_client import McpServerConfig, ServerInfo, redact_endpoint

logger = logging.getLogger(__name__)

# Per-call HTTP timeout (seconds) — bounds a slow/wedged external server so it can
# never hang a chat turn (RFC §5.4 hang/flood clamp).
CALL_TIMEOUT_SECONDS = 20

# Overall budgets for the INTERACTIVE probe (the add flow's "Test connect"), which makes TWO
# calls — ``initialize`` then ``tools/list`` — and so has no useful bound from a per-call
# timeout alone. These are deliberately LOOSER than ``CALL_TIMEOUT_SECONDS`` because a human
# started this and is watching it: a cold ``npx -y <package>`` genuinely downloads a package
# tree on first run (30-60s is normal, and that IS the documented quick start for a large
# share of real MCP servers), so a 20s cap would make the commonest install path look broken.
# A remote endpoint that cannot finish a handshake in 30s was never going to be usable.
#
# They apply to the probe ONLY. Autonomous tool dispatch keeps the tight per-call 20s — a
# different risk profile, with nobody watching.
PROBE_TIMEOUT_HTTP_SECONDS = 30
# 60s was measured too short against a real server: `npx -y interactive-brokers-mcp` unpacks
# ~264 MB and its cold npm download alone took 61.2s, so the probe was killed BEFORE the
# server had started — reported as a timeout, which reads as "this server is broken" rather
# than "your package manager is still downloading". Warm-cache, the same probe finished in
# 3.6s and discovered all 14 tools, so the budget only ever binds on a first install. 300s is
# sized for a cold fetch of a large package on an ordinary connection; the process group is
# still killed at expiry, so a genuinely wedged server is bounded, just later.
PROBE_TIMEOUT_STDIO_SECONDS = 300

# Hard byte cap on an external server's HTTP response body, read at the TRANSPORT layer
# BEFORE the JSON parse + the 4000-char result fence (security-review B1): a hostile MCP
# server could otherwise stream a multi-GB body that an unbounded ``resp.read()`` buffers
# fully into memory → OOM the ONW host. 1 MiB is ample for any legitimate JSON-RPC envelope
# while making an unbounded read impossible; over-cap → a clean ``McpCallError``, never a
# crash. This is an EARLIER, transport-level cap that pairs with the later untrusted fence.
MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB

# A tighter cap on the body read for an HTTP-error message (we only surface ~200 chars of
# it), so even an oversized error body can't be buffered unboundedly.
MAX_ERROR_BODY_BYTES = 8192

# Cap on a ``serverInfo`` string we persist. The server chooses its own name/version, so a
# hostile one could answer with megabytes; a name is a label, not a payload.
MAX_SERVER_INFO_CHARS = 200

# The spec's ``instructions`` field is meant as a paragraph, not a label, so it gets a looser
# cap than name/version/publisher — but it is still bounded, and unlike those short identity
# fields it goes through ``sanitize_untrusted`` (see ``parse_server_info``): it is rendered as
# a connector's description text, and a hostile server's free-text is exactly where a forged
# fence/confusable-homoglyph attempt would hide.
MAX_SERVER_INSTRUCTIONS_CHARS = 600

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "open-net-worth-client", "version": "1"}

_REDIRECT_CODES = (301, 302, 303, 307, 308)


# ── redirect-safe transport (security review: bearer/OAuth token exfil via a 3xx) ──
#
# ``_headers()`` puts a live credential (a bearer/header token, or — the higher-value
# target — a freshly-minted OAuth access token) on EVERY request this connection makes.
# The stock ``urllib.request.urlopen`` auto-follows a 3xx via its default
# ``HTTPRedirectHandler``, and per the real stdlib source
# (``HTTPRedirectHandler.redirect_request``) the ONLY headers it strips on a redirect hop
# are ``Content-Length``/``Content-Type`` — ``Authorization`` survives verbatim to
# WHATEVER host the server's ``Location:`` header names, even a completely different one.
# A hostile or supply-chain-compromised MCP server can therefore harvest every live
# credential just by answering an ordinary ``tools/list``/``tools/call`` with a 302 —
# and this fires on fully autonomous dispatch, no human in the loop to notice.
#
# Unlike OAuth's own discovery (``backend/mcp/oauth.py``), which has to fetch
# SERVER-CHOSEN metadata URLs and so re-validates each redirect hop against a trust-class
# rule, an MCP server's own URL here is a KNOWN, already-configured value with no
# legitimate reason to ever 3xx a JSON-RPC call — so the simplest safe posture is to
# refuse EVERY redirect outright rather than attempt to safely follow one.


def _urlopen(request: urllib.request.Request, timeout: float):
    """The one call site that actually puts a request on the wire for this transport.

    The configured URL is resolved and validated (``net_guard.resolve_owner_url``: a local or
    LAN MCP server is fine on a self-hosted box, never on a hosted one, and a link-local /
    cloud-metadata target never), then the connection is PINNED to the validated IP so a
    rebinding DNS answer cannot redirect it. The pinned opener also has automatic
    redirect-following stripped (see the section comment above): a 3xx raises
    ``urllib.error.HTTPError`` so ``_send`` can turn it into a clear refusal rather than
    leaking the ``Authorization`` header to an unvalidated host."""
    _, pinned_ip = resolve_owner_url(request.full_url)
    return pinned_opener(pinned_ip).open(request, timeout=timeout)  # noqa: S310 — validated + pinned


class Deadline:
    """One absolute budget shared across several calls.

    A probe's cost is ``initialize`` + ``tools/list``; bounding each independently bounds
    nothing overall (two 20s calls are a 40s wait). Every call under a deadline gets only the
    REMAINING budget, so the whole operation ends when the clock says so no matter how the
    time was spent. Monotonic, so a wall-clock adjustment can't extend or collapse it.
    """

    def __init__(self, budget_seconds: float):
        self._end = time.monotonic() + max(0.0, budget_seconds)

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0


class McpCallError(Exception):
    """A transport / protocol / server-side error reaching an external MCP server.

    Carries a human-readable message the manager fences into a tool-error string for
    the model — it is NEVER allowed to propagate into the chat loop as a 500.

    ``safe_summary`` is a SEPARATE, narrower rendering for the two channels a raw message
    must never reach verbatim: a persisted field (``last_error``) and the app log. It
    defaults to ``str(self)`` — most raise sites carry nothing a child process chose — but
    the stdio transport overrides it wherever the message quotes a spawned child's stderr
    (``stdio_client._StdioSession._explain``), because that quote is now captured rather
    than discarded and can contain anything the child printed, credential-shaped content
    included. The full message (``str(exc)``) stays available for the ONE channel built to
    carry it: the synchronous probe's own ephemeral HTTP response.
    """

    def __init__(
        self,
        message: str,
        *,
        safe_summary: Optional[str] = None,
        auth_challenge: Optional[str] = None,
    ):
        super().__init__(message)
        self._safe_summary = safe_summary
        # What a 401 asked for (``auth_challenge_of``): ``oauth`` / ``key`` / ``None``. Lets the
        # add flow say "this server needs you to sign in" instead of a bare HTTP 401.
        self.auth_challenge = auth_challenge

    @property
    def safe_summary(self) -> str:
        return self._safe_summary if self._safe_summary is not None else str(self)


class _OAuthUnauthorized(McpCallError):
    """Internal signal: an ``oauth``-authed request got a 401. Caught exactly once by
    ``McpClientConnection._rpc``, which refreshes and retries — never surfaced past that (a
    caller sees a plain ``McpCallError``, since this is a subclass, if the retry also fails)."""


class McpClientConnection:
    """An HTTP JSON-RPC client for one external MCP server, over the Streamable HTTP transport.

    Each method is one POST request/response, but a SERVER-ISSUED ``Mcp-Session-Id`` (returned
    as a response header on ``initialize``, per the MCP Streamable HTTP transport spec) is
    captured on THIS instance and echoed on every later call made through the SAME connection —
    so a session lives exactly as long as the ``with make_connection(...) as conn:`` block that
    holds it, never longer. Once ``initialize`` negotiates a protocol version, it is likewise
    echoed as ``MCP-Protocol-Version`` on every subsequent request (never on ``initialize``
    itself, which negotiates it via the JSON body). ``initialize`` establishes protocol parity
    AND reports the server's ``serverInfo`` (see ``parse_server_info``); ``list_tools`` /
    ``call_tool`` are the load-bearing methods.
    """

    def __init__(self, config: McpServerConfig, *, deadline: Optional[Deadline] = None):
        self._config = config
        self._next_id = 0
        # ``None`` = the standing per-call timeout (autonomous dispatch). A ``Deadline``
        # bounds the WHOLE operation across calls (the interactive probe).
        self._deadline = deadline
        # Set from ``initialize``'s response and echoed thereafter — see the class docstring.
        self._session_id: Optional[str] = None
        self._protocol_version: Optional[str] = None

    # ── context manager (symmetry with the stdio client, which has a child to reap) ──

    def __enter__(self) -> "McpClientConnection":
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        # Nothing to release: each ``_rpc`` is one self-contained urllib request/response.
        # Present so ``with make_connection(cfg) as conn`` is transport-agnostic — the stdio
        # sibling DOES have a spawned process group to kill on the way out.
        #
        # ``Literal[False]``, not ``bool``: a ``bool`` return says the manager MAY swallow an
        # exception, and this one never does — mypy's own ``exit-return`` check says so.
        return False

    # ── public API ───────────────────────────────────────────────────────────────

    def initialize(self) -> dict:
        """MCP ``initialize`` handshake. Returns the server's result block.

        The result carries ``serverInfo: {name, version}`` — what the server calls ITSELF.
        Callers read it through ``parse_server_info`` rather than indexing it raw.
        """
        return self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )

    def list_tools(self) -> list[dict]:
        """MCP ``tools/list`` → the server's tool descriptors (``[{name, description, inputSchema}]``).

        Malformed entries (missing a string ``name``) are dropped rather than crashing —
        a lying/buggy server can't poison the listing (RFC §5.4 schema-drift clamp).
        """
        result = self._rpc("tools/list", {})
        raw = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            return []
        tools: list[dict] = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not isinstance(name, str) or not name:
                continue
            entry = {
                "name": name,
                "description": t.get("description") or "",
                "inputSchema": t.get("inputSchema")
                if isinstance(t.get("inputSchema"), dict)
                else {"type": "object", "properties": {}},
            }
            # Carry ``annotations`` through (matching the stdio client): ``readOnlyHint``
            # inside it is what files a tool under the ladder's read vs. write heading. Was
            # dropped here, so annotation-based grouping silently worked for stdio only.
            ann = t.get("annotations")
            if isinstance(ann, dict):
                entry["annotations"] = ann
            tools.append(entry)
        return tools

    def call_tool(self, tool: str, arguments: dict) -> Tuple[str, list[dict]]:
        """MCP ``tools/call`` → ``(text, images)`` for the model/caller.

        MCP returns ``{content: [{type, text|...}], isError}``. Text blocks collapse into
        one string; an ``image`` block (``{type: "image", data: <base64>, mimeType: ...}``)
        is extracted separately into ``images`` instead — see ``split_result_content`` for
        why (a raw base64 screenshot JSON-dumped into "text" is neither usable text nor a
        displayable image). An ``isError`` result is raised as ``McpCallError`` so the
        manager fences it as a tool error. ``arguments`` is the model's tool-call input —
        the ONLY thing (besides the server's own auth header) we send.
        """
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        text, images = split_result_content(
            result.get("content") if isinstance(result, dict) else None
        )
        if isinstance(result, dict) and result.get("isError"):
            raise McpCallError(text or "external tool reported an error")
        return text, images

    # ── transport ────────────────────────────────────────────────────────────────

    def _rpc(self, method: str, params: dict) -> dict:
        """Send one JSON-RPC request; return the ``result`` block or raise ``McpCallError``.

        A 401 on an ``oauth``-authed connection is ONE-SHOT (RFC ``2026-08-14-mcp-oauth``
        §4.7 "Reactive"): a proactive refresh already ran before this connection was opened
        (``client_manager``'s ``ensure_fresh_token``), so a 401 here means the token was
        revoked/expired early — refresh once, retry once, then give up. Never a loop: the
        retry's own ``_send`` is not wrapped in another except, so a second 401 propagates as
        an ordinary ``McpCallError``.
        """
        try:
            return self._send(method, params)
        except _OAuthUnauthorized:
            self._refresh_oauth_token()
            return self._send(method, params)

    def _refresh_oauth_token(self) -> None:
        """Force a refresh of THIS connection's oauth config after a live 401 (see ``_rpc``).
        Local import: ``oauth_flow`` sits above this module (it composes a store + a locked
        transaction), so importing it at module level here would be the wrong direction for a
        leaf transport class to depend on for its common (non-oauth) path."""
        from backend.mcp import oauth_flow

        stale = self._config.oauth.access_token if self._config.oauth else None
        self._config = oauth_flow.ensure_fresh_token(
            self._config, force=True, stale_access_token=stale
        )

    def _send(self, method: str, params: dict) -> dict:
        """One JSON-RPC request/response cycle — the body ``_rpc`` retries at most once."""
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        body = json.dumps(payload).encode("utf-8")

        url = (self._config.url or "").strip()
        if not url:
            raise McpCallError(f"server '{self._config.id}' has no url configured")
        # SECURITY: ``urlopen`` speaks more than http. A stored ``file:///etc/passwd`` would
        # be read off the local disk and its result handed back as if a server had answered
        # (and a miss echoes the resolved path — a filesystem-existence oracle). The input
        # models reject a non-http(s) scheme at the boundary; this is the belt for a row that
        # predates them, and it is where enforcement has to live because a read of the store
        # must never raise. Loopback/LAN addresses are allowed on a self-hosted install —
        # ``http://127.0.0.1:3000/mcp`` is a first-class, documented way to run a local MCP
        # server — and refused in gateway mode; ``_urlopen`` enforces that.
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise McpCallError(
                f"server '{self._config.id}' url must be http:// or https:// "
                f"(got {scheme + ':' if scheme else 'no scheme'})"
            )

        timeout = CALL_TIMEOUT_SECONDS if self._deadline is None else self._deadline.remaining()
        if timeout <= 0:
            raise McpCallError(f"server '{self._config.id}' timed out before it finished replying")

        request = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        try:
            with _urlopen(request, timeout) as resp:
                # Read at most MAX_RESPONSE_BYTES + 1: if the extra byte materialises the
                # body exceeded the cap, so we abort WITHOUT buffering the whole stream — a
                # hostile multi-GB body can't OOM us (security-review B1). This bound applies
                # identically whether the body is a plain JSON envelope or SSE-framed: ``.read``
                # is capped BEFORE either shape is parsed below, so SSE framing (which drops the
                # ``Content-Length`` a plain JSON reply carries) cannot defeat it.
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise McpCallError(
                        f"server '{self._config.id}' response exceeds the "
                        f"{MAX_RESPONSE_BYTES}-byte limit"
                    )
                content_type = resp.headers.get("Content-Type") or ""
                session_id = resp.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                # SECURITY: never follow this — see the module comment above ``_urlopen``.
                # A legitimate MCP JSON-RPC endpoint has no business 3xx-redirecting a
                # tools/call POST, so refusing outright is not a functionality regression.
                exc.close()
                raise McpCallError(
                    f"server '{self._config.id}' attempted to redirect this request "
                    f"(HTTP {exc.code}) — refusing to follow a redirect on a tool-dispatch call"
                ) from exc
            # A non-2xx status — read whatever body it carries for the message, but
            # do not trust it as a valid result.
            detail = _error_detail(_safe_read(exc))
            if exc.code == 401 and self._config.auth_type == "oauth":
                raise _OAuthUnauthorized(
                    f"server '{self._config.id}' returned HTTP 401: {detail}"
                ) from exc
            raise McpCallError(
                f"server '{self._config.id}' returned HTTP {exc.code}: {detail}",
                auth_challenge=(
                    auth_challenge_of(exc.headers.get("WWW-Authenticate") if exc.headers else None)
                    if exc.code == 401
                    else None
                ),
            ) from exc
        except UnsafeUrlError as exc:
            raise McpCallError(f"server '{self._config.id}' url is not allowed: {exc}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = redact_error(str(getattr(exc, "reason", exc)))
            raise McpCallError(
                f"cannot reach mcp server '{self._config.id}' at "
                f"{redact_endpoint('http', url)}: {reason}"
            ) from exc

        if session_id:
            # The server MAY assign a session id on ``initialize`` (MCP Streamable HTTP
            # transport spec); once it does, every later call on THIS connection echoes it.
            self._session_id = session_id

        try:
            parsed = parse_rpc_body(raw, content_type, request_id)
        except (ValueError, TypeError) as exc:
            raise McpCallError(f"server '{self._config.id}' returned a non-JSON response") from exc

        if not isinstance(parsed, dict):
            raise McpCallError(f"server '{self._config.id}' returned a non-object response")
        if "error" in parsed and parsed["error"]:
            err = parsed["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise McpCallError(f"server '{self._config.id}' error: {msg}")

        result = parsed.get("result")
        if method == "initialize":
            # The version we echo on every later call in this session is what the SERVER
            # negotiated, not just what we asked for (MCP spec: the client MUST send
            # ``MCP-Protocol-Version`` on subsequent requests). Falls back to our own request
            # if the result omits it, rather than leaving the session unversioned.
            negotiated = result.get("protocolVersion") if isinstance(result, dict) else None
            self._protocol_version = negotiated or PROTOCOL_VERSION
        return result if isinstance(result, dict) else {}

    def _headers(self) -> dict:
        """Request headers — JSON content + the server's own auth header.

        ``Accept`` offers BOTH ``application/json`` and ``text/event-stream``: a Streamable
        HTTP server is free to answer either way, and a client that offers only one of the two
        gets a clean ``406`` from every server that requires both (verified live 2026-08-14
        against ``mcp.deepwiki.com`` / ``mcp.context7.com`` — RFC §2.5/§5.1).

        We send the configured credential to the server it belongs to and nothing else; no
        Open Net Worth cookie / API token / data is ever forwarded (RFC §5.4).
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if self._config.auth_type == "oauth":
            access_token = (self._config.oauth.access_token if self._config.oauth else "") or ""
            access_token = access_token.strip()
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            return headers
        token = (self._config.token or "").strip()
        if not token:
            return headers
        if self._config.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif self._config.auth_type == "header":
            headers[self._config.auth_header or "Authorization"] = token
        return headers


def auth_challenge_of(header: Optional[str]) -> Optional[str]:
    """What a 401's ``WWW-Authenticate`` asks for: ``"oauth"`` when it points at OAuth
    protected-resource metadata (MCP's sign-in discovery, RFC 9728's ``resource_metadata``),
    ``"key"`` for any other Bearer challenge (a token you paste), else ``None``. Only a hint for
    the add flow's copy — never a decision about what to send."""
    value = (header or "").lower()
    if "resource_metadata" in value:
        return "oauth"
    if "bearer" in value:
        return "key"
    return None


def parse_server_info(result: Any) -> ServerInfo:
    """Extract the ``serverInfo`` block — plus the top-level ``instructions`` field — from an
    ``initialize`` result.

    Per the MCP spec the handshake answers with ``serverInfo: {name, version}`` (newer
    revisions add ``title``/``websiteUrl``/``icons``); the add flow pre-fills its Name field from that
    name (§7 step 2) instead of guessing from a URL host. ``publisher``/``repositoryUrl`` are
    NOT in the spec — some servers volunteer them, so we keep them when present and leave
    them ``None`` when not, rather than fabricating provenance. ``instructions`` is a
    top-level sibling of ``serverInfo`` in the result (not nested inside it) — the spec's own
    slot for a server's free-text self-description, which ``services.connectors`` uses as a
    custom server's detail/directory description in place of the generic fallback.

    Defensive throughout, because this is UNTRUSTED third-party input and discovery must
    never crash on it: an absent block, a non-dict block, a missing/blank/whitespace-only
    value, or a non-string (a number, a nested object, ``None``) each yield ``None`` for that
    field rather than raising or storing junk. Strings are trimmed and length-capped so a
    server can't smuggle a megabyte of prose into our store through its own name.
    Transport-agnostic — both the HTTP and stdio clients route through it.
    """
    if not isinstance(result, dict):
        return ServerInfo()
    info = result.get("serverInfo")
    info = info if isinstance(info, dict) else {}
    return ServerInfo(
        name=_clean_info_field(info.get("name")),
        version=_clean_info_field(info.get("version")),
        title=_clean_info_field(info.get("title")),
        icons=_clean_info_icons(info.get("icons")),
        publisher=_clean_info_field(info.get("publisher") or info.get("vendor")),
        homepage_url=_clean_info_url(info.get("websiteUrl") or info.get("homepageUrl")),
        repo_url=_clean_info_url(info.get("repositoryUrl") or info.get("repository")),
        instructions=_clean_instructions(result.get("instructions")),
    )


def _clean_info_field(value: Any) -> Optional[str]:
    """A ``serverInfo`` field as a trimmed, bounded string — or ``None`` if unusable."""
    if not isinstance(value, str):
        return None  # a number / dict / None is not a name; do not coerce it into one
    cleaned = value.strip()
    return cleaned[:MAX_SERVER_INFO_CHARS] if cleaned else None


def _clean_info_icons(value: Any) -> tuple[str, ...]:
    """``serverInfo.icons`` as at most four ``https:``/``data:image`` sources. Only the URI is
    kept: the bytes are fetched, size-capped and typed by magic later (``mcp.site_identity``),
    so a declared ``mimeType`` (an SVG, say) is never trusted."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for entry in value:
        src = entry.get("src") if isinstance(entry, dict) else None
        if not isinstance(src, str):
            continue
        src = src.strip()
        if src.lower().startswith("https://") and len(src) <= 2048:
            out.append(src)
        elif src.lower().startswith("data:image/") and len(src) <= 256 * 1024:
            out.append(src)
    return tuple(out[:4])


def _clean_instructions(value: Any) -> Optional[str]:
    """The handshake's ``instructions`` field, sanitized + capped — or ``None`` if unusable.

    Unlike ``_clean_info_field`` (a short label), this is free-text prose rendered as a
    connector's description, so it goes through ``sanitize_untrusted`` (NFKC-normalize +
    strip the confusable/fence-forging range — see ``core.untrusted``) rather than a bare
    trim, matching every other server-chosen string this codebase shows a person or a model
    (tool descriptions, ``last_error``, a stdio child's stderr tail).
    """
    if not isinstance(value, str):
        return None
    cleaned = sanitize_untrusted(value, max_len=MAX_SERVER_INSTRUCTIONS_CHARS).strip()
    return cleaned or None


def _clean_info_url(value: Any) -> Optional[str]:
    """A ``serverInfo`` URL, or ``None`` unless it is plainly ``http(s)``.

    SECURITY: these land in the vendor-trust panel, which renders them as links. An
    untrusted server choosing its own ``websiteUrl`` could otherwise hand us
    ``javascript:…`` / ``data:…`` and turn a provenance display into script execution on
    click. Scheme-allowlisted here, at the transport boundary, so nothing downstream has to
    remember to — and a rejected URL becomes "not known", which is the honest reading of a
    URL we refuse to show.
    """
    cleaned = _clean_info_field(value)
    if cleaned is None:
        return None
    scheme = urllib.parse.urlsplit(cleaned).scheme.lower()
    return cleaned if scheme in ("http", "https") else None


def _is_image_block(block: dict) -> bool:
    return (
        block.get("type") == "image"
        and isinstance(block.get("data"), str)
        and isinstance(block.get("mimeType"), str)
    )


def split_result_content(content: Any) -> Tuple[str, list[dict]]:
    """Split an MCP ``tools/call`` result's ``content`` into ``(text, images)``.

    A well-formed ``{"type": "image", "data": <base64>, "mimeType": "image/..."}`` block is
    extracted into ``images`` (each ``{"data": <raw bytes>, "mime_type": str}``) instead of
    being JSON-dumped into the text, which is what this function used to do (as
    ``_render_content``) — the real bug behind a captured screenshot never reaching the user:
    a multi-MB base64 blob smashed into "text" was never a usable image regardless of whether
    it survived. Size is already bounded upstream, at the transport (``MAX_RESPONSE_BYTES`` /
    ``MAX_LINE_BYTES``) — no separate cap here. A block that LOOKS like an image but its data
    doesn't actually decode is reported as a short text note rather than silently dropped.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return ("" if content is None else json.dumps(content, ensure_ascii=False)), []
    parts: list[str] = []
    images: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        if _is_image_block(block):
            mime = block["mimeType"].strip().lower()
            if not mime.startswith("image/"):
                parts.append(f"[image omitted: not an image type ({mime or 'unknown'})]")
                continue
            try:
                raw = base64.b64decode(block["data"], validate=True)
            except (binascii.Error, ValueError):
                parts.append("[image omitted: invalid image data]")
                continue
            if not raw:
                parts.append("[image omitted: empty image data]")
                continue
            images.append({"data": raw, "mime_type": mime})
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts), images


def _error_detail(body: str) -> str:
    """A server-chosen error body, short and defanged, for a message a model may read. The
    endpoint is a credential, so a body that echoes it (or a token) is masked too."""
    return redact_error(sanitize_untrusted(body, max_len=200))


def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        # Bounded read — an oversized error body can't be buffered unboundedly either.
        return exc.read(MAX_ERROR_BODY_BYTES).decode("utf-8", errors="replace")
    except Exception:
        return ""


def make_connection(config: McpServerConfig, *, deadline: Optional[Deadline] = None):
    """Build the right transport client for a server config (http → here, stdio → spawn).

    Both clients expose the SAME ``initialize`` / ``list_tools`` / ``call_tool`` surface AND
    the same context-manager protocol, so the manager is transport-agnostic — this factory is
    the only place that branches on ``transport``. The stdio import is local to avoid a
    module-level import cycle (``stdio_client`` imports the shared helpers from this module).

    Use it as ``with make_connection(cfg) as conn:`` whenever the caller makes more than one
    call: for stdio that keeps ONE child process for the whole block and guarantees its
    process group is killed on every exit path, including an exception or a timeout.
    """
    if (getattr(config, "transport", "http") or "http") == "stdio":
        from backend.mcp.stdio_client import StdioMcpClientConnection

        return StdioMcpClientConnection(config, deadline=deadline)
    return McpClientConnection(config, deadline=deadline)


def probe_deadline(transport: str) -> Deadline:
    """The overall budget for one interactive probe of ``transport``."""
    budget = PROBE_TIMEOUT_STDIO_SECONDS if transport == "stdio" else PROBE_TIMEOUT_HTTP_SECONDS
    return Deadline(budget)
