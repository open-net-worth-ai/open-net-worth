"""``ai/search/html_extract`` — the process boundary around trafilatura/lxml.

The claim under test is not "extraction works" but "a NATIVE crash in the parser cannot take
this process with it". That distinction is the whole reason the module exists: an
``except Exception`` around ``trafilatura.extract`` looks like it covers the failure and does
not, because ``abort()`` from libxml2 never becomes a Python exception. The demo died twice
on 2026-08-25 with that guard in place.

So the crash test really crashes: the child is replaced with a program that calls
``os.abort()``, producing a genuine SIGABRT in a genuine child process. If containment ever
regresses, this test file takes the whole pytest worker down with it — which is exactly the
failure it is here to prevent, made visible.
"""

from __future__ import annotations

import os

import backend.ai.search.html_extract as he

_ARTICLE = """
<html><head><title>Q3 results</title></head>
  <body>
    <nav><a href="/">Home</a><a href="/markets">Markets</a></nav>
    <article>
      <h1>Acme beats expectations</h1>
      <p>Acme reported revenue of $4.2bn for the third quarter, ahead of the $3.9bn
         consensus, and raised full-year guidance for the second time this year.</p>
      <p>Management attributed the beat to stronger enterprise renewals and a easing
         of the component shortage that constrained shipments in the first half.</p>
    </article>
    <footer>Copyright Acme</footer>
  </body>
</html>
"""


def test_extracts_the_article_and_drops_the_chrome():
    out = he.extract_main_content(_ARTICLE, "https://example.com/q3")

    assert out is not None
    assert "revenue of $4.2bn" in out
    # The point of extraction: nav/footer boilerplate is gone.
    assert "Copyright Acme" not in out


def test_a_native_abort_in_the_child_is_contained(monkeypatch, caplog):
    """THE regression. A real SIGABRT in a real child returns None instead of killing us."""
    monkeypatch.setattr(he, "_CHILD", "import os, sys; sys.stdin.read(); os.abort()")

    with caplog.at_level("WARNING"):
        out = he.extract_main_content(_ARTICLE, "https://example.com/hostile")

    assert out is None, "a crashed child must degrade to None, never propagate"
    # Reaching this line at all is the real assertion — the process is still alive.
    assert "NATIVE CRASH" in caplog.text
    # POSIX reports SIGABRT as the negative signal; Windows as the NTSTATUS STATUS_STACK_BUFFER_OVERRUN.
    expected = str(0xC0000409) if os.name == "nt" else "-6"
    assert expected in caplog.text, "an abort should be reported as the status the OS gave it"


def test_the_caller_can_still_extract_after_a_crash(monkeypatch):
    """A crash must not poison the next call — the child is disposable, not a shared parser."""
    monkeypatch.setattr(he, "_CHILD", "import os, sys; sys.stdin.read(); os.abort()")
    assert he.extract_main_content(_ARTICLE, "https://example.com/hostile") is None

    monkeypatch.undo()
    assert he.extract_main_content(_ARTICLE, "https://example.com/q3") is not None


def test_a_hanging_child_times_out_rather_than_blocking_forever(monkeypatch):
    """The other native failure a process boundary contains: a C-level loop no Python
    timeout could interrupt in-process."""
    monkeypatch.setattr(he, "_EXTRACT_TIMEOUT_S", 1)
    monkeypatch.setattr(he, "_CHILD", "import sys, time; sys.stdin.read(); time.sleep(30)")

    assert he.extract_main_content(_ARTICLE, "https://example.com/slow") is None


def test_unreadable_child_output_is_a_miss_not_a_crash(monkeypatch):
    monkeypatch.setattr(he, "_CHILD", "import sys; sys.stdin.read(); sys.stdout.write('{{{')")

    assert he.extract_main_content(_ARTICLE, "https://example.com/garbage") is None


def test_an_unextractable_page_returns_none_so_the_caller_falls_back():
    # No article body at all — trafilatura finds nothing, which is a miss, not an error.
    assert he.extract_main_content("<html><body></body></html>", "https://example.com") is None


def test_the_untrusted_html_never_reaches_the_child_as_code(monkeypatch):
    """The page crosses on stdin as JSON data. If it were interpolated into the program
    text, this payload would execute; instead it is inert content."""
    captured: dict = {}
    real_run = he.subprocess.run

    def spy(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(he.subprocess, "run", spy)
    payload = "<html><body><p>'); import os; os.abort() #</p></body></html>"
    he.extract_main_content(payload, "https://example.com")

    assert "os.abort()" not in " ".join(captured["cmd"]), "page content must not be in argv"
    assert "os.abort()" in captured["input"], "it belongs on stdin, as data"


# ── extract_og_image: the article's own social image, for the Stories & Analysis cards ──
# (owner ask 2026-09-22: Perplexity-style cards on mobile — theirs all carry a picture
# because it reads each article's og:image; only ~1% of our RSS rows arrived with one).


def test_extract_og_image_prefers_og_then_twitter_and_needs_an_absolute_http_url():
    html = """<html><head>
      <meta name="twitter:image" content="https://cdn.x.com/tw.jpg">
      <meta property="og:image" content="https://cdn.x.com/og.jpg?w=1200">
    </head><body></body></html>"""
    assert he.extract_og_image(html) == "https://cdn.x.com/og.jpg?w=1200"
    only_twitter = '<meta name="twitter:image" content="https://cdn.x.com/tw.jpg">'
    assert he.extract_og_image(only_twitter) == "https://cdn.x.com/tw.jpg"
    # content/property order, single quotes, secure_url variant, entity-encoded query
    assert (
        he.extract_og_image(
            """<meta content='https://a.com/p.png&amp;x=1' property='og:image:secure_url'>"""
        )
        == "https://a.com/p.png&x=1"
    )
    assert he.extract_og_image('<meta property="og:image" content="/relative.jpg">') is None
    assert (
        he.extract_og_image('<meta property="og:image" content="data:image/png;base64,AAA">')
        is None
    )
    assert he.extract_og_image("<html></html>") is None
    assert he.extract_og_image("") is None


def test_extract_og_image_skips_a_site_default_logo():
    # A page with no article picture falls back to the site's own logo as og:image (Yahoo:
    # yahoo-finance-default-logo.png) — that is a brand mark, not a story image (owner-
    # reproduced 2026-09-22: a "yahoo!finance" tile on the story card).
    assert (
        he.extract_og_image(
            '<meta property="og:image" content="https://s.yimg.com/cv/apiv2/social/images/yahoo-finance-default-logo.png">'
        )
        is None
    )
    assert (
        he.extract_og_image('<meta property="og:image" content="https://a.com/img/site-logo.svg">')
        is None
    )
    assert (
        he.extract_og_image(
            '<meta property="og:image" content="https://a.com/2026/09/oracle-hq.jpg">'
        )
        == "https://a.com/2026/09/oracle-hq.jpg"
    )
