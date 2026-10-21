"""The one MetaData — every table the database owns, as SQLAlchemy Core objects.

Pydantic (backend/models/) stays the domain model; these are STORAGE shapes.
The naming convention is load-bearing: Alembic autogenerate diffs constraint
names, and an unnamed constraint autogenerates a spurious diff forever.

Identity (spec D4): users/books/book_members, seeded 1/1/1 by revision 0001.
book_id goes on Tier-1 (money) tables ONLY — evidence is Tier-2 world data.
Sentinel ids are deterministic (not random) so QA clones, fixtures and
round-trip diffs are stable across installs; hosted mode mints random ids for
NEW rows later, which cannot collide with the all-zeros sentinel.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.schema import conv

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

DEFAULT_USER_ID = "usr_000000000000"
DEFAULT_BOOK_ID = "book_000000000000"

users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False, server_default=""),
    Column("display_name", Text, nullable=False, server_default=""),
    Column("created_at", Text, nullable=False, server_default=""),
)

books = Table(
    "books",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False, server_default=""),
    Column("base_currency", Text, nullable=False, server_default=""),
    Column("created_at", Text, nullable=False, server_default=""),
)

book_members = Table(
    "book_members",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), primary_key=True),
    Column("role", Text, nullable=False),  # owner | member | viewer
)

# ── Tier 1: the money core (spec D4/D5, plan 3 task 2) ────────────────────────
# accounts / transactions / transaction_splits / transaction_external_refs / txn_audit /
# import_batches / statements_meta / merge_journal — every row book_id-scoped, book_id
# leading every non-PK index. `accounts` is given VERBATIM by the plan-3 task-2 brief and
# transcribed exactly; the rest are derived from the brief's prose PLUS the real row shapes
# in db/{account,transaction,import_batch,statement,merge_journal,transaction_audit}_store.py
# — every place a field-list/nullability choice deviates from a literal reading of the brief
# is called out inline, because that real shape is what today's actual ledger rows look like.
#
# Server-default convention for this whole block (matching `accounts.archived` below, the
# one concrete precedent the brief gives): a boolean-shaped Integer column gets
# `server_default=text("0")`; an ordinary NOT NULL text/enum column does NOT get a
# server_default even where the application layer has its own fallback (e.g.
# `accounts.type`/`currency` have no server_default despite `_make_account` defaulting them)
# — the app is the authority on those values, and a same-shaped DB default would either be
# meaningless ("" for an identity field) or actively risky (a wrong-but-valid enum value
# masking a caller bug). ONE exception (FIX ROUND 1, reviewer): the five "hazard" columns on
# `transactions`/`statements_meta` where the LIVE ledger's own history proves the key can be
# genuinely absent on a real row (`transactions.created_at`/`memo`/`tags`/`merged_from`,
# `statements_meta.batch_ids`) — see each table's own comment for the live-data counts and
# why `payload`, not the promoted column, is where that absence is provable.
#
# CheckConstraint names are wrapped in `conv(...)` (pre-expanded to the FINAL
# `ck_<table>_<name>` form) rather than left as a short logical name for the "ck" naming
# convention to expand — verified empirically that leaving it short double-expands under
# SQLite's `render_as_batch` autogenerate comparison (env.py sets that for sqlite), which
# `.copy()`-and-reattaches the constraint during comparison and reruns the convention on its
# own already-expanded output (`ck_x` -> `ck_transactions_ck_x`). `conv()` is SQLAlchemy's
# documented "this name is final" marker — stable across any number of copies. The revision
# 0006 migration (which builds its own bare, convention-less MetaData via `op.create_table`)
# spells out that SAME final string directly.
accounts = Table(
    "accounts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),  # file order, unique-indexed
    Column("name", Text, nullable=False),
    # FIX ROUND 1 (reviewer, Important/Minor fold-in): promoted on top of the otherwise-verbatim
    # DDL above. `_make_account`'s real field order is id/name/KIND/type/... — `kind` ("asset" |
    # "liability") is, per CLAUDE.md, "the only field net-worth math cares about", and the
    # verbatim brief DDL omitted it. `payload` already carries it (nothing to backfill); this
    # only adds the query-surface projection. Promotion is additive — every other column here
    # stays exactly as given.
    Column("kind", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("currency", Text, nullable=False),
    Column("archived", Integer, nullable=False, server_default=text("0")),
    Column("opening_txn_id", Text, nullable=True),  # NO FK: circular ref; services own
    # the dangling-RAISES invariant (ADR'd)
    Column("data", JSON, nullable=False),  # verbatim row (round-trip source of truth)
    Index("ix_accounts_book_seq", "book_id", "seq", unique=True),
)

# Shared account identity.  The account payload keeps only ``institution_id``; this table owns
# the mutable display name and one normalized name per book.
institutions = Table(
    "institutions",
    metadata,
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("id", Text, primary_key=True),
    Column("normalized_name", Text, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("book_id", "normalized_name"),
    Index("ix_institutions_book_seq", "book_id", "seq", unique=True),
)

# `transactions`: mirrors db/transaction_store.py's `make_transaction`/`_norm_splits`/
# `_norm_ext_refs` field-for-field, PLUS `payload` — the SAME verbatim-payload +
# promoted-columns pattern as `accounts` (RULING, reviewer fix round 1, supersedes the
# original brief's "FULLY promoted, no payload column" instruction for this table).
#
# WHY: a promoted column cannot round-trip the distinction between a KEY THAT IS ABSENT and
# one that is present-but-empty — and the live ledger has both, on real rows, on more than one
# field: `created_at` is missing outright on 68 of 1,816 real transaction rows; `memo` on 1,228.
# A promoted-only schema would force the absorb to invent a value for "missing" (typically the
# same value it uses for "empty"), silently merging two different historical facts into one.
# `payload` is the round-trip source of truth (the row exactly as absorbed/as-written); every
# promoted column below is the derived query surface, RECOMPUTED from payload on every write —
# never the other way around. `transaction_splits`/`transaction_external_refs` adopt the same
# pattern for the same reason: their `payload` holds each embedded dict exactly as it appeared
# inside the parent's `splits`/`external_refs` list, before `split_id`/`ref_id`/`idx` synthesis.
#
# `created_at`/`memo`/`tags`/`merged_from` get type-appropriate `server_default`s (still NOT
# NULL) precisely BECAUSE payload — not these columns — is now the place absence is provable:
# when the source row genuinely lacks a key, payload omits it (verbatim) while the promoted
# column still needs SOME value for a query-surface NOT NULL guarantee, and an empty
# string/array is the honest "unknown, see payload" sentinel — not a claim that the source data
# was empty. `description`/`source`/other NOT-NULL text columns are NOT given this treatment:
# the reviewer's live-data sweep named exactly these four fields on `transactions` (plus
# `statements_meta.batch_ids` below) as measured-missing; extending it further would be
# guessing past the evidence.
#
# `base_amount_pinned` is a RETIRED flag (ADR 2026-07-09, update 2026-07-18: "base_amount_pinned
# flag REMOVED") — every write since unconditionally strips it (`services/ledger.py`:
# `row.pop("base_amount_pinned", None)  # retired flag`), and no writer has set it true since.
# Kept as a column anyway because the brief names it explicitly and a pre-2026-07-18 row in
# the live JSON may still carry a stale value — the column exists purely so that legacy value
# round-trips through the task-3 absorb instead of silently vanishing; it is otherwise dead.
#
# `from_account_id`/`to_account_id` are RESTRICT (not the dialect default NO ACTION) so the
# existing `delete_account` 409-while-referenced invariant becomes DB-enforced, not just
# service-enforced. `merged_into` gets NO FK for the same reason `opening_txn_id` doesn't: a
# self-reference into this same table before the target row is guaranteed to exist is a
# circularity the services layer already owns (merge/undo can transiently point at a row that
# is itself mid-tombstone) — services own the invariant, not a DB constraint.
transactions = Table(
    "transactions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("date", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("category", Text, nullable=True),  # optional — null shows as "Uncategorized"; no sink
    Column("from_account_id", Text, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    Column("to_account_id", Text, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    # PLAN 3 TASK 5 (revision 0008): Float -> Text, holding repr(float)-exact decimal
    # strings ("shortest round-trip form" — float(repr(x)) == x guaranteed). Storage is
    # now exact and human-readable; the ARITHMETIC type stays float at the service
    # boundary (a deliberate deferral, ADR landing in plan 3 task 8 — converting ~3,000
    # lines of monetary math is its own future program). Postgres: plain Text, no NUMERIC
    # — see 0008's own docstring.
    Column("from_amount", Text, nullable=True),
    Column("to_amount", Text, nullable=True),
    Column("base_amount", Text, nullable=True),
    Column("fx_rate", Text, nullable=True),
    Column("original_amount", Text, nullable=True),
    Column("from_currency", Text, nullable=True),
    Column("to_currency", Text, nullable=True),
    Column("base_currency", Text, nullable=True),
    Column("original_currency", Text, nullable=True),
    Column("ticker", Text, nullable=True),
    Column("source", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column(
        "memo", Text, nullable=False, server_default=text("''")
    ),  # 1,228/1,816 real rows lack it
    Column("payee_id", Text, nullable=True),
    Column("batch_id", Text, nullable=True),  # no FK — not in the brief's FK list
    Column("merged_into", Text, nullable=True),  # NO FK — self-referential circularity, see above
    Column(
        "created_at", Text, nullable=False, server_default=text("''")
    ),  # 68/1,816 real rows lack it
    Column("reconciled_at", Text, nullable=True),
    Column("cleared", Integer, nullable=False, server_default=text("0")),
    Column("deleted", Integer, nullable=False, server_default=text("0")),
    Column("needs_review", Integer, nullable=False, server_default=text("0")),
    Column(  # RETIRED — see module comment above
        "base_amount_pinned", Integer, nullable=False, server_default=text("0")
    ),
    Column("quantity", Text, nullable=True),  # revision 0008 — see from_amount comment above
    Column("tags", JSON, nullable=False, server_default=text("'[]'")),
    Column("merged_from", JSON, nullable=False, server_default=text("'[]'")),
    Column("payload", JSON, nullable=False),  # verbatim row (round-trip source of truth)
    CheckConstraint(
        "kind <> 'transfer' OR (from_account_id IS NOT NULL AND to_account_id IS NOT NULL)",
        name=conv("ck_transactions_transfer_has_both_legs"),
    ),
    CheckConstraint("cleared IN (0,1)", name=conv("ck_transactions_cleared_bool")),
    CheckConstraint("deleted IN (0,1)", name=conv("ck_transactions_deleted_bool")),
    CheckConstraint("needs_review IN (0,1)", name=conv("ck_transactions_needs_review_bool")),
    CheckConstraint(
        "base_amount_pinned IN (0,1)", name=conv("ck_transactions_base_amount_pinned_bool")
    ),
    Index("ix_transactions_book_seq", "book_id", "seq", unique=True),
    Index("ix_transactions_book_date", "book_id", "date"),  # spec's own worked example
    Index("ix_transactions_book_from_account", "book_id", "from_account_id"),
    Index("ix_transactions_book_to_account", "book_id", "to_account_id"),
)

# Embedded lists promoted to child tables because they're queried, not just carried (spec
# D4/D5). Each ALSO carries `payload` (RULING, reviewer fix round 1 — same verbatim-payload
# pattern as `accounts`/`transactions` above): the embedded dict exactly as it appeared inside
# the parent transaction's `splits`/`external_refs` list — `{category, amount, memo}` /
# `{source, ref}` — before `split_id`/`ref_id`/`idx` synthesis. `category`/`ref` are NOT NULL
# despite a looser reading of the brief's wording: `_norm_splits`/`_norm_ext_refs`
# (transaction_store.py) unconditionally FILTER OUT any split lacking a category or any ext-ref
# lacking a ref before it is ever stored — today's real rows never carry a null there.
# `transaction_external_refs.source` stays nullable — `_norm_ext_refs` explicitly preserves "a
# missing/empty source stays None (a bare no-prefix ref)" as a real, documented domain state,
# not an absence of data. Neither child table's promoted columns got the hazard-column
# `server_default` treatment `transactions` did (above): the reviewer's live-data sweep found no
# comparable missing-key evidence on the CHILD rows, and inventing one would be guessing.
transaction_splits = Table(
    "transaction_splits",
    metadata,
    Column("split_id", Text, primary_key=True),  # surrogate — write_log single-col-pk rule
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("txn_id", Text, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
    Column("idx", Integer, nullable=False),  # embed order — round-trip exactness
    Column("amount", Text, nullable=False),  # revision 0008 — see transactions.from_amount
    Column("category", Text, nullable=False),
    Column("memo", Text, nullable=False),
    Column("payload", JSON, nullable=False),  # verbatim embedded split dict
    Index("ix_transaction_splits_book_txn_idx", "book_id", "txn_id", "idx", unique=True),
)

transaction_external_refs = Table(
    "transaction_external_refs",
    metadata,
    Column("ref_id", Text, primary_key=True),  # surrogate — write_log single-col-pk rule
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("txn_id", Text, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False),
    Column("idx", Integer, nullable=False),  # embed order — [0] is the creation-time origin
    Column("source", Text, nullable=True),  # None = a bare no-prefix ref (real domain state)
    Column("ref", Text, nullable=False),
    Column("payload", JSON, nullable=False),  # verbatim embedded ext-ref dict
    # Supports "reconstruct one txn's ref list" (task 3 round-trip export) — the dedup unique
    # index below does NOT lead with txn_id, so it can't serve that lookup on its own.
    Index("ix_transaction_external_refs_book_txn", "book_id", "txn_id"),
    Index(  # the dedup engine's lookup (by_external_ref), now O(log n)
        # PLAN 3 TASK 4 (own finding, discovered by this task's own store test — NOT
        # inherited from task 2/3): this index is NON-UNIQUE, deliberately, and was found
        # to need to be — a real, INTENDED, pre-existing domain state makes TWO DIFFERENT
        # transactions legitimately hold the identical NON-NULL (source, ref) pair at once.
        # `services.ledger.merge_transactions` copies the absorbed row's provenance pairs
        # onto the canonical row (`new_external_refs` in that function) WITHOUT ever
        # clearing them off the absorbed row itself — the absorbed row is tombstoned
        # (`deleted=True`, `merged_into=<canonical>`) but keeps its own `external_refs`
        # untouched, verbatim, by design (ADR 2026-07-21 — "the canonical row already
        # carries the absorbed pair in its own external_refs[]", stated directly in
        # `transaction_store.by_external_ref`'s own docstring). A UNIQUE constraint here
        # would make every ordinary merge of two rows that share an authoritative
        # (non-null-source) external ref raise IntegrityError — reproduced directly via
        # this store's own test suite (a plain two-row scenario, no live data needed) —
        # not a null-source edge case, but the DEDUP ENGINE'S CORE MERGE OPERATION.
        # A schema originally marked this index `unique=True` (fix round 1's reviewer
        # note below, now superseded) reasoning only about the SEPARATE, already-known
        # null-source gap (SQL NULL<>NULL means two (None, ref) rows already passed a
        # unique index on both dialects) — that reasoning never considered the merge
        # scenario, which affects non-null sources too. The app-level Python tuple-compare
        # in `transaction_store.by_external_ref`/`has_external_ref` (`target = (source,
        # ref)`, matched against `core.txn_sources.ext_ref_pairs`) was ALWAYS the sole
        # dedup authority (this schema never actually changed that — see that store's own
        # docstring) — this index exists PURELY as a lookup accelerant, never as an
        # enforcement mechanism, for either the null- or non-null-source case. Edited in
        # place on this already-landed revision (0006), matching plan 3 task 2's own fix
        # round 1 precedent: 0006 has still never absorbed a real user's data outside this
        # dev branch, so there is no shipped/applied state whose compatibility an edit here
        # would break — see ADR 2026-08-07-external-ref-index-non-unique for the full
        # writeup and the plan-3-task-4 report for how this was found.
        "ix_transaction_external_refs_book_source_ref",
        "book_id",
        "source",
        "ref",
        unique=False,
    ),
)

# Append-only forensic log (services/audit.py `_build_event`). Goes in
# `ai.checkpoint._EXCLUDED_TABLES` (task 4 wires the exclusion — a restore must never erase
# audit history). `at` mirrors the promoted-column name `services/audit.py`'s own `activity()`
# projection already uses for this same value when it reaches the frontend (`"at": e.get("ts")`)
# — the stored event dict's real key is `ts`; task 3/4's promotion write reads `payload["ts"]`
# into it. `txn_id` is indexed but carries NO FK: a transaction is only ever soft-deleted
# (`deleted`) or merge-tombstoned, never hard-deleted today, but the audit trail is a forensic
# record that must remain valid even against a hypothetical future hard-delete — the absence of
# a FK is deliberate, not an oversight. `seq` is not in the brief's literal column list, but
# every other list-shaped store in this file carries one for EXACT round-trip reconstruction
# (task 3): `ts`/`at` alone cannot break a same-second tie, only the file's own order can.
txn_audit = Table(
    "txn_audit",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("txn_id", Text, nullable=False),  # NO FK — see module comment above
    Column("at", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("surface", Text, nullable=False),
    Column("payload", JSON, nullable=False),  # verbatim event: id/txn_id/op/ts/actor/surface/
    # source/session_id/sender_id/intent/automation/automation_id/before/after/note/...
    Index("ix_txn_audit_book_seq", "book_id", "seq", unique=True),
    Index("ix_txn_audit_book_txn", "book_id", "txn_id"),
)

# Tier-2-plus (promoted-over-verbatim-payload, like accounts): `payload` stays the round-trip
# source of truth; these columns are the query surface — db/import_batch_store.py's own
# `query_by_session`/`list_batches` filter exactly `import_session_id`/`status`.
import_batches = Table(
    "import_batches",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("source_account_id", Text, nullable=True),  # no FK — not in the brief's FK list
    Column("transaction_count", Integer, nullable=False),
    Column("status", Text, nullable=False),  # "active" | "rolled_back"
    Column("import_session_id", Text, nullable=True),
    Column("statement_ref_id", Text, nullable=True),
    Column("payload", JSON, nullable=False),
    Index("ix_import_batches_book_seq", "book_id", "seq", unique=True),
    Index("ix_import_batches_book_status", "book_id", "status"),
    Index("ix_import_batches_book_session", "book_id", "import_session_id"),
)

# Tier-2-plus, same pattern. `StatementStore.create()`'s real signature only ever writes
# id/created_at/filename/mime/size_bytes/file_hash/file_path/kind/upload_entry/parsed_rows/
# parsed_at/account_id/batch_ids — `size_bytes`/`file_path`/`kind` are promoted here even
# though the brief's prose doesn't name them individually (real, always-present fields of that
# signature). `status` was reserved-but-unpopulated at this table's birth ("no literal source
# today... kept because the brief asks for it, reserved for a derived/summary value a future
# store rewrite computes") — revision 202609091200 is that rewrite: it now carries the ASYNC
# PREVIEW's parse lifecycle (`parsing` | `ready` | `failed` | `interrupted`), written by
# `services.statements.parse_progress`'s persistence hook (`StatementStore.set_parse_status`).
# NULL stays meaningful — a legacy/pre-B20 record, not a violation — so this gets no
# CheckConstraint, matching `import_batches.status` just above (a comment-documented enum, no
# CHECK). The hook's own snapshot dict — `{stages, started_at, elapsed_s, done, error}` —
# does NOT live on this row: `statement_parse_progress` (below) is a narrow side table for
# it, upserted roughly once per stage/page rather than paying this row's full
# read/reinline/re-extract/write cycle ten times per parse. `status` itself changes only on
# an actual transition, via a single-column UPDATE, never `update_by_id`. See
# `StatementStore.set_parse_status`/ADR 2026-09-09-statement-parse-status-lives-on-the-record.
# `meta_unavailable`/`import_meta` are
# promoted OUT of the richer statement pipeline (services/statements/canonical.StatementMeta,
# stamped via ad-hoc `update_by_id` calls the store itself never names — decisions.py commits
# it under the `import_meta` key) — `import_meta` stays nullable: `create()` never sets it, so
# it is genuinely absent until the first preview runs, not just empty. `file_hash` gets a
# supporting (non-unique) index for `find_by_hash`'s dedup lookup — NOT a unique constraint:
# the app's own create()-time dedup keeps new rows unique going forward, but a UNIQUE index
# here would abort task 3's absorption on any unverified legacy duplicate.
#
# payload/parsed_rows split (brief-mandated): `payload` never carries `parsed_rows` inline once
# extracted — task 3's absorption extracts it to `FILES_DIR/statements/<id>.parsed.json`
# (2026-08-22 consolidate-derived-data-storage RFC, Option A — retiring the earlier
# `DATA_DIR/statements_parsed/<id>.json` location) and stamps `has_parsed_rows` (1) so;
# `payload` then never carries `parsed_rows` (re-inlined from that deterministic path only on
# read/export). `has_parsed_rows` REPLACES the earlier `parsed_rows_path` (a full,
# DATA_DIR-relative stored path) — revision 0046: once the destination is deterministic
# (`<id>.parsed.json`, always, for every id) a stored literal path carries no information a
# boolean doesn't already imply, and `money_io._reinline_statement`'s whole reason to check
# the flag before touching disk (skip a filesystem read entirely for the common
# never-parsed case, rather than probing a file that provably doesn't exist) is served
# exactly as well by a boolean as by a path string.
#
# `meta_unavailable` gets a CheckConstraint (unlike `accounts.archived`, which does not): the
# brief's general "Booleans as Integer 0/1 with server_default=text("0") + CheckConstraints"
# rule applies to every table in this task; `accounts` is the one narrow, explicit exception
# because that table's DDL is given VERBATIM (transcribed character-for-character, no CHECK
# shown) — a carve-out for THAT table's literal text, not a pattern to extend to a table this
# file derives itself.
#
# `batch_ids` gets `server_default=text("'[]'")` (FIX ROUND 1, reviewer Important 1) — same
# hazard-column reasoning as `transactions.tags`/`merged_from` above: it stays NOT NULL as the
# query surface, `payload` (once this table also carries one — task 3) is where a genuinely
# absent key round-trips.
#
# ABSORB NOTE (task 3, FIX ROUND 1 reviewer Important 2): 86 of 87 real statement rows carry
# the batch linkage under the DEAD key `import_batch_ids` (renamed long ago, never backfilled)
# — the absorb must read `row.get('batch_ids') or row.get('import_batch_ids') or []` into this
# promoted column (input normalization at promotion time; `payload` stays verbatim under
# whichever key the source row actually used). A naive `row.get('batch_ids') or []` default
# would silently discard real linkage for 86/87 rows.
statements_meta = Table(
    "statements_meta",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("title", Text, nullable=True),  # a name the user gave it in Documents (202609271900)
    Column("note", Text, nullable=True),  # the user's note on it in Documents (202609281200)
    Column("tags", JSON, nullable=True),  # the user's tags on it in Documents (202609281435)
    Column("mime", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("file_hash", Text, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("kind", Text, nullable=False),  # upload kind: "text" | "pdf_pages" | "image"
    Column("account_id", Text, nullable=True),  # account binding — no FK, not in the brief's list
    Column("parsed_at", Text, nullable=True),
    # B20: parsing | ready | failed | interrupted — NULL = legacy/pre-B20 — see comment above
    Column("status", Text, nullable=True),
    Column("meta_unavailable", Integer, nullable=False, server_default=text("0")),
    Column("import_meta", JSON, nullable=True),
    Column("batch_ids", JSON, nullable=False, server_default=text("'[]'")),
    # revision 0046: replaces the earlier `parsed_rows_path` (Text) — see the comment above.
    Column("has_parsed_rows", Integer, nullable=False, server_default=text("0")),
    Column("payload", JSON, nullable=False),  # verbatim row — see payload/parsed_rows split above
    CheckConstraint(
        "meta_unavailable IN (0,1)", name=conv("ck_statements_meta_meta_unavailable_bool")
    ),
    CheckConstraint(
        "has_parsed_rows IN (0,1)", name=conv("ck_statements_meta_has_parsed_rows_bool")
    ),
    Index("ix_statements_meta_book_seq", "book_id", "seq", unique=True),
    Index("ix_statements_meta_book_account", "book_id", "account_id"),
    Index("ix_statements_meta_book_file_hash", "book_id", "file_hash"),
)

# B20 (revision 202609091200) — the parse_progress registry's persistence hook lands here, NOT
# on `statements_meta` (see that table's own comment): a narrow one-row-per-statement side
# table, upserted in place (never grows — the SAME row is overwritten on every stage/page), so
# a multi-minute scan's ~10 progress writes never pay `StatementStore.update_by_id`'s full
# read/reinline/re-extract-parsed-rows/write cycle. `ON DELETE CASCADE` — deleting a statement
# drops its progress row for free, mirroring `evidence_subjects`' identical
# id-as-primary-key-and-FK shape (this file, further down).
# Checkpoint-EXCLUDED (`ai.checkpoint._EXCLUDED_TABLES`) and absent from
# `db.portable_bridge.BRIDGED` — a derived, ephemeral cache with no rewind/export role, same
# precedent as `price_points`/`evidence`/`asset_brief`.
statement_parse_progress = Table(
    "statement_parse_progress",
    metadata,
    Column(
        "statement_id", Text, ForeignKey("statements_meta.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("progress", JSON, nullable=False),
    Column("updated_at", Text, nullable=False),
)

# Tier-2-plus, same pattern. `id_field` is `absorbed_id` (db/merge_journal_store.py's own
# JsonStore id_field, not "id"). The store today has NO query/filter methods beyond
# find-by-absorbed-id (`pop`) and upsert (`record`) — `canonical_id` is promoted anyway as the
# natural secondary lookup key (a future "merges that absorbed into X" query, or an undo-history
# view), at negligible cost.
merge_journal = Table(
    "merge_journal",
    metadata,
    Column("absorbed_id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("canonical_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("payload", JSON, nullable=False),
    Index("ix_merge_journal_book_seq", "book_id", "seq", unique=True),
    Index("ix_merge_journal_book_canonical", "book_id", "canonical_id"),
)

# ── Tier 2: evidence (world data — NO book_id) ────────────────────────────────
# payload is the VERBATIM row (source of truth; round-trip export reads only it).
# expires_at / domain / subjects are DERIVED indexes over it, recomputed on every
# write. seq preserves the original file's insertion order portably (assigned
# explicitly — SQLite has no non-PK autoincrement, so no dialect feature is used).
evidence = Table(
    "evidence",
    metadata,
    Column("id", Text, primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("expires_at", Text, nullable=False, server_default=""),
    Column("domain", Text, nullable=False, server_default=""),
    Column("created_at", Text, nullable=False, server_default=""),
    # Generic JSON → Postgres `json` (NOT jsonb) — DELIBERATE: payload is verbatim
    # source-of-truth we never index into; jsonb normalizes key order, against
    # round-trip fidelity. Ruled 2026-08-07 (task 6 review → task 9).
    Column("payload", JSON, nullable=False),
    Index("ix_evidence_expires_at", "expires_at"),
    Index("ix_evidence_seq", "seq", unique=True),
)

evidence_subjects = Table(
    "evidence_subjects",
    metadata,
    Column("evidence_id", Text, ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
    Column("subject", Text, primary_key=True),
    Index("ix_evidence_subjects_subject", "subject"),
)

# ── Cutover ledger (spec D6): which JSON files this database has absorbed ─────
# The import revision INSERTs a row (including the source file's sha256) inside
# its transaction; the migrate wrapper renames the source file AFTER `upgrade
# head` fully succeeds — ONLY if the bytes still hash the same (a mismatch means
# something wrote to the file after absorption → fail closed, never rename).
# Crash-safe: rename is idempotent on next boot; a restored-from-backup DB has
# no row, so no rename.
json_cutovers = Table(
    "json_cutovers",
    metadata,
    Column("store", Text, primary_key=True),
    Column("source_path", Text, nullable=False),
    Column("source_hash", Text, nullable=False, server_default=""),
    Column("migrated_at", Text, nullable=False),
    Column("renamed", Integer, nullable=False, server_default="0"),
)

# ── Checkpoint machinery (spec D7): row-level before-images for DB stores ─────
# Replaces the whole-file blob pool for DB-backed stores (ai/checkpoint.py):
# CheckpointContext.record_db_before writes one row per (checkpoint_id, table_name,
# pk) the first time a turn touches it, holding the BEFORE image (None = the row did
# not exist pre-turn). Restore replays these in reverse `seq` order inside one
# transaction — O(rows changed), not O(file size). `pk` and `before_payload` are
# opaque to this table; ai/checkpoint.py owns interpretation and exclusion policy.
write_log = Table(
    "write_log",
    metadata,
    Column("checkpoint_id", Text, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("table_name", Text, nullable=False),
    Column("pk", Text, nullable=False),
    Column("before_payload", JSON, nullable=True),  # None = row did not exist pre-turn
    Column("created_at", Text, nullable=False, server_default=""),
    Index("ix_write_log_checkpoint", "checkpoint_id"),
)

# ── Tier 2: reference data (world/market data — NO book_id) ───────────────────
# One (instrument, day) daily close per row. Composite PK = the store's "one point per
# day, last write wins" invariant, now DB-enforced (a write is a delete+insert on the
# same (price_source_id, date), never a duplicate row). `Float` — spec D5: money stays
# as-stored in the first cut; prices are floats today, like the JSON series they replace.
# Excluded from checkpoints (ai/checkpoint.py::_EXCLUDED_TABLES) — parity with the old
# store, whose `_save` never fired the file write-hook either (append-only cache data).
price_points = Table(
    "price_points",
    metadata,
    Column("price_source_id", Text, primary_key=True),
    Column("date", Text, primary_key=True),
    Column("price", Float, nullable=False),
    Column("source", Text, nullable=False, server_default=""),
    Index("ix_price_points_date", "date"),
)

# ── settings: a small table keyed by namespace (spec:205) — plan 4d, task 1. ONE row per
# namespace; payload = the whole config dict verbatim. NO book_id (Tier-3 installation
# settings — not book- or user-scoped). Holds the two non-secret singleton configs
# (memory_settings, preferred_models) today, read/written via
# `backend.db.payload_store.NamespacedSettingsStore`; a later task adds every
# connector/model config namespace (plaid, telegram, model, …) as more rows in this SAME
# table. Defined HERE — before the `from backend.db.payload_store import ...` line below —
# rather than alongside its sibling config tables further down the file, because
# `NamespacedSettingsStore` lives in payload_store.py (which THIS file imports below) and
# needs `settings` already bound on this module by the time that import runs, for the exact
# same reason `metadata` itself must be defined before that same line (see its own comment).
settings = Table(
    "settings",
    metadata,
    Column("namespace", Text, primary_key=True),
    Column("payload", JSON, nullable=False),
)

# ── Tier 2: instrument master, via the generic PayloadTableStore shape ────────
# The first store to ride `payload_table()` (plan 2, task 5) rather than a hand-rolled
# Table(...) — its rows are a plain id-keyed record with no derived/indexed column
# (unlike evidence's expires_at/domain), so the generic seq/id/payload shape is an exact
# fit. Import placed HERE (not at module top) and deliberately AFTER `metadata` is
# defined: `payload_store.py` does `from backend.db.tables import metadata` at its own
# top, so this is a genuine two-way reference the shared `metadata` object closes, not a
# layering violation (both live in backend.db) — but the import order is load-bearing.
# Whichever of the two modules starts executing first, its OWN `metadata = MetaData(...)`
# (this line) or `from backend.db.tables import metadata` (payload_store's) must resolve
# before the OTHER module is asked for a name it hasn't defined yet, or the circular
# import deadlocks. Concretely: this file's `metadata` must already be bound (it is —
# see the top of this file) before this import line runs, so a fresh `import
# backend.db.payload_store` anywhere in the app safely nests back into this already-
# mid-init module and reads `metadata` off it directly. See payload_store.py's own
# docstring for the reverse direction. E402 (import not at top) is repo-wide ignored
# (pyproject.toml) for exactly this "late, deliberate" shape.
from backend.db.payload_store import book_payload_table, payload_table

instruments = payload_table("instruments")

# ── Tier-1 planning stores (plan 4a) — book-scoped flat-list shape (book_id/seq/id/payload).
# book_id per spec:137-139/445 (planning IS Tier-1). NO cross-table FK BETWEEN planning tables
# (categories/envelopes/groups referenced by OPAQUE id only — loose coupling is deliberate; the
# only FK is book_id -> books.id, matching the money core).
envelope_groups = book_payload_table("envelope_groups")
categories = book_payload_table("categories")
commitments = book_payload_table("commitments")
envelopes = book_payload_table("envelopes")
income_events = book_payload_table("income_events")
recurring_streams = book_payload_table("recurring_streams")
targets = book_payload_table("targets")
corrections = book_payload_table("corrections")
match_decisions = book_payload_table("match_decisions")
match_reviews = book_payload_table("match_reviews")
rejected_identities = book_payload_table("rejected_identities")

# owned_assets / owned_asset_categories: the Assets tab domain (ADR 2026-09-06-assets-tab-
# faithful-port) — things you own that are valued by a dated manual mark, not a ledger balance
# or a price feed (property, vehicles, watches, art, precious metals, cash-value policies, and
# user-created categories). Tier-1 (book_id/seq/id/payload) for the same reason planning is:
# this is money-adjacent state scoped to the one book. NO FK to accounts/instruments —
# `loan_account_id` on an asset row is an opaque reference, resolved by the service, matching
# the loose-coupling convention every other Tier-1 store here already follows.
owned_assets = book_payload_table("owned_assets")
owned_asset_categories = book_payload_table("owned_asset_categories")

# budget_assignments: the ONE planning store whose money is a promoted typed column. amount is
# Text holding repr(float)-exact decimal (revision 0008 pattern) — NEVER Float. id is the
# deterministic surrogate f"{book_id}:{month}:{category}" (all three components colon-free:
# book_000000000000 / YYYY-MM / an env_/cat_ opaque id) — book-prefixed so two books can never
# collide on the same (month, category) PK. No seq: a keyed relation has no insertion order.
budget_assignments = Table(
    "budget_assignments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("month", Text, nullable=False),  # "YYYY-MM"
    Column("category", Text, nullable=False),  # envelope-id-native key
    Column("amount", Text, nullable=False),  # repr(float)-exact decimal string
    Index("ix_budget_assignments_book_month", "book_id", "month"),  # book-leading, month_map lookup
)

# budget_engine_state: the DEFAULT home for a pluggable budget engine's OWN configuration —
# one row per (book_id, engine_id), added for the multi-lens budgeting design (.claude/rfcs/
# 2026-08-22-pluggable-budget-engines.md, Decision A/E). Switching the "home" lens must never
# touch another engine's state, so each engine either keeps its existing bespoke store
# (zero_based: envelopes/budget_assignments/profile.category_targets; a hypothetical future
# category-caps engine: profile.monthly_budgets — NEITHER migrates here) or, if it owns no
# bespoke store, lands a row here. The 50/30/20 engine (id "proportional") is this table's own
# consumer — payload (2026-09-25, the envelope-based redesign) = {"targets": {needs, wants},
# "mapping": {category_id: bucket} (v1, read-only compat), "buckets": {envelope_id: bucket},
# "caps": {envelope_id: [[month, amount], ...]}, "income_category_ids": [category_id,...] |
# null} — but this table itself stays engine-agnostic, payload is opaque JSON, never inspected
# by this layer.
# id is the deterministic, book-prefixed surrogate f"{book_id}:{engine_id}" (both components
# colon-free) — budget_assignments' exact pattern, for the exact same reason: two books can
# never collide on the same engine's row, and a keyed relation has no insertion order (no seq).
# payload stays RAW JSON, never promoted to a decimal-Text column: target percentages, a
# category/envelope-to-bucket mapping, and a cap history are configuration, not ledger money
# summed into net worth — the same plan_state/profile precedent below. budget_assignments
# remains the only money-promoted planning store.
budget_engine_state = Table(
    "budget_engine_state",
    metadata,
    Column("id", Text, primary_key=True),
    Column("book_id", Text, ForeignKey("books.id"), nullable=False),
    Column("engine_id", Text, nullable=False),
    Column("payload", JSON, nullable=False),
    Index("ix_budget_engine_state_book_id", "book_id"),
)

# plan_state: per-book singleton. PK IS book_id (one row per book); payload = the whole
# {scenarios, baseline} dict verbatim. FIRE projection inputs are NOT ledger money (never summed
# into net worth), so amount fields stay raw in payload — no decimal-Text treatment.
plan_state = Table(
    "plan_state",
    metadata,
    Column("book_id", Text, ForeignKey("books.id"), primary_key=True),
    Column("payload", JSON, nullable=False),
)

# profile: per-book singleton — the owner's financial profile (base_currency, risk prefs, FIRE
# inputs, liabilities, briefing_preferences, monthly_budgets, category_targets/notes). PK IS
# book_id (one row per book); payload = the whole profile dict verbatim. Not a planning store —
# grouped here as a book-scoped singleton alongside plan_state (its exact shape). Profile amounts
# are user-entered projection/config inputs, NOT ledger money summed into net worth, so they stay
# raw in payload — no decimal-Text promotion (plan_state's rule). Checkpoint-INCLUDED + portable
# (durable user config). Wave 5 (JSON->DB) — replaces profile.json.
profile = Table(
    "profile",
    metadata,
    Column("book_id", Text, ForeignKey("books.id"), primary_key=True),
    Column("payload", JSON, nullable=False),
)

# ── Tier-1 investment stores (plan 4b) — book-scoped flat-list shape (book_id/seq/id/payload).
# book_id per spec:135-137 (investments ARE Tier-1). NO cross-table FK BETWEEN investment tables
# (lot.position_id / realized.consumptions[].lot_id / vest.grant_id / position.instrument_id are
# OPAQUE ids Python-filtered over all(), never a SQL JOIN — loose coupling is deliberate, exactly
# like planning; the only FK on each is book_id -> books.id). Money lives IN payload on every one
# (cost_basis, unit_cost, strike, proceeds, realized_gain, fmv) — exact via JSON round-trip, no
# promoted decimal-Text column this wave (see money_io._money_text's payload rule).
positions = book_payload_table("positions")
lots = book_payload_table("lots")
grants = book_payload_table("grants")
vest_events = book_payload_table("vest_events")
option_contracts = book_payload_table("option_contracts")
realized_events = book_payload_table("realized_events")

# watchlists: per-book singleton. PK IS book_id (one row per book); payload = the whole
# {watchlists, items, global_config} dict verbatim (the store hand-rolls every method over
# _get_raw/_save_raw, so it never used the flat-list surface — plan_state's exact pattern).
# No money; price thresholds/config only. Tier-1 (spec lists "watchlists") -> book_id-keyed.
watchlists = Table(
    "watchlists",
    metadata,
    Column("book_id", Text, ForeignKey("books.id"), primary_key=True),
    Column("payload", JSON, nullable=False),
)

# asset_brief: per-instrument synthesized-brief CACHE (checkpoint-EXCLUDED, non-portable, like
# evidence/price_history). NOT Tier-1 -> plain payload_table (NO book_id). PK is instrument_id.
asset_brief = payload_table("asset_brief")
# price_movement_notes: per-(instrument, session) "Notable Price Movement" CACHE — deterministic
# session facts (close, prev close, after-hours print, latest intraday observation) plus the
# LLM's cited explanation of the move. Derived + rebuildable (bars from the price provider,
# evidence from the `evidence` table), so checkpoint-EXCLUDED and non-portable exactly like
# `asset_brief`. id = "{instrument_id}:{session_date}" so one prefix scan lists a ticker's days.
price_movement_notes = payload_table("price_movement_notes")

# ── Tier-2 logs/telemetry stores (plan 4c) — plain flat-list shape (seq/id/payload), NO
# book_id: operational/runtime logs (automation execution records, scheduler queues,
# telemetry rings), not book-scoped user data — spec:196-197 names this tier's append-only-
# log membership explicitly. automation_runs is the ONE exception on the PORTABILITY/
# CHECKPOINT axis (checkpoint-INCLUDED, unlike its 8 siblings — see ai/checkpoint.py's
# _EXCLUDED_TABLES) — Tier and portability are independent questions; it still carries NO
# book_id.
automation_runs = payload_table("automation_runs")
automation_runtime = payload_table("automation_runtime")
batch_ledger = payload_table("batch_ledger")
mcp_write_log = payload_table("mcp_write_log")
model_telemetry = payload_table("model_telemetry")
pending_analysis = payload_table("pending_analysis")
run_history = payload_table("run_history")
tool_call_log = payload_table("tool_call_log")

# ── Wave 5B (JSON->DB) — 4 more Tier-2 logs/telemetry stores, same shape/reasoning as
# the nine above (plain flat-list seq/id/payload, NO book_id — operational/runtime logs,
# not book-scoped user data). cost_log_daily is dict-shaped like model_telemetry (id =
# the "YYYY-MM-DD|model|source" bucket key, never echoed inside its own payload) — same
# generic table, a hand-rolled store. agent_findings is VESTIGIAL: the Agent product (and
# its own AgentFindingStore) was retired in d0da1a09 ("feat!: retire the Agent product")
# — nothing reads or writes a finding anymore, but its 200-row JSON file is real
# historical data the cutover absorbs rather than silently drops.
tool_discovery_log = payload_table("tool_discovery_log")
cost_log = payload_table("cost_log")
cost_log_daily = payload_table("cost_log_daily")
agent_findings = payload_table("agent_findings")

# ── sql_query_log (revision 0050) — the audit ring for Lucius's read-only SQL access
# (docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md). DB-native: no `.json`
# ancestor, no cutover. Same plain flat-list seq/id/payload shape and same Tier-2
# classification as the thirteen logs above (NO book_id — an operational record of what was
# asked of the database, not book-scoped user data): checkpoint-EXCLUDED, and — mirroring
# `tool_call_log` exactly — NEITHER a `portable_bridge` BRIDGED entry NOR a `_WIPE_FNS` one,
# because a runtime log has no export or reset role.
#
# UNLIKE `tool_call_log`, whose store docstring pins "SIZES AND SHAPES ONLY — never raw
# arguments", this ring stores the SQL TEXT VERBATIM (capped — see sql_query_log_store.
# MAX_SQL_CHARS). That is a deliberate departure, not an oversight: for a telemetry ring the
# arguments are incidental, but here the statement IS the auditable action — the same reason
# `mcp_write_log` keeps the real call rather than its length. It is reachable only through an
# auth-gated endpoint that sets `Cache-Control: no-store`.
sql_query_log = payload_table("sql_query_log")

# ── Tier-1 config stores (plan 4d, task 1) — book-scoped flat-list shape, spec:139-140 lists
# payees/automations/alerts. NO cross-table FK between config tables — the only FK anywhere
# in this block is book_id -> books.id, matching every prior wave.
payees = book_payload_table("payees")
automations = book_payload_table("automations")
alerts = book_payload_table("alerts")

# dashboard_layouts: per-book singleton (plan_state/watchlists pattern) — spec lists it
# Tier-1. PK IS book_id (one row per book); payload = the whole {activeViewId, views} dict.
dashboard_layouts = Table(
    "dashboard_layouts",
    metadata,
    Column("book_id", Text, ForeignKey("books.id"), primary_key=True),
    Column("payload", JSON, nullable=False),
)

# ── memory: user_id-scoped (spec:276), NOT book_id — plain payload_table today (single-user
# install, no user_id column yet); family mode adds a user_id column in a LATER migration,
# never book_id — a memory fact is a per-USER relationship with Lucius, not a per-book
# ledger fact.
memory = payload_table("memory")

# ── Tier-3 config stores (plan 4d, task 1) — plain flat-list shape, NO book_id:
# installation-wide config/reference rows, not book-scoped user data.
news_sources = payload_table("news_sources")
statement_formats = payload_table("statement_formats")
notification_log = payload_table("notification_log")  # runtime cache — checkpoint-EXCLUDED
desktop_access_grants = payload_table("desktop_access_grants")
desktop_ingest_jobs = payload_table("desktop_ingest_jobs")
# One row per Import / Verify / Preview run of a provider's importer (services/import_runs.py):
# the stored report the Imports & sync screen reads. The columns are what the history list and the
# polls look up; `detail` holds the heavy parts (log, counts, problems, preview, verification) that
# only a run opened as a report reads. No credential in it. Checkpoint-EXCLUDED, non-restorable,
# wiped by Reset.
import_runs = Table(
    "import_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("provider", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text, nullable=True),
    Column("plan_id", Text, nullable=False),
    Column("plan_name", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("archived_at", Text, nullable=True),
    Column("phase", Text, nullable=False),
    Column("progress", JSON, nullable=False),
    Column("error", Text, nullable=True),
    Column("detail", JSON, nullable=False),
    Index("ix_import_runs_provider_started", "provider", "started_at"),
    Index("ix_import_runs_status", "status"),
)
# ── intel_stories / signal_timeline_entries / daily_digests (RFC intel-signals-data-model)
# Split of the old multi-writer `intel_signals` (stories + digests): three shapes shared one
# table with a name collision (`summary` meant two different things) and an unbounded JSON
# `timeline` list nothing indexed. `intel_stories` keeps the 7 real story types (asset/topic/
# risk/opportunity/macro/monitoring/price_alert) — `daily_digest` was structurally foreign
# (no ticker/timeline, a different id shape) and gets its own table. Promoted columns +
# verbatim `payload`, matching `accounts`/`evidence`'s own precedent (real per-type extra
# fields, e.g. `risk` rows carry `status_reason` with no equivalent on any other type —
# promoting every such field would be guessing past what's actually used).
#
# `instrument_id` (NEW) is the identity column going forward — `id`/`ticker`-embedded ids
# (`crash-watch-<ticker>`, `price-alert-<ticker>-...`, `news-<ticker>`) are a known-bad
# pattern the migration resolves where it can; `ticker` stays for DISPLAY/query-building
# only, never identity (a renamed/delisted ticker must not silently orphan a story's
# history). No `book_id` — unchanged Tier-2/3 classification, matching old `intel_signals`.
intel_stories = Table(
    "intel_stories",
    metadata,
    Column("id", Text, primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("instrument_id", Text, nullable=True),  # NO FK — opaque id, like positions/lots
    Column("ticker", Text, nullable=True),
    Column("label", Text, nullable=False),
    Column("status", Text, nullable=False),
    # Renamed from `summary` — the old table's ONE most-reported name collision: the story's
    # own running-state field and a timeline entry's per-development field shared the exact
    # same key, different scope, same function (`analyst._update_signal`). `current_summary`
    # reads correctly next to `signal_timeline_entries.summary` ("this entry's own summary")
    # with no shared-prefix collision in either direction.
    Column("current_summary", Text, nullable=False, server_default=text("''")),
    Column("portfolio_impact", Text, nullable=False, server_default=text("''")),
    Column("action_items", JSON, nullable=False, server_default=text("'[]'")),
    Column("affected_holdings", JSON, nullable=True),  # macro-only; computed, never LLM-authored
    Column("analyzed", Integer, nullable=False, server_default=text("0")),
    Column("read", Integer, nullable=False, server_default=text("0")),
    Column("dismissed", Integer, nullable=False, server_default=text("0")),
    Column("resolved", Integer, nullable=False, server_default=text("0")),
    Column("resolved_reason", Text, nullable=True),
    Column("archived", Integer, nullable=False, server_default=text("0")),
    Column("evidence_watermark", Text, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("payload", JSON, nullable=False),  # verbatim row (round-trip source of truth)
    CheckConstraint(
        "type IN ('asset','topic','risk','opportunity','macro','monitoring','price_alert')",
        name=conv("ck_intel_stories_type"),
    ),
    CheckConstraint("analyzed IN (0,1)", name=conv("ck_intel_stories_analyzed_bool")),
    CheckConstraint("read IN (0,1)", name=conv("ck_intel_stories_read_bool")),
    CheckConstraint("dismissed IN (0,1)", name=conv("ck_intel_stories_dismissed_bool")),
    CheckConstraint("resolved IN (0,1)", name=conv("ck_intel_stories_resolved_bool")),
    CheckConstraint("archived IN (0,1)", name=conv("ck_intel_stories_archived_bool")),
    Index("ix_intel_stories_seq", "seq", unique=True),
    Index("ix_intel_stories_type", "type"),
    Index("ix_intel_stories_instrument", "instrument_id"),
    Index("ix_intel_stories_status", "status"),
)

# `timeline[]` promoted to a child table (spec D4/D5 precedent — see `transaction_splits`
# above): the old JSON list was unbounded (F11 fix removed the storage-side truncation) and
# nothing indexed into it. Direct structural copy of `session_messages`' shape (migration
# 202608241937) WITH one deliberate difference: `session_messages` uses a DB-assigned
# `INTEGER PRIMARY KEY` alone because nothing outside that table ever references a message's
# identity. A timeline entry is different — `duplicate_of_id`/`contradicts_id`/`supersedes`
# (ADR 2026-08-28-sourced-and-corroborated-intel-signal-updates) are STABLE STRING IDS the
# model itself references across turns (`_find_timeline_entry(timeline, entry_id)`). So this
# table carries BOTH: a DB-assigned `seq` (ordering, race-free, session_messages' own
# reasoning verbatim) AND the existing `uuid4().hex` `id` (business identity, UNIQUE-indexed,
# what a `duplicate_of_id` reference resolves against).
signal_timeline_entries = Table(
    "signal_timeline_entries",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # SQLite rowid alias
    Column("id", Text, nullable=False),  # business identity — uuid4().hex, UNIQUE below
    Column("signal_id", Text, ForeignKey("intel_stories.id", ondelete="CASCADE"), nullable=False),
    Column("date", Text, nullable=False),
    Column("title", Text, nullable=False, server_default=text("''")),
    Column("summary", Text, nullable=False, server_default=text("''")),  # ONE dated development
    Column("sources", JSON, nullable=False, server_default=text("'[]'")),
    Column("superseded", Integer, nullable=False, server_default=text("0")),
    Column("supersedes", Text, nullable=True),  # this entry CONFIRMED-reversed that entry id
    Column("contradicts", Text, nullable=True),  # conflicts with that entry id, UNCONFIRMED
    Column("status", Text, nullable=True),  # 'flagged' when contradicts is set, unconfirmed
    Column("confirmed_at", Text, nullable=True),  # freshest supporting evidence timestamp (F3)
    Column("payload", JSON, nullable=False),  # verbatim entry dict
    CheckConstraint("superseded IN (0,1)", name=conv("ck_signal_timeline_entries_superseded_bool")),
    Index("ix_signal_timeline_entries_signal_seq", "signal_id", "seq"),
    Index("ix_signal_timeline_entries_id", "id", unique=True),
)

# `daily_digest`-typed rows, structurally foreign to `intel_stories` (no ticker/timeline, a
# different id shape: `daily-digest-<date>`, always upserted never a list). `date` is the
# LITERAL primary key (not a synthetic id) — `_write_inapp_digest` already treats the date as
# the real key. One real, pre-existing behavior this migration preserves rather than silently
# fixes: `run_eod_digest`/`run_premarket_digest` both write with the identical id shape, so on
# a day both fire, the later write overwrites the earlier one — only the day's LAST digest
# survives today. A literal date PK reproduces that exact behavior with an honest storage
# shape, not a silent fix.
#
# WIPE-ONLY, NOT bridged (deliberate policy change, called out not silent — same
# classification as `notification_log`/`tool_grants`'s "a signal is a disposable notification
# ring"): a delivered-digest record is regenerable notification history, not a book fact; it
# only ever lived inside `intel_signals.json` because it was never given its own file.
# Checkpoint inclusion is UNCHANGED (still checkpoint-included) — only export/backup-bundle
# membership narrows.
daily_digests = Table(
    "daily_digests",
    metadata,
    Column("date", Text, primary_key=True),  # 'YYYY-MM-DD'
    Column("created_at", Text, nullable=False),
    # 'eod' | 'premarket' | 'scheduled'. 'scheduled' is a REAL-DATA finding, not the RFC's
    # assumed enum: `monitoring/digest.py::_build_and_deliver(trigger)` receives "eod"/
    # "premarket" but its call to `_write_inapp_digest(digest, message)` never forwards
    # `trigger` — every digest row `_write_inapp_digest` has ever written stores the literal
    # "scheduled" regardless of which caller fired it. That gap is a pre-existing bug in
    # `digest.py` (unrelated to this migration, out of scope here — `monitoring/` writer
    # call sites are a later task) — this CHECK accepts what real rows actually carry today
    # rather than rejecting them on migrate.
    Column("trigger", Text, nullable=False),
    Column("severity", Text, nullable=False, server_default=text("'info'")),
    Column("title", Text, nullable=False, server_default=text("'Daily Digest'")),
    # Renamed from `summary` — same collision-avoidance reasoning as
    # `intel_stories.current_summary`; this table must not reintroduce the exact anti-pattern
    # it exists to help fix, even brand new.
    Column("message", Text, nullable=False, server_default=text("''")),
    Column("affected_tickers", JSON, nullable=False, server_default=text("'[]'")),
    Column("sources", JSON, nullable=False, server_default=text("'[]'")),
    Column("source_urls", JSON, nullable=False, server_default=text("'[]'")),
    Column("read", Integer, nullable=False, server_default=text("0")),
    # verbatim row, incl. period/pl_base/pl_pct — not promoted, nothing queries by them; a
    # rendered snapshot, not ledger money summed into net worth (matches plan_state's own
    # "stays raw" precedent).
    Column("payload", JSON, nullable=False),
    CheckConstraint(
        "trigger IN ('eod','premarket','scheduled')", name=conv("ck_daily_digests_trigger")
    ),
    CheckConstraint("read IN (0,1)", name=conv("ck_daily_digests_read_bool")),
)

# ── Tier-2 misc stores (plan 4e, task 1) — plain flat-list shape (seq/id/payload), NO
# book_id: none of these four is book/money-scoped (session-scoped chat artifacts, a
# user's own attachment catalog, global email provenance, a global runtime retry queue).
# artifacts/attachments are HYBRID (spec D5, Tier-4 "files stay files"): this table carries
# only the METADATA catalog — the document/parsed-payload BYTES stay files under
# ARTIFACTS_DIR/ATTACHMENTS_DIR, unchanged by this wave, already written file-direct via
# atomic_write_text (never through a store). email_snapshots carries every field inline
# (no blob dir) — id_field is "message_id", mapped onto the generic "id" column.
#
# `attachments` was `uploads` through plan 4e — renamed (revision 0041,
# cross-channel-attachments RFC Phase 2 / ADR 2026-08-16-attachment-store-replaces-
# upload-store) to carry the durable cross-channel record (source_channel/
# source_conversation_id/content_hash/media_type/blob_locator) every chat channel now
# writes before the turn, not only the web upload endpoint.
artifacts = payload_table("artifacts")
attachments = payload_table("attachments")

# Transaction receipts (cross-channel-attachments RFC Phase 4, items 23/24; revision 0042
# — see docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md "Transaction
# receipts"). Same HYBRID shape as `attachments`/`artifacts` above: this table is the
# METADATA catalog only, original bytes stay files under RECEIPTS_DIR. NO book_id — a
# receipt catalog entry, like an attachment, is not itself book-scoped. Deliberately a
# BARE `payload_table()` (no promoted `content_hash`/`source_attachment_id` columns): the
# scale here is a handful to a few hundred rows, ``query()``'s Python-side filter is the
# documented default for exactly that size (payload_store.py's own module docstring), and
# staying schemaless keeps this table a drop-in `misc_io.import_payload_list`/
# `export_payload_list` participant for the portable bridge with zero bespoke plumbing.
#
# `transaction_receipts` is the many-to-many link table (transaction_id, receipt_id,
# linked_at, audit_context) — a transaction may cite zero or more receipts, and one
# receipt (e.g. a single itemized bill) may support more than one transaction, so this is
# a genuine link table, not a foreign key on either side (RFC section 2.2/ADR "Transaction
# receipts" #5). Same bare `payload_table()` shape as `receipts` for the same reason — no
# promoted `transaction_id`/`receipt_id` columns, so `TransactionReceiptStore.by_transaction`/
# `by_receipt` filter Python-side over `query()`; a future hot-path lookup earns its own
# indexed column then (payload_store.py's own documented escape hatch), not preemptively.
# Idempotent attach (the same receipt linked twice to the same transaction is a no-op) is
# enforced at the SERVICE layer (`services.receipts.apply_receipt_change`), matching how
# `StatementStore.create`'s content-hash dedup is Python-level rather than a DB constraint.
receipts = payload_table("receipts")
transaction_receipts = payload_table("transaction_receipts")

email_snapshots = payload_table("email_snapshots")

# Connector (bank/broker) provenance snapshots — the RAW row a provider fetch returned,
# keyed by "<source>:<ref>" (services/connector_snapshots.py, RFC connectors-as-data-sources
# §C). A DELIBERATE SIBLING of email_snapshots, not a shared table (owner decision, RFC
# alternatives): email keeps its legacy-key/first-seen-wins quirks, this one hashes the
# normalized fields on every stash and reports a change when a later fetch disagrees with
# what it stored before. Same shape/posture as email_snapshots — checkpoint-INCLUDED
# (durable user evidence, not a disposable cache) and portable (db/portable_bridge.py).
connector_snapshots = payload_table("connector_snapshots")

pending_pushes = payload_table(
    "pending_pushes"
)  # checkpointed=False at the store — see pending_push_store.py

# checkpoints: the turn-checkpoint INDEX itself (spec D7) — SCHEMA-ONLY this task (plan
# 4e task 1); its store class (CheckpointIndexStore) and absorb (revision 0019) are plan
# 4e task 2, not this one — front-loaded here mirroring Wave A task 1's own precedent of
# defining a whole wave's DDL before every store class exists. Self-excluded from
# checkpointing (a checkpoint table must never checkpoint itself, wired in task 2) — the
# gzip blob pool for Tier-4 file snapshots (CHECKPOINTS_BLOBS) is unrelated and stays a
# file dir.
checkpoints = payload_table("checkpoints")

# ── Wave 5C (JSON->DB) — runtime queues/state ─────────────────────────────────

# runtime_state: the checkpoint-EXCLUDED counterpart to `settings` above — same
# namespace-keyed singleton shape (ONE row per namespace, payload verbatim), but for
# runtime state that must NOT survive a /rewind and must NOT travel in an export bundle
# (`settings`' two current namespaces, memory_settings/preferred_models, are durable user
# config and stay checkpoint-INCLUDED — the opposite of what harness_state needs, so it
# cannot share that table). Holds harness_state (the self-harness cooldown watermark,
# Wave 5C) and — Wave 5D2a — `app_config` (instance branding + feature flags, NOT a
# secret; landed here rather than `secret_config` for exactly that reason); a later
# checkpoint-excluded singleton config lands here as one more namespace row, exactly
# how `settings` itself grew from one occupant to many.
runtime_state = Table(
    "runtime_state",
    metadata,
    Column("namespace", Text, primary_key=True),
    Column("payload", JSON, nullable=False),
)

# skills_state: runtime state for the skill registry (db/skill_store.py), keyed by skill
# id — dict-shaped like model_telemetry/cost_log_daily (id = skill id, payload =
# {enabled, author, created_at, updated_at, invocation_count} verbatim, no "id" key
# inside it). Checkpoint-INCLUDED + portable (durable per-skill config paired with the
# skill CONTENT under SKILLS_DIR, which stays files) — unlike model_telemetry/
# cost_log_daily, which are checkpoint-EXCLUDED telemetry.
skills_state = payload_table("skills_state")

# subscriptions: VESTIGIAL — the subscription-detection feature this backed was dropped
# ("moved to payee/automation detection") well before this wave; zero live readers or
# writers (see docs/adr/2026-08-10-vestigial-subscriptions-absorbed-not-resurrected.md).
# Absorbs subscriptions.json's real historical rows (user dismiss/confirm overlay state
# on a detected group) byte-identically, mirroring agent_findings' own precedent exactly
# — generic payload_table shape, no book_id, checkpoint-INCLUDED (never excluded
# pre-migration either), no portable/bridge role (never portable pre-migration).
subscriptions = payload_table("subscriptions")

# actions: the property/commitment action log (services/actions.py) — flat list, no
# book_id. IDS ARE PLAIN INTEGERS (unlike every other payload_table store's string ids —
# confirmed by ai/tools/portfolio.py's own tool schema: "these ids are plain integers,
# not the xxx_<hex> ids other stores use"), so ActionStore (db/action_store.py)
# overrides _insert_row to str()-coerce the id COLUMN only — payload keeps the real JSON
# int verbatim, preserving round-trip fidelity. Checkpoint-INCLUDED + portable (was
# already reachable by /api/reset via core.portable.PORTABLE_FILES pre-migration).
actions = payload_table("actions")

# ── Wave 5D1 (JSON->DB) — secrets/config (security-review gate) ──────────────────────

# secret_config: the SECRET counterpart to `settings`/`runtime_state` — the SAME
# namespace-keyed singleton shape (ONE row per namespace, payload verbatim), for
# instance secrets that must NOT survive a /rewind or travel in an export bundle (like
# runtime_state's occupants) AND must ALSO never be handed to a caller in a sanitised
# backup download or come back from a `restore_backup` (core.sensitive.SECRET_TABLES /
# NON_RESTORABLE_TABLES) — NEITHER of which runtime_state's own occupant (harness_state,
# a mere cooldown timestamp) needs. Kept as its OWN table rather than one more
# runtime_state namespace precisely so SECRET_TABLES can target every secret-bearing row
# by TABLE NAME (the only granularity this codebase's backup/restore policy sets support)
# without also catching harness_state/app_config as collateral. Holds `auth` (password
# hash + session-signing HMAC key + API bearer token — read on every authenticated
# request) and `model_config` (per-provider LLM API keys). Connector credentials do not
# live here: they are rows of `connector_secrets` below (RFC connectors-table). A future
# secret singleton lands here as one more namespace row.
secret_config = Table(
    "secret_config",
    metadata,
    Column("namespace", Text, primary_key=True),
    Column("payload", JSON, nullable=False),
)

# connectors / connector_secrets: one row per connector (RFC connectors-table). `connectors`
# holds settings (`config`) and app-written state (`state`: last_synced_at/last_error, cursors,
# MCP discovery, price-source quota/circuit) — NEVER a credential, so it stays in a sanitised
# backup; but it carries raw server-chosen MCP text and personal settings, so it is SQL-DENIED
# to Lucius too (`db.sql_console`). `connector_secrets` holds the credentials, one row per
# connector that has any (including Telegram's chat->conversation map, the hidden secret
# `sessions`), and is the only table of the pair in `core.sensitive.SECRET_TABLES` (stripped
# from sanitised backups). Both are NON_RESTORABLE (a restore keeps the current connectors
# paired with their current secrets) and checkpoint-EXCLUDED (undoing a ledger write must never
# rewind a mailbox cursor or an OAuth token). Instance-global — no book_id, like
# `secret_config`. Ids are the connector ids used everywhere else (`plaid`, `email`,
# `web_search:tavily`, `mcp:<id>`) and are the join key for tool_permissions / chat_pairing /
# alert routing / accounts.external_ids, none of which hold a foreign key to this table.
connectors = Table(
    "connectors",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=false()),
    Column("config", JSON, nullable=False),
    Column("state", JSON, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("ix_connectors_kind", "kind"),
)

connector_secrets = Table(
    "connector_secrets",
    metadata,
    Column(
        "connector_id",
        Text,
        ForeignKey("connectors.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("secrets", JSON, nullable=False),
    Column("updated_at", Text, nullable=False),
)

# ── Wave 5D2b (JSON->DB) — permission/nonce machinery (security-review gate) ──────────
# pending_plans / chat_pairing (originally three — see the `mcp_pending_calls` note below):
# the generic (seq, id, payload) keyed-dict shape (payload_table(), skills_state's own
# precedent) — `id` is a TOKEN, a tool_use_id, or (chat_pairing) a
# "<connector>:<sender_id>" composite key, NEVER a field inside the payload dict itself. NO
# book_id — instance-local runtime queues, not book-scoped user data. Both are
# checkpoint-EXCLUDED (ai.checkpoint._policy._EXCLUDED_TABLES) AND NON_RESTORABLE
# (core.sensitive.NON_RESTORABLE_TABLES) — mirroring each one's own pre-existing
# *_JSON file-level status exactly (core.sensitive.NON_RESTORABLE_FILES, unchanged): a
# single-use token/nonce popped by a confirm/apply must not un-pop on a restore (the
# already-applied write would become replayable with no new confirm), and a stale pairing
# request must not un-expire. NOT SECRET — none of the three holds a bearer credential
# (pending_plans/mcp_pending_calls stage tool ARGS, which may carry sensitive financial
# figures but are not themselves a credential; chat_pairing holds a short human-readable
# pairing CODE, not a secret).
#
# `mcp_grant`/`code_grants`/`mcp_proposals` — the other three stores named in this wave's
# original scope (5C's own store inventory, carried forward unverified into 5D2's) — are
# DELIBERATELY ABSENT here: all three were already deleted from the codebase — store, API,
# service AND json file — well before this migration started. `mcp_grant`/`mcp_proposals`
# (the inbound-MCP T0/T1/T2 grant/proposal tier) went in 50626ce2 ("refactor(mcp): remove
# inbound authorization tiering", 2026-07-18) — that commit's own message: "the chmod-600
# test guarded mcp_grant.json, which held no secret" — confirming it was never a credential
# even when it existed. `code_grants` (run_code's per-conversation "Allow for this
# conversation" grant) went in f581b86d ("feat!: delete CodeGrantStore with the
# conversation grant tier", 2026-08-02), superseded by the durable
# `code_execution_auto_run` flag. There is nothing left for any of the three to migrate.
#
# `mcp_pending_calls` — the third table this wave originally created — is GONE (revision
# 0046, 2026-08-22): external-MCP's confirm→apply migrated off its own nonce-keyed queue
# onto `pending_plans` below, the SAME tool_use_id-keyed store every other gated tool
# already staged into (RFC 2026-07-22) — the double-confirm bug fix `apply_external_mcp_call`
# was also independently permission-gated on top of the proposal card it existed to
# redeem. See `backend/mcp/client_manager.py` and
# `docs/adr/2026-08-22-external-mcp-confirm-migrates-onto-generic-resolve.md`.
pending_plans = payload_table("pending_plans")
chat_pairing = payload_table("chat_pairing")

# ── Wave 5E (JSON->DB) — chat sessions ────────────────────────────────────────────────
# sessions: one row per chat session. Pre-migration this was FOUR file shapes under
# SESSIONS_DIR — {id}.json (transcript), {id}.fold.json, {id}.proactive.json and one
# shared index.json (the metadata list) — all collapsed into one row (backend/ai/
# assistant.py's "Disk persistence"/"fold sidecar"/"proactive sidecar"/"session index"
# sections are the only writer). NO book_id — a conversation belongs to the
# installation, not a book (matches memory/tool_call_log's own no-book_id reasoning).
#
# `meta` is the OLD index.json entry dict, VERBATIM (whatever subset of title/
# created_at/updated_at/message_count/source/model/extra_tool_groups/extra_tool_names/
# brief_instrument_id/brief_label/screen_handles/screen_commands/last_turn_status/
# last_turn_at/id a given session happens to carry — real data shows every session
# carries only a subset) — the round-trip source of truth, mirroring accounts.payload/
# transactions.payload's own "promoted columns are a derived query surface, payload is
# truth" split. `title`/`created_at`/`updated_at`/`message_count` are promoted on top
# (recomputed from `meta` on every write) because they are what a session list actually
# sorts/displays on; nothing sorts/filters on the remaining ~10 fields, so they stay
# JSON-only rather than each earning its own column.
#
# `transcript` is the message array (assistant.SESSIONS[id]) — load/save the WHOLE
# array, exactly matching pre-migration access; no per-message table (a chat message is
# never queried independent of its conversation). `fold`/`proactive` are nullable
# siblings: fold is the regenerable context-fold cache (RFC D; NULL = never folded),
# proactive is the list of STRUCTURED alert events staged for the next inbound turn
# (NULL/empty = none staged) — `{proactive_event_id, subject_key, created_at,
# report_ref, summary, delivery_status}` since the cross-channel-attachments RFC's
# bounded-proactive-context fix (§7): a plain list[str] let an unbounded run of raw
# broadcasts get joined verbatim into ONE giant permanent transcript entry (a live
# incident measured 367 accumulated report bodies doing exactly that); the structured
# shape lets `assistant._proactive_pair` coalesce by `subject_key` and admit only a
# bounded count/char budget BEFORE anything is written into durable history.
#
# Both sidecars were separate FILES that bypassed the checkpoint write-hook entirely
# pre-migration (neither `_save_fold` nor `record_proactive_message` ever called
# `fire_write_hook` — confirmed by reading every write site). `session_store.py`
# preserves that: it never calls `core.io.fire_db_write_hook` for ANY column of this
# table, belt-and-braces with the table's own `ai.checkpoint._policy._EXCLUDED_TABLES`
# membership — mirroring `checkpoints`' own "checkpointed=False AND excluded" precedent
# (both layers say never). A chat transcript must survive a /rewind (a checkpoint
# restore must not delete chat history), and skipping the hook call outright also
# avoids reading back a prior transcript — real ones run several MB — purely to build a
# before-image nothing will ever use, on every one of a turn's several transcript saves.
#
# No `seq` column, unlike every other flat-list store this migration ships: nothing
# reads sessions in "file insertion order" — `list_sessions()` always sorts by
# `updated_at` DESCENDING in Python, both before and after this migration — so there is
# no original order for a `seq` column to reconstruct.
#
# `extraction_cursor` (Wave 5F, revision 0034) is the fifth nullable JSON sidecar column
# — `ai/memory.py`'s per-session incremental-extraction watermark
# (`{"last_extracted_len": N}` verbatim, the SAME shape the old `{id}.extraction_cursor.
# json` sidecar held), explicitly flagged out of scope by Wave 5E's own cutover (see that
# revision's docstring) because a cursor can legitimately outlive or predate the session
# row it names — memory extraction is best-effort and never creates a session on its own.
# Follows `fold`/`proactive`'s exact precedent: never fires `core.io.fire_db_write_hook`
# (this table is checkpoint-EXCLUDED — see the sessions module comment above — and a
# memory-extraction watermark is not a fact a /rewind should ever touch), NOT part of
# `services.portability.session_bundle_entries()`'s export shape (the old sidecar was
# never part of the exported bundle either — it is memory.py's own private state, not
# session data).
sessions = Table(
    "sessions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("message_count", Integer, nullable=False),
    Column("meta", JSON, nullable=False),
    Column("fold", JSON, nullable=True),
    # RETIRED (revision 0056) — nothing reads or writes it. Alerts are ordinary appended
    # messages now; this held the staging queue that existed only because the transcript
    # was one JSON column a running turn rewrote wholesale.
    #
    # Kept DECLARED rather than dropped: on SQLite `batch_alter_table` cannot drop a column
    # in place, so it recreates `sessions` — and `session_messages.session_id` references
    # it ON DELETE CASCADE, which deletes every message. Measured on real data: 16,378 -> 12.
    # (`PRAGMA foreign_keys=OFF` does not help — SQLite ignores it inside a transaction, and
    # migrations run in one.) An unused nullable column is a much better trade, and it has
    # to stay in the model or `alembic check` reports permanent drift.
    Column("proactive", JSON, nullable=True),
    Column("extraction_cursor", JSON, nullable=True),
    # A fresh random token on every message write — "did the transcript change?" without
    # reading it (revision 202610071700; `SessionStore.transcript_revision`).
    Column("transcript_rev", Text, nullable=True),
)

# ── A transcript is a TABLE OF MESSAGES (revision 0051) ───────────────────────────────
# `sessions.transcript` was ONE JSON column holding an entire conversation — 843 sessions,
# 16,810 messages, 71.9 MB on the owner's instance, the largest single cell 3.1 MB, and
# REWRITTEN IN FULL on every model round (`SessionStore.set_transcript`). Two consequences
# forced the rest of the design:
#
#   * there is no "append a message", so an alert arriving from a scheduler thread mid-turn
#     could not be appended — the running turn would clobber the column with its own
#     in-memory copy. That is the ONLY reason proactive alerts live in a sidecar and are
#     folded in at the next turn's start, and every defect in that area traces back to it;
#   * a `tail=30` read rehydrated megabytes and sliced afterwards.
#
# `seq` is 0-based and matches what used to be the list index EXACTLY, which is what makes
# the cutover mechanical: the rollback (`del history[snapshot:]`), the checkpoint anchor
# (`anchor_history_len`), the fold `window_start` and the cost chip's `turn_index` were all
# already positional. `seq` is strictly better than a list index — an index shifts if
# anything is inserted mid-list, a seq does not.
#
# `content` stays the OPAQUE provider shape (a plain string, or the block list carrying
# text/tool_use/tool_result/image-ref). Splitting blocks into their own table is a separate
# question and fixes nothing here. RFC `2026-08-24-transcript-becomes-a-message-table.md`.
session_messages = Table(
    "session_messages",
    metadata,
    # The DATABASE assigns the key. `INTEGER PRIMARY KEY` is SQLite's rowid alias, so it is
    # allocated inside the INSERT — no read beforehand, no lock to depend on. Two earlier
    # attempts had the application decide the position instead (a caller list index, then
    # `max(seq)+1` under `engine._lock_for_table`) and both broke: that lock is a
    # `threading.Lock`, in-process only, so two processes appending to one session raced
    # (proven: `UNIQUE constraint failed`). CLAUDE.md records the same lesson from the
    # proactive queue's own process-local `RLock`, retired for the same reason.
    #
    # `sa.Integer`, NOT `sa.BigInteger`: the latter renders `BIGINT NOT NULL` on SQLite,
    # which is not the rowid alias, so nothing is auto-assigned.
    #
    # `id` ORDERS the conversation; it is not a position. POSITION is the row's index in the
    # ordered read, which is what every positional consumer uses.
    Column("id", Integer, primary_key=True, autoincrement=True),
    # ON DELETE CASCADE — a message can never outlive its conversation.
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),
    # The OPAQUE provider value — a plain string, or the block list carrying
    # text/tool_use/tool_result/attachment refs — byte-for-byte what the JSON array held.
    Column("content", JSON, nullable=False),
    # ISO-8601 UTC, matching `sessions` right above. Stamped at INSERT. The frontend's
    # wire field is still called `ts` (epoch ms) — `rehydrate_messages` maps it — but the
    # column says what it holds. `updated_at` moves when a message is edited in place,
    # which genuinely happens: `_abort_stale_pending` rewrites a dangling tool_use, and the
    # attachment path swaps a resolved message back to its ref-shaped durable form.
    Column("created_at", Text, nullable=True),
    Column("updated_at", Text, nullable=True),
    # Everything else the message dict carried — the skill host rides `_onw_skill`/
    # `_onw_skill_activity_id`/`_onw_skill_deactivation` on it. Promoting three fields and
    # dropping the rest is how revision 0051 lost 19 real skill-activation receipts.
    Column("extra", JSON, nullable=True),
    Index("ix_session_messages_session", "session_id", "id"),
)

# ── Wave 5F (JSON->DB) — the zero-`.json` finisher ────────────────────────────────────
# Closes the last two gaps Wave 5E's own brief flagged out of scope: `sessions.
# extraction_cursor` above (revisions 0034-0035), and three CACHE_DIR runtime-STATE
# watermarks that turned out not to deserve file status either — `digest_state`/
# `news_screen_state`/`memory_consolidate_state` join `runtime_state` as three more
# namespace rows (revisions 0036-0037), the SAME checkpoint-EXCLUDED namespaced-singleton
# table `harness_state`/`app_config` already share — see that table's own docstring
# above. A fourth CACHE_DIR file this wave investigated, `routing_decisions.json`, turned
# out to be VESTIGIAL (the cross-provider auto-routing engine that wrote it was removed
# in `5abdb183`, well before this wave) — cleaned up as dead data (revision 0038), not
# migrated: there is nothing left reading it, so no table was minted for it.

# ── THE permission table (revision 0039 as `connector_tool_permissions`, renamed and
# widened at revision 0040) ────────────────────────────────────────────────────────────
# tool_permissions: ONE row per (owner, tool) pair the user has actually decided about,
# for EVERY tool in the app. Tier-3 config, NO book_id (a permission is this
# INSTALLATION's trust posture, not a book's ledger fact).
#
# `connector_id` is the tool's OWNER: its `@tool(connector=...)` id, `mcp:<server_id>`
# for an external-MCP tool, or the reserved bucket "core"
# (`services.tool_permissions.CORE_CONNECTOR_ID`) for an unscoped one. The table was
# named `connector_tool_permissions` while it held only the first two; revision 0040
# absorbed `tool_grants` — the app's OTHER, binary answer to "may this tool run" — into
# the `core` bucket and deleted it, at which point the `connector_` prefix was simply
# false. ONE store, one answer, two surfaces (Settings › Permissions and the Connectors
# ladder). ADR `2026-08-14-one-permission-store-for-every-tool`.
#
# `tool_grants` is deliberately ABSENT from this module. Revision 0016 still replays the
# `tool_grants.json` cutover on a fresh chain (an instance upgrading from files must absorb
# its grants BEFORE 0040 folds them in here), but it does that through its OWN throwaway
# `sa.table(...)` handle. Registering a dropped table on this shared `metadata` would make
# metadata disagree with the live schema at head — which `alembic check` reports as a
# missing table, and which silently aborted the metadata-driven Tier-2 test cleanup.
#
# THE SHAPE IS THE POINT (design handoff §10): a level is stored PER TOOL and only per
# tool. The "Custom" group aggregate is DERIVED wherever it is displayed — a group is
# uniform when every tool in it agrees, Custom when they don't — and there is
# structurally nowhere in this table to write one, which is what makes "never stored"
# an invariant rather than a rule someone has to remember. A group-level pick is a
# deliberately LOSSY stamp of one level onto every named tool (see
# ``ToolPermissionStore.set_levels``), not a second, coarser record that could
# disagree with the rows underneath it.
#
# `id` is the deterministic surrogate f"{connector_id}:{tool}" — `budget_assignments`'
# exact precedent (a keyed relation whose pk is composed of the columns that identify
# it), for the same reason: it makes "set this pair's level" a plain upsert and makes a
# duplicate row for one pair structurally impossible. `connector_id` and `tool` are also
# carried verbatim IN the payload; a tool name is a Python identifier and a connector id
# never contains "/" (`services.connectors`' own ids: `email`, `web_search:tavily`,
# `mcp:<slug>` — and `models.mcp_client._SERVER_ID_ALPHABET` is a-z/0-9/-), so the pair
# also round-trips through one rsplit(":", 1) if anything ever needs it from the id alone.
#
# ABSENT means UNDECIDED, never "denied": a connector-owned tool reads
# `services.tool_permissions.DEFAULT_LEVEL` ("needs_approval"), a `core`-owned one reads
# the approval model's own verdict for its shape (write -> ask, read -> run) so every
# untagged tool keeps behaving exactly as it did. Only a real decision becomes a row.
#
# Checkpoint-EXCLUDED (ai.checkpoint._policy._EXCLUDED_TABLES) + NON_RESTORABLE
# (core.sensitive.NON_RESTORABLE_TABLES) + NOT portable — inherited unchanged from BOTH
# merged predecessors, which classified identically; see ADRs
# `2026-08-13-connector-tool-permissions-storage.md`,
# `2026-08-14-one-permission-store-for-every-tool.md`, and db/tool_permission_store.py.
tool_permissions = payload_table("tool_permissions")

# ── `claims` — cross-subject push-dedup registry (revision 202608301800). Owner override of
# RFC intel-signals-data-model §4's "NO-GO" — see
# docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md for the full reasoning. A
# `claims` row is ONE real-world fact a story's push decision judged worth communicating.
# `group_key` (services.claims.group_key_for) is the subject-agnostic identity that lets a
# DIFFERENT `intel_stories` row genuinely about the SAME fact (e.g. a linked topic row and its
# asset row) resolve to the SAME claim, so a second story's independent push of the identical
# development can be suppressed instead of firing twice — the proven incident: a topic-side and
# an asset-side signal each independently pushed the same tender-offer news, ~10h apart, past
# the existing same-tick-only pairing's reach (ADR 2026-08-28-cross-subject-fan-out-and-asset-
# brief-consolidation). Deliberately narrow: only news/development-tracking story types
# (asset/topic/risk/opportunity/macro) ever resolve a group_key, and only when they map to
# EXACTLY ONE instrument — a price_alert/daily_digest row, or a multi-instrument risk/macro
# finding, never gets one, so the RFC's own scoping caution (a price alert is never a duplicate
# of an M&A topic on the same ticker) holds structurally.
#
# NO FK to `intel_stories` — `story_ids` is an opaque id list, the same loose-coupling
# convention `intel_stories.instrument_id` already uses: a claim outlives any single story and
# must never cascade-delete when one of its stories is deleted.
claims = Table(
    "claims",
    metadata,
    Column("id", Text, primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("group_key", Text, nullable=False),
    Column("statement", Text, nullable=False, server_default=text("''")),
    # active | superseded | flagged — mirrors signal_timeline_entries' own status/superseded/
    # confirmed_at vocabulary (the SAME collision-avoidance precedent RFC intel-signals-data-
    # model already established), not a new one.
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("duplicate_of_claim_id", Text, nullable=True),
    Column("contradicts_claim_id", Text, nullable=True),
    # Server-built {url, title, domain} triples (analyst_prompt.source_dicts) — never
    # model-chosen, matching this codebase's existing grounding stance (ADR 2026-08-28-
    # sourced-and-corroborated-intel-signal-updates).
    Column("sources", JSON, nullable=False, server_default=text("'[]'")),
    # Which intel_stories rows this claim has been applied to — real story ids, never ticker
    # strings.
    Column("story_ids", JSON, nullable=False, server_default=text("'[]'")),
    Column("materiality", Text, nullable=True),
    Column("decision_relevant", Integer, nullable=False, server_default=text("1")),
    Column("created_at", Text, nullable=False),
    Column("confirmed_at", Text, nullable=True),
    # When any story tied to this claim last actually pushed — the freshness check
    # (services.claims.is_recent_duplicate_push) reads THIS to decide whether a second story's
    # push is a duplicate of an already-communicated fact.
    Column("pushed_at", Text, nullable=True),
    Column("payload", JSON, nullable=False),  # verbatim row (round-trip source of truth)
    CheckConstraint("status IN ('active','superseded','flagged')", name=conv("ck_claims_status")),
    CheckConstraint("decision_relevant IN (0,1)", name=conv("ck_claims_decision_relevant_bool")),
    Index("ix_claims_seq", "seq", unique=True),
    Index("ix_claims_group_key", "group_key"),
)
