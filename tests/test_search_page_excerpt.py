"""Query-focused page excerpts (``ai/search/excerpt.py``) — the scoring half of the
web-search top-result reads, behind ``search_reads_results``. Pure functions; no I/O."""

from __future__ import annotations

import re

import pytest

from backend.ai.search.excerpt import EXCERPT_MAX_CHARS, build_excerpt, query_terms

pytestmark = pytest.mark.flags(search_reads_results=True)

_NAV = "Home | Markets | Nvidia | Earnings | Guidance | Tech | Opinion | Sign in | Subscribe"
_MENU = (
    "Business Markets Nvidia Earnings Guidance Technology Politics Opinion Health Science "
    "World Sport Travel Culture Style Video Podcasts"
)
_COOKIES = "We use cookies to improve your experience. Accept all cookies or manage settings."
_RELEVANT = (
    "Nvidia reported record data-center revenue and guided next-quarter revenue to $54 billion, "
    "above analyst estimates, as demand for its Blackwell chips stayed ahead of supply."
)
_UNRELATED = (
    "The city council voted on Tuesday to extend the library's weekend opening hours through the "
    "end of the summer, citing a sharp rise in visitors."
)


def _page(*blocks: str) -> str:
    return "\n\n".join(blocks)


def test_query_terms_drop_stopwords_and_fold_plurals():
    assert query_terms("What are the latest Nvidia earnings?") == ["nvidia", "earning"]
    # A query of nothing but stopwords still has terms to match.
    assert query_terms("what is the") == ["what", "is", "the"]


def test_picks_the_matching_passage_not_the_nav_header():
    page = _page(_NAV, _MENU, _COOKIES, _UNRELATED, _RELEVANT)
    excerpt = build_excerpt(page, "Nvidia earnings guidance")
    assert "guided next-quarter revenue" in excerpt
    assert "Sign in" not in excerpt  # the pipe-separated nav bar is not prose
    assert "Politics" not in excerpt  # nor is the Title-Case menu run
    assert "cookies" not in excerpt
    assert "library" not in excerpt


def test_returns_nothing_when_no_passage_shares_a_query_term():
    assert build_excerpt(_page(_UNRELATED, _COOKIES), "Nvidia earnings") == ""
    assert build_excerpt("", "Nvidia") == ""
    assert build_excerpt(_RELEVANT, "") == ""


def test_markup_soup_is_not_an_excerpt():
    soup = "<script>var nvidia = {earnings: [1,2,3], guidance: (x) => x / 2};</script> " * 8
    assert build_excerpt(soup, "nvidia earnings guidance") == ""


def test_rarer_query_terms_outweigh_common_ones():
    common = [
        f"The company posted update number {i:02d} for its regional sales team today."
        for i in range(8)
    ]
    rare = (
        "Blackwell shipments began in the second half of the year, ahead of the planned schedule."
    )
    # 'company' is on eight passages, 'blackwell' on one. With room for ONE passage the rare
    # match wins, though every 'company' passage comes earlier in the page.
    page = _page(*common, rare)
    assert build_excerpt(page, "company blackwell", max_chars=len(rare)) == rare


def test_respects_the_cap_and_keeps_page_order():
    paragraphs = [
        f"Paragraph {i:02d}: Nvidia earnings guidance was discussed at length on the call, "
        f"with analysts probing the outlook for quarter number {i}."
        for i in range(60)
    ]
    excerpt = build_excerpt(_page(*paragraphs), "Nvidia earnings guidance")
    assert 0 < len(excerpt) <= EXCERPT_MAX_CHARS
    numbers = [int(m) for m in re.findall(r"Paragraph (\d\d):", excerpt)]
    assert len(numbers) > 5 and numbers == sorted(numbers)

    small = build_excerpt(_page(*paragraphs), "Nvidia earnings guidance", max_chars=400)
    assert 0 < len(small) <= 400


def test_non_adjacent_passages_are_joined_with_an_ellipsis_and_adjacent_ones_are_not():
    a = "Nvidia earnings beat expectations on strong data-center demand across every region."
    b = "Nvidia guidance for the next quarter came in above the consensus range from analysts."
    other = "Meanwhile the city council debated the budget for the new library for several hours."
    assert build_excerpt(_page(a, b, other), "Nvidia earnings guidance") == f"{a} {b}"
    assert build_excerpt(_page(a, other, b), "Nvidia earnings guidance") == f"{a} […] {b}"


def test_a_long_paragraph_is_windowed_so_only_the_matching_sentences_come_back():
    filler = " ".join(
        f"Sentence {i} talks about nothing in particular at all today." for i in range(80)
    )
    key = "Nvidia raised its earnings guidance sharply for the coming fiscal year."
    paragraph = f"{filler} {key} {filler}"
    excerpt = build_excerpt(paragraph, "Nvidia earnings guidance")
    assert key in excerpt
    assert len(excerpt) <= 700
    assert len(excerpt) < len(paragraph) / 5


def test_identical_boilerplate_paragraphs_count_once():
    repeated = (
        "Nvidia earnings guidance disclaimer: figures are estimates and may change without notice."
    )
    excerpt = build_excerpt(
        _page(repeated, repeated, repeated, _RELEVANT), "Nvidia earnings guidance"
    )
    assert excerpt.count("disclaimer") == 1
