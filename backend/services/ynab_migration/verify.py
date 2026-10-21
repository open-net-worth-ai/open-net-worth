"""Compare the migrated book with YNAB, number by number (read-only on both sides)."""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services import ledger as ledger_service
from backend.services.ynab_migration import apply, source
from backend.services.ynab_migration.mapping import RTA_CATEGORY, SOURCE, amt


def _row(group, label, expected, actual, note="", unit="money"):
    ok = (
        expected == actual
        if isinstance(expected, (int, str))
        else abs(expected - actual) < apply.TOL
    )
    return {
        "group": group,
        "label": label,
        "expected": expected,
        "actual": actual,
        "ok": ok,
        "note": note,
        "unit": unit,
    }


def verify_accounts(plan: dict, ynab_accounts: list[dict]) -> list[dict]:
    live = {a["id"]: a for a in ynab_accounts}
    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    onw = {}
    for a in ledger_service.accounts.query_accounts(include_archived=True):
        for e in a.get("external_ids") or []:
            if e.get("source") == SOURCE:
                onw[e["ref"]] = ledger_service.account_with_balance(a, txns)
    out = []
    for pa in plan["accounts"]:
        name, row = pa["payload"]["name"], onw.get(pa["id"])
        if row is None:
            out.append(_row("account", f"{name}: account", "present", "missing"))
            continue
        y = live.get(pa["id"], {})
        balance = amt(y.get("balance", 0))
        ysum = pa.get("ynab_sum", balance)
        out.append(
            _row(
                "account",
                f"{name}: balance",
                balance,
                float(row["balance"]),
                ""
                if abs(ysum - balance) < apply.TOL
                else f"YNAB's transactions sum to {ysum:,.2f}",
            )
        )
        adjust = pa["cleared_adjust"]
        cleared = amt(y.get("cleared_balance", 0))
        note = (
            f"YNAB {cleared:g} {adjust:+.2f}: a transfer is cleared per transaction here"
            if adjust
            else ""
        )
        out.append(
            _row(
                "account",
                f"{name}: cleared balance",
                round(cleared + adjust, 2),
                float(row.get("cleared_balance", 0)),
                note,
            )
        )
        count = sum(1 for t in txns if _touches(t, row["id"]))
        out.append(
            _row(
                "account",
                f"{name}: transactions",
                pa["expected_rows"],
                count,
                f"YNAB has {pa['ynab_rows']}",
                unit="count",
            )
        )
    return out


def _touches(txn: dict, account_id: str) -> bool:
    return account_id in (txn.get("from_account_id"), txn.get("to_account_id"))


def verify_budget(store, plan_id: str, plan: dict, env_ids: dict[str, dict]) -> list[dict]:
    today = user_today()
    month = f"{today.year:04d}-{today.month:02d}"
    ymonth = source.fetch_month(store, plan_id, month)
    table, rows = apply.month_rows(month)
    ycats = {c["id"]: c for c in ymonth["categories"]}
    out = []
    for cat in plan["categories"]:
        ycat, env = ycats.get(cat["id"]), env_ids.get(cat["id"], {}).get("envelope")
        if ycat is None:
            continue
        if env is None or env not in rows:
            out.append(_row("envelope", f"{cat['label']}: envelope", "present", "missing"))
            continue
        out.append(
            _row(
                "envelope",
                f"{cat['label']}: assigned {month}",
                amt(ycat["budgeted"]),
                float(rows[env]["assigned"]),
            )
        )
        out.append(
            _row(
                "envelope",
                f"{cat['label']}: available {month}",
                amt(ycat["balance"]),
                float(rows[env]["available"]),
            )
        )
    out += _special_rows(plan, ycats, rows, table, month)
    out.append(
        _row(
            "ready",
            f"Ready to Assign {month}",
            amt(ymonth["to_be_budgeted"]),
            float(table["ready_to_assign"]),
            "YNAB month: "
            + ", ".join(
                f"{k} {amt(ymonth.get(k) or 0):,.2f}" for k in ("income", "budgeted", "activity")
            ),
        )
    )
    return out


def _line_diff(plan: dict, month: str, cid: str, cat_id: str | None) -> str:
    """Which transactions only one side counts for this category and month."""
    ynab = plan.get("lines", {}).get(f"{month}|{cid}", {})
    here: dict[str, float] = {}
    for t in ledger_service.transactions.all():
        if t.get("deleted") or not (t.get("date") or "").startswith(month):
            continue
        parts = [s for s in t.get("splits") or [] if s.get("category") == cat_id]
        amount = sum(s["amount"] for s in parts) if parts else None
        if amount is None and t.get("category") == cat_id:
            amount = t.get("from_amount") or t.get("to_amount") or 0
        if amount is None:
            continue
        for r in t.get("external_refs") or []:
            here[r.get("ref")] = amount
    only_ynab = [f"{amt(v):,.2f} [{k[:8]}]" for k, v in ynab.items() if k not in here]
    only_here = [f"{v:,.2f} [{k[:8]}]" for k, v in here.items() if k not in ynab and ":" not in k]
    return f" · only in YNAB: {only_ynab[:6]} · only here: {only_here[:6]}"


def first_divergence(plan: dict, env_ids: dict[str, dict], until: str) -> list[dict]:
    """Walk every past month oldest-first and report, per envelope, the FIRST month whose
    activity or available differs from YNAB — the month to look at when today's figure is off."""
    labels = {c["id"]: c["label"] for c in plan["categories"]}
    labels |= {
        cid: f"{name} (card payment)" for cid, name in plan.get("card_categories", {}).items()
    }
    pending = {cid for cid in labels if env_ids.get(cid, {}).get("envelope")}
    out, rta_done = [], False
    for month in sorted(m for m in plan.get("history", {}) if m < until):
        if not pending and rta_done:
            break
        hist = plan["history"][month]
        table, rows = apply.month_rows(month)
        for cid in sorted(pending):
            row = rows.get(env_ids[cid]["envelope"])
            if row is None or cid not in hist["cats"]:
                continue
            activity, balance = (amt(v) for v in hist["cats"][cid])
            if (
                abs(activity - row["activity"]) < apply.TOL
                and abs(balance - row["available"]) < apply.TOL
            ):
                continue
            pending.discard(cid)
            out.append(
                _row(
                    "history",
                    f"{labels[cid]}: first differs {month}",
                    balance,
                    float(row["available"]),
                    f"available · activity YNAB {activity:,.2f} here {row['activity']:,.2f}"
                    + _line_diff(plan, month, cid, env_ids[cid].get("category")),
                )
            )
        if not rta_done and abs(amt(hist["rta"]) - table["ready_to_assign"]) >= apply.TOL:
            rta_done = True
            out.append(
                _row(
                    "history",
                    f"Ready to Assign: first differs {month}",
                    amt(hist["rta"]),
                    float(table["ready_to_assign"]),
                )
            )
    return out


def _special_rows(plan, ycats, rows, table, month) -> list[dict]:
    """YNAB's credit card payment and Uncategorized categories are not migrated as envelopes
    (the app keeps its own payment envelope and uncategorized hold); compare them to those."""
    migrated = {c["id"] for c in plan["categories"]}
    by_label = {r["label"].lower(): r for r in rows.values()}
    out = []
    for cid, ycat in ycats.items():
        name = ycat.get("name") or ""
        if cid in migrated or ycat.get("deleted") or name.strip().lower() == RTA_CATEGORY:
            continue
        if name.strip().lower() == "uncategorized":
            actual = float(table.get("held_outside_envelopes") or 0)
        elif name.lower() in by_label:
            actual = float(by_label[name.lower()]["available"])
        elif ycat.get("balance") or ycat.get("activity"):
            actual = 0.0  # a YNAB category holding money that has no counterpart here
        else:
            continue
        out.append(
            _row(
                "special",
                f"{name}: available {month}",
                amt(ycat["balance"]),
                actual,
                f"YNAB activity {amt(ycat.get('activity', 0)):,.2f}, "
                f"assigned {amt(ycat.get('budgeted', 0)):,.2f}",
            )
        )
    return out


def verify(store, plan_id: str, plan: dict, env_ids: dict[str, dict], with_budget: bool) -> dict:
    """Re-reads YNAB's CURRENT figures, so it reflects the plan as it stands now."""
    rows = verify_accounts(plan, source.fetch_accounts(store, plan_id))
    if with_budget:
        rows += verify_budget(store, plan_id, plan, env_ids)
        if not all(r["ok"] for r in rows if r["group"] in ("envelope", "special")):
            today = user_today()
            rows += first_divergence(plan, env_ids, f"{today.year:04d}-{today.month:02d}")
    return {
        "rows": rows,
        "ok": all(r["ok"] for r in rows),
        "mismatches": sum(not r["ok"] for r in rows),
    }
