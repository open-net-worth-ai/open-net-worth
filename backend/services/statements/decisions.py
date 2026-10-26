"""Decision-based statement import (RFC D7): preview parses ONCE and caches server-side; the
commit sends per-row DECISIONS (create / merge / skip + edits), applied in ONE batch.

This is what removes the PREVIEW_LIMIT commit cap structurally (the client never re-sends row
data), makes merges atomic + reversible (pre-merge snapshots ride the batch for
rollback-with-unmerge), files a Statement record under the account, and gives the chat tool
byte-identical semantics to the modal — both are thin clients of these two functions."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import asdict, dataclass, fields
from datetime import date as _date
from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.txn_sources import STATEMENT_NAMESPACE
from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.import_common import (
    ImportError_,
    RowResult,
    build_preview,
    disambiguate_identical_rows,
    flag_duplicates,
    resolve_account,
)
from backend.services.statements import (
    lucius_pass,
    parse_progress,
    routed_transfer,
    statement_list,
    stored_previews,
)
from backend.services.statements import match_policy as policy_service
from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta
from backend.services.statements.enrich import enrich_drafts
from backend.services.statements.guards import apply_chain_reconcile
from backend.services.statements.importer import (
    MIN_CONFIDENCE,
    ImportRouting,
    _row_to_draft,
    category_rows_for,
    external_ref,
    import_routing,
    strip_display_only,
)
from backend.services.statements.parser import parse_statement_bytes, run_async, to_upload
from backend.services.statements.tiers import (
    StatementParseError,
    extract_meta_from_text,
    harvest_preamble_last4s,
    parse_grid,
)

logger = logging.getLogger(__name__)

# `decision_commit` now runs off the event loop (asyncio.to_thread), so two commits of the
# same statement (a race between tabs, or the UI vs. the import_transactions tool) can run
# concurrently. Its duplicate defense (`has_external_ref` / `_matching.blocking_duplicate`
# inside `ledger.bulk_create_transactions`) reads the ledger BEFORE that choke's own write
# transaction opens, so two overlapping commits both see a pre-write ledger, both pass, and
# both book — every row doubled. This lock restores the pre-thread-offload serialization.
_commit_lock = threading.Lock()

_DECISION_ACTIONS = frozenset({"create", "merge", "skip"})
# Row edits the user may make in review before booking — applied server-side, pre-choke.
_EDITABLE_FIELDS = frozenset(
    {"category", "payee", "amount", "date", "direction", "account_id", "kind", "to_account_id"}
)

# Shared reference token linking the two legs of an internal FX conversion across currency
# sections (e.g. Meridian Bank prints the same "FT260722MB48213590" on the EUR withdrawal AND
# the SGD deposit). Long alnum runs only — short instant-transfer ids / dates never match.
_REF_TOKEN = re.compile(r"[A-Z0-9]{10,}")

# The RowMatch classes match_batch may assign (RFC batch-aware-dedup) that carry a real
# existing-ledger match — mirrors the flag types the OLD per-row engine scan
# (import_anomalies.detect_anomalies) used to attach for the SAME rows; _apply_batch_match
# below replaces those with the batch-aware engine's verdict so decision_preview sees the
# WHOLE statement (global 1:1 exclusivity + books-close promotion), not one row at a time.
_MATCH_FLAG_TYPES = frozenset({"near_duplicate", "transfer_pair", "transfer_leg_echo"})

# The classes a RowMatch assignment must carry to be a real ledger match (the three above, in
# engine terms) — shared by the assignment loop and the policy-suppression diff beside it.
_ASSIGNABLE_CLASSES = (
    matching.MatchClass.NEAR_DUPLICATE,
    matching.MatchClass.TRANSFER_PAIR,
    matching.MatchClass.TRANSFER_LEG_ECHO,
)

# Machine-generated bank exports that are complete-by-construction: the bank emits every
# booked entry for the covered period, so a declared period + a clean parse (no guard
# warnings) may claim batch completeness even when the file declares no opening/closing
# balances for the reconcile identity to prove itself. An LLM-read format (pdf, and the
# tabular tiers) never qualifies through this leg — it must reconcile.
_BANK_COMPLETE_FORMATS = frozenset({"ofx", "mt940", "camt"})

# Category-hint attempts per cached file (owner incident 2026-07-27: a single degraded/
# no-hit attempt used to forfeit auto-categorization for that file FOREVER, silently — no
# error, no retry, ever). A small cap self-heals from one bad attempt while still bounding
# re-billing if a file's content is persistently unhintable (e.g. no matching category).
_MAX_HINT_ATTEMPTS = 3

# Match-policy extraction attempts per statement record (RFC 2026-07-29 §D5, R6's correction).
# Same shape and same reason as the hint cap above: a preview is model-driven and retryable, so
# a source whose extraction keeps coming back malformed would re-bill on EVERY preview forever.
# A SUCCESSFUL extraction is stamped on the record and never retried, so this only bounds
# repeated failure — and unlike the hint pass, running out is safe by construction: the preview
# stamps the policy it actually matched under, so commit re-resolves to the same one.
_MAX_POLICY_ATTEMPTS = 3


def _completeness_ok(reconcile: dict | None, meta: StatementMeta, fmt: str) -> bool:
    """Whether this parse may claim ``BatchContext(complete=True)`` (the reconcile gate):
    the statement's own identity must PASS (opening + Σ activity ≈ closing → status
    ok/ok_if_flipped), OR the format is a bank-complete export with a declared period and a
    clean parse. A failed or unknown reconcile otherwise → complete=False — bands still
    compute, books-close promotion never fires on unproven books."""
    status = (reconcile or {}).get("status")
    if status in ("ok", "ok_if_flipped"):
        return True
    return (
        fmt in _BANK_COMPLETE_FORMATS
        and bool(meta.period_start and meta.period_end)
        and not meta.warnings
    )


# Reconcile statuses that a ledger-diff comparison may trust (guards.py::reconcile /
# reconcile_from_balance_chain). "unknown" never claimed either way (no printed
# opening/closing — silence, not a lie), so it stays trusted. "ok_if_flipped" is
# DELIBERATELY EXCLUDED even though `_completeness_ok` above treats it as a passing
# reconcile for the books-close promotion: that aggregate check only needs the TOTAL to
# close, but a flipped sign convention means every ROW's direction was read backwards, so
# a per-row ledger-diff comparison built on it is unproven even though the sum comes out
# right. "off" (or any other status) is an explicit failing verdict — the statement's own
# identity doesn't add up, so a ledger-only diff on top of it would compound one unproven
# claim onto another.
_RECONCILE_TRUSTED_STATUSES = ("ok", "unknown", None)


def _parse_withheld_reason(
    meta: dict, reconcile: dict | None, tier: str | None = None
) -> str | None:
    """Why the pre-commit/post-commit ledger-diff must not run for this parse — or None when
    it's safe (ADR 2026-09-09-an-incomplete-parse-withholds-the-ledger-diff). A parse that
    never finished reading every page/chunk must never let the
    "in your ledger, not on this statement" band claim rows are missing from the statement —
    they may just be rows the parse never read. Checked FIRST: an incomplete parse makes the
    reconcile verdict itself unreliable, so it always wins over a reconcile-status read.
    A statement whose own balance identity doesn't add up has the same problem one layer up:
    a ledger diff on top of unproven books doubles the unproven claim instead of catching it.

    A record with NO ``parse_complete`` key at all — every statement cached BEFORE this
    completeness tracking existed, including the one that caused the incident this whole gate
    exists for — is UNVERIFIED, never silently read as complete (security review follow-up:
    ``.get("parse_complete", True)`` let every pre-existing cached statement diff as clean).
    The exceptions are ``tier`` starting with "tabular", or exactly "structured" (OFX/QIF/
    MT940/CAMT): both are zero-LLM and parse every row deterministically, complete by
    construction regardless of when it was cached, so neither is held to a completeness
    check it could never have failed."""
    extra = meta.get("extra") or {}
    tier = tier or ""
    if "parse_complete" in extra:
        if not extra["parse_complete"]:
            return "parse_incomplete"
    elif not (tier.startswith("tabular") or tier == "structured"):
        return "parse_unverified"
    if (reconcile or {}).get("status") not in _RECONCILE_TRUSTED_STATUSES:
        return "reconcile_off"
    return None


def _withheld_ledger_check(
    meta: dict, reconcile: dict | None, tier: str | None = None
) -> dict | None:
    """The gated ``ledger_check`` shape for a withheld parse, or None when the ordinary
    ``preview_ledger_check`` derivation may run."""
    reason = _parse_withheld_reason(meta, reconcile, tier)
    if reason is None:
        return None
    extra = meta.get("extra") or {}
    return {
        "withheld": reason,
        "pages_incomplete": len(extra.get("incomplete_pages") or []),
        "delta": (reconcile or {}).get("delta"),
        "currency": (reconcile or {}).get("currency"),
    }


def _statement_store():
    from backend.db.statement_store import StatementStore

    return StatementStore()


def claim_for_account(statement_id: str, account_ref: str | None) -> None:
    """Stamp the account an upload was sent to onto its record, the moment it is accepted.

    ``StatementStore.create`` writes ``account_id=None`` and, until this, only COMMIT stamped
    it — so the account's Statements sheet (which lists by ``account_id``) never showed a
    statement that was parsing, ready to review, failed or interrupted, which is exactly when
    the owner looks for it. Never overwrites an account already on the record, and an
    unresolvable ref stamps nothing (the parse itself reports a bad account)."""
    if not (account_ref or "").strip():
        return
    store = _statement_store()
    record = store.find_by_id(statement_id)
    if record is None or record.get("account_id"):
        return
    try:
        resolved = resolve_account(account_ref, None)
    except ImportError_:
        return
    store.update_by_id(statement_id, {"account_id": resolved})


def _persist_parse_progress(statement_id: str, snapshot: dict) -> None:
    """``parse_progress``'s persistence hook (B20 async mode), attached at ``bind_statement``
    time: every stage boundary and done/error writes the durable copy — ``StatementStore.
    set_parse_status`` — so a restart (boot sweep) or a browser reopening this statement BY ID
    sees the same state a live poll would have. Best-effort: a write failure here is a
    durability gap, never a reason to break the parse that is still running."""
    status = parse_progress.status_for(snapshot)
    try:
        _statement_store().set_parse_status(statement_id, status, progress=snapshot)
    except Exception:  # noqa: BLE001 — persistence is durability, never a preview-breaker
        logger.exception("decision_preview: failed to persist parse status for %s", statement_id)


def _suggest_account(meta: StatementMeta) -> str | None:
    """Account auto-match (D4.2, SI-25), two layers, printed claim first.

    Layer 1 — the statement's own printed digits: ``meta.account_last4s`` (LLM-extracted on
    the document tiers, deterministically harvested from the preamble on tabular ones)
    intersected with the user's non-archived accounts. The intersection must be UNAMBIGUOUS:
    matches confined to ONE shared institution are that custodian's currency wallets (one
    printed number, many wallets — the multi-account routing shape) and anchor the first;
    matches spanning different institutions suggest nothing — honest beats guessing, and the
    statement's own ambiguous claim also blocks the memory fallback.

    Layer 2 — format-profile memory: no printed match at all → the account this source last
    COMMITTED into (``_stamp_profile_account``), if it still exists non-archived."""
    printed, claimed = printed_account_match(meta.account_last4s or [])
    if claimed:
        return printed
    return _profile_memory_account(meta)


def printed_account_match(last4s: list[str]) -> tuple[str | None, bool]:
    """Layer 1 of ``_suggest_account`` on its own: ``(account_id, claimed)``. ``claimed`` is
    True when ANY live account matched — an ambiguous claim returns ``(None, True)``, which
    must block every fallback (the statement's own claim beats memory)."""
    wanted = set(last4s)
    if not wanted:
        return None, False
    matched = [
        a
        for a in ledger_service.accounts.all()
        if not a.get("archived") and str(a.get("last_four") or "")[-4:] in wanted
    ]
    if not matched:
        return None, False
    institutions = {a.get("institution_id") for a in matched}
    if len(matched) == 1 or (len(institutions) == 1 and institutions != {None}):
        return matched[0]["id"], True
    return None, True


def _profile_memory_account(meta: StatementMeta) -> str | None:
    """The account this source's format profile last committed into — re-read LIVE from the
    store (the cached meta's ``format_profile`` snapshot may predate the commit stamp).
    None unless the account still exists non-archived."""
    prof_id = ((meta.extra or {}).get("format_profile") or {}).get("id")
    if not prof_id:
        return None
    try:
        from backend.db.statement_format_store import StatementFormatStore

        store = StatementFormatStore()
        prof = store.find_by_id(prof_id)
        acct_id = store.accounts_for(prof)[0] if prof else None
    except Exception:  # noqa: BLE001 — memory is an optimization, never a preview-breaker
        return None
    if not acct_id:
        return None
    acct = ledger_service.accounts.find_by_id(acct_id)
    return acct_id if acct and not acct.get("archived") else None


def _heal_cached_last4s(meta: StatementMeta, content: bytes, fmt: str) -> None:
    """Self-heal for tabular parses cached BEFORE the deterministic preamble harvest
    existed: a record whose meta carries no printed account digits re-derives them from the
    same bytes — zero LLM, zero re-parse (the refreshed meta persists with the cache update
    below, so a pre-fix record detects its account on its next preview). The header position
    comes from the source's format profile; no profile → no heal, because data rows are full
    of >= 6-digit runs (references, yyyymmdd stamps) and an unbounded scan would guess —
    ``needs_account`` is the honest state."""
    if meta.account_last4s or fmt not in ("csv", "xlsx"):
        return
    try:
        from backend.services.statements import profiles

        grid = parse_grid(to_upload(content, fmt).get("text") or "")
        prof = profiles.find_profile(grid)
        if not prof:
            return
        meta.account_last4s = harvest_preamble_last4s(
            grid, (prof.get("schema") or {}).get("header_row", 0)
        )
    except Exception:  # noqa: BLE001 — healing is opportunistic, never a preview-breaker
        logger.exception("decision_preview: cached account-last4 self-heal failed")


def _heal_cached_reconcile(result: ParseResult, fmt: str) -> None:
    """Self-heal for tabular parses cached BEFORE the balance-chain reconcile existed (the
    ``_heal_cached_last4s`` pattern): a record stuck at reconcile "unknown" whose cached rows
    carry a running balance re-proves itself from those SAME rows — zero LLM, zero re-parse.
    The healed verdict + derived window persist with the cache update below, so a pre-fix
    record's unique exact pairs start books-close-promoting on its very next preview."""
    if fmt not in ("csv", "xlsx"):
        return
    try:
        apply_chain_reconcile(result)
    except Exception:  # noqa: BLE001 — healing is opportunistic, never a preview-breaker
        logger.exception("decision_preview: cached reconcile self-heal failed")


def _heal_cached_meta(meta: StatementMeta, content: bytes, fmt: str) -> int:
    """Self-heal for a record whose metadata read FAILED (``meta_unavailable``) — the third
    heal in the ``_heal_cached_last4s`` family, and the only one that spends a call.

    It re-extracts the document text DETERMINISTICALLY from the retained bytes and re-runs the
    ONE bounded metadata call. The rows are never re-read: a metadata failure says nothing about
    them, and re-billing a 49-row statement to recover its header would be the reason nobody
    ever repairs one. Returns the calls spent (0 or 1) so the caller can bill it honestly.

    Gated on the FLAG, never on emptiness: a statement that genuinely prints no period is a
    legitimate all-null meta and must not buy a call on every preview, forever. Only a recorded
    failure is retried, and a heal that fails again leaves the flag up for the next preview
    rather than downgrading "unknown" to "absent".

    On success the whole meta is REPLACED (not merged): the failed one has no field worth
    keeping, and the fresh read is a complete answer. It lands before ``_suggest_account``
    downstream, so the printed account digits it recovers also re-detect the account the
    failure left unbound."""
    if not meta.meta_unavailable:
        return 0
    try:
        upload = to_upload(content, fmt)
        text = upload.get("text") or ""
        if not text.strip():
            return 0  # a scanned/vision statement has no text tier to re-read
        healed = run_async(extract_meta_from_text(text))
    except Exception:  # noqa: BLE001 — healing is opportunistic, never a preview-breaker
        logger.exception("decision_preview: cached metadata self-heal failed")
        return 0
    if healed.meta_unavailable:
        logger.info("decision_preview: metadata self-heal retried and failed again — still flagged")
        return 1
    for f in fields(StatementMeta):
        setattr(meta, f.name, getattr(healed, f.name))
    return 1


# ── Multi-account routing (one statement, many currency wallets) ─────────────────────────
# A consolidated multi-currency statement (Meridian Bank Multiplier, IBKR, Nomad Pay…) prints per-currency
# sections against ONE account number, while the book models it as per-currency wallet
# accounts sharing an `institution_id`. Booking a row into a wallet of a DIFFERENT currency corrupts
# the balance (the native fold is currency-blind), so each row routes to the wallet holding
# its own currency — and an unroutable currency is OFFERED but never default-booked.


def _account_map_memory(primary_id: str) -> dict[str, str]:
    """The user's remembered routing for this source: the newest committed statement whose
    account set includes ``primary_id`` and carries an ``account_map`` {currency: account_id}
    (stamped at commit). Explicit past choices beat institution inference."""
    try:
        records = _statement_store().all()
    except Exception:  # noqa: BLE001 — memory is an optimization, never blocks a preview
        return {}
    best: dict | None = None
    for rec in records:
        if not rec.get("account_map"):
            continue
        if primary_id != rec.get("account_id") and primary_id not in (rec.get("account_ids") or []):
            continue
        if best is None or (rec.get("created_at") or "") > (best.get("created_at") or ""):
            best = rec
    return {str(k).upper(): v for k, v in ((best or {}).get("account_map") or {}).items()}


def _route_accounts(chosen_id: str) -> dict[str, dict]:
    """currency (UPPER) → account row. The chosen account anchors its institution group: every
    non-archived account sharing its (non-empty) ``institution_id`` covers its own currency; a
    remembered account_map overrides inference; the chosen account always wins its OWN
    currency. Two siblings in one currency: prefer the chosen account's last-4, else stay
    unrouted (honest beats guessing)."""
    accounts = {a["id"]: a for a in ledger_service.accounts.all() if not a.get("archived")}
    chosen = accounts.get(chosen_id)
    if not chosen:
        return {}
    route: dict[str, dict] = {}
    institution_id = chosen.get("institution_id")
    if institution_id:
        by_ccy: dict[str, list[dict]] = {}
        for a in accounts.values():
            if a.get("institution_id") == institution_id:
                by_ccy.setdefault(str(a.get("currency") or "").upper(), []).append(a)
        chosen_l4 = str(chosen.get("last_four") or "")
        for ccy, cands in by_ccy.items():
            if not ccy:
                continue
            if len(cands) == 1:
                route[ccy] = cands[0]
            elif chosen_l4:
                same_l4 = [a for a in cands if str(a.get("last_four") or "") == chosen_l4]
                if len(same_l4) == 1:
                    route[ccy] = same_l4[0]
    for ccy, acct_id in _account_map_memory(chosen_id).items():
        acct = accounts.get(acct_id)
        if acct and str(acct.get("currency") or "").upper() == ccy:
            route[ccy] = acct
    route[str(chosen.get("currency") or ledger_service.base_currency()).upper()] = chosen
    return route


def _days_apart(a: str, b: str) -> int | None:
    try:
        return abs((_date.fromisoformat(a[:10]) - _date.fromisoformat(b[:10])).days)
    except (TypeError, ValueError):
        return None


def _pair_fx_transfers(
    rows: list[CanonicalRow], route: dict[str, dict], default_ccy: str
) -> dict[int, dict]:
    """Detect intra-statement internal FX conversions: the SAME long reference token on an OUT
    row in one currency section and an IN row in another is ONE transfer between the user's
    own wallets — never an expense + an income. Conservative by design: exactly two rows share
    the token, opposite directions, different currencies, both routed to distinct accounts,
    dates ≤ 2 days apart. Returns {row_index: {"leg": "out"|"in", "partner": index}}."""
    by_token: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        if row.skip:
            continue
        ccy = (row.currency or default_ccy).upper()
        if ccy not in route:
            continue
        text = f"{row.reference or ''} {row.description or ''}".upper()
        for tok in set(_REF_TOKEN.findall(text)):
            by_token.setdefault(tok, []).append(idx)
    pairs: dict[int, dict] = {}
    for idxs in by_token.values():
        uniq = sorted(set(idxs))
        if len(uniq) != 2 or uniq[0] in pairs or uniq[1] in pairs:
            continue
        a, b = rows[uniq[0]], rows[uniq[1]]
        ccy_a = (a.currency or default_ccy).upper()
        ccy_b = (b.currency or default_ccy).upper()
        if ccy_a == ccy_b or a.direction == b.direction:
            continue
        if route[ccy_a]["id"] == route[ccy_b]["id"]:
            continue
        days = _days_apart(a.date, b.date)
        if days is None or days > 2:
            continue
        out_i, in_i = (uniq[0], uniq[1]) if a.direction == "OUT" else (uniq[1], uniq[0])
        pairs[out_i] = {"leg": "out", "partner": in_i}
        pairs[in_i] = {"leg": "in", "partner": out_i}
    return pairs


@dataclass
class _RoutingContext:
    """Everything row-offering needs to place each row in the right wallet."""

    resolved: str
    default_ccy: str
    route: dict[str, dict]
    pairs: dict[int, dict]

    def row_currency(self, row: CanonicalRow) -> str:
        return (row.currency or self.default_ccy).upper()

    def target_for(self, row: CanonicalRow) -> dict | None:
        return self.route.get(self.row_currency(row))


def _booked_transfer_pool() -> list[dict]:
    """Every live ledger TRANSFER, in ledger order — read ONCE per statement pass and shared by
    every row's ``_find_booked_transfers`` (it used to re-read the whole ledger per routed row)."""
    try:
        return ledger_service.transactions.query_txns(kind="transfer")
    except Exception:  # noqa: BLE001 — advisory lookup, never breaks a preview
        return []


def _find_booked_transfers(
    from_acct: str,
    to_acct: str,
    from_amount: float,
    to_amount: float,
    date: str,
    pool: list[dict] | None = None,
) -> list[tuple[int, dict]]:
    """ALL already-booked ledger transfers matching these exact legs (booked earlier by an
    agent/email/hand), as (day_gap, txn) sorted closest-date-first (stable on pool order).
    Deterministic: same from/to accounts, both leg amounts within tolerance, date ≤ 2 days.
    Near-dup can't cover this — it is expense/income-only; a transfer needs its own check.
    Shared by the FX-pair check and the routed-draft echo check (SI-66). Returns the FULL
    ranked list so `_routed_booked_transfers` can assign 1:1 across rows — the old
    first-in-pool single return sent every same-amount routed row to the SAME booked
    transfer (reported bug: two −350,000 Nimbus Wallet top-ups, 06-02 and 06-04, both
    'already booked' as the 06-02 transfer while the 06-04 booking sat invisible)."""

    def _close(a: float, b: float) -> bool:
        return abs(a - b) <= max(0.01, abs(b) * 0.001)

    if pool is None:
        pool = _booked_transfer_pool()
    out: list[tuple[int, dict]] = []
    for t in pool:
        if t.get("deleted") or t.get("merged_into") or t.get("kind") != "transfer":
            continue
        if t.get("from_account_id") != from_acct or t.get("to_account_id") != to_acct:
            continue
        days = _days_apart(str(t.get("date") or ""), date)
        if days is None or days > 2:
            continue
        if not _close(float(t.get("from_amount") or 0.0), from_amount):
            continue
        if not _close(float(t.get("to_amount") or 0.0), to_amount):
            continue
        out.append((days, t))
    out.sort(key=lambda dt: dt[0])  # stable: pool order breaks day ties
    return out


def _find_booked_transfer(
    from_acct: str,
    to_acct: str,
    from_amount: float,
    to_amount: float,
    date: str,
    pool: list[dict] | None = None,
) -> dict | None:
    """Single-caller convenience (the FX-pair check): the CLOSEST-date booked transfer."""
    ranked = _find_booked_transfers(from_acct, to_acct, from_amount, to_amount, date, pool)
    return ranked[0][1] if ranked else None


def _existing_pair_transfer(
    out_row: CanonicalRow,
    in_row: CanonicalRow,
    ctx: _RoutingContext,
    pool: list[dict] | None = None,
) -> dict | None:
    """The two-leg transfer this FX pair WOULD book, if it already exists in the ledger
    (reported case: the €1,450→S$2,150.00 conversion was already in the book via the email
    agent, and the pair would have booked it AGAIN)."""
    out_ccy = ctx.row_currency(out_row)
    in_ccy = ctx.row_currency(in_row)
    from_acct = (ctx.route.get(out_ccy) or {}).get("id")
    to_acct = (ctx.route.get(in_ccy) or {}).get("id")
    if not from_acct or not to_acct:
        return None
    return _find_booked_transfer(
        from_acct, to_acct, out_row.amount, in_row.amount, out_row.date, pool
    )


def _claimed_existing_ids(results: list[RowResult]) -> set[str]:
    return {f["existing_id"] for r in results for f in (r.flags or []) if f.get("existing_id")}


def _statement_account_ids(ctx: "_RoutingContext", rows: list[CanonicalRow]) -> set[str]:
    """The accounts this statement actually COVERS — the anchor plus the routed account for
    every currency its rows contain.

    ``ctx.route`` is not that set: it maps every currency in the anchor's institution GROUP, so a
    single-currency Meridian Pay statement adopts the sibling Meridian Bank Multiplier (EUR)
    account and the ledger-diff then reports that account's EUR transfers as "in your ledger,
    not on this statement" — under a "Mark all for deletion" button (reported case: 4 unrelated
    transfers worth EUR 3,200.00 offered for deletion on a 13-row SGD statement).
    """
    present = {(row.currency or ctx.default_ccy).upper() for row in rows if not row.skip}
    ids = {ctx.resolved} - {""}
    for ccy, account in ctx.route.items():
        if ccy in present:
            ids.add(account["id"])
    return ids


def _match_leftovers(
    results: list[RowResult],
    ctx: "_RoutingContext",
    meta: StatementMeta,
    policy: matching.MatchPolicy = matching.MatchPolicy(),
) -> None:
    """Is anything in "Import" the same movement as anything in "Not on this statement"?

    The two lists the owner is left staring at, and nothing compared them. The assignment
    runs ONCE, before the Lucius ops, so an entry a routed row released is never offered
    again — a Nomad Pay payment about to book new and 'SwiftLink to Nomad Pay Singapore'
    sitting unmatched, same day, same amount, same account (reported case).

    A rejected row is INCLUDED — only the entry it rejected is skipped. Excluding the row
    fenced it off from the partner it does belong to; trusting the claim set alone handed it
    straight back the entry it had just refused, whenever nobody else took that entry.
    """
    from backend.services.import_anomalies import _existing_summary
    from backend.services.statements.ledger_diff import _window

    claimed = {f["existing_id"] for r in results for f in (r.flags or []) if f.get("existing_id")}
    unlinked = [
        r
        for r in results
        if r.draft is not None
        and not r.duplicate
        and r.draft.get("kind") != "transfer"
        and not any(f.get("existing_id") for f in (r.flags or []))
    ]
    if not unlinked:
        return
    window = _window(meta.to_dict())
    account_ids = frozenset({a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""}))
    context = matching.BatchContext(complete=False, window=window, account_ids=account_ids)
    row_matches = matching.match_batch(
        [r.draft for r in unlinked], context=context, policy=policy, weak_tier=True
    )
    opening_ids = ledger_service.opening_txn_ids()
    for r, rm in zip(unlinked, row_matches):
        refused = set((r.draft or {}).get("match_rejected_ids") or ())
        for cand in rm.candidates:
            if not cand.id or cand.id in claimed or cand.id in refused:
                continue
            if cand.klass not in _ASSIGNABLE_CLASSES:
                continue
            claimed.add(cand.id)
            r.flags.append(
                {
                    "type": cand.klass.value,
                    "reason": "; ".join(cand.reasons),
                    "existing_id": cand.id,
                    "existing": _existing_summary(cand.txn, opening_ids),
                    "score": cand.score,
                    "klass": cand.klass.value,
                    "band": rm.band,
                    "promoted": rm.promoted,
                }
            )
            break


def _attach_booked_echo_flags(
    results: list[RowResult], pair_booked: dict[int, dict], routed_booked: dict[int, dict]
) -> None:
    """Attach the FX-pair / routed-draft booked-twin echo flags to ``r.flags`` ITSELF —
    not a display copy. `preview_ledger_check` derives its covered set from ``r.flags``
    (ledger_diff.py), so a flag that only rides the rows payload leaves its booking listed
    under 'In your ledger, not on this statement' with the self-referential note "a
    possible match is already linked above" (reported bug: all three
    correctly-linked Nimbus Wallet bookings shown as uncovered). Idempotent per (type, existing_id)
    — preview and commit both call this after their own booked-map derivation."""
    from backend.services.import_anomalies import _existing_summary

    def _release(existing_id: str | None, keep: int) -> None:
        """Drop another row's claim on ``existing_id``.

        A routed row's echo is deterministic — the owner's own note put it on that account,
        and the booking matches on account pair, amount and date. The batch assignment's
        claim on the same entry came from breaking a tie between equal scores, so it is the
        weaker of the two. An FX pair is exempt: its two legs share ONE transfer by
        construction (reported case: a Solstice Pay collection and a Nomad Pay payment both
        rendering a clean Link chip against one Solstice Pay transfer)."""
        if not existing_id:
            return
        for other in results:
            other_idx = other.line - 1
            if other_idx == keep or not other.flags:
                continue
            if (pair_booked.get(other_idx) or {}).get("id") == existing_id:
                continue
            other.flags = [f for f in other.flags if f.get("existing_id") != existing_id]

    def _attach(r: RowResult, reason: str, booked: dict) -> None:
        flags = r.flags if isinstance(r.flags, list) else []
        if any(
            f.get("type") == "transfer_leg_echo" and f.get("existing_id") == booked.get("id")
            for f in flags
        ):
            return
        flags.append(
            {
                "type": "transfer_leg_echo",
                "reason": reason,
                "existing_id": booked.get("id"),
                "existing": _existing_summary(booked),
            }
        )
        r.flags = flags

    for r in results:
        idx = r.line - 1
        booked = pair_booked.get(idx)
        if booked is not None:
            _attach(
                r,
                "This conversion is ALREADY BOOKED as a transfer "
                f"on {booked.get('date')} — importing again would double it.",
                booked,
            )
        booked = routed_booked.get(idx)
        if booked is not None and r.draft is not None and r.draft.get("routing_automation_id"):
            _release(booked.get("id"), idx)
            _attach(
                r,
                "An Automation books this as a transfer, and that transfer "
                f"is ALREADY in your ledger ({booked.get('date')}) — importing again "
                "would double it.",
                booked,
            )


def _routed_booked_transfers(results: list[RowResult]) -> dict[int, dict]:
    """Row index → the already-booked ledger transfer a ROUTED draft (SI-66) duplicates —
    e.g. the alert/email agent already booked this exact bank→wallet movement. The batch
    match engine judges single-leg rows only, so routed transfer drafts get this
    deterministic echo check instead (the FX-pair precedent). Preview flags it
    (transfer_leg_echo); a blind commit default-skips it; an explicit merge links it.

    1:1 across the batch (reported bug): each booked transfer is claimable
    ONCE — candidates are gathered per row (closest-date-first), then rows are assigned
    best-gap-first with a claimed set, mirroring the engine's global claim discipline. Two
    same-amount routed rows can never both flag the same booking; the loser takes its next
    free candidate or none (imports as its own transfer)."""
    per_row: list[tuple[int, list[tuple[int, dict]]]] = []
    pool: list[dict] | None = None
    for r in results:
        d = r.draft
        if d is None or not d.get("routing_automation_id"):
            continue
        if pool is None:
            pool = _booked_transfer_pool()
        ranked = _find_booked_transfers(
            d["from_account_id"],
            d["to_account_id"],
            float(d.get("from_amount") or 0.0),
            float(d.get("to_amount") or 0.0),
            d.get("date") or "",
            pool,
        )
        if ranked:
            per_row.append((r.line - 1, ranked))

    claims = sorted(
        (
            (days, row_idx, rank, t)
            for row_idx, ranked in per_row
            for rank, (days, t) in enumerate(ranked)
        ),
        key=lambda c: (c[0], c[1], c[2]),
    )
    found: dict[int, dict] = {}
    claimed: set[str] = set()
    for _days, row_idx, _rank, t in claims:
        if row_idx in found or t["id"] in claimed:
            continue
        found[row_idx] = t
        claimed.add(t["id"])
    return found


def _pair_existing_transfers(rows: list[CanonicalRow], ctx: _RoutingContext) -> dict[int, dict]:
    """Row index (either leg) → the already-booked ledger transfer its FX pair duplicates."""
    found: dict[int, dict] = {}
    pool: list[dict] | None = None
    for idx, p in ctx.pairs.items():
        if p["leg"] != "out":
            continue
        if pool is None:
            pool = _booked_transfer_pool()
        t = _existing_pair_transfer(rows[idx], rows[p["partner"]], ctx, pool)
        if t is not None:
            found[idx] = t
            found[p["partner"]] = t
    return found


def _routing_context(
    rows: list[CanonicalRow], account_id: str | None, meta: StatementMeta | None = None
) -> _RoutingContext:
    """No account (detect-mode preview that found no anchor) → a NULL context: nothing routes,
    nothing books; rows draft account-less for display and the payload asks for an account."""
    if not account_id:
        default_ccy = ((meta.currency if meta else None) or ledger_service.base_currency()).upper()
        return _RoutingContext("", default_ccy, {}, {})
    resolved = resolve_account(account_id, None)
    acct = ledger_service.accounts.find_by_id(resolved)
    default_ccy = ((acct or {}).get("currency") or ledger_service.base_currency()).upper()
    route = _route_accounts(resolved)
    pairs = _pair_fx_transfers(rows, route, default_ccy)
    return _RoutingContext(resolved, default_ccy, route, pairs)


def _offer_rows(
    result: ParseResult,
    ctx: _RoutingContext,
    prefix: str,
    routing: ImportRouting | None = None,
) -> list[RowResult]:
    """Row mapping for the DECISION preview: unlike the legacy path, a low-confidence row is
    OFFERED (flagged needs-eyes, default skip) instead of silently dropped. Each row drafts
    against its ROUTED wallet (its own currency); an unroutable currency drafts account-less
    for display and can never default-book. ``routing`` (SI-66 routing rules) reshapes a
    matching row into a two-leg transfer draft — except an FX-pair row, which the pair
    machinery owns."""
    category_rows = category_rows_for(result.rows)
    results: list[RowResult] = []
    for i, row in enumerate(result.rows, start=1):
        if row.skip:
            results.append(RowResult(line=i, skip_reason=row.skip_reason or "unreadable row"))
            continue
        target = ctx.target_for(row)
        row_ccy = ctx.row_currency(row)
        results.append(
            RowResult(
                line=i,
                draft=_row_to_draft(
                    row,
                    (target or {}).get("id") or "",
                    row_ccy,
                    prefix,
                    category_rows=category_rows,
                    routing=routing if (i - 1) not in ctx.pairs else None,
                ),
            )
        )
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


def _flag_policy_suppressions(
    candidates: list[RowResult],
    derived: dict[int, list[str]],
    context: "matching.BatchContext",
    policy: matching.MatchPolicy,
) -> None:
    """BLOCKING mitigation (security review 2026-07-29 #10) for the failure mode the RFC's own
    "monotone-tightening ⇒ safe" argument MISSED.

    A tightening policy is not quietly safe, it is LOUD in two directions:

    * it MANUFACTURES duplicates — ``decision_commit``'s ``default_action`` is ``"create"``, so a
      policy that suppresses every proposal yields a preview that looks perfectly clean and
      books a re-import of last month as N brand-new transactions;
    * it REDIRECTS merges — removing a row's true candidate hands it the global assignment's
      second-best, a DIFFERENT real ledger row, which ``apply_import_merge(facts="statement")``
      then re-dates and re-amounts.

    So the policy is diffed against ``MatchPolicy()`` (one extra pure-compute pass, only when a
    policy is actually in play) and every row whose engine assignment DISAPPEARED or CHANGED is
    flagged and marked ``policy_suppressed`` — which makes it default to SKIP rather than
    create. The owner sees what their notes took away instead of a clean-looking preview."""
    if policy == matching.MatchPolicy():
        return
    from backend.services.import_anomalies import _existing_summary

    baseline = matching.match_batch([r.draft for r in candidates], context=context, weak_tier=True)
    for r, rm in zip(candidates, baseline):
        cand = rm.assigned
        if cand is None or not cand.id or cand.klass not in _ASSIGNABLE_CLASSES:
            continue
        kept_ids = derived.get(r.line - 1)
        kept = kept_ids[0] if kept_ids else None
        if kept == cand.id:
            continue
        r.draft["policy_suppressed"] = True
        r.flags.append(
            {
                "type": "policy_suppressed",
                "reason": (
                    "Your notes for this source point this row at a different ledger entry "
                    "than the matcher proposed — left out by default; include it to book it."
                    if kept
                    else "Your notes for this source rule out the match the matcher proposed "
                    "for this row — left out by default; include it to book it as new."
                ),
                "suppressed_id": cand.id,
                "existing": _existing_summary(cand.txn),
                "band": rm.band,
                # None = the policy removed the match outright; an id = it moved to that row.
                "replaced_by": kept,
            }
        )


def _apply_batch_match(
    results: list[RowResult],
    ctx: _RoutingContext,
    meta: StatementMeta,
    *,
    complete_ok: bool,
    policy: matching.MatchPolicy = matching.MatchPolicy(),
) -> dict[int, list[str]]:
    """Replace the per-row engine scan's near_duplicate/transfer_pair/transfer_leg_echo flags
    (attached just above by ``build_preview``'s ``detect_anomalies`` call — kept ONLY for its
    unrelated ``amount_anomaly`` check, which stays exactly as-is) with
    ``matching.match_batch``'s batch-aware verdict (RFC batch-aware-dedup): the whole statement
    is judged as ONE unit — global 1:1 exclusivity (ADR #30, now the engine's own) plus
    books-close promotion for a unique exact-amount pairing inside the statement's own complete
    window. Mutates ``r.flags`` in place; a row with no draft, or an exact L0 duplicate, is
    left untouched (it never entered the old per-row scan either).

    ``complete_ok`` is the RECONCILE GATE's verdict (``_completeness_ok``): the completeness
    claim additionally requires the statement to have proven itself (balances reconcile, or a
    bank-complete export with a declared period and clean parse) — a printed period alone no
    longer suffices.

    A ROUTED two-leg transfer draft (SI-66) never enters the batch: the engine judges
    single-leg statement rows (a transfer only ever appears as an existing CANDIDATE), and a
    two-leg incoming would echo-claim against its own draft (no id). Routed drafts get the
    deterministic ``_routed_booked_transfers`` check instead.

    ``weak_tier=True`` (RFC 2026-07-29 §D2) — the ONE surface that opts into the banded
    settled-drift tier, replacing ``ledger_diff``'s retired twin matcher. It is legitimate HERE
    and nowhere else: a statement is a complete printed record of its own accounts over its own
    window, so a booked estimate sitting 2.6% off a settled figure is evidence rather than noise.

    RETURNS the per-row DERIVED CANDIDATE map ``{row index: [existing ids, best-first]}`` — the
    row's own assigned id followed by ``RowMatch.alternatives`` (the engine's narrow near-tie
    set: same class, ≤0.02 score delta, unclaimed by any other row). This is DISPLAY + AUTHORITY
    SCOPING only, never a merge acceptance set: ``decision_commit`` accepts a merge onto the
    row's assigned id or an uncovered manual-link target and nothing else (security review
    2026-07-29 #1), and ``lucius_pass._validate_ops`` uses this map to bound ``force_match``'s
    reach to ids the engine actually derived for that row (#8).

    ``policy`` (RFC §D5/§D6, Phase 3) is the owner's own notes as arithmetic — resolved from the
    four layers by ``match_policy.resolve_policy`` and monotone-TIGHTENING against the engine's
    class caps. It is diffed against ``MatchPolicy()`` by ``_flag_policy_suppressions`` so what
    it took away is SURFACED rather than silently absent (#10)."""
    candidates = [
        r
        for r in results
        if r.draft is not None and not r.duplicate and r.draft.get("kind") != "transfer"
    ]
    for r in candidates:
        r.flags = [
            f
            for f in r.flags
            if f.get("type") not in _MATCH_FLAG_TYPES and f.get("type") != "policy_suppressed"
        ]
        r.draft.pop("policy_suppressed", None)
    if not candidates:
        return {}

    from backend.services.import_anomalies import _existing_summary
    from backend.services.statements.ledger_diff import _window

    window = _window(meta.to_dict())
    account_ids = frozenset({a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""}))
    # No printed period → no completeness claim; books-close promotion is then a guaranteed
    # no-op (matching._promote_books_close's own `not context.window` guard).
    context = matching.BatchContext(
        complete=window is not None and complete_ok, window=window, account_ids=account_ids
    )
    row_matches = matching.match_batch(
        [r.draft for r in candidates], context=context, policy=policy, weak_tier=True
    )
    derived: dict[int, list[str]] = {}
    opening_ids = ledger_service.opening_txn_ids()
    for r, rm in zip(candidates, row_matches):
        cand = rm.assigned
        if cand is None or not cand.id:
            continue  # no assignment, or the winning claim was a SIBLING row, not a real txn
        if cand.klass not in _ASSIGNABLE_CLASSES:
            continue
        # The match picker's option list, server-chosen and best-first. Each entry carries its
        # OWN summary: the engine's candidate pool is ±FETCH_WINDOW_DAYS across ALL accounts,
        # which is wider than anything the client can look an id up in, so a bare id list would
        # force the client to silently drop options it cannot render (RFC §D3 amendment (a)).
        # `_existing_summary` is the same projection `existing` below uses — original_amount /
        # original_currency included, so a foreign charge's two sides are comparable on screen.
        by_id = {c.id: c.txn for c in rm.candidates if c.id}
        alternatives = [
            {"id": alt_id, "existing": _existing_summary(by_id[alt_id], opening_ids)}
            for alt_id in rm.alternatives
            if alt_id in by_id
        ]
        derived[r.line - 1] = [
            cand.id,
            *(alt_id for alt_id in rm.alternatives if alt_id in by_id),
        ]
        r.flags.append(
            {
                "type": cand.klass.value,
                # Human-readable ONLY — this rides straight into the ImportModal UI verbatim
                # (the collapsed match line + expanded detail note). The raw class/score
                # debug pair is redundant with the flag's own `klass`/`score` fields below
                # anyway; a caller that wants it (e.g. the add_transactions tool result, which
                # reports back to the model, not a user) builds its own string instead of
                # reusing this one.
                "reason": "; ".join(cand.reasons),
                "existing_id": cand.id,
                "existing": _existing_summary(cand.txn, opening_ids),
                "score": cand.score,
                "klass": cand.klass.value,
                "band": rm.band,
                "promoted": rm.promoted,
                "alternatives": alternatives,
            }
        )
    _flag_policy_suppressions(candidates, derived, context, policy)
    return derived


def _resync_match_summary(summary: dict, results: list[RowResult]) -> None:
    """Recompute the near_duplicate/transfer_pair/transfer_leg_echo/flagged_rows counters in
    the legacy ``anomaly_summary`` block after ``_apply_batch_match`` replaces those flags —
    ``amount_anomaly``/``intra_batch_duplicate`` (untouched by the batch match) stay exactly as
    ``build_preview`` computed them."""
    counts = dict.fromkeys(_MATCH_FLAG_TYPES, 0)
    flagged = 0
    for r in results:
        if r.draft is None or r.duplicate:
            continue
        row_flagged = False
        for f in r.flags:
            ftype = f.get("type")
            if ftype in counts:
                counts[ftype] += 1
                row_flagged = True
            elif ftype == "amount_anomaly":
                row_flagged = True
        if row_flagged:
            flagged += 1
    summary.update(counts)
    summary["flagged_rows"] = flagged


def _instructions_hash(text: str) -> str | None:
    """Identity of the owner guidance a parse consumed — stamped on the statement record as
    ``parse_instructions_hash`` (None/absent = parsed without instructions). A cached parse
    is reusable only while the run's EFFECTIVE instructions hash the same; otherwise the
    guidance never steered the extraction and the cache must be busted."""
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


STALE_POLICY_ERROR = (
    "the matching policy for this statement changed since it was previewed — re-run the "
    "preview so you approve the matches that will actually book"
)


def _routed_accounts(ctx: _RoutingContext) -> dict[str, str]:
    """``{account_id: name}`` for the accounts THIS statement routes into — the only set a
    policy's ``blocked_account_names`` may ever resolve against (security review #5). The
    extraction prompt never sees it; it is applied after the model has spoken."""
    out = {a["id"]: str(a.get("name") or "") for a in ctx.route.values()}
    if ctx.resolved and ctx.resolved not in out:
        anchor = ledger_service.accounts.find_by_id(ctx.resolved) or {}
        out[ctx.resolved] = str(anchor.get("name") or "")
    return out


def _policy_layers(account_id: str | None, source_fields: dict | None) -> list:
    """§D6's layer stack, broadest first: global statement skills, account-scoped statement
    skills, then this source's own extracted fields. ``MatchPolicy()`` is layer 1 by being the
    dataclass's own defaults. Every layer is re-clamped through the ``_EXTRACTABLE_FIELDS``
    allowlist inside ``resolve_policy`` — skill metadata is an AI-writable channel, so it is
    held to exactly the same bar as the model's own reply (#4)."""
    from backend.services.statements import skills_service

    layers = list(skills_service.match_policy_layers(account_id))
    if source_fields is not None:
        layers.append((policy_service.SOURCE_LAYER, source_fields))
    return layers


@dataclass
class _ResolvedPolicy:
    """One preview's resolved match policy, plus everything the record and the response need."""

    policy: matching.MatchPolicy
    provenance: dict
    # The SOURCE layer's own extracted fields — stamped on the record so ``decision_commit``
    # re-resolves every layer with ZERO LLM (#7). None = no source layer applied.
    source_fields: dict | None = None
    note: str = ""
    llm_calls: int = 0
    cost_usd: float = 0.0
    # none · skills · cached · derived · unavailable — an extraction that did NOT happen must be
    # distinguishable from one that found nothing, or a provider outage reads as "no policy".
    status: str = "none"
    # Extraction attempts spent on THIS statement record (see _MAX_POLICY_ATTEMPTS).
    attempts: int = 0


def _cached_policy_blob(record: dict, content: bytes, fmt: str, instr_hash: str | None):
    """This source's extraction without an LLM call, from either cache, or None.

    TWO levels, and they cover different things (R6's correction — "cached forever" was wrong):

    * the STATEMENT RECORD's own stamp — the same FILE re-previewed. This is the level that
      makes a re-preview genuinely free, and it is the only one that works for a source with no
      fingerprint at all (a csv/xlsx before its profile is learned, a PDF with too little
      template text), which is precisely the case R6 flagged as re-deriving on every preview.
    * the FORMAT PROFILE — a NEW file from a source already known, which is the reuse §D5 is
      really after ("type it once, it steers every future import")."""
    if (
        instr_hash
        and record.get("match_policy_instructions_hash") == instr_hash
        and record.get("match_policy_source") is not None
    ):
        return {
            "fields": dict(record["match_policy_source"]),
            "note": str(record.get("match_policy_note") or ""),
        }
    from backend.services.statements import profiles

    return profiles.find_match_policy(content, fmt, instr_hash)


def _resolve_preview_policy(
    record: dict,
    content: bytes,
    fmt: str,
    instructions: str,
    instr_hash: str | None,
    ctx: _RoutingContext,
    account_id: str | None,
) -> _ResolvedPolicy:
    """Resolve §D6's four layers for THIS preview, deriving the source layer at most once.

    Both caches are consulted before the model is (see ``_cached_policy_blob``), and a MISS is
    additionally bounded by ``_MAX_POLICY_ATTEMPTS`` — the ``hints_attempts`` precedent, for the
    same reason (owner incident 2026-07-27). Previews are model-driven and retryable, so a
    source whose extraction keeps failing would otherwise re-bill on every single preview
    forever; a source whose extraction SUCCEEDS is stamped on the record and never retried at
    all, so the cap only ever bites on repeated failure. The call is billed inside
    ``collect_llm_cost`` so the per-import AI-cost line and the duplicate-call tripwire see it."""
    source_fields: dict | None = None
    note = ""
    calls = 0
    cost = 0.0
    status = "none"
    attempts = int(record.get("match_policy_attempts") or 0)
    if instructions:
        blob = _cached_policy_blob(record, content, fmt, instr_hash)
        status = "cached"
        if blob is None:
            if attempts >= _MAX_POLICY_ATTEMPTS:
                # The budget is spent on a source that keeps failing to extract. Stop billing.
                # This is NOT an `except -> MatchPolicy()`: the preview genuinely matches under
                # whatever the remaining layers resolve to, and stamps THAT, so commit
                # re-resolves to the same policy and agrees rather than silently widening.
                logger.info("statement match policy: extraction budget spent — no source layer")
                status = "unavailable"
            else:
                from backend.core.cost_scope import collect_llm_cost

                attempts += 1
                with collect_llm_cost() as spent:
                    blob = policy_service.derive_extraction(instructions)
                calls, cost = spent["calls"], spent["cost_usd"]
                if blob is None:
                    # Provider unreachable, or nothing valid after reasks. Cache NOTHING — never
                    # a stamp claiming a policy was derived — and let the next preview retry
                    # until the budget above runs out.
                    status = "unavailable"
                else:
                    from backend.services.statements import profiles

                    status = "derived"
                    attempts = 0  # a success is stamped; the failure budget resets with it
                    profiles.remember_match_policy(content, fmt, blob, instr_hash)
        if blob is not None:
            source_fields = dict(blob.get("fields") or {})
            note = str(blob.get("note") or "")
    layers = _policy_layers(account_id, source_fields)
    if status == "none" and layers:
        status = "skills"
    policy, provenance = policy_service.resolve_policy(
        layers, routed_accounts=_routed_accounts(ctx)
    )
    return _ResolvedPolicy(
        policy=policy,
        provenance=provenance,
        source_fields=source_fields,
        note=note,
        llm_calls=calls,
        cost_usd=cost,
        status=status,
        attempts=attempts,
    )


def _commit_match_policy(
    record: dict, ctx: _RoutingContext, account_id: str | None
) -> matching.MatchPolicy:
    """The policy ``decision_commit`` matches under — RE-RESOLVED from the deterministic layers
    (zero LLM, #7) and checked against the preview's stamp (#6).

    ``match_policy_hash`` covers the RESOLVED policy AND the sorted routed-account set, not
    ``parse_instructions_hash``: that hash covers only the last of four layers, so editing a
    statement skill's ``metadata.match_policy`` between preview and commit would move the
    acceptance set while leaving it untouched.

    There is deliberately NO ``except -> MatchPolicy()`` on this path. Absence yields the
    LOOSEST policy, which accepts merges the reviewed preview refused — the dangerous direction.
    A record stamped before Phase 3 that parsed under instructions is REFUSED outright, and so
    is one whose re-resolved layers no longer produce the reviewed policy."""
    routed_ids = sorted({a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""}))
    layers = _policy_layers(account_id, record.get("match_policy_source"))
    policy, _provenance = policy_service.resolve_policy(
        layers, routed_accounts=_routed_accounts(ctx)
    )
    stamped = record.get("match_policy_hash")
    if stamped is None:
        # Pre-Phase-3 record. Instructions steered its parse, or the layers we just re-resolved
        # are non-default (a statement skill gained a policy since) → the preview the owner
        # approved was not judged under this policy. Refuse rather than guess.
        if record.get("parse_instructions_hash") or policy != matching.MatchPolicy():
            raise StatementParseError(STALE_POLICY_ERROR)
        return matching.MatchPolicy()
    if stamped != policy_service.policy_hash(policy, routed_ids):
        raise StatementParseError(STALE_POLICY_ERROR)
    return policy


@dataclass(frozen=True)
class CachedMatchPass:
    """One statement's match verdict, re-derived from its CACHED parse — see
    ``rederive_match_pass``."""

    ctx: _RoutingContext
    results: list[RowResult]
    lucius_ops: list[dict]
    routing: ImportRouting | None


def rederive_match_pass(
    record: dict, rows: list[CanonicalRow], account_id: str | None
) -> CachedMatchPass:
    """Re-derive "what this statement accounts for" from the CACHED parse — the ONE derivation
    every post-preview caller shares (RFC 2026-07-29, Phase 4 / divergence #5).

    Same deterministic inputs as the preview (cached rows + the chosen account + the record's
    own reconcile verdict and policy stamp), same order — routing rules at draft birth, then
    ``matching.match_batch`` through ``_apply_batch_match``, then the CACHED Lucius ops the
    preview showed (SI-68: read off the record, never re-derived from an LLM, never
    caller-supplied). Zero LLM throughout: the policy is re-resolved from the deterministic
    layers and refused outright on a stale stamp (``STALE_POLICY_ERROR``), never quietly widened
    back to ``MatchPolicy()``.

    Two consumers, and they must not disagree: ``decision_commit`` (which merges against it) and
    ``ledger_diff._covered_ids`` (which subtracts it from the ledger for the post-commit "in your
    ledger, not on this statement" panel). Before Phase 4 the panel ran its own per-row
    ``import_anomalies.detect_anomalies`` scan instead — no weak band, no policy, no batch 1:1
    assignment — so the same statement had two different answers before and after commit.

    NOT included, deliberately: ``_attach_booked_echo_flags``. The FX-pair / routed-transfer
    booked-twin flags need maps the two callers build at different points, and attaching them
    here would silently widen ``decision_commit``'s merge acceptance set (its
    ``assigned_by_index`` is read off the flags BEFORE those attach). Each caller attaches them
    itself, at the same point in its own sequence as the preview does."""
    meta = StatementMeta(**(record.get("import_meta") or {}))
    fmt = record.get("format") or "csv"
    ctx = _routing_context(rows, account_id)
    routing = import_routing()
    results = _offer_rows(
        ParseResult(meta=meta, rows=rows, tier=record.get("tier") or "text"),
        ctx,
        prefix=fmt,
        routing=routing,
    )
    # The returned derived-candidate map is deliberately DISCARDED: it is a preview/display
    # derivation (the match picker's options + force_match's reach), and neither consumer here
    # takes its authority from it — see `_apply_batch_match`'s own docstring.
    _apply_batch_match(
        results,
        ctx,
        meta,
        complete_ok=_completeness_ok(record.get("reconcile"), meta, fmt),
        policy=_commit_match_policy(record, ctx, account_id),
    )
    ops = lucius_pass.active_ops(record)
    lucius_pass.apply_lucius_ops(results, rows, ops, ctx)
    return CachedMatchPass(ctx=ctx, results=results, lucius_ops=ops, routing=routing)


def _policy_suppression_rows(results: list[RowResult]) -> list[dict]:
    """The rows a policy took a proposal away from (#10) — "policy suppressed N proposals",
    with the affected rows, so a preview that looks clean because everything was suppressed is
    not mistaken for a preview that is clean."""
    out: list[dict] = []
    for r in results:
        for f in r.flags or []:
            if f.get("type") == "policy_suppressed":
                out.append(
                    {
                        "index": r.line - 1,
                        "suppressed_id": f.get("suppressed_id"),
                        "existing": f.get("existing"),
                        "replaced_by": f.get("replaced_by"),
                    }
                )
    return out


def decision_preview(
    content: bytes,
    fmt: str,
    account_id: str | None,
    *,
    filename: str = "",
    mime: str = "",
    mapping: dict | None = None,
    instructions: str | None = None,
    remember_instructions: bool = True,
    force_reparse: bool = False,
    force_relearn: bool = False,
    password: str | None = None,
    source_attachment_id: str | None = None,
) -> dict:
    """Parse (cheapest tier, format memory) + guards + enrich + match — then CACHE the parse
    on a Statement record and return everything the review UI needs. Writes no transactions.

    ``force_relearn`` is the "force reprocess" escape hatch (ImportModal's checkbox above
    "Let Lucius do it instead"): it ignores this source's saved tabular format profile
    entirely — even a sticky ``manual``-origin one — and re-runs cold LLM schema inference,
    whose result overwrites the profile in place. Implies ``force_reparse`` (a stale cached
    parse under the old schema must never be served back).
    ``password`` unlocks an encrypted xlsx — the statements/import path never had this before
    (it surfaced a raw ``zipfile.BadZipFile`` message with no hint it was a password problem);
    every other format ignores it.

    ``source_attachment_id`` (cross-channel-attachments RFC item 22,
    ``STATEMENT-ATTACHMENT-BOUNDARY``): when this content came from a chat attachment
    (``import_transactions(attachment_ref=...)``), the caller passes that attachment's id so
    the statement record it creates/dedups-onto records its provenance. Promotion is
    idempotent by construction — ``StatementStore.create`` dedups by content hash, so
    promoting the SAME attachment twice never mints two statement records.

    ``mapping`` (csv/xlsx only) is the MANUAL-MAPPING teach flow (SI-16 → decision entrance):
    the user's confirmed column mapping is saved as this source's format profile FIRST —
    keyed by the header-row fingerprint exactly like an inferred profile — and the parse then
    hits that profile (zero LLM), so the hand-mapping user gets the byte-identical decision
    review and the NEXT import of this layout needs no mapping at all. An explicit mapping
    also busts the cached-parse reuse below: a hand-map must override whatever an earlier
    inference cached for this same file.

    ``instructions`` (SI-67, upload-time owner guidance) is remembered on this source's
    format profile (keyed like mappings) and threaded — FENCED, never as instructions the
    model must obey from untrusted context — into the two LLM calls only (row extraction +
    the category-hint batch). Deterministic parses simply store them. Omitted/blank → the
    profile's remembered instructions apply. ``remember_instructions=False`` makes the
    typed text a ONE-OFF: it applies to this run identically but is never persisted, and
    instructions already saved on the profile survive untouched (the scanned-PDF
    apply-without-save shape). The record stamps the hash of the instructions its cached
    parse consumed: a preview whose effective instructions hash differently — or an
    explicit ``force_reparse`` — invalidates the cached parse and re-reads under the new
    guidance (re-bills, shown on the AI-cost line) with the once-per-file hint gate
    re-opened; identical instructions stay cached and free.

    The response is the legacy PreviewResult dict (back-compat) PLUS the decision payload:
    ``statement_id · meta · reconcile · tier · rows[] (ALL rows, uncapped) · needs_eyes ·
    account_suggestion · format_profile · already_imported · instructions``."""
    from backend.services.statements import profiles
    from backend.services.statements.source_guard import validate_preview_source

    validate_preview_source(content, source_attachment_id)

    # The record + its progress binding come FIRST, before anything that can raise (mapping
    # validation below, in particular) — a failure has somewhere to persist `failed` even
    # when it happens before a single byte gets parsed (security review finding H1: an async
    # job whose mapping was bad used to leave its record stamped `parsing` forever, since
    # nothing had bound the channel to an id yet for the persistence hook to fire against).
    store = _statement_store()
    record = store.create(
        filename or "statement",
        mime or "",
        content,
        {"kind": "import"},
        source_attachment_id=source_attachment_id,
    )
    claim_for_account(record["id"], account_id)
    parse_progress.bind_statement(
        record["id"], on_update=lambda snap: _persist_parse_progress(record["id"], snap)
    )

    if mapping and fmt in ("csv", "xlsx"):
        from backend.services import import_csv, import_xlsx

        # VALIDATE the caller's mapping before anything learns from it.
        # learn_from_manual_mapping is best-effort MEMORY: it builds its schema with a dict
        # comprehension that silently DROPS any field whose column it can't find, and accepts
        # the result on `date` + any 2 hits. That leniency was harmless while it was only a
        # cache-warming side road, but bc66197c routed every csv/xlsx import through this
        # pipeline and made it the ONLY consumer of the mapping — so the leniency silently
        # became the validation, and validate_mapping (which raises a precise, actionable
        # "Mapped column 'X' is not in the file. Available columns: ...") went unreachable
        # from production entirely.
        #
        # The failure was not cosmetic: a typo'd `description` column parsed clean, the row
        # fell back to the literal "Imported transaction", and that placeholder is hashed
        # into external_ref (importer.external_ref) — so re-importing the SAME file with a
        # CORRECTED mapping produced a different external_ref and duplicated every row instead
        # of L0-deduping. Worse, the derived schema is persisted as a Tier-0 format profile keyed
        # by header fingerprint, so one bad mapping silently blanked the description of every
        # future import from that source, at confidence 1.0 with needs_eyes false.
        #
        # Passing the CLEANED mapping on is deliberate: validate_mapping resolves each name
        # to the file's real column via normalized matching (it finds "Debit" inside
        # "Số tiền ghi nợ/\nDebit"), which profiles._normalize_cell's exact match does not —
        # closing a second, quieter version of the same drop.
        if fmt == "csv":
            columns = import_csv.detect_headers(content)
        else:
            columns = import_xlsx.detect_headers(content, password=password)
        mapping = import_csv.validate_mapping(columns, mapping)
        if profiles.learn_from_manual_mapping(content, fmt, mapping, password=password) is None:
            raise StatementParseError(
                "The column mapping didn't line up with this file's header row — "
                "re-check the mapped column names."
            )
    else:
        mapping = None  # a mapping is meaningless for structured/pdf sources
    applied_instructions = (instructions or "").strip()
    # "Remember for this source" unchecked → the typed text is a ONE-OFF: it steers THIS
    # run only, is never persisted, and never overwrites instructions already saved.
    one_off = bool(applied_instructions) and not remember_instructions
    if applied_instructions:
        if not one_off:
            # Remember-at-preview, like the mapping teach: typing instructions once covers
            # every future import of this source. Best-effort — persistence failure never
            # blocks.
            profiles.remember_instructions(
                content, fmt, applied_instructions, source_label=filename
            )
    else:
        applied_instructions = profiles.find_instructions(content, fmt)
    instr_hash = _instructions_hash(applied_instructions)
    already = None
    # A rolled-back import booked nothing that is still there — not "already imported".
    live_batches = statement_list.live_batch_ids(record)
    if live_batches or record.get("filed_at"):
        already = {
            "statement_id": record["id"],
            "imported_at": record.get("parsed_at") or record.get("created_at"),
            "batch_ids": live_batches,
        }
    # The review this file already finished is returned as it was left; only an explicit ask
    # (a re-read, a mapping, another account, changed instructions) builds a new one.
    if mapping is None and not force_reparse and not force_relearn:
        kept = stored_previews.recall(
            record["id"],
            fmt=fmt,
            instr_hash=instr_hash,
            account_id=account_id,
            record=record,
            one_off=one_off,
        )
        if kept is not None:
            return kept
    if (
        record.get("import_rows")
        and record.get("format") == fmt
        and mapping is None
        and not force_reparse
        and not force_relearn
        and record.get("parse_instructions_hash") == instr_hash
    ):
        # Re-preview of a file we already parsed (e.g. switching the destination account so
        # the ACCOUNT-SCOPED dedup flags recompute) — reuse the cached parse: zero LLM calls,
        # instant. Dedup/enrich below always recompute against the CHOSEN account. Reuse
        # requires the effective instructions to match what the cached parse consumed —
        # changed guidance (or an explicit force_reparse) must actually steer a fresh read.
        result = ParseResult(
            meta=StatementMeta(**(record.get("import_meta") or {})),
            rows=[CanonicalRow(**r) for r in record["import_rows"]],
            tier=record.get("tier") or "text",
            llm_calls=0,
            reconcile=record.get("reconcile"),
        )
        # This re-preview is free — the file's parse cost stays what the ORIGINAL run spent.
        llm_cost = {
            "calls": 0,
            "cost_usd": float(record.get("llm_cost_usd") or 0.0),
            "fresh": False,
        }
        _heal_cached_last4s(result.meta, content, fmt)
        _heal_cached_reconcile(result, fmt)
        # The one heal that spends: a record whose metadata read FAILED buys ONE call to try
        # again, billed onto the file like the hint batch and the Lucius pass below.
        from backend.core.cost_scope import collect_llm_cost as _collect_heal_cost

        with _collect_heal_cost() as heal_spent:
            heal_calls = _heal_cached_meta(result.meta, content, fmt)
        llm_cost["calls"] += heal_spent["calls"]
        llm_cost["cost_usd"] += heal_spent["cost_usd"]
    else:
        from backend.core.cost_scope import collect_llm_cost
        from backend.services.statements import skills_service

        heal_calls = 0  # a fresh parse read its own metadata — nothing to heal
        if record.get("import_rows") and (
            force_reparse or force_relearn or record.get("parse_instructions_hash") != instr_hash
        ):
            # The cached parse was read under DIFFERENT instructions (or a forced re-read):
            # re-open the hint gate (full budget) so the category-hint batch also reruns
            # under the new guidance. Mapping-driven busts keep the count unchanged.
            record["hints_attempts"] = 0
        guidance, skill_names = skills_service.guidance_for(account_id)
        with collect_llm_cost() as spent:
            result = parse_statement_bytes(
                content,
                fmt,
                guidance=guidance,
                instructions=applied_instructions or None,
                force_relearn=force_relearn,
                password=password,
            )
        llm_cost = {"calls": spent["calls"], "cost_usd": spent["cost_usd"], "fresh": True}
        if skill_names:
            result.meta.extra["statement_skills"] = skill_names
        if applied_instructions and not one_off:
            # A csv/xlsx first parse may only just have LEARNED its profile — stamp the
            # instructions onto it now so the fingerprint lookup finds them next time.
            profiles.remember_instructions(
                content, fmt, applied_instructions, source_label=filename
            )
    # A DOCUMENT source (pdf/image) never receives a `format_profile` stamp — parser.py sets it
    # only on the tabular path — so `_remember_source_accounts` found no profile id and the
    # Choose step's account chips had nothing to attach to. Give the source its identity and
    # carry the id on the preview. Placed after BOTH parse paths converge so a cached re-preview
    # (the common case: re-opening last month's statement) is covered too, and guarded so a
    # tabular profile learned above is never overwritten.
    if not (result.meta.extra or {}).get("format_profile"):
        _prof = profiles.ensure_source_profile(content, fmt, source_label=filename)
        if _prof:
            result.meta.extra["format_profile"] = {
                "id": _prof["id"],
                "kind": "document",
                "source_label": _prof.get("source_label"),
                "account_ids": _prof.get("account_ids") or [],
            }
    # No account chosen → DETECT the anchor from the statement's own printed account number
    # (meta.account_last4s → the user's account). The anchor drives routing exactly as an
    # explicit choice would; when nothing matches, the payload asks (needs_account) — with the
    # parse already cached, so picking an account re-previews instantly and free.
    anchor = account_id or _suggest_account(result.meta)
    ctx = _routing_context(result.rows, anchor, meta=result.meta)
    rule_routing = import_routing()
    results = _offer_rows(result, ctx, prefix=fmt, routing=rule_routing)
    legacy = asdict(build_preview([], len(result.rows), results))
    # The deterministic matcher runs FIRST (ADR 2026-07-28) — Lucius needs to see what it
    # proposed before it can veto (`reject_match`) or override (`force_match`) it; running
    # the pass blind, before a verdict even exists, left no recourse when routing missed.
    # SI-69 / RFC §D5: the owner's own notes, as ARITHMETIC the engine performs, instead of
    # prose the pass re-judges per row per import. Resolved BEFORE the matcher because the
    # policy is an INPUT to it — cached on the format profile, so a known source costs zero.
    resolved_policy = _resolve_preview_policy(
        record, content, fmt, applied_instructions, instr_hash, ctx, anchor
    )
    llm_cost["calls"] += resolved_policy.llm_calls
    llm_cost["cost_usd"] += resolved_policy.cost_usd
    parse_progress.report("match")
    derived_matches = _apply_batch_match(
        results,
        ctx,
        result.meta,
        complete_ok=_completeness_ok(result.reconcile, result.meta, fmt),
        policy=resolved_policy.policy,
    )
    _resync_match_summary(legacy["anomaly_summary"], results)
    # The Lucius draft-operations pass (SI-68): effective instructions steer the DRAFTS AND
    # now the matcher's own verdict — one bounded LLM call per (statement, instructions-hash),
    # validated hard, applied through the same deterministic reshape/label/skip/match
    # appliers rules and the matcher use. Runs AFTER rule routing (rules fire at draft-birth;
    # the pass never re-shapes a rule's work) and AFTER the matcher, BEFORE enrich, so
    # everything downstream judges the Lucius-adjusted shape.
    parse_progress.report("lucius")
    pass_outcome = lucius_pass.run_lucius_pass(
        record=record,
        instructions=applied_instructions,
        instr_hash=instr_hash,
        rows=result.rows,
        results=results,
        anchor_id=ctx.resolved,
        # force_match's reach (security review 2026-07-29 #8): the engine's own per-row
        # derivation plus the routing/meta the uncovered set is computed from. Without these
        # an injected "row 3 is the same transaction as txn_ab12…" rewrites an arbitrary
        # same-account transaction — `_validate_ops` only ever checked that the id existed.
        derived_matches=derived_matches,
        routed_account_ids=frozenset(
            {a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""})
        ),
        meta=result.meta.to_dict(),
        force=force_reparse,
    )
    lucius_applied = lucius_pass.apply_lucius_ops(
        results, result.rows, lucius_pass.merge_user_routes(record, pass_outcome.ops), ctx
    )
    # What the commit books from: every row's verdict exactly as `rederive_match_pass` would
    # produce it (matcher, then the Lucius ops — nothing after). Kept with the stored review so
    # Confirm books the reviewed decisions instead of re-matching the ledger (owner, 2026-10-26).
    commit_pass = {"anchor": anchor, "results": [asdict(r) for r in results]}
    llm_cost["calls"] += pass_outcome.llm_calls
    llm_cost["cost_usd"] += pass_outcome.cost_usd
    # A row Lucius just routed to a transfer carries a STALE match flag from the matcher pass
    # above (which ran under the row's old, unrouted kind) — apply_lucius_ops already strips
    # it for route_transfer; re-sync the summary counts to the FINAL flag set.
    _resync_match_summary(legacy["anomaly_summary"], results)

    drafts = [r.draft for r in results if r.draft]
    verdicts = enrich_drafts(drafts)
    by_line: dict[int, dict] = {}
    v_iter = iter(verdicts)
    for r in results:
        if r.draft:
            by_line[r.line] = next(v_iter)

    # Enrich-time category suggestions (owner: "lucius should be able to guess it's
    # groceries"): ONE batched completion for rows still Uncategorized with no hint — this is
    # what covers CACHED parses and the zero-LLM tabular tiers (the row-extraction prompt
    # never runs for those). Hints persist onto the cached rows below.
    #
    # BOUNDED RETRY, not once-ever (owner incident 2026-07-27): a single attempt that came
    # back with nothing — because the model declined, replied short, or the call itself
    # degraded — used to forfeit auto-categorization for that cached file PERMANENTLY, with
    # no error and no way to tell "confidently declined" apart from "this attempt was bad".
    # `hints_attempts` counts actual attempts (incremented whenever the call is MADE,
    # success or failure) up to `_MAX_HINT_ATTEMPTS`, so a degraded attempt self-heals on
    # the next re-preview instead of silently freezing the row at Uncategorized forever, while
    # still bounding re-billing on a file that's genuinely never going to get a hint.
    hint_calls = 0
    hints_attempts = record.get("hints_attempts", 0)
    parse_progress.report("categorise")
    try:
        pending = [
            (r, row)
            for r, row in zip(results, result.rows)
            if r.draft is not None
            and not r.duplicate
            and not r.draft.get("routing_automation_id")  # routed transfer needs no hint
            and not r.draft.get("lucius_skip")  # a row Lucius skips per the notes needs none
            and not row.category_hint
            and not (by_line.get(r.line) or {}).get("category")
        ]
        if pending and hints_attempts < _MAX_HINT_ATTEMPTS:
            from backend.core.cost_scope import collect_llm_cost
            from backend.services.statements.parser import run_async
            from backend.services.statements.tiers import suggest_category_hints

            hints_attempts += 1
            with collect_llm_cost() as hint_spent:
                hints = run_async(
                    suggest_category_hints(
                        [row.description or row.payee or "" for _, row in pending],
                        instructions=applied_instructions or None,
                    )
                )
            hint_calls = hint_spent["calls"]
            llm_cost["calls"] += hint_spent["calls"]
            llm_cost["cost_usd"] += hint_spent["cost_usd"]
            changed = []
            for (r, row), hint in zip(pending, hints):
                if hint:
                    row.category_hint = hint
                    r.draft["category_hint"] = hint
                    changed.append(r)
            if changed:
                for r, verdict in zip(changed, enrich_drafts([r.draft for r in changed])):
                    by_line[r.line] = verdict
    except Exception:  # noqa: BLE001 — suggestions are a nicety, never a preview-breaker
        logger.exception("decision_preview: category-hint pass failed")

    pair_booked = _pair_existing_transfers(result.rows, ctx)
    routed_booked = _routed_booked_transfers(results)
    _attach_booked_echo_flags(results, pair_booked, routed_booked)
    # AFTER the echo attach, never before: that pass is what releases a row whose entry a
    # routed row has a stronger claim on, and a row still holding a stale link does not read
    # as unlinked.
    _match_leftovers(results, ctx, result.meta)
    _resync_match_summary(legacy["anomaly_summary"], results)
    rows_payload: list[dict] = []
    needs_eyes = 0
    currencies_seen: list[str] = []
    for r, row in zip(results, result.rows):
        target = ctx.target_for(row) if not row.skip else None
        row_ccy = ctx.row_currency(row)
        if not row.skip and row_ccy not in currencies_seen:
            currencies_seen.append(row_ccy)
        # Routing/pair flags attach HERE (post-anomaly-pass — detect_anomalies resets r.flags).
        # "No wallet for this currency" is only meaningful once an anchor exists — in
        # needs-account mode the ask is the account itself, not per-row flags.
        flags = list(r.flags or [])
        if (r.line - 1) in ctx.pairs:
            # The fx-pair machinery OWNS these rows: the anomaly layer's draft-vs-draft
            # transfer_pair (existing_id null — it matched the SIBLING ROW of this same
            # file) is redundant noise that reads as importable. Drop it; the real ledger
            # check follows.
            flags = [
                f
                for f in flags
                if not (f.get("type") == "transfer_pair" and not f.get("existing_id"))
            ]
        # Routing Automations: a routed transfer draft that duplicates an ALREADY-BOOKED
        # transfer (an alert/email agent got there first) is flagged exactly like an FX
        # pair's booked twin — default skip at commit, explicit merge links it.
        routed_info = None
        if r.draft is not None and r.draft.get("routing_automation_id"):
            routed_from = ledger_service.accounts.find_by_id(r.draft["from_account_id"]) or {}
            routed_to = ledger_service.accounts.find_by_id(r.draft["to_account_id"]) or {}
            routed_info = {
                "automation_id": r.draft["routing_automation_id"],
                "from_account_id": r.draft["from_account_id"],
                "from_account_name": routed_from.get("name"),
                "to_account_id": r.draft["to_account_id"],
                "to_account_name": routed_to.get("name"),
            }
        if r.draft is not None and target is None and ctx.resolved:
            flags.append(
                {
                    "type": "no_account_for_currency",
                    "reason": f"No {row_ccy} account here — pick one on this row or it stays skipped.",
                }
            )
        pair = ctx.pairs.get(r.line - 1)
        if pair is not None and r.draft is not None:
            partner_row = result.rows[pair["partner"]]
            partner_acct = ctx.target_for(partner_row) or {}
            if pair["leg"] == "out":
                flags.append(
                    {
                        "type": "fx_transfer_pair",
                        "reason": "Same bank reference as row "
                        f"{pair['partner'] + 1} in another currency — books as ONE transfer "
                        f"into {partner_acct.get('name') or 'the paired account'}.",
                    }
                )
            else:
                flags.append(
                    {
                        "type": "fx_transfer_leg",
                        "reason": f"Second leg of row {pair['partner'] + 1} — folds into that "
                        "transfer. Include it only to book it separately.",
                    }
                )
        entry: dict = {
            "index": r.line - 1,
            "date": row.date,
            "posted_date": row.posted_date,
            "payee": row.payee,
            "description": row.description,
            "amount": row.amount if row.direction == "IN" else -row.amount,
            "currency": (r.draft or {}).get("to_currency")
            or (r.draft or {}).get("from_currency")
            or row.currency,
            "original_amount": (r.draft or {}).get("original_amount"),
            "original_currency": (r.draft or {}).get("original_currency"),
            "reference": row.reference,
            "source_row": row.source_row,
            "confidence": row.confidence,
            "notes": row.notes,
            "skip": r.draft is None,
            "skip_reason": r.skip_reason,
            "duplicate": bool(r.duplicate),
            "duplicate_of": r.duplicate_of,
            "external_refs": (r.draft or {}).get("external_refs") or [],
            "account_id": (target or {}).get("id"),
            "account_name": (target or {}).get("name"),
            "pair": ({"role": pair["leg"], "partner_index": pair["partner"]} if pair else None),
            "routed": routed_info,
            # SI-68 provenance: the Lucius-pass op that shaped this row (chip + reason +
            # per-row undo in the review UI), None for untouched rows.
            "lucius": lucius_applied.get(r.line - 1),
            "flags": flags,
            "enrich": by_line.get(r.line),
        }
        entry["needs_eyes"] = bool(r.draft) and (row.confidence < MIN_CONFIDENCE or bool(row.notes))
        needs_eyes += 1 if entry["needs_eyes"] else 0
        rows_payload.append(entry)

    routing = (
        {
            ccy: (
                {"account_id": ctx.route[ccy]["id"], "account_name": ctx.route[ccy].get("name")}
                if ccy in ctx.route
                else None
            )
            for ccy in currencies_seen
        }
        if ctx.resolved
        else {}
    )
    routed_ids = {e["account_id"] for e in rows_payload if e.get("account_id")}
    multi = len(routed_ids) > 1 or any(v is None for v in routing.values())
    policy_suppressions = _policy_suppression_rows(results)

    cache_updates: dict = {
        "hints_attempts": hints_attempts,  # bounded retry — see the pass above
        "import_rows": [row.to_dict() for row in result.rows],
        "import_meta": result.meta.to_dict(),
        "reconcile": result.reconcile,
        "tier": result.tier,
        "format": fmt,
        # What guidance the cached rows were read under — the reuse check above compares
        # against this, so a later preview with different instructions re-parses.
        "parse_instructions_hash": instr_hash,
        # RFC §D5 preview→commit contract. THREE fields, each doing a distinct job:
        #   match_policy        — the RESOLVED policy, for display and for the audit trail.
        #   match_policy_source — the SOURCE layer's extracted fields, so decision_commit can
        #                         re-resolve every layer without the file, the notes, or an LLM.
        #   match_policy_hash   — resolved policy + sorted routed accounts (NOT
        #                         parse_instructions_hash, which covers one layer of four).
        # Always stamped, even for the default policy: an ABSENT stamp is what tells commit it
        # is looking at a record from before this contract existed.
        "match_policy": policy_service.policy_to_dict(resolved_policy.policy),
        "match_policy_source": resolved_policy.source_fields,
        "match_policy_hash": policy_service.policy_hash(
            resolved_policy.policy,
            sorted({a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""})),
        ),
        # Which instructions the stamped source layer was extracted from — the same-FILE cache
        # `_cached_policy_blob` reads, so a re-preview of THIS file is free even when the source
        # has no fingerprint the format profile could key on. Written ONLY alongside a real
        # extraction, so a failure never looks like a cached empty one.
        "match_policy_instructions_hash": (
            instr_hash if resolved_policy.source_fields is not None else None
        ),
        "match_policy_note": resolved_policy.note,
        "match_policy_attempts": resolved_policy.attempts,
    }
    if pass_outcome.fresh:
        # The Lucius pass ran (SI-68): stamp the validated ops + the guidance hash they
        # answered, so an unchanged re-preview re-applies them free and decision_commit
        # books exactly the reviewed shape. Undo clears the ops but KEEPS the hash — the
        # decision "not these" is remembered without re-billing; editing the instructions
        # re-opens the pass.
        cache_updates["lucius_ops"] = pass_outcome.ops
        cache_updates["lucius_pass_hash"] = instr_hash
        cache_updates["lucius_summary"] = pass_outcome.summary
        cache_updates["lucius_verified"] = pass_outcome.verified
    if llm_cost["fresh"]:
        # What THIS file cost to read (all tiers/calls of this parse) — shown in the UI and
        # kept on the record; the global cost ledger already counted it by source.
        cache_updates["llm_cost_usd"] = round(llm_cost["cost_usd"], 6)
        cache_updates["llm_calls"] = llm_cost["calls"]
    elif hint_calls or pass_outcome.llm_calls or heal_calls:
        # A cached re-preview that ran the hint batch, the Lucius pass and/or the metadata
        # heal — the file's read cost grows by them.
        cache_updates["llm_cost_usd"] = round(llm_cost["cost_usd"], 6)
        cache_updates["llm_calls"] = (
            int(record.get("llm_calls") or 0) + hint_calls + pass_outcome.llm_calls + heal_calls
        )
    store.update_by_id(record["id"], cache_updates)
    # The PRE-COMMIT ledger check (owner: "I shouldn't have to double-check my ledger"):
    # what the ledger holds for this period + accounts that the statement does NOT — on the
    # SAME screen as the row review, before anything books. An incomplete parse or a
    # failing reconcile withholds this instead of running it (ADR
    # 2026-09-09-an-incomplete-parse-withholds-the-ledger-diff) — this branch covers BOTH
    # the fresh parse and the cached-parse reuse above, since they converge on ``result``
    # before this point.
    parse_progress.report("ledger_check")
    check_accounts = _statement_account_ids(ctx, result.rows)
    ledger_check = _withheld_ledger_check(result.meta.to_dict(), result.reconcile, result.tier)
    if ledger_check is None:
        from backend.services.statements.ledger_diff import preview_ledger_check

        try:
            ledger_check = preview_ledger_check(
                record, result.rows, results, check_accounts, meta=result.meta.to_dict()
            )
        except Exception:  # noqa: BLE001 — advisory panel; the preview never fails on it
            logger.exception("decision_preview: ledger check failed")

    response = legacy | {
        "statement_id": record["id"],
        "meta": result.meta.to_dict(),
        "reconcile": result.reconcile,
        "tier": result.tier,
        "llm_calls": result.llm_calls,
        "rows": rows_payload,
        "needs_eyes": needs_eyes,
        # An explicit caller choice must never be second-guessed by the "looks like X" banner —
        # it's driven by the same imprecise/pinnable heuristic as detect-mode itself, and a
        # one-click "Preview into X" on top of an account the user already picked silently
        # discarded that choice (reported case, Nimbus Wallet import).
        "account_suggestion": None if account_id else _suggest_account(result.meta),
        "format_profile": (result.meta.extra or {}).get("format_profile"),
        "already_imported": already,
        "routing": routing,
        "multi": multi,
        "llm_cost_usd": round(llm_cost["cost_usd"], 6),
        # Detect-mode contract: the anchor actually used (chosen or detected from the printed
        # account number), and whether the caller must supply one (parse cached → retry free).
        "resolved_account_id": ctx.resolved or None,
        "needs_account": not ctx.resolved,
        "ledger_check": ledger_check,
        # SI-67: what owner guidance applied to this preview (request-supplied or remembered
        # on the format profile). `used_in_parse` = an LLM row-extraction prompt actually saw
        # it (a cached/deterministic parse only feeds the hint batch, if that ran).
        # `remembered` = False only for a one-off (text supplied with remember unchecked).
        "instructions": (
            {
                "text": applied_instructions,
                "lines": len([ln for ln in applied_instructions.splitlines() if ln.strip()]),
                "used_in_parse": bool(llm_cost["fresh"] and result.llm_calls),
                "remembered": not one_off,
            }
            if applied_instructions
            else None
        ),
        # RFC §D5: the resolved match policy, its per-field provenance, and — the part that
        # matters most — what it TOOK AWAY. A tightening policy is not quietly safe: it
        # manufactures duplicates (default_action is "create") and redirects merges onto other
        # real ledger rows, so `suppressed` is reported loudly and those rows default to skip
        # (security review 2026-07-29 #10). `note` is the model's own one-paragraph reading of
        # the notes — sanitized and length-capped at extraction (#11).
        "match_policy": (
            {
                "status": resolved_policy.status,
                "policy": policy_service.policy_to_dict(resolved_policy.policy),
                # {policy field: winning layer} — "global-skill:<name>" /
                # "account-skill:<name>" / "source-instructions". A field no layer set is
                # absent: it is the engine's own constant.
                "provenance": resolved_policy.provenance,
                "note": resolved_policy.note,
                "suppressed": policy_suppressions,
                "llm_calls": resolved_policy.llm_calls,
                "cost_usd": round(resolved_policy.cost_usd, 6),
            }
            if (resolved_policy.status != "none" or policy_suppressions)
            else None
        ),
        # SI-68: what the Lucius pass did to this preview's drafts (per-row detail rides
        # each row's `lucius` field). `fresh` = the pass billed on THIS preview; a cached
        # re-preview re-applies the same ops free. Present whenever guidance was in play —
        # a pass that was SKIPPED or whose provider was unreachable must report itself
        # (`status`), not read as an absent block indistinguishable from "no instructions".
        "lucius_pass": (
            {
                "status": pass_outcome.status,
                # proposed -> validated -> applied: the two funnels a decision can vanish
                # into (hard validation in `_validate_ops`, then the contextual appliers in
                # `apply_lucius_ops`). Reported as three numbers, never one, so a drop is
                # visible instead of silent (owner, 2026-07-29).
                "proposed": pass_outcome.proposed,
                "validated": len(pass_outcome.ops),
                "applied": len(lucius_applied),
                "routes": sum(1 for v in lucius_applied.values() if v["op"] == "route_transfer"),
                "categories": sum(1 for v in lucius_applied.values() if v["op"] == "set_category"),
                "skips": sum(1 for v in lucius_applied.values() if v["op"] == "skip"),
                # ADR 2026-07-28: Lucius's authority over the matcher's own verdict.
                "rejected_matches": sum(
                    1 for v in lucius_applied.values() if v["op"] == "reject_match"
                ),
                "forced_matches": sum(
                    1 for v in lucius_applied.values() if v["op"] == "force_match"
                ),
                "fresh": pass_outcome.fresh,
                # The model's OWN plain-language recap (owner, 2026-07-28: "i want lucius to
                # generate a summary, not we aggregate by ourselves") — "" when nothing was
                # actionable or the model didn't produce one.
                "summary": pass_outcome.summary,
                # PR-review loop (owner, 2026-07-28): a reviewer pass checks the proposal
                # against the owner's own notes before it ever reaches this response; False
                # means it was never approved within the retry budget and `ops` (so `applied`
                # above) was degraded to empty — never surface an unverified decision.
                "verified": pass_outcome.verified,
                # What the pass itself spent, separate from the parse's own tiers — the
                # response's top-level `llm_calls` is parse-tier only.
                "llm_calls": pass_outcome.llm_calls,
                "cost_usd": round(pass_outcome.cost_usd, 6),
            }
            if (applied_instructions or lucius_applied or pass_outcome.fresh)
            else None
        ),
    }
    # Persisted BEFORE this function returns — the caller's `ready` (the progress channel's
    # mark_done, for an async job and for a sync request alike) comes after it. A payload that
    # still asks for an account is not a review anyone can act on, so it is never kept.
    if not response.get("needs_account"):
        stored_previews.keep(
            record["id"],
            fmt=fmt,
            instr_hash=instr_hash,
            account_arg=account_id,
            preview=response,
            commit_pass=commit_pass,
        )
    return response


def _manual_link_ids(
    record: dict,
    rows: list[CanonicalRow],
    results: list[RowResult],
    account_ids: frozenset[str],
    meta: StatementMeta,
) -> set[str]:
    """The ids of every UNCOVERED ledger entry (the "In your ledger, not on this statement"
    set: `preview_ledger_check`'s `rows` + `boundary`) — the safe target set for a USER-
    ASSERTED manual link (owner 2026-07-18: "let the user decide to merge these two
    themselves, not via the dedup engine"). Unlike the engine's own per-row assignment, a
    manual link accepts ANY row → ANY uncovered entry: the user is
    asserting the match the engine's gates were too tight to make. Safety is preserved by
    WHAT the set contains — only real, non-deleted transactions ALREADY in this statement's
    accounts and period window, not yet claimed by another row — plus the unchanged account-
    intersection floor at the merge site. A fabricated / covered / out-of-window / foreign id
    is not in this set and is still refused. Fails CLOSED (empty on no window / exception)."""
    from backend.services.statements.ledger_diff import preview_ledger_check

    try:
        check = preview_ledger_check(record, rows, results, set(account_ids), meta=meta.to_dict())
    except Exception:  # noqa: BLE001 — fail closed: no derivation, no manual-link acceptance
        logger.exception("decision_commit: manual-link re-derivation failed")
        return set()
    if not check:
        return set()
    return {
        str(e["id"])
        for e in ((check.get("rows") or []) + (check.get("boundary") or []))
        if e.get("id")
    }


def _apply_edits(row: CanonicalRow, edits: dict) -> CanonicalRow:
    if not edits:
        return row
    unknown = set(edits) - _EDITABLE_FIELDS
    if unknown:
        raise StatementParseError(f"uneditable field(s): {sorted(unknown)}")
    if "date" in edits:
        row.date = str(edits["date"])
    if "payee" in edits:
        row.payee = str(edits["payee"]) or None
    if "direction" in edits:
        d = str(edits["direction"]).upper()
        if d not in ("IN", "OUT"):
            raise StatementParseError("direction edit must be IN or OUT")
        row.direction = d
    if "amount" in edits:
        try:
            amt = float(edits["amount"])
        except (TypeError, ValueError):
            raise StatementParseError("amount edit must be a number")
        row.direction = "IN" if amt >= 0 else "OUT"
        row.amount = abs(amt)
    return row


def _transfer_draft(
    out_row: CanonicalRow, in_row: CanonicalRow, ctx: _RoutingContext, prefix: str
) -> dict:
    """ONE two-leg transfer from an internal FX pair — native amounts on each leg (the actual
    conversion rate is implied by the legs, never invented). The external_refs entry
    reuses the OUT row's single-draft pair, so a re-import L0-dedups the OUT row and the IN row
    echo-flags against this transfer (TRANSFER_LEG_ECHO)."""
    out_ccy = ctx.row_currency(out_row)
    in_ccy = ctx.row_currency(in_row)
    from_acct = ctx.route[out_ccy]["id"]
    to_acct = ctx.route[in_ccy]["id"]
    note = out_row.description or in_row.description or "FX transfer"
    return {
        "date": out_row.date,
        "kind": "transfer",
        "description": note,
        # ONE writer value for every format — see importer.py. This site minted "pdf_import",
        # which no roster contained, so 12 live rows skipped the review queue entirely.
        "source": "import",
        "external_refs": [
            {
                # ONE namespace for every statement format — see importer.py's mint site.
                "source": STATEMENT_NAMESPACE,
                "ref": external_ref(
                    from_acct, out_row.date, note, out_row.amount, False, out_row.reference
                ),
            }
        ],
        "from_account_id": from_acct,
        "from_amount": out_row.amount,
        "from_currency": out_ccy,
        "to_account_id": to_acct,
        "to_amount": in_row.amount,
        "to_currency": in_ccy,
    }


def decision_commit(
    statement_id: str,
    account_id: str | None,
    decisions: list[dict] | None = None,
    default_action: str = "create",
    delete_ids: list[str] | None = None,
    *,
    reviewed: bool = False,
) -> dict:
    """Serializes ``_decision_commit_locked`` — see ``_commit_lock`` for why. A statement may
    legitimately be committed more than once with different decisions, so this is NOT an
    "already committed" refusal — just mutual exclusion for the duration of one commit.

    ``reviewed`` = the owner confirmed this in the import review (the modal's Confirm): it books
    the stored review's verdicts and the owner's decisions exactly — no second matching pass and
    no fuzzy duplicate guard, only the identity floors (owner, 2026-10-26). An unattended commit
    (Lucius's import tool, the one-shot REST import) keeps re-deriving and the duplicate guard,
    because nobody looked at what it books."""
    with _commit_lock:
        outcome = _decision_commit_locked(
            statement_id, account_id, decisions, default_action, delete_ids, reviewed=reviewed
        )
    # The stored review is KEPT: while this import stands it is never served (it describes the
    # ledger before the commit — `stored_previews` checks the statement's live batches), and once
    # the import is undone the ledger is back to exactly what it describes, so Review again and
    # Confirm are instant instead of a full recompute (owner, 2026-10-26).
    return outcome


def _decision_commit_locked(
    statement_id: str,
    account_id: str | None,
    decisions: list[dict] | None = None,
    default_action: str = "create",
    delete_ids: list[str] | None = None,
    *,
    reviewed: bool = False,
) -> dict:
    """Apply the review decisions in ONE batch: creates through the ledger choke, merges via
    ``apply_import_merge`` with pre-merge snapshots recorded on the batch (rollback un-merges),
    everything else skipped. Reads the CACHED parse — never re-parses, never re-bills.

    ``delete_ids`` are LEDGER entries the user marked for deletion in the "In your ledger, not
    on this statement" panel. They live HERE, rather than as a client-side loop of
    ``DELETE /api/transactions/<id>``, for one reason: the safe target set is the statement's
    own re-derived UNCOVERED set (``_manual_link_ids``), and only this function can re-derive
    it. Sent to the generic delete endpoint the ids carry no statement context and get no guard
    at all — which is exactly the unguarded shape this replaced. Same posture as a merge's
    ``existing_id``: the caller's list is never trusted raw (SECURITY FIX 2, review
    2026-07-16).

    They run LAST, after the row decisions have committed. If a delete fails, the import the
    user confirmed still stands and the failures are REPORTED (``delete_errors``) — the
    ``merge_errors`` contract. Running them first would risk destroying ledger rows for an
    import that then failed to apply."""
    if default_action not in ("create", "skip"):
        raise StatementParseError("default_action must be create or skip")
    store = _statement_store()
    record = store.find_by_id(statement_id)
    if record is None or not record.get("import_rows"):
        raise StatementParseError(
            "statement not found or its preview expired — run the preview again"
        )
    rows = [CanonicalRow(**r) for r in record["import_rows"]]
    fmt = record.get("format") or "csv"
    resolved_default = resolve_account(account_id, None)
    by_index = {int(d["row"]): d for d in (decisions or []) if "row" in d}
    for d in by_index.values():
        if d.get("action") not in _DECISION_ACTIONS:
            raise StatementParseError(f"unknown action {d.get('action')!r}")

    # Re-derive exact-dup + twin suffixes + routing + the engine's OWN verdict over the full row
    # set, from the same deterministic inputs as the preview (cached rows + the chosen account),
    # so defaults stay honest and a merge decision can be checked against what the engine
    # actually proposed instead of trusting a caller-supplied existing_id (SECURITY FIX 2,
    # review 2026-07-16 — an unrelated transaction on another account was silently rewritten).
    # A ledger that changed between preview and commit may now honestly reject a merge that is
    # no longer proposed; that's correct, and the caller re-previews.
    #
    # `rederive_match_pass` is that derivation, shared with the post-commit ledger-diff panel so
    # "what this statement accounts for" has ONE answer on both sides of the commit (RFC
    # 2026-07-29, Phase 4). It runs the matcher BEFORE re-applying the cached Lucius ops (ADR
    # 2026-07-28) — the preview's order, so a re-applied reject_match/force_match sees the SAME
    # verdict it reviewed, and `assigned_by_index` below reflects the FINAL, Lucius-adjusted one.
    meta = StatementMeta(**(record.get("import_meta") or {}))
    # The reviewed verdicts, when the stored review was built for this account: Confirm books
    # what the owner reviewed and decided — no second matching pass over the live ledger
    # (owner, 2026-10-26: "after i spent minutes reviewing … it should follow my decision").
    # A merge is still only accepted if THAT review proposed it (server-side, never the
    # client's say-so), which is the security role the re-derive used to play.
    stored = (
        stored_previews.commit_pass(statement_id, (account_id, resolved_default), record)
        if reviewed
        else None
    )
    if stored is not None:
        ctx = _routing_context(rows, account_id)
        results = [RowResult(**r) for r in stored]
        lucius_ops = lucius_pass.active_ops(record)
        rule_routing = import_routing()
    else:
        match_pass = rederive_match_pass(record, rows, account_id)
        ctx, results, lucius_ops = match_pass.ctx, match_pass.results, match_pass.lucius_ops
        rule_routing = match_pass.routing
    lucius_by_row = {op["row"]: op for op in lucius_ops}
    routed_account_ids = frozenset({a["id"] for a in ctx.route.values()} | ({ctx.resolved} - {""}))
    assigned_by_index: dict[int, str] = {}
    for r in results:
        for f in r.flags:
            if f.get("type") in _MATCH_FLAG_TYPES and f.get("existing_id"):
                assigned_by_index[r.line - 1] = f["existing_id"]
    # Routing Automations: a routed transfer draft's already-booked twin is RE-DERIVED here
    # (never trusted from the caller) — it joins the engine's proposals so an explicit merge
    # onto it passes the guard, and with no decision the row default-skips (pair precedent).
    rule_routed_booked = _routed_booked_transfers(results)
    for idx, booked in rule_routed_booked.items():
        assigned_by_index.setdefault(idx, booked["id"])
    # A pair books as ONE transfer only while the IN leg has no explicit decision of its own.
    # An ordinary expense/income near-duplicate on either leg is not a two-leg transfer; the
    # review sends decisions for both legs so neither one is silently absorbed.
    active_pairs = {
        idx: p["partner"]
        for idx, p in ctx.pairs.items()
        if p["leg"] == "out" and by_index.get(p["partner"], {}).get("action") is None
    }
    absorbed_in_legs = set(active_pairs.values())
    # A pair whose transfer ALREADY EXISTS in the ledger defaults to skip — re-booking it
    # would double the movement. An explicit create decision on the OUT row overrides.
    pair_booked = _pair_existing_transfers(rows, ctx)
    _attach_booked_echo_flags(results, pair_booked, rule_routed_booked)
    # The SAME final pairing the preview showed, in the same position — otherwise the review
    # screen offers a link this guard then refuses, and the owner is told to "re-run the
    # preview" for a link the preview itself proposed (reported case).
    _match_leftovers(results, ctx, meta)
    for r in results:
        for f in r.flags:
            if f.get("type") in _MATCH_FLAG_TYPES and f.get("existing_id"):
                assigned_by_index.setdefault(r.line - 1, f["existing_id"])
    # User-asserted manual-link targets (the uncovered "not on statement" set) — lazy; None =
    # not yet derived (owner 2026-07-18).
    manual_link_set: set[str] | None = None
    # Which OTHER row's engine assignment already owns each id (security review 2026-07-29 #2).
    # ``apply_import_merge(facts="statement")`` re-dates and re-amounts its target, so two rows
    # pointed at one id would rewrite the same real transaction twice — and corrupt rollback
    # with it, since the second pre-merge snapshot captures post-first-merge state and replays
    # as a "success".
    claimants_by_id: dict[str, set[int]] = {}
    for _idx, _eid in assigned_by_index.items():
        claimants_by_id.setdefault(_eid, set()).add(_idx)
    # An engine-matched entry whose row the owner booked NEW (or linked elsewhere) is released:
    # nothing on this statement accounts for it any more, so — exactly like the review shows
    # it — it may be deleted or linked by another row (owner, 2026-10-26).
    released_ids: set[str] = set()
    for _idx, _eid in assigned_by_index.items():
        _d = by_index.get(_idx)
        if _d is None:
            continue
        if _d.get("action") == "create" or (
            _d.get("action") == "merge" and str(_d.get("existing_id") or "") != _eid
        ):
            released_ids.add(_eid)
            claimants_by_id.get(_eid, set()).discard(_idx)
    merge_targets: set[str] = set()
    # Staged ledger deletions, VALIDATED HERE — before a single write — against the same
    # uncovered set a manual link is validated against, and executed at the very end. Checking
    # pre-commit is not incidental: once the creates land, this statement's own new rows are in
    # the ledger and the uncovered set is no longer the one the user reviewed. So the set the
    # deletes are judged against is exactly the panel the user was looking at when they marked
    # them. An id outside it RAISES — nothing is written, the caller re-previews.
    to_delete: list[str] = []
    already_gone: list[str] = []
    if delete_ids:
        withheld = _parse_withheld_reason(
            meta.to_dict(), record.get("reconcile"), record.get("tier")
        )
        if withheld:
            raise ValidationError(
                f"delete refused ({withheld}): this statement's ledger-diff is withheld — "
                "the parse never finished reading every page, or its own balances don't "
                "reconcile, so a system row it doesn't mention cannot be trusted as "
                "genuinely missing from it. Re-read the file, or delete these rows by hand."
            )
        seen_del: set[str] = set()
        for raw_id in delete_ids:
            did = str(raw_id)
            if did in seen_del:
                continue
            seen_del.add(did)
            to_delete.append(did)
        # An id whose row is ALREADY soft-deleted is not a guard breach — the user's intent is
        # simply already satisfied (it vanished between the preview and this commit). It is out
        # of the uncovered set for that reason alone, so testing it as a stray would fail the
        # whole confirmed import over a row that is already in the state they asked for. It is
        # dropped from the batch but still REPORTED below, so "delete 8" never quietly becomes
        # "deleted 7".
        already_gone = [
            d for d in to_delete if (ledger_service.transactions.find_by_id(d) or {}).get("deleted")
        ]
        to_delete = [d for d in to_delete if d not in set(already_gone)]
        if manual_link_set is None:
            manual_link_set = (
                _manual_link_ids(record, rows, results, routed_account_ids, meta) | released_ids
            )
        stray = [d for d in to_delete if d not in manual_link_set]
        if stray:
            raise ValidationError(
                "delete refused: "
                f"{', '.join(sorted(stray))} "
                "is not an uncovered entry of this statement — re-run the preview and re-submit"
            )
        # Deleting a row this same commit also merges INTO would destroy the merge's target.
        # The UI cannot produce it (a linked entry leaves the panel), so it is a caller bug —
        # refuse rather than pick an order. Read off the REQUESTED decisions, not the
        # `merge_targets` the loop below fills in: this check runs before that loop, by design.
        requested_merges = {
            str(d["existing_id"])
            for d in by_index.values()
            if d.get("action") == "merge" and d.get("existing_id")
        }
        both = [d for d in to_delete if d in requested_merges]
        if both:
            raise ValidationError(
                f"delete refused: {', '.join(sorted(both))} is also a merge target in this "
                "commit — an entry cannot be both linked and deleted"
            )
    category_rows = category_rows_for(rows)

    creates: list[dict] = []
    # Rows the owner EXPLICITLY chose to create, having already seen the preview's duplicate
    # flag for them (the review's Include toggle). That choice IS the confirmation the
    # pre-write duplicate guard exists to ask for, so these are booked with allow_duplicate=True —
    # otherwise the guard silently overrules a decision the user made with the same evidence
    # the guard would have shown them ("default-skip is a DEFAULT, not a refusal").
    creates_explicit: list[dict] = []
    merges: list[
        tuple[int, str, dict, bool]
    ] = []  # (row_index, existing_id, draft, is_transfer_pair)
    # Seeded from the offer's ROUTING (committing confirms it — even an all-skip "file this
    # statement" commit links every routed wallet and remembers the currency→account map);
    # booked rows overwrite with identical values.
    account_map_used: dict[str, str] = {}
    for row in rows:
        if row.skip:
            continue
        routed = ctx.target_for(row)
        if routed:
            account_map_used.setdefault(ctx.row_currency(row), routed["id"])
    booked_out: set[int] = set()
    fired_routing_automations: list[tuple[str, str, list[dict]]] = []
    skipped = 0
    duplicates = 0
    transfers = 0
    for r, row in zip(results, rows):
        idx = r.line - 1
        decision = by_index.get(idx, {})
        action = decision.get("action")
        if r.draft is None:
            skipped += 1
            continue
        if r.duplicate and action != "create":
            duplicates += 1
            continue
        if idx in absorbed_in_legs and action != "create":
            continue  # folded into its OUT partner's transfer (counted there)
        low_conf = row.confidence < MIN_CONFIDENCE or bool(row.notes)
        if action is None:
            # A Lucius skip op (SI-68) default-skips exactly like a needs-eyes row — an
            # explicit create decision (the review's Include toggle) overrides it.
            #
            # So does a POLICY-SUPPRESSED row (security review 2026-07-29 #10): the owner's
            # notes removed or redirected the match the engine would otherwise have proposed,
            # and `default_action` is "create" — so without this line a policy of "no drift in
            # date" would book a re-import of last month as N brand-new transactions behind a
            # preview that looked perfectly clean. Skip is the honest default when the reason a
            # row has no match is a policy rather than the ledger.
            action = (
                "skip"
                if (low_conf or r.draft.get("lucius_skip") or r.draft.get("policy_suppressed"))
                else default_action
            )
        if action == "skip":
            skipped += 1
            continue
        row_ccy = ctx.row_currency(row)
        if idx in active_pairs and idx in pair_booked and decision.get("action") is None:
            skipped += 1  # the transfer is already in the ledger — never silently re-book
            continue
        if idx in rule_routed_booked and decision.get("action") is None:
            skipped += 1  # the routed transfer is already booked — never silently re-book
            continue
        if idx in active_pairs and action == "create":
            if routed_transfer.transfer_target(decision.get("edits") or {}):
                raise StatementParseError(
                    f"row {idx} is already half of an FX transfer pair on this statement; "
                    "it can't also be routed to another account"
                )
            in_row = rows[active_pairs[idx]]
            draft = _transfer_draft(row, in_row, ctx, fmt)
            if (decision.get("edits") or {}).get("category"):
                draft["category"] = decision["edits"]["category"]
            creates.append(strip_display_only(draft))
            transfers += 1
            booked_out.add(idx)
            account_map_used[row_ccy] = draft["from_account_id"]
            account_map_used[ctx.row_currency(in_row)] = draft["to_account_id"]
            continue
        edited = _apply_edits(CanonicalRow(**row.to_dict()), decision.get("edits") or {})
        explicit_account = (decision.get("edits") or {}).get("account_id")
        target_account = explicit_account or (ctx.target_for(row) or {}).get("id")
        if not target_account:
            skipped += 1  # unroutable currency, no explicit pick — never book blind
            continue
        acct = ledger_service.accounts.find_by_id(target_account)
        if acct is None:
            raise StatementParseError(f"row {idx}: account {target_account!r} not found")
        currency = (acct.get("currency") or ledger_service.base_currency()).upper()
        if row_ccy and currency != row_ccy:
            raise StatementParseError(
                f"row {idx} is {row_ccy} but {acct.get('name') or target_account!r} holds "
                f"{currency} — booking across currencies would corrupt the balance"
            )
        # An explicit transfer routing replaces whatever a rule or Lucius op would have done.
        transfer_to = routed_transfer.transfer_target(decision.get("edits") or {})
        if transfer_to and action == "merge":
            raise StatementParseError(
                f"row {idx}: a merge links an existing entry — it can't also route the row "
                "as a transfer"
            )
        draft = _row_to_draft(
            edited,
            target_account,
            currency,
            fmt,
            category_rows=category_rows,
            routing=None if idx in ctx.pairs or transfer_to else rule_routing,
        )
        # Re-apply this row's Lucius op onto the rebuilt (possibly edited) draft — the
        # decision edits below still win over it.
        if idx not in ctx.pairs and not transfer_to:
            draft = lucius_pass.apply_op_to_commit_draft(
                draft, edited, lucius_by_row.get(idx), target_account, currency
            )
        # The OFFERED draft's external_refs is this row's dedup identity: the
        # ref carries the intra-file disambiguation suffix (`:1`, `:2`…) for content-identical
        # twins, and a re-import recomputes the same offer deterministically. Recomputing from
        # the (possibly edited) row here collapsed twins back onto ONE ref — booked fine once,
        # then re-created on every re-import (found by the unification e2e round-trip).
        if r.draft.get("external_refs"):
            draft["external_refs"] = [dict(e) for e in r.draft["external_refs"]]
        if (decision.get("edits") or {}).get("category"):
            draft["category"] = decision["edits"]["category"]
        if transfer_to:
            draft = routed_transfer.routed_transfer_draft(
                draft, edited, target_account, currency, transfer_to, idx
            )
            transfers += 1
        fired_routing_automation = draft.get("routing_automation_id")
        if action == "merge":
            existing_id = decision.get("existing_id")
            if not existing_id:
                raise StatementParseError(f"merge for row {idx} needs existing_id")
            # SECURITY (FIX 2 · security review 2026-07-29 #1+#2). The acceptance set is
            # EXACTLY TWO server-re-derived paths, and nothing else:
            #
            #   1. `assigned_by_index[idx]` — THIS row's own engine assignment (post-Lucius),
            #      which now covers the settled-drift weak band too: the tier lives inside the
            #      engine, so its verdicts arrive as ordinary flags with every invariant applied
            #      and 1:1 exclusivity already resolved.
            #   2. `_manual_link_ids(...)` — the freshly re-derived UNCOVERED set (owner
            #      2026-07-18): a user-asserted "these two are the same" onto an entry in this
            #      statement's own accounts and window that nothing else claims.
            #
            # The retired third path (the twin matcher's PRE-assignment eligible map) is gone
            # with the matcher. It lost the engine's invariants, the statement window and 1:1
            # exclusivity at once, and it bought nothing: it was a SUBSET of the uncovered set
            # by construction, since it only ever emitted ids `preview_ledger_check` had already
            # listed. Path 2 still accepts every merge it did.
            #
            # A row's engine `alternatives` are DISPLAY only — the picker's options are all
            # uncovered entries, so path 2 already accepts each one. Never widen this to the
            # engine's candidate pool: that pool is ±FETCH_WINDOW_DAYS across ALL accounts.
            proposed_id = assigned_by_index.get(idx)
            if existing_id != proposed_id:
                other_claimants = claimants_by_id.get(existing_id, set()) - {idx}
                if other_claimants:
                    raise ValidationError(
                        f"merge for row {idx}: existing_id {existing_id!r} is already matched to "
                        f"row {min(other_claimants)} — resolve that row's match first"
                    )
                if manual_link_set is None:
                    manual_link_set = (
                        _manual_link_ids(record, rows, results, routed_account_ids, meta)
                        | released_ids
                    )
                if existing_id not in manual_link_set:
                    raise ValidationError(
                        f"merge for row {idx}: existing_id {existing_id!r} was not proposed "
                        "by the matching engine for this row, and is not an uncovered entry "
                        "available for a manual link — re-run the preview and re-submit"
                    )
            # One target, one merge. `apply_import_merge(facts="statement")` re-dates and
            # re-amounts its target, so a second merge onto the same id would rewrite one real
            # transaction twice and leave rollback replaying a snapshot of the already-merged
            # state (security review 2026-07-29 #1).
            if existing_id in merge_targets:
                raise ValidationError(
                    f"merge for row {idx}: existing_id {existing_id!r} is already the target of "
                    "another merge in this commit — one ledger entry backs at most one row"
                )
            merge_targets.add(existing_id)
            # Belt-and-suspenders (the engine's own assignment already implies this): the
            # merge target must actually live on an account this statement routes to.
            target_txn = ledger_service.transactions.find_by_id(existing_id)
            touched = (
                {
                    a
                    for a in (target_txn.get("from_account_id"), target_txn.get("to_account_id"))
                    if a
                }
                if target_txn
                else set()
            )
            if not (touched & routed_account_ids):
                raise ValidationError(
                    f"merge for row {idx}: existing_id {existing_id!r} is not on an account "
                    "this statement routes to"
                )
            # A transfer_pair match has NEITHER side recorded as a transfer yet — Link must
            # actually BUILD one (apply_import_transfer_pair_merge), not do the same-kind-only
            # fact merge near_duplicate/transfer_leg_echo use (where the movement is already
            # fully booked and linking just marks the row covered).
            is_transfer_pair = any(
                f.get("type") == "transfer_pair" and f.get("existing_id") == existing_id
                for f in r.flags
            )
            merges.append((idx, existing_id, strip_display_only(draft), is_transfer_pair))
            if fired_routing_automation:
                fired_routing_automations.append((fired_routing_automation, existing_id, []))
        else:
            # Explicit confirmation comes from either principal: the owner ticking Include, or
            # Lucius issuing reject_match ("this is NOT the same event as that row"). Both are
            # decisions made WITH the proposed match in hand, so re-guarding them would refuse
            # on evidence already weighed.
            confirmed = decision.get("action") == "create" or draft.get("match_rejected")
            target = creates_explicit if confirmed else creates
            target.append(strip_display_only(draft))
            if fired_routing_automation:
                fired_routing_automations.append(
                    (
                        fired_routing_automation,
                        "",
                        [dict(ref) for ref in (draft.get("external_refs") or [])],
                    )
                )
                transfers += 1  # a routed create books a two-leg transfer
        account_map_used[row_ccy] = target_account

    # An absorbed IN leg whose OUT partner never booked its transfer went nowhere — honest count.
    skipped += sum(1 for out_i in active_pairs if out_i not in booked_out)

    from backend.core.audit_context import clear_audit_scope, set_audit_scope_if_absent

    # Provenance: a decision-commit is the USER applying an import (surface=import) — only
    # when no richer scope is already active (a chat-driven commit keeps its chat scope).
    # Wraps BOTH the creates and the merges so every audit event carries the same actor.
    _owns_scope = set_audit_scope_if_absent("user", "import")
    try:
        outcome = _apply(
            statement_id=statement_id,
            record=record,
            rows=rows,
            fmt=fmt,
            resolved_default=resolved_default,
            creates=creates,
            creates_explicit=creates_explicit,
            merges=merges,
            skipped=skipped,
            duplicates=duplicates,
            transfers=transfers,
            account_map=account_map_used,
            delete_ids=to_delete,
            reviewed=reviewed,
        )
    finally:
        if _owns_scope:
            clear_audit_scope()
    _record_routing_automation_matches(fired_routing_automations, batch_id=outcome["batch_id"])
    outcome["delete_errors"] = list(outcome.get("delete_errors") or []) + [
        {"id": gone, "error": "already deleted"} for gone in already_gone
    ]
    return outcome


def _record_routing_automation_matches(
    fired: list[tuple[str, str, list[dict]]], *, batch_id: str
) -> None:
    """Record routing usage only after the enclosing decision commit succeeds."""
    if not fired:
        return
    try:
        from backend.db.automation_store import AutomationStore
        from backend.services.automations.transaction_events import (
            AutomationMatch,
            record_committed_matches,
        )

        automations = {row["id"]: row for row in AutomationStore().all()}
        created_by_ref = {
            (ref.get("source"), ref.get("ref")): txn["id"]
            for txn in ledger_service.transactions.all()
            if txn.get("batch_id") == batch_id
            for ref in (txn.get("external_refs") or [])
            if ref.get("ref")
        }
        for automation_id, transaction_id, external_refs in fired:
            automation = automations.get(automation_id)
            if automation is None:
                continue
            resolved_transaction_id = transaction_id or next(
                (
                    created_by_ref.get((ref.get("source"), ref.get("ref")))
                    for ref in external_refs
                    if created_by_ref.get((ref.get("source"), ref.get("ref")))
                ),
                None,
            )
            match = AutomationMatch(
                automation_id=automation_id,
                automation_name=automation.get("name") or "",
                phase="statement_draft",
                category_from=None,
                category_to=None,
                category_applied=False,
            )
            record_committed_matches(
                [match],
                transaction_id=resolved_transaction_id,
                context="statement_draft",
            )
    except Exception:  # noqa: BLE001 — usage metadata never breaks a commit
        logger.exception("decision_commit: routing Automation usage update failed")


def _restore_deleted(ids: list[str]) -> None:
    """Undo the staged soft-deletes when the rest of a commit fails unexpectedly.

    The deletions run first now (see ``_apply``), so this is what keeps the guarantee their old
    last-place ordering bought: a confirmed import that blows up must never also cost the user
    rows it never got to justify removing. Best-effort and never raising — it runs on the way
    out of a failure, and a compensation that raises would replace the real error with its own.
    """
    for txn_id in ids:
        try:
            ledger_service.transactions.update_by_id(txn_id, {"deleted": False})
        except Exception:  # noqa: BLE001
            logger.exception("decision_commit: could not restore staged delete %s", txn_id)
    try:
        from backend.services import rejected_identities

        rejected_identities.forget_for_txns(ids)
    except Exception:  # noqa: BLE001
        logger.exception("decision_commit: could not forget rejections for %s", ids)


def _apply(
    *,
    statement_id: str,
    record: dict,
    rows: list,
    fmt: str,
    resolved_default: str,
    creates: list[dict],
    merges: list[tuple[int, str, dict, bool]],
    skipped: int,
    duplicates: int,
    transfers: int = 0,
    account_map: dict[str, str] | None = None,
    delete_ids: list[str] | None = None,
    reviewed: bool = False,
    # Defaulted, not required: `_apply` has direct callers (tests, and any future re-apply
    # path) for which "no explicitly-included rows" is the honest default. Making it required
    # broke them for no gain — an empty list means exactly what they meant.
    creates_explicit: list[dict] | None = None,
) -> dict:
    # A batch exists to roll a commit's WRITES back. Filing a statement that books, links and
    # deletes nothing writes nothing, so it mints none (owner, 2026-10-06: an empty batch per
    # "File statement — nothing new"); `_apply_rows` remembers the filing as `filed_at` instead.
    writes = bool(creates or creates_explicit or merges or delete_ids)
    batch = (
        ledger_service.import_batches.create_batch(
            description=f"Statement import: {record.get('filename') or fmt}",
            transaction_count=len(creates),
            source_account_id=resolved_default,
            statement_ref_id=statement_id,
        )
        if writes
        else None
    )
    # A statement row IS the bank's own record of a settled movement, so it books CLEARED
    # (owner, 2026-08-09: "they should mark as cleared by default"). `cleared` defaults to
    # False on the model — correct for a hand-typed transaction, which nothing has confirmed
    # yet — and the CREATE side never overrode it.
    #
    # The asymmetry is why this survived so long: the MERGE side has always cleared its row
    # (`apply_import_merge` patches cleared=True), and a real import links far more rows than
    # it books, so the ledger read as mostly-cleared (a reported book: ~97% cleared) while
    # every genuinely NEW imported row sat uncleared. Rows the source marks pending/failed
    # never reach here — the status-column guard (`profiles._status_skip_values`) drops them
    # at parse time.
    # `setdefault`, not assignment: a caller that deliberately sets cleared=False keeps it.
    for draft in [*creates, *(creates_explicit or [])]:
        draft.setdefault("cleared", True)

    # The staged ledger deletions run FIRST, ahead of every create and merge.
    #
    # They used to run last, so that a failure could never cost the user an import they had
    # confirmed. That ordering made one half of the user's decision invisible to the other
    # half: a merge asks `resolve_external_refs_patch` who owns each ref it is patching, and
    # a row this same commit is about to delete answers. Reported case, 2026-08-27 — the owner linked a
    # statement row to one of three Apple rows sharing `email:aaaa1111bbbb` and marked the
    # other two for deletion; the merge was refused against a world the decision had already
    # dismissed, counted as "1 left out", and the deletes freed the ref seconds later. The
    # statement row stayed unlinked and came back offering to link on every re-import.
    #
    # The original guarantee is kept by COMPENSATION rather than by ordering: an unexpected
    # failure in the rest of the commit restores the soft-deleted rows (`_restore_deleted`).
    # That is strictly stronger — deletes-last only ever protected the deletions, while the
    # creates it ran after were already written and never rolled back.
    deleted_ids: list[str] = []
    delete_errors: list[dict] = []
    if delete_ids:
        try:
            outcome = ledger_service.bulk_delete_transactions(delete_ids)
            deleted_ids = list(outcome.get("deleted") or [])
            # A `not_found` id is a real miss, not a silent success: the row was deleted or
            # merged away between the preview and this commit.
            for missing in outcome.get("not_found") or []:
                delete_errors.append({"id": missing, "error": "transaction not found"})
        except Exception as e:  # noqa: BLE001 — mirrors the merge_errors contract above
            logger.exception("decision_commit: staged delete failed for %s", delete_ids)
            delete_errors.append({"id": ", ".join(delete_ids), "error": str(e)})

    try:
        return _apply_rows(
            statement_id,
            record,
            rows,
            fmt,
            resolved_default,
            creates,
            merges,
            skipped,
            duplicates,
            transfers,
            account_map,
            creates_explicit,
            batch,
            deleted_ids,
            delete_errors,
            reviewed=reviewed,
        )
    except Exception:
        # The import failed after the rows were already removed. Put them back — a confirmed
        # import that blew up must not also silently cost the user the deletions it never got
        # to justify. Re-raised: the caller still sees the real failure.
        _restore_deleted(deleted_ids)
        raise


def _apply_rows(
    statement_id: str,
    record: dict,
    rows: list,
    fmt: str,
    resolved_default: str,
    creates: list[dict],
    merges: list[tuple[int, str, dict, bool]],
    skipped: int,
    duplicates: int,
    transfers: int,
    account_map: dict[str, str] | None,
    creates_explicit: list[dict] | None,
    batch: dict | None,
    deleted_ids: list[str],
    delete_errors: list[dict],
    *,
    reviewed: bool = False,
) -> dict:
    """The row half of a statement commit — everything after the staged deletions have landed.

    Split out of ``_apply`` so the deletions have exactly one compensation boundary: anything
    that escapes this function restores them (see ``_restore_deleted``)."""
    created_rows: list[dict] = []
    refused_rows: list[dict] = []
    if creates:
        # A REVIEWED commit books the owner's decisions as decided: no fuzzy duplicate guard (it
        # once refused three real MoMo top-ups as "already in your ledger" against a transfer
        # leg it built itself — owner, 2026-10-26). Two floors remain, both identity-exact:
        # a row whose own statement ref is already booked (a double Confirm, a re-commit), and
        # a row the owner deleted before (`rejected_identities`, enforced on every path).
        created_rows, refused_rows = ledger_service.bulk_create_transactions(
            creates,
            batch_id=batch["id"] if batch else None,
            allow_duplicate=reviewed,
            dedup_by_external_ref=reviewed,
        )
    if creates_explicit:
        # allow_duplicate=True: the owner ticked Include on these rows with the duplicate flag already in
        # front of them. Guarding them again would refuse a decision made on the same evidence.
        forced_rows, forced_refused = ledger_service.bulk_create_transactions(
            creates_explicit, batch_id=batch["id"] if batch else None, allow_duplicate=True
        )
        created_rows = created_rows + forced_rows
        refused_rows = refused_rows + forced_refused
    if refused_rows:
        # NEVER discard this list. A refused row is one the user ASKED to import and did not
        # get; dropping it silently turns "import 4" into "3 imported" with no explanation —
        # the same shape of silent failure that let a swallowed metadata error cache as fact.
        # Surfaced on the outcome so the Done screen can name each one and its existing match.
        for r in refused_rows:
            logger.warning(
                "decision_commit: row refused as duplicate of %s — %s",
                r.get("_duplicate_of"),
                r.get("_duplicate_reason"),
            )

    merge_snapshots: list[dict] = []
    merge_errors: list[dict] = []
    merged = 0
    for idx, existing_id, draft, is_transfer_pair in merges:
        before = ledger_service.transactions.find_by_id(existing_id)
        if before is None:
            # Counted like a failed merge and LOGGED like one. Silently folding it into
            # `skipped` made a vanished merge target indistinguishable from a row the user
            # chose to leave out — the same unexplained "N left out" a swallowed merge error
            # produced.
            logger.warning(
                "decision_commit: merge target gone for row=%d existing_id=%s", idx, existing_id
            )
            merge_errors.append(
                {"row": idx, "existing_id": existing_id, "error": "transaction not found"}
            )
            skipped += 1
            continue
        try:
            if is_transfer_pair:
                # Neither side is a real transfer yet — build one (right direction, right
                # amount, right from/to account), not a same-kind fact merge.
                ledger_service.apply_import_transfer_pair_merge(existing_id, draft)
            else:
                # Statement-flow merges (all bands, RFC batch-aware-dedup §Merge semantics):
                # the statement row's date + settled amount + reference win; category/notes/
                # payee/tags/splits are the canonical's own and stay untouched (facts=
                # "statement" never sets them).
                ledger_service.apply_import_merge(existing_id, draft, facts="statement")
        except Exception as e:  # noqa: BLE001 — one bad merge (e.g. a protected opening txn)
            # must never sink the rest of an otherwise-good commit — reported, not raised.
            logger.exception(
                "decision_commit: merge failed for row=%d existing_id=%s", idx, existing_id
            )
            merge_errors.append({"row": idx, "existing_id": existing_id, "error": str(e)})
            skipped += 1
            continue
        # (The MATCHED side needs nothing here — `apply_import_merge` already patches
        # cleared=True, and `apply_import_transfer_pair_merge` builds the pair cleared.)
        merge_snapshots.append({"txn_id": existing_id, "before": dict(before)})
        merged += 1
    if merge_snapshots:
        if batch:
            ledger_service.import_batches.update_by_id(batch["id"], {"merges": merge_snapshots})

    store = _statement_store()
    if batch:
        store.add_batch_id(statement_id, batch["id"])
    else:
        # Filed with nothing to write: the record's own "this was filed" mark, read wherever a
        # batch id used to be the only signal (re-upload detection, the Statements sheet).
        store.update_by_id(
            statement_id,
            {"filed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        )
    if resolved_default:
        updates: dict = {"account_id": resolved_default}
        # Multi-account statements: the record files under EVERY routed wallet, and remembers
        # the currency→account routing for the next import of this source. Stamped on ANY
        # commit — an all-skip "file this statement" confirms the routing just as a booking
        # does (a fully-deduped statement still belongs in each wallet's history).
        booked_ids = sorted({v for v in (account_map or {}).values() if v} | {resolved_default})
        if len(booked_ids) > 1:
            updates["account_ids"] = booked_ids
        if account_map:
            updates["account_map"] = dict(account_map)
        store.update_by_id(statement_id, updates)
    _stamp_profile_account(record, resolved_default, bool(created_rows or merged))

    # Post-commit safety net (D6 checkpoint 3): fuzzy-flag anything that slipped past preview.
    if created_rows:
        try:
            from backend.services import match_review

            match_review.review_created_rows(created_rows, batch_id=batch["id"] if batch else None)
        except Exception:  # noqa: BLE001 — surfacing must never break a completed import
            logger.exception("decision_commit: post-commit match pass failed")

    return {
        "created": len(created_rows),
        "merged": merged,
        "duplicates": duplicates,
        "skipped": skipped,
        "transfers": transfers,
        "total_rows": len(rows),
        "batch_id": batch["id"] if batch else None,
        "statement_record_id": statement_id,
        "merge_errors": merge_errors,
        "deleted": len(deleted_ids),
        "delete_errors": delete_errors,
        # Rows the ledger REFUSED as duplicates of something already booked. Same contract as
        # merge_errors/delete_errors: reported, never raised — one refusal must not cost the
        # user the rest of the import — and never dropped.
        "refused_duplicates": [
            {
                "description": r.get("description"),
                "date": r.get("date"),
                "duplicate_of": r.get("_duplicate_of"),
                "reason": r.get("_duplicate_reason"),
                "previously_deleted": bool(r.get("_previously_deleted")),
            }
            for r in refused_rows
        ],
    }


def _stamp_profile_account(record: dict, account_id: str | None, committed: bool) -> None:
    prof = ((record.get("import_meta") or {}).get("extra") or {}).get("format_profile")
    if not prof or not account_id or not committed:
        return
    try:
        from backend.db.statement_format_store import StatementFormatStore

        StatementFormatStore().record_use(prof["id"], account_ids=[account_id])
    except Exception:  # noqa: BLE001
        pass
