"""The canonical statement contract — every tier emits these; downstream never knows the tier.

Pure dataclasses, NO logic (RFC statement-import-unified D2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class CanonicalRow:
    """One parsed statement transaction, tier-agnostic.

    ``date`` is the TRANSACTION date (what the ledger books on); ``posted_date`` rides along
    when the statement shows both. ``description`` is the RAW narration verbatim (the
    cross-source dedup signal — never lost); ``payee`` is the clean merchant name an LLM tier
    extracted (None on deterministic tiers). ``balance`` is the running-balance cell when the
    source has one — consumed by the guards, never stored on a transaction.
    """

    date: str
    direction: str  # "IN" | "OUT"
    amount: float  # positive magnitude, statement currency
    currency: str = ""  # "" → the importing account's native currency
    description: str = ""
    payee: str | None = None
    posted_date: str | None = None
    original_amount: float | None = None  # pre-conversion foreign charge (display/audit)
    original_currency: str | None = None
    reference: str | None = None  # bank ref → strongest same-source dedup key
    balance: float | None = None  # running balance after this row (guards only)
    status: str | None = None  # raw status cell (e.g. "Thất bại")
    skip: bool = False
    skip_reason: str | None = None
    account_hint: str | None = None  # multi-account statements: section attribution
    category_hint: str | None = None  # LLM suggestion — preview-only, NEVER auto-applied
    # The 1-based row of the file this came from (a CSV, or an Excel file's FIRST sheet) — the
    # review highlights it in the file shown beside the rows. None where no row maps 1:1.
    source_row: int | None = None
    # Format-NATIVE dedup id (OFX FITID, CAMT AcctSvcrRef…), minted by the deterministic
    # adapters (statements/structured.py) so decision-flow drafts keep the EXACT id shape the
    # per-format importers always used — a re-import of a file booked via either path still
    # hard-dedups. One ``[{source, ref}]`` list (ADR 2026-07-21): ``source`` = the format
    # namespace, ``ref`` = the raw ref (everything the old ``<fmt>:`` prefix wrapped).
    # Empty → the standard content-hash ref is fabricated at draft time.
    external_refs: list[dict] = field(default_factory=list)
    # The source file's OWN category reference (QIF `L` field, a mapped CSV category column) —
    # a raw label/id resolved against the live category store at DRAFT time (unresolvable →
    # dropped, never invented). Distinct from category_hint: this is the file's declared
    # fact, applied; the hint is an LLM suggestion, preview-only.
    category_ref: str | None = None
    confidence: float = 1.0  # deterministic tiers = 1.0; < 0.6 → "needs your eyes"
    notes: list[str] = field(default_factory=list)  # guard annotations
    # EVERY non-blank column of the source row, keyed by its own header label — independent
    # of whichever named roles the schema mapped (owner, 2026-07-28: "why the fuck can't the
    # parser parse all columns instead of leaving out some"). The schema's fixed vocabulary
    # (desc/payee/status/id/balance/category/debit/credit) can only assign ONE column to
    # each role, so a richer source (e.g. separate from/to account NUMBER columns alongside
    # their display-name columns) loses everything past what fits those roles — this is the
    # passthrough that keeps the rest reachable (Lucius's pass reads it) without inventing a
    # new named role per source. Empty on non-tabular tiers (no grid to key by).
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StatementMeta:
    """Statement-level header metadata — read once, used twice (guards + the statement record).

    ``sign_convention``: "bank" (debit column = money out of an asset account) or "card"
    (charges positive, payments CR) — drives the reconciliation identity. ``extra`` is the open
    bag: whatever else this statement prints (loyalty tier, promo APR, FX fees…) survives
    without a schema migration.
    """

    currency: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    statement_date: str | None = None
    payment_due_date: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    amount_due: float | None = None
    minimum_payment: float | None = None
    credit_limit: float | None = None
    interest: float | None = None
    fees: float | None = None
    rewards: dict | None = None  # {earned, redeemed, balance, expiry, unit}
    sign_convention: str | None = None  # "bank" | "card"
    account_last4s: list[str] = field(default_factory=list)
    issuer: str | None = None
    extra: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # True = the metadata read FAILED. Every null field above is then UNKNOWN, not absent —
    # the distinction the rest of the app cannot otherwise make, because a statement that
    # genuinely prints no period and one whose header we never managed to read cache as the
    # SAME all-null record. Reported bug (2026-07-16 → surfaced 2026-08-01): a Meridian Bank
    # eStatement prints "Statement Date July 05, 2026" 1,660 chars in — comfortably inside the
    # metadata slice — but the extraction call hit a transient provider error (`529 Overloaded`,
    # see app.log 2026-07-31), the exception was swallowed into an empty meta, and that empty
    # meta was cached as fact. Downstream, `_window` fell through to its rows-derived fallback,
    # so the ledger-check window itself was wrong, `_window_is_self_referential` was true, and
    # the near-cut-off chip never fired; `_suggest_account` got no printed digits and the record
    # bound to no account at all. A FLAGGED failure is repairable (`_heal_cached_meta`); a
    # silent one is forever.
    meta_unavailable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParseResult:
    """What ``parse_statement`` returns: rows + meta + how it got them."""

    meta: StatementMeta
    rows: list[CanonicalRow]
    tier: str  # "tabular-schema" | "tabular-profile" | "text" | "vision"
    llm_calls: int = 0
    reconcile: dict | None = None  # guards verdict: {status, expected, actual, delta, currency}
    schema: dict | None = None  # tabular tiers: the applied column schema (profile learning)

    def to_dict(self) -> dict:
        return {
            "meta": self.meta.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
            "tier": self.tier,
            "llm_calls": self.llm_calls,
            "reconcile": self.reconcile,
            "schema": self.schema,
        }
