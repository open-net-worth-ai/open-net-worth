"""Text that reaches a phone: the one cleaner every sender goes through.

An alert's text can be shaped by a page or a headline, and Telegram links a bare domain and fetches
it for a preview with no click, so a form this misses is a leak of whatever the model put in it.
"""

from __future__ import annotations

import time

import pytest

from backend.connectors.msgfmt import render
from backend.monitoring.text_sanitize import plain_line, strip_unknown_links

LINK = "https://wire.example/story"


@pytest.mark.parametrize(
    "bad",
    [
        "[source]<(https<://evil<.com/r?v=41250)",
        "http<://evil<.com/r?v=41250",
        "evil<.com/r?v=41250",
        "evil.com/r?v=41250",
        "EVIL.COM/R?V=41250",
        "EVIL.COM?V=41250",
        "EVIL.COM:8443/R?V=41250",
        "EVIL.COM#41250",
        "évil.com/r?v=41250",
        "пример.рф/r?v=41250",
        "93.184.216.34/r?v=41250",
        "evil。com/r?v=41250",
        "evil．com/r?v=41250",
        "evil\u200b.com/r?v=41250",
        "//evil.com/r?v=41250",
        "t.me/evil_channel?start=41250",
        "www.evil.com/41250",
        "<a href='https://evil.com/r?v=41250'>Open</a>",
        "[https://evil.example.com/r?v=41250](x)",
        "[evil.com/r?v=41250](x)",
        "evil*.com/r?v=41250",
        "[[https://evil.com/r?v=41250](a)](b)",
        "_evil_.com/r?v=41250",
        "**evil**.com/r?v=41250",
        "कंपनी.com/r?v=41250",
        "ข่าวนี้.com/r?v=41250",
        "x̴.com/r?v=41250",
        "evil️.com/r?v=41250",
        "evil.भारत/r?v=41250",
        "evil.বাংলা/r?v=41250",
        "(evil.com/r?v=41250).",
        "see:evil.com/r?v=41250",
    ],
)
def test_no_form_of_a_link_survives_and_its_data_goes_with_it(bad):
    cleaned = strip_unknown_links(f"Read {bad} before the open.", set())
    assert "://" not in cleaned and "/r?" not in cleaned and "?v=" not in cleaned
    assert "41250" not in cleaned, cleaned
    assert "<a" not in render(cleaned, "telegram") and "<i>" not in render(cleaned, "telegram")


@pytest.mark.parametrize(
    "bad", ["evil.com", "acme-filings.com", "evil.xyz", "EVIL.COM", "COST-41250.EVIL.COM"]
)
def test_a_bare_domain_no_longer_reads_as_one(bad):
    assert bad not in strip_unknown_links(f"Read {bad} now.", set())


def test_details_markup_cannot_reach_the_rich_path():
    bad = "<details><summary><a href='//evil.com/x'>Open</a></summary>x</details>"
    cleaned = strip_unknown_links(bad, set())
    assert "<" not in cleaned and ">" not in cleaned


@pytest.mark.parametrize(
    "bad", ["@evil_support_bot", ".@evilbot", "@@evilbot", "@*evilbot", "/start@evilbot"]
)
def test_a_handle_is_removed(bad):
    assert "evil" not in strip_unknown_links(f"Message {bad} now.", set())


def test_cleaning_stays_fast_on_one_huge_word():
    started = time.monotonic()
    assert plain_line("a" * 200_000, 40)
    assert plain_line("[" * 50_000, 40) is not None
    assert plain_line("a." * 50_000, 40) is not None
    assert time.monotonic() - started < 1


def test_double_dollars_cannot_reach_telegrams_rich_path():
    assert "$$" not in plain_line("Cost $$x^2$$ here", 40)


@pytest.mark.parametrize(
    "link",
    ["https://medium.com/@acme/post", "https://www.youtube.com/@acme", "https://x.example/a_b_"],
)
def test_a_given_link_is_never_altered(link):
    assert strip_unknown_links(f"See [Post]({link}) now.", {link}) == f"See [Post]({link}) now."
    assert strip_unknown_links(f"See {link} now.", {link}) == f"See {link} now."


def test_a_given_link_survives_in_both_forms():
    assert strip_unknown_links(f"See [Wire]({LINK}).", {LINK}) == f"See [Wire]({LINK})."
    assert strip_unknown_links(f"See {LINK}.", {LINK}) == f"See {LINK}."


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Trim @180.50 and rebuy @4.5", "Trim @180.50 and rebuy @4.5"),
        ("P/E>30 and RSI<30", "P/E above 30 and RSI below 30"),
        ("price <= 150 and >= 120", "price ≤ 150 and ≥ 120"),
        ("J.P.Morgan cut it", "J. P. Morgan cut it"),
        ("U.S.Steel fell", "U. S. Steel fell"),
        ("sales rose.Margins fell", "sales rose. Margins fell"),
        ("Q3.Revenue up", "Q3. Revenue up"),
        ("Reuters.com says so", "Reuters says so"),
        ("earnings.In Q3", "earnings. In Q3"),
        ("Up 2*3 x<y", "Up 2*3 x below y"),
        ("via @Reuters reported", "via reported"),
        ("Up 3.5 today in the U.S., e.g. i.e. $3.5bn, 2.5x, 1.2B", None),
        ("10.5am ACME.DE 0700.HK BRK.B fell", None),
        ("HDFCBANK.NS and VNM.VN and THYAO.IS fell", None),
        ("snake_case stays", None),
        ("ACME.DE: fell 3%.", None),
    ],
)
def test_ordinary_alert_text_stays_readable(text, expected):
    assert strip_unknown_links(text, set()) == (text if expected is None else expected)


def test_plain_line_is_one_line_with_no_emphasis_and_a_word_limit():
    assert plain_line("**Sold**\n`now` and   more words here", 4) == "Sold now and more"
