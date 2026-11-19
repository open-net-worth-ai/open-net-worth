"""A House member's option trades: read from each row's description, held as contracts on the
stock they are written on, closed by an exercise or a sale, gone after expiry.

The scar: every option row was once read and thrown away, so a member who trades mostly in
calls (bought on one report, exercised into shares on a later one) showed a fraction of what
they hold, and copying them copied the wrong thing."""

from __future__ import annotations

from backend.services.followed_portfolios import house_options, house_ptr, service

HEADER = (
    "Name: Hon. Pat Example Status: Member\nID Owner Asset Transaction Type Date "
    "Notification Date Amount Cap. Gains > $200?\n"
)


def _row(name, sym, kind, side, day, low, high, desc=""):
    tail = f"F S : New\nD : {desc}\n" if desc else "F S : New\n"
    return f"SP {name} ({sym}) [{kind}]\n{side} {day} {day} ${low} - ${high}\n{tail}"


REPORT = HEADER + "".join(
    [
        _row(
            "Acme Corp - Common Stock",
            "ACME",
            "OP",
            "P",
            "01/14/2025",
            "250,001",
            "500,000",
            "Purchased 50 call options with a strike price of $150 and an expiration date "
            "of 1/16/26.",
        ),
        _row(
            "Acme Corp - Common Stock",
            "ACME",
            "ST",
            "P",
            "01/16/2026",
            "500,001",
            "1,000,000",
            "Exercised 50 call options purchased 1/14/25 (5,000 shares) at a strike price "
            "of $15 with an expiration date of 1/16/26.",
        ),
        _row(
            "Bolt Inc. Class A",
            "BOLT",
            "OP",
            "P",
            "05/29/2026",
            "1,000,001",
            "5,000,000",
            "Purchased 200 call options with a strike price of $2.50 and an expiration date "
            "of 3/19/27.",
        ),
        _row(
            "Cog Ltd",
            "COG",
            "OP",
            "P",
            "06/01/2026",
            "15,001",
            "50,000",
            "Purchased 10 put options with a strike price of $40 and an expiration date of "
            "12/18/26.",
        ),
        _row(
            "Cog Ltd", "COG", "ST", "P", "06/02/2026", "50,001", "100,000", "Purchased 900 shares."
        ),
        _row(
            "Dyna Co",
            "DYNA",
            "OP",
            "P",
            "02/01/2024",
            "15,001",
            "50,000",
            "Purchased 5 call options with a strike price of $10 and an expiration date of "
            "1/17/25.",
        ),
    ]
)


def test_each_option_row_carries_the_contract_its_description_names():
    rows = house_ptr.parse_report_text(REPORT)
    by = {(r["symbol"], r["asset_type"], r["date"]): r for r in rows}
    call = by[("ACME", "OP", "2025-01-14")]
    assert call["counted"] is True
    assert call["option"] == {
        "right": "call",
        "strike": 150.0,
        "expiry": "2026-01-16",
        "contracts": 50,
    }
    exercise = by[("ACME", "ST", "2026-01-16")]
    assert exercise["exercised"]["contracts"] == 50 and exercise["shares"] == 5000
    # A strike with cents keeps them: the description does not end at "$2."
    assert by[("BOLT", "OP", "2026-05-29")]["option"]["strike"] == 2.5
    assert by[("COG", "OP", "2026-06-01")]["option"]["right"] == "put"
    assert by[("COG", "ST", "2026-06-02")]["shares"] == 900


def test_an_option_row_without_its_terms_is_not_counted():
    text = HEADER + _row("Acme Corp", "ACME", "OP", "P", "01/14/2025", "1,001", "15,000")
    assert house_ptr.parse_report_text(text)[0]["counted"] is False


def test_calls_count_toward_their_stock_puts_are_listed_and_expired_contracts_are_gone():
    rows = house_ptr.parse_report_text(REPORT)
    held = {p["symbol"]: p for p in house_ptr.estimate_positions(rows, "2026-11-12")}
    # The exercise closed the calls (its strike restated by a split: still the same series)
    # and left the shares, valued at the exercise row's midpoint only.
    assert held["ACME"]["options"] == []
    assert held["ACME"]["value"] == (500001 + 1000000) / 2
    # A live call is a holding of its stock, listed with its terms.
    assert held["BOLT"]["value"] == (1000001 + 5000000) / 2
    assert held["BOLT"]["options"] == [
        {"right": "call", "strike": 2.5, "expiry": "2027-03-19", "contracts": 200}
    ]
    # A put is a bet against the stock: listed, never added to it.
    assert held["COG"]["value"] == (50001 + 100000) / 2
    assert [o["right"] for o in held["COG"]["options"]] == ["put"]
    # Expired before today, never exercised: nothing left.
    assert "DYNA" not in held


def test_a_sale_of_calls_takes_its_contracts_and_their_cost_out():
    bought = {"right": "call", "strike": 50.0, "expiry": "2027-01-15", "contracts": 20}
    sold = {**bought, "contracts": 5}
    trades = [
        {
            "symbol": "ACME",
            "name": "Acme",
            "side": "buy",
            "date": "2026-01-02",
            "amount_low": 100000,
            "amount_high": 100000,
            "counted": True,
            "option": bought,
        },
        {
            "symbol": "ACME",
            "name": "Acme",
            "side": "sell",
            "date": "2026-02-02",
            "amount_low": 1,
            "amount_high": 1,
            "counted": True,
            "option": sold,
        },
    ]
    [acme] = house_ptr.estimate_positions(trades, "2026-11-12")
    assert acme["options"][0]["contracts"] == 15
    assert acme["value"] == 75000


def test_a_row_the_page_breaks_in_two_is_put_back_together():
    text = HEADER + (
        "SP Acme Corp - Class A Common P 01/16/2026 01/16/2026 $50,001 - "
        "Filing ID #20000001 ID Owner Asset Transaction Type Date Notification Date Amount "
        "Cap. Gains > $200? Stock (ACME) [ST] $100,000 F S : New D : Exercised 5 call options "
        "purchased 1/14/25 (500 shares) at a strike price of $20 with an expiration date of "
        "1/16/26."
    )
    [row] = house_ptr.parse_report_text(text)
    assert (row["symbol"], row["asset_type"], row["amount_high"]) == ("ACME", "ST", 100000.0)
    assert row["exercised"]["contracts"] == 5


def test_a_report_read_before_options_were_understood_is_read_again(monkeypatch):
    meta = {
        "ref": "example|pat",
        "name": "Pat Example",
        "district": "ZZ01",
        "year": 2026,
        "filed_at": "2026-06-01",
        "doc_id": "20000001",
    }
    monkeypatch.setattr(house_ptr, "_index", lambda year: [meta])
    fetched = []

    def fake_fetch(m):
        fetched.append(m["doc_id"])
        trades = [
            {**t, "filed_at": m["filed_at"], "doc_id": m["doc_id"]}
            for t in house_ptr.parse_report_text(REPORT)
        ]
        return {**m, "readable": True, "trades": trades, "parser": house_ptr.PARSER_VERSION}

    monkeypatch.setattr(house_ptr, "_fetch_report", fake_fetch)
    old = {
        "current": {"filing_id": "20000001"},
        "reports": {"20000001": {**meta, "readable": True, "trades": []}},
    }
    out = house_ptr.fetch("example|pat", old)
    assert fetched == ["20000001"]
    assert not out.get("unchanged")
    # Read under the current reading: kept, never fetched again.
    fetched.clear()
    again = house_ptr.fetch("example|pat", out)
    assert again == {"unchanged": True} and fetched == []


def test_the_contract_reaches_the_screen():
    assert {"option", "exercised"} <= set(service._ACTIVITY_FIELDS)


def test_same_series_ignores_a_strike_a_split_restated():
    a = {"right": "call", "strike": 120.0, "expiry": "2024-12-20"}
    assert house_options.same_series(a, {**a, "strike": 12.0})
    assert not house_options.same_series(a, {**a, "expiry": "2025-01-17"})
