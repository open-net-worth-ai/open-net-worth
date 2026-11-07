"""RSS/Atom parsing + portfolio-driven feed resolution (pure; no network)."""

from __future__ import annotations

from backend.services import feeds

# ── locale + URL builders ────────────────────────────────────────────────────────


def test_locale_for_ticker_by_suffix():
    assert feeds.locale_for_ticker("ONWAI.DE") == ("de", "DE", "DE:de")
    assert feeds.locale_for_ticker("7203.T") == ("ja", "JP", "JP:ja")
    assert feeds.locale_for_ticker("BHP.AX") == ("en-AU", "AU", "AU:en")
    assert feeds.locale_for_ticker("AAPL") == ("en-US", "US", "US:en")  # no suffix → default
    assert feeds.locale_for_ticker("FOO.ZZ") == ("en-US", "US", "US:en")  # unknown → default


def test_google_news_url_encodes_query_and_locale():
    url = feeds.google_news_url('"ONWAI"', ("de", "DE", "DE:de"))
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "%22ONWAI%22" in url
    assert "hl=de&gl=DE&ceid=DE%3Ade" in url


def test_yahoo_ticker_url():
    assert feeds.yahoo_ticker_url("ONWAI.DE").startswith(
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=ONWAI.DE"
    )


# ── resolver ──────────────────────────────────────────────────────────────────────


def test_resolve_holding_equity_gets_both_locales_plus_yahoo():
    row = {
        "instrument_id": "i1",
        "price_source_id": "ONWAI.DE",
        "label": "ONWAI SE",
        "asset_class": "equity",
    }
    fs = feeds.resolve_holding_feeds(row)
    assert len(fs) == 3  # local Google News + English Google News + Yahoo
    assert {f["via"] for f in fs} == {"rss:google_news", "rss:yahoo"}
    for f in fs:
        assert f["subjects"] == ["asset:i1", "scan:intel"]  # per-asset AND scan lanes
    gnews = [f["url"] for f in fs if f["via"] == "rss:google_news"]
    assert any("hl=de" in u for u in gnews)  # local (German) edition
    assert any("hl=en-US" in u for u in gnews)  # AND the English edition


def test_resolve_holding_equity_query_carries_both_ticker_forms():
    """Regression (owner ask 2026-08-29): the query text carries BOTH the exchange-qualified
    and bare ticker ("KSTL.DE" and "KSTL") — but locale routing and the Yahoo feed still get
    the exact exchange-qualified ticker, since only the query text benefits from the bare form."""
    fs = feeds.resolve_holding_feeds(
        {
            "instrument_id": "i1",
            "price_source_id": "KSTL.DE",
            "label": "Kestrel Dash SE",
            "asset_class": "equity",
        }
    )
    gnews = [f["url"] for f in fs if f["via"] == "rss:google_news"]
    for u in gnews:
        assert "KSTL.DE" in u and "KSTL%20DHER" not in u  # both present, not duplicated oddly
        assert "KSTL" in u
    yahoo = [f["url"] for f in fs if f["via"] == "rss:yahoo"][0]
    assert yahoo.endswith("s=KSTL.DE&region=US&lang=en-US")  # unchanged — needs the real suffix


def test_resolve_holding_ticker_without_suffix_adds_no_redundant_term():
    fs = feeds.resolve_holding_feeds(
        {
            "instrument_id": "i2",
            "price_source_id": "AAPL",
            "label": "Apple",
            "asset_class": "equity",
        }
    )
    gnews = [f for f in fs if f["via"] == "rss:google_news"][0]
    assert "AAPL%20AAPL" not in gnews["url"]  # bare_ticker(AAPL) is None — no redundant term


def test_resolve_holding_us_equity_no_duplicate_english_feed():
    fs = feeds.resolve_holding_feeds(
        {
            "instrument_id": "i2",
            "price_source_id": "AAPL",
            "label": "Apple",
            "asset_class": "equity",
        }
    )
    assert len(fs) == 2  # US listing: local IS en-US → one Google News + Yahoo (no duplicate)
    gnews = [f for f in fs if f["via"] == "rss:google_news"]
    assert len(gnews) == 1 and "hl=en-US" in gnews[0]["url"]


def test_resolve_holding_crypto_gets_google_only():
    row = {"instrument_id": "c1", "ticker": "BTC", "label": "Bitcoin", "asset_class": "crypto"}
    fs = feeds.resolve_holding_feeds(row)
    assert len(fs) == 1 and fs[0]["via"] == "rss:google_news"
    assert "Bitcoin" in feeds.urllib.parse.unquote(fs[0]["url"])


def test_resolve_holding_without_instrument_id_still_scan_tagged():
    row = {"price_source_id": "AAPL", "label": "Apple", "asset_class": "equity"}
    fs = feeds.resolve_holding_feeds(row)
    assert fs and all(f["subjects"] == ["scan:intel", "ticker:AAPL"] for f in fs)


def test_resolve_holding_empty_when_no_ticker_or_label():
    assert feeds.resolve_holding_feeds({"instrument_id": "x", "asset_class": "equity"}) == []


def test_resolve_topic_feeds_from_search_queries():
    item = {"id": "t1", "search_queries": ["fed rate cut", "cpi print"], "value": "macro"}
    fs = feeds.resolve_topic_feeds(item)
    assert len(fs) == 2
    assert all(f["subjects"] == ["topic:t1", "scan:intel"] for f in fs)
    assert all(f["via"] == "rss:google_news" for f in fs)


def test_resolve_topic_feeds_falls_back_to_value():
    fs = feeds.resolve_topic_feeds({"id": "t2", "value": "semiconductors"})
    assert len(fs) == 1 and "semiconductors" in feeds.urllib.parse.unquote(fs[0]["url"])


def test_resolve_intel_feeds_combines():
    holdings = [{"instrument_id": "i1", "price_source_id": "AAPL", "label": "Apple"}]
    topics = [{"id": "t1", "search_queries": ["ai chips"]}]
    fs = feeds.resolve_intel_feeds(holdings, topics)
    assert len(fs) == 3  # 2 holding + 1 topic


# ── parsing ───────────────────────────────────────────────────────────────────────

_GOOGLE_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item>
  <title>Uber adds to ONWAI stake - Reuters</title>
  <link>https://news.google.com/rss/articles/CBxyz?oc=5</link>
  <guid isPermaLink="false">CBxyz</guid>
  <pubDate>Mon, 18 May 2026 07:00:00 GMT</pubDate>
  <description>&lt;a href="x"&gt;Uber adds&lt;/a&gt; to its stake</description>
  <source url="https://reuters.com">Reuters</source>
</item>
</channel></rss>"""

_ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry>
  <title>SEC 8-K filing</title>
  <link rel="alternate" href="https://sec.gov/x.htm"/>
  <id>urn:sec:8k:1</id>
  <published>2026-06-17T12:00:00Z</published>
  <summary>Material event disclosed.</summary>
  <author><name>Acme Corp</name></author>
  <media:thumbnail url="https://img/x.png"/>
</entry></feed>"""


def test_parse_rss_google_news_item_captures_all_fields():
    hits = feeds.parse_rss(_GOOGLE_RSS, default_via="rss:google_news")
    assert len(hits) == 1
    h = hits[0]
    assert h["url"].startswith("https://news.google.com/rss/articles/")
    assert h["title"] == "Uber adds to ONWAI stake - Reuters"
    assert h["source"] == "Reuters"  # outlet attribution from <source>
    assert h["source_url"] == "https://reuters.com"  # publisher homepage from <source url=>
    assert h["guid"] == "CBxyz"
    assert h["published_date"] == "Mon, 18 May 2026 07:00:00 GMT"
    assert "<a" not in h["content"] and "Uber adds" in h["content"]  # html stripped
    assert h["via"] == "rss:google_news"


def test_parse_atom_entry_link_href_author_image():
    hits = feeds.parse_rss(_ATOM, default_via="regulatory:edgar")
    assert len(hits) == 1
    h = hits[0]
    assert h["url"] == "https://sec.gov/x.htm"  # Atom link href
    assert h["author"] == "Acme Corp"
    assert h["image"] == "https://img/x.png"
    assert h["published_date"] == "2026-06-17T12:00:00Z"
    assert h["via"] == "regulatory:edgar"


def test_parse_rss_source_falls_back_to_host():
    rss = b"""<rss><channel><item><title>t</title>
      <link>https://www.example.co.uk/a</link></item></channel></rss>"""
    h = feeds.parse_rss(rss)[0]
    assert h["source"] == "Example"  # derived from hostname when no <source>
    assert h["source_url"] == ""  # no <source url=> → no publisher homepage


def test_parse_rss_source_url_rejects_dangerous_scheme():
    # A <source url> with a javascript:/data: scheme must be dropped — it would otherwise flow to a
    # favicon src downstream.
    rss = (
        b"<rss><channel><item><title>t</title><link>https://news.google.com/rss/articles/CB</link>"
        b'<source url="javascript:alert(1)">Evil</source></item></channel></rss>'
    )
    h = feeds.parse_rss(rss)[0]
    assert h["source"] == "Evil"  # the display name is still captured
    assert h["source_url"] == ""  # but the unsafe url is dropped


def test_parse_rss_source_url_rejects_malformed_host():
    # A http(s) <source url> whose authority carries breakout chars (quotes/spaces/angle-brackets)
    # is dropped — its host must never reach a favicon src even though the scheme is allowed.
    rss = (
        b"<rss><channel><item><title>t</title><link>https://news.google.com/rss/articles/CB</link>"
        b'<source url="https://a&quot;onerror=alert(1).com">Evil</source></item></channel></rss>'
    )
    h = feeds.parse_rss(rss)[0]
    assert h["source_url"] == ""  # malformed authority dropped


def test_parse_rss_garbage_returns_empty():
    assert feeds.parse_rss(b"not xml") == []
    assert feeds.parse_rss(b"") == []


def test_parse_rss_rejects_dtd_entity_attack():
    # billion-laughs / XXE shape — a feed carrying a DTD/entity is refused outright.
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        b"<rss><channel><item><title>x</title><link>https://e/a</link></item></channel></rss>"
    )
    assert feeds.parse_rss(evil) == []


def test_parse_rss_drops_dangerous_url_scheme():
    # a javascript:/data: link must never reach the store/href — dropped at ingest.
    rss = b"<rss><channel><item><title>x</title><link>javascript:alert(1)</link></item></channel></rss>"
    h = feeds.parse_rss(rss)[0]
    assert h["url"] == ""


def test_watch_item_without_instrument_tags_its_ticker():
    """A watchlist item carries its symbol as `value`, and with no instrument its news would
    otherwise land only in the shared scan pool, unreachable for that one stock."""
    fs = feeds.resolve_holding_feeds({"value": "acme", "label": "ACME Entry"})
    assert fs and all(f["subjects"] == ["scan:intel", "ticker:ACME"] for f in fs)


def test_row_with_instrument_does_not_add_a_ticker_tag():
    fs = feeds.resolve_holding_feeds({"instrument_id": "i1", "ticker": "ACME", "label": "ACME"})
    assert fs and all(f["subjects"] == ["asset:i1", "scan:intel"] for f in fs)
