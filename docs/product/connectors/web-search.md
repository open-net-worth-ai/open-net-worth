---
title: Web search
description: Let Lucius search the web and read pages — works out of the box with DuckDuckGo; add a Tavily, Perplexity, Serper, Google or Firecrawl key for better results.
category: connectors
order: 50
audience: [user]
status: stable
related: [lucius/permissions, connectors/external-mcp, features/intel]
keywords: [web search, duckduckgo, tavily, perplexity, serper, google, programmable search, firecrawl, fetch page, api key, default provider, setup]
---

# Web search

Lucius can search the web and read a specific page when a question needs current information — a
company's latest news, a fund's fee schedule, a public filing. The same search also powers
background work: Intel's news scans and the briefs it writes about your holdings.

**It works out of the box.** DuckDuckGo needs no account or key and is on from the start, so you
only need this page if you want a different search provider.

## Choosing a provider

| Provider | Good for | Key from |
|---|---|---|
| **DuckDuckGo** | Free, no setup. Fine for occasional lookups; can be rate-limited under heavy use. | No key needed |
| **Tavily** | Search built for AI — returns clean page text. A good all-round upgrade. | [app.tavily.com](https://app.tavily.com/) |
| **Perplexity (Sonar)** | Answers the question directly and lists the sources it used. | Your Perplexity account → **Settings → API** ([perplexity.ai/settings/api](https://www.perplexity.ai/settings/api)); needs API credit |
| **Serper** | Google's results, fast and low-cost. | [serper.dev/api-keys](https://serper.dev/api-keys) |
| **Google Programmable Search** | Google's own search API. Needs an API key **and** a search engine ID. | See [Google Programmable Search](#google-programmable-search) |
| **Firecrawl** | Search plus full-page reading, including pages that only render with JavaScript. Pick it if page reads come back empty or blocked. | [firecrawl.dev/app/api-keys](https://www.firecrawl.dev/app/api-keys) |

The paid providers charge per search at their own rates; most have a free tier to start.

## Set up a provider

1. Create an account at the provider's site (links above) and copy your **API key**.
2. In Open Net Worth, open **Connectors** in the sidebar, click **Browse & add**, choose **Web**,
   click the provider's card, then **Add connector**.
3. Paste the key into **API key**. (Google also needs **Search engine ID (cx)** — see below.)
4. Click **Save**.

The first paid provider you add becomes the **default** automatically — the one Lucius uses when
you don't name a provider. With several set up, open the one you want and click **Make default**
at the bottom of its form (the provider's page shows a **Default** tag when it's the one in use).

## Google Programmable Search

Google needs two values:

1. At [programmablesearchengine.google.com](https://programmablesearchengine.google.com), create
   a search engine set to search the entire web. Copy its **Search engine ID**.
2. In the [Google Cloud console](https://console.cloud.google.com), enable the **Custom Search
   API** for a project and create an **API key** under **APIs & Services → Credentials**.
3. In the Google Programmable Search connector, paste the key into **API key** and the ID into
   **Search engine ID (cx)**, then **Save**.

## Check that it works

Ask Lucius:

> *Search the web for the latest news on the Fed's interest-rate decision.*

To try one provider specifically, name it: *"Search with Tavily for…"*. Each provider is its own
tool, so Lucius can use the one you ask for even when it isn't the default — he
uses it straight away (see [Permissions](#permissions) below).

## What a search returns

A search result is normally just a line or two of text from the search engine. So Lucius also
reads the pages behind the **top three results** at the same moment, and gets back the passages
that best match what he searched for (up to about 2,500 characters per page) alongside each
result. He answers from what the pages say, not from the search-engine snippet.

- It adds a second or two to a search, and never more than about five.
- A page that is slow, blocked or has nothing relevant is skipped: that result keeps its snippet
  and the search carries on.
- Lucius can ask for fewer or more pages (up to five) or none, and still reads a whole page when
  an excerpt isn't enough — see below.
- With **Tavily** or **Firecrawl**, each page read is billed by the provider like any other page
  read (a few tenths of a cent each), on top of the search. The other providers download the pages
  from your machine at no extra charge.

## Reading a page

Ask Lucius to read a link — *"read this page and summarise the fees: https://…"* — and he fetches
it through the default provider. Tavily and Firecrawl read the page on their own servers and
return clean text. With the others, the page is downloaded directly from the machine running
Open Net Worth.

Lucius won't fetch addresses on your private network (such as `localhost` or `192.168.…`) — only
public web pages.

## What's sent

A search sends only the search text to the provider; reading a page sends only its address
(this includes the pages read for the top results of each search).
Nothing about your accounts, balances or holdings is sent — although the search text itself may
mention a company or ticker you asked about.

Because Intel's background news scans and asset briefs also use the default provider, choosing a
paid provider as default means those searches count against its quota and bill too.

## Permissions

Searching and reading a page just run — you connected the provider, so Lucius doesn't ask again
for every search. An ordinary *"search the web for…"* uses the default provider; asking for a
named one (*"search with Tavily"*) uses that provider's own search tool.

You stay in control from the provider's connector page, under **Tool permissions**: each provider
has a search tool and a page-reading tool, and you can set either to **Ask** or
**Block**. The everyday search and page reading follow the levels of whichever provider is active
— there is nothing separate to set — so blocking Tavily's search also stops Lucius from searching
through Tavily when it's the default. Each provider has its own levels, so you can trust one more
than another — see [Permissions](../lucius/permissions.md). The pages read for a search's top
results are part of that search, so they follow the search tool's level, not the page-reading tool's.

To search X (Twitter), ask Lucius for posts about a topic; he adds the right site filter to an
ordinary search. Posts are unverified opinion, so he treats them as chatter, not fact.

To stop using a provider, open it and click **Pause**, or choose **⋯ (More actions) → Remove
connector** to delete its key.

## Where your keys are stored

On this machine only. Keys are never sent to Lucius or the AI model, never included in an export,
and left out of a sanitised backup. The **API key** field hides the saved key; you can reveal it
with the eye icon to check it.

## Troubleshooting

**"Web search is not set up. Connect a provider in Connectors → Web."** — every provider is paused
or removed. Resume DuckDuckGo or add another provider.

**"Web search provider … has no API key configured."** — that provider was chosen but its key is
missing. Open it and paste the key again.

**Searches fail with an HTTP 401 or 403 error** — the key is wrong, expired or out of credit.
Check it on the provider's site and paste it again.

**DuckDuckGo results stop coming back** — DuckDuckGo rate-limits heavy use. Wait a few minutes, or
add a paid provider and make it the default.

**A page reads back empty or blocked** — the site may need JavaScript or block automated readers.
Ask Lucius to read it with Firecrawl (*"use Firecrawl to read…"*), or add Firecrawl as a provider.

**Google returns nothing** — check **Search engine ID (cx)** is filled in and the engine is set to
search the entire web.

## Related pages

- [Permissions](../lucius/permissions.md) — setting a level per provider
- [Intel](../features/intel.md) — the news scans that also use web search
- [External MCP servers](external-mcp.md) — add other tools, such as a full browser
