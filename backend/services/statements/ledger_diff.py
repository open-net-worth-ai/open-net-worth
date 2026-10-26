"""Ledger-vs-statement diff — the OTHER direction of the import reconciliation.

The import flow accounts for every STATEMENT row (create / merge / skip / dup). This module
answers the inverse question the owner asked for: which LEDGER transactions in the covered
period + accounts are NOT on the statement? Those are exactly two things:

- a duplicate that slipped the near-dup tolerances (an agent/email-booked row with an
  ESTIMATED amount vs the statement's settled figure — reported case: a RideNow ride
  booked at −S$13.71 by an agent vs −S$14.07 settled, 2.6% apart, both in the ledger), or
- a transaction genuinely missing from the statement — pending, mis-accounted, or worth a
  closer look.

Computed-not-stored: derived on demand from the statement record's cached parse + the live
ledger. Read-only.

This module does NO matching and NO money comparison (RFC 2026-07-29 §D3). It is a pure SET
DIFFERENCE: every ledger row in the window+accounts, minus everything the statement accounts for
(``covered``). Pairing a statement row to a ledger entry — including the settled-drift tier this
file used to own as ``_rank_twin_pairs`` — belongs to ``services/matching`` and only there, so
one set of tolerances and one set of invariants govern every "these might be the same
transaction" claim in the app.

Every uncovered row is projected by ``import_anomalies._existing_summary`` plus two fields this
module owns, both computed SERVER-SIDE so the import preview and the ``import_transactions``
tool read one answer (owner, 2026-07-31 — "please handle these on the backend, not client so the
ai tool can get the same information"):

- ``on_statements`` — the statement(s) the entry is ALREADY on, resolved from ``external_refs``
                    (NOT from ``batch_id``, which answers where the row came FROM and is
                    blind to every row a statement linked rather than created). None when it is
                    on no statement we hold. See ``ledger_statement_links``.
- ``near_cutoff`` — which window edge it sits near and by how many days, or None. Non-None is
                    *by construction* the same predicate that files a row under ``boundary``.

Both directions of that difference read ONE derivation of ``covered``: pre-commit,
``preview_ledger_check`` takes the flags ``decision_preview`` just built; post-commit,
``uncovered_ledger_rows`` rebuilds them from the cached parse through
``decisions.rederive_match_pass`` — the same pass ``decision_commit`` merges against (Phase 4 /
divergence #5). A statement never has two answers to "what does this account for"."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.core.txn_sources import ext_ref_pairs
from backend.services import ledger as ledger_service
from backend.services.import_anomalies import _existing_summary
from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.decisions import (
    _attach_booked_echo_flags,
    _pair_existing_transfers,
    _parse_withheld_reason,
    _routed_booked_transfers,
    rederive_match_pass,
)
from backend.services.statements.ledger_statement_links import StatementRefIndex, on_statements
from backend.services.statements.tiers import StatementParseError

logger = logging.getLogger(__name__)


def _parse_day(value: str) -> date | None:
    """A stored ``date`` as a real date. Returns None for anything unparseable — a row whose
    date we cannot measure can never be *proven* near a cut-off, so it stays in the main list
    rather than being quietly filed under "probably the adjacent statement"."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# The window widens a little past the printed period — banks post boundary transactions on
# either side, and a diff that false-flags the first day of the next cycle helps nobody.
_PERIOD_SLACK_DAYS = 1


def _draft_ref_pairs(results: list) -> set[tuple[str | None, str]]:
    """Every ``(source, ref)`` provenance pair carried by the drafts of a result set — the
    dedup key both ``preview_ledger_check`` and ``_covered_ids`` test existing ledger rows against."""
    pairs: set[tuple[str | None, str]] = set()
    for r in results:
        if r.draft is not None:
            pairs |= ext_ref_pairs(r.draft)
    return pairs


def _statement_store():
    from backend.db.statement_store import StatementStore

    return StatementStore()


def _window(meta: dict, rows: list[CanonicalRow] | None = None) -> tuple[str, str] | None:
    """The date window the statement covers. Printed period wins; a statement-date-only
    record (some cards print no period) falls back to the trailing month; with neither (owner,
    2026-07-28: "in case the printed period disappear"), the caller may opt in to a THIRD
    fallback — the statement's own first/last transaction dates — by passing ``rows``.

    That third tier is an opt-in, not a default: it's the right window for "which of my
    ledger entries in this span are missing from the statement" (the statement's own rows are
    an honest, independent lower bound on the span it covers), but it must NEVER back a
    completeness CLAIM (e.g. books-close promotion) — a statement's rows trivially span their
    own min/max date, so deriving "complete" from that would be circular. Callers making a
    completeness claim must omit ``rows`` and get the strict None-on-missing-period behavior."""
    start, end = meta.get("period_start"), meta.get("period_end")
    if not (start and end):
        stmt_date = meta.get("statement_date")
        if stmt_date:
            try:
                d = date.fromisoformat(str(stmt_date)[:10])
            except ValueError:
                return None
            start, end = (d - timedelta(days=31)).isoformat(), d.isoformat()
        else:
            dates = sorted(r.date for r in rows if r.date) if rows else []
            if not dates:
                return None
            start, end = dates[0], dates[-1]
    try:
        lo = date.fromisoformat(str(start)[:10]) - timedelta(days=_PERIOD_SLACK_DAYS)
        hi = date.fromisoformat(str(end)[:10]) + timedelta(days=_PERIOD_SLACK_DAYS)
    except ValueError:
        return None
    return lo.isoformat(), hi.isoformat()


def _signed_row(row: CanonicalRow) -> float:
    return row.amount if row.direction == "IN" else -row.amount


def is_reconcilable_leg(t: dict) -> bool:
    """A ledger row counts toward a statement's ledger-side net only when it is a live,
    non-adjustment leg — soft-deleted rows are gone, and ``kind == "adjustment"`` rows
    (Starting Balance, a manual ``reconcile()`` delta) are book-keeping fixes the statement
    itself never printed. ``_ledger_net`` has always skipped these; the ONE test other
    statement/ledger tie-outs (``verify_statement_balance``'s AI-tool fallback, MATH-46)
    must apply too, so the two never disagree on whether the ledger matches the statement."""
    return not t.get("deleted") and t.get("kind") != "adjustment"


def _ledger_net(account_id: str, lo: str, hi: str) -> float:
    """Signed native activity for one account in the window: + into, − out of."""
    net = 0.0
    for t in ledger_service.transactions.all():
        if not is_reconcilable_leg(t):
            continue
        if not (lo <= (t.get("date") or "") <= hi):
            continue
        if t.get("from_account_id") == account_id and t.get("from_amount") is not None:
            net -= float(t["from_amount"])
        if t.get("to_account_id") == account_id and t.get("to_amount") is not None:
            net += float(t["to_amount"])
    return round(net, 2)


def _totals(
    rows: list[CanonicalRow],
    results: list,
    check: dict,
    lo: str,
    hi: str,
) -> list[dict]:
    """The money-level tie-out (owner: "the amounts of the included ones match the total"):
    per routed account, ledger activity for the window vs the statement's own net — with the
    difference DECOMPOSED exactly: rows flagged not-on-statement, rows near the cut-off,
    statement rows not (yet) booked, and residual drift (matched entries kept at their
    original amounts — what Merge exists to fix). Never a bare mismatch."""
    per_acct: dict[str, dict] = {}
    accounts = {a["id"]: a for a in ledger_service.accounts.all()}
    for r, row in zip(results, rows):
        if r.draft is None:
            continue
        acct = r.draft.get("from_account_id") or r.draft.get("to_account_id") or ""
        if not acct:
            continue
        entry = per_acct.setdefault(
            acct,
            {
                "account_id": acct,
                "account_name": (accounts.get(acct) or {}).get("name"),
                "currency": (
                    r.draft.get("from_currency") or r.draft.get("to_currency") or ""
                ).upper(),
                "statement_net": 0.0,
                "statement_unbooked_net": 0.0,
            },
        )
        signed = _signed_row(row)
        entry["statement_net"] += signed
        booked = bool(r.duplicate) or any(f.get("existing_id") for f in (r.flags or []))
        if not booked:
            entry["statement_unbooked_net"] += signed

    def _net(entries: list[dict], acct: str) -> float:
        return round(
            sum(
                float(e.get("amount") or 0.0)
                for e in entries
                if acct in (e.get("_acct_ids") or [acct])
            ),
            2,
        )

    out: list[dict] = []
    for acct, entry in per_acct.items():
        ledger_net = _ledger_net(acct, lo, hi)
        uncovered_net = round(sum(float(e.get("amount") or 0.0) for e in check["rows"]), 2)
        boundary_net = round(sum(float(e.get("amount") or 0.0) for e in check["boundary"]), 2)
        if len(per_acct) > 1:
            # multi-account statements: uncovered entries carry no account split in their
            # summary — attribute conservatively only in the single-account case
            uncovered_net = 0.0
            boundary_net = 0.0
        stmt_net = round(entry["statement_net"], 2)
        unbooked = round(entry["statement_unbooked_net"], 2)
        # ledger = (statement − unbooked + drift) + uncovered + boundary  →  solve for drift
        #
        # `drift` is a RESIDUAL, not an independently-computed quantity: it is defined as
        # whatever makes the identity balance, so the identity can never fail and any real
        # discrepancy (a parse error, a dropped row, an FX mistake) is absorbed into this term
        # instead of surfacing. That is why the UI's "Show the math" waterfall — which printed
        # a "✓ adds up" tick off this identity — was removed (owner, 2026-07-31). Do not
        # resurrect a UI that reads this as a verification: an honest version would compute the
        # matched-row drift directly from the merged rows and REPORT the disagreement between
        # that figure and this residual.
        drift = round(ledger_net - (stmt_net - unbooked) - uncovered_net - boundary_net, 2)
        out.append(
            {
                **{k: v for k, v in entry.items() if not k.startswith("_")},
                "statement_net": stmt_net,
                "statement_unbooked_net": unbooked,
                "ledger_net": ledger_net,
                "uncovered_net": uncovered_net,
                "boundary_net": boundary_net,
                "drift": drift,
                "tie": abs(drift) < 0.01 and abs(uncovered_net) < 0.01 and abs(unbooked) < 0.01,
            }
        )
    out.sort(key=lambda e: e.get("account_name") or "")
    return out


def preview_ledger_check(
    record: dict,
    rows: list[CanonicalRow],
    results: list,
    account_ids: set[str],
    meta: dict | None = None,
) -> dict | None:
    """The PRE-COMMIT ledger check for the import preview: same diff, computed from the
    offers already built for this preview (no recompute), against the ROUTED accounts —
    the record's own account links don't exist until commit. Returns None when the
    statement carries no usable window.

    ``covered`` is built from EVERY flag carrying an ``existing_id``, so a weak-band
    settled-drift assignment removes its entry from ``rows`` for free — the inverted view is a
    consequence of the forward one, not a second derivation (RFC 2026-07-29 §D3)."""
    meta = meta if meta is not None else (record.get("import_meta") or {})
    window = _window(meta, rows=rows)
    if window is None or not account_ids or not rows:
        return None
    lo, hi = window
    covered: set[str] = set()
    batch_ids = set(record.get("batch_ids") or [])
    draft_pairs = _draft_ref_pairs(results)
    for r in results:
        for f in r.flags or []:
            if f.get("existing_id"):
                covered.add(f["existing_id"])
    for t in ledger_service.transactions.all():
        if t.get("deleted"):
            continue
        if t.get("batch_id") in batch_ids:
            covered.add(t["id"])
            continue
        if ext_ref_pairs(t) & draft_pairs:
            covered.add(t["id"])
    boundary_days = 0 if _window_is_self_referential(meta) else _BOUNDARY_DAYS
    out = _collect_uncovered(lo, hi, account_ids, covered, boundary_days=boundary_days)
    out["totals"] = _totals(rows, results, out, lo, hi)
    return out


# Rows this close to the window edge likely belong to the ADJACENT statement (post/cut-off
# dates) — reported separately, never silently dropped and never crying wolf. That reasoning
# requires the window to be an INDEPENDENT claim (a printed period, or a statement date) about
# where the statement's coverage actually ends — meaningful uncertainty about an adjacent
# statement. The rows-fallback window (``_window(meta, rows=...)`` with neither) has no such
# claim: its edges are DERIVED from these same rows' own dates, so "near the edge" is
# tautological, not a real adjacent-statement risk — reported case: an exact
# ledger match (same booking, same amount, same day) sat invisible in "boundary" instead of
# either linking or surfacing in "not on statement," because the fallback window's tail
# happened to land within 3 days of that row's own date, which it always will.
_BOUNDARY_DAYS = 3


def _window_is_self_referential(meta: dict) -> bool:
    """True when ``_window`` would have to fall back to the rows-derived window (no printed
    period, no statement date) — mirrors ``_window``'s own fallback condition without
    duplicating its date-parsing logic."""
    return not (meta.get("period_start") and meta.get("period_end")) and not meta.get(
        "statement_date"
    )


def _near_cutoff(d: date, lo: date, hi: date, boundary_days: int) -> dict | None:
    """Which window edge an entry sits near, and by how many days — or None when it sits
    clear of both.

    This is THE boundary definition, not a second one. The bucketing below used to compare
    ``d`` against ``lo + boundary_days`` / ``hi - boundary_days``; those are exactly
    ``(d - lo).days < boundary_days`` and ``(hi - d).days < boundary_days``, so this function
    returning non-None is *identical* to "belongs in the boundary bucket" — and
    ``_collect_uncovered`` now buckets on its result rather than re-deriving the comparison.
    ``boundary_days == 0`` (the self-referential window, see ``_BOUNDARY_DAYS``) therefore
    disables the flag and the bucket together, in one place.

    ``label`` is written here, server-side, so the preview UI and the tool surface say the same
    thing (owner, 2026-07-31). It is deliberately short: it renders inside a row chip."""
    if boundary_days <= 0:
        return None
    from_start, to_end = (d - lo).days, (hi - d).days
    if from_start >= boundary_days and to_end >= boundary_days:
        return None
    edge, days = ("start", from_start) if from_start <= to_end else ("end", to_end)
    if days <= 0:
        label = "Opening cut-off" if edge == "start" else "Closing cut-off"
    else:
        unit = "day" if days == 1 else "days"
        label = (
            f"{days} {unit} after opening" if edge == "start" else f"{days} {unit} before closing"
        )
    return {"edge": edge, "days": days, "label": label}


def _collect_uncovered(
    lo: str,
    hi: str,
    account_ids: set[str],
    covered: set[str],
    *,
    boundary_days: int = _BOUNDARY_DAYS,
) -> dict:
    core: list[dict] = []
    boundary: list[dict] = []
    lo_date, hi_date = date.fromisoformat(lo), date.fromisoformat(hi)
    index = StatementRefIndex()
    opening_ids = ledger_service.opening_txn_ids()
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("id") in covered:
            continue
        if t.get("kind") == "adjustment":
            continue
        d = t.get("date") or ""
        if not (lo <= d <= hi):
            continue
        if not ({t.get("from_account_id"), t.get("to_account_id")} & account_ids):
            continue
        # source/original_amount/original_currency ride on the base summary itself now
        # (_existing_summary computes them once) — no per-caller override needed here.
        # `on_statements` and `near_cutoff` are ledger-check-only (they need the statement store
        # and this window respectively), so they are attached here rather than widening the
        # shared projection every match flag also pays for.
        summary = _existing_summary(t, opening_ids) or {}
        summary["on_statements"] = on_statements(t, index)
        day = _parse_day(d)
        near = _near_cutoff(day, lo_date, hi_date, boundary_days) if day else None
        summary["near_cutoff"] = near
        (boundary if near else core).append(summary)
    core.sort(key=lambda r: (r.get("date") or "", r.get("id") or ""))
    boundary.sort(key=lambda r: (r.get("date") or "", r.get("id") or ""))
    return {"status": "ok", "period": {"start": lo, "end": hi}, "rows": core, "boundary": boundary}


def _covered_ids(
    record: dict, rows: list[CanonicalRow], account_id: str | None
) -> tuple[set[str], list]:
    """Every ledger transaction the statement ACCOUNTS FOR: rows this import created or
    merged (batch link / external-id link), plus every existing txn the match engine pairs
    with a statement row (near-dup, transfer pair, leg echo — the flags carry existing_id).
    Returns (covered ids, the built offers) so callers reuse one match pass.

    The pairing comes from ``decisions.rederive_match_pass`` — the SAME derivation
    ``decision_commit`` merges against and the same one ``decision_preview`` fed
    ``preview_ledger_check`` (RFC 2026-07-29, Phase 4 / divergence #5). This used to run its own
    per-row ``import_anomalies.detect_anomalies`` scan instead, which knows nothing of the weak
    settled-drift band, the owner's match policy or the batch's global 1:1 assignment — so an
    entry the preview showed as covered reappeared under "In your ledger, not on this statement"
    the moment the same statement was committed, and the owner got two answers to one question."""
    covered: set[str] = set()
    batch_ids = set(record.get("batch_ids") or [])

    match_pass = rederive_match_pass(record, rows, account_id)
    results = match_pass.results
    # The FX-pair / routed-transfer booked twins, attached at the same point in the sequence as
    # the preview attaches them (decision_preview) and commit re-derives them: without this an
    # already-booked transfer the statement legitimately accounts for reads as uncovered.
    _attach_booked_echo_flags(
        results,
        _pair_existing_transfers(rows, match_pass.ctx),
        _routed_booked_transfers(results),
    )
    draft_pairs = _draft_ref_pairs(results)
    for r in results:
        for f in r.flags or []:
            if f.get("existing_id"):
                covered.add(f["existing_id"])

    for t in ledger_service.transactions.all():
        if t.get("deleted"):
            continue
        if t.get("batch_id") in batch_ids:
            covered.add(t["id"])
            continue
        if ext_ref_pairs(t) & draft_pairs:
            covered.add(t["id"])
    return covered, results


def uncovered_ledger_rows(statement_id: str) -> dict:
    """Ledger transactions in the statement's window + accounts that the statement does not
    contain. Returns ``{status, period, accounts, rows}`` — status "no_window" when the
    record carries neither a period nor a statement date, "stale_policy" when the matching policy
    this statement was reviewed under no longer resolves (the panel withholds itself rather than
    re-derive under the looser default), and "parse_incomplete"/"reconcile_off" (ADR
    2026-09-09-an-incomplete-parse-withholds-the-ledger-diff) when the cached parse never
    finished reading every page/chunk or the statement's own balances don't add up — either
    way, "not on this statement" cannot be trusted, so no rows are listed."""
    record = _statement_store().find_by_id(statement_id)
    if record is None:
        return {"status": "not_found", "rows": []}
    meta = record.get("import_meta") or {}
    withheld = _parse_withheld_reason(meta, record.get("reconcile"), record.get("tier"))
    if withheld:
        return {"status": withheld, "rows": []}
    rows = [CanonicalRow(**r) for r in (record.get("import_rows") or [])]
    window = _window(meta, rows=rows)
    if window is None:
        return {"status": "no_window", "rows": []}
    lo, hi = window

    account_ids = set(record.get("account_ids") or [])
    if record.get("account_id"):
        account_ids.add(record["account_id"])
    if not account_ids:
        return {"status": "no_account", "rows": []}

    if not rows:
        return {"status": "no_parse", "rows": []}
    try:
        covered, results = _covered_ids(record, rows, record.get("account_id"))
    except StatementParseError:
        # The match policy the preview was reviewed under no longer re-resolves (a statement
        # skill's `metadata.match_policy` changed, or the statement re-routed). Say nothing
        # rather than guess: falling back to `MatchPolicy()` is the LOOSER direction, which
        # covers MORE entries and would under-report this panel — the reconciliation lie the
        # whole RFC exists to kill. `decision_commit` refuses the same stamp outright.
        logger.info(
            "ledger diff: statement %s has a stale match policy — panel withheld", statement_id
        )
        return {"status": "stale_policy", "rows": []}

    boundary_days = 0 if _window_is_self_referential(meta) else _BOUNDARY_DAYS
    out = _collect_uncovered(lo, hi, account_ids, covered, boundary_days=boundary_days)
    out["totals"] = _totals(rows, results, out, lo, hi)
    names = {
        a["id"]: a.get("name") for a in ledger_service.accounts.all() if a["id"] in account_ids
    }
    out["accounts"] = [
        {"id": i, "name": n} for i, n in sorted(names.items(), key=lambda x: x[1] or "")
    ]
    return out
