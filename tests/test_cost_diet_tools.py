"""Unit tests for the AI-cost-optimization tool diet changes (lane B).

Covers:
  1. get_transactions — limit/offset paging, full-row projection
  2. get_portfolio     — full-row projection, ticker-dedup
  3. list_accounts     — include_archived filter + note, full-row projection
  4. get_statement     — parsed_rows paging (20-row cap + offset), upload_entry text kept
  5. registry.dispatch — compact re-serialisation (JSON and non-JSON)

NOTE (2026-07-09): the original lane-B "diet" curated a field allowlist per tool (dropping
things like ticker/quantity/needs_review/last_four/search_queries/upload_entry as "UI-only").
That silently hid data multiple agents needed to make correct decisions — root-caused after
an unattended agent couldn't match a card's last-4 digits because list_accounts had deleted
the one field that would have let it. The curation was removed tool-by-tool; the tests below
that pinned "field X is dropped" now pin "field X is present" instead. Pagination, dedup, and
compact re-serialisation (the genuinely token-saving, non-data-hiding parts of lane B) are
unchanged and still tested as before.
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import _bounded as _b
from backend.core.txn_sources import primary_ref, primary_source

# ── helpers ──────────────────────────────────────────────────────────────────


def run(coro):
    return asyncio.run(coro)


def call_tool(name, **kwargs):
    from backend.ai.tools import execute_tool

    text, proposed = run(execute_tool(name, kwargs))
    return (json.loads(text) if text is not None else None), proposed


# ── 1. get_transactions: limit/offset paging ─────────────────────────────────


def _make_txn(i: int, kind: str = "expense") -> dict:
    return {
        "id": f"txn-{i:04d}",
        "date": "2026-06-01",
        "kind": kind,
        "description": f"Txn {i}",
        "category": "food",
        "from_account_id": "acc1",
        "from_amount": 10.0 * i,
        "from_currency": "SGD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "base_amount": 10.0 * i,
        "base_currency": "SGD",
        "cleared": False,
        "payee_id": None,
        "tags": [],
        "splits": None,
        "external_refs": [{"source": None, "ref": None}],
        "batch_id": None,
        "ticker": "BTC",
        "quantity": 1,
        "created_at": "2026-06-01T00:00:00",
        "source": "manual",
        "deleted": False,
        "fx_rate": None,
        "merged_from": None,
        "merged_into": None,
    }


def _service_result(txns, limit=None, offset=0):
    """Simulate the dict format returned by services/ledger.get_transactions."""
    if limit is not None:
        page = txns[offset : offset + limit]
        return {
            "transactions": page,
            "total": len(txns),
            "has_more": offset + len(page) < len(txns),
        }
    return {"transactions": txns}


def test_get_transactions_default_limit(monkeypatch):
    """Default call passes the bounded default limit to the service.

    Lowered 100 → 25 (2026-07-19 context-bounding lane): a 100-row page measured ~15,175
    tokens, and the `summary` block already answers money questions over the FULL filtered
    set, so the large default bought reach the model almost never used.
    """
    captured = {}

    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    def fake_get_txns(**kwargs):
        captured.update(kwargs)
        txns = [_make_txn(i) for i in range(5)]
        return _service_result(txns, kwargs.get("limit"), kwargs.get("offset") or 0)

    monkeypatch.setattr(ledger_mod, "get_transactions", fake_get_txns)

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    call_tool("get_transactions", month=6, year=2026)
    assert captured["limit"] == _b.DEFAULT_TXN_LIMIT == 25
    assert captured["offset"] == 0


def test_get_transactions_custom_limit_offset(monkeypatch):
    """Custom limit/offset are forwarded to the service."""
    captured = {}

    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    def fake_get_txns(**kwargs):
        captured.update(kwargs)
        txns = [_make_txn(i) for i in range(50)]
        return _service_result(txns, kwargs.get("limit"), kwargs.get("offset") or 0)

    monkeypatch.setattr(ledger_mod, "get_transactions", fake_get_txns)

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    res, _ = call_tool("get_transactions", month=6, year=2026, limit=10, offset=20)
    assert captured["limit"] == 10
    assert captured["offset"] == 20
    assert res["summary"]["showing"] == 10
    assert res["summary"]["count"] == 50
    assert res["summary"]["has_more"] is True
    assert "page_note" in res["summary"]


def test_get_transactions_projection_keeps_full_row(monkeypatch):
    """Returned transactions include ticker, quantity, created_at, source, deleted, fx_rate —
    a prior curated allowlist dropped these (root-caused 2026-07-09: an agent had no way to
    see which security a cash leg was for, or read back needs_review)."""
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    txns = [_make_txn(1)]
    monkeypatch.setattr(
        ledger_mod,
        "get_transactions",
        lambda **kw: _service_result(txns, kw.get("limit"), kw.get("offset") or 0),
    )

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    res, _ = call_tool("get_transactions", month=6, year=2026)
    txn = res["transactions"][0]
    for kept in (
        "id",
        "date",
        "kind",
        "description",
        "from_amount",
        "base_amount",
        "ticker",
        "quantity",
        "created_at",
        "source",
        "deleted",
        "fx_rate",
    ):
        assert kept in txn, f"Field '{kept}' should be present"


def test_get_transactions_optional_fields_present_even_when_empty(monkeypatch):
    """Optional fields (tags, splits, external_ref) are present (with their empty/null value)
    even when unset — the row is returned verbatim, not curated."""
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    txn = _make_txn(1)
    txn["note"] = None
    txn["tags"] = []
    txn["splits"] = None
    txn["external_refs"] = []
    monkeypatch.setattr(
        ledger_mod,
        "get_transactions",
        lambda **kw: _service_result([txn], kw.get("limit"), kw.get("offset") or 0),
    )

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    res, _ = call_tool("get_transactions", month=6, year=2026)
    row = res["transactions"][0]
    assert "note" in row and row["note"] is None
    assert row["tags"] == []
    assert row["splits"] is None
    assert primary_source(row) is None
    assert primary_ref(row) is None


def test_get_transactions_optional_fields_present_when_set(monkeypatch):
    """Optional fields ARE included when they carry real values."""
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    txn = _make_txn(1)
    txn["note"] = "reimbursable"
    txn["tags"] = ["work"]
    txn["external_refs"] = [{"source": "stmt", "ref": "ext-123"}]
    monkeypatch.setattr(
        ledger_mod,
        "get_transactions",
        lambda **kw: _service_result([txn], kw.get("limit"), kw.get("offset") or 0),
    )

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    res, _ = call_tool("get_transactions", month=6, year=2026)
    row = res["transactions"][0]
    assert row["note"] == "reimbursable"
    assert row["tags"] == ["work"]
    assert primary_source(row) == "stmt"
    assert primary_ref(row) == "ext-123"


def test_get_transactions_no_page_note_when_no_more(monkeypatch):
    """page_note is absent when the full result fits in the page."""
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    import backend.services.ledger as ledger_mod

    txns = [_make_txn(i) for i in range(3)]
    monkeypatch.setattr(
        ledger_mod,
        "get_transactions",
        lambda **kw: _service_result(txns, kw.get("limit"), kw.get("offset") or 0),
    )

    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    res, _ = call_tool("get_transactions", month=6, year=2026, limit=100)
    assert "page_note" not in res["summary"]
    assert res["summary"]["has_more"] is False


# ── 2. get_portfolio: projection ─────────────────────────────────────────────


def _make_position(symbol="AAPL", ticker="AAPL"):
    return {
        "position_id": "pos-1",
        "instrument_id": "inst-1",
        "symbol": symbol,
        "ticker": ticker,
        "label": "Apple Inc",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "quantity": 10,
        "price": 180.0,
        "currency": "USD",
        "quote_currency": "USD",
        "cost_basis": 150.0,
        "value": 1800.0,
        "base_value": 2400.0,
        "account_id": "acc-brok",
        "account_name": "Vantage Brokerage",
        "account_color": "#e2554f",
        "bucket": "liquid",
        "unit": "",
        "day_change": 5.0,
        "day_change_pct": 0.28,
        "prev_price": 179.5,
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "quote_per_unit": 1.0,
        "sector": "Technology",
        "institution": "Vantage Brokerage",
        "category": "Vantage Brokerage",
    }


def test_get_portfolio_keeps_full_row(monkeypatch, fx_rates):
    """account_color, day_change, prev_price, price_source, sector, institution, note are present —
    a prior curated allowlist dropped these (root-caused 2026-07-09: an agent had no way to
    match a bank/broker alert's account by institution, or see a position's own note)."""
    import backend.ai.tools as tools

    pos = _make_position()
    monkeypatch.setattr(tools, "read_rows", lambda: [dict(pos)])
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio")
    row = res["positions"][0]
    for kept in (
        "position_id",
        "instrument_id",
        "symbol",
        "quantity",
        "price",
        "day_change_pct",
        "account_color",
        "day_change",
        "prev_price",
        "price_source",
        "sector",
        "institution",
        "quote_per_unit",
        "category",
    ):
        assert kept in row, f"'{kept}' should be present"


def test_get_portfolio_ticker_dedup(monkeypatch, fx_rates):
    """ticker is omitted when it matches symbol."""
    import backend.ai.tools as tools

    pos = _make_position(symbol="AAPL", ticker="AAPL")
    monkeypatch.setattr(tools, "read_rows", lambda: [dict(pos)])
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio")
    assert "ticker" not in res["positions"][0], "ticker should be omitted when equal to symbol"


def test_get_portfolio_ticker_kept_when_different(monkeypatch, fx_rates):
    """ticker is kept when it differs from symbol (e.g. ADR vs local symbol)."""
    import backend.ai.tools as tools

    pos = _make_position(symbol="BABA", ticker="9988.HK")
    monkeypatch.setattr(tools, "read_rows", lambda: [dict(pos)])
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio")
    assert res["positions"][0].get("ticker") == "9988.HK", "ticker kept when it differs from symbol"


# ── 3. list_accounts: include_archived filter ────────────────────────────────


def _make_account(id, name, archived=False):
    return {
        "id": id,
        "name": name,
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "archived": archived,
        "color": "#e2554f",
        "note": "some note",
        "initial_balance_date": "2024-01-01",
        "last_four": "1234",
        "statement_close_day": None,
        "payment_due_day": None,
        "reminder_days_before": None,
    }


class FakeAccounts:
    def __init__(self, accts):
        self._accts = accts

    def query_accounts(self, include_archived=False):
        return self._accts

    def find_by_id(self, id):
        return next((a for a in self._accts if a["id"] == id), None)


class FakeTransactions:
    def all(self):
        return []


class FakeSvc:
    def __init__(self, accts, bals):
        self.accounts = FakeAccounts(accts)
        self.transactions = FakeTransactions()
        self._bals = bals

    def balances(self, include_archived=False):
        return self._bals

    def opening_amount(self, account, txns=None, *, idx=None):
        return 0.0

    def opening_index(self, txns, accts=None):
        from backend.services import ledger as _real_ledger

        return _real_ledger.opening_index(txns, accts)

    def _opening_row(self, account, txns=None, *, idx=None):
        from backend.services import ledger as _real_ledger

        return _real_ledger._opening_row(account, txns, idx=idx)

    def _signed_opening(self, row):
        from backend.services import ledger as _real_ledger

        return _real_ledger._signed_opening(row)

    def cleared_account_balance(self, account, txns, opening_date):
        from backend.services import ledger as _real_ledger

        return _real_ledger.cleared_account_balance(account, txns, opening_date)


def test_list_accounts_default_hides_archived(monkeypatch):
    """Default call hides archived accounts and appends a note."""
    accts = [_make_account("a1", "Active"), _make_account("a2", "Old", archived=True)]
    fake_svc = FakeSvc(accts, {"a1": 100.0, "a2": 0.0})

    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", fake_svc)

    res, _ = call_tool("list_accounts")
    ids = [a["id"] for a in res["accounts"]]
    assert "a1" in ids
    assert "a2" not in ids
    assert "note" in res
    assert "1 archived" in res["note"]


def test_list_accounts_include_archived(monkeypatch):
    """include_archived=true returns all accounts."""
    accts = [_make_account("a1", "Active"), _make_account("a2", "Old", archived=True)]
    fake_svc = FakeSvc(accts, {"a1": 100.0, "a2": 0.0})

    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", fake_svc)

    res, _ = call_tool("list_accounts", include_archived=True)
    ids = [a["id"] for a in res["accounts"]]
    assert "a1" in ids
    assert "a2" in ids
    assert "note" not in res


def test_list_accounts_keeps_full_row(monkeypatch):
    """color, note, initial_balance_date, last_four ARE in the returned rows — a prior curated
    allowlist dropped last_four specifically (root-caused 2026-07-09: an unattended agent
    couldn't match a card alert's "ending 1115" against any account, because the one tool
    exposing accounts to agents had deleted the field that would have let it)."""
    accts = [_make_account("a1", "Active")]
    fake_svc = FakeSvc(accts, {"a1": 500.0})

    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", fake_svc)

    res, _ = call_tool("list_accounts")
    row = res["accounts"][0]
    for kept in ("color", "note", "initial_balance_date", "last_four"):
        assert kept in row, f"'{kept}' should be present"
    assert row["balance"] == 500.0
    assert row["currency"] == "SGD"


def test_list_accounts_no_note_when_all_active(monkeypatch):
    """No note is appended when there are no archived accounts."""
    accts = [_make_account("a1", "Active")]
    fake_svc = FakeSvc(accts, {"a1": 100.0})

    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", fake_svc)

    res, _ = call_tool("list_accounts")
    assert "note" not in res


# ── 4. get_statement: parsed_rows paging ─────────────────────────────────────


def _make_statement_record(n_rows: int) -> dict:
    return {
        "id": "stmt-abc",
        "filename": "bank.csv",
        "created_at": "2026-01-01T00:00:00Z",
        "parsed_rows": [
            {"date": "2026-01-01", "amount": float(i), "description": f"Row {i}"}
            for i in range(n_rows)
        ],
        "upload_entry": {"kind": "text"},
    }


class FakeStatements:
    def __init__(self, record):
        self._record = record

    def find_by_id(self, id):
        if id == self._record["id"]:
            return self._record
        return None


class FakeSvcWithStatements:
    def __init__(self, record):
        self.statements = FakeStatements(record)


def test_get_statement_first_page(monkeypatch):
    """Default call returns first 20 rows and total count."""
    record = _make_statement_record(35)
    fake_svc = FakeSvcWithStatements(record)

    import backend.ai.tools.statements as stmt_tool

    monkeypatch.setattr(stmt_tool, "svc", fake_svc)

    res, _ = call_tool("get_statement", statement_id="stmt-abc")
    assert res["ok"] is True
    s = res["statement"]
    assert s["parsed_row_count"] == 35
    assert len(s["parsed_rows"]) == 20
    assert s["parsed_rows"][0]["amount"] == 0.0
    assert "parsed_rows_note" in s


def test_get_statement_second_page(monkeypatch):
    """offset=20 returns the next page."""
    record = _make_statement_record(35)
    fake_svc = FakeSvcWithStatements(record)

    import backend.ai.tools.statements as stmt_tool

    monkeypatch.setattr(stmt_tool, "svc", fake_svc)

    res, _ = call_tool("get_statement", statement_id="stmt-abc", offset=20)
    assert res["ok"] is True
    s = res["statement"]
    assert len(s["parsed_rows"]) == 15
    assert "parsed_rows_note" not in s


def test_get_statement_small_result_no_note(monkeypatch):
    """When all rows fit in first page, no paging note is added."""
    record = _make_statement_record(5)
    fake_svc = FakeSvcWithStatements(record)

    import backend.ai.tools.statements as stmt_tool

    monkeypatch.setattr(stmt_tool, "svc", fake_svc)

    res, _ = call_tool("get_statement", statement_id="stmt-abc")
    s = res["statement"]
    assert len(s["parsed_rows"]) == 5
    assert "parsed_rows_note" not in s


def test_get_statement_upload_entry_text_kept_binary_stripped(monkeypatch):
    """upload_entry is present (has_upload_entry bool also present) with its extracted text
    intact — a prior version stripped the whole blob (root-caused 2026-07-09: an agent
    reconciling an ambiguous parsed row had no way back to the source document). Only the
    genuinely large binary parts (data/images, base64 page renders) are still stripped —
    see test_get_statement_upload_entry_strips_binary_fields below."""
    record = _make_statement_record(5)
    fake_svc = FakeSvcWithStatements(record)

    import backend.ai.tools.statements as stmt_tool

    monkeypatch.setattr(stmt_tool, "svc", fake_svc)

    res, _ = call_tool("get_statement", statement_id="stmt-abc")
    assert res["statement"]["has_upload_entry"] is True
    assert res["statement"]["upload_entry"] == {"kind": "text"}


def test_get_statement_upload_entry_strips_binary_fields(monkeypatch):
    """data/images (base64 blobs) are stripped from upload_entry; text/kind/summary/format
    survive — an agent should see extracted text, never a megabyte of base64 image data."""
    record = _make_statement_record(1)
    record["upload_entry"] = {
        "kind": "pdf_pages",
        "summary": "PDF (3 pages, scanned)",
        "images": [{"media_type": "image/png", "data": "not-really-base64"}],
    }
    fake_svc = FakeSvcWithStatements(record)

    import backend.ai.tools.statements as stmt_tool

    monkeypatch.setattr(stmt_tool, "svc", fake_svc)

    res, _ = call_tool("get_statement", statement_id="stmt-abc")
    ue = res["statement"]["upload_entry"]
    assert ue["kind"] == "pdf_pages"
    assert ue["summary"] == "PDF (3 pages, scanned)"
    assert "images" not in ue
    assert "data" not in ue


# ── 5. registry: compact re-serialisation ────────────────────────────────────


def test_registry_compact_json():
    """dispatch compacts JSON with no extra whitespace."""
    from backend.ai.tools.registry import _compact_json

    ugly = '{"a": 1, "b": [1, 2, 3]}'
    compact = _compact_json(ugly)
    assert compact == '{"a":1,"b":[1,2,3]}'
    assert "  " not in compact


def test_registry_compact_non_json_passthrough():
    """Non-JSON text passes through unchanged."""
    from backend.ai.tools.registry import _compact_json

    plain = "This is not JSON at all!"
    assert _compact_json(plain) == plain


def test_registry_compact_unicode_preserved():
    """Non-ASCII characters are preserved as-is (ensure_ascii=False)."""
    from backend.ai.tools.registry import _compact_json

    with_unicode = '{"name": "Nguyễn"}'
    out = _compact_json(with_unicode)
    assert "Nguyễn" in out


def test_registry_compact_applied_to_dispatch(monkeypatch):
    """Actual dispatch output is compact JSON."""
    import backend.ai.tools as tools

    # Use a real registered tool that always returns JSON — list_accounts is simple.
    accts = [_make_account("a1", "Main")]
    fake_svc = FakeSvc(accts, {"a1": 100.0})

    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", fake_svc)

    text, _ = asyncio.run(tools.execute_tool("list_accounts", {}))
    # Compact JSON has no ": " or ", " with trailing spaces
    assert ": " not in text
    assert ", " not in text


def test_registry_dispatch_preserves_proposed_change(monkeypatch):
    """proposed_change tuple slot is still returned correctly after compaction."""
    # propose_position_update is now an ALIAS of update_positions (2026-07-19
    # propose_*/apply_* merge) — it returns (json, change_dict) on a card-capable/attended
    # turn (should_preview_write() True); check the tuple still works.
    from backend.ai.tools import registry as tool_registry
    from backend.services import positions as _ps

    fake_row = {
        "position_id": "pos-1",
        "symbol": "AAPL",
        "ticker": "AAPL",
        "label": "Apple",
        "category": "Vantage Brokerage",
        "quantity": 10,
        "cost_basis": 150.0,
        "bucket": "liquid",
    }
    monkeypatch.setattr(_ps, "hydrated_one", lambda pid: fake_row)
    monkeypatch.setattr(_ps, "validate_position_update", lambda pid, changes: None)
    monkeypatch.setattr(_ps, "find_account", lambda **_: None)
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)

    import backend.ai.tools as tools

    # A pending card is KEYED by the tool_use_id, which a real dispatch always binds.
    text, proposed = asyncio.run(
        tools.execute_tool(
            "update_positions",
            {"position_id": "pos-1", "changes": {"account_name": "Saxo"}},
            tool_use_id="toolu_cost_diet_test",
        )
    )
    assert proposed is not None
    assert proposed["type"] == "update"
    # A staged card PAUSES the tool_use — there is no result text at all, which is the
    # sentinel the turn loop reads (ADR 2026-08-25-one-confirm-mechanism-for-every-tool).
    assert text is None


# ── 6. money totals over the FULL filtered set under pagination ──────────────
# Review finding: page-slice sums must never be reported as filter-set totals.


def test_service_totals_full_set_when_paginated(monkeypatch):
    import backend.services.fx_cache as fx_mod
    import backend.services.ledger as ledger_mod

    txns = [_make_txn(i) for i in range(1, 121)]  # expenses 10.0 .. 1200.0
    for t in txns:
        t["date"] = "2026-06-15"
    monkeypatch.setattr(ledger_mod.transactions, "query_txns", lambda **kw: [dict(t) for t in txns])
    monkeypatch.setattr(ledger_mod, "base_currency", lambda: "SGD")
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: {})

    res = ledger_mod.get_transactions(month=6, year=2026, limit=100)

    assert res["total"] == 120
    assert res["has_more"] is True
    assert len(res["transactions"]) == 100
    expected = round(sum(10.0 * i for i in range(1, 121)), 2)  # 72,600.00 full set
    assert res["total_expenses_base"] == expected
    assert res["total_income_base"] == 0
    assert res["net_base"] == -expected


def test_tool_totals_use_service_full_set(monkeypatch):
    import backend.services.ledger as ledger_mod
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    def fake_get_txns(**kwargs):
        txns = [_make_txn(i) for i in range(1, 121)]
        out = _service_result(txns, kwargs.get("limit"), kwargs.get("offset") or 0)
        out["total_income_base"] = 0.0
        out["total_expenses_base"] = 72600.0
        out["net_base"] = -72600.0
        return out

    monkeypatch.setattr(ledger_mod, "get_transactions", fake_get_txns)
    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)

    data, _ = call_tool("get_transactions", month=6, year=2026)
    s = data["summary"]
    # Page size follows the bounded default rather than a hardcoded 100, so this test
    # keeps testing "totals come from the service" instead of the current limit value.
    assert s["showing"] == _b.DEFAULT_TXN_LIMIT
    assert s["count"] == 120
    assert s["has_more"] is True
    n = _b.DEFAULT_TXN_LIMIT
    page_sum = round(sum(10.0 * i for i in range(1, n + 1)), 2)  # the WRONG number
    assert s["total_expenses_base"] == 72600.0
    assert s["total_expenses_base"] != page_sum


# ── Result bounding (2026-07-19 context lane) ────────────────────────────────
#
# Tool results measured 48% of transcript bytes. These pin the two properties that make
# bounding safe rather than merely smaller:
#   (a) a capped read always REPORTS that it is capped, with the true total;
#   (b) summary_only is EXACT, not an approximation — the service computes its money
#       totals over the full filtered set before paging.


def _service_result_with_totals(txns, limit=None, offset=0):
    """Like _service_result but including the base totals the real service returns.

    The real ledger.get_transactions computes these via _txn_base_totals(txns) over the
    FULL filtered list, and only returns them on the paginated branch.
    """
    total_in = sum(abs(t["base_amount"]) for t in txns if t["kind"] == "income")
    total_out = sum(abs(t["base_amount"]) for t in txns if t["kind"] == "expense")
    totals = {
        "total_income_base": round(total_in, 2),
        "total_expenses_base": round(total_out, 2),
        "net_base": round(total_in - total_out, 2),
    }
    if limit is not None:
        page = txns[offset : offset + limit]
        return {
            "transactions": page,
            "total": len(txns),
            "has_more": offset + len(page) < len(txns),
            **totals,
        }
    return {"transactions": txns}


def _patch_ledger(monkeypatch, txns, captured=None):
    from backend.services import ledger as svc

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    import backend.services.ledger as ledger_mod

    def fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return _service_result_with_totals(txns, kwargs.get("limit"), kwargs.get("offset") or 0)

    monkeypatch.setattr(ledger_mod, "get_transactions", fake)
    # The tool now resolves account filters (an unknown one is an error, never a silent zero);
    # these tests check filter PLUMBING with made-up ids, so every id exists here.
    monkeypatch.setattr(svc.accounts, "find_by_id", lambda aid: {"id": aid})
    import backend.ai.tools.ledger as ledger_tools

    monkeypatch.setattr(ledger_tools, "svc", svc)


def _mixed_book(n=312):
    return [_make_txn(i, kind="income" if i % 3 == 0 else "expense") for i in range(1, n + 1)]


def test_truncated_page_reports_total_and_next_step(monkeypatch):
    """A partial page must announce itself, name the true total, and warn against
    totalling the page. A model that thinks it got everything reports a wrong number."""
    txns = _mixed_book(312)
    _patch_ledger(monkeypatch, txns)

    res, _ = call_tool("get_transactions", month=6, year=2026)
    s = res["summary"]
    assert s["showing"] == 25
    assert s["count"] == 312, "true total must survive the cap"
    assert s["has_more"] is True
    note = s["page_note"]
    assert "312" in note, "the note must state the real total"
    assert "PARTIAL" in note
    # And it must point at concrete narrowing levers, not a vague 'filter more'.
    for lever in ("date_from", "account_id", "offset"):
        assert lever in note


def test_partial_page_refuses_a_ROW_LEVEL_claim_not_just_a_total(monkeypatch):
    """The note must block "nothing unusual here", not only "you spent N".

    Root-caused from a repro (2026-08-16). Asked to "check my last 5 days transactions and flag
    me if anything", the model took ONE 25-of-40 page, never paged, and answered "nothing looks
    fraudulent or duplicated — no repeat merchant/amount pairs". Three duplicate clusters were
    in that window; the 25-row cut ran straight between the halves of two of them, and a third
    pair (rows 19 and 22, same email ref, same S$42.18) was fully on the page and still missed.

    The old note ended "the summary figures below already cover all N" — true of TOTALS and of
    nothing else, but read as general reassurance. A guardrail against over-totalling had become
    permission to under-read. Totals are exact over the whole set; the ROWS are not, and a
    question about rows cannot be answered from a page."""
    txns = _mixed_book(312)
    _patch_ledger(monkeypatch, txns)

    note = call_tool("get_transactions", month=6, year=2026)[0]["summary"]["page_note"]

    assert "287" in note, "it must say how many rows are still UNSEEN, not just the total"
    for row_level_word in ("duplicates", "flag anything"):
        assert row_level_word in note, f"the note must name the row-level case ({row_level_word})"
    assert "has_more is false" in note, "it must name the concrete stopping condition"
    assert "never state that something is absent" in note


def test_summary_only_matches_full_fetch_exactly(monkeypatch):
    """The whole justification for summary_only: identical money figures, no rows.

    If these ever diverge, summary_only is silently lying about totals and must not ship.
    """
    txns = _mixed_book(312)

    _patch_ledger(monkeypatch, txns)
    full, _ = call_tool("get_transactions", month=6, year=2026, limit=1000)

    _patch_ledger(monkeypatch, txns)
    lean, _ = call_tool("get_transactions", month=6, year=2026, summary_only=True)

    for field in ("count", "total_expenses_base", "total_income_base", "net_base", "base_currency"):
        assert lean["summary"][field] == full["summary"][field], f"{field} diverged"

    assert "transactions" not in lean, "summary_only must not ship rows"
    assert lean["summary"]["showing"] == 0
    assert "exact" in lean["summary"]["page_note"]
    # The point of the mode: dramatically smaller.
    assert len(json.dumps(lean)) < len(json.dumps(full)) / 10


def test_summary_only_totals_are_not_page_sums(monkeypatch):
    """Guard against a regression to the tool's page-sum fallback.

    summary_only asks the service for 1 row. If the totals were derived from the returned
    page they would reflect that single row instead of all 312.
    """
    txns = _mixed_book(312)
    captured: dict = {}
    _patch_ledger(monkeypatch, txns, captured)

    res, _ = call_tool("get_transactions", month=6, year=2026, summary_only=True)
    assert captured["limit"] == 1, "summary_only must stay on the paginated totals branch"

    expected_out = sum(abs(t["base_amount"]) for t in txns if t["kind"] == "expense")
    assert res["summary"]["total_expenses_base"] == round(expected_out, 2)
    assert res["summary"]["count"] == 312


def test_unbounded_read_reports_no_truncation(monkeypatch):
    """A complete read must NOT carry a truncation warning — crying wolf is its own bug."""
    txns = _mixed_book(5)
    _patch_ledger(monkeypatch, txns)

    res, _ = call_tool("get_transactions", month=6, year=2026)
    assert res["summary"]["has_more"] is False
    assert "page_note" not in res["summary"]
    assert len(res["transactions"]) == 5


# ── Entity-list FILTERS (owner directive 2026-07-19) ─────────────────────────
#
# A cap returns an arbitrary prefix and can cut off the exact row the caller needed. A
# filter returns the right row. list_accounts was the headline case: 169 calls, 1.23 MB
# total, and it took NO parameters, so every call dumped every account.


def _book(n=6):
    """A small mixed book: assets/liabilities, two currencies, two institutions."""
    rows = []
    for i in range(n):
        a = _make_account(f"a{i}", f"Bank {i}")
        if i % 2:
            a["kind"] = "liability"
            a["type"] = "credit"
        if i % 3 == 0:
            a["currency"] = "USD"
        a["institution"] = "Vantage Brokerage" if i < 2 else "Meridian Bank"
        rows.append(a)
    rows[0]["name"] = "Chase Checking"
    return rows


def _accounts_svc(monkeypatch, rows):
    import backend.ai.tools.accounts as accts_tool

    monkeypatch.setattr(accts_tool, "svc", FakeSvc(rows, {r["id"]: 10.0 for r in rows}))


def test_list_accounts_reports_both_numbers(monkeypatch):
    """Requirement 2: every filtered call states what it returned AND what exists."""
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", query="chase")
    assert res["showing"] == 1
    assert res["total"] == 6
    assert res["accounts"][0]["name"] == "Chase Checking"


def test_list_accounts_query_is_case_insensitive_substring(monkeypatch):
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", query="CHASE")
    assert res["showing"] == 1


def test_list_accounts_query_matches_institution(monkeypatch):
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", query="vantage")
    assert res["showing"] == 2


def test_list_accounts_zero_match_does_not_look_like_empty_book(monkeypatch):
    """Requirement 1, and the most dangerous case in this whole change.

    If a filtered miss reads as an empty database, the model tells the user they have no
    accounts — a confidently wrong answer about their money.
    """
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", query="nonexistent-bank")
    assert res["accounts"] == []
    assert res["showing"] == 0
    assert res["total"] == 6, "the true total must still be reported on a zero match"
    note = res["note"]
    assert "0 of 6" in note
    assert "DO exist" in note
    assert "no accounts" in note.lower(), "must explicitly warn against the wrong conclusion"


def test_list_accounts_genuinely_empty_book_says_so_differently(monkeypatch):
    """An actually-empty book must NOT claim a filter excluded things."""
    _accounts_svc(monkeypatch, [])
    res, _ = call_tool("list_accounts")
    assert res["total"] == 0
    assert "no accounts yet" in res["note"]
    assert "0 of" not in res["note"]


def test_list_accounts_filters_by_kind_and_currency(monkeypatch):
    rows = _book(6)
    _accounts_svc(monkeypatch, rows)
    res, _ = call_tool("list_accounts", kind="liability")
    assert res["showing"] == 3
    assert all(a["kind"] == "liability" for a in res["accounts"])

    _accounts_svc(monkeypatch, rows)
    res, _ = call_tool("list_accounts", currency="usd")  # case-insensitive
    assert res["showing"] == 2


def test_list_accounts_ids_accepts_names_via_shared_resolver(monkeypatch):
    """`ids` resolves through services.accounts.resolve_account_ref — the SAME matcher the
    ledger write choke uses, so a name means one thing across read and write."""
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", ids=["Chase Checking", "a3"])
    assert {a["id"] for a in res["accounts"]} == {"a0", "a3"}


def test_list_accounts_unmatched_ref_is_reported_not_silently_dropped(monkeypatch):
    """A ref that matches nothing must be named. Silently dropping it is indistinguishable
    from 'that account has no data', which sends the model down the wrong path."""
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts", ids=["Chase Checking", "Not A Real Bank"])
    assert res["showing"] == 1
    assert "Not A Real Bank" in res["note"]


def test_list_accounts_ambiguous_name_is_reported(monkeypatch):
    """Two live accounts sharing a name is a real, actionable failure — never a coin flip."""
    rows = _book(4)
    rows[1]["name"] = rows[0]["name"] = "Twin"
    _accounts_svc(monkeypatch, rows)
    res, _ = call_tool("list_accounts", ids=["Twin"])
    assert "ambiguous" in res["note"].lower()


def test_list_accounts_unfiltered_still_returns_everything(monkeypatch):
    """The filters are opt-in — an unfiltered call is unchanged in coverage."""
    _accounts_svc(monkeypatch, _book(6))
    res, _ = call_tool("list_accounts")
    assert res["showing"] == res["total"] == 6


# ── get_portfolio filters ────────────────────────────────────────────────────


def test_get_portfolio_unfiltered_returns_positions_and_summary(monkeypatch, fx_rates):
    """An unfiltered read has nothing to disclose (showing == total) but still gets the
    {positions, showing, total, summary} envelope — no more bare-list special case
    (2026-07-31: a caller summing raw rows silently included unvested equity)."""
    import backend.ai.tools as tools

    btc = _make_position(symbol="BTC", ticker="BTC")
    btc["label"] = "Bitcoin"
    rows = [_make_position(symbol="AAPL"), btc]
    monkeypatch.setattr(tools, "read_rows", lambda: rows)
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio")
    assert res["showing"] == res["total"] == 2
    assert len(res["positions"]) == 2
    assert "note" not in res  # unfiltered: nothing to disclose
    # Both fixture rows are asset_class="equity", quote_currency="USD", bucket="liquid",
    # quantity=10 @ price=180.0 → 1800.0 USD each, converted to the test's base currency.
    assert res["summary"]["unvested_base"] == 0.0
    assert res["summary"]["owned_base"] > 0


def test_get_portfolio_filtered_reports_both_numbers(monkeypatch, fx_rates):
    import backend.ai.tools as tools

    btc = _make_position(symbol="BTC", ticker="BTC")
    btc["label"] = "Bitcoin"
    rows = [_make_position(symbol="AAPL"), btc]
    monkeypatch.setattr(tools, "read_rows", lambda: rows)
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio", query="aapl")
    assert res["showing"] == 1
    assert res["total"] == 2
    assert "not truncated" in res["note"]
    assert "portfolio total" in res["note"], "must warn against totalling a subset"


def test_get_portfolio_zero_match_does_not_look_like_empty_book(monkeypatch):
    import backend.ai.tools as tools

    rows = [_make_position(symbol="AAPL")]
    monkeypatch.setattr(tools, "read_rows", lambda: rows)
    monkeypatch.setattr(tools, "_NAMES_CACHE", {})

    res, _ = call_tool("get_portfolio", query="TSLA")
    assert res["positions"] == []
    assert res["total"] == 1
    assert "DO exist" in res["note"]
    assert "holds nothing" in res["note"]


# ── Plural filters + q_field (owner directive 2026-07-19) ────────────────────
#
# The tool shadowed GET /api/transactions but accepted only SINGULAR filters, so "what did
# I spend across my three cards" was three calls, each re-sending the whole conversation.
# query_txns has always OR'd lists; the service signature just stopped hiding it.


def test_plural_filters_reach_the_service_as_lists(monkeypatch):
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool(
        "get_transactions",
        account_ids=["a1", "a2", "a3"],
        category=["cat_food", "cat_transport"],
        transaction_type=["expense", "income"],
        payee_id=["pay_1", "pay_2"],
        tag=["work", "travel"],
    )
    assert captured["account_ids"] == ["a1", "a2", "a3"]
    assert captured["category"] == ["cat_food", "cat_transport"]
    assert captured["kind"] == ["expense", "income"]
    assert captured["payee_id"] == ["pay_1", "pay_2"]
    assert captured["tag"] == ["work", "travel"]


def test_singular_filters_still_work(monkeypatch):
    """Stored skills and agent playbooks reference the singular names — a silently dropped
    filter returns plausible rows for the WRONG question, which is worse than no filter."""
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool(
        "get_transactions",
        account_id="a1",
        category="cat_food",
        transaction_type="expense",
        payee_id="pay_1",
        tag="work",
    )
    assert captured["account_id"] == "a1"
    assert captured["category"] == "cat_food"
    assert captured["kind"] == "expense"
    assert captured["payee_id"] == "pay_1"
    assert captured["tag"] == "work"


def test_single_element_list_collapses_to_scalar(monkeypatch):
    """A 1-element list is the scalar case — collapsing keeps the kind-as-category
    auto-map and the logging seeing the shape they already understood."""
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool("get_transactions", account_ids=["a1"], category=["cat_food"])
    assert captured["account_ids"] == "a1"
    assert captured["category"] == "cat_food"


def test_empty_list_filter_is_dropped_not_sent_as_empty(monkeypatch):
    """An empty list must mean 'no filter', never 'match nothing'."""
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool("get_transactions", account_ids=[], tag=[], month=6, year=2026)
    assert captured["account_ids"] is None
    assert captured["tag"] is None


def test_account_ids_suppresses_month_defaulting(monkeypatch):
    """account_ids must behave like account_id for the all-time defaulting rule, otherwise
    a multi-card query silently gets clamped to the current month."""
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool("get_transactions", account_ids=["a1", "a2"])
    assert captured["month"] is None
    assert captured["year"] is None


def test_q_field_reaches_the_service(monkeypatch):
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool("get_transactions", q="lunch", q_field="memo")
    assert captured["q"] == "lunch"
    assert captured["q_field"] == "memo"


def test_q_field_defaults_to_any(monkeypatch):
    captured: dict = {}
    _patch_ledger(monkeypatch, _mixed_book(10), captured)

    call_tool("get_transactions", q="uber")
    assert captured["q_field"] == "any"


def test_q_field_separates_description_from_memo():
    """The reason q_field matters: since ADR 2026-07-17 these fields have DIFFERENT
    semantics — description is the bank's matching string, memo is the user's own note."""
    from backend.services.ledger import txn_matches_q

    row = {"description": "STARBUCKS #4412", "memo": "coffee with Dana", "category": ""}

    # description = what the BANK wrote (dedup/rules/payees read this).
    assert txn_matches_q(row, "starbucks", "description", {}, {})
    assert not txn_matches_q(row, "dana", "description", {}, {}), (
        "'description' must not match the user's memo — that makes the two scopes "
        "indistinguishable, which is the whole reason q_field exists"
    )

    # memo = what the USER wrote (nothing else reads it).
    assert txn_matches_q(row, "dana", "memo", {}, {})
    assert not txn_matches_q(row, "starbucks", "memo", {}, {})

    # 'any' spans both; 'note' is the pre-split alias and also spans both (the register's
    # note: token sends it, so it must keep the old meaning).
    for scope in ("any", "note"):
        assert txn_matches_q(row, "starbucks", scope, {}, {})
        assert txn_matches_q(row, "dana", scope, {}, {})


def test_q_field_payee_matches_payee_name_only():
    from backend.services.ledger import txn_matches_q

    row = {"description": "POS 4412", "memo": "", "category": "", "payee_id": "p1"}
    names = {"p1": "Starbucks"}
    assert txn_matches_q(row, "starbucks", "payee", names, {})
    assert not txn_matches_q(row, "4412", "payee", names, {})


# ── get_signals: a capped live feed must still report its denominator ────────


def _signal_store(monkeypatch, tmp_path, db_isolated, n):
    # NotificationLogStore is DB-backed (plan 4d task 2) — db_isolated redirects it; monkeypatch/
    # tmp_path kept for call-site symmetry with other helpers in this file, unused here.
    from backend.db import notification_log_store as ss_mod

    store = ss_mod.NotificationLogStore()
    for i in range(n):
        store.append({"title": f"Signal {i}", "type": "news" if i % 2 else "macro"})
    return store


def test_get_signals_reports_total_and_truncation(monkeypatch, tmp_path, db_isolated):
    """query_signals applies the limit itself, so the returned list cannot say how much it
    left behind — without a separate count the model can't tell 5-of-5 from 5-of-25."""
    _signal_store(monkeypatch, tmp_path, db_isolated, 25)

    res, _ = call_tool("get_notifications", limit=5)
    assert res["showing"] == 5
    assert res["total"] == 25
    assert res["truncated"] is True
    assert "of 25" in res["note"]
    # A live newest-first feed has no stable offset — the note must say how to reach more.
    assert "narrow with" in res["note"].lower()


def test_get_signals_complete_read_reports_no_truncation(monkeypatch, tmp_path, db_isolated):
    _signal_store(monkeypatch, tmp_path, db_isolated, 6)

    res, _ = call_tool("get_notifications", limit=50)
    assert res["showing"] == 6
    assert res["total"] == 6
    assert res["truncated"] is False
    assert "note" not in res


def test_get_signals_total_respects_the_same_filters(monkeypatch, tmp_path, db_isolated):
    """The denominator must be the count for THIS filter, not the whole store — otherwise
    'showing 3 of 25' is a lie when the filter itself only matches 12."""
    _signal_store(monkeypatch, tmp_path, db_isolated, 24)  # 12 news, 12 macro

    res, _ = call_tool("get_notifications", type="news", limit=3)
    assert res["showing"] == 3
    assert res["total"] == 12


# ── get_signals: ticker widened to scalar-or-list (2026-07-19) ───────────────


def _ticker_signal_store(monkeypatch, tmp_path, db_isolated):
    # NotificationLogStore is DB-backed (plan 4d task 2) — db_isolated redirects it; monkeypatch/
    # tmp_path kept for call-site symmetry with other helpers in this file, unused here.
    from backend.db import notification_log_store as ss_mod

    store = ss_mod.NotificationLogStore()
    store.append({"title": "AAPL news", "affected_tickers": ["AAPL"]})
    store.append({"title": "MSFT news", "affected_tickers": ["MSFT"]})
    store.append({"title": "TSLA news", "affected_tickers": ["TSLA"]})
    store.append({"title": "No ticker news", "affected_tickers": []})
    return store


def test_get_signals_ticker_single_value_unchanged(monkeypatch, tmp_path, db_isolated):
    """Also exercises the pre-2026-07-19d singular ``ticker`` key — must keep resolving
    as a read-side fallback (see watchlist.py's module docstring param-arity note)."""
    _ticker_signal_store(monkeypatch, tmp_path, db_isolated)
    res, _ = call_tool("get_notifications", ticker="aapl")
    assert res["showing"] == 1
    assert res["signals"][0]["title"] == "AAPL news"


def test_get_signals_ticker_list_matches_any(monkeypatch, tmp_path, db_isolated):
    """New batch capability: ticker accepts a LIST — an OR filter across several subjects
    in one call instead of one get_signals call per ticker."""
    _ticker_signal_store(monkeypatch, tmp_path, db_isolated)
    res, proposed = call_tool("get_notifications", ticker=["aapl", "tsla"])
    assert proposed is None
    assert res["total"] == 2
    titles = {s["title"] for s in res["signals"]}
    assert titles == {"AAPL news", "TSLA news"}


def test_get_signals_ticker_list_respects_limit_and_reports_truncation(
    monkeypatch, tmp_path, db_isolated
):
    _ticker_signal_store(monkeypatch, tmp_path, db_isolated)
    res, _ = call_tool("get_notifications", ticker=["aapl", "msft", "tsla"], limit=2)
    assert res["showing"] == 2
    assert res["total"] == 3
    assert res["truncated"] is True


# ── get_signals: ticker renamed tickers (2026-07-19d param-arity fixup) ──────


def test_get_signals_tickers_single_value_via_plural_key(monkeypatch, tmp_path, db_isolated):
    _ticker_signal_store(monkeypatch, tmp_path, db_isolated)
    res, _ = call_tool("get_notifications", tickers="aapl")
    assert res["showing"] == 1
    assert res["signals"][0]["title"] == "AAPL news"


def test_get_signals_tickers_list_via_plural_key(monkeypatch, tmp_path, db_isolated):
    _ticker_signal_store(monkeypatch, tmp_path, db_isolated)
    res, proposed = call_tool("get_notifications", tickers=["aapl", "tsla"])
    assert proposed is None
    assert res["total"] == 2
    titles = {s["title"] for s in res["signals"]}
    assert titles == {"AAPL news", "TSLA news"}


# ── mark_signals_read: signal_id widened to scalar-or-list (2026-07-19) ──────


def test_mark_signals_read_single_id_unchanged(monkeypatch, tmp_path, db_isolated):
    """Also exercises the pre-2026-07-19d singular ``signal_id`` key — must keep
    resolving as a read-side fallback (see watchlist.py's module docstring param-arity
    note)."""
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 2)
    sid = store.query_signals(limit=10)[0]["id"]
    res, proposed = call_tool("mark_notifications_read", signal_id=sid)
    assert proposed is None
    assert res == {"ok": True, "signal_id": sid}
    assert next(s for s in store.query_signals(limit=10) if s["id"] == sid)["read"] is True


def test_mark_signals_read_omitted_marks_all(monkeypatch, tmp_path, db_isolated):
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 3)
    res, proposed = call_tool("mark_notifications_read")
    assert proposed is None
    assert res == {"ok": True, "marked_all_read": True}
    assert all(s["read"] for s in store.query_signals(limit=10))


def test_mark_signals_read_list_dismisses_only_those(monkeypatch, tmp_path, db_isolated):
    """New batch capability: signal_id accepts a LIST — dismiss several specific signals
    without marking the WHOLE inbox read."""
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 3)
    rows = store.query_signals(limit=10)
    ids = [rows[0]["id"], rows[1]["id"]]
    res, proposed = call_tool("mark_notifications_read", signal_id=ids)
    assert proposed is None
    assert res["ok"] is True
    assert res["marked"] == 2
    by_id = {s["id"]: s for s in store.query_signals(limit=10)}
    assert by_id[rows[0]["id"]]["read"] is True
    assert by_id[rows[1]["id"]]["read"] is True
    assert by_id[rows[2]["id"]]["read"] is False  # not in the list — untouched


def test_mark_signals_read_list_one_bad_id_does_not_block_the_rest(
    monkeypatch, tmp_path, db_isolated
):
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 1)
    sid = store.query_signals(limit=10)[0]["id"]
    res, proposed = call_tool("mark_notifications_read", signal_id=[sid, "ghost"])
    assert proposed is None
    assert res["ok"] is False  # "ghost" didn't match a real signal
    assert res["marked"] == 1
    assert {r["signal_id"]: r["ok"] for r in res["results"]} == {sid: True, "ghost": False}


# ── mark_signals_read: signal_id renamed signal_ids (2026-07-19d param-arity fixup) ──


def test_mark_signals_read_single_id_via_plural_key(monkeypatch, tmp_path, db_isolated):
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 2)
    sid = store.query_signals(limit=10)[0]["id"]
    res, proposed = call_tool("mark_notifications_read", signal_ids=sid)
    assert proposed is None
    assert res == {"ok": True, "signal_id": sid}
    assert next(s for s in store.query_signals(limit=10) if s["id"] == sid)["read"] is True


def test_mark_signals_read_list_via_plural_key(monkeypatch, tmp_path, db_isolated):
    store = _signal_store(monkeypatch, tmp_path, db_isolated, 3)
    rows = store.query_signals(limit=10)
    ids = [rows[0]["id"], rows[1]["id"]]
    res, proposed = call_tool("mark_notifications_read", signal_ids=ids)
    assert proposed is None
    assert res["ok"] is True
    assert res["marked"] == 2
    by_id = {s["id"]: s for s in store.query_signals(limit=10)}
    assert by_id[rows[0]["id"]]["read"] is True
    assert by_id[rows[1]["id"]]["read"] is True
    assert by_id[rows[2]["id"]]["read"] is False  # not in the list — untouched
