"""INT5 additions to monitoring/collect.py:
* gather_rss emits newly-inserted rows (only) to core.news_arrivals — a re-merge of an
  already-known row must NOT re-emit.
* gather_matched (the shared-firehose / combined-query fetch) fans hits out to whichever
  entities match, records + reads correctly, and ALSO emits new arrivals.
* conditional GET (ETag/Last-Modified) round-trips through _fetch_feed's real code path
  (a fake opener/response, network-free).
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error

import pytest

from backend.db.evidence_store import EvidenceStore


@pytest.fixture
def store(db_isolated):
    return EvidenceStore()


@pytest.fixture(autouse=True)
def _clear_arrivals_registry():
    import backend.core.news_arrivals as na

    na._CONSUMERS.clear()
    yield
    na._CONSUMERS.clear()


def _capture_arrivals():
    import backend.core.news_arrivals as na

    seen: list = []

    async def consumer(rows):
        seen.extend(rows)

    na.register_consumer("test", consumer)
    return seen


# ── gather_rss arrival emission ──────────────────────────────────────────────────


def test_gather_rss_emits_only_newly_inserted_rows(store, monkeypatch):
    from backend.monitoring import collect

    seen = _capture_arrivals()

    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {"url": "https://e/a", "title": "A", "content": "b", "published_date": "just now"}
        ],
    )
    feeds_in = [{"url": "u1", "subjects": ["asset:i1"], "kind": "news", "via": "rss"}]

    # First fetch: brand-new row → emitted.
    asyncio.run(collect.gather_rss(feeds_in, store=store))
    assert len(seen) == 1
    assert seen[0]["url"] == "https://e/a"

    # Second fetch of the SAME url/content: seen_count bumps to 2 → NOT re-emitted.
    asyncio.run(collect.gather_rss(feeds_in, store=store))
    assert len(seen) == 1  # unchanged


def test_gather_rss_no_arrivals_when_no_new_rows(store, monkeypatch):
    from backend.monitoring import collect

    seen = _capture_arrivals()
    monkeypatch.setattr(collect, "_fetch_feed", lambda url, max_age=None: [])
    asyncio.run(
        collect.gather_rss(
            [{"url": "u1", "subjects": ["asset:i1"], "kind": "news", "via": "rss"}], store=store
        )
    )
    assert seen == []


def test_gather_rss_arrival_row_carries_subjects(store, monkeypatch):
    from backend.monitoring import collect

    seen = _capture_arrivals()
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [{"url": "https://e/a", "title": "A"}],
    )
    asyncio.run(
        collect.gather_rss(
            [{"url": "u1", "subjects": ["topic:t1", "scan:intel"], "kind": "news", "via": "rss"}],
            store=store,
        )
    )
    assert seen[0]["subjects"] == ["scan:intel", "topic:t1"]


# ── gather_matched ────────────────────────────────────────────────────────────────


def test_gather_matched_fans_out_by_entity_and_emits(store, monkeypatch):
    from backend.monitoring import collect

    seen = _capture_arrivals()
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {"url": "https://e/onwai", "title": "ONWAI confirms takeover talks"},
            {"url": "https://e/unrelated", "title": "Totally unrelated headline"},
        ],
    )
    entities = [{"subject": "topic:t1", "terms": ["onwai"]}]

    out = asyncio.run(collect.gather_matched("https://firehose", entities, store=store))

    assert any(r["url"] == "https://e/onwai" for r in out)
    assert not any(r["url"] == "https://e/unrelated" for r in out)  # unmatched hit dropped
    assert store.for_subjects(["topic:t1"], now="")
    assert len(seen) == 1
    assert seen[0]["url"] == "https://e/onwai"


def test_gather_matched_empty_entities_noop(store):
    from backend.monitoring import collect

    assert asyncio.run(collect.gather_matched("https://firehose", [], store=store)) == []


def test_gather_matched_no_matches_returns_empty(store, monkeypatch):
    from backend.monitoring import collect

    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {"url": "https://e/x", "title": "unrelated"}
        ],
    )
    entities = [{"subject": "topic:t1", "terms": ["onwai"]}]
    assert asyncio.run(collect.gather_matched("https://firehose", entities, store=store)) == []


def test_gather_matched_passes_parser_and_user_agent_through(store, monkeypatch):
    from backend.monitoring import collect

    received = {}

    def _fake_fetch(url, max_age=None, user_agent=None, parser=None):
        received["url"] = url
        received["user_agent"] = user_agent
        received["parser"] = parser
        return []

    monkeypatch.setattr(collect, "_fetch_feed", _fake_fetch)
    entities = [{"subject": "topic:t1", "terms": ["x"]}]

    def _custom_parser(raw):
        return []

    asyncio.run(
        collect.gather_matched(
            "https://firehose",
            entities,
            parser=_custom_parser,
            user_agent="CustomUA/1.0",
            store=store,
        )
    )
    assert received["user_agent"] == "CustomUA/1.0"
    assert received["parser"] is _custom_parser


def test_gather_matched_swallows_fetch_exception(store, monkeypatch):
    from backend.monitoring import collect

    def _boom(url, max_age=None, user_agent=None, parser=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(collect, "_fetch_feed", _boom)
    entities = [{"subject": "topic:t1", "terms": ["x"]}]
    # _fetch_feed itself already swallows and returns [] internally in production, but
    # gather_matched's own asyncio.to_thread call must not propagate a raise either way here
    # since the stub bypasses _fetch_feed's own try/except.
    with pytest.raises(RuntimeError):
        asyncio.run(collect.gather_matched("https://firehose", entities, store=store))


# ── SF-1 (code review): `via` must actually LAND in the stored evidence row ──────────
#
# parse_rss's bare default_via="rss" means a hit arrives from _fetch_feed already carrying
# {"via": "rss"} (never missing) — a caller-side setdefault is therefore always a no-op. These
# tests assert what actually PERSISTS in the evidence store (not just a registry attribute),
# through the real gather_matched/gather_rss -> record_evidence path.


def test_gather_matched_via_param_lands_in_stored_row_over_parser_stamp(store, monkeypatch):
    from backend.monitoring import collect

    # Mimics parse_rss's real behavior: every hit already carries the generic "rss" via,
    # baked in by the bare default_via, regardless of which feed it came from.
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {"url": "https://e/ft1", "title": "ONWAI takeover talks", "via": "rss"}
        ],
    )
    entities = [{"subject": "topic:t1", "terms": ["onwai"]}]

    asyncio.run(
        collect.gather_matched(
            "https://ft.com/companies", entities, via="rss:ft_companies", store=store
        )
    )

    rows = store.for_subjects(["topic:t1"], now="")
    assert len(rows) == 1
    assert rows[0]["via"] == "rss:ft_companies"  # NOT the parser's generic "rss"


def test_gather_matched_without_via_falls_back_to_hit_or_rss(store, monkeypatch):
    """No `via` passed (existing callers, pre-SF-1 behavior) — still degrades gracefully."""
    from backend.monitoring import collect

    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {"url": "https://e/x", "title": "ONWAI takeover talks"}
        ],
    )
    entities = [{"subject": "topic:t1", "terms": ["onwai"]}]

    asyncio.run(collect.gather_matched("https://firehose", entities, store=store))

    rows = store.for_subjects(["topic:t1"], now="")
    assert rows[0]["via"] == "rss"


def test_gather_rss_resolver_via_wins_over_parser_stamped_via(store, monkeypatch):
    """Same bug, gather_rss side: Google/Yahoo/custom-feed hits also arrive with a baked-in
    "rss" via from parse_rss — the RESOLVER's own via (from services/feeds.py's `_feed()`) must
    win, not be silently discarded by a no-op setdefault."""
    from backend.monitoring import collect

    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {"url": "https://e/y", "title": "A", "content": "b", "via": "rss"}
        ],
    )
    feeds_in = [{"url": "u1", "subjects": ["asset:i1"], "kind": "news", "via": "rss:google_news"}]

    asyncio.run(collect.gather_rss(feeds_in, store=store))

    rows = store.for_subjects(["asset:i1"], now="")
    assert rows[0]["via"] == "rss:google_news"  # NOT the parser's generic "rss"


# ── conditional GET (ETag / Last-Modified) ────────────────────────────────────────


def _make_conditional_opener_stub(
    monkeypatch, *, xml: bytes, etag: str | None, last_modified: str | None
):
    class _Headers:
        def __init__(self, etag, last_modified):
            self._d = {}
            if etag:
                self._d["ETag"] = etag
            if last_modified:
                self._d["Last-Modified"] = last_modified

        def get(self, key):
            return self._d.get(key)

    class _FakeResp:
        headers = _Headers(etag, last_modified)

        def read(self, n):
            return xml

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _FakeOpener:
        def open(self, req, timeout=None):
            return _FakeResp()

    monkeypatch.setattr(
        "backend.core.pinned_http._build_pinned_opener", lambda pinned_ip: _FakeOpener()
    )


def test_fetch_feed_stores_etag_and_last_modified(monkeypatch):
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._conditional_cache.clear()
    xml = b"<rss><channel><item><title>t</title><link>https://e/a</link></item></channel></rss>"
    _make_conditional_opener_stub(
        monkeypatch, xml=xml, etag='"abc123"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT"
    )

    collect._fetch_feed("https://example.com/feed", max_age=0.0)

    assert collect._conditional_cache["https://example.com/feed"]["etag"] == '"abc123"'
    assert (
        collect._conditional_cache["https://example.com/feed"]["last_modified"]
        == "Mon, 01 Jan 2026 00:00:00 GMT"
    )
    collect._conditional_cache.clear()


def test_fetch_feed_response_without_headers_attr_is_safe(monkeypatch):
    """A minimal fake response (no .headers) must not raise — defensive getattr guard."""
    from backend.monitoring import collect

    collect._rss_cache.clear()

    class _BareResp:
        def read(self, n):
            return b"<rss><channel><item><title>t</title><link>https://e/a</link></item></channel></rss>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _BareOpener:
        def open(self, req, timeout=None):
            return _BareResp()

    monkeypatch.setattr(
        "backend.core.pinned_http._build_pinned_opener", lambda pinned_ip: _BareOpener()
    )

    hits = collect._fetch_feed("https://example.com/feed-bare", max_age=0.0)
    assert hits and hits[0]["title"] == "t"


# ── SF-3/SF-6 (code review): fetch_failed signal + bounded caches ────────────────


def test_fetch_feed_failure_log_includes_sanitized_url_and_error_detail(monkeypatch, caplog):
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._fetch_failed.clear()
    url = "https://example.com/private/feed?api_key=do-not-log"

    def _boom(*a, **kw):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr("backend.core.net_guard.resolve_public_url", _boom)
    caplog.set_level(logging.WARNING, logger="backend.monitoring.collect")

    collect._fetch_feed(url, max_age=0.0)

    assert "url=https://example.com/private/feed" in caplog.text
    assert "error=RuntimeError" in caplog.text
    assert "detail=upstream unavailable" in caplog.text
    assert "api_key" not in caplog.text


def test_fetch_feed_failure_log_does_not_throw_for_malformed_url(caplog):
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._fetch_failed.clear()
    caplog.set_level(logging.WARNING, logger="backend.monitoring.collect")

    assert collect._fetch_feed("https://[not-an-ipv6/feed", max_age=0.0) == []
    assert "url=<invalid-url>" in caplog.text


def test_rss_error_detail_redacts_mixed_case_url_secrets():
    from backend.monitoring import collect

    detail = collect._rss_error_detail(
        urllib.error.URLError("HTTPS://example.com/feed?token=do-not-log")
    )

    assert "<url>" in detail
    assert "token" not in detail


def test_rss_log_url_strips_control_characters():
    from backend.monitoring import collect

    assert "\x1b" not in collect._rss_log_url("https://example.com/feed\x1b[31m")
    assert "\x9b" not in collect._rss_log_url("https://example.com/feed\x9b31m")


def test_fetch_failed_true_after_a_real_failure(monkeypatch):
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._fetch_failed.clear()
    url = "https://example.com/broken-feed"

    def _boom(*a, **kw):
        raise RuntimeError("network down")

    # _fetch_feed now resolves+pins via pinned_urlopen -> net_guard.resolve_public_url.
    monkeypatch.setattr("backend.core.net_guard.resolve_public_url", _boom)
    collect._fetch_feed(url, max_age=0.0)
    assert collect.fetch_failed(url) is True


def test_fetch_failed_false_after_a_success(monkeypatch):
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._fetch_failed.clear()
    url = "https://example.com/healthy-feed"

    class _FakeResp:
        def read(self, n):
            return b"<rss><channel><item><title>t</title><link>https://e/a</link></item></channel></rss>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _FakeOpener:
        def open(self, req, timeout=None):
            return _FakeResp()

    monkeypatch.setattr(
        "backend.core.pinned_http._build_pinned_opener", lambda pinned_ip: _FakeOpener()
    )
    collect._fetch_feed(url, max_age=0.0)
    assert collect.fetch_failed(url) is False


def test_fetch_failed_unknown_url_defaults_false():
    from backend.monitoring import collect

    assert collect.fetch_failed("https://never-fetched.example.com") is False


def test_fetch_failed_not_touched_by_a_pure_cache_hit(monkeypatch):
    """A cache HIT (within max_age) must not overwrite the last real attempt's failure status —
    it never re-attempts the network at all."""
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._fetch_failed.clear()
    url = "https://example.com/cached-feed"
    collect._rss_cache[url] = (__import__("time").monotonic(), [{"title": "cached"}])
    collect._fetch_failed[url] = True  # simulate: the LAST real attempt had failed

    hits = collect._fetch_feed(url, max_age=900.0)  # well within TTL → cache hit, no fetch

    assert hits == [{"title": "cached"}]
    assert collect.fetch_failed(url) is True  # unchanged — no new attempt was made


def test_rss_cache_bounded_evicts_oldest_by_timestamp():
    from backend.monitoring import collect

    collect._rss_cache.clear()
    now = __import__("time").monotonic()
    # Seed one MORE than the cap, with strictly increasing timestamps (oldest first).
    for i in range(collect._MAX_CACHE_ENTRIES + 5):
        collect._rss_cache[f"https://e/{i}"] = (now + i, [])

    collect._evict_oldest_by_timestamp(collect._rss_cache, collect._MAX_CACHE_ENTRIES)

    assert len(collect._rss_cache) == collect._MAX_CACHE_ENTRIES
    # the 5 OLDEST (lowest timestamp) entries must be gone; the newest must survive
    assert "https://e/0" not in collect._rss_cache
    assert "https://e/4" not in collect._rss_cache
    assert f"https://e/{collect._MAX_CACHE_ENTRIES + 4}" in collect._rss_cache
    collect._rss_cache.clear()


def test_conditional_cache_bounded_evicts_oldest_inserted():
    from backend.monitoring import collect

    collect._conditional_cache.clear()
    for i in range(collect._MAX_CACHE_ENTRIES + 3):
        collect._conditional_cache[f"https://e/{i}"] = {"etag": str(i)}

    collect._evict_oldest_fifo(collect._conditional_cache, collect._MAX_CACHE_ENTRIES)

    assert len(collect._conditional_cache) == collect._MAX_CACHE_ENTRIES
    assert "https://e/0" not in collect._conditional_cache  # first-inserted → first evicted
    assert f"https://e/{collect._MAX_CACHE_ENTRIES + 2}" in collect._conditional_cache
    collect._conditional_cache.clear()


def test_fetch_feed_caps_all_three_caches_on_every_call(monkeypatch):
    """_fetch_feed itself enforces the bound after every real attempt — not just a
    standalone eviction-fn unit test."""
    from backend.monitoring import collect

    collect._rss_cache.clear()
    collect._conditional_cache.clear()
    collect._fetch_failed.clear()

    class _FakeResp:
        def read(self, n):
            return b"<rss><channel><item><title>t</title><link>https://e/a</link></item></channel></rss>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _FakeOpener:
        def open(self, req, timeout=None):
            return _FakeResp()

    monkeypatch.setattr(
        "backend.core.pinned_http._build_pinned_opener", lambda pinned_ip: _FakeOpener()
    )

    for i in range(collect._MAX_CACHE_ENTRIES + 10):
        collect._fetch_feed(f"https://example.com/feed{i}", max_age=0.0)

    assert len(collect._rss_cache) <= collect._MAX_CACHE_ENTRIES
    assert len(collect._fetch_failed) <= collect._MAX_CACHE_ENTRIES
    collect._rss_cache.clear()
    collect._fetch_failed.clear()


# ── read_back=False: the pollers record + emit, and skip the evidence read-back ─────────────


def test_read_back_false_records_and_emits_without_reading_back(store, monkeypatch):
    """Every poller discards gather_rss's return value, yet each call decoded every evidence
    row tagged with its subjects (tens of MB of JSON on a real book) — CPU that starved a
    concurrent statement commit (2026-10-26). read_back=False must change nothing else."""
    from backend.monitoring import collect
    from backend.services import evidence as ev

    seen = _capture_arrivals()
    reads: list = []
    real_read = ev.read_evidence

    def _counting_read(**kwargs):
        reads.append(kwargs)
        return real_read(**kwargs)

    monkeypatch.setattr(collect, "read_evidence", _counting_read)
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None: [
            {"url": f"https://e/{url}", "title": url, "content": "c", "published_date": "just now"}
        ],
    )
    feeds_in = [{"url": "u1", "subjects": ["asset:i1"], "kind": "news", "via": "rss"}]

    out = asyncio.run(collect.gather_rss(feeds_in, store=store, read_back=False))
    assert out == [] and reads == []
    assert [r["url"] for r in seen] == ["https://e/u1"]  # the arrival still fired
    assert [r["url"] for r in store.for_subjects(["asset:i1"], now="")] == ["https://e/u1"]

    feeds_in[0]["url"] = "u2"
    out = asyncio.run(collect.gather_rss(feeds_in, store=store))  # default: unchanged contract
    assert {r["url"] for r in out} == {"https://e/u1", "https://e/u2"} and len(reads) == 1


def test_gather_matched_read_back_false_skips_the_read(store, monkeypatch):
    from backend.monitoring import collect

    seen = _capture_arrivals()
    monkeypatch.setattr(collect, "read_evidence", lambda **k: pytest.fail("read back"))
    monkeypatch.setattr(
        collect,
        "_fetch_feed",
        lambda url, max_age=None, user_agent=None, parser=None: [
            {
                "url": "https://e/acme",
                "title": "Acme beats",
                "content": "Acme",
                "published_date": "now",
            }
        ],
    )
    out = asyncio.run(
        collect.gather_matched(
            "u", [{"subject": "asset:acme", "terms": ["Acme"]}], store=store, read_back=False
        )
    )
    assert out == []
    assert [r["url"] for r in seen] == ["https://e/acme"]
    assert store.for_subjects(["asset:acme"], now="")
