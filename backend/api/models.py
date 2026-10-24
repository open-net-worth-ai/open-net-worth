"""Runtime AI provider/model config API.

Lets a self-hoster switch the AI provider + model from the running instance —
including pointing at a LOCAL OpenAI-compatible endpoint (Ollama or any
OpenAI-compatible URL) — without an env edit + restart. The change takes effect on
the NEXT chat turn (``assistant.get_provider``/``get_model`` read the store).

Thin router: validate input → call ``ModelConfigStore`` → shape the response. No
financial logic. The ``api_key`` is WRITE-ONLY — accepted on ``PUT`` but never
returned (``public()`` exposes only ``has_api_key``); a blank key leaves the saved
one unchanged.

Human-only, like auth/branding — there is intentionally NO Lucius tool for this
(it is instance config that changes which model the assistant itself runs on). All
routes are gated by the app-wide ``require_auth`` dependency.

Multi-provider credentials are stored in the ``providers`` sub-map of
``model_config.json`` (see ``ModelConfigStore.update_provider``).  A separate
``PUT /api/model-config/providers/{provider_key}`` endpoint patches ONE provider's
credentials so the chat/agent model picker can show models from ALL configured
providers (``GET /api/models?all=1`` returns the union, each entry labelled by
provider so the UI can render grouped option groups).

Key-validation gating
---------------------
When a key is saved (``PUT providers/{key}``), the backend synchronously validates
it by attempting the real model-list fetch (timeout ~6 s).  The result —
``key_status ∈ "valid" | "invalid" | "unreachable"`` + ``checked_at`` ISO timestamp
— is persisted on the provider entry and echoed in the response.  The same status is
included in ``GET /api/model-config/providers`` so the UI can gate the model-select
and "Set as default" controls.  A dedicated
``POST /api/model-config/providers/{key}/validate`` re-runs validation with the
STORED credentials (bypassing the models cache) and updates the persisted status.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.ai.models.cli_default_model import cli_default_model, cli_default_models
from backend.ai.models.codex_cli_catalog import model_ids as _codex_model_ids
from backend.core.config import get_settings
from backend.core.errors import ConfigurationError, ProviderDisabledError
from backend.core.io import atomic_write_text
from backend.core.net_guard import UnsafeUrlError
from backend.db import app_config_store
from backend.db.model_config_store import PROVIDER_CATALOG, PROVIDER_HINTS, ModelConfigStore
from backend.models.model_config import VALID_PROVIDERS, ModelConfigUpdate
from backend.paths import MODELS_CACHE_JSON

logger = logging.getLogger(__name__)

router = APIRouter()

# Last-known current model ids, used when a LIVE fetch is impossible (no key / no
# network / provider error). Kept tiny + current — NOT a hand-maintained catalog,
# just a safe floor so the dropdown is never empty. The Anthropic ids are the
# concern named in the spec (a stale hardcoded list showed a retired Opus).
# Refreshed 2026-10-04 against LiteLLM's public price list (it still named Opus 4.8 / Sonnet 4.6).
_ANTHROPIC_FALLBACK = [
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-4-5",
]
# For an OpenAI-compatible provider we can't know the model set offline; the
# configured model (if any) is added by the route so the picker still shows it.
_OPENAI_FALLBACK: list[str] = []
# The Claude Code CLI exposes no model-list endpoint, so this transport's picker is a
# curated list. Aliases first (they track the CLI's own current model for each tier —
# the right default for a self-hoster who does not want to chase model ids), then a few
# pinned ids for someone who does.
_CLAUDE_CLI_MODELS = [
    "sonnet",
    "opus",
    "haiku",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-4-5",
    "claude-fable-5-1",
]
_ANTIGRAVITY_CLI_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.7-flash",
]

# Below this the CLI could truncate the final `result` line when stdout is piped, which
# would silently cost us the usage/cost record for every turn.
_MIN_CLI_VERSION = (2, 1, 208)
# Per-CLI-transport probe facts: how to name it, how to LIST its models, the install hint,
# and the version floor. The BINARY NAME is not repeated here — it lives on the
# PROVIDER_CATALOG entry (``cli_binary``), which is also what the store's auto-detection
# resolves, so the picker and the probe can never disagree about which executable a
# transport means.
#
# ``models`` is a CALLABLE, resolved per request rather than frozen at import, because the
# Codex CLI publishes its own catalog on disk ($CODEX_HOME/models_cache.json) and refreshes
# it behind our back — a list captured at import would go stale the moment the CLI updated.
# The Claude CLI publishes nothing, so its resolver returns the curated aliases.
#
# A minimum version is asserted ONLY where we have a verified reason for one (the Claude
# CLI's truncated-`result` bug) — inventing a floor for Codex would fail a working install
# over an unverified claim.
_CLI_TRANSPORTS: dict = {
    "claude_cli": {
        "name": "Claude Code CLI",
        "models": lambda: list(_CLAUDE_CLI_MODELS),
        "min_version": _MIN_CLI_VERSION,
        "upgrade": "claude update",
        "install": (
            "Install it from https://claude.com/claude-code on the machine running "
            "Open Net Worth, run `claude` once to sign in, then check again. "
            "If needed, enter the executable's absolute CLI path."
        ),
    },
    "codex_cli": {
        "name": "Codex CLI",
        "models": _codex_model_ids,
        "min_version": None,
        "upgrade": "codex --upgrade",
        "install": (
            "Install it from https://developers.openai.com/codex/cli on the machine running "
            "Open Net Worth, run `codex login` once to sign in, then check again. "
            "If needed, enter the executable's absolute CLI path."
        ),
    },
    "antigravity_cli": {
        "name": "Antigravity CLI",
        "models": lambda: list(_ANTIGRAVITY_CLI_MODELS),
        "min_version": None,
        "upgrade": "agy update",
        "install": (
            "Install it from https://antigravity.google, run `agy` once to log in, "
            "then restart this app."
        ),
    },
}

# How long a live model list is trusted before we re-fetch (the dropdown is opened
# often; the available model set changes rarely). Cache is keyed on provider+base_url
# so switching provider invalidates it.
_CACHE_TTL_SECONDS = 6 * 60 * 60
# Bump when the cached row SHAPE or its derived values change, so pre-existing caches
# are treated as a miss and re-fetched immediately instead of serving stale data for
# up to the TTL. v2: per-model effort_levels (Anthropic xhigh / OpenRouter supported_efforts).
# v3: the capability filter drops no-tool-calling / non-text-output models at fetch, so a
# v2 cache (built pre-filter) must not be served — it would still contain the junk.
# v4: metadata-less providers (xAI/OpenAI-direct) now also drop obvious media models by
# modality-name heuristic — a v3 cache still holds e.g. grok-imagine-image.
# v5: cache file shape changed to a multi-key {providers: {key: {...}}} map (a v4
# single-slot file would just miss and re-fetch).
_CACHE_SCHEMA = 5

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"
_HTTP_TIMEOUT = 8.0
# Shorter dedicated timeout for the on-save validation probe (the user is waiting).
_VALIDATE_TIMEOUT = 6.0

KeyStatus = Literal["valid", "invalid", "unreachable", "unchecked"]


def _probe_cli(transport: str, cli_path: str) -> tuple[KeyStatus, list[str], str]:
    """Validate a headless-CLI transport by locating and version-probing the binary.

    There is no key to validate here — the CLI authenticates with the user's own
    interactive login — so "valid" means *the binary exists, runs, and is new enough*.
    A missing/old binary is ``"unreachable"``, never ``"invalid"``: nothing was rejected,
    something is just not installed (the same rule keyless Ollama already follows).
    """
    import shutil as _shutil
    import subprocess

    from backend.db.model_config_store import _provider_entry

    spec = _CLI_TRANSPORTS[transport]
    binary = (_provider_entry(transport) or {}).get("cli_binary") or transport
    resolved = _shutil.which((cli_path or "").strip() or binary)
    if not resolved:
        return (
            "unreachable",
            [],
            f"{spec['name']} not found on PATH. {spec['install']}",
        )
    try:
        out = subprocess.run(  # noqa: S603 - resolved absolute path, fixed argv, no shell
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=_VALIDATE_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return "unreachable", [], f"Could not run {resolved}: {exc}"
    if out.returncode != 0:
        return "unreachable", [], f"{resolved} --version exited {out.returncode}"

    version_text = (out.stdout or "").strip()
    floor = spec["min_version"]
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_text)
    if floor and match and tuple(int(g) for g in match.groups()) < floor:
        wanted = ".".join(str(p) for p in floor)
        return (
            "unreachable",
            [],
            f"{spec['name']} {version_text} is too old (need >= {wanted}); "
            f"run `{spec['upgrade']}`.",
        )
    return "valid", list(spec["models"]()), ""


# Phrases providers use for "that key is not good", when the status code does not say so.
# Deliberately narrow: a wrong guess here turns a temporary outage into "your key is wrong",
# which sends someone hunting for a new key they do not need.
_KEY_REJECTION_MARKERS = (
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    "invalid_api_key",
    "valid api key",
    "incorrect api key",
    "unauthorized",
    "authentication",
)


def _assert_endpoint_allowed(url: str) -> None:
    """Refuse a provider endpoint that points somewhere an owner-configured URL may not:
    cloud metadata / link-local always, loopback and LAN too on a hosted (gateway) instance.
    Raises ``UnsafeUrlError``; callers turn it into their own "unreachable" answer."""
    from backend.core.net_guard import resolve_owner_url

    resolve_owner_url(url)


def _provider_error_text(resp: "httpx.Response") -> str:
    """The provider's own message when it comes in a shape we KNOW, otherwise "".

    Used for CLASSIFYING, not for display: see `_explain_failure`. Every provider nests its
    message somewhere different, so the common shapes are tried in turn.
    """
    try:
        body = resp.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        for field in ("message", "detail", "status", "code"):
            value = err.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    elif isinstance(err, str) and err.strip():
        return err.strip()[:200]
    for field in ("message", "detail"):
        value = body.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


# Providers answer in their own machine-facing words — "invalid x-api-key", "API_KEY_INVALID",
# "Please pass a valid API key". Those are for logs. What reaches a setup screen is OUR
# sentence, naming the provider, for the cases we can recognise; anything else gets a plain
# fallback rather than a raw string pasted out of a JSON body.
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "too many requests", "quota", "429")
_OVERLOAD_MARKERS = ("overloaded", "unavailable", "capacity", "try again later")


def _explain_failure(label: str, status: int, body: str) -> str:
    text = (body or "").lower()
    if _looks_like_a_key_rejection(text) or status in (401, 403):
        return f"That key was rejected by {label}."
    if any(m in text for m in _RATE_LIMIT_MARKERS) or status == 429:
        return f"{label} is rate-limiting this key. Try again shortly."
    if any(m in text for m in _OVERLOAD_MARKERS) or status >= 500:
        return f"{label} is not responding right now. Try again shortly."
    return f"Could not reach {label}."


def _unusable_key_reason(api_key: str) -> str:
    """Why this string cannot even be SENT as a credential, or "" if it can.

    An HTTP header is latin-1 at best, so a key carrying an em dash, a smart quote or a
    newline makes httpx raise before any request leaves the machine — which surfaced as a
    generic "could not check that provider" and sent people looking for an outage. The usual
    cause is a paste that picked up the wrong clipboard contents, and that is worth saying.
    """
    if not api_key:
        return ""
    if "\n" in api_key or "\r" in api_key:
        return "That key contains a line break — check what was pasted."
    if not api_key.isascii():
        return "That key contains characters a credential cannot carry — check what was pasted."
    # Spaces and control bytes are not legal in a header value either, and h11's refusal
    # quotes the offending value — i.e. the key — into an exception we log. Refuse it here,
    # before any request is built.
    if re.search(r"[\x00-\x20\x7f]", api_key):
        return "That key contains a space or a control character — check what was pasted."
    return ""


def _looks_like_a_key_rejection(body: str) -> bool:
    text = (body or "").lower()
    return any(marker in text for marker in _KEY_REJECTION_MARKERS)


def _exc_label(exc: BaseException) -> str:
    """What is safe to LOG about a failed provider call: its type, or its HTTP status.

    Never its text. h11 refuses a header value holding a control byte with an error that QUOTES
    the value — `Illegal header value b'Bearer <key>'` — and httpx/httpcore pass it through
    unchanged, so logging ``exc`` writes the API key into app.log (which Lucius can read).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _validate_credentials(
    provider_key: str, transport: str, base_url: str, api_key: str
) -> tuple[KeyStatus, list[str], str]:
    """Synchronously validate credentials by attempting the model-list fetch.

    Returns ``(status, models, error_message)`` where:
    - ``status`` is ``"valid"``, ``"invalid"``, or ``"unreachable"``.
    - ``models`` is the live list on success, empty list otherwise.
    - ``error_message`` is a short human-readable string (empty on success).

    Classification:
    - HTTP 401 / 403 → ``"invalid"`` (key rejected by the provider).
    - Successful fetch with non-empty model list → ``"valid"``.
    - Timeout / connection failure / 5xx / empty-list / other → ``"unreachable"``.
    - Ollama (keyless, ``needs_api_key=False``): reachable + models → ``"valid"``;
      any failure → ``"unreachable"`` (never ``"invalid"``).
    """
    from backend.db.model_config_store import _provider_entry

    entry = _provider_entry(provider_key)
    # An OPTIONAL key behaves like no key at all for validation: a local LiteLLM started
    # without a master key is not misconfigured, so it must never be told its key was
    # rejected, and must not be refused for having none.
    needs_api_key = bool(entry and entry["needs_api_key"] and not entry.get("optional_api_key"))

    label = (entry or {}).get("label") or provider_key
    unusable = _unusable_key_reason(api_key)
    if unusable:
        return "invalid", [], unusable

    try:
        if transport in _CLI_TRANSPORTS:
            return _probe_cli(transport, base_url)
        if transport == "anthropic":
            if not api_key:
                return "unreachable", [], "No API key provided"
            resp = httpx.get(
                _ANTHROPIC_MODELS_URL,
                headers={"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION},
                params={"limit": 1000},
                timeout=_VALIDATE_TIMEOUT,
            )
            if resp.status_code in (401, 403):
                return "invalid", [], _explain_failure(label, resp.status_code, resp.text)
            resp.raise_for_status()
            data = resp.json().get("data") or []
            ids = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
        else:  # openai_compatible
            if not api_key and needs_api_key:
                return "unreachable", [], "No API key provided"
            url = base_url.rstrip("/") + "/models"
            _assert_endpoint_allowed(url)
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(url, headers=headers, timeout=_VALIDATE_TIMEOUT)
            # Only flag as invalid on auth errors AND when a key is required
            # (keyless providers like Ollama should never return "invalid").
            if resp.status_code in (401, 403) and needs_api_key:
                return "invalid", [], _explain_failure(label, resp.status_code, resp.text)
            if resp.status_code >= 400:
                # Not every provider signals a bad key with 401/403. Google answers a bogus
                # key with HTTP 400 and "Please pass a valid API key", which fell through to
                # "unreachable" — so first run accepted any string as a Gemini key and only
                # failed later, when Lucius could not answer. Classify on what the provider
                # SAYS when the status code is not specific enough.
                explained = _explain_failure(label, resp.status_code, resp.text)
                if needs_api_key and _looks_like_a_key_rejection(resp.text):
                    return "invalid", [], explained
                return "unreachable", [], explained
            data = resp.json().get("data") or []
            ids = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]

        if not ids:
            return "unreachable", [], f"{label} returned no models."

        # A public model list proves the provider is up, not that the key is good. Where the
        # provider offers an endpoint that actually requires the credential, ask it too —
        # otherwise any string validates and fails on the first real request instead.
        probe_path = (entry or {}).get("auth_probe_path") if entry else None
        if probe_path and needs_api_key:
            _assert_endpoint_allowed(base_url.rstrip("/") + probe_path)
            probe = httpx.get(
                base_url.rstrip("/") + probe_path,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_VALIDATE_TIMEOUT,
            )
            if probe.status_code in (401, 403) or (
                probe.status_code >= 400 and _looks_like_a_key_rejection(probe.text)
            ):
                return "invalid", [], _explain_failure(label, probe.status_code, probe.text)

        return "valid", ids, ""
    except UnsafeUrlError:
        return "unreachable", [], f"The address for {label} is not allowed."
    except httpx.TimeoutException:
        return "unreachable", [], f"{label} did not respond in time."
    except httpx.ConnectError:
        return "unreachable", [], f"Could not connect to {label}."
    except Exception as exc:
        # LOGGED in full, shown as a plain sentence. The exception text is for whoever reads
        # the logs; an operator staring at a setup screen needs to know it failed, not to read
        # a Python repr.
        logger.warning("provider validation failed for %s: %r", provider_key, exc)
        return "unreachable", [], f"Could not check {label}."


@router.get("/api/model-config")
def get_model_config():
    """The active provider/model/base_url (+ has_api_key). Never returns the key.

    Also carries ``image_reader`` — the model that would read an attachment for a chat
    model with no vision, or ``""`` when there is none. DERIVED, not stored (the
    ``chat_vision`` pin, else the instance default when it isn't itself blind), so the
    composer can tell the user "no vision model is configured" as a FACT before they
    send, instead of the vague "if you've configured one" it would otherwise have to
    guess with.
    """
    store = ModelConfigStore()
    out = store.public()
    # A blank CLI model means "run the CLI's own default" — name it, so what the composer chip
    # shows is what actually runs.
    out["effective_model"] = out["model"] or cli_default_model(out["provider"])
    try:
        from backend.services.vision_capability import resolve_extractor_ref

        out["image_reader"] = resolve_extractor_ref(store=store)
    except Exception:  # never fail the config read over a derived convenience field
        out["image_reader"] = ""
    return out


@router.get("/api/model-config/providers")
def get_model_config_providers():
    """The supported provider kinds + well-known base_url hints (e.g. Ollama).

    Returns both the legacy ``providers`` map (transport-keyed, back-compat) and the
    new ``catalog`` list of named presets (presentation layer — Anthropic · OpenAI ·
    Gemini · Ollama · xAI (Grok) · Zhipu (GLM) · OpenRouter · LiteLLM · Custom). The UI uses
    ``catalog`` for the provider <Select>; the underlying PUT still validates against
    the two transports (``anthropic`` | ``openai_compatible``), not provider keys.

    Also returns ``configured_providers`` — the public (no-key) view of any per-provider
    credentials that have been explicitly stored so the multi-provider model picker can
    show which providers are available without revealing any key.

    Each configured provider now includes ``key_status`` (``"valid" | "invalid" |
    "unreachable" | "unchecked"``) and ``checked_at`` (ISO datetime or null) so the
    frontend can gate the model-select and "Set as default" controls.

    ``detected_clis`` maps each local-CLI catalog key to whether its binary resolves on
    THIS machine.  Those transports enrol by detection rather than by stored credentials,
    so without this the Settings row has nothing true to say about itself while its models
    are already showing up in every picker.  Detection writes nothing.
    ``cli_host_platform`` names the server OS for installation instructions.
    """
    store = ModelConfigStore()
    return {
        "providers": PROVIDER_HINTS,
        "catalog": PROVIDER_CATALOG,
        "configured_providers": store.providers_public(),
        "detected_clis": store.detected_clis(),
        # Absolute path per local CLI ("" when absent) — the row shows it exactly where a
        # URL provider shows its endpoint.
        "detected_cli_paths": store.detected_cli_paths(),
        # What each CLI runs when told nothing (its own config, else its own catalog order) —
        # the model first run and Settings suggest, never a hint of ours.
        "cli_default_models": cli_default_models(),
        "cli_host_platform": {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(
            platform.system(), "other"
        ),
    }


@router.put("/api/model-config")
def put_model_config(body: ModelConfigUpdate):
    """Update the runtime AI config. A blank/omitted ``api_key`` keeps the saved key;
    an unknown ``provider`` → HTTP 422. Returns the public (no-key) view."""
    changes = body.model_dump(exclude_none=True)
    provider = changes.get("provider")
    if provider is not None:
        provider = provider.strip().lower()
        if provider not in VALID_PROVIDERS:
            raise HTTPException(
                status_code=422,
                detail=f"provider must be one of {list(VALID_PROVIDERS)}",
            )
        changes["provider"] = provider
    try:
        return ModelConfigStore().update(changes)
    except ConfigurationError as exc:
        # e.g. defaulting to a provider with no model picked — a 422, not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Per-provider credential endpoints ─────────────────────────────────────────
#
# These let the user store a 2nd (or 3rd…) provider's api_key without changing
# the instance default.  A per-conversation override can then reference that
# provider by provider_key so different conversations / agents run on different
# transports.


class ProviderCredentialsUpdate(BaseModel):
    """Partial update for one provider's credentials.

    ``api_key`` is WRITE-ONLY: a blank/omitted value leaves the saved key unchanged.
    ``base_url`` overrides the catalog default for this provider (useful for custom
    editable-base-url entries like ``ollama`` / ``vllm`` / ``custom``).
    ``model`` stores a per-provider preferred model so the UI can pre-populate the
    model field for a non-default provider without touching the instance default.
    ``enabled`` toggles the provider on/off without removing credentials; absent
    means "no change".
    """

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


@router.put("/api/model-config/providers/{provider_key}")
def put_provider_credentials(provider_key: str, body: ProviderCredentialsUpdate):
    """Store credentials for ONE additional provider (by provider key).

    Does NOT change the instance default (use ``PUT /api/model-config`` for that).
    Validates the credentials synchronously after saving (timeout ~6 s) and persists
    the result (``key_status``, ``checked_at``) on the provider entry.

    When ``enabled=False`` is sent, the provider is disabled without losing its
    credentials.  Disabling the provider key that the active instance default resolves
    to is refused with 409 — set another provider as default first.

    When ``enabled=True`` is sent, the provider is re-enabled and validation is
    re-run automatically so stale key status is refreshed.

    Response includes the public view of ALL configured per-provider entries plus
    ``key_status``, ``models`` (when valid), and ``error`` (terse human string on
    failure).  The api_key is NEVER included in any response.

    An unknown ``provider_key`` → 404.  Sending an empty/blank api_key + empty
    base_url removes the entry for this provider.

    The models cache is busted on any ``enabled`` change so the model picker
    reflects the new state without waiting 6 hours.
    """
    from backend.db.model_config_store import _infer_provider_key, _provider_entry

    entry = _provider_entry(provider_key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown provider key: {provider_key!r}")

    changes = body.model_dump(exclude_none=True)
    store = ModelConfigStore()

    # Guard: refuse to disable the active instance default.
    if changes.get("enabled") is False:
        inst = store.resolved()
        default_key = _infer_provider_key(inst["provider"], inst.get("base_url") or "")
        if default_key == provider_key:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot disable {entry['label']!r} — it is the active default. "
                    "Set another provider as default first."
                ),
            )
        # Bust the models cache so the change is reflected immediately.
        _bust_models_cache()

    elif changes.get("enabled") is True:
        # Re-enabling: also bust the cache.
        _bust_models_cache()

    store.update_provider(provider_key, changes)

    # Skip validation when disabling — no credentials to check.
    if changes.get("enabled") is False:
        public = store.providers_public()
        return {"configured_providers": public, "key_status": "unchecked", "checked_at": None}

    # Validate with the now-saved credentials (may have just been updated).
    try:
        creds = store.resolve_for_provider(provider_key) or {}
    except Exception:
        # resolve_for_provider_key raises ProviderDisabledError when disabled; this
        # branch is hit if enabled was just set to False above (already handled) —
        # guard against races.
        public = store.providers_public()
        return {"configured_providers": public, "key_status": "unchecked", "checked_at": None}

    api_key = (creds.get("api_key") or "").strip()
    base_url = (creds.get("base_url") or entry["base_url"] or "").strip()
    transport: str = entry["transport"]

    status, models, error = _validate_credentials(provider_key, transport, base_url, api_key)
    checked_at = datetime.now(timezone.utc).isoformat()

    # Persist the validation result without touching the key.
    store.update_provider(provider_key, {"key_status": status, "checked_at": checked_at})

    # Update the models cache on success so GET /api/models benefits immediately.
    if status == "valid" and models:
        cache_key = _cache_key(provider_key, base_url)
        _write_cache(cache_key, _annotate_ids(models, transport, base_url))

    public = store.providers_public()
    result: dict = {"configured_providers": public, "key_status": status, "checked_at": checked_at}
    if status == "valid":
        result["models"] = models
    else:
        result["error"] = error
    return result


@router.delete("/api/model-config/providers/{provider_key}/key")
def delete_provider_key(provider_key: str):
    """Explicitly remove ONE provider's stored api_key (and validation status).

    This is the only deletion path — the write-only PUT contract treats a blank
    ``api_key`` as "keep the saved key", so deletion must be an explicit action.
    Removing the key of the provider the active instance default resolves to is
    refused with 409 (it would break every chat turn) — set another default first.
    ``base_url``/``model``/``enabled=False`` survive; an empty entry is pruned.
    """
    from backend.db.model_config_store import _infer_provider_key, _provider_entry

    entry = _provider_entry(provider_key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown provider key: {provider_key!r}")

    store = ModelConfigStore()
    inst = store.resolved()
    default_key = _infer_provider_key(inst["provider"], inst.get("base_url") or "")
    if default_key == provider_key and entry.get("needs_api_key", True):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot remove the API key for {entry['label']!r} — it is the active "
                "default. Set another provider as default first."
            ),
        )

    public = store.remove_provider_key(provider_key)
    _bust_models_cache()
    return {"configured_providers": public}


@router.delete("/api/model-config/providers/{provider_key}")
def delete_provider(provider_key: str):
    """Remove ONE provider entirely — key, base_url, model and enabled flag.

    The sibling ``…/{provider_key}/key`` deletes only the API key and deliberately keeps
    the rest, so a DISABLED provider (which always retains ``enabled=False``) never left
    the Settings list however many times you pressed Remove. This is the endpoint behind
    that button; ``+ Add provider`` re-adds the provider from the catalog.

    Refused with 409 when the provider backs the active instance default — same guard as
    key deletion, and for the same reason: it would break every chat turn. Set another
    default first.
    """
    from backend.db.model_config_store import _infer_provider_key, _provider_entry

    entry = _provider_entry(provider_key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown provider key: {provider_key!r}")

    store = ModelConfigStore()
    inst = store.resolved()
    default_key = _infer_provider_key(inst["provider"], inst.get("base_url") or "")
    if default_key == provider_key:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot remove {entry['label']!r} — it is the active default. "
                "Set another provider as default first."
            ),
        )

    public = store.remove_provider(provider_key)
    _bust_models_cache()
    return {"configured_providers": public}


# ── Per-task model assignment ─────────────────────────────────────────────────
#
# Background tasks (memory, intel, suggestions, briefing) can each be pinned to
# a specific provider:model.  Absent assignment = task uses its hard-coded default.


class TaskModelsUpdate(BaseModel):
    """Body for PUT /api/model-config/task-models — the WHOLE assignment map.

    Each field is a compound ``"<provider_key>:<model_id>"`` string (e.g.
    ``"anthropic:claude-haiku-4-5"``); ``null``/empty clears that task's assignment
    (restores the default). Omitted fields are left unchanged.
    """

    # EVERY member of VALID_TASKS must have a field here — a task the store can route but
    # this body cannot name is one Pydantic silently drops, so it can never be pinned and
    # falls back to the instance default forever. That is exactly how `news_screen` ended up
    # running on the OpenRouter default while the owner had pinned every task the UI showed
    # them to the Claude CLI, with nothing anywhere reporting the discrepancy.
    # `tests/test_task_models_coverage.py` fails if the two lists ever drift again.
    memory: Optional[str] = None
    intel: Optional[str] = None
    suggestions: Optional[str] = None
    briefing: Optional[str] = None
    news_screen: Optional[str] = None
    # The Analyst sweep's full synthesis call — see VALID_TASKS's own comment for the
    # 2026-08-25 incident this closes (a task the store always routed but no field here could
    # ever name, so it silently ran on the instance default forever).
    analysis: Optional[str] = None
    price_monitor: Optional[str] = None
    # ONE knob for the whole import pipeline (owner, 2026-08-09: "merge all of these into
    # one settings only") — replaced csv_schema / statement_parse / statement_vision /
    # statement_metadata / statement_lucius_pass, which were only ever set to the same model.
    # A scanned page still needs to SEE: it falls back to the instance default image model
    # rather than carrying its own knob (`vision_capability.resolve_extractor_ref`).
    statement_import: Optional[str] = None
    # Reads an image attached to a chat turn whose own model has no vision (ADR
    # 2026-07-26). Settable here because the no-vision fallback message TELLS the user to
    # pin it — an instruction pointing at a field the API silently drops would be a lie.
    chat_vision: Optional[str] = None
    title: Optional[str] = None
    # The Auto-approval judge's model; unpinned it runs on the conversation's own model.
    approval: Optional[str] = None


@router.put("/api/model-config/task-models")
def put_task_models(body: TaskModelsUpdate):
    """Assign/clear ALL per-task model overrides in one request, one atomic write.

    All entries are validated first — a bad one fails the whole request (no partial
    state). Reads live in ``GET /api/model-config`` (``task_models`` field).

    400 malformed override / unknown provider key; 409 referenced provider disabled.
    Returns the full public model-config view so the frontend updates in one
    round-trip.
    """
    store = ModelConfigStore()
    try:
        return store.set_task_models(body.model_dump(exclude_unset=True))
    except ProviderDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Turn time limit ──────────────────────────────────────────────────────────
#
# How long one Lucius turn (chat or Automation run) may run. Instance config, human-only: no
# Lucius tool writes it. Writes are demo-locked with the rest of /api/model-config.

TURN_TIME_LIMIT_CHOICES = (1200, 1800, 3600, 7200, 0)  # 0 = no limit


class TurnTimeLimitUpdate(BaseModel):
    seconds: int


def _turn_time_limit_choices() -> list[int]:
    """The fixed choices plus the env default (an install may set ONW_TURN_TIMEOUT_S), with
    no limit last."""
    timed = {c for c in TURN_TIME_LIMIT_CHOICES if c} | {get_settings().turn_timeout_s}
    return sorted(t for t in timed if t > 0) + [0]


def _turn_time_limit_view() -> dict:
    return {
        "seconds": app_config_store.turn_time_limit_s(),
        "default_seconds": get_settings().turn_timeout_s,
        "choices": _turn_time_limit_choices(),
    }


@router.get("/api/model-config/turn-time-limit")
def get_turn_time_limit():
    """The turn time limit in force (``seconds``, 0 = no limit) and the offered choices."""
    return _turn_time_limit_view()


@router.put("/api/model-config/turn-time-limit")
def put_turn_time_limit(body: TurnTimeLimitUpdate):
    """Set the turn time limit. ``seconds`` must be one of ``choices``; 0 = no limit."""
    choices = _turn_time_limit_choices()
    if body.seconds not in choices:
        raise HTTPException(status_code=422, detail=f"seconds must be one of {choices}")
    app_config_store.AppConfigStore().set_turn_time_limit_s(body.seconds)
    return _turn_time_limit_view()


# ── Re-check stored credentials ──────────────────────────────────────────────
#
# Used by the "Check connection" affordance and for legacy rows that have no
# stored key_status (they show as "unchecked" until the user expands them).


@router.post("/api/model-config/providers/{provider_key}/validate")
def validate_provider_credentials(provider_key: str):
    """Re-run key validation using the STORED credentials and update the persisted status.

    Bypasses the models cache (force-fetches live) and updates the cache on success.
    Returns ``{key_status, checked_at, models (when valid), error (otherwise)}``.
    The api_key is NEVER echoed.

    404 on unknown provider key.
    """
    from backend.db.model_config_store import _provider_entry

    entry = _provider_entry(provider_key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown provider key: {provider_key!r}")

    store = ModelConfigStore()
    # require_enabled=False: testing a key is not using the provider. Going through the gate
    # RAISED for a switched-off provider, and unguarded that 500'd — the UI, seeing no
    # `error` field, then printed "unreachable" and blamed the network for a real key on a
    # provider the owner had simply turned off.
    creds = store.resolve_for_provider(provider_key, require_enabled=False) or {}
    api_key = (creds.get("api_key") or "").strip()
    base_url = (creds.get("base_url") or entry["base_url"] or "").strip()
    transport: str = entry["transport"]

    # Bust the cache so this is always a live round-trip.
    cache_key_str = _cache_key(provider_key, base_url)
    try:
        if MODELS_CACHE_JSON.exists():
            raw = json.loads(MODELS_CACHE_JSON.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("key") == cache_key_str:
                MODELS_CACHE_JSON.unlink(missing_ok=True)
    except OSError:
        pass

    status, models, error = _validate_credentials(provider_key, transport, base_url, api_key)
    checked_at = datetime.now(timezone.utc).isoformat()
    store.update_provider(provider_key, {"key_status": status, "checked_at": checked_at})

    if status == "valid" and models:
        _write_cache(cache_key_str, _annotate_ids(models, transport, base_url))

    result: dict = {"key_status": status, "checked_at": checked_at}
    if status == "valid":
        result["models"] = models
    else:
        result["error"] = error
    return result


# ── Reveal stored API key ─────────────────────────────────────────────────────
#
# Human-only affordance so Settings can show the saved key on demand.  This
# must NEVER appear in any AI tool, never in logs, never in any other payload.


@router.get(
    "/api/model-config/providers/{provider_key}/reveal-key",
    include_in_schema=False,  # human-only settings affordance — keep out of the OpenAPI surface
)
def reveal_provider_key(provider_key: str):
    """Return the stored plaintext API key for one provider (on demand, human-only).

    Rules:
    - Reads only from model_config.json (providers map).  ENV-derived keys are
      never exposed.
    - 404 on unknown provider key or when no stored key exists.
    - Response carries ``Cache-Control: no-store`` so the key is never cached.
    - The key VALUE is never logged; the reveal EVENT is, so operators can see
      reveals in the log viewer.
    """
    from fastapi.responses import JSONResponse

    from backend.db.model_config_store import _provider_entry

    if _provider_entry(provider_key) is None:
        raise HTTPException(status_code=404, detail="unknown provider key")
    stored_key = ModelConfigStore().read_stored_api_key(provider_key)
    if not stored_key:
        raise HTTPException(status_code=404, detail="no stored key for this provider")
    logger.info("api key revealed for provider %s", provider_key)
    return JSONResponse(
        content={"api_key": stored_key},
        headers={"Cache-Control": "no-store"},
    )


# ── Live model list (GET /api/models) ─────────────────────────────────────────
#
# The Settings + chat model pickers used to show a hardcoded/hint-only list that
# drifted from reality (a retired Opus id). This endpoint fetches the ACTIVE
# provider's real model set live, caches it briefly, and falls back to a small
# static floor when a live call is impossible — it NEVER raises.
#
# With ``?all=1`` it returns the UNION across ALL configured providers (active +
# every provider in the per-provider credentials map), each entry labelled with
# ``provider_key`` and ``provider_label`` so the frontend can render grouped
# option groups.  Unknown/unconfigured providers contribute only their static
# fallback list.


def _anthropic_key(resolved: dict) -> str:
    """The Anthropic API key from the stored config (env fallback removed — configure via Settings)."""
    return (resolved.get("api_key") or "").strip()


def _fetch_anthropic_models(api_key: str) -> list[dict]:
    """GET the Anthropic models list → ordered [{id, reasoning, effort_levels}].

    Effort levels are PER MODEL (capabilities.effort_levels): modern models expose the
    native low/medium/high/xhigh/max ladder, Opus 4.6/Sonnet 4.6 drop xhigh, legacy
    budget models show their distinct rungs. Every current Anthropic model reasons.
    """
    from backend.ai.models.capabilities import can_disable_reasoning, effort_levels

    resp = httpx.get(
        _ANTHROPIC_MODELS_URL,
        headers={"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION},
        params={"limit": 1000},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    out: list[dict] = []
    for m in data:
        if not (isinstance(m, dict) and m.get("id")):
            continue
        levels = effort_levels("anthropic", "", m["id"])
        out.append(
            {
                "id": m["id"],
                "reasoning": bool(levels),
                "effort_levels": levels,
                "can_disable": can_disable_reasoning("anthropic", "", m["id"]),
                # Every model Anthropic's /v1/models returns accepts image input, and the
                # native provider sends real image blocks — so this is a fact, not a guess.
                "vision": True,
            }
        )
    return out


# Hyphen/dot-delimited id segments that mark a NON-text generative model (image/video/
# audio/embeddings) — used ONLY for providers whose /models carries no capability
# metadata (xAI, OpenAI-direct). NOT a model allow/deny list: a new TEXT model never
# contains these tokens, so it's a modality heuristic, not a name whitelist.
_NON_TEXT_MODALITY_TOKENS = frozenset(
    {
        "image",
        "images",
        "imagine",
        "video",
        "audio",
        "tts",
        "speech",
        "voice",
        "whisper",
        "embed",
        "embedding",
        "embeddings",
        "moderation",
        "rerank",
        "dalle",
    }
)


def _looks_non_text_model(model_id: str) -> bool:
    """Best-effort: does this model id clearly denote a non-text (image/video/audio/
    embedding) model? Segment-aware (so 'grok-imagine-image' and 'gpt-image-1' match,
    but a plain 'grok-4.5' / 'gpt-4o' never does), plus a 'dall-e' substring catch."""
    lid = (model_id or "").lower()
    if "dall-e" in lid:
        return True
    segments = set(re.split(r"[^a-z0-9]+", lid))
    return bool(_NON_TEXT_MODALITY_TOKENS & segments)


def _model_capability_ok(m: dict) -> bool:
    """Picker capability gate: the assistant is a tool-calling agent that answers in
    text, so a model that can't call tools or can't emit text is useless here and is
    dropped from the picker (a broken tool loop, not a real option).

    Two-tier, because only OpenRouter's ``/models`` publishes capability metadata:
    - **Metadata present** (OpenRouter): trust it exactly — drop when ``tools`` is not
      in ``supported_parameters`` or ``text`` is not in ``architecture.output_modalities``.
    - **Metadata absent** (xAI-direct, OpenAI-direct, GLM, local /models return only
      ``{id}``): fall back to a modality-NAME heuristic (``_looks_non_text_model``) so
      obvious media models — e.g. xAI's ``grok-imagine-image``/``-video``, OpenAI's
      ``dall-e-3``/``tts-1``/``whisper``/``text-embedding-*`` — are still dropped. A
      plain text id (``grok-4.5``, ``gpt-4o``) has no such token and is kept.
    """
    supported = m.get("supported_parameters")
    out_modalities = (m.get("architecture") or {}).get("output_modalities")
    has_metadata = isinstance(supported, list) or isinstance(out_modalities, list)
    if has_metadata:
        if isinstance(supported, list) and "tools" not in supported:
            return False
        if isinstance(out_modalities, list) and "text" not in out_modalities:
            return False
        return True
    return not _looks_non_text_model(m.get("id", ""))


def _vision_rows() -> dict:
    """The stored image-input capability map, read ONCE per model-list build.

    A provider's ``/models`` can carry hundreds of entries and every one of them needs
    this lookup, so it must not be a per-row store read (each is a JSON file read).
    Never raises — an unreadable config just means no stored opinions.
    """
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().vision_capabilities()
    except Exception:
        return {}


def _vision_for(
    provider_key: str, model_id: str, declared: Optional[bool], stored: dict
) -> Optional[bool]:
    """One model's image-input answer for the picker: stored > declared > name-inferred.

    Same precedence as the runtime ladder in services/vision_capability.py — the picker and the
    turn must never disagree about whether an attachment will be seen. ``None`` = unknown,
    which the UI renders as no claim (never as "no vision").
    """
    from backend.core.vision_names import infer_vision_from_name

    if provider_key:
        row = stored.get(f"{provider_key}:{model_id}")
        if isinstance(row, dict) and isinstance(row.get("supports"), bool):
            return bool(row["supports"])
    if declared is not None:
        return declared
    return infer_vision_from_name(model_id)


def _fetch_openai_models(base_url: str, api_key: str) -> list[dict]:
    """GET an OpenAI-compatible /models list → ordered [{id, reasoning}].

    ``reasoning`` is derived per model: OpenRouter's authoritative
    ``supported_parameters`` when present, else the provider's known reasoning
    families (see ai/models/capabilities). Models declaring no tool-calling or no text
    output are filtered out (``_model_capability_ok``). Raises on fetch failure.

    ``vision`` (image INPUT) rides along the same way, and where the provider declares
    it (OpenRouter's ``architecture.input_modalities`` — nothing else we talk to does) the
    declaration is RECORDED into the config store, so the runtime pre-send check in
    services/vision_capability.py gets authoritative data without making a live call of its own.
    """
    from backend.ai.models.capabilities import can_disable_reasoning, effort_levels
    from backend.core.vision_names import vision_from_modalities
    from backend.db.model_config_store import _infer_provider_key
    from backend.services.vision_capability import record_vision_capability

    provider_key = _infer_provider_key("openai_compatible", base_url) or ""
    stored_vision = _vision_rows()

    url = base_url.rstrip("/") + "/models"
    _assert_endpoint_allowed(url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    resp = httpx.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data") or []
    out: list[dict] = []
    for m in data:
        if not (isinstance(m, dict) and m.get("id")):
            continue
        if not _model_capability_ok(m):
            continue
        reasoning_meta = m.get("reasoning") if isinstance(m.get("reasoning"), dict) else None
        cross_ref = _cross_ref_efforts(base_url, m["id"])
        levels = effort_levels(
            "openai_compatible",
            base_url,
            m["id"],
            m.get("supported_parameters"),
            reasoning_meta,
            cross_ref,
        )
        declared = vision_from_modalities((m.get("architecture") or {}).get("input_modalities"))
        if declared is not None and provider_key:
            # Never overwrites a "learned" refusal or the owner's own override — the
            # provenance ratchet lives in record_vision_capability.
            record_vision_capability(f"{provider_key}:{m['id']}", declared, "declared")
        out.append(
            {
                "id": m["id"],
                "reasoning": bool(levels),
                "effort_levels": levels,
                "can_disable": can_disable_reasoning(
                    "openai_compatible",
                    base_url,
                    m["id"],
                    m.get("supported_parameters"),
                    reasoning_meta,
                    cross_ref,
                ),
                # True/False/None — None means "nobody knows", which the picker must render
                # as no claim at all rather than as "no vision" (see services/vision_capability.py).
                "vision": _vision_for(provider_key, m["id"], declared, stored_vision),
            }
        )
    return out


def _annotate_ids(ids: list[str], transport: str, base_url: str) -> list[dict]:
    """Bare id list (e.g. the validation probe / a fallback floor) → [{id, reasoning,
    effort_levels}]. No inline OpenRouter reasoning object here, but a DIRECT OpenAI/
    xAI model still cross-references OpenRouter's public catalog for its efforts.
    """
    from backend.ai.models.capabilities import can_disable_reasoning, effort_levels
    from backend.db.model_config_store import _infer_provider_key

    provider_key = _infer_provider_key(transport, base_url) or ""
    stored_vision = _vision_rows()
    out: list[dict] = []
    for i in ids:
        cross_ref = _cross_ref_efforts(base_url, i)
        lv = effort_levels(transport, base_url, i, None, None, cross_ref)
        out.append(
            {
                "id": i,
                "reasoning": bool(lv),
                "effort_levels": lv,
                "can_disable": can_disable_reasoning(transport, base_url, i, None, None, cross_ref),
                # No provider metadata on this path (a bare id list) — stored opinion, else
                # the name heuristic. Same precedence as everywhere else.
                "vision": _vision_for(provider_key, i, None, stored_vision),
            }
        )
    return out


def _model_row(model: dict, **extra) -> dict:
    """Shape one internal {id, reasoning, effort_levels} model into a picker row
    (+ optional provider_key/provider_label). ``effort_levels`` are the exact tiers
    the model accepts (empty → hide the effort dial); ``reasoning`` is the derived
    show/hide bool."""
    from backend.ai.models.capabilities import FULL_LADDER

    levels = model.get("effort_levels")
    if levels is None:  # legacy shape safety (e.g. a hand-built dict)
        levels = list(FULL_LADDER) if model.get("reasoning", True) else []
    row = {
        "id": model["id"],
        "reasoning": bool(levels),
        "effort_levels": levels,
        "can_disable": bool(model.get("can_disable")),
    }
    # Image input is a TRI-state and must not be coerced like can_disable above: the key is
    # emitted ONLY when the answer is actually known, so ABSENT means "no claim" on the wire.
    # Sending `false` for an unknown model would make the UI refuse an attachment on a model
    # that may well accept it — the one failure mode this whole feature exists to avoid.
    if isinstance(model.get("vision"), bool):
        row["vision"] = model["vision"]
    row.update(extra)
    return row


def _preferred_ref_set() -> set[str]:
    """The user's preferred-model refs (PreferredModelsStore), plus each ref's bare
    model tail — so a picker row matches whether or not it carries a ``provider_key``
    (single-provider rows have none). Best-effort: never fails the picker (an unreadable
    shortlist just means "no preferred group")."""
    try:
        from backend.db.preferred_models_store import PreferredModelsStore

        allowlist = PreferredModelsStore().resolved().get("models") or []
    except Exception:  # noqa: BLE001 — the picker degrades to "no preferred", never 500s
        return set()
    refs: set[str] = set()
    for ref in allowlist:
        ref = (ref or "").strip()
        if not ref:
            continue
        refs.add(ref)
        if ":" in ref:
            refs.add(ref.split(":", 1)[1])
    return refs


def _mark_preferred(rows: list[dict], refs: set[str]) -> list[dict]:
    """Stamp ``preferred`` on each row by shortlist membership (in place). No-op when
    the shortlist is empty (every row → preferred=False)."""
    for r in rows:
        pk = r.get("provider_key")
        full = f"{pk}:{r['id']}" if pk else r["id"]
        r["preferred"] = bool(refs) and (full in refs or r["id"] in refs)
    return rows


# ── OpenRouter public catalog cross-reference (for DIRECT OpenAI/xAI capability) ──
# OpenRouter's public /models (no auth) carries per-model reasoning.supported_efforts
# for the SAME models reachable directly (x-ai/…, openai/…). We use it as the
# authoritative effort source for a direct OpenAI/xAI model, falling back to the
# curated table when the model isn't listed. Deliberately NOT used for Anthropic
# (OpenRouter's Claude effort mapping differs from the native API). Fetched at most
# once per _OR_CATALOG_TTL and only when a direct OpenAI/xAI list is being priced.
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OR_CATALOG_TTL = 6 * 60 * 60
_OR_CATALOG_EMPTY_TTL = 5 * 60  # a failed/empty fetch is retried sooner, not per-model
_or_catalog_lock = threading.Lock()
_or_catalog_cache: dict = {"fetched_at": 0.0, "map": {}}


def _openrouter_effort_catalog() -> dict:
    """``{slug(lower): [efforts]}`` from OpenRouter's public catalog. Best-effort:
    any failure caches + returns ``{}`` so callers fall back to the curated table.

    Per slug: a ``reasoning`` object with a ``supported_efforts`` list → that list; with
    null/missing efforts → the full ladder (all accepted); no reasoning object → ``[]``
    (a known non-reasoning model). A slug absent from the map → not found (curated).
    """
    from backend.ai.models.capabilities import FULL_LADDER

    now = time.time()
    with _or_catalog_lock:
        fetched_at = _or_catalog_cache["fetched_at"]
        if fetched_at > 0:
            # A successful (non-empty) fetch is trusted for the full TTL; a failed/empty
            # one only briefly, so a transient blip doesn't pin us on curated for 6h —
            # but we NEVER re-fetch per model (the storm this guard prevents).
            ttl = _OR_CATALOG_TTL if _or_catalog_cache["map"] else _OR_CATALOG_EMPTY_TTL
            if (now - fetched_at) < ttl:
                return _or_catalog_cache["map"]
    mapping: dict = {}
    try:
        resp = httpx.get(_OPENROUTER_MODELS_URL, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        for m in resp.json().get("data") or []:
            if not (isinstance(m, dict) and m.get("id")):
                continue
            rz = m.get("reasoning")
            if isinstance(rz, dict):
                efforts = rz.get("supported_efforts")
                mapping[m["id"].lower()] = (
                    list(efforts) if isinstance(efforts, list) and efforts else list(FULL_LADDER)
                )
            else:
                mapping[m["id"].lower()] = []
    except Exception as exc:  # noqa: BLE001 — never break the direct-provider fetch
        logger.info("OpenRouter capability catalog unavailable (%s); using curated fallback", exc)
    with _or_catalog_lock:
        _or_catalog_cache["fetched_at"] = now
        _or_catalog_cache["map"] = mapping
    return mapping


def _cross_ref_efforts(base_url: str, model_id: str) -> Optional[list]:
    """OpenRouter-catalog efforts for a DIRECT OpenAI/xAI model, or None (not
    applicable / not found → curated fallback). Empty list = known non-reasoning."""
    base = (base_url or "").lower()
    if "api.x.ai" in base:
        slug = f"x-ai/{model_id}"
    elif "api.openai.com" in base:
        slug = f"openai/{model_id}"
    else:
        return None  # OpenRouter (inline meta), Anthropic (blanket), local (full ladder)
    return _openrouter_effort_catalog().get(slug.lower())


def _cache_key(provider: str, base_url: str) -> str:
    """A cache identity that invalidates when the provider or endpoint changes."""
    return f"{provider}|{base_url}"


# The cache file holds EVERY provider's list at once, keyed by _cache_key:
#   {"schema": N, "providers": {"<key>": {"fetched_at": ts, "models": [...]}, ...}}
# (The pre-v5 single-slot shape `{"key", "fetched_at", "models"}` held ONE provider, so
# the ?all=1 union — which reads/writes per provider — clobbered its own cache and hit
# the network for every provider on every call. Multi-key fixes that.) Writes serialize
# on _cache_write_lock so the concurrent union fetch can't lose a provider's entry in a
# read-modify-write race.
_cache_write_lock = threading.Lock()


def _load_cache_providers() -> dict:
    """The schema-checked {key: {fetched_at, models}} map from disk, or {} on any
    miss (missing/corrupt file, or a stale schema — including a pre-v5 single-slot file)."""
    try:
        raw = json.loads(MODELS_CACHE_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema") != _CACHE_SCHEMA:
        return {}
    providers = raw.get("providers")
    return providers if isinstance(providers, dict) else {}


def _read_cache(key: str) -> list[dict] | None:
    """Return this provider's cached [{id, reasoning, ...}] if present + fresh, else None.
    A pre-reasoning/bare-id shape is treated as a miss so the next fetch re-populates."""
    entry = _load_cache_providers().get(key)
    if not isinstance(entry, dict):
        return None
    if (time.time() - float(entry.get("fetched_at", 0))) > _CACHE_TTL_SECONDS:
        return None
    models = entry.get("models")
    if not (isinstance(models, list) and models):
        return None
    if not all(isinstance(m, dict) and "id" in m for m in models):
        return None
    return models


def _write_cache(key: str, models: list[dict]) -> None:
    """Persist ONE provider's list into the shared multi-key file, preserving the other
    providers' entries (read-modify-write under a lock). Best-effort — never raises."""
    with _cache_write_lock:
        providers = _load_cache_providers()
        providers[key] = {"fetched_at": time.time(), "models": models}
        try:
            atomic_write_text(
                MODELS_CACHE_JSON,
                json.dumps({"schema": _CACHE_SCHEMA, "providers": providers}),
            )
        except OSError:
            logger.debug("could not write models cache", exc_info=True)


def _bust_models_cache() -> None:
    """Remove the models cache file so the next GET /api/models?all=1 re-fetches live.

    Used when the enabled-set changes (toggle on/off) so the model picker reflects
    the new state within seconds instead of waiting up to 6 hours for TTL expiry.
    Best-effort — a failure is logged but never raises.
    """
    try:
        if MODELS_CACHE_JSON.exists():
            MODELS_CACHE_JSON.unlink(missing_ok=True)
    except OSError:
        logger.debug("could not bust models cache", exc_info=True)


def _fallback_models(transport: str, configured_model: str, base_url: str = "") -> list[dict]:
    """The static floor as [{id, reasoning, effort_levels}], for when a live fetch
    is impossible. The configured model is folded in so the picker always lists
    what's in use. Levels derive without OpenRouter metadata (None) — see capabilities.
    """
    if transport in _CLI_TRANSPORTS:
        ids = list(_CLI_TRANSPORTS[transport]["models"]())
    else:
        ids = list(_ANTHROPIC_FALLBACK if transport == "anthropic" else _OPENAI_FALLBACK)
    if configured_model and configured_model not in ids:
        ids.insert(0, configured_model)
    return _annotate_ids(ids, transport, base_url)


def _fetch_models_for_provider(
    provider_key: str, store: ModelConfigStore
) -> tuple[list[dict], str]:
    """Fetch (or fallback) models for one provider key.

    Returns ``(models, source)`` where each model is ``{id, reasoning}`` and source
    is ``"live"`` or ``"fallback"``. Never raises.
    """
    from backend.db.model_config_store import _provider_entry

    entry = _provider_entry(provider_key)
    if entry is None:
        return [], "fallback"
    transport: str = entry["transport"]
    creds = store.resolve_for_provider(provider_key) or {}
    api_key = (creds.get("api_key") or "").strip()
    base_url = (creds.get("base_url") or entry["base_url"] or "").strip()

    cache_key = _cache_key(provider_key, base_url)
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached, "live"

    try:
        if transport in _CLI_TRANSPORTS:
            # No model-list endpoint exists for a CLI; the curated list IS the answer,
            # and it is only honest to call it "live" once the binary actually probes OK.
            status, ids, _err = _probe_cli(transport, base_url)
            if status != "valid":
                raise RuntimeError(_err or f"{_CLI_TRANSPORTS[transport]['name']} unavailable")
            models = _annotate_ids(ids, transport, base_url)
        elif transport == "anthropic":
            if not api_key:
                raise RuntimeError("no Anthropic API key configured")
            models = _fetch_anthropic_models(api_key)
        else:  # openai_compatible
            models = _fetch_openai_models(base_url, api_key)
        if not models:
            raise RuntimeError("provider returned no models")
        _write_cache(cache_key, models)
        return models, "live"
    except Exception as exc:
        logger.info(
            "live model list unavailable for %s (%s); using fallback", provider_key, _exc_label(exc)
        )
        if transport in _CLI_TRANSPORTS:
            # For a CLI there is no "known model set we merely can't reach right now" —
            # a failed probe means the binary is missing or too old, so EVERY curated id
            # is unrunnable. Offering them would put models in the picker that error on
            # the first turn, so this provider contributes nothing and its group vanishes.
            return [], "fallback"
        return _fallback_models(transport, "", base_url), "fallback"


def _resolved_or_unset(store: ModelConfigStore) -> dict:
    """``store.resolved()``, or a best-effort stand-in when NO default model is picked yet.

    ``resolved()`` RAISES on an unset default by design — it must never invent a vendor for a
    caller that is about to send a request somewhere. This route is the opposite case: it is
    the picker you use to CHOOSE that default, and its own contract is "NEVER raises — the
    picker always has a usable, non-empty list". Letting the raise through deadlocks setup:
    you add a provider, the picker 500s because no model is chosen, so you can never choose
    one. (Real report, 2026-08-23: Claude Code CLI showing "Ready" with an empty model list.)

    A provider IS usually configured at that point even though the default model is not, so
    prefer its transport over a blank one — otherwise a Claude Code CLI instance would be
    offered the OpenAI floor. Falls back to a bare unset shape when nothing is configured
    either, which lands on the same static floor the ``all=1`` branch already uses.
    """
    try:
        return store.resolved()
    except ConfigurationError:
        pass
    try:
        keys = store.configured_provider_keys()
    except Exception:  # noqa: BLE001 — the picker must render whatever else is broken
        keys = []
    for key in keys:
        entry = _provider_entry_safe(key)
        if entry:
            return {
                "provider": entry.get("transport") or key,
                "base_url": entry.get("base_url") or "",
                "model": "",
            }
    return {"provider": "", "base_url": "", "model": ""}


def _provider_entry_safe(key: str) -> dict | None:
    from backend.db.model_config_store import _provider_entry

    try:
        return _provider_entry(key)
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/models")
def get_models(all: bool = Query(default=False, alias="all")):  # noqa: A002
    """The available model ids for the AI provider(s), fetched live (cached briefly).

    Default (``?all=0``): returns the ACTIVE provider's model list.
    ``{models: [{id}], source}`` where ``source`` is ``"live"`` or ``"fallback"``.

    With ``?all=1``: returns the UNION across ALL configured providers.
    ``{models: [{id, provider_key, provider_label}], source}`` — each entry is
    labelled so the frontend can render grouped option groups by provider.
    The source is ``"live"`` only when ALL providers returned live data; ``"fallback"``
    if any provider had to use its static floor.

    NEVER raises — the picker always has a usable, non-empty list.
    """
    store = ModelConfigStore()
    resolved = _resolved_or_unset(store)
    provider = resolved["provider"]
    base_url = (resolved.get("base_url") or "").strip()
    configured_model = (resolved.get("model") or "").strip()
    pref_refs = _preferred_ref_set()

    def _resp(rows: list[dict], source: str) -> dict:
        return {"models": _mark_preferred(rows, pref_refs), "source": source}

    if not all:
        # Single-provider path — use the provider key for cache identity so this
        # entry is shared with the multi-provider union path (same actual provider).
        from backend.db.model_config_store import _infer_provider_key

        provider_key_single = _infer_provider_key(provider, base_url) or provider
        key = _cache_key(provider_key_single, base_url)
        cached = _read_cache(key)
        if cached is not None:
            return _resp([_model_row(m) for m in cached], "live")

        try:
            if provider in _CLI_TRANSPORTS:
                # A CLI has no /models endpoint; a clean version probe IS the live answer.
                # Without this branch a CLI instance-default fell through to the
                # openai_compatible fetch against a blank base_url and always "failed".
                status, ids, err = _probe_cli(provider, base_url)
                if status != "valid":
                    raise RuntimeError(err or f"{_CLI_TRANSPORTS[provider]['name']} unavailable")
                models = _annotate_ids(ids, provider, base_url)
            elif provider == "anthropic":
                api_key = _anthropic_key(resolved)
                if not api_key:
                    raise RuntimeError("no Anthropic API key configured")
                models = _fetch_anthropic_models(api_key)
            else:  # openai_compatible
                models = _fetch_openai_models(base_url, (resolved.get("api_key") or "").strip())
            if not models:
                raise RuntimeError("provider returned no models")
            _write_cache(key, models)
            return _resp([_model_row(m) for m in models], "live")
        except Exception as exc:  # never raise — fall back to the static floor
            logger.info("live model list unavailable (%s); using fallback", _exc_label(exc))
            models = _fallback_models(provider, configured_model, base_url)
            return _resp([_model_row(m) for m in models], "fallback")

    # ── Multi-provider union path (all=1) ─────────────────────────────────────
    provider_keys = store.configured_provider_keys()
    if not provider_keys:
        # Nothing configured — fall back to the active provider's fallback floor.
        models = _fallback_models(provider, configured_model, base_url)
        return _resp(
            [_model_row(m, provider_key=None, provider_label=None) for m in models],
            "fallback",
        )

    from backend.db.model_config_store import _provider_entry

    def _fetch_one(key: str) -> tuple[str, str, list[dict], str]:
        entry = _provider_entry(key)
        label = entry["label"] if entry else key
        models, src = _fetch_models_for_provider(key, store)
        return key, label, models, src

    # Fetch every provider CONCURRENTLY — a cold union used to pay the sum of all
    # providers' latencies sequentially (each up to _HTTP_TIMEOUT); now it pays the
    # slowest one. ex.map preserves provider_keys order, so the grouped list is stable.
    with ThreadPoolExecutor(max_workers=min(8, len(provider_keys))) as pool:
        fetched = list(pool.map(_fetch_one, provider_keys))

    all_models: list[dict] = []
    any_fallback = False
    seen: set[str] = set()  # dedup model ids within a provider (across pages)
    for key, label, models, src in fetched:
        if src == "fallback":
            any_fallback = True
        for m in models:
            dedup_key = f"{key}:{m['id']}"
            if dedup_key not in seen:
                seen.add(dedup_key)
                all_models.append(_model_row(m, provider_key=key, provider_label=label))

    return _resp(all_models, "fallback" if any_fallback else "live")
