---
title: Web search reads its results (Experimental)
description: An experimental switch that makes Lucius read the pages behind the top search results instead of answering from search-engine snippets.
category: connectors
order: 70
audience: [user, operator]
status: beta
related: [connectors/web-search, features/settings]
keywords: [web search, experimental, page excerpts, top results, snippets, x search, twitter, tavily, firecrawl, cost]
---

# Web search reads its results (Experimental)

This page describes an **experimental** switch, off by default. Turn it on under Settings →
Developer → **Experimental** (see [Experimental features](../features/settings.md#experimental-features)),
as **Web search reads the pages behind its top results**. Everything on [Web search](web-search.md)
is true as written while the switch is off.

## What changes when it is on

A search result is normally a line or two of text from the search engine, which can be cut short
or out of date. With the switch on, each search also reads the pages behind the **top three
results** at the same moment and gives Lucius the passages that best match what was searched for
(up to about 2,500 characters per page) next to each result. He answers from what the pages say.

- A search takes a second or two longer, and never more than about five.
- A page that is slow, blocked or has nothing relevant is skipped: that result keeps its snippet
  and the search carries on.
- When an excerpt isn't enough, Lucius can read more of a page — up to its first 15,000
  characters — see [Reading a page](web-search.md#reading-a-page).
- With **Tavily** or **Firecrawl**, each page read is billed by the provider like any other page
  read (a few tenths of a cent each), on top of the search, and shows in **Settings → Cost &
  Performance**. The other providers download the pages from the machine running Open Net Worth at no extra
  charge, so those sites see its IP address and a plain `OpenNetWorth/1.0` User-Agent.
- The same rules apply to the pages as to any page Lucius reads: only public web pages, never an
  address on your private network, and page text is treated as information, never as instructions.

## X (Twitter) search

The separate **X search** tool goes away while the switch is on. To look at posts on X, ask Lucius
for them as usual: he adds the site filter to an ordinary search, and treats posts as chatter, not
fact. An Automation that was allowed to use X search is allowed to use ordinary web search.
Nothing you saved is changed, so turning the switch off brings X search back exactly as it was.

## Turning it off

Switch it off, or press **Reset**, and searches return search-engine snippets again, with no page
reads and no extra cost.
