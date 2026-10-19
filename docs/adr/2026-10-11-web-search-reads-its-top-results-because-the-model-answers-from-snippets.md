---
title: Web search reads its top results, because the model answers from snippets 82% of the time
status: Accepted
date: 2026-10-11
deciders: Repo owner, Claude
---

# Web search reads its top results, because the model answers from snippets 82% of the time

## Context

A search result is a one-to-two-line snippet, often truncated or stale. Measured on the owner's real
chat history (metadata only, 2026-10-11): of 114 turns that used web search (297 searches), 93 (82%)
answered from snippets alone and never read a page — some after 10-14 searches. `web_search`'s
description already points at `fetch_page`, and the model ignores it. A nudge in prose did not change
behaviour, so the read has to be part of the tool.

## Decision

- **Every search tool reads its top results.** `web_search` and every `<provider>_search`
  share one path (`ai/tools/misc.search_and_read`): search, read the top `read_top` result URLs
  (default 3, max 5, `0` = snippets only), sanitize, return. The result stays a flat JSON list.
- **Through the provider's own `fetch_page`**, in parallel (`ai/search/page_reader.py`): Tavily and
  Firecrawl use their extract APIs, the others the SSRF-guarded pinned GET. Every URL passes
  `assert_safe_outbound_url` before any fetch.
- **The model gets an excerpt, not the page.** `ai/search/excerpt.py` scores prose passages by the
  query terms they contain (rarer terms weigh more) and packs the best into 2,500 characters, in page
  order. No model call. A page with no passage matching the query yields no excerpt, so a bot-wall
  or boilerplate page never poses as an answer. `page_chars_total` says how much page exists;
  `fetch_page` returns the rest.
- **A read that fails keeps the snippet and says why** (`excerpt_unavailable`: `timeout`, `blocked`,
  `error`, `empty`, `no_relevant_text`). A row never attempted carries neither field. A failed read
  never fails the search.
- **Bounded:** 4s per page, 5s for the whole batch; at most 5 x 2,500 = 12,500 added characters (7,500
  at the default).
- **Paid reads are accounted** through the same `record_paid_fetch` Intel's full-text fetch uses
  (`fetch:<id>` pseudo-model, source `web_search_read`).

## Consequences

### Positive
- The answer rests on page text for the sources that matter, without the model having to choose to fetch.
- One policy for every search tool; the excerpt is sanitized in the same pass as title/content.

### Negative
- A search takes up to 5s longer (typically 1-3s with plain GET readers), and returns up to ~3k more
  tokens at the default depth.
- Tavily and Firecrawl bill one extract per page read (about $0.002 and $0.001): a default search now
  costs up to three extra reads.
- A page that times out leaves its fetch thread running until the pinned GET's own 60s socket timeout.

### Follow-ups
- The `fetch_page` tool itself is not cost-logged (only Intel's reads are); out of scope here.
- A per-call timeout for `plain_get_fetch` would let an abandoned read release its thread sooner.

## Alternatives considered

- **Strengthen the description nudge** — rejected: one already exists and is ignored.
- **Return whole pages** — rejected: 15,000 characters per page x N would dominate the context; a
  query-focused excerpt carries the relevant part and `fetch_page` carries the rest.
- **Ask the model to summarise each page** — rejected: a model call per result is slow and costly, and
  puts a model between the page and the answer.
- **A provider-side answer, e.g. Tavily `include_raw_content`** — rejected: not every provider has
  one, and one reader through `fetch_page` keeps the SSRF guard and the cost accounting in one place.
- **Read on every search, no knob** — rejected: `read_top=0` lets a caller that only wants links skip
  the latency and the cost.

## Agent provenance

- **Decided by:** Claude, on the repo owner's measurement and brief.
- **Lane / branch:** `web-search-reads-top-results`.
- **Alternatives weighed:** a stronger nudge; whole pages; model summaries; provider raw content; no knob.
- **Driving artifacts:** the 2026-10-11 chat-history measurement (114 turns, 297 searches, 21 turns read a page).
- **Why this way (one line):** the model ignores a prompt nudge to read pages, so the search tool does the read and hands back the relevant passages.
