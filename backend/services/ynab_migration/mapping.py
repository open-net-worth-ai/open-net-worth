"""YNAB plan -> Open Net Worth plan: the PURE half of the one-shot migration (no I/O).

Everything here is deterministic over YNAB's JSON (accounts, category groups, transactions,
months; amounts are MILLIUNITS). It yields a neutral ``plan`` keyed by YNAB ids; ``apply`` writes
it, ``verify`` checks the result against YNAB. See ADR
``2026-10-19-ynab-migration-is-a-one-shot-job-beside-the-importer``.
"""

from __future__ import annotations

from collections import Counter

from backend.core.errors import ValidationError
from backend.core.untrusted import sanitize_untrusted

SOURCE = "ynab"  # external_refs / external_ids namespace — the connector's, so both dedup
SB_PAYEE = "starting balance"
RTA_CATEGORY = "inflow: ready to assign"
CC_GROUP = "credit card payments"
RESERVED_GROUPS = {"commitments", "credit card payments"}  # groups the app keeps itself
LOAN_TYPES = {"mortgage", "autoLoan", "studentLoan", "personalLoan", "medicalDebt", "otherDebt"}


INT64_MAX = 2**63 - 1


def clean(text, max_len: int) -> str:
    """YNAB text is third-party data that Lucius later reads: defang fence forgeries and cap it."""
    return sanitize_untrusted(str(text or ""), max_len=max_len).strip()


def validate_amounts(accounts: list[dict], txns: list[dict], months: list[dict]) -> None:
    """Every milliunit figure must be a plain int64 — a float, NaN or oversized number from a
    misbehaving server would otherwise reach the ledger and the budget writers."""

    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > INT64_MAX:
            raise ValidationError("YNAB returned an amount that is not a valid whole number.")

    for a in accounts:
        for key in ("balance", "cleared_balance"):
            check(a.get(key, 0))
    for t in txns:
        check(t["amount"])
        for sub in t.get("subtransactions") or []:
            check(sub["amount"])
    for m in months:
        for c in m.get("categories", []):
            check(c.get("budgeted", 0))
        check(m.get("to_be_budgeted", 0))


def amt(milli: int) -> float:
    return round(milli / 1000, 3)


def sign(n: int) -> int:
    return (n > 0) - (n < 0)


def map_account_type(acct: dict) -> str:
    """YNAB account type -> one of ONW's seven. On-budget is ONW's cash/credit only, so an
    off-budget (tracking) YNAB account lands on the nearest off-budget type: cash-like ->
    fixed_deposit (counts in net worth, not spendable), otherAsset -> investment, debt -> loan."""
    kind, on_budget = acct.get("type"), bool(acct.get("on_budget"))
    if kind in LOAN_TYPES:
        return "loan"
    if kind in ("creditCard", "lineOfCredit", "otherLiability"):
        return "credit" if on_budget else "loan"
    if kind == "otherAsset":
        return "cash" if on_budget else "investment"
    return "cash" if on_budget else "fixed_deposit"


def unique_labels(items: list[tuple[str, str, str]]) -> dict[str, str]:
    """(id, name, group) -> id -> a case-insensitively unique label; a clash becomes 'Name (Group)'."""
    taken: set[str] = set()
    out: dict[str, str] = {}
    for key, name, group in items:
        label = name.strip()
        if label.lower() in taken:
            label = f"{name.strip()} ({group})"
        while label.lower() in taken:
            label += f" [{key[:4]}]"
        taken.add(label.lower())
        out[key] = label
    return out


def plan_categories(groups: list[dict], txns: list[dict], months: list[dict]) -> dict:
    """Which YNAB categories become ONW categories + envelopes, and why each other one is not."""
    used = {t.get("category_id") for t in txns if not t.get("deleted")}
    for t in txns:
        used |= {
            s.get("category_id") for s in t.get("subtransactions") or [] if not s.get("deleted")
        }
    for m in months:
        used |= {c["id"] for c in m.get("categories", []) if c.get("budgeted")}
    skip: dict[str, str] = {}
    cards: dict[str, str] = {}  # card payment category id -> its card account's name
    chosen: list[tuple[str, str, str, bool]] = []
    for g in groups:
        if g.get("deleted"):
            continue
        card_group = g["name"].strip().lower() == CC_GROUP
        for c in g.get("categories", []):
            hidden = bool(c.get("hidden") or g.get("hidden"))
            if c.get("deleted"):
                continue
            # The category's own flag: YNAB's full-plan export marks EVERY group internal, which
            # skipped all of a real plan's categories and booked their spending uncategorized.
            if c.get("internal"):
                skip[c["id"]] = "rta" if c["name"].strip().lower() == RTA_CATEGORY else "internal"
            elif card_group:
                skip[c["id"]] = "card_payment"
                cards[c["id"]] = clean(c["name"], 120)
            elif hidden and c["id"] not in used:
                skip[c["id"]] = "hidden_unused"
            else:
                chosen.append((c["id"], clean(c["name"], 120), clean(g["name"], 120), hidden))
    labels = unique_labels([(i, n, g) for i, n, g, _ in chosen])
    cats = [
        {
            "id": i,
            "label": labels[i],
            "group": f"{g} (YNAB)" if g.strip().lower() in RESERVED_GROUPS else g,
            "hidden": h,
        }
        for i, _, g, h in chosen
    ]
    return {"categories": cats, "skipped": skip, "card_categories": cards}


def plan_transactions(txns: list[dict], accounts: list[dict], cat_plan: dict):
    """YNAB transactions -> ONW 'pieces' (one ONW transaction each, keyed by YNAB ids), the
    per-account Starting Balance rows, and counters."""
    live = {a["id"]: a for a in accounts if not a.get("deleted")}
    real_cats = {c["id"] for c in cat_plan["categories"]}
    rows = sorted((t for t in txns if not t.get("deleted")), key=lambda t: (t["date"], t["id"]))
    by_id = {t["id"]: t for t in rows}
    by_link = {t["transfer_transaction_id"]: t for t in rows if t.get("transfer_transaction_id")}
    stats: Counter = Counter()
    openings: dict[str, dict] = {}
    for t in rows:
        is_sb = (t.get("payee_name") or "").strip().lower() == SB_PAYEE
        if t["account_id"] in live and is_sb and not t.get("transfer_account_id"):
            openings.setdefault(t["account_id"], t)
    opening_ids = {t["id"] for t in openings.values()}
    handled: set[str] = set()
    pieces: list[dict] = []

    def cref(cid):
        if cid in real_cats:
            return cid
        if cid and cid not in cat_plan["skipped"]:
            stats["unknown_category"] += 1
        return None

    def plain(t, refs, amount=None, category=None, memo=None):
        value = t["amount"] if amount is None else amount
        pieces.append(
            {
                "kind": "expense" if value < 0 else "income",
                "date": t["date"],
                "description": clean(t.get("payee_name"), 200),
                "memo": clean(t.get("memo") if memo is None else memo, 500),
                "category": category,
                "splits": None,
                "cleared": t.get("cleared") != "uncleared",
                "refs": refs,
                "skew": [],
                "from" if value < 0 else "to": (t["account_id"], abs(value)),
            }
        )
        return pieces[-1]

    def transfer(out, inn, refs, category=None, memo=None):
        stats["transfers_paired"] += 1
        c_out, c_in = out.get("cleared") != "uncleared", inn.get("cleared") != "uncleared"
        both = c_out and c_in
        # ONW keeps one cleared flag per transaction; remember what that does to each account's
        # cleared balance versus YNAB's per-leg flags, so verification can account for it.
        skew = [
            (row["account_id"], (int(both) - int(flag)) * row["amount"])
            for row, flag in ((out, c_out), (inn, c_in))
        ]
        pieces.append(
            {
                "kind": "transfer",
                "date": max(out["date"], inn["date"]),
                "description": clean(out.get("payee_name"), 200),
                "memo": clean(
                    memo if memo is not None else (out.get("memo") or inn.get("memo")), 500
                ),
                "category": category,
                "splits": None,
                "cleared": both,
                "refs": refs,
                "skew": [(acct, delta) for acct, delta in skew if delta],
                "from": (out["account_id"], abs(out["amount"])),
                "to": (inn["account_id"], abs(inn["amount"])),
            }
        )
        return pieces[-1]

    def budget_leg_category(out, inn):
        """YNAB counts a transfer's category only when exactly one leg is in a budget account,
        and only that leg's; a tracking leg can keep a stale one from before it was tracking."""
        legs = [r for r in (out, inn) if live[r["account_id"]].get("on_budget")]
        return cref(legs[0].get("category_id")) if len(legs) == 1 else None

    def categorized_opening(t):
        """A Starting Balance YNAB split into categories funds those categories: book each
        categorized part as its own row and keep only the rest as the opening balance."""
        subs = [x for x in t.get("subtransactions") or [] if not x.get("deleted")]
        parts = [x for x in subs or [t] if x["amount"] and cref(x.get("category_id"))]
        for x in parts:
            plain(t, [x["id"]], x["amount"], cref(x["category_id"]), x.get("memo"))
        return {**t, "amount": t["amount"] - sum(x["amount"] for x in parts)}

    def partner_of(row, other):
        """The row in the other account that mirrors ``row`` (a transfer line), if it exists."""
        p = by_id.get(row.get("transfer_transaction_id")) or by_link.get(row["id"])
        ok = other and p and p["account_id"] == other and other in live and p["id"] != row["id"]
        return p if ok and sign(p["amount"]) == -sign(row["amount"]) != 0 else None

    # Split parents first: they claim the mirror rows of their transfer lines.
    ordered = [t for t in rows if t.get("subtransactions")] + [
        t for t in rows if not t.get("subtransactions")
    ]
    for t in ordered:
        if t["id"] in opening_ids:
            openings[t["account_id"]] = categorized_opening(t)
            continue
        if t["id"] in handled or t["account_id"] not in live:
            continue
        subs = [s for s in t.get("subtransactions") or [] if not s.get("deleted")]
        if subs:
            plan_split(
                t, subs, partner_of, cref, plain, transfer, handled, stats, budget_leg_category
            )
            continue
        if t["amount"] == 0:
            stats["zero_amount_skipped"] += 1
            continue
        other = t.get("transfer_account_id")
        partner = partner_of(t, other)
        if partner:
            out, inn = (t, partner) if t["amount"] < 0 else (partner, t)
            handled.add(partner["id"])
            transfer(out, inn, [out["id"], inn["id"]], budget_leg_category(out, inn))
            continue
        if other:
            stats["transfers_one_sided"] += 1
        on_budget = live[t["account_id"]].get("on_budget")
        plain(t, [t["id"]], category=cref(t.get("category_id")) if on_budget else None)
    pieces.sort(key=lambda p: (p["date"], p["refs"][0]))
    stats["subcent_amounts"] = sum(
        1
        for t in rows
        for a in [t["amount"], *(s["amount"] for s in t.get("subtransactions") or [])]
        if a % 10
    )
    return pieces, openings, stats


def plan_split(t, subs, partner_of, cref, plain, transfer, handled, stats, leg_category):
    """A split parent: transfer lines become transfers, the rest one ONW split (in the direction
    of its total); opposite-direction or category-less lines become plain rows of their own. The
    parent's id rides on the first piece, so the connector's row for it dedups."""
    stats["splits"] += 1
    lines, loose, moves = [], [], []
    for s in subs:
        if s["amount"] == 0:
            continue
        partner = partner_of(s, s.get("transfer_account_id"))
        if partner and s["transfer_account_id"] != t["account_id"]:
            moves.append((s, partner))
        else:
            (lines if cref(s.get("category_id")) else loose).append(s)
    direction = sign(sum(s["amount"] for s in lines)) or sign(t["amount"])
    main = [s for s in lines if sign(s["amount"]) == direction]
    made = []
    if main:
        piece = plain(t, [s["id"] for s in main], amount=sum(s["amount"] for s in main))
        piece["splits"] = [
            {
                "category": cref(s["category_id"]),
                "milli": abs(s["amount"]),
                "memo": clean(s.get("memo"), 500),
            }
            for s in main
        ]
        if len(main) == 1:
            piece["category"], piece["splits"] = piece["splits"][0]["category"], None
        made.append(piece)
    for s in lines:
        if s not in main:
            made.append(plain(t, [s["id"]], s["amount"], cref(s["category_id"]), s.get("memo")))
    for s in loose:
        made.append(plain(t, [s["id"]], s["amount"], None, s.get("memo")))
    for s, partner in moves:
        stats["split_transfer_lines"] += 1
        handled.add(partner["id"])
        mine = {**t, "amount": s["amount"], "category_id": s.get("category_id")}
        out, inn = (mine, partner) if s["amount"] < 0 else (partner, mine)
        made.append(
            transfer(
                out,
                inn,
                [s["id"], partner["id"]],
                category=leg_category(out, inn),
                memo=s.get("memo") or t.get("memo"),
            )
        )
    if made:
        made[0]["refs"].append(t["id"])
    else:
        stats["zero_amount_skipped"] += 1


def balance_gaps(live, txns, pieces, stats) -> None:
    """YNAB's balance for some accounts (loans, with interest YNAB calculates itself) is not the
    sum of their transactions; book the difference once, as dated, labelled adjustments — the
    cleared part cleared and the rest uncleared, so both balances match YNAB's."""
    last = max((t["date"] for t in txns if not t.get("deleted")), default=None)
    for a in live:
        mine = [t for t in txns if not t.get("deleted") and t["account_id"] == a["id"]]
        cleared_gap = a.get("cleared_balance", 0) - sum(
            t["amount"] for t in mine if t.get("cleared") != "uncleared"
        )
        gap = a.get("balance", 0) - sum(t["amount"] for t in mine)
        for part, cleared, ref in (
            (cleared_gap, True, "balance"),
            (gap - cleared_gap, False, "balance-uncleared"),
        ):
            if not part or last is None:
                continue
            stats["balance_adjustments"] += 1
            pieces.append(
                {
                    "kind": "adjustment",
                    "date": last,
                    "description": "YNAB balance not covered by its transactions",
                    "memo": "Booked by the YNAB import (e.g. loan interest YNAB calculates itself)",
                    "category": None,
                    "splits": None,
                    "cleared": cleared,
                    "refs": [f"{a['id']}:{ref}"],
                    "skew": [],
                    "to" if part > 0 else "from": (a["id"], abs(part)),
                }
            )


def build_plan(plan_name, currency, accounts, groups, txns, months) -> dict:
    validate_amounts(accounts, txns, months)
    cat_plan = plan_categories(groups, txns, months)
    pieces, openings, stats = plan_transactions(txns, accounts, cat_plan)
    live = [a for a in accounts if not a.get("deleted")]
    names = unique_labels(
        [(a["id"], clean(a["name"], 120), "closed" if a.get("closed") else "2") for a in live]
    )
    balance_gaps(live, txns, pieces, stats)
    earliest: dict[str, str] = {}
    legs: Counter = Counter()
    skew: Counter = Counter()
    for p in pieces:
        for acct, delta in p["skew"]:
            skew[acct] += delta
        for side in ("from", "to"):
            if p.get(side):
                acct = p[side][0]
                earliest[acct] = min(earliest.get(acct, p["date"]), p["date"])
                legs[acct] += 1
    out_accounts = []
    for a in live:
        sb = openings.get(a["id"])
        dates = [d for d in (sb["date"] if sb else None, earliest.get(a["id"])) if d]
        payload = {
            "name": names[a["id"]],
            "type": map_account_type(a),
            "currency": currency,
            "initial_balance": amt(sb["amount"]) if sb else 0,
            "external_ids": [{"source": SOURCE, "ref": a["id"], "name": clean(a["name"], 120)}],
            "note": clean(a.get("note"), 500),
        }
        if dates:
            payload["initial_balance_date"] = min(dates)
        out_accounts.append(
            {
                "id": a["id"],
                "ynab_type": a["type"],
                "on_budget": bool(a.get("on_budget")),
                "closed": bool(a.get("closed")),
                "payload": payload,
                "ynab_rows": sum(
                    1 for t in txns if not t.get("deleted") and t["account_id"] == a["id"]
                ),
                "ynab_sum": amt(
                    sum(
                        t["amount"]
                        for t in txns
                        if not t.get("deleted") and t["account_id"] == a["id"]
                    )
                ),
                "expected_rows": legs[a["id"]] + 1,  # +1: ONW always mints the Starting Balance row
                "cleared_adjust": amt(skew[a["id"]]),
            }
        )
    real = {c["id"] for c in cat_plan["categories"]}
    budgeted = real | set(cat_plan["card_categories"])
    assigned: dict[str, dict[str, int]] = {}
    for m in months:
        row = {
            c["id"]: c["budgeted"]
            for c in m.get("categories", [])
            if c["id"] in budgeted and c.get("budgeted")
        }
        if row:
            assigned[m["month"][:7]] = row
    lines: dict[str, dict[str, int]] = {}  # "YYYY-MM|category" -> {YNAB id: milliunits}
    for t in txns:
        if t.get("deleted"):
            continue
        for row in [t, *(t.get("subtransactions") or [])]:
            if row.get("category_id") in real and not row.get("deleted"):
                key = f"{t['date'][:7]}|{row['category_id']}"
                lines.setdefault(key, {})[row["id"]] = row["amount"]
    history = {
        m["month"][:7]: {
            "rta": m.get("to_be_budgeted", 0),
            "cats": {
                c["id"]: (c.get("activity", 0), c.get("balance", 0))
                for c in m.get("categories", [])
                if c["id"] in budgeted
            },
        }
        for m in months
    }
    return {
        "name": clean(plan_name, 120),
        "currency": currency,
        "accounts": out_accounts,
        "categories": cat_plan["categories"],
        "skipped_categories": dict(Counter(cat_plan["skipped"].values())),
        "pieces": pieces,
        "assigned": assigned,
        "card_categories": cat_plan["card_categories"],
        "history": history,
        "lines": lines,
        "stats": dict(stats),
    }


def render_piece(p: dict, acct_ids: dict, cat_ids: dict, currency: str) -> dict:
    """A piece -> a ledger bulk-create row (account and category ids resolved)."""
    row = {
        "date": p["date"],
        "kind": p["kind"],
        "description": p["description"],
        "memo": p["memo"],
        "external_refs": [{"source": SOURCE, "ref": r} for r in p["refs"]],
        "needs_review": False,
        "cleared": p["cleared"],
    }
    for side in ("from", "to"):
        if p.get(side):
            acct, milli = p[side]
            row[f"{side}_account_id"], row[f"{side}_amount"] = acct_ids[acct], amt(milli)
            row[f"{side}_currency"] = currency
    if p["category"]:
        row["category"] = cat_ids[p["category"]]
    if p["splits"]:
        row["splits"] = [
            {"category": cat_ids[s["category"]], "amount": amt(s["milli"]), "memo": s["memo"]}
            for s in p["splits"]
        ]
    return row


def preview_summary(plan: dict) -> dict:
    """The numbers the UI shows before anything is written."""
    s = plan["stats"]
    warnings = []
    if s.get("subcent_amounts"):
        warnings.append(
            f"{s['subcent_amounts']} amount(s) have sub-cent precision; they are rounded to cents."
        )
    if s.get("unknown_category"):
        warnings.append(
            f"{s['unknown_category']} line(s) use a category YNAB no longer lists; they import uncategorized."
        )
    if s.get("transfers_one_sided"):
        warnings.append(
            f"{s['transfers_one_sided']} transfer(s) have no matching row in the other account; they import as plain income or expense."
        )
    return {
        "plan_name": plan["name"],
        "currency": plan["currency"],
        "accounts": [
            {
                "name": a["payload"]["name"],
                "ynab_type": a["ynab_type"],
                "type": a["payload"]["type"],
                "on_budget": a["on_budget"],
                "closed": a["closed"],
                "opening_balance": a["payload"]["initial_balance"],
                "transactions": a["expected_rows"] - 1,
            }
            for a in plan["accounts"]
        ],
        "categories": len(plan["categories"]),
        "groups": len({c["group"] for c in plan["categories"]}),
        "skipped_categories": plan["skipped_categories"],
        "transactions": len(plan["pieces"]),
        "transaction_kinds": dict(Counter(p["kind"] for p in plan["pieces"])),
        "splits": s.get("splits", 0),
        "transfers": s.get("transfers_paired", 0),
        "budget_months": len(plan["assigned"]),
        "warnings": warnings,
    }
