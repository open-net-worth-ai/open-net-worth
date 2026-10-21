"""A synthetic YNAB API for tests and local QA: serve() returns a running stub and its
/v1 base URL (patch backend.services.aggregation.ynab.API_BASE to point the importer at
it). Fixture: 2 cash accounts, a card, a closed account, a tracking account, a transfer pair,
a split with a transfer line, an uncategorized row, inflows to Ready to Assign, a hidden used
category, a deleted row, a zero row, and 3 months of budgeted amounts + one future month.
All figures are invented."""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "stub-ynab-token"
PLAN = "11111111-1111-1111-1111-111111111111"


def build(today: date, negative_assignment: bool = False) -> dict:

    def month(off):
        i = today.year * 12 + today.month - 1 + off
        return i // 12, i % 12 + 1

    def d(off, day):
        y, m = month(off)
        return f"{y:04d}-{m:02d}-{day:02d}"

    def mk(n):
        return f"{n:08d}-0000-0000-0000-000000000000"

    A = {k: mk(i) for i, k in enumerate(["chk", "sav", "visa", "old", "brk"], 1)}
    C = {
        k: mk(100 + i)
        for i, k in enumerate(
            ["rta", "visacat", "rent", "util", "groc", "dine", "fun", "oldcat", "uncat"], 1
        )
    }
    G = {
        k: mk(200 + i)
        for i, k in enumerate(["internal", "cc", "bills", "needs", "wants", "hid"], 1)
    }

    accounts = [
        dict(
            id=A["chk"],
            name="Checking",
            type="checking",
            on_budget=True,
            closed=False,
            deleted=False,
        ),
        dict(
            id=A["sav"], name="Savings", type="savings", on_budget=True, closed=False, deleted=False
        ),
        dict(
            id=A["visa"],
            name="Visa",
            type="creditCard",
            on_budget=True,
            closed=False,
            deleted=False,
        ),
        dict(
            id=A["old"],
            name="Old Account",
            type="checking",
            on_budget=True,
            closed=True,
            deleted=False,
        ),
        dict(
            id=A["brk"],
            name="Brokerage",
            type="otherAsset",
            on_budget=False,
            closed=False,
            deleted=False,
        ),
    ]

    def cat(key, name, gkey, hidden=False, internal=False):
        return dict(
            id=C[key],
            name=name,
            category_group_id=G[gkey],
            hidden=hidden,
            internal=internal,
            deleted=False,
        )

    groups = [
        dict(
            id=G["internal"],
            name="Internal Master Category",
            hidden=False,
            internal=True,
            deleted=False,
            categories=[cat("rta", "Inflow: Ready to Assign", "internal", internal=True)],
        ),
        dict(
            id=G["cc"],
            name="Credit Card Payments",
            hidden=False,
            internal=False,
            deleted=False,
            categories=[cat("visacat", "Visa", "cc")],
        ),
        dict(
            id=G["bills"],
            name="Bills",
            hidden=False,
            internal=False,
            deleted=False,
            categories=[cat("rent", "Rent", "bills"), cat("util", "Utilities", "bills")],
        ),
        dict(
            id=G["needs"],
            name="Needs",
            hidden=False,
            internal=False,
            deleted=False,
            categories=[cat("groc", "Groceries", "needs"), cat("dine", "Dining out", "needs")],
        ),
        dict(
            id=G["wants"],
            name="Wants",
            hidden=False,
            internal=False,
            deleted=False,
            categories=[cat("fun", "Fun Money", "wants")],
        ),
        dict(
            id=G["hid"],
            name="Hidden Categories",
            hidden=False,
            internal=False,
            deleted=False,
            categories=[cat("oldcat", "Old Cat", "hid", hidden=True)],
        ),
    ]

    _n = [0]

    def tid():
        _n[0] += 1
        return f"txn-{_n[0]:04d}"

    txns = []

    def T(acct, date_, milli, payee=None, cat_=None, memo=None, cleared="cleared", **kw):
        t = dict(
            id=tid(),
            account_id=A[acct],
            date=date_,
            amount=milli,
            payee_name=payee,
            memo=memo,
            category_id=C[cat_] if cat_ else None,
            cleared=cleared,
            approved=True,
            deleted=False,
            transfer_account_id=None,
            transfer_transaction_id=None,
            subtransactions=[],
        )
        t.update(kw)
        txns.append(t)
        return t

    def link(a, b):
        a["transfer_account_id"], b["transfer_account_id"] = b["account_id"], a["account_id"]
        a["transfer_transaction_id"], b["transfer_transaction_id"] = b["id"], a["id"]

    T("chk", d(-2, 1), 5_000_000, "Starting Balance", "rta", cleared="reconciled")
    T("sav", d(-2, 1), 1_000_000, "Starting Balance", "rta", cleared="reconciled")
    T("visa", d(-2, 1), -300_000, "Starting Balance", None, cleared="reconciled")
    T("old", d(-2, 1), 100_000, "Starting Balance", "rta", cleared="reconciled")
    T("brk", d(-2, 1), 10_000_000, "Starting Balance", None, cleared="reconciled")
    T("chk", d(-2, 2), 3_000_000, "Employer", "rta")
    T("chk", d(-2, 3), -1_200_000, "Landlord", "rent")
    T("chk", d(-2, 5), -80_500, "Grocer", "groc", memo="weekly shop")
    T("chk", d(-2, 5), -80_500, "Grocer", "groc", memo="weekly shop")  # identical twin on purpose
    a = T("chk", d(-2, 10), -500_000, "Transfer : Savings")
    b = T("sav", d(-2, 10), 500_000, "Transfer : Checking")
    link(a, b)
    a = T("chk", d(-2, 15), -50_000, "Transfer : Visa", cleared="uncleared")
    b = T("visa", d(-2, 15), 50_000, "Transfer : Checking")
    link(a, b)
    T("visa", d(-2, 20), -60_000, "Bistro", "dine")
    a = T("old", d(-2, 28), -100_000, "Transfer : Checking")
    b = T("chk", d(-2, 28), 100_000, "Transfer : Old Account")
    link(a, b)
    T("sav", d(-1, 28), 1_230, "Interest", "rta")
    T("chk", d(-1, 1), 3_000_000, "Employer", "rta")
    T("chk", d(-1, 2), -1_200_000, "Landlord", "rent")
    # split with a transfer line: -150 = groceries -100, fun -30, transfer to Savings -20
    sp = T("chk", d(-1, 12), -150_000, "Costco", None, memo="big trip", subtransactions=[])
    sp["subtransactions"] = [
        dict(
            id="sub-1",
            transaction_id=sp["id"],
            amount=-100_000,
            memo="food",
            category_id=C["groc"],
            deleted=False,
            transfer_account_id=None,
            transfer_transaction_id=None,
            payee_name=None,
        ),
        dict(
            id="sub-2",
            transaction_id=sp["id"],
            amount=-30_000,
            memo=None,
            category_id=C["fun"],
            deleted=False,
            transfer_account_id=None,
            transfer_transaction_id=None,
            payee_name=None,
        ),
        dict(
            id="sub-3",
            transaction_id=sp["id"],
            amount=-20_000,
            memo="to savings",
            category_id=None,
            deleted=False,
            transfer_account_id=A["sav"],
            transfer_transaction_id=None,
            payee_name=None,
        ),
    ]
    mirror = T(
        "sav",
        d(-1, 12),
        20_000,
        "Transfer : Checking",
        None,
        transfer_account_id=A["chk"],
        transfer_transaction_id="sub-3",
    )
    sp["subtransactions"][2]["transfer_transaction_id"] = mirror["id"]
    T("chk", d(0, 1), 3_000_000, "Employer", "rta")
    T("chk", d(0, 1), -1_200_000, "Landlord", "rent")
    T("chk", d(0, 1), 15_000, "Grocer", "groc", memo="refund")
    T("chk", d(0, 1), -12_340, "Mystery", None, cleared="uncleared")
    T("chk", d(0, 1), -5_000, "Old shop", "oldcat")
    T("visa", d(0, 1), -25_000, "Pizza", "dine", cleared="uncleared")
    T("chk", d(0, 1), -99_000, "Deleted", "groc", deleted=True)
    T("chk", d(0, 1), 0, "Zero", "groc")

    budget = {  # offset -> {cat: milli}
        -2: dict(rent=1_200_000, groc=300_000, dine=100_000, fun=50_000),
        -1: dict(rent=1_200_000, groc=300_000, dine=100_000, fun=50_000),
        0: dict(rent=1_200_000, groc=300_000, dine=100_000, fun=50_000, oldcat=10_000),
        1: dict(rent=1_200_000),
    }
    if negative_assignment:
        budget[-1]["fun"] = -10_000
    cat_ids = {v: k for k, v in C.items()}

    def activity_by_cat_month():
        act = {}
        for t in txns:
            if t["deleted"]:
                continue
            if t["subtransactions"]:
                for s in t["subtransactions"]:
                    if s["category_id"]:
                        act[(s["category_id"], t["date"][:7])] = (
                            act.get((s["category_id"], t["date"][:7]), 0) + s["amount"]
                        )
            elif t["category_id"] and t["category_id"] not in (C["rta"],):
                act[(t["category_id"], t["date"][:7])] = (
                    act.get((t["category_id"], t["date"][:7]), 0) + t["amount"]
                )
        return act

    def month_detail(off):
        y, m = month(off)
        key = f"{y:04d}-{m:02d}"
        act = activity_by_cat_month()
        cats = []
        for g in groups:
            for c in g["categories"]:
                if g["internal"] or g["name"] == "Credit Card Payments":
                    continue
                avail = 0
                for o in range(-2, off + 1):
                    yy, mm = month(o)
                    k = f"{yy:04d}-{mm:02d}"
                    bud = budget.get(o, {}).get(cat_ids[c["id"]], 0)
                    avail = max(0, avail) + bud + act.get((c["id"], k), 0)
                cats.append(
                    dict(
                        c,
                        budgeted=budget.get(off, {}).get(cat_ids[c["id"]], 0),
                        activity=act.get((c["id"], key), 0),
                        balance=avail,
                    )
                )
        inflow = sum(t["amount"] for t in txns if not t["deleted"] and t["category_id"] == C["rta"])
        tbb = inflow - sum(sum(v.values()) for o, v in budget.items())
        return dict(
            month=f"{key}-01",
            income=0,
            budgeted=sum(c["budgeted"] for c in cats),
            activity=0,
            to_be_budgeted=tbb,
            deleted=False,
            categories=cats,
        )

    def acct_out():
        out = []
        for a in accounts:
            mine = [t for t in txns if t["account_id"] == a["id"] and not t["deleted"]]
            bal = sum(t["amount"] for t in mine)
            clr = sum(t["amount"] for t in mine if t["cleared"] != "uncleared")
            out.append(dict(a, balance=bal, cleared_balance=clr, uncleared_balance=bal - clr))
        return out

    return dict(
        accounts=accounts,
        groups=groups,
        txns=txns,
        month_detail=month_detail,
        acct_out=acct_out,
        d=d,
    )


def _export(state: dict) -> dict:
    """The full-plan export's flat shape: payees by id, split lines and categories in lists."""
    payees: dict[str, str] = {}

    def payee_id(name):
        return payees.setdefault(name, f"payee-{len(payees)}") if name else None

    txns, subs = [], []
    for t in state["txns"]:
        flat = {k: v for k, v in t.items() if k not in ("subtransactions", "payee_name")}
        txns.append({**flat, "payee_id": payee_id(t.get("payee_name"))})
        subs += [{**s, "transaction_id": t["id"]} for s in t.get("subtransactions") or []]
    groups = state["groups"]
    return {
        "accounts": state["acct_out"](),
        # YNAB's real export flags every group internal; only the category flag is reliable.
        "category_groups": [
            {**{k: v for k, v in g.items() if k != "categories"}, "internal": True} for g in groups
        ],
        "categories": [
            {**c, "category_group_id": g["id"]} for g in groups for c in g.get("categories", [])
        ],
        "months": [state["month_detail"](o) for o in (-2, -1, 0, 1)],
        "transactions": txns,
        "subtransactions": subs,
        "payees": [{"id": i, "name": n} for n, i in payees.items()],
    }


def _handler(state: dict, calls: list):
    month_detail, d = state["month_detail"], state["d"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            query, path = parse_qs(u.query), u.path
            calls.append(path)
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send({"error": {"id": "401", "name": "unauthorized"}}, 401)
            base = f"/v1/plans/{PLAN}"
            fmt = dict(iso_code="USD", currency_symbol="$", decimal_digits=2)
            if path == "/v1/plans":
                return self._send(
                    {"data": {"plans": [dict(id=PLAN, name="Synthetic Plan", currency_format=fmt)]}}
                )
            if path == base:
                return self._send({"data": {"plan": _export(state), "server_knowledge": 1}})
            if path == base + "/accounts":
                return self._send(
                    {"data": {"accounts": state["acct_out"](), "server_knowledge": 1}}
                )
            if path == base + "/categories":
                return self._send(
                    {"data": {"category_groups": state["groups"], "server_knowledge": 1}}
                )
            if path.startswith(base + "/accounts/") and path.endswith("/transactions"):
                account = path.split("/")[-2]
                since = (query.get("since_date") or [d(-12, 1)])[0]
                rows = [
                    t for t in state["txns"] if t["account_id"] == account and t["date"] >= since
                ]
                return self._send({"data": {"transactions": rows, "server_knowledge": 1}})
            if path == base + "/transactions":
                since = (query.get("since_date") or [d(-12, 1)])[0]
                rows = [t for t in state["txns"] if t["date"] >= since]
                return self._send({"data": {"transactions": rows, "server_knowledge": 1}})
            if path == base + "/months":
                months = [
                    {k: v for k, v in month_detail(o).items() if k != "categories"}
                    for o in (-2, -1, 0, 1)
                ]
                return self._send({"data": {"months": months, "server_knowledge": 1}})
            if path.startswith(base + "/months/"):
                wanted = path.rsplit("/", 1)[1][:7]
                for o in (-2, -1, 0, 1):
                    if month_detail(o)["month"][:7] == wanted:
                        return self._send({"data": {"month": month_detail(o)}})
            self._send({"error": {"id": "404"}}, 404)

    return Handler


def serve(today: date | None = None, port: int = 0, negative_assignment: bool = False):
    """Start the stub on a daemon thread. Returns ``(server, base_url, calls)``; call
    ``server.shutdown()`` when done. ``calls`` records every request path."""
    state = build(today or date.today(), negative_assignment)
    calls: list = []
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(state, calls))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1", calls
