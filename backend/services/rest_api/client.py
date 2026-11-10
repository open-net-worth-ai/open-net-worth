"""Send ONE request to a configured REST API.

The caller (Lucius, through ``ai.tools.rest_api``) chooses the method, the path under the
API's base URL, the query and the body. Everything else is fixed by what the owner saved:

  * **the host** — the request URL is the saved base URL plus the path, and is re-checked
    after joining; a redirect to any other origin is NOT followed. Whatever Lucius reads from
    the book can therefore only ever be sent to the API the owner configured;
  * **the credential and fixed headers** — added here; the caller cannot set a header;
  * **where it may connect** — ``net_guard.resolve_owner_url`` (a LAN / loopback API is fine on
    a self-hosted install, never in gateway mode; link-local metadata never) with the
    connection pinned to the validated address (``pinned_http.pinned_opener``).

The response comes back WHOLE or not at all: the request, not this module, bounds its size
(``?limit=``, a page, a date range), and ``MAX_RESPONSE_BYTES`` is a fuse for an API that ignores
that — over it the call fails with the size, it never returns part of a body.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from backend.core.errors import ExternalServiceError, OpenNetWorthError, ValidationError
from backend.core.net_guard import UnsafeUrlError, resolve_owner_url
from backend.core.pinned_http import pinned_opener
from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 30
BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_REDIRECTS = (301, 302, 303, 307, 308)
# RFC 3986 path characters that stay as written; everything else is percent-encoded.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"


def _origin(url: str) -> tuple[str, str, Optional[int]]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or (443 if scheme == "https" else 80)


def build_url(base_url: str, path: str, query: Optional[dict]) -> str:
    """``base_url`` + ``path`` (+ ``query``), refused unless it stays under the base.

    ``path`` is relative to the base and starts with ``/``. A query string written into it
    (``/txns?page=2``) is honoured and merged with ``query``."""
    raw = str(path or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        raise ValidationError("path must start with a single '/' and be relative to the API.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise ValidationError("path cannot contain control characters.")
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        raise ValidationError("path must be relative to the API — it cannot name a host.")
    if _has_dot_segment(parts.path):
        raise ValidationError("path cannot contain '.' or '..' segments.")
    # Encode what a URL cannot carry raw (a space, non-ASCII). Left raw, http.client refuses
    # the request with an error that QUOTES the whole URL — base path and query key included.
    parts = parts._replace(path=quote(parts.path, safe=_PATH_SAFE))
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    for key, value in (query or {}).items():
        values = value if isinstance(value, (list, tuple)) else [value]
        pairs.extend((str(key), _scalar(v)) for v in values if v is not None)
    base = urlsplit(base_url)
    url = urlunsplit(
        (base.scheme, base.netloc, base.path.rstrip("/") + parts.path, urlencode(pairs), "")
    )
    if not _under_base(url, base_url):
        raise ValidationError("path leaves the API's base URL.")
    return url


def _has_dot_segment(path: str) -> bool:
    """True for a ``.`` / ``..`` segment however it is dressed: percent-encoded any number of
    times (``%252e%252e``), or carrying matrix parameters a servlet container strips before it
    resolves the path (``..;x``)."""
    decoded = path
    for _ in range(4):
        if any(seg.split(";")[0] in (".", "..") for seg in decoded.split("/")):
            return True
        again = unquote(decoded)
        if again == decoded:
            return False
        decoded = again
    return True  # still changing after four passes: nobody encodes a real path that deep


def _under_base(url: str, base_url: str) -> bool:
    base = urlsplit(base_url)
    prefix = base.path.rstrip("/") + "/"
    return _origin(url) == _origin(base_url) and (urlsplit(url).path + "/").startswith(prefix)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _with_auth(url: str, api: dict) -> tuple[str, dict]:
    """The request URL and headers with the owner's fixed headers and credential applied."""
    headers = {"Accept": "application/json", "User-Agent": "open-net-worth"}
    headers.update(api.get("headers") or {})
    auth = api.get("auth") or {}
    kind, value = auth.get("type") or "none", str(auth.get("value") or "")
    if kind == "bearer":
        headers["Authorization"] = f"Bearer {value}"
    elif kind == "basic":
        token = base64.b64encode(f"{auth.get('username', '')}:{value}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    elif kind == "header":
        headers[str(auth.get("name"))] = value
    elif kind == "query":
        parts = urlsplit(url)
        # The key's own parameter is the app's alone. A caller that supplied one too (in the
        # path's query string or the query object) would otherwise ride in FRONT of the real
        # key, and a server that reads the first value would run the request as someone else.
        key = str(auth.get("name"))
        pairs = [
            pair
            for pair in parse_qsl(parts.query, keep_blank_values=True)
            if pair[0].lower() != key.lower()
        ]
        pairs.append((key, value))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))
    return url, headers


def _encode_body(body: Any, headers: dict) -> Optional[bytes]:
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(body).encode("utf-8")
    headers.setdefault("Content-Type", "text/plain; charset=utf-8")
    return str(body).encode("utf-8")


def _open(url: str, method: str, headers: dict, data: Optional[bytes], base_url: str):
    """Open ``url`` pinned to its validated address, following a redirect only while it stays
    under ``base_url`` (same origin, same path prefix) — the credential rides along, so a
    redirect may not carry it anywhere a request could not have gone. Returns the final
    response object (an ``HTTPError`` IS one for a 4xx/5xx)."""
    for _ in range(MAX_REDIRECTS + 1):
        clean, pinned_ip = resolve_owner_url(url)
        req = urllib.request.Request(clean, data=data, headers=headers, method=method)
        try:
            return pinned_opener(pinned_ip).open(req, timeout=TIMEOUT_SECONDS)  # noqa: S310
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code not in _REDIRECTS or not location:
                return exc
            url = urljoin(clean, location)
            if not _under_base(url, base_url):
                raise ExternalServiceError(
                    "The API redirected to a different host or outside its base URL, "
                    "which is not followed."
                )
            if exc.code == 303 or (exc.code in (301, 302) and method != "GET"):
                method, data = "GET", None
                headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    raise ExternalServiceError("The API redirected too many times.")


def _clean(value: Any) -> Any:
    """Defang every string of a parsed body (it is text an outside server chose) without
    shortening any of it."""
    if isinstance(value, str):
        return sanitize_untrusted(value, max_len=len(value))
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {_clean(k) if isinstance(k, str) else k: _clean(v) for k, v in value.items()}
    return value


def _read_body(response) -> Any:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        declared = response.headers.get("Content-Length") if response.headers else None
        size = f"{int(declared):,} bytes" if (declared or "").isdigit() else "more than 1 MB"
        raise ExternalServiceError(
            f"The response is {size}, over the {MAX_RESPONSE_BYTES:,}-byte limit for one call. "
            "Nothing was returned. Ask the API for less: a smaller page or limit, or a "
            "narrower date range."
        )
    text = raw.decode("utf-8", errors="replace")
    try:
        return _clean(json.loads(text))
    except ValueError:
        return _clean(text)


def send(api: dict, method: str, path: str, query: Optional[dict] = None, body: Any = None) -> dict:
    """One request to ``api`` (a STORED entry — credentials included). Returns
    ``{"status", "body"}`` for any HTTP answer, 4xx/5xx included: the API's own error text is how
    the caller corrects a request. Raises ``ValidationError`` for a request this connector will
    not send and ``ExternalServiceError`` when the API could not be reached."""
    method = str(method or "GET").strip().upper()
    if method != "GET" and method not in BODY_METHODS:
        raise ValidationError(f"Unsupported method '{method}'.")
    base_url = api.get("base_url") or ""
    url, headers = _with_auth(build_url(base_url, path, query), api)
    data = _encode_body(body, headers) if method != "GET" else None
    try:
        with _open(url, method, headers, data, base_url) as response:
            return {"status": response.status, "body": _read_body(response)}
    except OpenNetWorthError:
        raise
    except UnsafeUrlError as exc:
        raise ExternalServiceError(f"This API's address cannot be reached: {exc}")
    except Exception as exc:  # noqa: BLE001 — see below
        # EVERY other failure, and the class name only. urllib and http.client put the request
        # in their messages (``InvalidURL`` quotes the URL, ``Invalid header value`` the
        # header), which is the base path and the key; an exception that escaped here would
        # reach the model as its text and the log with its traceback.
        logger.warning("rest api %s: request failed (%s)", api.get("id"), type(exc).__name__)
        raise ExternalServiceError(f"Could not reach the API ({type(exc).__name__}).") from None
