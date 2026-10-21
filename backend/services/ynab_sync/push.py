"""Ledger -> YNAB: send the ledger's version of each changed transaction in as few requests as
possible (one POST for creates, one PATCH per 100 updates, one DELETE per delete), keep the
mirror equal to what YNAB now holds (so the next read of our own write is a no-op), and report
what could not be sent — never silently drop it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backend.core.errors import ExternalServiceError
from backend.services.ynab_sync import client as ynab_client
from backend.services.ynab_sync.client import RateCapReached, YnabClient
from backend.services.ynab_sync.context import Context
from backend.services.ynab_sync.payloads import (
    Unsupported,
    classify,
    create_payload,
    update_payloads,
)
from backend.services.ynab_sync.units import Unit

BATCH = 100


@dataclass
class Outcome:
    sent: list[Unit] = field(default_factory=list)
    deferred: list[Unit] = field(default_factory=list)
    unsupported: list[tuple[Unit, str]] = field(default_factory=list)
    failed: list[tuple[Unit, str]] = field(default_factory=list)
    new_refs: dict[str, list[str]] = field(default_factory=dict)  # unit key -> YNAB ids created
    # Called the moment a unit is confirmed sent, so refs and baselines are recorded per batch and
    # a later failure in the run cannot leave YNAB holding rows the ledger has not linked.
    on_sent: Callable[[Unit], None] = lambda unit: None


def _currencies(unit: Unit) -> set[str]:
    row = unit.row or {}
    return {row[k].upper() for k in ("from_currency", "to_currency") if row.get(k)}


def _remember(ctx: Context, saved: dict) -> None:
    """Make the mirror hold what YNAB reported back. A transfer's other leg is rebuilt from this
    one when YNAB did not return it — without it the transfer would read as one-sided."""
    ctx.mirror[saved["id"]] = {**ctx.mirror.get(saved["id"], {}), **saved}
    ctx.mirror[saved["id"]].setdefault("subtransactions", [])
    twin = saved.get("transfer_transaction_id")
    if twin and twin not in ctx.mirror and saved.get("transfer_account_id"):
        ctx.mirror[twin] = {
            **saved,
            "id": twin,
            "account_id": saved["transfer_account_id"],
            "transfer_account_id": saved["account_id"],
            "transfer_transaction_id": saved["id"],
            "amount": -saved["amount"],
            "subtransactions": [],
        }
    for s in saved.get("subtransactions") or []:
        ctx.sub_parent[s["id"]] = saved["id"]


def _send(client: YnabClient, method: str, jobs: list[tuple[Unit, list[dict]]], out: Outcome, done):
    """Send ``jobs`` as one batch; when YNAB rejects it, halve until the bad job stands alone."""
    ops = [op for _, group in jobs for op in group]
    try:
        saved = client.save_transactions(method, ops)
    except ExternalServiceError as exc:
        if not ynab_client.rejected(exc):
            raise
        if len(jobs) == 1:
            out.failed.append((jobs[0][0], str(exc)))
            return
        mid = len(jobs) // 2
        _send(client, method, jobs[:mid], out, done)
        _send(client, method, jobs[mid:], out, done)
        return
    done(jobs, saved)


def _confirm(out: Outcome, unit: Unit) -> None:
    out.sent.append(unit)
    out.on_sent(unit)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _prepare(ctx: Context, units: list[Unit], out: Outcome, build) -> list[tuple[Unit, list[dict]]]:
    jobs = []
    for unit in units:
        try:
            jobs.append((unit, build(unit)))
        except Unsupported as exc:
            out.unsupported.append((unit, str(exc)))
    return jobs


def _run(ctx, client, method, jobs, out: Outcome, done) -> None:
    for i, chunk in enumerate(_chunks(jobs, BATCH)):
        try:
            _send(client, method, chunk, out, done)
        except RateCapReached:
            done_ids = {id(u) for u in out.sent}
            out.deferred += [u for u, _ in jobs[i * BATCH :] if id(u) not in done_ids]
            return


def _key(row: dict, *, sent: bool) -> tuple:
    """What identifies a created transaction in YNAB's answer: account, date, amount, memo, payee
    (case-insensitive), category, and whether it is a transfer."""
    payee = (row.get("payee_name") or "").strip().casefold()
    transfer = bool(row.get("payee_id") if sent else row.get("transfer_account_id"))
    return (
        row["account_id"],
        row["date"],
        row["amount"],
        row.get("memo") or "",
        "" if transfer else payee,
        row.get("category_id"),
        transfer,
    )


def _match_created(jobs: list[tuple[Unit, list[dict]]], saved: list[dict]) -> list[dict | None]:
    """Which saved transaction answers each create payload (YNAB does not echo our order)."""
    pool = list(saved)
    found = []
    for _, (body,) in jobs:
        key = _key(body, sent=True)
        hit = next((s for s in pool if _key(s, sent=False) == key), None)
        if hit is not None:
            pool.remove(hit)
        found.append(hit)
    return found


def send_creates(ctx: Context, client: YnabClient, units: list[Unit], out: Outcome) -> None:
    jobs = _prepare(ctx, units, out, lambda u: [create_payload(ctx, u.onw, _currencies(u))])

    def done(batch, saved):
        for (unit, _), hit in zip(batch, _match_created(batch, saved)):
            if hit is None:
                out.failed.append((unit, "YNAB created it but did not report which one it is"))
                continue
            _remember(ctx, hit)
            twin = hit.get("transfer_transaction_id")
            out.new_refs[unit.key] = [hit["id"], *([twin] if twin else [])]
            _confirm(out, unit)

    _run(ctx, client, "POST", jobs, out, done)


def send_updates(ctx: Context, client: YnabClient, units: list[Unit], out: Outcome) -> None:
    jobs = _prepare(ctx, units, out, lambda u: update_payloads(ctx, u.refs, u.base, u.onw))

    def done(batch, saved):
        for s in saved:
            _remember(ctx, s)
        for unit, _ in batch:
            _confirm(out, unit)

    _run(ctx, client, "PATCH", jobs, out, done)


def send_deletes(ctx: Context, client: YnabClient, units: list[Unit], out: Outcome) -> None:
    for i, unit in enumerate(units):
        shape, tops = classify(ctx, unit.refs)
        if shape == "complex" or not tops:
            out.unsupported.append(
                (unit, "this is one part of a larger YNAB split; delete it in YNAB")
            )
            continue
        try:
            client.delete_transaction(tops[0])
        except RateCapReached:
            out.deferred += units[i:]
            return
        except ExternalServiceError as exc:
            if "HTTP 404" not in str(exc):
                raise
        for gone in (tops[0], ctx.mirror.get(tops[0], {}).get("transfer_transaction_id")):
            ctx.mirror.pop(gone, None)
        _confirm(out, unit)
