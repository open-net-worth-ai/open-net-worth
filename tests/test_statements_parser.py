"""Golden-fixture suite for the unified statement parser (services/statements, RFC
statement-import-unified P1). LLM stubbed per stage via the seam `source` key — deterministic,
offline. Fixtures are synthesized equivalents of real-world shapes validated in the field:
a PocketPay-style wallet export (status column + txn-id + signed amounts), a Riverside Bank-style
bilingual sheet (metadata preamble + debit/credit + running balance, DMY), EU-locale amounts,
ambiguous dates, scanned-page vision with a repeated boundary row, and the reconcile identity."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core import llm_seam
from backend.services.statements import guards, tiers
from backend.services.statements.canonical import CanonicalRow, StatementMeta
from backend.services.statements.parser import parse_statement, to_upload
from backend.services.statements.tiers import StaleSchemaError, StatementParseError


@pytest.fixture(autouse=True)
def _clear_seam(monkeypatch):
    monkeypatch.setattr(llm_seam, "_complete", None)
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    yield


def _stub(monkeypatch, by_source: dict, counter: list | None = None):
    """Route stub replies by the seam call's `source` (csv_schema / statement_parse /
    statement_metadata / statement_vision). Counts calls into `counter` when given."""

    async def _complete(prompt, **kwargs):
        if counter is not None:
            counter.append(kwargs.get("source"))
        reply = by_source.get(kwargs.get("source"))
        if reply is None:
            raise AssertionError(f"unexpected LLM call source={kwargs.get('source')}")
        return reply if isinstance(reply, str) else json.dumps(reply)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


# ── PocketPay-style wallet export: status col + txn id + signed amount + balance ─────

_MOMO_CSV = (
    "STT,Thời gian,Mã giao dịch,Loại giao dịch,Số Tiền,Số Dư,Trạng Thái GD\n"
    "1,30/04/2026 23:43:07,900000000001,Thanh toán GRAB,-63000,99,Thành công\n"
    "2,30/04/2026 23:38:19,900000000002,Thanh toán Example Ride,-62000,62099,Thất bại\n"
    "3,30/04/2026 22:35:43,900000000003,Nạp tiền vào Ví,66000,66099,Thành công\n"
)

_MOMO_SCHEMA = {
    "header_row": 0,
    "date_col": 1,
    "debit_col": 4,
    "credit_col": 4,
    "signed": True,
    "desc_col": 3,
    "payee_col": None,
    "id_col": 2,
    "balance_col": 5,
    "status_col": 6,
    "success_values": ["Thành công"],
    "currency": "VND",
    "date_order": "DMY",
}


def test_momo_schema_inference_skips_failed_rows_and_keeps_ids(monkeypatch):
    calls: list = []
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA}, calls)
    result = asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    assert result.tier == "tabular-schema" and result.llm_calls == 1
    assert calls == ["csv_schema"]  # ONE call, regardless of row count
    live = [r for r in result.rows if not r.skip]
    skipped = [r for r in result.rows if r.skip]
    assert len(live) == 2 and len(skipped) == 1
    assert "Thất bại" in (skipped[0].skip_reason or "")
    grab = next(r for r in live if "GRAB" in r.description)
    assert grab.direction == "OUT" and grab.amount == 63000
    assert grab.reference == "900000000001"
    topup = next(r for r in live if "Nạp tiền" in r.description)
    assert topup.direction == "IN" and topup.amount == 66000
    assert grab.date == "2026-04-30"


def test_momo_with_saved_schema_is_zero_llm(monkeypatch):
    calls: list = []
    _stub(monkeypatch, {}, calls)  # ANY seam call would raise
    result = asyncio.run(
        parse_statement(
            {"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}, schema=_MOMO_SCHEMA
        )
    )
    assert result.tier == "tabular-profile" and result.llm_calls == 0
    assert calls == []
    assert len([r for r in result.rows if not r.skip]) == 2


def test_stale_schema_raises_for_relearn(monkeypatch):
    _stub(monkeypatch, {})
    wrong = dict(_MOMO_SCHEMA, date_col=0)  # bank "changed" its layout → dates don't parse
    with pytest.raises(StaleSchemaError):
        asyncio.run(
            parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}, schema=wrong)
        )


# ── Riverside Bank-style: bilingual preamble + debit/credit + running balance ─────────

_VCB_CSV = (
    "Chủ tài khoản/ Account holder,NGUYEN VAN A,,\n"
    "Số tài khoản/ Account number,0451000541234,,\n"  # pii-lint: ok (synthetic)
    ",,,\n"
    "Ngày/ Date,Số tiền ghi nợ/ Debit,Số tiền ghi có/ Credit,Số dư/ Balance\n"
    '02/05/2026,"1.000.000",,"9.000.000"\n'
    '03/05/2026,,"2.000.000","11.000.000"\n'
    '04/05/2026,"500.000",,"10.500.000"\n'
)

_VCB_SCHEMA = {
    "header_row": 3,
    "date_col": 0,
    "debit_col": 1,
    "credit_col": 2,
    "signed": False,
    "desc_col": None,
    "payee_col": None,
    "id_col": None,
    "balance_col": 3,
    "status_col": None,
    "success_values": [],
    "currency": "VND",
    "date_order": "DMY",
}


def test_vcb_preamble_vn_thousands_and_balance_rederive(monkeypatch):
    _stub(monkeypatch, {"csv_schema": _VCB_SCHEMA})
    result = asyncio.run(parse_statement({"kind": "text", "text": _VCB_CSV, "shape": "tabular"}))
    rows = [r for r in result.rows if not r.skip]
    assert [r.amount for r in rows] == [1000000, 2000000, 500000]
    assert [r.direction for r in rows] == ["OUT", "IN", "OUT"]
    assert rows[0].balance == 9000000  # VN '.'-thousands parsed correctly


def test_balance_column_corrects_a_flipped_direction(monkeypatch):
    # Same sheet, but the schema mis-reads column roles so row 2's money lands in the DEBIT
    # column (direction OUT) while the running balance PROVES it came IN. The guard fixes it.
    swapped = (
        "Ngày/ Date,Số tiền ghi nợ/ Debit,Số tiền ghi có/ Credit,Số dư/ Balance\n"
        '02/05/2026,"1.000.000",,"9.000.000"\n'
        '03/05/2026,"2.000.000",,"11.000.000"\n'  # balance ROSE — this was a credit
    )
    schema = dict(_VCB_SCHEMA, header_row=0)
    _stub(monkeypatch, {"csv_schema": schema})
    result = asyncio.run(parse_statement({"kind": "text", "text": swapped, "shape": "tabular"}))
    meta = result.meta
    meta.opening_balance = 10000000.0
    rows = [r for r in result.rows if not r.skip]
    guards.rederive_from_balance(rows, meta)
    assert rows[1].direction == "IN"
    assert any("corrected from running balance" in n for n in rows[1].notes)


def test_balance_rederive_walks_a_newest_first_statement_chronologically():
    """Regression: a PocketPay wallet export lists
    NEWEST-first (unlike Riverside Bank's oldest-first). `rederive_from_balance` used to always pair
    each row with the row ABOVE it — the wrong neighbor on a newest-first file, since that
    row is chronologically LATER, not earlier — so the computed delta was actually the
    PRECEDING row's own amount. On the real file this false-flagged 89 of 92 rows as
    "running-balance delta disagrees with amount", defaulting every one of them to Skip.
    Three genuinely consistent rows, newest-first, no opening balance: chronologically
    +500 (01/05, bal 500) → +1000 (02/05, bal 1500) → -500 (03/05, bal 1000)."""
    rows = [
        CanonicalRow(date="2026-05-03", direction="OUT", amount=500.0, balance=1000.0),
        CanonicalRow(date="2026-05-02", direction="IN", amount=1000.0, balance=1500.0),
        CanonicalRow(date="2026-05-01", direction="IN", amount=500.0, balance=500.0),
    ]
    meta = StatementMeta()
    corrections = guards.rederive_from_balance(rows, meta)
    assert corrections == 0
    assert all(not r.notes for r in rows), [r.notes for r in rows]


def test_balance_rederive_newest_first_still_catches_a_real_mismatch():
    """The direction-detection fix must not make the guard toothless — a genuine amount
    error on a newest-first file still gets flagged, just correctly attributed to the ONE
    row that's actually wrong (not every row after it)."""
    rows = [
        CanonicalRow(date="2026-05-03", direction="OUT", amount=500.0, balance=1000.0),
        # Wrong: the balance jump from 500->1500 is +1000, but this row claims 700.
        CanonicalRow(date="2026-05-02", direction="IN", amount=700.0, balance=1500.0),
        CanonicalRow(date="2026-05-01", direction="IN", amount=500.0, balance=500.0),
    ]
    meta = StatementMeta()
    guards.rederive_from_balance(rows, meta)
    assert rows[0].notes == []
    assert any("doesn't confirm this amount" in n for n in rows[1].notes)
    assert rows[2].notes == []


def test_balance_rederive_exempts_a_pass_through_row_with_zero_delta():
    """Regression: after the ordering fix, 13 of
    a real PocketPay statement's rows were still falsely flagged — the wallet sometimes routes a
    payment directly bank->destination without the float ever moving (a "pass-through"), so
    consecutive rows can share the EXACT same balance despite each carrying a real nonzero
    amount. A delta of exactly zero is unambiguous — this row simply isn't verifiable against
    the balance column, never a math error — and this is a general, source-agnostic numeric
    rule (no PocketPay/language-specific check): chronologically 05-01 IN 500 (balance 500), then
    a 05-02 pass-through OUT 300 that leaves the balance at 500 (unchanged), then a real 05-03
    OUT 500 that drops it to 0 — the middle row must be exempted despite its real amount."""
    rows = [
        CanonicalRow(date="2026-05-03", direction="OUT", amount=500.0, balance=0.0),
        # Pass-through: balance unchanged (500 -> 500) despite a real 300 OUT.
        CanonicalRow(date="2026-05-02", direction="OUT", amount=300.0, balance=500.0),
        CanonicalRow(date="2026-05-01", direction="IN", amount=500.0, balance=500.0),
    ]
    meta = StatementMeta()
    corrections = guards.rederive_from_balance(rows, meta)
    assert corrections == 0
    assert all(not r.notes for r in rows), [r.notes for r in rows]


# ── locale fixtures ───────────────────────────────────────────────────────────────────

_EU_CSV = "Date;Amount;Description\n21.05.2026;-1.234,56;Miete\n22.05.2026;99,10;Gehalt\n"


def test_eu_locale_decimal_comma(monkeypatch):
    schema = {
        "header_row": 0,
        "date_col": 0,
        "debit_col": 1,
        "credit_col": 1,
        "signed": True,
        "desc_col": 2,
        "currency": "EUR",
        "date_order": "DMY",
    }
    grid = [row.split(";") for row in _EU_CSV.strip().split("\n")]
    rows = tiers.apply_schema(grid, schema, StatementMeta())
    assert rows[0].amount == 1234.56 and rows[0].direction == "OUT"
    assert rows[1].amount == 99.10 and rows[1].direction == "IN"


def test_excel_datetime_cells_parse():
    schema = {
        "header_row": 0,
        "date_col": 0,
        "debit_col": 1,
        "credit_col": 1,
        "signed": True,
        "desc_col": 2,
        "date_order": None,
    }
    grid = [
        ["Date", "Amount", "Desc"],
        ["2026-05-21 00:00:00", "-4.50", "Coffee"],  # openpyxl datetime str form
    ]
    rows = tiers.apply_schema(grid, schema, StatementMeta())
    assert rows[0].date == "2026-05-21"


def test_ambiguous_dates_raise_actionable_never_guess():
    schema = {
        "header_row": 0,
        "date_col": 0,
        "debit_col": 1,
        "credit_col": 1,
        "signed": True,
        "desc_col": 2,
        "date_order": None,  # no LLM vote, all days ≤ 12
    }
    grid = [["Date", "Amount", "Desc"], ["03/04/2026", "-5.00", "x"], ["05/06/2026", "-6.00", "y"]]
    with pytest.raises(StatementParseError, match="ambiguous"):
        tiers.apply_schema(grid, schema, StatementMeta())


def test_llm_date_order_vote_breaks_ambiguity_with_warning():
    schema = {
        "header_row": 0,
        "date_col": 0,
        "debit_col": 1,
        "credit_col": 1,
        "signed": True,
        "desc_col": 2,
        "date_order": "DMY",  # the model judged locale from the language
    }
    grid = [["Date", "Amount", "Desc"], ["03/04/2026", "-5.00", "x"]]
    meta = StatementMeta()
    rows = tiers.apply_schema(grid, schema, meta)
    assert rows[0].date == "2026-04-03"
    assert any("inferred from document language" in w for w in meta.warnings)


# ── raw column passthrough ("why can't the parser parse all columns") ───────────────────


def test_apply_schema_captures_every_column_not_just_the_mapped_roles():
    """A two-sided export (like PocketPay's) prints separate from/to ACCOUNT NUMBER columns
    alongside their display-name columns — the schema's fixed role vocabulary has no slot
    for account numbers at all, so only ``raw`` keeps them reachable."""
    schema = {
        "header_row": 0,
        "date_col": 0,
        "debit_col": 4,
        "credit_col": 4,
        "signed": True,
        "desc_col": 1,
        "payee_col": 3,
    }
    grid = [
        ["Date", "Type", "To Account No", "To Name", "Amount"],
        ["2026-07-01", "Ecom", "0191234567", "GRAB", "-99000"],
    ]
    rows = tiers.apply_schema(grid, schema, StatementMeta())
    assert rows[0].payee == "GRAB"
    assert rows[0].raw == {
        "Date": "2026-07-01",
        "Type": "Ecom",
        "To Account No": "0191234567",
        "To Name": "GRAB",
        "Amount": "-99000",
    }


def test_raw_row_dedupes_blank_and_duplicate_header_labels():
    header = ["Date", "", "Amount", "Amount"]
    row = ["2026-07-01", "unlabeled cell", "-99000", "-99000"]
    assert tiers._raw_row(header, row) == {
        "Date": "2026-07-01",
        "col_1": "unlabeled cell",
        "Amount": "-99000",
        "Amount_1": "-99000",
    }


def test_to_canonical_carries_the_models_own_raw_object():
    """``to_canonical`` is shared by tier2_text (regular PDFs/CSVs the LLM row-extracts) AND
    tier3_vision (scanned/photographed statements) — this is the ONE place either tier's
    extra-column capture lands, so covering it here covers both."""
    row = tiers.to_canonical(
        {
            "date": "2026-07-01",
            "amount": 99000,
            "direction": "OUT",
            "description": "Ecom top up",
            "payee": "GRAB",
            "raw": {"To Account No": "0191234567", "Channel": "Ecom"},
        }
    )
    assert row.raw == {"To Account No": "0191234567", "Channel": "Ecom"}


def test_to_canonical_raw_is_defensively_coerced_never_trusted_raw():
    """Model output riding into a LATER prompt (Lucius's pass) — never a bare dict, never
    unbounded: non-string keys/values coerce to str, blanks drop, everything truncates."""
    row = tiers.to_canonical(
        {
            "date": "2026-07-01",
            "amount": 5,
            "direction": "OUT",
            "raw": {
                "acct": 12345,  # non-string value
                "": "dropped — blank key",
                "blank_value": "",
                "long": "x" * 500,
                **{f"k{i}": "v" for i in range(20)},  # over the entry cap
            },
        }
    )
    assert row.raw["acct"] == "12345"
    assert "" not in row.raw
    assert "blank_value" not in row.raw
    assert len(row.raw["long"]) == tiers._MAX_RAW_VALUE
    assert len(row.raw) <= tiers._MAX_RAW_ENTRIES


def test_to_canonical_raw_defaults_to_empty_when_absent_or_malformed():
    assert tiers.to_canonical({"date": "2026-07-01", "amount": 5, "direction": "OUT"}).raw == {}
    assert (
        tiers.to_canonical(
            {"date": "2026-07-01", "amount": 5, "direction": "OUT", "raw": "not a dict"}
        ).raw
        == {}
    )


# ── reconciliation identity ───────────────────────────────────────────────────────────


def _row(amount, direction, ccy="SGD"):
    return CanonicalRow(date="2026-05-01", direction=direction, amount=amount, currency=ccy)


def test_reconcile_ok_and_off_and_flipped():
    meta = StatementMeta(currency="SGD", opening_balance=100.0, closing_balance=40.0)
    ok = guards.reconcile([_row(70, "OUT"), _row(10, "IN")], meta)
    assert ok["status"] == "ok"
    off = guards.reconcile([_row(70, "OUT"), _row(50, "IN")], meta)
    assert off["status"] == "off" and abs(off["delta"] + 40.0) < 0.01
    flipped = guards.reconcile([_row(70, "IN"), _row(10, "OUT")], meta)
    assert flipped["status"] == "ok_if_flipped"


def test_reconcile_card_convention_and_per_currency_groups():
    # Card: charges (OUT) grow the owed balance.
    card = StatementMeta(
        currency="SGD", opening_balance=1000.00, closing_balance=4000.00, sign_convention="card"
    )
    rows = [_row(4000.00, "OUT"), _row(1000.00, "IN")]
    assert guards.reconcile(rows, card)["status"] == "ok"
    # A foreign-currency row on the same statement must not poison the identity.
    rows_multi = rows + [_row(1000000, "OUT", ccy="VND")]
    assert guards.reconcile(rows_multi, card)["status"] == "ok"


def test_span_verify_flags_fabricated_amounts():
    rows = [_row(60.36, "OUT"), _row(999999.99, "OUT")]
    flagged = guards.span_verify(rows, "MPOS*CINEBAR 60.36 Ref 74720746142802720528652")
    assert flagged == 1
    assert rows[0].confidence == 1.0
    assert rows[1].confidence <= 0.4
    assert any("not found in source text" in n for n in rows[1].notes)


# ── vision tier ───────────────────────────────────────────────────────────────────────

_PAGE1_REPLY = {
    "meta": {
        "issuer": "Meridian Bank",
        "currency": "SGD",
        "sign_convention": "card",
        "opening_balance": 1000.00,
        "closing_balance": 4000.00,
        "payment_due_date": "2026-07-10",
        "amount_due": 4000.00,
        "account_last4s": ["1111"],
        "rewards": {"balance": 4019, "unit": "points"},
    },
    "transactions": [
        {
            "date": "2026-05-21",
            "amount": 60.36,
            "direction": "OUT",
            "payee": "Classychotel",
            "description": "MPOS*EXAMPLEHOTEL",
            "confidence": 0.9,
        },
        {
            "date": "2026-05-22",
            "amount": 58.86,
            "direction": "OUT",
            "payee": "Cong Ty In Hotel",
            "description": "EXAMPLE PRINT CO",
            "confidence": 0.9,
        },
    ],
}
_PAGE2_REPLY = [  # first row looks identical to page 1's last row — a genuine repeat, not overlap
    {
        "date": "2026-05-22",
        "amount": 58.86,
        "direction": "OUT",
        "payee": "Cong Ty In Hotel",
        "description": "EXAMPLE PRINT CO",
        "confidence": 0.9,
    },
    {
        "date": "2026-05-23",
        "amount": 63.90,
        "direction": "OUT",
        "payee": "Crystal Jade",
        "description": "CRYSTAL JADE LND",
        "confidence": 0.9,
    },
]


def test_vision_pages_meta_and_images_kwarg_never_drops_a_boundary_row(monkeypatch):
    # Pin image-input capability: since ADR 2026-07-26 tier3_vision refuses up front when
    # the statement_vision model is known blind, which resolves the instance default and so
    # depends on whatever model config an earlier test in the run left behind (this passed
    # alone and failed in the full suite). The subject here is page flattening and the images
    # kwarg — the refusal has its own coverage in test_vision_fallback_chat.py.
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: True)
    seen_images: list = []
    replies = [json.dumps(_PAGE1_REPLY), json.dumps(_PAGE2_REPLY)]

    async def _complete(prompt, **kwargs):
        assert kwargs.get("images"), "vision call must carry page images"
        seen_images.append(len(kwargs["images"]))
        return replies[len(seen_images) - 1]

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    result = asyncio.run(parse_statement({"kind": "pdf_pages", "images": pages}))
    assert result.tier == "vision" and result.llm_calls == 2
    assert seen_images == [1, 1]  # one page per call
    assert result.meta.issuer == "Meridian Bank" and result.meta.payment_due_date == "2026-07-10"
    assert result.meta.rewards == {"balance": 4019, "unit": "points"}
    assert len(result.rows) == 4  # every page's rows kept — no boundary dedup, ever
    assert all(r.confidence <= 0.95 for r in result.rows)  # vision never claims certainty
    assert result.reconcile is not None


def test_vision_page_cap_is_a_loud_cost_ceiling(monkeypatch):
    _stub(monkeypatch, {})
    pages = [{"media_type": "image/png", "data": "x"}] * 41
    with pytest.raises(StatementParseError, match="page"):
        asyncio.run(parse_statement({"kind": "pdf_pages", "images": pages}))


# ── bytes routing ─────────────────────────────────────────────────────────────────────


def test_textless_pdf_routes_to_vision_pages():
    import fitz

    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    upload = to_upload(data, "pdf")
    assert upload["kind"] == "pdf_pages"
    assert len(upload["images"]) == 1
    assert upload["images"][0]["media_type"] == "image/png"


def test_scanned_pdf_reports_render_once_per_page_not_once_per_render_call():
    """Regression (live QA): a 3-page scan used to grow THREE "render" stage entries — one
    per rendered page — even though it's one logical stage. `parse_progress.report`'s own
    same-stage-updates-in-place rule (mirrored by `read`) means this now stays ONE entry,
    its page/pages counters updated as each page renders."""
    import fitz

    from backend.services.statements import parse_progress

    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    data = doc.tobytes()
    doc.close()

    with parse_progress.track("tok-render-pages-0001"):
        to_upload(data, "pdf")
        snap = parse_progress.snapshot("tok-render-pages-0001")

    render_entries = [s for s in snap["stages"] if s["stage"] == "render"]
    assert len(render_entries) == 1
    assert render_entries[0]["page"] == 3
    assert render_entries[0]["pages"] == 3
    assert render_entries[0]["label"] == "Rendering page 3 of 3"


def test_report_render_false_never_touches_the_progress_channel():
    """`report_render=False` (live QA follow-up: `profiles._document_fingerprint`'s own
    re-render of a scanned PDF, purely to hash an identity, must not touch the shared
    progress channel at all — see `to_upload`'s own docstring)."""
    import fitz

    from backend.services.statements import parse_progress

    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()

    with parse_progress.track("tok-report-render-false-0001"):
        to_upload(data, "pdf", layout=False, report_render=False)
        snap = parse_progress.snapshot("tok-report-render-false-0001")

    assert snap["stages"] == []


def test_document_fingerprint_reuses_a_scanned_pdfs_render_without_reopening_the_stage():
    """Regression (live QA): `profiles.ensure_source_profile` calls `_document_fingerprint`
    right after the main parse's own `render`/`reconcile` stages have already run — its
    `layout=False` re-read falls through to the SAME image-rendering branch for a scanned
    PDF, which used to report a SECOND `render` entry after `reconcile`, stepping the UI's
    stage bar backwards. A full preview must show each stage at most once, in the order it
    actually ran."""
    import fitz

    from backend.services.statements import parse_progress

    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()

    with parse_progress.track("tok-doc-fingerprint-0001"):
        to_upload(data, "pdf")  # the main parse's own render
        parse_progress.report("reconcile")  # later stages ran...
        from backend.services.statements.profiles import ensure_source_profile

        ensure_source_profile(data, "pdf")  # ...then this fires, same file, same fallback
        snap = parse_progress.snapshot("tok-doc-fingerprint-0001")

    stages = [s["stage"] for s in snap["stages"]]
    assert stages == ["render", "reconcile"]  # not ["render", "reconcile", "render"]
    assert len(stages) == len(set(stages))  # each stage at most once


def test_xlsx_bytes_route_tabular(monkeypatch):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Amount", "Desc"])
    ws.append(["2026-05-21", -4.5, "Coffee"])
    buf = io.BytesIO()
    wb.save(buf)
    upload = to_upload(buf.getvalue(), "xlsx")
    assert upload["kind"] == "text" and upload["shape"] == "tabular"
    assert "Coffee" in upload["text"]


class _FakeEncryptedOffice:
    """Stands in for ``msoffcrypto.OfficeFile`` at the ``services.office_crypt`` seam — a
    real password-protected xlsx is an OLE/CFB container msoffcrypto offers no way to author
    in a test, so the encryption/decryption boundary is faked rather than the reader."""

    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return True

    def load_key(self, password):
        if password != "secret":
            raise ValueError("bad key")

    def decrypt(self, out):
        import io as _io

        import openpyxl as _openpyxl

        wb = _openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Amount", "Desc"])
        ws.append(["2026-05-21", -4.5, "Coffee"])
        buf = _io.BytesIO()
        wb.save(buf)
        out.write(buf.getvalue())


def test_encrypted_xlsx_without_password_raises_password_required_not_zipfile_error(
    monkeypatch,
):
    """Was: openpyxl's raw ``BadZipFile`` wrapped verbatim ("Not a readable Excel (.xlsx)
    file: File is not a zip file") — no hint it's a password problem."""
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    with pytest.raises(StatementParseError) as exc:
        to_upload(b"encrypted-bytes-no-password", "xlsx")
    assert str(exc.value).startswith("password_required:")


def test_encrypted_xlsx_wrong_password_raises_a_distinct_error(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    with pytest.raises(StatementParseError) as exc:
        to_upload(b"encrypted-bytes", "xlsx", password="wrong")
    assert "Wrong password" in str(exc.value)
    assert not str(exc.value).startswith("password_required:")


def test_encrypted_xlsx_correct_password_decrypts_and_routes_tabular(monkeypatch):
    from backend.services import office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    upload = to_upload(b"encrypted-bytes", "xlsx", password="secret")
    assert upload["kind"] == "text" and upload["shape"] == "tabular"
    assert "Coffee" in upload["text"]


def test_text_tier_meta_failure_is_nonfatal(monkeypatch):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            raise RuntimeError("meta model down")
        return json.dumps(
            [
                {
                    "date": "2026-01-02",
                    "amount": 4.5,
                    "direction": "OUT",
                    "description": "Coffee 4.50",
                }
            ]
        )

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    result = asyncio.run(parse_statement({"kind": "text", "text": "Coffee 4.50 on 2026-01-02"}))
    assert len(result.rows) == 1  # rows still parsed
    assert result.reconcile["status"] == "unknown"
    assert any("metadata unavailable" in w for w in result.meta.warnings)


# ── format memory (P2.1): learn once, repeat free, drift re-learns, forgettable ───────


@pytest.fixture()
def _profile_store(db_isolated):
    # StatementFormatStore is DB-backed now (plan 4d task 2) — db_isolated redirects the
    # process-wide singleton to a fresh, empty database; no more per-instance module-JSON-
    # constant isolation.
    import backend.db.statement_format_store as fps

    return fps.StatementFormatStore


def test_first_import_learns_profile_then_repeat_is_zero_llm(_profile_store, monkeypatch):
    calls: list = []
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA}, calls)
    first = asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    assert first.llm_calls == 1 and first.meta.extra["format_profile"]["learned"] is True
    profiles = _profile_store().all_profiles()
    assert len(profiles) == 1 and profiles[0]["origin"] == "inferred"

    _stub(monkeypatch, {}, calls)  # ANY further seam call would raise
    second = asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    assert second.llm_calls == 0  # THE promise: a known source costs nothing
    assert second.meta.extra["format_profile"]["id"] == profiles[0]["id"]
    assert _profile_store().all_profiles()[0]["uses"] == 1


def test_format_drift_relearns_and_version_bumps(_profile_store, monkeypatch):
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA})
    asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    prof = _profile_store().all_profiles()[0]
    assert prof["version"] == 1

    # The "bank" reshuffles columns: same header text (same fingerprint), data moved — the
    # saved schema fails sample-validation → re-infer with the NEW schema → version 2.
    drifted_csv = (
        "STT,Thời gian,Mã giao dịch,Loại giao dịch,Số Tiền,Số Dư,Trạng Thái GD\n"
        "xx,yy,900000000001,Thanh toán GRAB,zz,aa,Thành công\n"  # date col now junk
    )
    new_schema = dict(_MOMO_SCHEMA, date_col=2)  # pretend the model re-reads it differently
    # craft data where date_col=2 parses (ids are not dates → use a dateable col)
    drifted_csv = (
        "STT,Thời gian,Mã giao dịch,Loại giao dịch,Số Tiền,Số Dư,Trạng Thái GD\n"
        "1,junk,30/04/2026,Thanh toán GRAB,-63000,99,Thành công\n"
    )
    _stub(monkeypatch, {"csv_schema": new_schema})
    result = asyncio.run(parse_statement({"kind": "text", "text": drifted_csv, "shape": "tabular"}))
    assert any("re-learned" in w for w in result.meta.warnings)
    prof2 = _profile_store().all_profiles()[0]
    assert prof2["version"] == 2 and prof2["id"] == prof["id"]


def test_forgotten_profile_reinfers(_profile_store, monkeypatch):
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA})
    asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    store = _profile_store()
    pid = store.all_profiles()[0]["id"]
    assert store.delete(pid) is True

    calls: list = []
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA}, calls)
    result = asyncio.run(parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}))
    assert calls == ["csv_schema"] and result.llm_calls == 1  # forgot → re-infers


def test_force_relearn_bypasses_a_stuck_manual_profile_and_relearns_status(
    _profile_store, monkeypatch
):
    """Regression: a manually-mapped format profile has no way to
    capture a status column (`schema_from_manual_mapping` never asked for one), so it silently
    books failed/reversed transactions forever — and there was previously no escape hatch.
    `force_relearn` ignores the saved profile (however it was learned) and re-infers fresh;
    the profile's SCHEMA is overwritten in place even though its `origin` stays "manual"
    (never silently downgraded — see `StatementFormatStore.upsert`)."""
    from backend.services.statements import profiles
    from backend.services.statements.tiers import parse_grid

    grid = parse_grid(_MOMO_CSV)
    stuck_schema = dict(_MOMO_SCHEMA, status_col=None, success_values=[])
    saved = profiles.learn(grid, stuck_schema, origin="manual")
    assert saved["origin"] == "manual"

    # Without force_relearn: the stuck manual profile is reused verbatim — zero LLM, and the
    # failed row is NOT skipped. This is the bug as filed.
    _stub(monkeypatch, {})  # any seam call would raise
    stuck_result = asyncio.run(
        parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"})
    )
    assert stuck_result.llm_calls == 0
    assert len([r for r in stuck_result.rows if r.skip]) == 0

    # With force_relearn: the profile is ignored, a fresh LLM inference runs (WITH the status
    # column this time), and the failed row is now skipped.
    calls: list = []
    _stub(monkeypatch, {"csv_schema": _MOMO_SCHEMA}, calls)
    fresh = asyncio.run(
        parse_statement({"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}, force_relearn=True)
    )
    assert calls == ["csv_schema"]
    assert len([r for r in fresh.rows if r.skip]) == 1
    updated = _profile_store().find_by_id(saved["id"])
    assert updated["origin"] == "manual"  # provenance never silently downgraded
    assert updated["schema"]["status_col"] == 6  # but the schema itself IS fixed
    assert updated["version"] == 2


def test_manual_mapping_status_column_infers_majority_value_as_success():
    """The manual column-mapping teach flow can now capture a status column — there's no UI
    for enumerating which value means "completed", so the majority (non-blank) value in the
    mapped column is inferred as the single success value; every other distinct value is then
    skipped by tier1_tabular's status guard, same as the LLM tier's own detection."""
    from backend.services.statements import profiles

    content = _MOMO_CSV.encode("utf-8")
    mapping = {
        "date": "Thời gian",
        "description": "Loại giao dịch",
        "amount": "Số Tiền",
        "status": "Trạng Thái GD",
    }
    derived = profiles.schema_from_manual_mapping(content, "csv", mapping)
    assert derived is not None
    _, schema = derived
    assert schema["status_col"] == 6
    assert schema["success_values"] == ["Thành công"]


def test_manual_mapping_without_status_field_is_unaffected():
    """No `status` key in the mapping → status_col stays None, success_values stays empty
    (byte-identical to the pre-fix behavior) — this is an additive, optional field."""
    from backend.services.statements import profiles

    content = _MOMO_CSV.encode("utf-8")
    mapping = {"date": "Thời gian", "description": "Loại giao dịch", "amount": "Số Tiền"}
    derived = profiles.schema_from_manual_mapping(content, "csv", mapping)
    assert derived is not None
    _, schema = derived
    assert schema["status_col"] is None
    assert schema["success_values"] == []


def test_explicit_schema_bypasses_memory(_profile_store, monkeypatch):
    _stub(monkeypatch, {})
    asyncio.run(
        parse_statement(
            {"kind": "text", "text": _MOMO_CSV, "shape": "tabular"}, schema=_MOMO_SCHEMA
        )
    )
    assert _profile_store().all_profiles() == []  # a pinned schema never writes memory


def test_salvage_row_objects_recovers_truncated_array():
    """A reply cut off at the output-token cap (the real Statement 6.pdf failure): keep the
    complete rows, report truncation so the tier split-retries — never a silent 0-rows."""
    from backend.services.statements.tiers import salvage_row_objects

    truncated = (
        '[{"date": "2026-06-01", "amount": 800.00, "direction": "OUT", '
        '"description": "Advice Bill Payment"}, '
        '{"date": "2026-06-04", "amount": 99.75, "direction": "OUT", '
        '"description": "QUICKPAY {escaped \\"quote\\" and } brace"}, '
        '{"date": "2026-06-05", "amount": 200.0, "direc'
    )
    rows, was_truncated = salvage_row_objects(truncated)
    assert was_truncated is True
    assert [r["amount"] for r in rows] == [800.00, 99.75]

    clean = '[{"date": "2026-06-01", "amount": 1.0, "direction": "OUT", "description": "x"}]'
    rows, was_truncated = salvage_row_objects(clean)
    assert was_truncated is False and len(rows) == 1

    rows, was_truncated = salvage_row_objects("no json here at all")
    assert rows == [] and was_truncated is False


def test_text_tier_splits_and_retries_on_truncated_reply(monkeypatch):
    """Truncated chunk → halved and re-read (both halves fit) — full row set, no partial keep."""
    import asyncio
    import json as _json

    from backend.core import llm_seam
    from backend.services.statements.tiers import tier2_text

    full_rows = [
        {
            "date": f"2026-06-{d:02d}",
            "amount": float(d),
            "direction": "OUT",
            "description": f"ROW {d}",
            "confidence": 0.95,
        }
        for d in range(1, 11)
    ]
    calls = {"n": 0}

    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return "{}"
        calls["n"] += 1
        if calls["n"] == 1:  # first row call: cap-truncated mid-array
            return _json.dumps(full_rows)[:120]
        # split halves: return half the rows each, closed arrays
        half = full_rows[:5] if calls["n"] == 2 else full_rows[5:]
        return _json.dumps(half)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    text = "\n".join(f"line {i} " + "x" * 40 for i in range(200))
    result = asyncio.run(tier2_text(text))
    assert len(result.rows) == 10
    assert [r.amount for r in result.rows] == [float(d) for d in range(1, 11)]
    assert calls["n"] == 3  # 1 truncated + 2 halves


def test_text_tier_raises_loud_on_broken_json_but_keeps_prose_as_legit_zero(monkeypatch):
    """A reply that ATTEMPTED structured output but parsed to nothing must RAISE (never cache
    an empty parse as 'no rows'); pure prose stays a legitimate zero (pinned behavior)."""
    import asyncio

    import pytest as _pytest

    from backend.core import llm_seam
    from backend.services.statements.tiers import StatementParseError, tier2_text

    def _stub(reply: str):
        async def _complete(prompt, **kwargs):
            return "{}" if kwargs.get("source") == "statement_metadata" else reply

        monkeypatch.setattr(llm_seam, "_complete", _complete)

    monkeypatch.setattr(llm_seam, "_parse_json", None)
    _stub('{"unexpected": "shape, not rows"}')
    with _pytest.raises(StatementParseError):
        asyncio.run(tier2_text("some statement text"))
    _stub("I could not find any transactions.")
    assert asyncio.run(tier2_text("some statement text")).rows == []


def test_row_call_falls_back_to_conservative_cap_when_provider_rejects(monkeypatch):
    """Task-model routing may point statement_parse at a model whose output limit is below
    8192 — those providers hard-error on the oversized max_tokens. One retry at 4096 keeps
    the import working; the split-retry harness handles any truncation that follows."""
    import asyncio
    import json as _json

    from backend.core import llm_seam
    from backend.services.statements.tiers import tier2_text

    rows = [
        {
            "date": "2026-06-01",
            "amount": 5.0,
            "direction": "OUT",
            "description": "COFFEE",
            "confidence": 0.95,
        }
    ]
    attempts = []

    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return "{}"
        attempts.append(kwargs.get("max_tokens"))
        if kwargs.get("max_tokens", 0) > 4096:
            raise RuntimeError("max_tokens exceeds model output limit")
        return _json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    result = asyncio.run(tier2_text("statement text"))
    assert [r.amount for r in result.rows] == [5.0]
    assert attempts == [8192, 4096]  # generous first, conservative retry, then success


# ── source_row: the review's "show this row in the file" ───────────────────────────────


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_SOURCE_ROW_SCHEMA = {
    "header_row": 2,
    "date_col": 0,
    "desc_col": 1,
    "debit_col": 2,
    "credit_col": 2,
    "signed": True,
}


def test_source_row_is_the_row_number_the_file_preview_shows():
    """Owner, 2026-10-26: reviewing a statement meant scrolling the spreadsheet by hand to find
    each row. Every parsed row carries the 1-based row it came from — the same numbering the
    Documents grid (document_tables) shows — through a title row, a blank row and a blank row
    between data rows."""
    from backend.services.document_tables import _read

    content = _xlsx(
        {
            "History": [
                ["Synthetic Wallet — account history"],
                [],
                ["Date", "Description", "Amount"],
                ["2026-08-01", "Coffee QA", -45000],
                [],
                ["2026-08-02", "Top-up QA", 500000],
                ["2026-08-03", "Taxi QA", -63000],
            ]
        }
    )
    text = to_upload(content, "xlsx")["text"]
    result = asyncio.run(tiers.tier1_tabular(text, schema=_SOURCE_ROW_SCHEMA))
    shown = _read(content, "xlsx", None, 0)[2]
    assert [r.source_row for r in result.rows] == [4, 6, 7]
    for row in result.rows:
        assert row.description in [str(c) for c in shown[row.source_row - 1]]


def test_source_row_is_left_empty_past_the_first_sheet():
    """Sheets are read as one concatenated grid, so only the first sheet's indexes are its own
    row numbers: a later sheet's rows get no source_row rather than a wrong one."""
    first = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Coffee QA", -45000],
    ]
    content = _xlsx({"One": [[], [], *first], "Two": [[], [], *first[1:]]})
    text = to_upload(content, "xlsx")["text"]
    result = asyncio.run(tiers.tier1_tabular(text, schema=_SOURCE_ROW_SCHEMA))
    assert result.rows[0].source_row == 4
    assert all(r.source_row is None for r in result.rows[1:])


def test_source_row_for_a_csv_counts_every_line():
    text = "Account history\n\nDate,Description,Amount\n2026-08-01,Coffee QA,-45000\n\n2026-08-02,Taxi QA,-63000\n"
    result = asyncio.run(tiers.tier1_tabular(text, schema=_SOURCE_ROW_SCHEMA))
    assert [r.source_row for r in result.rows] == [4, 6]
