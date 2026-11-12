"""Followed portfolios — the pure parts: 13F and House-report parsing, the ticker lookup, the
model built from snapshots, and the mirror plan math. No network, no database."""

from __future__ import annotations

import json
from datetime import date

import pytest

from backend.core.errors import ExternalServiceError, ValidationError
from backend.services.followed_portfolios import (
    edgar_13f,
    etf_daily,
    figi,
    form4,
    house_ptr,
    performance,
    sec_tickers,
)
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
        {"key": "000000202", "name": "Bolt Inc", "share_class": None, "value": 900.0, "qty": 9.0},
        {"key": "000000101", "name": "Acme Corp", "share_class": None, "value": 500.0, "qty": 50.0},
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
    monkeypatch.setattr(
        edgar_13f, "_recent_filings", lambda cik, count=5: ("Acme Capital Lp", filings)
    )
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
    known = {**out, "current": out["current"]}
    again = edgar_13f.fetch("12345", known)
    # Same filing: nothing is downloaded; only the code that found no ticker is asked again.
    assert again == {"unchanged": True} and asked == [["000000202"]]


def test_fetch_retries_a_ticker_lookup_that_never_answered(monkeypatch):
    filing = {"filing_id": "0000000000-26-000002", "as_of": "2026-06-30", "filed_at": "2026-08-14"}
    monkeypatch.setattr(
        edgar_13f, "_recent_filings", lambda cik, count=5: ("ACME CAPITAL LP", [filing])
    )
    current = {
        **filing,
        "positions": [
            {"key": "000000101", "symbol": None, "name": "ACME CORP", "value": 600.0, "qty": 60.0},
            {"key": "000000202", "symbol": None, "name": "BOLT INC", "value": 400.0, "qty": 40.0},
        ],
    }
    for p in current["positions"]:
        p["share_class"] = None
    current = {
        **current,
        "positions": [
            *current["positions"],
        ],
    }
    known = {
        "current": current,
        "previous": None,
        "cusip_map": {"000000202": ""},
        "history": [{"as_of": "2026-06-30", "filed_at": "2026-08-14", "qty": {}, "value": {}}],
        "history_quarters": edgar_13f.HISTORY_QUARTERS,
        "all_positions": True,
    }
    monkeypatch.setattr(figi, "tickers_for", lambda cusips: {})
    assert edgar_13f.fetch("12345", known) == {"unchanged": True}
    asked = []

    def answer(cusips):
        asked.append(list(cusips))
        return {"000000101": "ACME"}

    monkeypatch.setattr(figi, "tickers_for", answer)
    out = edgar_13f.fetch("12345", known)
    assert asked == [["000000101", "000000202"]]
    assert [p["symbol"] for p in out["current"]["positions"]] == ["ACME", None]
    assert out["current"]["filing_id"] == filing["filing_id"] and out["previous"] is None


# ── ticker lookup ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_sec_name_list(monkeypatch):
    monkeypatch.setattr(sec_tickers, "tickers_for_names", lambda names: {})


def test_a_foreign_issuer_is_asked_about_as_a_cins():
    assert figi.id_type("H1467J104") == "ID_CINS"
    assert figi.id_type("037833100") == "ID_CUSIP"


def test_a_holding_the_code_lookup_missed_is_matched_by_its_exact_name(monkeypatch):
    filing = {"filing_id": "0000000000-26-000002", "as_of": "2026-06-30", "filed_at": "2026-08-14"}
    monkeypatch.setattr(
        edgar_13f, "_recent_filings", lambda cik, count=5: ("ACME CAPITAL LP", [filing])
    )
    current = {
        **filing,
        "positions": [
            {"key": "H00000101", "symbol": None, "name": "ACME LIMITED", "value": 6.0, "qty": 1.0},
            {"key": "000000202", "symbol": None, "name": "BOLT INC", "value": 4.0, "qty": 1.0},
        ],
    }
    for p in current["positions"]:
        p["share_class"] = None
    current = {
        **current,
        "positions": [
            *current["positions"],
        ],
    }
    monkeypatch.setattr(figi, "tickers_for", lambda cusips: {c: "" for c in cusips})
    monkeypatch.setattr(
        sec_tickers,
        "tickers_for_names",
        lambda names: {k: "ACME" for k, n in names.items() if n == "ACME LIMITED"},
    )
    known = {
        "current": current,
        "cusip_map": {},
        "ticker_overrides": {"000000202": "BOLT"},
        "history": [{"as_of": "2026-06-30", "filed_at": "2026-08-14", "qty": {}, "value": {}}],
        "history_quarters": edgar_13f.HISTORY_QUARTERS,
        "all_positions": True,
    }
    out = edgar_13f.fetch("12345", known)
    # The name match fills the gap; the owner's own correction wins over any lookup.
    assert [p["symbol"] for p in out["current"]["positions"]] == ["ACME", "BOLT"]


def test_the_sec_name_index_drops_suffixes_and_refuses_a_shared_name():
    index = sec_tickers.build_index(
        {
            "0": {"cik_str": 1, "ticker": "ACME", "title": "Acme Ltd"},
            "1": {"cik_str": 2, "ticker": "BOLT", "title": "Bolt Corp"},
            "2": {"cik_str": 3, "ticker": "BOLX", "title": "Bolt Inc."},
            "3": {"cik_str": 4, "ticker": "CRUX-A", "title": "Crux Holdings"},
            "4": {"cik_str": 4, "ticker": "CRUX-B", "title": "Crux Holdings"},
        }
    )
    assert index[sec_tickers.name_key("ACME LIMITED")] == "ACME"
    assert sec_tickers.name_key("Bolt Corp") not in index
    assert index[sec_tickers.name_key("Crux Holdings")] == "CRUX-A"


@pytest.fixture(autouse=True)
def _figi_fresh(monkeypatch):
    figi._known.clear()
    monkeypatch.setattr(figi, "_MIN_GAP_SECONDS", 0)


def test_figi_remembers_answers_and_only_asks_for_new_cusips(monkeypatch):
    asked = []

    def fake(url, **kw):
        jobs = json.loads(kw["data"])
        asked.append([j["idValue"] for j in jobs])
        return [
            {"data": [{"ticker": "ACME", "securityType": "Common Stock", "marketSector": "Equity"}]}
            for _ in jobs
        ]

    monkeypatch.setattr(figi, "fetch_json", fake)
    assert figi.tickers_for(["000000101"]) == {"000000101": "ACME"}
    assert figi.tickers_for(["000000101", "000000202"]) == {
        "000000101": "ACME",
        "000000202": "ACME",
    }
    assert asked == [["000000101"], ["000000202"]]


def test_figi_picks_a_us_equity_and_rewrites_share_classes(monkeypatch):
    def fake(url, **kw):
        jobs = json.loads(kw["data"])
        assert [j["idValue"] for j in jobs] == ["000000101", "000000202", "000000303"]
        return [
            {
                "data": [
                    {"ticker": "ACME/B", "securityType": "Common Stock", "marketSector": "Equity"}
                ]
            },
            {"warning": "No identifier found."},
            {"data": [{"ticker": "ACME 5 2030", "securityType": "Corp", "marketSector": "Corp"}]},
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
        {
            "key": "ACME",
            "symbol": "ACME",
            "name": "Acme",
            "value": 13000.0,
            "qty": 13000.0,
            "options": [],
        }
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
    assert out["subtitle"] == "Representative, ZZ01"
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
    assert model["exited"] == [
        {"symbol": "EXIT", "name": "EXIT", "prev_shares": 9.0, "prev_value": 900.0}
    ]
    assert model["prev_as_of"] == "2026-05-15"
    by = {h["symbol"]: h for h in model["holdings"]}
    assert (by["BOLT"]["shares"], by["BOLT"]["prev_shares"], by["BOLT"]["shares_change"]) == (
        10.0,
        20.0,
        -10.0,
    )
    assert by["BOLT"]["shares_change_pct"] == pytest.approx(-0.5)
    assert by["BOLT"]["price"] == pytest.approx(30.0)
    assert by["BOLT"]["change_value"] == pytest.approx(-300.0)
    assert (by["COG"]["prev_shares"], by["COG"]["shares_change_pct"]) == (0.0, None)
    assert by["ACME"]["shares_change"] == 0.0
    assert model["unmatched"] == [
        {"key": "MYSTERY CO", "name": "MYSTERY CO", "weight": pytest.approx(400 / 1500)}
    ]
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


def test_a_member_holding_carries_its_newest_counted_trade_and_no_share_counts():
    trade = {
        "symbol": "ACME",
        "counted": True,
        "side": "buy",
        "date": "2026-09-01",
        "filed_at": "2026-10-01",
        "amount_low": 1001.0,
        "amount_high": 15000.0,
    }
    follow = {
        "kind": "house_member",
        "current": _snap("2026-10-01", ("ACME", "ACME", 500, 500)),
        "previous": None,
        "activity": [
            {**trade, "counted": False, "date": "2026-09-20"},
            trade,
            {**trade, "date": "2026-01-01"},
        ],
    }
    holding = build_model(follow, TODAY)["holdings"][0]
    assert holding["last_trade"] == {
        "date": "2026-09-01",
        "filed_at": "2026-10-01",
        "side": "buy",
        "amount_low": 1001.0,
        "amount_high": 15000.0,
        "option": None,
    }
    assert holding["options"] == []
    assert "shares" not in holding


def test_a_first_filing_has_shares_but_no_move():
    follow = {
        "kind": "fund_13f",
        "current": _snap("2026-10-01", ("ACME", "ACME", 500, 5)),
        "previous": None,
    }
    holding = build_model(follow, TODAY)["holdings"][0]
    assert (holding["shares"], holding["price"]) == (5.0, 100.0)
    assert "shares_change" not in holding


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


# ── performance of the current holdings ─────────────────────────────────────────────


def _days(start: date, n: int) -> list[str]:
    from datetime import timedelta

    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_performance_weights_each_holdings_price_return_against_the_benchmark():
    days = _days(date(2026, 8, 14), 4)
    out = performance.build_performance(
        weights={"ACME": 0.75, "BOLT": 0.25},
        closes={
            "ACME": dict(zip(days, [100, 110, 120, 130])),
            "BOLT": dict(zip(days, [50, 50, 45, 40])),
        },
        bench=dict(zip(days, [400, 404, 408, 420])),
        filed_at="2026-08-14",
        today=date(2026, 8, 17),
    )
    filing = out["periods"]["FILING"]
    assert filing["status"] == "ok" and (filing["start"], filing["end"]) == (days[0], days[-1])
    assert filing["return"] == pytest.approx(0.75 * 0.30 + 0.25 * -0.20)
    assert filing["benchmark_return"] == pytest.approx(0.05)
    assert filing["holdings"] == {"ACME": pytest.approx(0.30), "BOLT": pytest.approx(-0.20)}
    assert filing["series"][0] == {"date": days[0], "portfolio": 0.0, "benchmark": 0.0}
    assert set(out["periods"]) == {"1M", "6M", "12M", "FILING", "ALL"}


def test_performance_leaves_out_a_holding_with_no_price_and_reweights_the_rest():
    days = _days(date(2026, 8, 14), 3)
    out = performance.build_performance(
        weights={"ACME": 0.5, "GHOST": 0.5},
        closes={"ACME": dict(zip(days, [100, 100, 110])), "GHOST": {days[2]: 9.0}},
        bench=dict(zip(days, [1, 1, 1])),
        filed_at="2026-08-14",
        today=date(2026, 8, 16),
    )
    filing = out["periods"]["FILING"]
    assert filing["return"] == pytest.approx(0.10)
    assert filing["missing"] == ["GHOST"] and filing["covered_weight"] == 0.5


def test_performance_carries_a_close_over_a_day_one_venue_was_shut():
    days = _days(date(2026, 8, 14), 3)
    out = performance.build_performance(
        weights={"ACME": 1.0},
        closes={"ACME": {days[0]: 100.0, days[2]: 120.0}},
        bench=dict(zip(days, [1, 1, 1])),
        filed_at="2026-08-14",
        today=date(2026, 8, 16),
    )
    assert [p["portfolio"] for p in out["periods"]["FILING"]["series"]] == [0.0, 0.0, 0.2]


def test_performance_is_unavailable_without_two_benchmark_days_or_any_priced_holding():
    one_day = performance.build_performance(
        weights={"ACME": 1.0},
        closes={"ACME": {"2026-08-14": 1.0}},
        bench={"2026-08-14": 1.0},
        filed_at="2026-08-14",
        today=date(2026, 8, 14),
    )
    assert one_day["periods"]["FILING"]["status"] == "unavailable"
    nothing = performance.build_performance(
        weights={"ACME": 1.0},
        closes={},
        bench={"2026-08-14": 1.0, "2026-08-15": 1.0},
        filed_at="2026-08-14",
        today=date(2026, 8, 15),
    )
    assert nothing["periods"]["FILING"]["status"] == "unavailable"


def test_a_trade_is_measured_from_the_day_it_became_public():
    days = _days(date(2026, 8, 14), 3)
    out = performance.build_performance(
        weights={"ACME": 1.0},
        closes={"ACME": dict(zip(days, [100, 110, 150]))},
        bench=dict(zip(days, [1, 1, 1])),
        filed_at="2026-08-14",
        today=date(2026, 8, 16),
        activity=[{"date": days[0], "filed_at": days[1], "symbol": "ACME", "counted": True}],
    )
    assert out["trade_returns"] == {f"{days[0]}|ACME": pytest.approx(150 / 110 - 1)}


def test_a_members_counted_trade_gets_its_return_since_the_trade_date():
    days = _days(date(2026, 8, 14), 3)
    out = performance.build_performance(
        weights={"ACME": 1.0},
        closes={"ACME": dict(zip(days, [100, 110, 150]))},
        bench=dict(zip(days, [1, 1, 1])),
        filed_at="2026-08-14",
        today=date(2026, 8, 16),
        activity=[
            {"date": days[1], "symbol": "ACME", "counted": True},
            {"date": days[1], "symbol": "ACME", "counted": False},
            {"date": "2020-01-01", "symbol": "ACME", "counted": True},
            {"date": days[0], "symbol": "NOPE", "counted": True},
            # Filed on the last day with a price: no price after it, so no return (not 0%).
            {"date": days[0], "filed_at": days[2], "symbol": "ACME", "counted": True},
        ],
    )
    assert out["trade_returns"] == {f"{days[1]}|ACME": pytest.approx(150 / 110 - 1)}


# ── ARK daily holdings CSV ──────────────────────────────────────────────────────────

_CSV_HEAD = "date,fund,company,ticker,cusip,shares,market value ($),weight (%)\n"


def _csv(day, *rows):
    return _CSV_HEAD + "".join(
        f'{day},ARKK,{name},{sym},{cusip},"{sh}","${val}",1.00%\n'
        for name, sym, cusip, sh, val in rows
    )


def test_ark_csv_skips_cash_and_footer_and_reads_formatted_numbers():
    text = _csv("11/11/2026", ("TESLA INC", "TSLA", "88160R101", "1,809,170", "687,086,582.60"))
    text += '11/11/2026,ARKK,Cash,,,"0","$5.00",0.00%\n'
    text += '"The principal risks of investing..."\n'
    snap = etf_daily.parse_csv(text)
    assert snap["as_of"] == snap["filing_id"] == snap["filed_at"] == "2026-11-11"
    [pos] = snap["positions"]
    assert (pos["symbol"], pos["qty"], pos["value"], pos["name"]) == (
        "TSLA",
        1809170.0,
        687086582.60,
        "Tesla Inc",
    )
    assert snap["total_value"] == pos["value"]


def test_ark_csv_with_nothing_readable_is_an_error():
    with pytest.raises(ExternalServiceError):
        etf_daily.parse_csv(_CSV_HEAD)


def test_ark_fetch_is_unchanged_the_same_day_and_moves_day_over_day(monkeypatch):
    days = {
        "monday": _csv("11/09/2026", ("ACME CORP", "ACME", "000000101", "100", "1000")),
        "tuesday": _csv("11/10/2026", ("ACME CORP", "ACME", "000000101", "150", "1500")),
    }
    served = ["monday"]
    urls = []

    def fake(url, **kw):
        urls.append(url)
        return days[served[0]].encode()

    monkeypatch.setattr(etf_daily, "fetch_bytes", fake)
    first = etf_daily.fetch("arkk")
    assert first["previous"] is None and first["name"] == "ARK Innovation ETF"
    assert urls[0].endswith("/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv")
    assert etf_daily.fetch("ARKK", {"current": first["current"]}) == {"unchanged": True}
    served[0] = "tuesday"
    second = etf_daily.fetch("ARKK", {"current": first["current"]})
    assert second["previous"] == first["current"]
    row = {"kind": "etf_daily", **second}
    model = build_model(row, date(2026, 11, 11))
    assert model["estimated"] is False
    assert [(h["symbol"], h["change"]) for h in model["holdings"]] == [("ACME", "increased")]
    assert status_of(row, model) == "ok"
    assert status_of(row, build_model(row, date(2026, 11, 25))) == "stale"


def test_ark_ref_is_an_allowlist_and_the_urls_encode_the_ampersand(monkeypatch):
    with pytest.raises(ValidationError):
        etf_daily.normalize_ref("QQQ")
    with pytest.raises(ValidationError):
        etf_daily.fetch("../ARKK")
    assert [r["source_ref"] for r in etf_daily.search("fintech")] == ["ARKF"]
    assert etf_daily.search("zzz") == []
    urls = []
    monkeypatch.setattr(
        etf_daily,
        "fetch_bytes",
        lambda url, **kw: (
            urls.append(url) or _csv("11/11/2026", ("A", "A", "1", "1", "1")).encode()
        ),
    )
    etf_daily.fetch("ARKF")
    assert "%26" in urls[0] and "&" not in urls[0]


# ── Form 4 ──────────────────────────────────────────────────────────────────────────


def _owner_xml(*txns, symbol="ACME", owner="1067983"):
    body = "".join(
        f"<nonDerivativeTransaction><transactionDate><value>{d}</value></transactionDate>"
        f"<transactionCoding><transactionCode>{c}</transactionCode></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{s}</value></transactionShares>"
        f"<transactionPricePerShare><value>{p}</value></transactionPricePerShare>"
        "</transactionAmounts></nonDerivativeTransaction>"
        for d, c, s, p in txns
    )
    return (
        "<ownershipDocument><issuer><issuerName>ACME CORP</issuerName>"
        f"<issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>"
        f"<reportingOwner><reportingOwnerId><rptOwnerCik>{owner}</rptOwnerCik>"
        "</reportingOwnerId></reportingOwner>"
        f"<nonDerivativeTable>{body}</nonDerivativeTable></ownershipDocument>"
    )


def test_form4_keeps_open_market_buys_and_sells_only():
    xml = _owner_xml(
        ("2026-11-03", "P", "100", "10"),
        ("2026-11-04", "S", "50", "12.5"),
        ("2026-11-04", "A", "999", "0"),
        ("2026-11-05", "M", "10", "1"),
    )
    rows = form4.parse_ownership(xml, filed_at="2026-11-06", accession="0000000000-26-000010")
    assert [(r["side"], r["shares"], r["price"], r["value"]) for r in rows] == [
        ("buy", 100.0, 10.0, 1000.0),
        ("sell", 50.0, 12.5, 625.0),
    ]
    assert rows[0]["symbol"] == "ACME" and rows[0]["name"] == "Acme Corp"
    assert rows[0]["source"] == "form4" and rows[0]["counted"] is True
    assert rows[0]["amount_low"] == rows[0]["amount_high"] == 1000.0


def test_form4_drops_a_form_the_fund_did_not_file_as_owner():
    # EDGAR lists a listed fund's filings AS ISSUER too: its own insiders' trades are not its.
    xml = _owner_xml(("2026-11-03", "P", "45", "510"), owner="1999999")
    kw = {"filed_at": "2026-11-06", "accession": "0000000000-26-000010"}
    assert form4.parse_ownership(xml, owner_cik="1067983", **kw) == []
    assert (
        len(form4.parse_ownership(xml.replace("1999999", "0001067983"), owner_cik="1067983", **kw))
        == 1
    )


def test_form4_drops_rows_with_no_valid_symbol_or_figures_and_refuses_a_doctype():
    accession = "0000000000-26-000010"
    for xml in (
        _owner_xml(("2026-11-03", "P", "100", "10"), symbol="A B;C"),
        _owner_xml(("2026-11-03", "P", "abc", "10")),
        _owner_xml(("not a date", "P", "100", "10")),
        _owner_xml(("2026-11-03", "P", "100", "")),
    ):
        assert form4.parse_ownership(xml, filed_at="2026-11-06", accession=accession) == []
    with pytest.raises(ExternalServiceError):
        form4.parse_ownership(
            '<!DOCTYPE x [<!ENTITY a "b">]>' + _owner_xml(), filed_at="2026-11-06", accession="x"
        )


def test_form4_scan_skips_bad_accessions_and_filenames_and_known_filings(monkeypatch):
    today = date.today()
    recent_day = today.isoformat()
    recent = {
        "form": ["4", "4", "4", "4", "13F-HR", "4"],
        "accessionNumber": [
            "0000000000-26-000001",
            "not-an-accession",
            "0000000000-26-000003",
            "0000000000-26-000004",
            "0000000000-26-000005",
            "0000000000-26-000006",
        ],
        "filingDate": [recent_day, recent_day, recent_day, recent_day, recent_day, "2001-01-01"],
        "primaryDocument": [
            "x/ok.xml",
            "x/ok.xml",
            "../evil.xml",
            "x/known.xml",
            "x/ok.xml",
            "old.xml",
        ],
    }
    monkeypatch.setattr(form4, "fetch_json", lambda url, **k: {"filings": {"recent": recent}})
    got = []

    def fake_bytes(url, **k):
        got.append(url)
        return _owner_xml((recent_day, "P", "1", "2")).encode()

    monkeypatch.setattr(form4, "fetch_bytes", fake_bytes)
    trades, fetched = form4.scan("1067983", {"0000000000-26-000004"})
    assert fetched == ["0000000000-26-000001"] and len(trades) == 1
    assert got == ["https://www.sec.gov/Archives/edgar/data/1067983/000000000026000001/ok.xml"]
    assert form4.recent_trades("1067983", set(), limit=1) == trades


def test_company_names_reads_the_cache_and_fetches_only_what_is_missing(monkeypatch):
    from backend.services import prices

    monkeypatch.setattr(prices, "_NAMES_CACHE", {"ACME": "Acme Corp Common Stock"})
    asked = []

    def fake_fetch(symbol):
        asked.append(symbol)
        prices._NAMES_CACHE[symbol] = "Bolt Industries Inc"
        return symbol, "Bolt Industries Inc", ""

    monkeypatch.setattr(prices, "_fetch_name", fake_fetch)
    monkeypatch.setattr(prices, "save_names_cache", lambda names: None)
    assert prices.company_names(["ACME", "BOLT"]) == {"ACME": "Acme Corp"}
    assert asked == []
    got = prices.company_names(["ACME", "BOLT", "ACME"], fetch_missing=True)
    assert got == {"ACME": "Acme Corp", "BOLT": "Bolt Industries Inc"}
    assert asked == ["BOLT"]


def test_a_13f_line_names_its_share_class():
    assert edgar_13f.share_class("CAP STK CL C") == "C"
    assert edgar_13f.share_class("CL A") == "A"
    assert edgar_13f.share_class("COM") is None


def test_sec_requests_are_spaced_under_the_fair_use_cap(monkeypatch):
    """SEC allows at most 10 requests a second; a first Discover visit asks for every featured
    fund at once, so every SEC request waits its turn. Other hosts are not delayed."""
    from backend.services.followed_portfolios import http

    clock = {"now": 100.0}
    slept: list[float] = []
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(http, "_sec_next", 0.0)

    for _ in range(3):
        http._wait_for_sec("https://data.sec.gov/submissions/CIK0000000001.json")
    assert slept == [pytest.approx(http.SEC_MIN_INTERVAL_SECONDS), pytest.approx(0.25)]

    slept.clear()
    http._wait_for_sec("https://disclosures-clerk.house.gov/public_disc/x.zip")
    http._wait_for_sec("https://notsec.gov.example.com/x")
    assert slept == []


def test_the_featured_funds_are_unique_and_well_formed():
    """Each featured fund is one SEC filer, named once, with who runs it."""
    from backend.services.followed_portfolios import edgar_13f

    ciks = [cik for cik, _, _ in edgar_13f.FEATURED]
    assert len(ciks) == len(set(ciks))
    assert all(edgar_13f.normalize_cik(cik) == cik for cik in ciks)
    names = [name for _, name, _ in edgar_13f.FEATURED]
    assert len(names) == len(set(names))
    assert all(who for _, _, who in edgar_13f.FEATURED)


def test_a_filings_abbreviated_issuer_matches_secs_spelled_out_name():
    """ "Asml Hldg Nv" in a 13F is "ASML HOLDING N.V." in SEC's list: abbreviations are
    spelled out, a dotted N.V. is one word, and "Holding" only says what kind of company."""
    from backend.services.followed_portfolios.sec_tickers import name_key

    assert name_key("Asml Hldg Nv") == name_key("ASML HOLDING N.V.") == "asml"
    assert name_key("Acme Semiconductor Mfg Co Ltd") == name_key(
        "ACME SEMICONDUCTOR MANUFACTURING CO LTD"
    )
    assert name_key("Acme Intl Grp") == name_key("ACME INTERNATIONAL GROUP INC")


def test_a_portfolios_page_lists_every_holding_as_its_share_of_the_whole():
    """No cap on the page: every position the filing lists, each weighed against the whole
    filing (a holding with no ticker included), so the table adds up to 100%."""
    from datetime import date

    from backend.services.followed_portfolios.model import build_model

    positions = [
        {"key": f"K{i:02d}", "symbol": f"S{i:02d}", "name": f"Co {i}", "qty": 1, "value": 100 - i}
        for i in range(30)
    ] + [{"key": "XNONE", "symbol": None, "name": "Mystery", "qty": 1, "value": 50}]
    follow = {
        "kind": "fund_13f",
        "current": {"as_of": "2026-06-30", "filed_at": "2026-08-14", "positions": positions},
    }
    whole = sum(p["value"] for p in positions)
    page = build_model(follow, date(2026, 11, 12), None)
    assert len(page["holdings"]) == 30
    assert [u["key"] for u in page["unmatched"]] == ["XNONE"]
    shares = sum(h["weight"] for h in page["holdings"]) + sum(
        u["weight"] for u in page["unmatched"]
    )
    assert shares == pytest.approx(1.0)
    assert page["holdings"][0]["weight"] == pytest.approx(100 / whole)
    # A card or a copy still trades toward the top N, summing to 1 among them.
    card = build_model(follow, date(2026, 11, 12))
    assert len(card["holdings"]) == 20
    assert sum(h["weight"] for h in card["holdings"]) == pytest.approx(1.0)


def test_ticker_lookups_go_largest_first(monkeypatch):
    """The lookup service allows so many codes a refresh: the newest quarter's largest
    holdings are asked about first, so the weight resolves on the first visit."""
    from backend.services.followed_portfolios import edgar_13f

    asked: list[str] = []
    monkeypatch.setattr(edgar_13f.figi, "tickers_for", lambda keys: asked.extend(keys) or {})
    monkeypatch.setattr(edgar_13f.sec_tickers, "tickers_for_names", lambda names: {})
    snaps = [
        {"positions": [{"key": "SMALL", "value": 1}, {"key": "BIG", "value": 9}]},
        {"positions": [{"key": "OLDBIG", "value": 99}]},
    ]
    edgar_13f.resolve_tickers(snaps, {})
    assert asked == ["BIG", "SMALL", "OLDBIG"]


def test_a_new_york_registry_share_is_a_stock_a_13f_can_hold():
    """ASML's US listing is typed "NY Reg Shrs", not "Common Stock" or "ADR": it is matched."""
    from backend.services.followed_portfolios import figi

    reply = [
        {
            "ticker": "ASML",
            "securityType": "NY Reg Shrs",
            "securityType2": "Depositary Receipt",
            "marketSector": "Equity",
        }
    ]
    assert figi._pick(reply) == "ASML"


def test_a_code_is_matched_to_a_share_never_to_a_derivative_or_a_bond():
    """One filing code can come back as several instruments: only a share gets the ticker."""
    from backend.services.followed_portfolios import figi

    def pick(**match):
        return figi._pick([{"ticker": "ACME", **match}])

    assert pick(securityType="Common Stock", marketSector="Equity") == "ACME"
    assert pick(securityType="GDR", securityType2="Depositary Receipt", marketSector="Equity")
    assert pick(securityType="Equity Option", securityType2="Option", marketSector="Equity") == ""
    assert pick(securityType="Equity WRT", securityType2="Warrant", marketSector="Equity") == ""
    assert pick(securityType="Right", marketSector="Equity") == ""
    assert pick(securityType="Corp", securityType2="Corp", marketSector="Corp") == ""


def test_search_finds_a_filer_by_the_start_of_its_name_and_keeps_only_13f_filers(monkeypatch):
    """ "nv" finds NVIDIA (SEC's name search, as its search box suggests), keeping only names
    with a 13F on file; the full-text search adds whole-word matches, never fragments."""
    from backend.services.followed_portfolios import edgar_13f

    def fake_fetch(url, what):
        if "keysTyped=" in url:
            return {
                "hits": {
                    "hits": [
                        {"_id": "0000000001", "_source": {"entity": "ACME VIDEO CORP (ACMV)"}},
                        {"_id": "0000000002", "_source": {"entity": "ACME HOMES INC (AHM)"}},
                    ]
                }
            }
        return {
            "hits": {
                "hits": [
                    {"_source": {"display_names": ["Acme Group NV (CIK 0000000003)"]}},
                    {"_source": {"display_names": ["Invesco Acme Advisers (CIK 0000000004)"]}},
                ]
            }
        }

    monkeypatch.setattr(edgar_13f, "fetch_json", fake_fetch)
    monkeypatch.setattr(
        edgar_13f,
        "_recent_filings",
        lambda cik, n: ("", [{"as_of": "2026-06-30"}] if cik == "1" else []),
    )
    found = [(r["source_ref"], r["name"]) for r in edgar_13f.search("nv")]
    assert found == [("1", "Acme Video Corp"), ("3", "Acme Group NV")]
    assert edgar_13f.starts_a_word("nv", "Acme Group NV")
    assert not edgar_13f.starts_a_word("nv", "Invesco Acme Advisers")


def test_a_card_asks_the_lookup_about_its_largest_holdings_only(monkeypatch):
    """A card shows the top holdings: it waits on the shared lookup pace for those, not for
    every code a filing lists. The page (no limit) asks about them all."""
    from backend.services.followed_portfolios import edgar_13f

    asked: list[str] = []
    monkeypatch.setattr(edgar_13f.figi, "tickers_for", lambda keys: asked.extend(keys) or {})
    monkeypatch.setattr(edgar_13f.sec_tickers, "tickers_for_names", lambda names: {})
    snaps = [{"positions": [{"key": f"K{i}", "value": 100 - i} for i in range(40)]}]
    edgar_13f.resolve_tickers(snaps, {}, limit=3)
    assert asked == ["K0", "K1", "K2"]
    asked.clear()
    edgar_13f.resolve_tickers(snaps, {})
    assert len(asked) == 40
