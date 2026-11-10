"""Advisory import anomaly / near-duplicate detection (L2 — compute-only).

A SOFT review layer on top of the import preview. The HARD guarantee — content-addressed
``(source, ref)``-pair dedup in ``import_common.flag_duplicates`` — is untouched:
an exact re-import still drops to zero. This module catches what that *cannot* catch:

  * **near_duplicate**  — a draft that matches an EXISTING ledger txn with a DIFFERENT
    ``(source, ref)`` provenance pair (a different source, or a row the user hand-entered
    earlier). Detection
    delegates to the Match & Dedup Engine (``matching.check``, SCAN profile) so the preview
    and the post-commit review queue use IDENTICAL classification rules — including the 5%
    FX-drift band for foreign-currency charges.
  * **transfer_pair** — a draft that forms the opposite-direction leg of a movement already
    in the ledger (debit + credit of a single Meridian Bank→QuickPay top-up, etc.). Also
    delegated to the engine.
  * **amount_anomaly** — an amount far outside a payee's own historical range (a recurring
    $12 subscription suddenly $1,200), inferred deterministically from the user's history.

All money is compared in the CURRENT base currency via ``ledger.txn_base_amount`` (so it is
base-change-safe — never the stored ``base_amount``). Deterministic: NO LLM, NO network, NO
money mutation. An empty / young book yields no flags — nothing is ever fabricated.

Layer: services → db → core (+ sibling services ``ledger``). No FastAPI/HTTP/AI types here.
The detector is ADVISORY ONLY — it never drops a row or changes the commit path; the user
decides. Wired into ``import_common.build_preview`` so every format (CSV/OFX/QIF/MT940/CAMT/
XLSX/PDF) gets it through the one shared preview choke point.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from backend.core.txn_sources import display_source, ext_ref_pairs, primary_pair
from backend.db.transaction_store import txn_text
from backend.services import ledger as ledger_service

# Tuning knobs — amount_anomaly (the only bespoke non-duplicate detection remaining).
_NEAR_DUP_DATE_WINDOW_DAYS = (
    4  # kept for the ledger window hi-bound (must match matching.WINDOW_DAYS)
)
_LOOKBACK_DAYS = 120  # how far back the ledger window looks (must match matching.LOOKBACK_DAYS)

# amount_anomaly: only fire on a payee with enough history and a clearly outlier amount.
_ANOMALY_MIN_HISTORY = 4  # need ≥ this many prior same-payee txns to judge "normal"
_ANOMALY_FACTOR = 5.0  # draft ≥ this × the payee's median historical base amount
_ANOMALY_MIN_BASE = 50.0  # ...and ≥ this absolute base amount (ignore tiny absolutes)

_DIGITS_PUNCT = re.compile(r"[\d\W_]+", re.UNICODE)
_WS = re.compile(r"\s+")


def _payee_key(description: str | None) -> str:
    """Simple payee normalizer (lowercase, drop digits/punctuation, collapse whitespace),
    matching ``corrections._payee_key`` so 'STARBUCKS #123' and 'STARBUCKS @ ION' both →
    'starbucks'. Empty/blank → ''."""
    if not description:
        return ""
    s = _DIGITS_PUNCT.sub(" ", description.lower())
    return _WS.sub(" ", s).strip()


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _draft_base_amount(draft: dict, rates: dict | None, base: str | None = None) -> float:
    """The draft's reporting magnitude in the CURRENT base (the draft is already a two-leg
    txn shape, so ``txn_base_amount`` consumes it directly — base-change-safe)."""
    return ledger_service.txn_base_amount(draft, base, rates=rates)


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if not n:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def detect_anomalies(results, *, lookback_days: int = _LOOKBACK_DAYS) -> dict:
    """Annotate each draft ``RowResult`` with a ``flags`` list and return a summary.

    ``results`` is the list of ``import_common.RowResult`` (mutated in place — each draft
    result gains a ``flags: list[dict]`` of ``{type, reason, existing_id?, line?, score?,
    klass?}``). Only IMPORTABLE drafts (a draft that is not already an exact hard-dedup
    duplicate) are judged — an exact re-import is already handled and would just double-flag.

    Returns ``{near_duplicate, intra_batch_duplicate, transfer_pair, amount_anomaly,
    flagged_rows}`` counts. Compute-only, never raises into the import path: any failure
    degrades to zero flags.
    """
    summary = {
        "near_duplicate": 0,
        "intra_batch_duplicate": 0,
        "transfer_pair": 0,
        "transfer_leg_echo": 0,
        "amount_anomaly": 0,
        "flagged_rows": 0,
    }
    try:
        return _detect(results, lookback_days, summary)
    except Exception:  # advisory layer — never break a preview
        for r in results:
            if getattr(r, "draft", None) is not None and not getattr(r, "flags", None):
                r.flags = []
        return summary


def _existing_summary(txn: dict | None, opening_ids: frozenset[str] | None = None) -> dict | None:
    """A compact projection of the EXISTING ledger txn a draft matched — enough for the import
    preview to show the user what they already have (and to drive a skip/merge decision). The
    signed display amount mirrors ``import_common.display_draft`` (income = to-leg positive,
    else from-leg negative). ``account_id`` is picked by the SAME kind branch as amount/currency.
    ``source`` is the DISPLAY-projected vocabulary (``core.txn_sources.display_source`` —
    manual/bank/import/ai/agent/automation/reconcile/opening), not the raw stored string, so
    every raw feed variant (csv_import/qif_import/api/...) collapses onto the same handful of
    values a caller's phrase map can switch on. ``original_amount``/``original_currency`` are the
    pre-conversion foreign charge (display/audit only) — computed once here so callers (the
    match-line sub-line, the ledger-diff panel) never re-derive or disagree on it.
    ``opening_ids`` is ``ledger.opening_txn_ids()`` read once by a caller summarizing many rows
    (it is an accounts read)."""
    from backend.services.ledger import opening_txn_ids

    if not txn:
        return None
    if opening_ids is None:
        opening_ids = opening_txn_ids()
    if txn.get("kind") == "income":
        amount, currency, account_id = (
            txn.get("to_amount"),
            txn.get("to_currency"),
            txn.get("to_account_id"),
        )
    elif txn.get("kind") == "transfer":
        amount, currency, account_id = (
            txn.get("from_amount"),
            txn.get("from_currency"),
            txn.get("from_account_id"),
        )
    else:
        amount = -txn["from_amount"] if txn.get("from_amount") is not None else None
        currency = txn.get("from_currency")
        account_id = txn.get("from_account_id")
    out_extra = {}
    if txn.get("kind") == "transfer":
        # Both legs ride the wire for transfers (additive, 2026-07-18): a single-leg
        # statement row linked to a transfer must be compared/displayed against the leg on
        # ITS OWN account — a reported bug: a +1,250,000 VND credit linked to a Wise
        # SGD→Mekong Bank transfer showed "S$78.20" and a false "Off by ₫1,249,900" because only
        # the from-leg (SGD) shipped. The FE resolves the anchor-side leg from these.
        out_extra = {
            "to_account_id": txn.get("to_account_id"),
            "from_amount": txn.get("from_amount"),
            "from_currency": txn.get("from_currency"),
            "to_amount": txn.get("to_amount"),
            "to_currency": txn.get("to_currency"),
        }
    return {
        **out_extra,
        "id": txn.get("id"),
        "date": txn.get("date"),
        "note": txn.get("description"),
        "amount": amount,
        "currency": currency,
        "kind": txn.get("kind"),
        "category": txn.get("category"),
        "has_category": bool(txn.get("category")),
        "cleared": bool(txn.get("cleared")),
        "source": display_source(
            txn.get("source"), kind=txn.get("kind"), is_opening=txn.get("id") in opening_ids
        ),
        "account_id": account_id,
        "original_amount": txn.get("original_amount"),
        "original_currency": txn.get("original_currency"),
    }


def _detect(results, lookback_days: int, summary: dict) -> dict:
    from backend.services import matching
    from backend.services.fx_cache import load_fx_cache

    rates = load_fx_cache()
    known_routes = matching.known_transfer_routes()
    base = ledger_service.base_currency()

    # The importable drafts we will judge (exact hard-dedup dups are left alone).
    candidates = [r for r in results if r.draft is not None and not r.duplicate]
    for r in results:
        if r.draft is not None:
            r.flags = []
    if not candidates:
        return summary

    # Bound the ledger read to the union date window the drafts span (± the lookback), so a
    # young book / a single-row import stays cheap. Empty book → no flags.
    draft_dates = [d for d in (_parse_date(r.draft.get("date")) for r in candidates) if d]
    if draft_dates:
        lo = (min(draft_dates) - timedelta(days=lookback_days)).isoformat()
        hi = (max(draft_dates) + timedelta(days=_NEAR_DUP_DATE_WINDOW_DAYS)).isoformat()
        ledger_pool = [
            t
            for t in ledger_service.transactions.query_txns(date_from=lo, date_to=hi)
            if not t.get("deleted") and not t.get("merged_into")
        ]
    else:
        ledger_pool = []

    # Stamp all drafts with a shared preview batch_id so the engine's invariant 3 (same-feed
    # rows are distinct real charges) applies exactly as it will post-commit.  The sentinel is
    # local to this call and is cleaned up in the finally block.
    preview_batch_id = f"_preview_{id(candidates)}"
    for r in candidates:
        r.draft["batch_id"] = preview_batch_id

    # Per-payee history for the amount_anomaly check (unchanged non-duplicate anomaly type).
    history: dict[str, list[float]] = {}
    for t in ledger_pool:
        if t.get("kind") not in ("expense", "income"):
            continue
        if _parse_date(t.get("date")) is None:
            continue
        amt_base = _draft_base_amount(t, rates, base)
        key = _payee_key(txn_text(t))
        if key:
            history.setdefault(key, []).append(amt_base)

    # Ledger rows + PRIOR in-file drafts (accumulated as we go), indexed by date: the engine
    # only ever scores a candidate within FETCH_WINDOW_DAYS of the draft (the rest of the
    # 120-day lookback exists for the payee history above), so each draft is handed just those.
    scope = _DraftScope(ledger_pool)
    opening_ids = ledger_service.opening_txn_ids()

    try:
        for r in candidates:
            draft = r.draft
            d_key = primary_pair(draft)

            # Engine call: SCAN profile (no L0 — exact-id dups already removed by prefilter).
            # Candidate pool = ledger rows + PRIOR in-file drafts so the engine's invariants
            # (including same-feed veto via batch_id) decide exactly as post-commit.
            # Exclude any candidate carrying the same (external_source, external_ref) pair as the
            # draft — that is the hard-dedup's domain (exact re-import) and the advisory layer
            # must not double-flag it.
            pool, ref_freq = scope.candidates_for(draft, d_key)
            result = matching.check(
                draft,
                profile=matching.SCAN,
                candidates=pool,
                rates=rates,
                known_routes=known_routes,
                ref_freq=ref_freq,
                base=base,
            )

            if result.action == "review" and result.best is not None:
                klass = result.best.klass
                if klass == matching.MatchClass.NEAR_DUPLICATE:
                    r.flags.append(
                        {
                            "type": "near_duplicate",
                            "reason": "; ".join(result.reasons)
                            or "Matches an existing transaction (same amount, similar date and description).",
                            "existing_id": result.canonical_id,
                            "existing": _existing_summary(result.best.txn, opening_ids),
                            "score": result.best.score,
                            "klass": klass.value,
                            "_alts": _ranked_alts(result, klass),
                        }
                    )
                    summary["near_duplicate"] += 1
                elif klass == matching.MatchClass.TRANSFER_PAIR:
                    r.flags.append(
                        {
                            "type": "transfer_pair",
                            "reason": "; ".join(result.reasons)
                            or "Possible transfer pair with an existing transaction.",
                            "existing_id": result.canonical_id,
                            "existing": _existing_summary(result.best.txn, opening_ids),
                            "score": result.best.score,
                            "klass": klass.value,
                            "_alts": _ranked_alts(result, klass),
                        }
                    )
                    summary["transfer_pair"] += 1
                elif klass == matching.MatchClass.TRANSFER_LEG_ECHO:
                    # A single-leg draft that duplicates one leg of an EXISTING two-leg
                    # transfer (the card-payment credit on a card statement whose payment is
                    # already booked as bank→card). Importing it would double the leg — it was
                    # classified by the engine but previously dropped here (a reported bug: the
                    # S$620.00 "Meridian Bank Visa Direct" credit sailed through unflagged).
                    r.flags.append(
                        {
                            "type": "transfer_leg_echo",
                            "reason": "; ".join(result.reasons)
                            or "Duplicates one leg of an existing transfer.",
                            "existing_id": result.canonical_id,
                            "existing": _existing_summary(result.best.txn, opening_ids),
                            "score": result.best.score,
                            "klass": klass.value,
                        }
                    )
                    summary["transfer_leg_echo"] += 1

            # amount_anomaly vs the payee's own history (enough observations + clear outlier).
            d_base = _draft_base_amount(draft, rates, base)
            key = _payee_key(txn_text(draft))
            hist = history.get(key) or []
            if len(hist) >= _ANOMALY_MIN_HISTORY and d_base >= _ANOMALY_MIN_BASE:
                med = _median(hist)
                if med > 0 and d_base >= _ANOMALY_FACTOR * med:
                    r.flags.append(
                        {
                            "type": "amount_anomaly",
                            "reason": (
                                f"Unusually large for this payee "
                                f"(~{d_base:,.0f} {base} vs a typical ~{med:,.0f} {base})."
                            ),
                        }
                    )
                    summary["amount_anomaly"] += 1

            scope.append(draft)

    finally:
        # Remove the temporary batch_id so drafts are not mutated past the anomaly pass.
        for r in candidates:
            r.draft.pop("batch_id", None)

    _enforce_exclusive_matches(candidates, summary)
    summary["flagged_rows"] = sum(1 for r in candidates if r.flags)
    return summary


class _DraftScope:
    """The engine's comparison scope for one draft — ledger pool + prior drafts, minus rows
    sharing the draft's primary ``(source, ref)`` — served NARROWED (only rows near the draft's
    date, in scope order) together with that WHOLE scope's reference-token counts, which is
    what ``matching.check`` counted itself when it was handed the whole scope. Token sets are
    tokenized once per row and counted incrementally, never re-counted per draft."""

    def __init__(self, ledger_pool: list[dict]):
        from backend.services import matching

        self._matching = matching
        self._dated = matching._DatedPool()
        self._tokens: list[frozenset[str]] = []
        self._freq: dict[str, int] = {}
        self._by_ref: dict[tuple, list[int]] = {}
        for t in ledger_pool:
            self.append(t)

    def append(self, row: dict) -> None:
        pos = len(self._dated.pool)
        self._dated.append(row)
        tokens = frozenset(self._matching._reference_token_frequencies([row]))
        self._tokens.append(tokens)
        for tok in tokens:
            self._freq[tok] = self._freq.get(tok, 0) + 1
        for pair in ext_ref_pairs(row):
            self._by_ref.setdefault(pair, []).append(pos)

    def candidates_for(self, draft: dict, d_key: tuple) -> tuple[list[dict], dict[str, int]]:
        m = self._matching
        excluded = set(self._by_ref.get(d_key, ())) if d_key[1] else set()
        positions = self._dated.positions(m.parse_date(draft.get("date")), m.FETCH_WINDOW_DAYS)
        pool = [self._dated.pool[i] for i in positions if i not in excluded]
        # Only the draft's OWN tokens are ever looked up: a shared reference token is by
        # definition one the draft prints, so the rest of the scope's counts are never read.
        freq = m._reference_token_frequencies([draft])
        for tok in freq:
            freq[tok] += self._freq.get(tok, 0) - sum(tok in self._tokens[i] for i in excluded)
        return pool, freq


def _ranked_alts(result, klass) -> list:
    """The row's OTHER review-band engine candidates of the same class (real ledger txns
    only, best-first) — carried on the flag as ``_alts`` so ``_enforce_exclusive_matches``
    can reassign a losing claimant to its next FREE twin, then popped before the payload."""
    from backend.services import matching

    return [
        c
        for c in result.candidates[1:]
        if c.klass is klass and c.score >= matching.SCORE_FENCE and c.id
    ]


def _flag_from_alt(alt) -> dict:
    """Rebuild a review flag from a ranked engine Candidate (the reassignment path)."""
    return {
        "type": alt.klass.value,
        # klass/score already ride their own structured fields below — the reason text is
        # user-facing only, no debug annotation prepended (owner, 2026-07-27).
        "reason": "; ".join(alt.reasons) or "Possible match with an existing transaction.",
        "existing_id": alt.id,
        "existing": _existing_summary(alt.txn),
        "score": alt.score,
        "klass": alt.klass.value,
    }


def _enforce_exclusive_matches(candidates, summary: dict) -> None:
    """One existing transaction can be the duplicate of ONE statement row — never two.

    A reported bug: two 7-Eleven rows (Mar 4 + Mar 11, both S$4.20) matched the SAME booked
    Mar-11 transaction (scores 0.75 and 1.0); both defaulted to Skip, so the Mar-4 purchase was
    silently never imported — real data loss. For near_duplicate/transfer_pair claims on one
    existing id, keep only the best (score, then closest date, then file order).

    REASSIGNMENT (a reported bug): three same-fare BUS/MRT rides in one statement all
    ranked the SAME booked ride alert as their best match (identical amount + wording, and
    nearest-date ties broke toward one entry) — the losers were stripped to plain creates
    while their true twins sat unclaimed, so the review showed unflagged table rows AND
    "in your ledger but not on this statement" for the same purchases. Claims are now
    assigned GLOBALLY: every row's ranked same-class candidates compete best-first, each
    row and each existing txn is assigned at most once, and a losing row falls back to its
    next free candidate — a plain create only when every alternative is taken.

    transfer_leg_echo is EXEMPT: a statement legitimately lists BOTH legs of one internal
    move, and both echo the same existing two-leg transfer.

    THIN ADAPTER (RFC batch-aware-dedup): the assignment algorithm itself now lives in
    ``matching._global_claim_assignment`` — the ONE home for the ordering/greedy-assign
    shape, shared with ``matching.match_batch``'s own global assignment step. This function
    only adapts the RowResult/flags representation into claims and the winners back onto
    ``r.flags``/``summary`` — ported verbatim, byte-compatible with every caller."""
    from backend.services import matching

    exclusive = ("near_duplicate", "transfer_pair")
    entries: list[tuple] = []  # one (row, primary flag) per row — _detect appends at most one
    alts_by_row: dict[int, list] = {}
    for r in candidates:
        for f in r.flags:
            # Pop the engine-internal alternatives from EVERY flag — Candidate objects must
            # never reach the preview payload (also covers an id-less prior-draft match).
            alts = f.pop("_alts", None)
            if f.get("type") in exclusive and f.get("existing_id"):
                entries.append((r, f))
                alts_by_row[id(r)] = alts or []
    if len({f["existing_id"] for _, f in entries}) == len(entries):
        return  # every claim distinct — nothing contested

    def _gap(r, date_str) -> int:
        draft_date = _parse_date(r.draft.get("date"))
        existing_date = _parse_date(date_str)
        return abs((draft_date - existing_date).days) if draft_date and existing_date else 999

    # Every claim each row could make: its primary flag, then its ranked alternatives.
    # Sort key (−score, date gap, file order, alt rank) is total and deterministic.
    claims: list[tuple] = []
    for order, (r, f) in enumerate(entries):
        gap = _gap(r, (f.get("existing") or {}).get("date"))
        claims.append(((-(f.get("score") or 0.0), gap, order, 0), id(r), f["existing_id"], f))
        for rank, alt in enumerate(alts_by_row[id(r)], start=1):
            alt_flag = _flag_from_alt(alt)
            key = (-(alt_flag["score"] or 0.0), _gap(r, alt.txn.get("date")), order, rank)
            claims.append((key, id(r), alt_flag["existing_id"], alt_flag))

    won = matching._global_claim_assignment(claims)
    for r, f in entries:
        winner = won.get(id(r))
        if winner is f:
            continue
        r.flags.remove(f)
        if f["type"] in summary:
            summary[f["type"]] = max(0, summary[f["type"]] - 1)
        if winner is not None:
            r.flags.append(winner)
            if winner["type"] in summary:
                summary[winner["type"]] += 1
