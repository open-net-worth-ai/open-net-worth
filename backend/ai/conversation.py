"""Channel-neutral model selection and execution for an interactive conversation turn."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Sequence
from typing import TYPE_CHECKING, Optional

from backend.ai import assistant, pending_injection, skill_runtime
from backend.ai.image_payloads import validate_message_images
from backend.ai.prompts import build_system_prompt
from backend.core.errors import ProviderDisabledError
from backend.services.model_selection import canonical_model_ref, instance_default_model_ref

if TYPE_CHECKING:
    from backend.db.model_config_store import ModelConfigStore


def select_session_model(
    session_id: str, ref: str, *, store: ModelConfigStore | None = None
) -> Optional[str]:
    """Persist an explicit selection canonically; blank clears to the instance default."""

    if store is None:
        from backend.db.model_config_store import ModelConfigStore

        store = ModelConfigStore()
    canonical = canonical_model_ref(ref, store=store)
    if canonical:
        # A picker choice is a durable preference, not a speculative turn hint. Refuse a
        # provider that was disabled between rendering and selection instead of persisting
        # a conversation that can only fail.
        store.resolve_override(canonical)
    assistant.upsert_session_meta(session_id, model=canonical, fail_loud=True)
    return canonical or None


def session_model_ref(session_id: str, *, store: ModelConfigStore | None = None) -> Optional[str]:
    """Return this conversation's canonical model, migrating a legacy bare ref on read."""

    stored = assistant.session_model(session_id)
    if not stored:
        return None
    canonical = canonical_model_ref(stored, store=store, legacy_bare=True)
    if canonical != stored:
        assistant.upsert_session_meta(session_id, model=canonical)
    return canonical


def turn_model_ref(
    session_id: str,
    *,
    fallback_refs: Sequence[str] = (),
    store: ModelConfigStore | None = None,
) -> Optional[str]:
    """Snapshot the provider-pinned model ref that one turn will use.

    A conversation override wins, then caller-owned fallbacks (for example an
    Automation conversation's configured model), then the instance default. Returning
    the default canonically instead of ``None`` lets attachment capability checks and
    provider execution use the exact same immutable selection.
    """

    stored = session_model_ref(session_id, store=store)
    if stored:
        return stored
    for ref in fallback_refs:
        if (ref or "").strip():
            if store is None:
                from backend.db.model_config_store import ModelConfigStore

                store = ModelConfigStore()
            canonical = canonical_model_ref(ref, store=store)
            try:
                # Caller-owned fallbacks (Automation config/run metadata) are not an
                # explicit user preference. If their provider was disabled after the run,
                # skip safely to the instance default; explicit stored selections still
                # resolve later and fail loudly as before.
                store.resolve_override(canonical)
            except ProviderDisabledError:
                return instance_default_model_ref(store=store) or None
            return canonical
    return instance_default_model_ref(store=store) or None


async def stream_turn(
    session_id: str,
    new_messages: list[dict],
    *,
    model_ref: Optional[str] = None,
    effort: str = "medium",
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    direct_invocations: Sequence[skill_runtime.InvocationRequest] = (),
    recovered_injections: Sequence[pending_injection.QueuedMessage] = (),
    on_recovered_adopted: Optional[
        Callable[[Sequence[pending_injection.QueuedMessage]], None]
    ] = None,
) -> AsyncGenerator[dict, None]:
    """Resolve one immutable model snapshot, build its prompt, and run the turn.

    Web, Telegram, and every future interactive connector enter the provider through
    this function. Channel adapters remain responsible only for ingress normalization
    and rendering the canonical events it yields.
    """

    validate_message_images(new_messages)
    selected = canonical_model_ref(model_ref) if model_ref else turn_model_ref(session_id)
    model_context = assistant.resolve_model_context(selected)
    system = build_system_prompt(
        model=model_context.model,
        base_url=model_context.base_url,
    )
    source = assistant.run(
        session_id,
        new_messages,
        system,
        model=selected,
        effort=effort,
        surface=surface,
        sender_id=sender_id,
        turn_id=turn_id,
        model_context=model_context,
        direct_invocations=direct_invocations,
        recovered_injections=recovered_injections,
        on_recovered_adopted=on_recovered_adopted,
    )
    try:
        async for event in source:
            yield event
    finally:
        await source.aclose()


def system_prompt_for(model_ref: Optional[str]) -> str:
    """Lucius's prompt for the model ``model_ref`` names (a ``provider_key:model`` ref or a bare
    id), so its family guidance matches the model that will serve the turn. Resolves the ref
    WITHOUT building a provider; no ref, or one the store cannot place, is the instance
    default's prompt exactly as ``build_system_prompt()`` gives it.
    """

    if not model_ref:
        return build_system_prompt()
    from backend.db.model_config_store import ModelConfigStore

    resolved = ModelConfigStore().resolve_override(model_ref)
    if resolved is None:
        return build_system_prompt()
    return build_system_prompt(
        model=resolved.get("model") or "", base_url=resolved.get("base_url") or ""
    )


async def resume_pending(
    session_id: str,
    tool_use_id: str,
    decision: str,
    *,
    surface: str = "chat:web",
    sender_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Answer an approval card and run the rest of the turn on the conversation's own model.

    The resume twin of ``stream_turn``, entered by every interactive approval path (web,
    Telegram and other channels, voice). A conversation with a model of its own resumes on that
    model, with a prompt built for it — it used to fall back to the instance default for the
    turn's second half. One without resumes exactly as before: the default, and its prompt.
    """

    stored = session_model_ref(session_id)
    source = assistant.resolve_pending_tool_use(
        session_id,
        tool_use_id,
        decision,
        system_prompt_for(stored),
        model=stored,
        surface=surface,
        sender_id=sender_id,
        turn_id=turn_id,
    )
    try:
        async for event in source:
            yield event
    finally:
        await source.aclose()
