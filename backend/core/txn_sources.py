"""Transaction provenance constants — the single source of truth for ``source`` strings and
external-id namespaces. The dedup engine's invariants, the write choke, and every ingest
surface key off these sets; never inline-duplicate them (audit findings F08/F23/F24).

Layer: ``core`` leaf — stdlib only.
"""

from __future__ import annotations

# ── The writer vocabulary (RFC 2026-08-02: one fact, one field) ────────────────────
#
# ``source`` answers exactly ONE question: WHAT KIND OF WRITER put this row in the ledger.
# It does NOT say what format arrived, which provider, or which channel — those are already
# recorded, correctly, by ``external_refs[].source`` and ``batch_id``. Every historical
# mess in this file came from a second, drifting copy of a fact stored properly elsewhere:
# ``csv_import`` stamped on xlsx, ``email`` dead as a writer since 2026-06-01, ``pdf_import``
# never registered at all (12 live rows silently skipped the review queue).
#
# CLOSED, and it stays closed. A new bank/broker provider does NOT get a value here — it is
# ``feed``, and *which* feed lives in ``external_refs``. That is what makes a user-installed
# integration work on day one with no core change (ADR 2026-07-09-extensibility-substrate),
# instead of being silently rewritten to "api" and rendered as "You".
WRITERS = frozenset(
    {
        "manual",  # the owner, by hand (any surface — the channel is the audit log's `surface`)
        "ai",  # Lucius, because the owner asked (attended)
        "agent",  # an agent, autonomously (unattended)
        "automation",  # a schedule or rule fired it
        "import",  # a file the owner supplied (csv/xlsx/qif/pdf/ofx) — format: external_refs
        "api",  # arrived over HTTP, writer unidentified — the honest "unknown"
        "feed",  # an external provider supplied it (which one: external_refs)
    }
)

# Retired writer values → their replacement. Read-side ONLY: a stored row, a backup, an
# in-flight payload or a third-party caller may still carry any of these, and a source string
# is PERSISTED DATA — it must keep parsing forever. Nothing new is ever written with a key
# from this map.
_LEGACY_WRITER_ALIASES = {
    # mechanism, not writer — the format is in external_refs, the batch in batch_id
    "csv_import": "import",
    "qif_import": "import",
    "pdf_import": "import",
    "ofx_import": "import",
    # channels, not writers — a person typed it; `surface` records where
    "telegram": "manual",
    "slack": "manual",
    "discord": "manual",
    "whatsapp": "manual",
    # the IMAP path is an unattended agent; the channel is external_refs[].source == "email"
    "email": "agent",
    "recurring": "automation",
    # every provider collapses; WHICH provider is external_refs[].source
    "simplefin": "feed",
    "gocardless": "feed",
    "plaid": "feed",
    "enable_banking": "feed",
    "pluggy": "feed",
    "yodlee": "feed",
    "ibkr": "feed",
    "lunch_money": "feed",
    "ynab": "feed",
    "bank": "feed",
}


def canonical_writer(source: str | None) -> str | None:
    """A stored ``source`` mapped onto the current vocabulary, or ``None`` when it is neither a
    known writer nor a retired alias (an UNKNOWN writer — treated as least-trusted, never as a
    hand entry). Idempotent: a value already in ``WRITERS`` is returned unchanged."""
    s = (source or "").strip().lower()
    if not s:
        return None
    if s in WRITERS:
        return s
    return _LEGACY_WRITER_ALIASES.get(s)


# A DELIBERATE writer is one where a HUMAN was at the keyboard: the owner typing, or the owner
# asking Lucius. The engine's invariant 4 (two deliberate rows are never flagged as duplicates
# of each other — a 9am and a 3pm coffee are two real charges) trusts this.
#
# NOT replaceable by "batch_id is None": an agent logging one row at 3am also has no
# batch id, and nobody was there to mean it. Batch-ness and attendance are different facts;
# ``_same_feed`` (invariant 3) already covers same-batch rows from the other direction.
DELIBERATE_SOURCES = frozenset({"manual", "ai"})

# Kept as the public name every caller already imports. It is now simply the writer set —
# there is no separate whitelist to forget to update.
KNOWN_SOURCES = WRITERS

# The ONE identity namespace for a statement file (RFC 2026-08-01). csv/qif/xlsx/pdf all mint
# through the same `importer.external_ref()`, so IDENTICAL row content in two of these formats
# produces the IDENTICAL ref string — collapsing them is precisely what turns an L0 miss into an
# L0 hit when the same statement is re-imported in a different export format. mt940/camt join
# for consistency (their refs can never string-equal an external_ref() output, so they buy no
# dedup — but leaving a residual two-element set would preserve the very axis conflation this
# retires). `ofx` deliberately stays its own namespace: a FITID is spec-mandated unique per
# account, so it is genuinely AUTHORITATIVE and collapsing it would downgrade it for nothing.
STATEMENT_NAMESPACE = "statement"
RETIRED_STATEMENT_NAMESPACES = frozenset({"csv", "qif", "xlsx", "mt940", "camt", "pdf"})

# Every namespace a statement FILE mints refs under. These are row identities (dedup
# fingerprints), not evidence — nothing is ever stored as a snapshot for them, so the drawer's
# "Source" panel must never list them as a connector entry.
FILE_IMPORT_NAMESPACES = RETIRED_STATEMENT_NAMESPACES | {STATEMENT_NAMESPACE, "ofx"}

# The ALLOWLIST: namespaces whose refs are native per-event identifiers. Everything else —
# including anything nobody has classified — is non-authoritative.
#
# This used to be the exclusion list above ("anything not named is authoritative"), and that
# default is backwards for the hazard it guards. The two error directions are NOT symmetric:
#
#   false negative  a real feed treated as non-authoritative -> F03 does not veto -> the pair
#                   is SURFACED for review. A human decides. Cost: noise.
#   false positive  a content hash treated as native -> F03 VETOES -> a second row is written
#                   with no human in the loop. Cost: a silent double-count in net worth.
#
# Under the exclusion default a stale retired namespace re-entering the book (a checkpoint
# revert, a restored backup, an old export re-imported, an unmigrated merge journal) is
# AUTHORITATIVE and therefore dangerous. Under the allowlist it is merely inert.
AUTHORITATIVE_ID_NAMESPACES = frozenset(
    {
        "email",  # RFC 822 Message-ID
        "ofx",  # FITID — spec-mandated unique per account (OFX 2.x §3.2.1)
        "simplefin",
        "gocardless",
        "plaid",
        "enable_banking",
        "pluggy",
        "yodlee",
        "ibkr",
        "lunch_money",  # Lunch Money's own numeric transaction id — globally unique
        "ynab",  # YNAB's own transaction id — unique within the budget
        "vest",  # equity vest event id
        "option-premium",
        "option-close",
        # The iOS tap-to-log shortcuts' idempotency keys (docs/shortcuts/ios-tap-to-log.md),
        # one namespace PER TRIGGER: "wallet-tap:<when>" under ``ios-wallet``,
        # "notif:<when>@<app>" under ``ios-notification`` — captured client-side at the
        # moment of the event, stable across a retry of it, distinct across two real ones.
        # Authoritative so two genuinely separate purchases with the same merchant/amount/day/
        # card — two vending-machine taps — are not refused as duplicates of each other. One
        # namespace per trigger because a tap and the bank's notification of the SAME purchase
        # are two reports of one event: under the shared ``ios-shortcut`` (still listed, for
        # the rows it already stamped) their distinct refs vetoed the pair before it was ever
        # compared, and both were booked (live 2026-10-15).
        "ios-shortcut",
        "ios-wallet",
        "ios-notification",
    }
)

# A notification ref names its sender: "notif:<when>@<app>". Two apps can announce one
# purchase (the card's bank and the merchant's app), so the APP is the emitter — the same
# role an email's sender address plays under ``email``.
_NOTIFICATION_REF_NAMESPACE = "ios-notification"
_NOTIFICATION_APP_SEPARATOR = "@"

# Before each trigger had its own source, both shortcuts sent ``ios-shortcut`` and the trigger
# lived only in the ref's prefix. Those rows are still in the book (and a shortcut not yet
# reinstalled keeps minting them), so the prefix IS their emitter: a tap and a notification
# are compared, two taps stay two events. Any other prefix stays unresolved.
_LEGACY_SHORTCUT_NAMESPACE = "ios-shortcut"
_LEGACY_SHORTCUT_TRIGGERS = frozenset({"wallet-tap", "notif"})


def is_authoritative_source(source: str | None) -> bool:
    """True when refs under this namespace are native per-event identifiers (not content
    hashes) — so two distinct refs from the same source ARE two distinct real events
    (invariant 1).

    Fails CLOSED: an unclassified namespace is non-authoritative. See
    ``AUTHORITATIVE_ID_NAMESPACES`` for why that direction is the safe one."""
    return bool(source) and source in AUTHORITATIVE_ID_NAMESPACES


def ext_ref_emitters(row: dict) -> dict[str, set[str | None]]:
    """Authoritative ``source -> {emitter}`` for a row, read from ``external_refs[]``.

    An emitter is WHO announced the event: an email's sender address, a feed's connection. A
    namespace is not one — ``email`` carries every sender the book has ever received, so two
    distinct msgids under it are two distinct EMAILS, which is only two distinct PAYMENTS when
    the same emitter sent both. See ``matching._same_source_event_veto``.

    An entry with no recorded emitter contributes ``None`` — unresolved, never a wildcard, so a
    caller that cannot establish two emitters differ must treat them as possibly the same."""
    by: dict[str, set[str | None]] = {}
    for entry in row.get("external_refs") or []:
        if not isinstance(entry, dict) or not entry.get("ref"):
            continue
        src = entry.get("source")
        if not is_authoritative_source(src):
            continue
        emitter = entry.get("emitter") or ref_emitter(src, str(entry["ref"]))
        by.setdefault(str(src), set()).add(str(emitter) if emitter else None)
    return by


def ref_emitter(source: str | None, ref: str | None) -> str | None:
    """The emitter a ref states about itself, or None when it states none.

    An ``ios-notification`` ref names its app: everything after the FIRST "@" — the
    timestamp before it never contains one, an app name might. Casefolded so one app is one
    emitter however iOS capitalises it. An older ref without the app stays unresolved — the
    veto's safe direction. A legacy ``ios-shortcut`` ref names its trigger (``wallet-tap`` /
    ``notif``) in its prefix."""
    if not ref:
        return None
    if source == _LEGACY_SHORTCUT_NAMESPACE:
        trigger, sep, _ = ref.partition(":")
        return trigger if sep and trigger in _LEGACY_SHORTCUT_TRIGGERS else None
    if source != _NOTIFICATION_REF_NAMESPACE:
        return None
    _, sep, app = ref.partition(_NOTIFICATION_APP_SEPARATOR)
    app = app.strip().casefold()
    return app if sep and app else None


def ext_ref_pairs(row: dict) -> set[tuple[str | None, str]]:
    """Every ``(source, ref)`` pair a row carries — the one ``external_refs[]`` list, read as a
    set. A pair is included only when its ref is non-empty. Shared by the dedup floor
    (``TransactionStore.by_external_ref``) and the match engine's identity invariant
    (``matching._full_ref_set``) so both read one definition of 'what refs does this row own'.
    Layer-safe: ``core`` leaf, so ``db`` can call it too."""
    pairs: set[tuple[str | None, str]] = set()
    for entry in row.get("external_refs") or []:
        if isinstance(entry, dict) and entry.get("ref"):
            pairs.add((entry.get("source"), entry["ref"]))
    return pairs


def primary_pair(row: dict) -> tuple[str | None, str | None]:
    """The row's creation-time provenance pair — ``external_refs[0]`` by the list-order contract
    (ADR 2026-07-21: the FIRST entry is the origin; later entries were absorbed by merges).
    ``(None, None)`` when the row carries no external provenance."""
    for entry in row.get("external_refs") or []:
        if isinstance(entry, dict) and entry.get("ref"):
            return (entry.get("source"), entry["ref"])
        break  # a malformed first entry never promotes a later absorbed pair to "primary"
    return (None, None)


def primary_source(row: dict) -> str | None:
    """The creation-time source namespace (``external_refs[0]["source"]``), or None."""
    return primary_pair(row)[0]


def primary_ref(row: dict) -> str | None:
    """The creation-time raw ref (``external_refs[0]["ref"]``), or None."""
    return primary_pair(row)[1]


# ── Display-source vocab (Accounts redesign — the register's Source icon) ─────────
# The stored internal ``source`` strings collapse onto this 8-value set so the register can
# render one of a known icon set. This is a DISPLAY projection only — it never changes the
# stored source (the dedup engine / write choke key off the raw set). "opening" is in the set
# but has NO stored counterpart: it is emitted only when the caller passes
# ``display_source(..., is_opening=True)`` — i.e. the row is the one an account's
# ``opening_txn_id`` points at. See ``ledger.opening_txn_ids``.
DISPLAY_SOURCES = frozenset(
    {"manual", "bank", "import", "ai", "agent", "automation", "reconcile", "opening"}
)

# internal source string → display vocab. Anything unmapped falls back to "manual"
# (a hand entry is the safe default for an unrecognized legacy/test source).
# Keyed by CANONICAL writer only — callers reach it through ``canonical_writer``, so the
# retired values (csv_import, telegram, plaid, …) resolve via ``_LEGACY_WRITER_ALIASES``
# rather than being duplicated here. One collapse map, not two.
_DISPLAY_BY_SOURCE = {
    "manual": "manual",  # "You"
    "ai": "ai",  # "Lucius"
    "agent": "agent",  # "Agent"
    "automation": "automation",  # "Automation"
    "import": "import",  # "File import"
    "api": "import",  # "File import" — something wrote this over HTTP
    "feed": "bank",  # "Bank feed"
    # Not writers — display-only vocab values that a caller may pass straight through.
    "reconcile": "reconcile",
}

# display vocab → the human "Added by" label shown in the provenance block.
_ADDED_BY = {
    "manual": "You",
    "bank": "Bank feed",
    "import": "File import",
    "ai": "Lucius",
    "agent": "Agent",
    "automation": "Automation",
    "reconcile": "System",
    "opening": "Starting Balance",
}

# Writers whose creates are TRUSTED — everything else lands in the review queue.
#
# Stated as the exception list, not the inclusion list, so the default FAILS SAFE: a writer
# nobody thought about gets reviewed rather than silently trusted. The old inclusion list did
# the opposite and that is exactly how ``pdf_import``'s 12 rows bypassed the queue that
# ``csv_import`` rows land in — a value absent from the list read as "no review needed".
#
# ``manual`` is the owner's own hand. ``feed`` is a bank/broker provider carrying authoritative
# data. Everything else — ai, agent, automation, api, and any unknown — gets a glance.
# An explicit ``needs_review`` on the create always wins over this.
TRUSTED_WRITERS = frozenset({"manual", "feed"})

# Retained for callers that import it. Derived, never hand-maintained.
REVIEW_ON_CREATE_SOURCES = WRITERS - TRUSTED_WRITERS


def display_source(source: str | None, *, kind: str | None = None, is_opening: bool = False) -> str:
    """Map a stored internal ``source`` onto the register's 8-value display vocab.

    ``kind`` lets an ``adjustment`` row that would otherwise read "manual" surface as
    "reconcile" (reconcile is how an adjustment normally enters the ledger), while a
    richer recorded source (ai/agent) on an adjustment is preserved.

    ``is_opening`` wins outright: an account's Starting Balance row stores no marker of its
    own (its identity is the account's ``opening_txn_id`` pointer), so the caller — which
    already knows the pointer set via ``ledger.opening_txn_ids()`` — supplies the bool.
    ``core`` stays a leaf: this function never reads an account."""
    if is_opening:
        return "opening"
    # ABSENT and UNRECOGNISED are different, and conflating them broke both directions:
    #   - absent (None/"") is a legacy or hand-built row — "manual" has always been its
    #     default, and it is what lets the adjustment branch below reach "reconcile".
    #   - PRESENT but unrecognised is a writer that identified itself and we don't know it.
    #     That must never render as "You": defaulting a plugin's rows to "manual" made them
    #     claim the owner hand-entered them, which is a false provenance claim.
    raw = (source or "").strip().lower()
    if not raw:
        disp = "manual"
    elif raw in _DISPLAY_BY_SOURCE:
        # Already a display-vocab value (e.g. "reconcile", which is not a WRITER and so has no
        # canonical form) — a caller may pass one straight through.
        disp = _DISPLAY_BY_SOURCE[raw]
    else:
        writer = canonical_writer(raw)
        disp = _DISPLAY_BY_SOURCE.get(writer, "import") if writer else "import"
    if kind == "adjustment" and disp == "manual":
        disp = "reconcile"
    return disp


def added_by_label(display_src: str) -> str:
    """The human 'Added by' label for a display-source vocab value."""
    return _ADDED_BY.get(display_src, "You")


def default_needs_review(source: str | None) -> bool:
    """Whether a CREATE from this source should land in the review queue by default.

    Fails SAFE for an UNRECOGNISED writer: it is reviewed, not trusted. The old membership test
    did the reverse — a value absent from the inclusion list read as "no review needed", which
    is how ``pdf_import`` (never registered) wrote 12 rows straight past the queue that
    ``csv_import`` rows land in, and how a third-party integration would be trusted MORE than
    the owner's own CSV import.

    An ABSENT source is NOT the same thing: a row with no source at all is a legacy or
    hand-built one, which has always defaulted to ``manual`` and is the owner's own. Treating
    it as unknown would flag every such create, so the later "mark for review" edit becomes a
    no-op and the flag event never fires."""
    raw = (source or "").strip().lower()
    if not raw:
        return False
    return canonical_writer(raw) not in TRUSTED_WRITERS
