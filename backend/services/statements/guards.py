"""Deterministic correctness guards (RFC statement-import-unified D4). No LLM, no writes.

Run between parse and preview: re-derive direction/amount from a running-balance column,
reconcile against the statement's own opening/closing balances per currency group, and verify
extracted values exist in the source text (the "highlighter, not pen" constraint). The
extraction can be "usually right"; these make it "right, or it tells you"."""

from __future__ import annotations

import re

from backend.services.statements import parse_progress
from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta

_TOL = 0.02  # money comparison slack (rounding across a whole statement)
_CHAIN_TOL = 0.01  # per-link slack — one row's own arithmetic, tighter than the whole-file _TOL
_LOW_CONFIDENCE = 0.4


def _signed(row: CanonicalRow, out_positive: bool) -> float:
    sign_out = 1.0 if out_positive else -1.0
    return row.amount * (sign_out if row.direction == "OUT" else -sign_out)


def rederive_from_balance(rows: list[CanonicalRow], meta: StatementMeta) -> int:
    """Correct each row's direction from the running-balance delta when the source carries a
    balance column — the balance arithmetic is proof, so the fix is applied (not just flagged).
    The model/schema supplies only the magnitude; a delta that disagrees with the magnitude
    beyond tolerance is annotated instead (never silently rewritten). Returns corrections made.

    The delta only means anything walked in CHRONOLOGICAL order (each row's own balance is
    "after this transaction", so ``row.balance - prev`` is only that row's own delta when
    ``prev`` is the immediately-EARLIER transaction). Many exports list newest-first (e.g. a
    Nimbus Wallet statement) — walking those top-to-bottom pairs every row with the WRONG
    neighbor's balance (off by one), so the delta actually equals the PRECEDING row's amount,
    not this row's — a reported case where 101 of 104 rows falsely got flagged "disagrees with
    amount" and defaulted to Skip. Direction is detected from the overall date span (first vs
    last dated row) — ``rows`` itself is never reordered, only the walk order for this
    calculation, so every other consumer still sees the source's original row order.

    A ZERO delta (the balance is unchanged from the previous kept row) is never treated as a
    disagreement, however large the row's own amount is — some sources log a transaction that
    genuinely doesn't move the running balance (reported case: a Nimbus Wallet
    row can be a pass-through the wallet routes directly bank→destination without the float
    ever changing), and "the balance didn't move" is itself an unambiguous, source-agnostic
    signal that this row simply isn't verifiable against this column — never a math error. This
    is NOT a Nimbus-Wallet-specific rule (no vendor/language check): it fires for ANY source whose
    balance column can carry a pass-through row, cutting false-flags on the affected
    statement from 78 to 11 kept rows."""
    out_positive = meta.sign_convention == "card"  # card balance grows with charges
    corrections = 0
    dated = [r.date for r in rows if r.date]
    descending = bool(dated) and dated[0] > dated[-1]
    walk = list(reversed(rows)) if descending else rows
    kept = [i for i, r in enumerate(walk) if not r.skip and r.balance is not None]
    prev = meta.opening_balance
    p = 0
    while p < len(kept):
        row = walk[kept[p]]
        if prev is None:
            prev = row.balance
            p += 1
            continue
        delta = row.balance - prev
        if abs(delta) <= _TOL:
            prev = row.balance  # pass-through: balance genuinely unaffected, not a disagreement
            p += 1
            continue
        if abs(abs(delta) - row.amount) <= max(_TOL, row.amount * 0.001):
            expected_dir = (
                ("OUT" if delta > 0 else "IN") if out_positive else ("IN" if delta > 0 else "OUT")
            )
            if row.direction != expected_dir:
                row.direction = expected_dir
                row.notes.append("direction corrected from running balance")
                corrections += 1
            prev = row.balance
            p += 1
            continue
        # Two shapes where a source's balance column misleads one row at a time while the
        # amounts are right (owner, 2026-10-26, a MoMo export — every flag was a false alarm):
        # 1. Transactions settled together print ONE balance on each of them, so only the
        #    group as a whole moves the balance from ``prev`` to it.
        group_end = p + 1
        while group_end < len(kept) and abs(walk[kept[group_end]].balance - row.balance) <= _TOL:
            group_end += 1
        # The shortest run that adds up: a pass-through row printing the same balance right
        # after the group must not be pulled into it.
        settled = None
        for end in range(p + 2, group_end + 1):
            group = [walk[kept[k]] for k in range(p, end)]
            moved = sum(_signed(g, out_positive) for g in group)
            if abs(moved - delta) <= max(_TOL, sum(g.amount for g in group) * 0.001):
                settled = end
                break
        if settled is not None:
            prev = row.balance
            p = settled
            continue
        # 2. Failed retries beside a completed row: the source can print the completed row's
        #    true balance on a neighbouring failed (skipped) row instead.
        expected_balance = prev + _signed(row, out_positive)
        found = _balance_on_skipped_neighbour(walk, kept[p], expected_balance)
        if found is not None:
            prev = found
            p += 1
            continue
        # User-facing (surfaced verbatim in the review UI) — plain language, no verdict on
        # WHOSE fault it is: state what's uncertain and what to do, not the arithmetic term.
        row.notes.append(
            "The statement's own running balance doesn't confirm this amount — "
            "worth a quick check before importing."
        )
        prev = row.balance
        p += 1
    if corrections:
        meta.warnings.append(f"{corrections} row direction(s) corrected from the balance column")
    return corrections


def _balance_on_skipped_neighbour(
    walk: list[CanonicalRow], at: int, expected: float
) -> float | None:
    """The balance printed on a skipped row right beside ``walk[at]`` (either side, across a run
    of skipped rows) that equals ``expected`` — where a failed retry carries the completed row's
    true balance."""
    for step in (-1, 1):
        k = at + step
        while 0 <= k < len(walk) and walk[k].skip:
            bal = walk[k].balance
            if bal is not None and abs(bal - expected) <= _TOL:
                return bal
            k += step
    return None


def reconcile(rows: list[CanonicalRow], meta: StatementMeta) -> dict:
    """The statement identity, per currency group: opening + Σsigned ≈ closing.

    Bank convention: money IN raises the balance. Card convention: charges (OUT) raise the
    owed balance. Only the statement's own currency group carries the opening/closing pair;
    other currency groups report "unknown" rather than false-failing a global sum (D4.2).
    Never checks FX rows against spot rates."""
    stmt_ccy = (meta.currency or "").upper()
    nets: dict[str, float] = {}
    for row in rows:
        if row.skip:
            continue
        ccy = (row.currency or stmt_ccy or "").upper()
        nets[ccy] = nets.get(ccy, 0.0) + _signed(row, out_positive=meta.sign_convention == "card")
    if meta.opening_balance is None or meta.closing_balance is None:
        return {"status": "unknown", "currency": stmt_ccy or None, "groups": sorted(nets)}
    expected = round(meta.closing_balance - meta.opening_balance, 2)
    net = round(nets.get(stmt_ccy, nets.get("", 0.0)), 2)
    tol = max(_TOL, 0.005 * max(1.0, len([r for r in rows if not r.skip])))
    if abs(net - expected) <= tol:
        return {"status": "ok", "expected": expected, "actual": net, "currency": stmt_ccy}
    if abs(-net - expected) <= tol:
        meta.warnings.append(
            "statement reconciles only with money in/out flipped — check the direction convention"
        )
        return {
            "status": "ok_if_flipped",
            "expected": expected,
            "actual": net,
            "currency": stmt_ccy,
        }
    return {
        "status": "off",
        "expected": expected,
        "actual": net,
        "delta": round(expected - net, 2),
        "currency": stmt_ccy,
    }


def _chain_walk(
    seq: list[CanonicalRow], out_positive: bool, flip: bool
) -> tuple[float, float] | None:
    """Validate one chain orientation: every row's balance must equal the previous balance
    plus its signed amount (± when ``flip`` inverts the direction convention). Returns the
    derived (opening, closing) for this orientation, or None on any broken link."""
    sign = -1.0 if flip else 1.0
    prev = seq[0].balance
    for row in seq[1:]:
        if abs(row.balance - (prev + sign * _signed(row, out_positive))) > _CHAIN_TOL:
            return None
        prev = row.balance
    opening = round(seq[0].balance - sign * _signed(seq[0], out_positive), 2)
    return opening, round(seq[-1].balance, 2)


def _chain_orientations(chain: list[CanonicalRow]) -> list[tuple[list[CanonicalRow], bool]]:
    """(sequence, flip) candidates, non-flipped first. Banks export oldest-first AND
    newest-first; when the file's own date ordering proves one direction, the other is not
    even tried — this is what keeps a uniform-amount book (rent/DCA exports, where forward
    and reversed-flipped are algebraically identical) provable instead of ambiguous."""
    dates = [r.date for r in chain]
    ascending = all(a <= b for a, b in zip(dates, dates[1:]))
    descending = all(a >= b for a, b in zip(dates, dates[1:]))
    fwd, rev = chain, list(reversed(chain))
    if ascending and not descending:
        return [(fwd, False), (fwd, True)]
    if descending and not ascending:
        return [(rev, False), (rev, True)]
    return [(fwd, False), (rev, False), (fwd, True), (rev, True)]


def reconcile_from_balance_chain(
    rows: list[CanonicalRow], *, out_positive: bool = False, currency: str | None = None
) -> dict | None:
    """Prove a statement complete from its OWN running-balance column — for sources that
    declare no opening/closing pair (tabular exports print per-row balances but rarely a
    header identity, so ``reconcile`` stays "unknown" forever). An unbroken chain in FILE
    ORDER (never date-sorted — same-day rows interleave) is per-link proof that no row is
    missing between the first and last: strictly stronger than the endpoint identity, and
    label-free (no bank keywords, no LLM).

    Verdict shape mirrors ``reconcile`` (status ok / ok_if_flipped + expected/actual/
    currency) plus ``derived`` {opening_balance, closing_balance, period_start, period_end}
    — the observed [min, max] date window is justified as the period precisely because the
    chain proves nothing is missing inside it.

    Returns None — NEVER a false claim — when: any non-skip row lacks a balance or date (a
    partial column proves nothing), fewer than two rows chain (a single row has no link, so
    it can't speak to missing rows), currencies mix, every amount is zero (links that never
    move prove nothing), the chain breaks in every orientation, or two passing orientations
    disagree on opening/closing (degenerate books — genuinely undecidable)."""
    chain = [r for r in rows if not r.skip]
    if len(chain) < 2 or any(r.balance is None or not r.date for r in chain):
        return None
    ccys = {r.currency.upper() for r in chain if r.currency}
    if len(ccys) > 1 or all(abs(r.amount) <= _CHAIN_TOL for r in chain):
        return None
    passes: list[tuple[str, float, float]] = []
    for seq, flip in _chain_orientations(chain):
        walked = _chain_walk(seq, out_positive, flip)
        if walked is not None:
            passes.append(("ok_if_flipped" if flip else "ok", *walked))
    if not passes:
        return None
    status, opening, closing = passes[0]
    if any(
        abs(o - opening) > _CHAIN_TOL or abs(c - closing) > _CHAIN_TOL for _, o, c in passes[1:]
    ):
        return None
    net = round(sum(_signed(r, out_positive) for r in chain), 2)
    return {
        "status": status,
        "expected": round(closing - opening, 2),
        "actual": round(-net, 2) if status == "ok_if_flipped" else net,
        "currency": (currency or "").upper() or (sorted(ccys)[0] if ccys else None),
        "method": "balance_chain",
        "derived": {
            "opening_balance": opening,
            "closing_balance": closing,
            "period_start": min(r.date for r in chain),
            "period_end": max(r.date for r in chain),
        },
    }


def _warn_once(meta: StatementMeta, msg: str) -> None:
    # The chain re-runs on every cached re-preview (the decisions.py self-heal) — a stable
    # message must never stack.
    if msg not in meta.warnings:
        meta.warnings.append(msg)


def apply_chain_reconcile(result: ParseResult) -> bool:
    """Upgrade an UNKNOWN reconcile from the running-balance chain and, on a pass, fill
    ``meta``'s opening/closing/period ONLY where absent — a printed/extracted value is never
    overwritten, and a printed value that CONTRADICTS the derived one beyond tolerance keeps
    the verdict at unknown + a warning (the chain and the header disagree; one of them is
    misread, so claiming completeness on either would be a lie). Returns True on upgrade."""
    if ((result.reconcile or {}).get("status") or "unknown") != "unknown":
        return False
    meta = result.meta
    verdict = reconcile_from_balance_chain(
        result.rows, out_positive=meta.sign_convention == "card", currency=meta.currency
    )
    if verdict is None:
        return False
    derived = verdict["derived"]
    for attr, label in (("opening_balance", "opening"), ("closing_balance", "closing")):
        printed = getattr(meta, attr)
        if printed is not None and abs(printed - derived[attr]) > _CHAIN_TOL:
            _warn_once(
                meta,
                f"the running-balance chain derives {label} {derived[attr]:.2f} but the "
                f"statement declares {printed:.2f} — completeness not claimed",
            )
            return False
    result.reconcile = verdict
    if verdict["status"] == "ok_if_flipped":
        _warn_once(
            meta,
            "statement reconciles only with money in/out flipped — check the direction convention",
        )
    if meta.opening_balance is None:
        meta.opening_balance = derived["opening_balance"]
    if meta.closing_balance is None:
        meta.closing_balance = derived["closing_balance"]
    if not meta.period_start:
        meta.period_start = derived["period_start"]
    if not meta.period_end:
        meta.period_end = derived["period_end"]
    return True


def _amount_variants(amount: float) -> list[str]:
    variants = [f"{amount:,.2f}", f"{amount:.2f}"]
    if amount == int(amount):
        variants += [f"{int(amount):,}", str(int(amount))]
    variants += [v.replace(",", "X").replace(".", ",").replace("X", ".") for v in list(variants)]
    return variants


def span_verify(rows: list[CanonicalRow], source_text: str) -> int:
    """The Rossum constraint — a highlighter, not a pen: every LLM-extracted amount/reference
    must exist in the source text. An unmatched value is flagged as possible hallucination
    (confidence floored, routed to review) — never silently trusted. Text tiers only (vision
    has no text layer; those rows lean on the balance/reconcile guards). Returns flag count."""
    if not source_text:
        return 0
    digits_stream = re.sub(r"\D", "", source_text)
    flagged = 0
    for row in rows:
        if row.skip:
            continue
        found = any(v in source_text for v in _amount_variants(row.amount))
        if not found:
            digits = re.sub(r"\D", "", f"{row.amount:.2f}")
            found = len(digits) >= 6 and digits in digits_stream
        if not found:
            row.confidence = min(row.confidence, _LOW_CONFIDENCE)
            row.notes.append("amount not found in source text (possible misread)")
            flagged += 1
        if row.reference and len(row.reference) >= 8 and row.reference not in source_text:
            if re.sub(r"\D", "", row.reference) not in digits_stream:
                row.notes.append("reference not found in source text")
                row.reference = None
    return flagged


def apply_guards(result: ParseResult, source_text: str | None = None) -> ParseResult:
    """Run the deterministic guard pipeline over a fresh ParseResult, in the D4 order.

    Tabular tiers get the balance-chain fallback: a spreadsheet never declares the
    opening/closing pair the identity needs, so without it every csv/xlsx reconcile stayed
    "unknown" and the completeness gate (books-close promotion) could never open."""
    parse_progress.report("reconcile")
    rederive_from_balance(result.rows, result.meta)
    result.reconcile = reconcile(result.rows, result.meta)
    if result.tier.startswith("tabular"):
        apply_chain_reconcile(result)
    if source_text and result.tier == "text":
        span_verify(result.rows, source_text)
    return result
