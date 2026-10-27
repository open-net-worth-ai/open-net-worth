"""Typed, framework-free error hierarchy for the domain (RFC P1-A1 §3.4).

Services raise these instead of bare ``ValueError``/``RuntimeError`` or
``fastapi.HTTPException``; routers translate them to HTTP at the edge (Guide
§4.1), keeping services HTTP-agnostic. This module imports nothing internal and
nothing from FastAPI — ``to_http_status`` returns a plain ``int`` so ``core/``
stays leaf-pure.

Each error carries a stable machine ``code`` and a human-readable ``message``,
plus arbitrary ``context`` kwargs (kept on the instance for logging/handlers).
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable


class OpenNetWorthError(Exception):
    """Base for all domain errors. Carries a stable ``code`` + a public ``message``."""

    code = "error"

    def __init__(self, message: str = "", **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context


class NotFoundError(OpenNetWorthError):
    """A referenced entity does not exist."""

    code = "not_found"


class ValidationError(OpenNetWorthError):
    """Input failed a domain invariant or schema check."""

    code = "validation"


class ConflictError(OpenNetWorthError):
    """The request conflicts with current state (duplicate, race, etc.)."""

    code = "conflict"


class DuplicateTransactionError(ConflictError):
    """A create was REFUSED because the match engine identified an existing row as the same
    real-world event. Maps to HTTP 409.

    Refusal — not a silent flag — because "created-then-flagged" only protects a book whose
    review queue is actually read, and rows arrive here from paths with no reader at all
    (scheduled agents, connectors, the AI tool surface). The engine's own review CAP is
    deliberately NOT the bar: a cap means "never auto-MERGE unattended", which is a statement
    about a destructive irreversible edit, not about whether the rows are the same event.
    Refusing is neither destructive nor irreversible — ``allow_duplicate=True`` books the row anyway —
    so it is safe in exactly the cases where auto-merge is not (``TRANSFER_LEG_ECHO``, capped
    to 0.75 forever, is the motivating one).

    ``context`` carries the evidence the caller needs to DECIDE rather than merely retry:
    ``existing_id`` and an ``existing`` summary (so an agent can inspect the row it collided
    with without a second round trip), plus ``score``/``match_class``/``reasons``.
    """

    code = "duplicate_transaction"


class ImplausibleTransferAmountError(ConflictError):
    """A transfer's two legs carry the exact same NUMBER in different currencies while a
    KNOWN, non-1:1 exchange rate exists for the pair. Maps to HTTP 409.

    Narrower sibling of the rate-magnitude guard in ``services.ledger.
    _assert_transfer_rate_plausible``: that guard only refuses a rate off by 10x or more (the
    class it exists for), so a caller that has the right rate in hand — often sitting right in
    the row's own description — but never actually multiplies by it sails straight through
    whenever the real rate happens to be single-digit (almost every fiat pair). This catches
    that specific "amount carried across the currency boundary unconverted" shape.

    Refusal — not a silent flag — for the same reason ``DuplicateTransactionError`` refuses:
    the caller may have no reader for a review queue at all (a scheduled agent, in particular).
    Also not permanent: a transfer that genuinely cleared at parity (a stale quote, a peg, a
    fee-inclusive settlement) is real, so ``force_transfer_rate=True`` books it anyway rather
    than making the shape unrepresentable.
    """

    code = "implausible_transfer_amount"


class PreviouslyDeletedError(ConflictError):
    """A create was REFUSED because an external ref on the row belongs to a transaction the
    user DELETED — the same source event, re-presented. Maps to HTTP 409.

    Distinct from ``DuplicateTransactionError`` because the evidence is different: not "this
    resembles a live row" but "you already rejected exactly this". ``force``/``allow_duplicate``
    do not lift it — those mean "book it even if it looks like another row", and there is no
    other row. Only an explicit resurrection does (``resurrect=True`` at the choke, a ``create``
    decision at the tool), which also forgets the record.

    ``context`` carries ``rejection_id``, ``deleted_txn_id``, ``rejected_at``, ``source``, ``ref``.
    """

    code = "previously_deleted"


class ReviewApprovalRefusedError(ConflictError):
    """An Automation tried to CLEAR a transaction's review flag. Maps to HTTP 409.

    Approving a row is the owner's decision, made from the review queue — an unattended run
    that approves what it just wrote empties the queue before anyone has looked. Refused at the
    ledger update choke whatever the Automation's approval mode (Skip included), because it is
    not a tool-permission question. ``context`` carries ``txn_id``.
    """

    code = "review_approval_owner_only"


class MissingFilterError(ValidationError):
    """A bulk/destructive operation (e.g. a filter-based delete-where) was called with
    NO filters at all — refused rather than matching (and wiping) everything. Maps to
    HTTP 400, matching the literal-400 contract the call sites this guards already
    returned before the guard moved into the service choke point (CATF2 follow-up)."""

    code = "missing_filter"


class InvalidLotSelection(ValidationError):
    """A specific-ID disposal named a lot not in the position, or asked to take more
    than a lot's remaining quantity (P2-INV1 AC-7). Maps to HTTP 422 (validation)."""

    code = "validation"


class InsufficientLots(ValidationError):
    """A disposal asked to sell more units than the position's open lots hold
    (oversell; P2-INV1 AC-13). Maps to HTTP 422 (validation)."""

    code = "validation"


class ExternalServiceError(OpenNetWorthError):
    """An upstream dependency (FX, price feed, LLM) failed or was unreachable."""

    code = "upstream"


class ProviderTimeoutError(ExternalServiceError):
    """The model provider did not answer in time (a hard ceiling, or ``stalled`` — it went
    quiet mid-turn). The message names the provider and the setting that bounds it; the chat
    surface shows it as written."""

    code = "provider_timeout"

    def __init__(self, message: str = "", stalled: bool = False, **context: Any) -> None:
        super().__init__(message, stalled=stalled, **context)
        self.stalled = stalled


class MissingFxRateError(ExternalServiceError):
    """A conversion cannot be valued without a finite, positive exchange rate."""

    code = "missing_fx_rate"

    def __init__(self, currency: str, target: str) -> None:
        super().__init__(
            f"Exchange rate unavailable for {currency} → {target}. "
            "Refresh exchange rates and retry; no 1:1 conversion was assumed.",
            currency=currency,
            target=target,
        )


class UnsupportedModalityError(ExternalServiceError):
    """A provider refused a request because it does not accept a content modality
    (currently only ``"image"``).

    The typed replacement for ``openai_provider``'s old narrow string-match-and-strip
    reactive retry (RFC ``cross-channel-attachments-vision-and-statement-evidence``
    section 4). A provider adapter raises this ONLY for a narrowly recognized provider
    modality-refusal shape (AND the rejected request actually carried that modality) —
    every other error re-raises untouched. The RETRY decision belongs to shared turn
    orchestration (``ai/assistant.py``), never to the adapter that raises this: an
    adapter must not itself decide to invoke a different model, learn a capability, or
    rebuild the request. ``provider_ref`` is the ``"<provider_key>:<model_id>"`` that
    refused — the same compound ref ``services.vision_capability.record_vision_capability``
    keys its store by.
    """

    code = "unsupported_modality"

    def __init__(self, provider_ref: str, modality: str = "image", **context: Any) -> None:
        message = f"{provider_ref or 'the selected model'} does not accept {modality} input"
        super().__init__(message, provider_ref=provider_ref, modality=modality, **context)
        self.provider_ref = provider_ref
        self.modality = modality


class UnknownFeatureFlagError(NotFoundError):
    """A feature-flag name that is not declared in ``core.feature_flags.FLAGS``. Raised, never
    read as ``False`` — a typo would otherwise silently run the old path forever."""


class FeatureFlagLockedError(ConflictError):
    """A feature flag cannot be changed here (the public demo always runs the defaults)."""


class ConfigurationError(OpenNetWorthError):
    """A required setting/credential is missing or invalid."""

    code = "config"


class ProviderDisabledError(ConfigurationError):
    """The requested AI provider is disabled in Settings.

    Raised by ``resolve_for_provider`` and ``resolve_override`` when the
    caller references a catalog key whose ``enabled`` flag is ``False``.
    The ``provider_key`` context kwarg carries the key for error messages.
    """

    code = "provider_disabled"


class MentionError(OpenNetWorthError):
    """A mention descriptor registration or guard check failed.

    Raised by ``_collect.register()`` on duplicate types, secret field keys,
    or forbidden-store references.
    """

    code = "mention_error"


class DataIntegrityError(OpenNetWorthError):
    """A data file exists but is corrupt/unparseable, or the database fails its
    boot-time integrity check.

    Raised by a fail-closed file store's own read path (e.g. ``AuthStore._raise_corrupt``)
    instead of silently treating the corrupt file as empty — an "empty" read would make the
    next write persist the data loss permanently — and, via its ``MigrationError``
    subclass, by ``db.migrate.ensure_migrated()`` when the database is unmigrated or its
    pre-migrate backup fails verification at boot. The ``path`` context kwarg carries the
    file for logging/handlers.
    """

    code = "data_integrity"


class MigrationError(DataIntegrityError):
    """A schema migration failed (or could not be safely attempted). The boot
    wrapper restores the pre-migrate backup and the app REFUSES to start —
    a financial app on a half-migrated database is worse than one that is down."""


# Stable mapping from error code → HTTP status. Returns plain ints so this module
# imports no web framework. Routers (or a single app-level exception handler) use
# this to translate domain errors to responses.
_STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "validation": 422,
    "conflict": 409,
    "duplicate_transaction": 409,
    "implausible_transfer_amount": 409,
    "previously_deleted": 409,
    "upstream": 502,
    "provider_timeout": 504,
    "missing_fx_rate": 503,
    "unsupported_modality": 502,
    "config": 500,
    "provider_disabled": 409,
    "mention_error": 422,
    "data_integrity": 500,
    "missing_filter": 400,
    "error": 500,
}


def to_http_status(exc: OpenNetWorthError) -> int:
    """HTTP status for a domain error, defaulting to 500 for unknown codes."""
    return _STATUS_BY_CODE.get(getattr(exc, "code", "error"), 500)


# --- Prescriptive error messages (S2) -------------------------------------------------
# An error the model can't act on costs a whole round-trip. These build the RECOVERY
# PATH into the message itself — the closest existing values plus the call that lists
# them — so a bad reference is self-correctable instead of just refused. Stdlib only
# (``difflib``), so ``core`` stays a leaf: callers pass the candidates they already
# hold, and only ever on the ERROR path (never a lookup on the happy path).

_MAX_LISTED_CANDIDATES = 12


def did_you_mean(value: str, candidates: Iterable[str], *, limit: int = 3) -> list[str]:
    """The ``limit`` closest candidates to ``value`` by string distance.

    Case-insensitive and cutoff-free at the low end (a wrong-but-close guess should
    still get suggestions), capped at ``limit`` so the message stays short.
    """
    pool = [c for c in candidates if c]
    if not value or not pool:
        return []
    lowered = {c.lower(): c for c in reversed(pool)}
    matches = difflib.get_close_matches(value.lower(), list(lowered), n=limit, cutoff=0.4)
    return [lowered[m] for m in matches]


def recovery_hint(
    value: str,
    candidates: Iterable[str],
    *,
    tool_hint: str,
    limit: int = 3,
) -> str:
    """The recovery half of an "unknown reference" error, as a sentence to append to the
    caller's own head sentence (call sites keep their existing wording; only the fix is
    added). Leads with a space, so ``f"{head}." + recovery_hint(...)`` reads correctly.

    Names the closest existing values, falling back to listing them outright when nothing
    is close enough, and to the bare tool hint when there are none at all (empty book).
    """
    pool = [c for c in candidates if c]
    tail = f" Call {tool_hint} for the full list."
    close = did_you_mean(value, pool, limit=limit)
    if close:
        return f" Closest existing: {', '.join(repr(c) for c in close)}.{tail}"
    if pool:
        shown = pool[:_MAX_LISTED_CANDIDATES]
        more = "" if len(pool) <= _MAX_LISTED_CANDIDATES else f" (+{len(pool) - len(shown)} more)"
        return f" Existing: {', '.join(repr(c) for c in shown)}{more}.{tail}"
    return f" None exist yet — call {tool_hint} to check."


def duplicate_hint(
    existing_id: str,
    *,
    facts: Iterable[str] = (),
    force_hint: str = "allow_duplicate=true",
    tool_hint: str = "get_transactions",
    attach_hint: str = "",
) -> str:
    """The recovery half of a DUPLICATE refusal — same appendable contract as
    ``recovery_hint`` (leads with a space, appended to the caller's own head sentence).

    A refusal is only useful if the caller can act on it WITHOUT another round trip, so this
    names the row that was collided with, describes it (``facts`` — date/amount/description/
    account, pre-formatted by the caller because this module is a stdlib-only leaf and cannot
    format money), and states the exits: book it anyway, or go read the row. An agent that
    genuinely meant to record a second identical charge needs the first exit; one that
    double-booked needs the second.

    ``attach_hint`` (ADR 2026-09-07) is a THIRD exit, pre-formatted by the caller (this module
    stays a stdlib-only leaf and never names a tool or an external-ref pair itself) and
    appended as its own sentence only when non-empty — the caller builds it ONLY when the
    incoming row it refused actually carries external refs to link.
    """
    described = f" ({'; '.join(f for f in facts if f)})" if any(facts) else ""
    tail = f" {attach_hint}" if attach_hint else ""
    return (
        f" It matches existing transaction {existing_id!r}{described}."
        f" If this is genuinely a separate charge, retry with {force_hint};"
        f" otherwise call {tool_hint} to inspect it — do NOT retry unchanged.{tail}"
    )
