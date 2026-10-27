"""The ONE statement of the transaction shape, shared by the ledger trio.

``add_transactions`` (create), ``update_transaction`` (patch) and ``get_transactions``
(filter) all describe the same data model. They used to each carry their own full copy of
the property prose — three statements of one shape, ~16k chars of the CORE offer, free to
drift apart. This module holds the single field table they all build from.

Anthropic's ``input_schema`` cannot be relied on for ``$ref``/``$defs``, so the factoring is
at the PYTHON level: ``_FIELDS`` holds one schema fragment per field, and the per-tool
builders below assemble them, applying an override only where create/patch/filter genuinely
mean different things (a filter's ``category`` selects rows; a create's names one). The
emitted JSON is unchanged in shape — only its source of truth moved.

Two conventions govern the field prose:

**Enforcement lives in the mechanism, not the schema.** A description that explains what
happens when the model gets the field WRONG is pre-loading error-recovery text into every
turn. That belongs in the error the model actually receives, where it arrives with the
offending value in hand. ``services.ledger`` already raises prescriptive errors for unknown
categories (``_unknown_category_message`` → closest labels + ``list_categories``), unknown
accounts, ambiguous transfer legs and split-sum mismatches; ``tests/test_txn_schema.py``
pins each one so this module can stay silent about them.

**Teaching states the rule once, shows one worked example, and stops.** No restating the
rule in different words, no spelling out a consequence the example already demonstrates.
What survives is what the model cannot infer: which currency figure to read off a source,
which of the confusable foreign-amount fields applies, which text field dedup reads.

``PRIOR_DESCRIPTIONS`` keeps the pre-compression wording retrievable so an A/B eval can
measure the two variants against each other instead of anyone having to judge by eye.

Measured effect on the always-on CORE offer (description + serialized input_schema, chars)::

    add_transactions     10,707 ->  7,868
    update_transaction    4,711 ->  4,079
    get_transactions      3,636 ->  2,596
    ------------------------------------
    trio                 19,054 -> 14,543   (-23.7%)
    CORE total           30,396 -> 25,885   (-14.8%)

The other 16 core tools are byte-identical — the whole delta is this slice. The trio fell
from 62.7% to 56.2% of everything the model is offered on every turn.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# --- Prior wording (A/B baseline) -----------------------------------------------------
# The descriptions these fields carried before the compression pass, keyed by field name.
# Retained ONLY so an eval harness can rebuild the previous variant and run it against the
# current one; nothing in the emitted schema reads this table.
PRIOR_DESCRIPTIONS: dict[str, str] = {
    "description": (
        "Merchant/bank string identifying the txn (e.g. 'STARBUCKS #4412') — used for "
        "dedup, rules & payee matching. Keep the stable merchant name PLUS any "
        "reference/trace/confirmation code the BANK itself stamped on the transaction "
        "(a transfer ref, trace number) — that's used to catch the same event if it's "
        "re-imported later. Not per-purchase detail you're recording for yourself "
        "(order items, who you were with) — that's memo."
    ),
    "memo": (
        "Personal note (order items, who you were with). Display-only — never affects "
        "matching/rules/payees. Do NOT put a bank-issued reference/trace/confirmation "
        "code here — those belong in description even though they're unique per txn. "
        "Optional — leave it empty/omit it when there's no personal detail worth "
        "keeping; don't invent something just to fill it."
    ),
    "currency": (
        "REQUIRED on every row. 3-letter ISO 4217 code of the amount as stated in the "
        "SOURCE (card alert, receipt, statement, user message) — e.g. an alert saying "
        "'USD 10.94' → currency='USD' even when the account is denominated in "
        "something else. Never assume the account's currency; read it from the source. "
        "When this differs from the account's own currency the original foreign charge "
        "is preserved automatically (optionally pass settled_amount for the exact posted "
        "figure)."
    ),
    "settled_amount": (
        "Optional. For a FOREIGN charge (currency differs from the account's own), "
        "the amount that actually POSTED in the account's currency — e.g. US$19.00 "
        "on an SGD card settling at S$25.62 → amount=19, currency='USD', "
        "settled_amount=25.62. The account leg uses it; amount/currency stay as the "
        "original charge. Omit to convert at today's rate. Expense/income only."
    ),
    "original_amount": (
        "Optional. The ORIGINAL foreign amount BEFORE conversion, when amount/"
        "currency are the SETTLED account-currency figures — e.g. a VND 200,000 "
        "dinner posted as SGD 36.08 → amount=36.08, currency='SGD', "
        "original_amount=200000, original_currency='VND'. Display/audit only; never "
        "changes the leg or base amount. Pair with original_currency. (If amount/"
        "currency are the foreign charge itself, use settled_amount instead.) "
        "Expense/income only."
    ),
    "splits": (
        "SPLIT one purchase across categories ($100 store run = $60 food + $40 shopping). The "
        "money moves ONCE (one leg) — a category allocation of the same amount, not separate "
        "transactions. Line amounts are in the transaction's currency and MUST sum to it (a "
        "mismatch is REFUSED, never re-scaled). Expense/income only, never a transfer. "
        "Supplying splits ignores the top-level category; a 1-line split becomes a plain "
        "category. An empty list CLEARS an existing split (update only)."
    ),
    "category": (
        "Spending label — an existing category's label (e.g. 'Food & Dining') or its "
        "id; both resolve to the same category. Call list_categories rather than "
        "assuming a fixed set; an unknown category is REJECTED, not created. "
        "OPTIONAL: unset (None) records the row as 'Uncategorized' — never pass "
        "'uncategorized'. 'income'/'transfer'/'investment' are KINDS, not categories "
        "— use direction='credit', to_account_id/from_account_id, or kind='investment' "
        "respectively; each may still carry any category. A transfer's category is "
        "inert unless exactly one leg is on-budget (e.g. a loan payment)."
    ),
    "to_account_id": (
        "INTERNAL TRANSFER, money LEAVING: the destination account money moves INTO "
        "(one of the user's own accounts); account_id is the source. Do NOT use this "
        "for incoming transfers — use from_account_id instead. Omit for external "
        "transfers — those log as expense."
    ),
    "from_account_id": (
        "INTERNAL TRANSFER, money ARRIVING: source account money moves FROM, with "
        "account_id as the destination. PREFERRED over to_account_id for credits — use "
        "it whenever money arrives INTO account_id from another of the user's accounts "
        "(wallet top-up, bank transfer in). Passing from_account_id + to_account_id "
        "together is also accepted (account_id may then be omitted — the source anchors "
        "the row)."
    ),
    "import_session_id": (
        "UUID shared across ALL batches of the same statement when splitting a large import. "
        "Generate once (uuid4) before the first batch call and reuse it for every subsequent "
        "batch of the same statement. This prevents false duplicate warnings between batches."
    ),
}


# --- The shared field table -----------------------------------------------------------
# One entry per transaction field. Builders below copy these; nothing mutates them in place.
_FIELDS: dict[str, dict[str, Any]] = {
    "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today if omitted."},
    "description": {
        "type": "string",
        "description": (
            "Bank/merchant string, e.g. 'STARBUCKS #4412 REF:8837' — keep the merchant name "
            "plus any reference/trace code the bank itself stamped on it. Read by dedup, "
            "rules and payee matching."
        ),
    },
    "memo": {
        "type": "string",
        "description": (
            "Your own note — what you'd jot down yourself: what was bought, who it was with, "
            "what it was for. Not a copy of the alert: no alert text, times, card or account "
            "numbers, or anything another field already shows. Leave it empty when there's "
            "nothing worth noting."
        ),
    },
    "amount": {"type": "number", "description": "Always a positive number (absolute value)."},
    "direction": {
        "type": "string",
        "enum": ["debit", "credit"],
        "description": (
            "'debit' = money leaving (expense, transfer out, tax, investment deposit). "
            "'credit' = money arriving (income, refund, cashback, transfer in). Default: debit."
        ),
    },
    "currency": {
        "type": "string",
        "description": (
            "REQUIRED. 3-letter ISO 4217 code of the amount AS STATED IN THE SOURCE — a card "
            "alert reading 'USD 10.94' on an SGD account is currency='USD'."
        ),
    },
    # settled_amount and original_amount are the confusable pair: the SAME foreign charge,
    # differing only in which figure the caller put in `amount`. One scenario carried across
    # both descriptions teaches the distinction by contrast; two independent examples (the
    # prior wording used USD/SGD for one and VND/SGD for the other) obscured that they are
    # mirror images of each other.
    "settled_amount": {
        "type": "number",
        "description": (
            "Use when `amount` is the FOREIGN figure: the amount that actually posted in the "
            "account's currency. US$19.00 posting as S$25.62 on an SGD card → amount=19, "
            "currency='USD', settled_amount=25.62. The account leg uses it. Omit to convert at "
            "today's rate. Expense/income only."
        ),
    },
    "original_amount": {
        "type": "number",
        "description": (
            "The mirror of settled_amount, for that same charge entered at its POSTED figure: "
            "amount=25.62, currency='SGD', original_amount=19, original_currency='USD'. "
            "Display/audit only — never changes the leg or the base amount. Expense/income only."
        ),
    },
    "original_currency": {
        "type": "string",
        "description": "3-letter ISO code of original_amount (e.g. 'USD'). Pair with original_amount.",
    },
    # NOTE: the prose deliberately stops short of saying how to reach kind='investment' on
    # this surface. `add_transactions` has no `kind` parameter (the old wording pointed at
    # one anyway), and the inference that would otherwise cover it is currently unreliable —
    # see tests/test_txn_schema.py::test_category_label_and_id_disagree_on_inferred_kind.
    "category": {
        "type": "string",
        "description": (
            "Spending label — a category's label ('Food & Dining') or its id; both resolve. "
            "OPTIONAL: unset records the row as 'Uncategorized'. 'income'/'transfer'/'investment' "
            "are transaction KINDS, not categories — use direction='credit' for income and "
            "the leg account ids for a transfer."
        ),
    },
    "account_id": {
        "type": "string",
        "description": (
            "REQUIRED. The account money leaves (debit) or enters (credit) — for a statement "
            "row, the statement's own account. Accepts the account's NAME ('Meridian Bank Savings', "
            "case-insensitive) or its id — no list_accounts lookup needed just to translate a "
            "name. A name matching two accounts is refused with both named; send an id then."
        ),
    },
    "to_account_id": {
        "type": "string",
        "description": (
            "Internal transfer, money LEAVING account_id: the destination account (name or "
            "id). Omit for external transfers — those log as an expense."
        ),
    },
    "from_account_id": {
        "type": "string",
        "description": (
            "Internal transfer, money ARRIVING into account_id: the source account (name or "
            "id). Passing from_account_id and to_account_id together is also accepted, and "
            "account_id may then be omitted."
        ),
    },
    "to_amount": {
        "type": "number",
        "description": (
            "TRANSFER ONLY: amount received in the destination account's currency. Defaults to "
            "amount for same-currency transfers."
        ),
    },
    "to_currency": {
        "type": "string",
        "description": (
            "TRANSFER ONLY: currency of the destination leg. Defaults to that account's own "
            "currency; pass it explicitly for cross-currency pairs."
        ),
    },
    "tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Free-form labels, e.g. ['work', 'reimbursable']. Additive; rules may add more.",
    },
    "splits": {
        "type": "array",
        "description": (
            "Allocate ONE purchase across categories — a $100 store run as $60 food + $40 "
            "shopping, booked as a single $100 leg. Lines are in the transaction's currency "
            "and must sum to it. Expense/income only. Splits override the top-level category; "
            "an empty list clears an existing split."
        ),
        "items": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "An existing category — label or id.",
                },
                "amount": {"type": "number", "description": "Positive magnitude for this line."},
                "memo": {"type": "string", "description": "Optional per-line memo."},
            },
            "required": ["category", "amount"],
        },
    },
    "external_refs": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["email"],
                    "description": (
                        'Provenance namespace. Set to "email" when logging a transaction '
                        "from a mailbox."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        'The raw per-event id. For source "email", copy the `message_id` '
                        "value shown by `search_messages`/`get_messages` exactly as-is — do "
                        "NOT add brackets, a prefix, or a colon."
                    ),
                },
            },
            "required": ["source", "ref"],
        },
        "description": (
            "Provenance/idempotency pairs — usually exactly one entry, e.g. "
            '[{"source": "email", "ref": "<message_id>"}]. Re-booking a row carrying an '
            "already-present (source, ref) pair returns the existing row instead of "
            "duplicating it. Omit for one-off manual entries."
        ),
    },
    "payee_id": {
        "type": "string",
        "description": (
            "Link to a payee entity (pay_… id from get_payees or create_payee) instead of "
            "auto-resolving one from the description. Expense/income only."
        ),
    },
}


def field(name: str, **overrides: Any) -> dict[str, Any]:
    """One field's schema fragment, deep-copied so callers can never mutate the table.

    ``overrides`` replace top-level keys — used where a tool genuinely means something
    different by the field (a filter vs. a value), not to re-word a shared fact.
    """
    frag = deepcopy(_FIELDS[name])
    frag.update(overrides)
    return frag


def fields(*names: str) -> dict[str, dict[str, Any]]:
    """A ``properties`` block built from the shared table, in the order given."""
    return {n: field(n) for n in names}


# --- Per-tool property blocks ---------------------------------------------------------

# Fields whose create-time wording carries over verbatim to the patch surface. `update_
# transaction` used to restate every one of these in its own words; identical meaning,
# so it now reads them from the same table.
#
# The leg fields, `category` and `original_amount` are deliberately NOT in this list: the
# create surface anchors a row on `account_id` and expresses money direction with
# `direction`/`settled_amount`, none of which exist on a patch (which addresses both legs
# directly as from_*/to_*). Sharing that wording here would point the model at parameters
# this tool does not accept, so those four carry a patch-specific description below.
_UPDATE_SHARED = (
    "date",
    "description",
    "memo",
    "to_amount",
    "to_currency",
    "original_currency",
    "tags",
    "splits",
)


def create_item_properties() -> dict[str, dict[str, Any]]:
    """The per-item transaction schema for ``add_transactions``."""
    return fields(
        "date",
        "description",
        "memo",
        "amount",
        "direction",
        "currency",
        "settled_amount",
        "original_amount",
        "original_currency",
        "category",
        "account_id",
        "to_account_id",
        "from_account_id",
        "to_amount",
        "to_currency",
        "tags",
        "splits",
        "external_refs",
        "payee_id",
    )


def preflight_item_properties() -> dict[str, dict[str, Any]]:
    """The per-item schema for ``check_duplicates`` — a PROPER SUBSET of
    ``create_item_properties()``, so a caller can hand it the exact rows it was about to send
    to ``add_transactions`` and every extra field is simply ignored downstream.

    The subset is "what the match engine actually reads", traced through ``services/matching``:
    the legs and their amounts/currencies, the date, the ``description`` (``txn_text`` — never
    ``memo``, which dedup is forbidden to consult), the original-charge fingerprint (F22
    ``_originals_align``), ``external_refs`` (the L0 floor) and ``category`` (which decides the
    derived ``kind`` the classifier branches on). Everything else on a create — ``memo``,
    ``tags``, ``splits``, ``payee_id`` — cannot change any verdict, so advertising
    it would be schema bytes bought with nothing.
    """
    return fields(
        "date",
        "description",
        "amount",
        "direction",
        "currency",
        "settled_amount",
        "original_amount",
        "original_currency",
        "category",
        "account_id",
        "to_account_id",
        "from_account_id",
        "to_amount",
        "to_currency",
        "external_refs",
    )


def update_properties() -> dict[str, dict[str, Any]]:
    """The per-row patch surface for ``update_transactions`` (one row or many).

    Same fields, same meanings — only the three that genuinely differ on a patch carry an
    override: the source leg is addressed as from_* (there is no `direction`/`amount`
    shorthand once a row exists), and payee_id becomes nullable so the link can be cleared.

    ``cleared``/``needs_review`` are patch-only concepts with no create-time equivalent
    (a brand-new row is never "reconciled" or "flagged for review" at birth) — like
    ``kind``/``from_amount``/``from_currency``, they're declared here directly rather than
    threaded through the shared ``_FIELDS`` table. Formerly ``bulk_update_transactions``-only
    fields, promoted onto the single-row patch too (2026-07-19 update-tool merge) so a caller
    updating one row never has to reach for a second tool just to flip a flag.
    """
    props = fields(*_UPDATE_SHARED)
    props["transaction_id"] = {
        "type": "string",
        "description": "UUID of the transaction to update.",
    }
    props["kind"] = {
        "type": "string",
        "enum": ["expense", "income", "transfer", "adjustment", "investment"],
        "description": "Change the transaction kind.",
    }
    props["from_amount"] = {
        "type": "number",
        "description": "Amount in the source account's native currency.",
    }
    props["from_currency"] = {"type": "string", "description": "Currency of the source leg."}
    props["from_account_id"] = field(
        "from_account_id",
        description="Account the money leaves (expense, or a transfer's source leg).",
    )
    props["to_account_id"] = field(
        "to_account_id",
        description="Account the money arrives in (income, or a transfer's destination leg).",
    )
    props["category"] = field(
        "category",
        description=(
            "Spending label — a category's label ('Food & Dining') or its id; both resolve. "
            "Unset means 'Uncategorized'. 'income'/'transfer'/'investment' are transaction KINDS, "
            "not categories — change those with `kind` plus the leg account ids."
        ),
    )
    props["original_amount"] = field(
        "original_amount",
        description=(
            "The original foreign figure behind an already-converted leg: a USD 10.98 charge "
            "that settled as SGD 14.21 → original_amount=10.98, original_currency='USD', "
            "from_amount stays 14.21. Display/audit only — never changes the leg or base amount."
        ),
    )
    props["payee_id"] = field(
        "payee_id",
        type=["string", "null"],
        description=(
            "Link to a payee entity (pay_… id from get_payees or create_payee); null clears "
            "the link. Editing description does not re-resolve the payee on an update."
        ),
    )
    props["external_refs"] = field(
        "external_refs",
        description=(
            "Append provenance/idempotency pairs to this existing transaction. Existing pairs "
            "are always preserved and duplicate (source, ref) pairs are ignored. When multiple "
            "emails contributed facts to one transaction, include every contributing email's "
            "message_id as its own source='email' entry."
        ),
    )
    props["cleared"] = {
        "type": "boolean",
        "description": (
            "SET the cleared/reconciled flag — whether this has been matched against the "
            "bank. Never changes the working balance, only which legs the cleared balance "
            "folds. An account's Starting Balance row can be set true but never false."
        ),
    }
    props["needs_review"] = {
        "type": "boolean",
        "description": (
            "Re-flag a row for review (true). Approving is approve_transactions; false is "
            "refused here."
        ),
    }
    return props


def filter_properties() -> dict[str, dict[str, Any]]:
    """The filter surface for ``get_transactions``.

    A filter SELECTS rows rather than naming a value, so most of these carry a filter-specific
    description rather than the create-time one; they share the table only where the meaning
    is identical (the account/payee/tag references).
    """
    return {
        "date_from": {
            "type": "string",
            "description": "Start date inclusive, YYYY-MM-DD. Use for multi-month ranges.",
        },
        "date_to": {"type": "string", "description": "End date inclusive, YYYY-MM-DD."},
        "month": {
            "type": "integer",
            "description": "1–12. Single-month shorthand. Ignored when date_from/date_to are set.",
        },
        "year": {
            "type": "integer",
            "description": "4-digit year. Single-month shorthand. Ignored when date_from/date_to are set.",
        },
        # The five OR-able filters below accept a single value OR a list. A list is OR'd
        # within the field and AND'd against the others, so "spending across my three
        # cards" is ONE call — three singular calls each re-sent the whole conversation,
        # which is where the cost actually was.
        #
        # categories/transaction_types/payee_ids/tags are PLURAL param names (renamed from
        # category/transaction_type/payee_id/tag 2026-07-19b, `tool_inventory.py --lint`'s
        # "param takes a list but is named singular" rule) — a singular name inviting a
        # list is the exact trap that got delete_account's account_id stringified. The old
        # keys still work: `get_transactions`'s handler (`ledger.py`) reads the new key
        # first, falling back to the old one — see its `_dual_filter` helper.
        "categories": {
            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            "description": (
                "Filter by category label or id; pass a LIST to match any of several. "
                "Passing a kind name here ('expense', 'income', …) filters by "
                "transaction_types instead."
            ),
        },
        "transaction_types": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["expense", "income", "transfer", "adjustment", "investment"],
                },
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["expense", "income", "transfer", "adjustment", "investment"],
                    },
                },
            ],
            "description": "Filter by transaction kind, or a list of kinds (any of).",
        },
        "account_id": field(
            "account_id",
            description=(
                "Filter by a single account ID (from list_accounts). For several accounts "
                "use account_ids — one call, not one per account."
            ),
        ),
        "account_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Filter by SEVERAL accounts at once (matches any of them, either leg). Use "
                "this for 'what did I spend across my cards' instead of one call per card."
            ),
        },
        "payee_ids": {
            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            "description": (
                "Filter by payee entity ID (pay_… from get_payees), or a list to match any "
                "of several. All-time unless a month/year/date range is also set."
            ),
        },
        "min_amount": {
            "type": "number",
            "description": (
                "Only rows whose amount is >= this. Compared in the account's native currency "
                "when account_id is set, otherwise in base currency."
            ),
        },
        "max_amount": {
            "type": "number",
            "description": "Only rows whose amount is <= this. Same currency rule as min_amount.",
        },
        "q": {
            "type": "string",
            "description": (
                "Free-text search — case-insensitive substring. Scope it with q_field; the "
                "default searches description, memo, category label and payee name (e.g. 'uber')."
            ),
        },
        "q_field": {
            "type": "string",
            "enum": ["any", "payee", "description", "memo", "category"],
            "description": (
                "Which field q searches (default 'any'). 'description' is the BANK's string "
                "— what dedup, spend rules and payee matching read; 'memo' is the user's own "
                "personal annotation, which none of them read. 'find rows where I wrote X' "
                "is memo; 'where the bank wrote X' is description."
            ),
        },
        "tags": {
            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            "description": (
                "Only rows carrying this tag (case-insensitive exact match); pass a LIST to "
                "match any of several tags."
            ),
        },
        "needs_review": {
            "type": "boolean",
            "description": (
                "true = only rows awaiting review (all-time unless dated); false = only "
                "reviewed rows. Approve with approve_transactions."
            ),
        },
        "transaction_id": {
            "type": "string",
            "description": (
                "UUID of one transaction. All other filters are ignored and a single record "
                "is returned."
            ),
        },
        "limit": {
            "type": "integer",
            "description": (
                "Max rows to return (default 25). Raise it only when you genuinely need to "
                "read individual rows — the `summary` block already totals the FULL filtered "
                "set, so a bigger page never changes the money figures."
            ),
        },
        "offset": {"type": "integer", "description": "Zero-based row offset (default 0)."},
        "summary_only": {
            "type": "boolean",
            "description": (
                "Return ONLY the summary block (totals + count), no transaction rows. The "
                "totals are identical to a full fetch — they are computed over the whole "
                "filtered set before paging — so prefer this whenever the question is "
                "'how much', not 'which rows'."
            ),
        },
    }
