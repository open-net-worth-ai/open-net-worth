---
title: Web search reads its top results, because the model answers from snippets 82% of the time
status: Accepted
date: 2026-10-11
deciders: Repo owner, Claude
---

# Web search reads its top results, because the model answers from snippets 82% of the time

**Behind the `search_reads_results` feature flag (default off, declared 2026-10-31, `remove_by`
2027-01-31).** Off, every search tool returns exactly what it returned before. Everything below
describes the flag ON. The rules a flag follows are in
[`2026-10-25-feature-flags-off-means-the-old-code`](2026-10-25-feature-flags-off-means-the-old-code.md);
the user-facing note is `docs/product/connectors/web-search-reads-results.md`.

## Context

A search result is a one-to-two-line snippet, often truncated or stale. Measured on the owner's real
chat history (metadata only, 2026-10-11): of 114 turns that used web search (297 searches), 93 (82%)
answered from snippets alone and never read a page — some after 10-14 searches. `web_search`'s
description already points at `fetch_page`, and the model ignores it. A nudge in prose did not change
behaviour, so the read has to be part of the tool.

## Decision

- **Every search tool reads its top results.** `web_search` and every `<provider>_search` call
  `ai/search/switch.search_results` instead of `provider.search`. Off, it is `provider.search`
  and nothing else; on, it also reads the top 3 result URLs (never more than 5) and returns the
  same flat JSON list with an `excerpt` on each row it read. This is the ONE place the flag is read
  for search (`switch.reads_results`).
- **In parallel** (`ai/search/page_reader.py`): Tavily and Firecrawl through their extract APIs, every
  other provider through `bounded_fetch.fetch_text_until`, a deadline-aware variant of
  `plain_get_fetch` (which stays untouched for `fetch_page`): the same SSRF-guarded, DNS-rebind-pinned
  GET (`core.pinned_http.pinned_urlopen`, `https_context()`, every redirect hop re-validated, body
  capped at 4 MB), but read in chunks against a wall-clock deadline, because a per-socket timeout is
  defeated by a server that sends a byte at a time, and with the extraction child killed at the same
  deadline. Every URL passes `assert_safe_outbound_url` before any fetch.
- **On its own threads.** Page reads and excerpt builds run on a dedicated pool of 4 behind a
  semaphore that frees a slot when the THREAD returns, not when the awaiting task is cancelled
  (`ai/search/bounded_fetch.py`). A read abandoned at its timeout can therefore neither outlive its
  deadline nor starve `asyncio.to_thread` for the rest of the app.
- **The model gets an excerpt, not the page.** `ai/search/excerpt.py` scores prose passages by the
  query terms they contain (rarer terms weigh more) and packs the best into 2,500 characters, in page
  order. No model call. A page with no passage matching the query yields no excerpt, so a bot-wall
  or boilerplate page never poses as an answer. `page_chars_total` says how much page exists;
  `fetch_page` returns up to its first 15,000 characters. The excerpt is run through
  `sanitize_untrusted` where it is made, capped at 2,500 AFTER normalisation (NFKC can expand a
  character: U+FDFA becomes 18), and at most 32 query terms are scored.
- **A read that fails keeps the snippet and says why** (`excerpt_unavailable`: `timeout`, `blocked`,
  `error`, `empty`, `no_relevant_text`). A row never attempted carries neither field. A failed read
  never fails the search.
- **Bounded:** 4s per page (the wall clock for the whole read), 5s for the whole batch; a page is
  scored from its first 200,000 characters; at most 3 x 2,500 = 7,500 added characters (12,500 at the hard maximum).
- **No model-facing knob.** The tool schemas are built once at import and must be main's byte for byte
  while the flag is off, so `web_search` gains no `read_top` argument and its description is
  unchanged. The depth is the `READ_TOP` constant.
- **Paid reads are accounted** when the request is SENT (the provider bills a call we stop waiting
  for), with the session and surface, in `cost_log` as the `fetch:<id>` pseudo-model, source
  `web_search_read` (`tavily` about $0.002, `firecrawl` about $0.001, the plain-GET readers nothing).
  The prices mirror `monitoring/asset_brief` (Intel's full-text fetch), which sits above `ai/search`
  and so cannot be imported; a test pins the two equal.
- **`x_search` is retired from the offer.** It was `web_search` with
  `(site:x.com OR site:twitter.com)` appended, and its snippets-only result is exactly what this
  decision replaces. With the flag on it is left out of the system-prompt catalog, `find_tools`, an
  explicit (Automation or agent) tool offer and the names a session loaded earlier
  (`catalog.hidden_tools`), and an Automation's `always_allowed` entry for it counts as an entry for
  `web_search` (`switch.with_stand_ins`). The tool and every stored value stay as they are: no
  migration, nothing is rewritten, and it still runs if called by name, so switching the flag off and
  on again is safe.
- **The stand-in never overrides the owner.** The `x_search` -> `web_search` mapping is not made
  when `web_search` has a stored Ask (`needs_approval`) or Block level, or the store cannot be
  read: the entry was written for a tool the owner never restricted, and an inherited grant must
  not lift a restriction on another.
- **One flag read per turn; an unreadable flag hides nothing.** `assistant.run` pins the retired set
  in `_skill_scope` with the rest of the turn's scope, so no offer build or dispatch re-reads the
  flag; outside a turn it is resolved on the spot. A failed read means off: nothing hidden, no
  page read, no stand-in, and `find_tools` is no wider for it.

## Consequences

### Positive
- The answer rests on page text for the sources that matter, without the model having to choose to fetch.
- One policy for every search tool, one switch point, and a flag-off run that is main's code.

### Negative
- A search takes up to 5s longer (typically 1-3s with plain GET readers), and returns up to ~2k more
  tokens at the default depth.
- Tavily and Firecrawl bill one extract per page read: a default search now costs up to three extra reads.
- A read abandoned at its timeout can hold one of the 4 reader threads for up to about 2s longer (one
  socket operation); at most 4 reads run at once, so a burst of searches queues rather than fans out.
- With the flag on, the model is not told in the tool description that results carry excerpts or how to
  search X; the result is self-describing and `site:x.com` is common knowledge to it.

### Follow-ups
- The `fetch_page` tool itself is not cost-logged (only Intel's reads and these are); out of scope here.
- `fetch_page` itself still uses `plain_get_fetch` (60s socket timeout, no wall clock, the shared
  executor); hardening it is a separate decision.
- At retirement: delete the snippets-only path, `x_search`, and make the excerpt description and a
  `read_top` argument part of the schema.

## Alternatives considered

- **Strengthen the description nudge** — rejected: one already exists and is ignored.
- **Return whole pages** — rejected: 15,000 characters per page x N would dominate the context; a
  query-focused excerpt carries the relevant part and `fetch_page` carries more.
- **Ask the model to summarise each page** — rejected: a model call per result is slow and costly, and
  puts a model between the page and the answer.
- **A provider-side answer, e.g. Tavily `include_raw_content`** — rejected: not every provider has
  one, and one reader through `fetch_page` keeps the SSRF guard and the cost accounting in one place.
- **A `read_top` argument so a caller can skip the reads** — deferred to the flag's retirement: it
  would change the tool schema while the flag is off.
- **Delete `x_search` and rewrite stored Automation entries** — rejected for as long as the flag
  lives: a conversion would break the flag-off path; hiding it is reversible.

## Agent provenance

- **Decided by:** Claude, on the repo owner's measurement and brief.
- **Lane / branch:** `search-reads-results`.
- **Alternatives weighed:** a stronger nudge; whole pages; model summaries; provider raw content; a knob; deleting `x_search`.
- **Driving artifacts:** the 2026-10-11 chat-history measurement (114 turns, 297 searches, 21 turns read a page).
- **Why this way (one line):** the model ignores a prompt nudge to read pages, so the search tool does the read and hands back the relevant passages.
