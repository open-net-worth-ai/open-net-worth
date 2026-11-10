"""Followed portfolios — the pure parts: 13F and House-report parsing, the ticker lookup, the
model built from snapshots, and the mirror plan math. No network, no database."""

from __future__ import annotations

import json
from datetime import date

import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.services.followed_portfolios import edgar_13f, figi, house_ptr
from backend.services.followed_portfolios.model import build_model, normalize_symbol, status_of

TODAY = date(2026, 11, 10)


def _info_row(name, cusip, value, shares, extra=""):
    return (
        f"<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        f"<cusip>{cusip}</cusip><value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}"
        f"</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>{extra}</infoTable>"
    )


def _info_table(*rows):
    ns = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
    return f'<informationTable xmlns="{ns}">{"".join(rows)}</informationTable>'


# ── 13F holdings table ──────────────────────────────────────────────────────────────


def test_info_table_merges_one_cusip_and_sorts_by_value():
    xml = _info_table(
        _info_row("ACME CORP", "000000101", 300, 30),
        _info_row("BOLT INC", "000000202", 900, 9),
        _info_row("ACME CORP", "000000101", 200, 20),
    )
    assert edgar_13f.parse_info_table(xml) == [
        {"key": "000000202", "name": "Bolt Inc", "value": 900.0, "qty": 9.0},
        {"key": "000000101", "name": "Acme Corp", "value": 500.0, "qty": 50.0},
    ]


def test_info_table_drops_options_bonds_and_bad_rows():
    xml = _info_table(
        _info_row("ACME CORP", "000000101", 300, 30),
        _info_row("ACME CORP", "000000101", 999, 9, "<putCall>Call</putCall>"),
        _info_row("BOND CO", "000000303", 500, 5).replace(">SH<", ">PRN<"),
        _info_row("NO CUSIP", "bad", 500, 5),
        _info_row("ZERO", "000000404", 0, 5),
    )
    assert [r["key"] for r in edgar_13f.parse_info_table(xml)] == ["000000101"]


def test_info_table_refuses_a_doctype():
    with pytest.raises(ExternalServiceError):
        edgar_13f.parse_info_table('<!DOCTYPE x [<!ENTITY a "b">]><informationTable/>')


@pytest.mark.parametrize("raw", ["", "abc", "12345678901", "12 34"])
def test_cik_must_be_digits(raw):
    with pytest.raises(ValidationError):
        edgar_13f.normalize_cik(raw)


def test_cik_drops_leading_zeros():
    assert edgar_13f.normalize_cik("0000012345") == "12345"


def test_fetch_reads_two_quarters_and_maps_tickers(monkeypatch):
    filings = [
        {"filing_id": "0000000000-26-000002", "as_of": "2026-06-30", "filed_at": "2026-08-14"},
        {"filing_id": "0000000000-26-000001", "as_of": "2026-03-31", "filed_at": "2026-05-15"},
    ]
    tables = {
        filings[0]["filing_id"]: _info_table(_info_row("ACME CORP", "000000101", 600, 60)),
        filings[1]["filing_id"]: _info_table(_info_row("BOLT INC", "000000202", 400, 40)),
    }
    monkeypatch.setattr(edgar_13f, "_recent_filings", lambda cik: ("Acme Capital Lp", filings))
    monkeypatch.setattr(edgar_13f, "_info_table_url", lambda cik, accession: accession)
    monkeypatch.setattr(edgar_13f, "fetch_bytes", lambda url, **kw: tables[url].encode())
    asked = []

    def fake_tickers(cusips):
        asked.append(list(cusips))
        return {"000000101": "ACME", "000000202": ""}

    monkeypatch.setattr(figi, "tickers_for", fake_tickers)
    out = edgar_13f.fetch("12345")
    assert out["name"] == "Acme Capital Lp"
    assert out["current"]["positions"][0]["symbol"] == "ACME"
    assert out["previous"]["positions"][0]["symbol"] is None
    assert asked == [["000000101", "000000202"]]
    asked.clear()
    again = edgar_13f.fetch("12345", {"current": out["current"], "cusip_map": out["cusip_map"]})
    assert again == {"unchanged": True} and asked == []


def test_fetch_retries_a_ticker_lookup_that_never_answered(monkeypatch):
    filing = {"filing_id": "0000000000-26-000002", "as_of": "2026-06-30", "filed_at": "2026-08-14"}
    monkeypatch.setattr(edgar_13f, "_recent_filings", lambda cik: ("ACME CAPITAL LP", [filing]))
    current = {
        **filing,
        "positions": [
            {"key": "000000101", "symbol": None, "name": "ACME CORP", "value": 600.0, "qty": 60.0},
            {"key": "000000202", "symbol": None, "name": "BOLT INC", "value": 400.0, "qty": 40.0},
        ],
    }
    known = {"current": current, "previous": None, "cusip_map": {"000000202": ""}}
    monkeypatch.setattr(figi, "tickers_for", lambda cusips: {})
    assert edgar_13f.fetch("12345", known) == {"unchanged": True}
    asked = []

    def answer(cusips):
        asked.append(list(cusips))
        return {"000000101": "ACME"}

    monkeypatch.setattr(figi, "tickers_for", answer)
    out = edgar_13f.fetch("12345", known)
    assert asked == [["000000101"]]
    assert [p["symbol"] for p in out["current"]["positions"]] == ["ACME", None]
    assert out["current"]["filing_id"] == filing["filing_id"] and out["previous"] is None


# ── ticker lookup ───────────────────────────────────────────────────────────────────


def test_figi_picks_a_us_equity_and_rewrites_share_classes(monkeypatch):
    def fake(url, **kw):
        jobs = json.loads(kw["data"])
        assert [j["idValue"] for j in jobs] == ["000000101", "000000202", "000000303"]
        return [
            {"data": [{"ticker": "ACME/B", "securityType": "Common Stock"}]},
            {"warning": "No identifier found."},
            {"data": [{"ticker": "ACME 5 2030", "securityType": "Corp"}]},
        ]

    monkeypatch.setattr(figi, "fetch_json", fake)
    assert figi.tickers_for(["000000101", "000000202", "000000303"]) == {
        "000000101": "ACME-B",
        "000000202": "",
        "000000303": "",
    }


def test_figi_failure_leaves_cusips_unanswered(monkeypatch):
    def boom(url, **kw):
        raise ExternalServiceError("down")

    monkeypatch.setattr(figi, "fetch_json", boom)
    assert figi.tickers_for(["000000101"]) == {}


# ── House trade reports ─────────────────────────────────────────────────────────────

REPORT = (
    "Name: Hon. Pat Example\x00\x00 Status: Member\nID Owner Asset Transaction Type Date "
    "Notification Date Amount Cap. Gains > $200?\n"
    "SP Acme Corp - Common Stock (ACME) [ST]\nP 01/05/2026 01/06/2026 $15,001 -\n$50,000\n"
    "F\x00 S\x00 : New\nD\x00 : Bought 100 shares. Held jointly.\n"
    "SP Bolt Inc. Class A (BOLT) [ST]\nS (partial) 02/01/2026 02/02/2026 $1,001 - $15,000\n"
    "F S : New\n"
    "Bolt Inc. Class A (BOLT) [OP]\nP 02/03/2026 02/03/2026 $1,001 - $15,000\n"
    "F S : New\n"
    "Treasury Bill [GS]\nP 02/04/2026 02/04/2026 $50,001 - $100,000\n"
    "F S : New\n"
    "Cog LLC (COG) [ST]\nE 02/05/2026 02/05/2026 $1,000,001 +\n"
)


def test_report_text_yields_every_row_with_clean_names():
    rows = house_ptr.parse_report_text(REPORT)
    assert [(r["symbol"], r["side"], r["asset_type"], r["counted"]) for r in rows] == [
        ("ACME", "buy", "ST", True),
        ("BOLT", "sell", "ST", True),
        ("BOLT", "buy", "OP", False),
        (None, "buy", "GS", False),
        ("COG", "exchange", "ST", False),
    ]
    assert rows[0]["name"] == "Acme Corp - Common Stock"
    assert rows[1]["name"] == "Bolt Inc. Class A"
    assert rows[1]["partial"] is True
    assert (rows[0]["date"], rows[0]["amount_low"], rows[0]["amount_high"]) == (
        "2026-01-05",
        15001.0,
        50000.0,
    )
    assert rows[4]["amount_high"] == rows[4]["amount_low"] == 1000001.0


def test_a_scanned_report_yields_nothing():
    assert house_ptr.parse_report_text("\x00\x00 \n") == []


def _trade(symbol, side, low, high, day, partial=False, counted=True):
    return {
        "symbol": symbol,
        "name": symbol.title(),
        "side": side,
        "partial": partial,
        "amount_low": low,
        "amount_high": high,
        "date": day,
        "counted": counted,
    }


def test_estimate_folds_buys_partial_and_full_sales_in_date_order():
    trades = [
        _trade("ACME", "sell", 1000, 3000, "2026-03-01", partial=True),
        _trade("ACME", "buy", 10000, 20000, "2026-01-01"),
        _trade("BOLT", "buy", 1000, 3000, "2026-01-01"),
        _trade("BOLT", "sell", 1, 1, "2026-02-01"),
        _trade("COG", "buy", 500, 500, "2026-01-01", counted=False),
        _trade("DYNA", "sell", 5000, 5000, "2026-01-01", partial=True),
    ]
    assert house_ptr.estimate_positions(trades) == [
        {"key": "ACME", "symbol": "ACME", "name": "Acme", "value": 13000.0, "qty": 13000.0}
    ]


def test_index_keeps_only_trade_reports():
    tsv = (
        "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
        "Hon.\tExample\tPat\t\tP\tZZ01\t2026\t4/6/2026\t20000001\r\n"
        "Hon.\tExample\tPat\t\tC\tZZ01\t2026\t5/6/2026\t10000002\r\n"
        "\tOther\tSam\t\tP\tZZ02\t2026\tnot-a-date\t20000003\r\n"
    )
    assert house_ptr.parse_index(tsv) == [
        {
            "ref": "example|pat",
            "name": "Pat Example",
            "district": "ZZ01",
            "year": 2026,
            "filed_at": "2026-04-06",
            "doc_id": "20000001",
        }
    ]


@pytest.mark.parametrize("raw", ["", "example", "a|b|c", "|pat"])
def test_member_ref_shape(raw):
    with pytest.raises(ValidationError):
        house_ptr.normalize_ref(raw)


def test_member_fetch_builds_estimate_and_skips_known_reports(monkeypatch):
    monkeypatch.setattr(house_ptr, "user_today", lambda: TODAY)
    index = {
        2026: [
            {
                "ref": "example|pat",
                "name": "Pat Example",
                "district": "ZZ01",
                "year": 2026,
                "filed_at": "2026-04-06",
                "doc_id": "20000002",
            },
            {
                "ref": "example|pat",
                "name": "Pat Example",
                "district": "ZZ01",
                "year": 2026,
                "filed_at": "2026-02-01",
                "doc_id": "20000001",
            },
            {
                "ref": "other|sam",
                "name": "Sam Other",
                "district": "ZZ02",
                "year": 2026,
                "filed_at": "2026-02-01",
                "doc_id": "20000009",
            },
        ]
    }
    monkeypatch.setattr(house_ptr, "_index", lambda year: index.get(year, []))
    texts = {
        "20000001": "Acme Corp (ACME) [ST] P 01/05/2026 01/06/2026 $15,001 - $50,000",
        "20000002": "Bolt Inc (BOLT) [ST] P 03/05/2026 03/06/2026 $1,001 - $15,000",
    }
    fetched = []

    def fake_bytes(url, **kw):
        doc = url.rsplit("/", 1)[1].removesuffix(".pdf")
        fetched.append(doc)
        return doc.encode()

    monkeypatch.setattr(house_ptr, "fetch_bytes", fake_bytes)
    monkeypatch.setattr(house_ptr, "_pdf_text", lambda body: texts[body.decode()])
    out = house_ptr.fetch("Example|Pat")
    assert sorted(fetched) == ["20000001", "20000002"]
    assert out["subtitle"] == "House · ZZ01"
    assert [p["symbol"] for p in out["current"]["positions"]] == ["ACME", "BOLT"]
    assert [p["symbol"] for p in out["previous"]["positions"]] == ["ACME"]
    assert out["current"]["filing_id"] == "20000002"
    assert out["activity"][0]["symbol"] == "BOLT"
    fetched.clear()
    assert house_ptr.fetch("example|pat", out) == {"unchanged": True}
    assert fetched == []


# ── the model ───────────────────────────────────────────────────────────────────────


def _snap(filed, *positions, filing_id="f1"):
    rows = [
        {"key": s, "symbol": sym, "name": s, "value": v, "qty": q} for s, sym, v, q in positions
    ]
    return {
        "filing_id": filing_id,
        "as_of": filed,
        "filed_at": filed,
        "total_value": sum(r["value"] for r in rows),
        "positions": rows,
        "unread_reports": 0,
    }


def test_model_weights_sum_to_one_and_flag_changes():
    follow = {
        "kind": "fund_13f",
        "current": _snap(
            "2026-08-14",
            ("ACME", "ACME", 500, 50),
            ("BOLT", "BOLT", 300, 10),
            ("COG", "COG", 200, 30),
            ("DYNA", "DYNA", 100, 5),
            ("MYSTERY CO", None, 400, 9),
        ),
        "previous": _snap(
            "2026-05-15",
            ("ACME", "ACME", 400, 50),
            ("BOLT", "BOLT", 600, 20),
            ("EXIT", "EXIT", 900, 9),
        ),
    }
    model = build_model(follow, TODAY, 3)
    assert [h["symbol"] for h in model["holdings"]] == ["ACME", "BOLT", "COG"]
    assert sum(h["weight"] for h in model["holdings"]) == pytest.approx(1.0)
    assert {h["symbol"]: h["change"] for h in model["holdings"]} == {
        "ACME": "unchanged",
        "BOLT": "reduced",
        "COG": "new",
    }
    assert model["exited"] == [{"symbol": "EXIT", "name": "EXIT"}]
    assert model["unmatched"] == [{"name": "MYSTERY CO", "weight": pytest.approx(400 / 1500)}]
    assert model["age_days"] == 88 and model["estimated"] is False


def test_model_without_a_previous_filing_flags_nothing():
    follow = {
        "kind": "house_member",
        "current": _snap("2026-10-01", ("ACME", "ACME", 500, 500)),
        "previous": None,
    }
    model = build_model(follow, TODAY)
    assert model["holdings"][0]["change"] == "unchanged"
    assert model["estimated"] is True and model["exited"] == []


def test_status_reflects_error_empty_and_stale():
    old = {
        "kind": "fund_13f",
        "current": _snap("2026-01-01", ("ACME", "ACME", 1, 1)),
    }
    assert status_of(old, build_model(old, TODAY)) == "stale"
    assert status_of({**old, "last_error": "x"}, build_model(old, TODAY)) == "error"
    assert status_of({"kind": "fund_13f"}, None) == "empty"
    member = {**old, "kind": "house_member"}
    assert status_of(member, build_model(member, TODAY)) == "ok"


# ── hardening ───────────────────────────────────────────────────────────────────────


def test_member_fetch_survives_a_year_index_that_is_not_published_yet(monkeypatch):
    monkeypatch.setattr(house_ptr, "user_today", lambda: date(2027, 1, 2))
    row = {
        "ref": "example|pat",
        "name": "Pat Example",
        "district": "ZZ01",
        "year": 2026,
        "filed_at": "2026-04-06",
        "doc_id": "20000002",
    }

    def index(year):
        if year == 2027:
            raise ExternalServiceError("not there yet")
        return [row] if year == 2026 else []

    monkeypatch.setattr(house_ptr, "_index", index)
    monkeypatch.setattr(house_ptr, "fetch_bytes", lambda url, **kw: b"x")
    monkeypatch.setattr(
        house_ptr,
        "_pdf_text",
        lambda body: "Acme Corp (ACME) [ST] P 01/05/2026 01/06/2026 $1,001 - $15,000",
    )
    assert house_ptr.fetch("example|pat")["current"]["positions"][0]["symbol"] == "ACME"

    def all_down(year):
        raise ExternalServiceError("down")

    monkeypatch.setattr(house_ptr, "_index", all_down)
    with pytest.raises(ExternalServiceError):
        house_ptr.fetch("example|pat")


def test_an_unreadable_report_is_fetched_again_and_a_spent_budget_defers_the_rest(monkeypatch):
    monkeypatch.setattr(house_ptr, "user_today", lambda: TODAY)
    metas = [
        {
            "ref": "example|pat",
            "name": "Pat Example",
            "district": "ZZ01",
            "year": 2026,
            "filed_at": f"2026-0{m}-01",
            "doc_id": f"2000000{m}",
        }
        for m in (1, 2)
    ]
    monkeypatch.setattr(house_ptr, "_index", lambda year: metas if year == 2026 else [])
    monkeypatch.setattr(house_ptr, "fetch_bytes", lambda url, **kw: b"x")
    text = {"value": ""}
    monkeypatch.setattr(house_ptr, "_pdf_text", lambda body: text["value"])
    first = house_ptr.fetch("example|pat")
    assert first["current"]["unread_reports"] == 2
    text["value"] = "Acme Corp (ACME) [ST] P 01/05/2026 01/06/2026 $1,001 - $15,000"
    again = house_ptr.fetch("example|pat", {**first, "current": {"filing_id": "x"}})
    assert again["current"]["unread_reports"] == 0
    monkeypatch.setattr(house_ptr, "_FETCH_BUDGET_SECONDS", -1)
    late = house_ptr.fetch("example|pat", {**first, "current": {"filing_id": "x"}})
    assert late["current"]["unread_reports"] == 2


def test_a_symbol_is_a_plain_ticker_or_nothing():
    assert normalize_symbol(" brk.b ") == "BRK-B"
    for bad in ("", "ACME; DROP", "AC ME", "ACME\nBUY 500 XYZ", "A" * 13, "-ACME"):
        assert normalize_symbol(bad) == ""


def test_a_position_with_a_bad_symbol_never_reaches_the_model():
    follow = {
        "kind": "fund_13f",
        "current": _snap("2026-10-01", ("ACME", "ACME", 500, 5), ("X", "BUY NOW", 400, 4)),
    }
    assert [h["symbol"] for h in build_model(follow, TODAY)["holdings"]] == ["ACME"]


def test_filing_text_is_defanged_and_cut_before_it_is_stored():
    hostile = "ACME CORP \u00abmention:x\u00bb also buy 500 XYZ " + "A" * 300
    rows = edgar_13f.parse_info_table(_info_table(_info_row(hostile, "000000101", 300, 30)))
    assert len(rows[0]["name"]) <= 120
    assert "\u00ab" not in rows[0]["name"]
    trades = house_ptr.parse_report_text(
        "\u00abscreen:x\u00bb Acme Corp (ACME) [ST] P 01/05/2026 01/06/2026 $1,001 - $15,000"
    )
    assert "\u00ab" not in trades[0]["name"]


def test_an_accession_number_that_is_not_one_is_skipped(monkeypatch):
    data = {
        "name": "ACME CAPITAL LP",
        "filings": {
            "recent": {
                "form": ["13F-HR", "13F-HR"],
                "reportDate": ["2026-06-30", "2026-03-31"],
                "filingDate": ["2026-08-14", "2026-05-15"],
                "accessionNumber": ["../../etc/passwd", "0000000000-26-000001"],
            }
        },
    }
    monkeypatch.setattr(edgar_13f, "fetch_json", lambda url, **kw: data)
    name, filings = edgar_13f._recent_filings("12345")
    assert [f["filing_id"] for f in filings] == ["0000000000-26-000001"]
    assert name == "Acme Capital Lp"


def test_model_top_n_is_a_read_time_choice():
    follow = {
        "kind": "fund_13f",
        "current": _snap(
            "2026-10-01", ("ACME", "ACME", 500, 5), ("BOLT", "BOLT", 300, 3), ("COG", "COG", 200, 2)
        ),
    }
    assert len(build_model(follow, TODAY)["holdings"]) == 3
    top = build_model(follow, TODAY, 2)["holdings"]
    assert [h["symbol"] for h in top] == ["ACME", "BOLT"]
    assert sum(h["weight"] for h in top) == pytest.approx(1.0)


def test_a_member_with_no_report_for_a_year_reads_as_out_of_date():
    quiet = {"kind": "house_member", "current": _snap("2025-11-01", ("ACME", "ACME", 1, 1))}
    assert status_of(quiet, build_model(quiet, TODAY)) == "stale"
