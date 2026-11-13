"""Does the active model accept image input? — the resolution ladder.

Motivation (owner, 2026-07-26): a chat model with no vision (GLM being the live
case) used to fail the whole turn on an attached image, and the only mitigation was
REACTIVE — ``openai_provider.stream_chat`` caught the provider's refusal, stripped
the image parts, and retried once with a ``[image omitted]`` placeholder. That fires
only when the error text happens to say image/vision/modal, costs a wasted round
trip plus the base64 upload every time, and leaves the model answering from a
placeholder about a file the user can see attached in their own transcript. It is
the same shape as LibreChat's known bug where switching from a vision model to a
text one bricks the conversation, and Open WebUI's own diagnosis of the root cause
is ours too: the provider APIs mostly don't publish capabilities and there was
nowhere for a user to state one.

So capability is resolved BEFORE the send, from four sources, most-trusted first:

  1. ``user``     — an explicit override the owner set in Settings. Always wins.
  2. ``learned``  — recorded the first time a provider actually refused image input
                    for this exact model. We sit on that error path already, so we
                    get for free what other tools have to ask the user for.
  3. ``declared`` — the provider's own ``architecture.input_modalities`` (OpenRouter
                    publishes it; recorded into the store when the model list is
                    fetched, so the runtime needs no live call).
  4. inferred     — a name-pattern heuristic (``capabilities.infer_vision_from_name``).

``None`` (nobody knows) is a real answer and means **let it through**: a wrong
False silently removes image input from a model that has it, which is worse than
one wasted round trip that teaches us the truth via (2). The reactive retry in
``openai_provider`` stays as the backstop for exactly this case.

Stored under ``vision`` in ``model_config.json``, keyed ``"<provider_key>:<model_id>"``
(the same compound form model overrides and ``task_models`` already use).

Lives in ``services/`` rather than ``ai/models/`` because THREE layers need it and the
dependency rule (api → ai → services → db → core) only allows that from here: the chat
path (api) decides before building an image block, the OpenAI-compatible provider (ai)
strips images before the wire call, and the statement parser (services) must refuse a
scanned PDF up front instead of failing mid-parse. The pure name heuristic it consults is
a core leaf (``core/vision_names.py``).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.core.vision_names import infer_vision_from_name

log = logging.getLogger(__name__)

# Provenance ratchet, least → most trusted. A recorded value never overwrites one
# from a MORE trusted source (mirrors instruments' classification_source ladder).
_SOURCE_RANK: dict[str, int] = {"inferred": 0, "declared": 1, "learned": 2, "user": 3}

# Keyed to what each provider's `stream_chat`/`complete` actually does. Both CLI providers
# now carry native images on both entry points: Claude uses stream-JSON image blocks;
# Codex exec uses `-i`, and Codex app-server uses `localImage` turn inputs. Keep the two
# sets separate because a future transport can still differ by entry point.
_STREAM_CHAT_INCAPABLE_TRANSPORTS: frozenset = frozenset()
_COMPLETE_INCAPABLE_TRANSPORTS: frozenset = frozenset()


def _transport_refuses_images(provider_key: str, *, via: str = "stream_chat") -> bool:
    """True when this provider's transport cannot carry an image for this ENTRY POINT.

    ``via`` is ``"stream_chat"`` (default — the live chat path) or ``"complete"`` (a
    single-shot call: statement vision, the chat_vision extractor). Never raises.
    """
    if not provider_key:
        return False
    incapable = (
        _COMPLETE_INCAPABLE_TRANSPORTS if via == "complete" else _STREAM_CHAT_INCAPABLE_TRANSPORTS
    )
    try:
        from backend.db.model_config_store import _provider_entry

        entry = _provider_entry(provider_key) or {}
        return (entry.get("transport") or "") in incapable
    except Exception:
        return False


def vision_key(provider_key: str, model_id: str) -> str:
    """The store key for one (provider, model) pair — ``"glm:glm-4.6"``."""
    return f"{(provider_key or '').strip().lower()}:{(model_id or '').strip()}"


def _split_ref(model_ref: str) -> "tuple[str, str]":
    """Split a ``"provider_key:model_id"`` ref; a bare model id yields ``("", id)``.

    Only the FIRST colon splits — a model id may itself contain one (OpenRouter's
    namespaced ids use ``/``, but a custom gateway alias can be anything).
    """
    ref = (model_ref or "").strip()
    if ":" not in ref:
        return "", ref
    provider, _, model = ref.partition(":")
    return provider.strip().lower(), model.strip()


def _stored(store, provider_key: str, model_id: str) -> Optional[dict]:
    """The stored capability row for this pair, if any. Never raises."""
    try:
        rows = store.vision_capabilities()
    except Exception:  # a corrupt/unreadable config must not break the turn
        return None
    row = rows.get(vision_key(provider_key, model_id))
    if isinstance(row, dict) and isinstance(row.get("supports"), bool):
        return row
    return None


def _resolve_provider_model(model_ref: str, store) -> "tuple[str, str]":
    """``(provider_key, model_id)`` for ``model_ref``, filling in whichever half a
    partial/empty ref left out from the instance default. Shared by ``supports_vision``
    and ``capability_source`` so the two can never resolve the same ref differently."""
    from backend.db.model_config_store import _infer_provider_key

    provider_key, model_id = _split_ref(model_ref)
    if not model_id or not provider_key:
        try:
            resolved = store.resolved()
            if not model_id:
                model_id = (resolved.get("model") or "").strip()
            if not provider_key:
                provider_key = (
                    resolved.get("provider_key")
                    or _infer_provider_key(
                        (resolved.get("provider") or "").strip().lower(),
                        (resolved.get("base_url") or "").strip(),
                    )
                    or ""
                )
        except Exception:
            pass
    return provider_key, model_id


def supports_vision(model_ref: str, *, store=None, via: str = "stream_chat") -> Optional[bool]:
    """Whether ``model_ref`` accepts image input: True / False / None (unknown).

    ``model_ref`` is a compound ``"provider_key:model_id"`` or a bare model id (which
    resolves against the instance-default provider, exactly like a model override).
    An EMPTY ref means "the instance default", which is resolved the same way.

    ``via`` picks which entry point is asking — ``"stream_chat"`` (default, the live chat
    path) or ``"complete"`` (statement vision, the chat_vision extractor) — because on the
    two CLI transports one can carry an image and the other structurally cannot; see
    ``_STREAM_CHAT_INCAPABLE_TRANSPORTS`` / ``_COMPLETE_INCAPABLE_TRANSPORTS`` above.

    Never raises — an unreadable config, an unknown provider, or a missing model all
    degrade to ``None`` (let it through), because refusing image input on the basis of
    a failed lookup is the one outcome that silently loses a working capability.

    The ONE exception to fail-open is a transport that structurally cannot carry an image
    via THIS entry point: there, ``False`` is a fact about the pipe, not a guess about the
    model, so it outranks the whole ladder — including a `user` override, which would
    otherwise let the owner pin a reader that can only ever fail.
    """
    from backend.db.model_config_store import ModelConfigStore

    store = store or ModelConfigStore()
    provider_key, model_id = _resolve_provider_model(model_ref, store)

    # Transport check FIRST — see _STREAM_CHAT_INCAPABLE_TRANSPORTS /
    # _COMPLETE_INCAPABLE_TRANSPORTS. Deliberately ahead of the stored row, so no override
    # can advertise a reader that the pipe cannot serve via this entry point.
    if _transport_refuses_images(provider_key, via=via):
        return False

    if not model_id:
        return None

    row = _stored(store, provider_key, model_id)
    if row is not None:
        return bool(row["supports"])
    return infer_vision_from_name(model_id)


def capability_source(model_ref: str, *, store=None, via: str = "stream_chat") -> str:
    """Which rung of ``supports_vision``'s ladder actually answered for ``model_ref`` —
    ``"user" | "learned" | "declared" | "inferred" | "transport" | "unknown"``.

    Diagnostics-only (RFC cross-channel-attachments item 32's ``capability_source``
    field). Mirrors ``supports_vision``'s own resolution order exactly — via the shared
    ``_resolve_provider_model`` helper — so the label this returns always names the rung
    that produced the value ``supports_vision`` returned for the SAME ref, never a
    reconstruction that can drift from it. Never raises; degrades to ``"unknown"``."""
    from backend.db.model_config_store import ModelConfigStore

    store = store or ModelConfigStore()
    try:
        provider_key, model_id = _resolve_provider_model(model_ref, store)
        if _transport_refuses_images(provider_key, via=via):
            return "transport"
        if not model_id:
            return "unknown"
        row = _stored(store, provider_key, model_id)
        if row is not None:
            source = str(row.get("source") or "")
            return source if source in _SOURCE_RANK else "unknown"
        return "inferred" if infer_vision_from_name(model_id) is not None else "unknown"
    except Exception:
        return "unknown"


def resolve_extractor_ref(*, task: str = "chat_vision", store=None) -> str:
    """Which model should READ an image, or ``""`` when nothing usable can.

    Two steps, no fan-out:
      1. the ``task`` pin, if the owner set one AND it can actually see;
      2. otherwise the INSTANCE DEFAULT model, unless we positively know it's blind too.

    ``task`` selects whose pin is consulted. ``chat_vision`` is a chat turn whose own model
    is blind. ``statement_import`` is a SCANNED statement page: since the five per-stage
    import knobs merged into one (2026-08-09), that single pin is usually a text model chosen
    for row extraction, and a scan must not be refused just because the pin can't see —
    "for the scanned pages, it should use the default image model of the instance as the
    fallback" (owner). Step 1 therefore drops a pin that is KNOWN blind rather than
    returning it, so step 2 can do its job.

    Step 2 covers the actual common case: the default is a vision model and the owner
    switched *this conversation* to a cheaper text-only one. It is OpenClaw's "active
    reply model when its provider supports the capability" step, adapted — for us the
    active reply model IS the blind one, so the instance default takes that slot.

    An UNKNOWN-capability default is allowed through: it may well work, a failure is
    caught and degrades to the honest message, and the attempt is what teaches us the
    truth. What is never allowed is routing to a model we KNOW is blind — that would
    spend money to ask a model to describe an image it cannot see.
    """
    from backend.db.model_config_store import ModelConfigStore, _infer_provider_key

    store = store or ModelConfigStore()
    try:
        pinned = (store.task_model_override(task) or "").strip()
    except Exception:
        pinned = ""
    # A pin we KNOW is blind is not an answer — fall through to the instance default rather
    # than spending money asking a text-only model to describe an image. An unknown-capability
    # pin is still honoured: it may well work, and the attempt is what teaches us the truth.
    if pinned and supports_vision(pinned, store=store, via="complete") is not False:
        return pinned

    try:
        resolved = store.resolved()
    except Exception:
        return ""  # no usable instance default (unconfigured provider) — nothing to route to
    model_id = (resolved.get("model") or "").strip()
    if not model_id:
        return ""
    provider_key = (
        resolved.get("provider_key")
        or _infer_provider_key(
            (resolved.get("provider") or "").strip().lower(),
            (resolved.get("base_url") or "").strip(),
        )
        or ""
    )
    ref = f"{provider_key}:{model_id}" if provider_key else model_id
    # An extractor only ever runs through `complete()` — never `stream_chat` — so its
    # capability check uses the `complete`-path transport set (see `supports_vision`).
    return "" if supports_vision(ref, store=store, via="complete") is False else ref


def record_vision_capability(model_ref: str, supports: bool, source: str, *, store=None) -> None:
    """Persist what we now know about one model's image-input support. Best-effort.

    Refuses to downgrade: a value from a LESS trusted source never overwrites a more
    trusted one (so a name-pattern guess or a stale ``declared`` row can't clobber the
    owner's explicit Settings answer, and a re-fetch of the model list can't undo what
    a real provider refusal taught us).
    """
    from backend.db.model_config_store import ModelConfigStore, _infer_provider_key

    if source not in _SOURCE_RANK:
        raise ValueError(f"unknown vision source {source!r}; must be one of {sorted(_SOURCE_RANK)}")

    store = store or ModelConfigStore()
    provider_key, model_id = _split_ref(model_ref)
    if not provider_key:
        try:
            resolved = store.resolved()
            provider_key = (
                resolved.get("provider_key")
                or _infer_provider_key(
                    (resolved.get("provider") or "").strip().lower(),
                    (resolved.get("base_url") or "").strip(),
                )
                or ""
            )
        except Exception:
            provider_key = ""
    if not model_id:
        return

    existing = _stored(store, provider_key, model_id)
    if existing is not None:
        prior = _SOURCE_RANK.get(str(existing.get("source") or ""), -1)
        if prior > _SOURCE_RANK[source]:
            return
        if prior == _SOURCE_RANK[source] and bool(existing["supports"]) == supports:
            return  # unchanged — skip the write entirely
    try:
        store.set_vision_capability(vision_key(provider_key, model_id), supports, source)
    except Exception as exc:  # never fail a turn over a capability note
        log.debug("record_vision_capability failed for %s: %s", model_ref, type(exc).__name__)
