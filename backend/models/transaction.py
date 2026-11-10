"""Transaction schemas — the two-leg money-movement ledger.

Mirrors ``db/transaction_store.py`` (``make_transaction``, ``KINDS``) and the inline
``TransactionCreate``/``TransactionUpdate``/``BulkTransactionImport`` in
``api/ledger.py`` that the ``*Create``/``*Update``/``Bulk`` schemas here replace
in wave 2.

Amount/currency validators mirror the store's ``_amt`` (abs magnitude) and ``_cur``
(.upper()). The store stays the runtime authority in wave 1.

Leg rules by kind (expense=from-only, income=to-only, transfer=both,
adjustment=exactly-one) are documented but **NOT enforced** in wave 1 — turning the
``@model_validator`` on would reject historical/edge rows the store tolerates.
Enforcement is a wave-2 decision once ``ledger_service.create_transaction`` is the
only writer (RFC P1-A3 §6.4). The stub is left commented below as a reserved hook.

Scaffold only (RFC P1-A3).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import field_validator

from .base import ONBaseModel


class TxnKind(str, Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"
    adjustment = "adjustment"
    investment = "investment"


class TxnSource(str, Enum):
    manual = "manual"
    ai = "ai"
    email = "email"
    telegram = "telegram"
    csv_import = "csv_import"


def _abs_amount(v):
    return abs(float(v)) if v is not None else None


def _upper_cur(v):
    return v.upper() if isinstance(v, str) and v else (v or None)


# The reserved top-level category a split txn carries (the single leg is allocated across the
# split lines). Non-budgetable / structural — it never appears as a spending envelope; the
# folds distribute a split txn to its line categories, never to this sentinel. See
# docs/rfc/split-transactions.md.
SPLIT = "split"


class TxnSplit(ONBaseModel):
    """One category allocation of a split txn's single leg amount.

    A split line is a LABEL allocation, not a leg: it carries NO account / base_amount / fx_rate.
    ``amount`` is in the allocated leg's NATIVE currency (from_currency for an expense,
    to_currency for income), so ``Σ amount == the leg amount`` is a pure same-currency identity."""

    category: str  # an expense/income category key (validated like the top-level category)
    amount: float  # positive magnitude, in the split leg's native currency
    memo: str = ""  # optional per-line annotation — display-only, exactly like the
    # transaction-level ``memo``: never read by matching, spend rules or payee resolution
    # (splits reach none of them). Named ``note`` until 2026-08-02, which collided with the
    # RETIRED transaction-level ``note`` and with the ``note:`` SEARCH alias — three
    # unrelated meanings of one word on the same entity tree.

    @field_validator("amount", mode="before")
    @classmethod
    def _magnitude(cls, v):
        return _abs_amount(v)


class Transaction(ONBaseModel):
    """The full stored two-leg transaction shape."""

    id: str
    date: str
    kind: TxnKind = TxnKind.expense
    # Two freeform fields split on PROVENANCE (ADR 2026-07-17):
    # ``description`` = the bank-origin / matching string — the ONLY freeform field read by dedup
    # (matching.py), spend rules, payee resolution, and the import external_ref hash.
    # ``memo`` = the user's personal annotation (what you ordered, who you were with) — display-only,
    # read by NONE of the matching/rules/payee systems, so editing it never perturbs dedup.
    description: str = ""
    memo: str = ""
    category: Optional[str] = None  # expense/income only; null for transfer/adjustment
    payee_id: Optional[str] = None  # → PayeeStore entity; expense/income only (P4-PAYEE)

    # FROM leg — account money leaves. null only when kind == "income".
    from_account_id: Optional[str] = None
    from_amount: Optional[float] = None  # positive magnitude, native to from-account
    from_currency: Optional[str] = None

    # TO leg — account money enters. null only when kind == "expense".
    to_account_id: Optional[str] = None
    to_amount: Optional[float] = None  # positive magnitude, native to to-account
    to_currency: Optional[str] = None

    # original foreign charge captured before any leg conversion (e.g. an SGD card billed in USD).
    # DISPLAY/AUDIT only — never read by FX recompute or balance/split math. None = no distinct
    # foreign original; the account leg is the whole story.
    original_amount: Optional[float] = None
    original_currency: Optional[str] = None

    # base-currency reporting snapshot (expense/income/adjustment; null on transfers).
    base_amount: Optional[float] = None
    base_currency: Optional[str] = None
    fx_rate: Optional[float] = None  # native→base at txn date

    # optional investment annotation (cash leg only)
    ticker: Optional[str] = None
    quantity: Optional[float] = None

    tags: List[str] = []  # free-form labels (additive; set by the rules engine — P2-SP1)

    # split allocation (P3-SP): None = unsplit (the common case). When set, ``category`` is the
    # SPLIT sentinel and the single leg's amount is allocated across these lines (Σ amount ==
    # the leg amount, enforced at the create_transaction choke point). A label overlay, not legs.
    splits: Optional[List[TxnSplit]] = None

    # reconciliation state (cleared/reconcile MVP): a leg the user has matched against the
    # bank, or that a reconcile locked in. The working balance ignores it; the cleared
    # balance folds only cleared legs. Default False = uncleared.
    cleared: bool = False

    # Review queue (Accounts redesign): an import-sourced or AI-sourced create lands here
    # (True) until the user Approves (clears it) or Rejects (soft-deletes the row). Trusted
    # sources (manual / bank sync / automation) default False.
    needs_review: bool = False

    # Register declutter: a hidden row drops out of the register unless "Show hidden" is on.
    # Display-only — every balance, net-worth, budget and report fold still counts it.
    hidden: bool = False

    source: TxnSource = TxnSource.manual
    # Provenance/dedup — ONE list (ADR 2026-07-21, collapsing the 2026-07-20 scalar+list split):
    # every ``{"source": str|None, "ref": str}`` pair this row owns. ``source`` is the namespace
    # (``email``/``opening``/``recurring``/``ibkr``/``csv``/``simplefin``/...), ``ref`` the raw
    # per-event id (a Message-ID hash, a FITID, an account:txn key, a content hash). Entry [0] is
    # the creation-time origin (display/actor inference read it via ``core.txn_sources
    # .primary_source``); later entries are pairs absorbed by the merge/dedup-review flow. Every
    # pair is an L0 idempotency key (``by_external_ref``). Empty list = no external provenance.
    external_refs: List[dict] = []
    # ``merged_from`` is the list of txn ids this canonical row absorbed (audit / undo).
    merged_from: List[str] = []
    # ``merged_into`` set on an ABSORBED row → the canonical id it folded into. A tombstone:
    # excluded from balance / net-worth folds exactly like ``deleted``, but reversible (undo).
    merged_into: Optional[str] = None

    batch_id: Optional[str] = None
    created_at: Optional[str] = None
    deleted: bool = False

    @field_validator("from_amount", "to_amount", "base_amount", "original_amount", mode="before")
    @classmethod
    def _magnitude(cls, v):
        return _abs_amount(v)

    @field_validator(
        "from_currency", "to_currency", "base_currency", "original_currency", mode="before"
    )
    @classmethod
    def _cur(cls, v):
        return _upper_cur(v)

    # RESERVED (wave 2): leg-rule enforcement, once create_transaction is the only writer.
    # @model_validator(mode="after")
    # def _check_legs(self):
    #     k = self.kind
    #     if k == "expense" and (self.from_account_id is None or self.to_account_id is not None):
    #         raise ValueError("expense must have a from leg and no to leg")
    #     if k == "income" and (self.to_account_id is None or self.from_account_id is not None):
    #         raise ValueError("income must have a to leg and no from leg")
    #     if k == "transfer" and (self.from_account_id is None or self.to_account_id is None):
    #         raise ValueError("transfer must have both legs")
    #     if k == "adjustment" and (self.from_account_id is None) == (self.to_account_id is None):
    #         raise ValueError("adjustment must have exactly one leg")
    #     return self


class TransactionCreate(ONBaseModel):
    """Replaces the inline ``TransactionCreate`` in ``api/ledger.py`` (wave 2)."""

    date: str
    kind: TxnKind = TxnKind.expense
    description: str = ""  # bank/matching string (ADR 2026-07-17)
    memo: str = ""  # personal, display-only — never affects matching/rules/payees
    category: Optional[str] = None
    payee_id: Optional[str] = (
        None  # → PayeeStore entity (P4-PAYEE); resolved at choke point if unset
    )
    from_account_id: Optional[str] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_account_id: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    original_amount: Optional[float] = None  # foreign charge before conversion (display/audit)
    original_currency: Optional[str] = None
    ticker: Optional[str] = None
    quantity: Optional[float] = None
    tags: List[str] = []  # free-form labels (additive; rules engine + manual — P2-SP1)
    splits: Optional[List[TxnSplit]] = None  # category allocation of the single leg (P3-SP)
    cleared: bool = False  # reconciliation state (cleared/reconcile MVP)
    # Review queue: None lets the create choke point default from `source` (import/AI → True);
    # an explicit True/False wins.
    needs_review: Optional[bool] = None
    source: TxnSource = TxnSource.manual
    # Provenance/dedup {source, ref} pairs; [0] = the creation-time origin (ADR 2026-07-21).
    external_refs: List[dict] = []


class TransactionUpdate(ONBaseModel):
    """PATCH shape — all Optional. Replaces ``api/ledger.py TransactionUpdate``."""

    date: Optional[str] = None
    kind: Optional[TxnKind] = None
    description: Optional[str] = None  # bank/matching string (ADR 2026-07-17)
    memo: Optional[str] = None  # personal, display-only
    category: Optional[str] = None
    from_account_id: Optional[str] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_account_id: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    original_amount: Optional[float] = None  # foreign charge before conversion (display/audit)
    original_currency: Optional[str] = None
    tags: Optional[List[str]] = None  # replace the txn's tags (additive field — P2-SP1)
    splits: Optional[List[TxnSplit]] = None  # set/replace the split allocation (P3-SP)
    cleared: Optional[bool] = None  # toggle reconciliation state (cleared/reconcile MVP)
    # Review queue: set False to Approve (clear review); None leaves it untouched.
    needs_review: Optional[bool] = None
    # Append-only provenance patch. Existing pairs are preserved; duplicates are ignored.
    external_refs: Optional[List[dict]] = None


class BulkTransactionImport(ONBaseModel):
    """Replaces ``api/ledger.py BulkTransactionImport``."""

    transactions: List[TransactionCreate]
