"""Turn an OpenAPI 3 / Swagger 2 document into a DRAFT API for the REST connector.

Optional at setup: the owner may type every field by hand instead. Nothing here saves — the
draft pre-fills the form, the owner reviews it and saves through ``apis.save_api``. A document
names where the API lives (``servers`` / ``host``), how it authenticates
(``securitySchemes`` / ``securityDefinitions``) and what can be called (``paths``); the
credential itself is never in a document and is always typed by the owner.

Every string taken from the document is text an outside party wrote — it goes through
``apis.clean_endpoints`` (sanitised, length-capped) before it can reach a model.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Optional
from urllib.parse import urljoin

import yaml

from backend.core.errors import ValidationError
from backend.core.net_guard import UnsafeUrlError
from backend.core.pinned_http import PinnedFetchError, pinned_urlopen
from backend.core.untrusted import sanitize_untrusted
from backend.services.rest_api.apis import MAX_ENDPOINTS, MAX_PARAMS, clean_endpoints

MAX_SPEC_BYTES = 10_000_000
_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_BODY_FIELDS = 40


def fetch_spec(url: str) -> str:
    """Download a document from a URL the owner typed (so a LAN address is allowed on a
    self-hosted install — ``pinned_urlopen(owner_configured=True)``)."""
    try:
        with pinned_urlopen(
            url, headers={"Accept": "application/json, */*"}, timeout=20, owner_configured=True
        ) as response:
            raw = response.read(MAX_SPEC_BYTES + 1)
    except UnsafeUrlError as exc:
        raise ValidationError(f"That address cannot be fetched: {exc}")
    # A ValidationError (422), not an ExternalServiceError (502), on purpose: the address is
    # something the owner typed and can correct, and a 502 does not survive a reverse proxy.
    # Behind a Cloudflare tunnel the proxy swaps its own error page in for ours, so the form
    # could only say "Could not import that document" with the reason gone (owner, 2026-11-11).
    except urllib.error.HTTPError as exc:
        raise ValidationError(
            f"That address answered {exc.code}, not a document. Check it is the OpenAPI "
            "document's own address, or paste the document instead."
        )
    except (urllib.error.URLError, PinnedFetchError, OSError) as exc:
        raise ValidationError(
            f"Could not reach that address ({type(exc).__name__}). Check it, or paste the "
            "document instead."
        )
    if len(raw) > MAX_SPEC_BYTES:
        raise ValidationError("That document is over 10 MB. Paste the part you need instead.")
    return raw.decode("utf-8", errors="replace")


def _load(text: str) -> dict:
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        raise ValidationError(
            "That is not an OpenAPI or Swagger document (it has no 'paths'). "
            "You can still add endpoints by hand."
        )
    return doc


def _resolve(doc: dict, node: Any) -> Any:
    """Follow one local ``$ref`` (``#/components/...``). Anything else is returned as is."""
    for _ in range(8):
        ref = node.get("$ref") if isinstance(node, dict) else None
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return node
        target: Any = doc
        for part in ref[2:].split("/"):
            target = (
                target.get(part.replace("~1", "/").replace("~0", "~"))
                if isinstance(target, dict)
                else None
            )
        node = target
    return node


def _base_url(doc: dict, source_url: Optional[str]) -> str:
    servers = doc.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url") or "").strip()
        for name, var in (servers[0].get("variables") or {}).items():
            if isinstance(var, dict):
                url = url.replace("{" + str(name) + "}", str(var.get("default") or ""))
        if url:
            return urljoin(source_url, url) if source_url else url
    host = str(doc.get("host") or "").strip()
    if host:
        schemes = doc.get("schemes") or ["https"]
        scheme = "https" if "https" in schemes else str(schemes[0])
        return f"{scheme}://{host}{str(doc.get('basePath') or '').rstrip('/')}"
    return ""


def _auth(doc: dict) -> tuple[dict, str]:
    """``({type, name}, note)`` from the first scheme this connector can send. ``note`` says so
    when the document only offers one it cannot (OAuth sign-in)."""
    schemes = (doc.get("components") or {}).get("securitySchemes") or doc.get("securityDefinitions")
    unsupported = ""
    for scheme in (schemes or {}).values() if isinstance(schemes, dict) else ():
        scheme = _resolve(doc, scheme)
        if not isinstance(scheme, dict):
            continue
        kind = str(scheme.get("type") or "").lower()
        http = str(scheme.get("scheme") or "").lower()
        if kind == "http" and http == "bearer":
            return {"type": "bearer", "name": ""}, ""
        if kind == "basic" or (kind == "http" and http == "basic"):
            return {"type": "basic", "name": ""}, ""
        if kind == "apikey" and str(scheme.get("in") or "").lower() in ("header", "query"):
            return {"type": str(scheme["in"]).lower(), "name": str(scheme.get("name") or "")}, ""
        unsupported = kind or unsupported
    if unsupported:
        return {"type": "none", "name": ""}, (
            f"This API signs in with {unsupported}, which the connector cannot do on its own. "
            "If you can create a personal access token for it, choose Bearer token."
        )
    return {"type": "none", "name": ""}, ""


def _body_params(doc: dict, operation: dict) -> list[dict]:
    """The top-level fields of a JSON request body, as ``in: body`` parameters."""
    body = _resolve(doc, operation.get("requestBody"))
    content = body.get("content") if isinstance(body, dict) else None
    schema: Any = None
    if isinstance(content, dict) and content:
        media = content.get("application/json") or next(iter(content.values()))
        schema = media.get("schema") if isinstance(media, dict) else None
    schema = _resolve(doc, schema)
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return []
    required = set(schema.get("required") or [])
    out = []
    for name, prop in list(schema["properties"].items())[:_MAX_BODY_FIELDS]:
        prop = _resolve(doc, prop)
        desc = prop.get("description") if isinstance(prop, dict) else ""
        out.append(
            {"name": name, "in": "body", "required": name in required, "description": desc or ""}
        )
    return out


def _params(doc: dict, shared: Any, operation: dict) -> list[dict]:
    out: list[dict] = []
    own = operation.get("parameters")
    declared = [
        *(shared if isinstance(shared, list) else []),
        *(own if isinstance(own, list) else []),
    ]
    for raw in declared[:MAX_PARAMS]:
        param = _resolve(doc, raw)
        if not isinstance(param, dict) or not param.get("name"):
            continue
        place = str(param.get("in") or "query").lower()
        if place in ("header", "cookie"):
            continue  # the connector sets headers itself; a caller cannot
        if place == "body":  # Swagger 2's whole-body parameter
            schema = _resolve(doc, param.get("schema"))
            out.extend(_body_params(doc, {"requestBody": {"content": {"_": {"schema": schema}}}}))
            continue
        out.append(
            {
                "name": param["name"],
                "in": "path" if place == "path" else "query",
                "required": bool(param.get("required")),
                "description": param.get("description") or "",
            }
        )
    return [*out, *_body_params(doc, operation)][:MAX_PARAMS]


def _endpoints(doc: dict) -> list[dict]:
    """Up to ``MAX_ENDPOINTS + 1`` endpoints — the extra one is how the caller knows the
    document had more. The caps are what bound the work a hostile document can ask for (one
    shared path item referenced from every path multiplies otherwise)."""
    endpoints: list[dict] = []
    for path, item in doc["paths"].items():
        if len(endpoints) > MAX_ENDPOINTS:
            break
        item = _resolve(doc, item)
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            summary = operation.get("summary") or operation.get("description") or ""
            endpoints.append(
                {
                    "operation_id": str(operation.get("operationId") or ""),
                    "method": method.upper(),
                    "path": str(path),
                    "description": str(summary).strip().split("\n\n")[0],
                    "params": _params(doc, item.get("parameters"), operation),
                }
            )
    return endpoints


def draft_from_spec(text: str, *, source_url: Optional[str] = None) -> dict:
    """The form pre-fill for one document: name, base URL, auth type, endpoints, and a note
    when something needs the owner's attention."""
    doc = _load(text)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    auth, note = _auth(doc)
    base_url = _base_url(doc, source_url)
    endpoints = _endpoints(doc)
    if len(endpoints) > MAX_ENDPOINTS:
        endpoints = endpoints[:MAX_ENDPOINTS]
        note = (note + " " if note else "") + (
            f"The document lists more than {MAX_ENDPOINTS:,} endpoints; only the first "
            f"{MAX_ENDPOINTS:,} were read."
        )
    if not base_url:
        note = (note + " " if note else "") + "The document names no server — enter the base URL."
    return {
        "name": sanitize_untrusted(str(info.get("title") or "").strip(), max_len=80),
        "base_url": sanitize_untrusted(base_url, max_len=500),
        "auth": auth,
        "endpoints": clean_endpoints(endpoints),
        "note": note.strip(),
    }


def draft(*, url: Optional[str] = None, text: Optional[str] = None) -> dict:
    """A draft from a pasted document, or from one downloaded from ``url``."""
    if text and text.strip():
        return draft_from_spec(text)
    if url and url.strip():
        return draft_from_spec(fetch_spec(url.strip()), source_url=url.strip())
    raise ValidationError("Paste the document or give its address.")
