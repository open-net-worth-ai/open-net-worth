---
title: Price sources — Yahoo Finance & CoinGecko
description: Where live and historical prices come from, how to add an optional CoinGecko API key, and what is sent out to get a price.
category: connectors
order: 10
audience: [user]
status: stable
related: [troubleshooting/prices-arent-updating, features/investments, operations/security-and-privacy]
keywords: [prices, yahoo finance, coingecko, quotes, market data, crypto, api key, demo key, pro key, rate limit]
---

# Price sources — Yahoo Finance & CoinGecko

Every price in the app — stocks, ETFs, indices, exchange rates and crypto — comes from **Yahoo
Finance** or **CoinGecko**. Both are built in and always on: there is nothing to set up, and they
can't be paused or removed.

- **Yahoo Finance** prices every stock and ETF, and the major cryptocurrencies priced in US
  dollars. No account or key.
- **CoinGecko** prices any crypto Yahoo can't, and powers crypto search and coin facts. It works
  without a key on a small shared allowance; an optional free key raises it.

You'll find both under **Connectors → Data sources**.

## Add a CoinGecko API key (optional)

Worth doing if you hold several cryptocurrencies Yahoo doesn't cover, or crypto prices sometimes
show as stale.

1. Create a free account at [CoinGecko](https://www.coingecko.com) and open its developer
   dashboard to create a **Demo** API key (or use your **Pro** key if you pay for one). Copy it.
2. In Open Net Worth, open **Connectors**, click **CoinGecko**, then **⋯ (More actions) → Edit
   configuration**.
3. Under **Key tier**, choose **Demo** or **Pro** to match your key.
4. Paste the key into **API key** and click **Save**.

**Check it worked:** the form shows how many calls were made this minute against the allowance
(for example *3 / 25 calls this minute* with a Demo key, out of 4 without one) and a status badge
— **Available** means the last call succeeded.

To remove the key, click **Clear saved key**, then **Save**.

## Check that prices are flowing

Open **Connectors**, click **Yahoo Finance** or **CoinGecko**, then **⋯ (More actions) → Edit
configuration**. Each shows when it last answered (*Last successful 2 minutes ago*) and a status:

| Status | Meaning |
|---|---|
| **Available** | The last call worked. |
| **Not checked yet** | No successful call has been recorded yet. |
| **Cooling down** / **Recovering** | The provider pushed back (usually too many requests); the app is pausing and will retry on its own. |
| **Unavailable** | Recent calls failed. |

Or just ask Lucius: *"What's AAPL trading at right now?"*

## Crypto: which one goes first

For the major cryptocurrencies priced in US dollars, Yahoo Finance is asked first and CoinGecko is
the fallback. The switch **Use Yahoo Finance first for supported major crypto** controls this —
open **Connectors → Yahoo Finance → ⋯ → Edit configuration**. Leave it on; turn it off only if
Yahoo's crypto prices look wrong and you want CoinGecko to go first for a while. Your holdings
and saved charts don't change either way.

## How fresh prices are

- A live quote is reused for about a minute before it's fetched again.
- Charts and price history are kept for up to 30 minutes, depending on the time range (the 1-day
  chart refreshes every minute).
- In the background, the app checks the prices of your holdings and watchlist every minute, even
  when nobody is looking. Change that under **Settings → Intelligence → Monitoring → Price
  check**.

## Shares quoted in pence

Yahoo quotes most London-listed shares in **pence** (and Johannesburg in cents, Tel Aviv in
agorot). The app converts those quotes to pounds (rand, shekels) as they arrive, so a share
trading at 26.5p is valued at £0.265, never £26.50. Listings Yahoo already quotes in pounds,
such as many London ETFs, are left as they are. Price history saved in pence by an older
version is corrected automatically the next time the app starts.

## What leaves your machine

Only the **ticker symbols** (and, for crypto, coin names) being priced — never balances, holdings,
quantities or anything else about your book. The provider sees *"what is AAPL worth?"*, never
*"what does this person own?"*. The requests only ever go to Yahoo's and CoinGecko's own
addresses.

Your CoinGecko key is stored on this machine only, never sent to Lucius or the AI model, and left
out of exports and sanitised backups.

## Troubleshooting

**Prices look stale or show as missing** — see [Prices aren't
updating](../troubleshooting/prices-arent-updating.md).

**A coin has no price** — it may not be listed on Yahoo, and CoinGecko's free allowance may be used
up for the moment. Add a CoinGecko Demo key (above), or wait a minute and refresh.

**A London share is valued 100× too high** — update the app and restart it; the pence
conversion and the history repair run on their own.

**CoinGecko shows Cooling down** — too many requests in a short time. The app backs off and retries
by itself; a Demo key makes this much rarer.

**"Could not save — try again."** — the setting didn't save; check the app is still running and
try again.

## Related pages

- [Prices aren't updating](../troubleshooting/prices-arent-updating.md) — fixing stale prices
- [Investments](../features/investments.md) — where prices show up
- [Security and privacy](../operations/security-and-privacy.md) — what else leaves this machine
