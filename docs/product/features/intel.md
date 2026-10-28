---
title: Intel
description: Watch tickers and topics, set price alerts with instructions for Lucius, and get market news and signals tied to what you own.
category: features
order: 110
audience: [user]
status: beta
related: [features/investments, connectors/web-search, connectors/telegram, lucius/automations]
keywords: [intel, intelligence, watchlist, price alert, news, signals, topics, portfolio brief, pulse, buy zone]
---

# Intel

Intel keeps an eye on the market for you. It watches the stocks you own, the ones on your
watchlists and the topics you follow, and tells you when something actually matters: a move that's
unusual for that stock, a price you set an alert on, news that touches your holdings, or a stock
entering the price range you said you'd buy at. Everything is tied to your real portfolio, not a
generic news feed.

Open **Intel** in the sidebar (the page is titled **Intelligence**).

**Beta:** fully working, but its news and alert pipeline is still being tuned. Treat an alert as a
prompt to look, not a complete picture — the Intel page itself is the full record.

## Set it up

Intel starts working on its own for the stocks you hold. These steps add what you want to watch
and get alerts to your phone.

### 1. Watch some tickers

1. Open **Intel** and click **＋ New list** (beside the tabs). In **Watchlists & monitors**, click
   **New watchlist**, and rename it — for example "Semis".
2. Close the pop-up. Your list now has its own tab.
3. On the list's tab, type a ticker into **Add ticker — e.g. NVDA** in the side panel and press
   Enter. Add as many as you like.

For crypto, use the price feed's pair symbol — `BTC-USD`, not `BTC` (a bare `BTC` is a US-listed
fund, not the coin).

### 2. Follow a topic (optional)

For a story rather than one stock — an IPO, a war, interest rates:

1. Open the **Topics & macro** tab.
2. Type it into **Follow another topic** — e.g. *"SpaceX IPO and relevant stocks"* — and click
   **Add via Lucius ✦**. Lucius sets up the search terms and related tickers.
3. Click the topic to edit its name, its **Instructions for Lucius** (what to watch for and flag),
   and its tickers.

### 3. Set a price alert

1. Click the bell 🔔 on a ticker's row (or the **›** beside the ticker in the side panel).
2. Click **New alert** — or, under **Not sure what to set?**, click **Suggest** and Lucius proposes
   levels based on your position.
3. Choose **Target price** (**Falls below** / **Rises above** a price) or **% move** (**Falls by** /
   **Rises by** a percentage in a day).
4. Optional: a **Name**, a **Cooldown** (or **Turn off** after it fires once), **Notifications**
   (where this alert goes), and **Additional instructions** — what Lucius should check when it
   fires, e.g. *"check whether the move is news-driven"*.
5. Click **Create alert**. The ticker's alerts screen shows every level on a 52-week price scale, each with its own
   on/off switch.

### 4. Say why you're watching (optional, recommended)

Open the ticker's page and click **Add** (or **Edit**) on the **Your Thesis** card, or fill in
**Why you're watching** in its alerts screen — both edit the same thesis:

- **Why you'd buy it** (**Why you own it** for a stock you hold) — one line on why you care, e.g.
  *"Leader in AI chips, but too expensive today"*.
- **Buy at or under** (and optionally **…but not under**) — your buy zone.
- **Fair value**.

Intel then flags *"Entered your buy zone"*, *"12% below your fair value"* or *"Earnings in 3 days
— check your thesis"*, and shades your buy zone on the ticker's trend line.

### 5. Get alerts outside the app

Alerts always show in Intel. To get them on your phone or by email:

1. Set up a channel in **Connectors** — [Telegram](../connectors/telegram.md),
   [Email](../connectors/email-ingestion.md) or a webhook.
2. Open **Settings → Intelligence → Alerts** and tick which channels each kind of alert goes to:
   **Price alerts** (the default for new alerts — each alert can override it), **Watch alerts**
   and **News analyses**.

**Check it worked:** open any alert's **Notifications** menu — your channels are listed there. A
channel that isn't set up yet reads *"… set up in Connectors"*.

### 6. Better news coverage (optional)

Intel reads news feeds on its own. Adding a [web search connector](../connectors/web-search.md)
lets its news scans search the web too. Explained alerts and Lucius's reads need an AI provider
([Connecting an AI provider](../getting-started/connecting-an-ai-provider.md)).

Want a daily summary of your money and portfolio? Start the **Daily briefing** template in
[Automations](../lucius/automations.md).

## Find your way around

The tabs are **My holdings**, one tab for each of your watchlists, and **Topics & macro**.

| Tab | Shows |
|---|---|
| **My holdings** | The **Holdings brief** (today's move, what led and dragged, how you compare with your benchmark), then portfolio-wide stories such as concentration or currency risk, then one row per holding. |
| **A list of yours** | The **Watchlist pulse** for that list — the day's most unusual move, what's scheduled this week, best and worst performers — then one row per ticker. Tickers you own are tagged **Owned**. |
| **Topics & macro** | The topics you follow, central banks, currencies, the economy, and news about companies you don't own or watch. Filter by **Risk**, **Alerts**, **Opportunities**, **Macro**, **Topics** and **Assets**. |

A story shows on every tab it belongs to — a story about MSFT appears on My holdings if you own it
and on your "Tech" list. Marking it read or dismissing it anywhere does so everywhere.

**Each ticker row** shows:

- **Ticker** — price and name. Tap it to open the asset's page.
- **Trend** — a small chart over **1M**, **3M** or **1Y**. The dashed line is what it's compared
  with: your benchmark for stocks you own, the stock's sector fund (e.g. XLK for technology) for
  stocks you only watch. Green shading means ahead, red means behind.
- **Next** — the next earnings or ex-dividend date within 60 days.
- **Today** — today's move, and how unusual it is for this stock (*"11× usual · company"*).
- **🔔** — the ticker's price alerts and *Why you're watching*.

Under each row, short flags say what changed (hover or tap one for an explanation), and the most
urgent story sits one line below — tap it to unfold all of them. On My holdings you also see each
holding's share of your investments and its effect on your day.

The **sort menu** in the status bar orders rows by **Most urgent**, **Biggest move today**, **Most
ahead or behind**, **Closest to 52-week high** or **Next event soonest**. The status bar also has
**Mark all read** and the archive of dismissed and resolved stories.

To manage everything in one place, click **＋ New list**: **Watchlists & monitors** lists every
watchlist (rename, **Mute**, delete), **All monitors** (**Price alerts** — with **Triggered now**
and **Disabled** filters — and **Topics**), **News sources**, and **Monitoring & alerts**, which
opens Settings → Intelligence.

## What Intel watches for

- **Unusual moves for that stock.** A move is measured against the stock's own usual daily swing
  over the last 60 trading days, so a calm stock is flagged at 2% while a volatile one isn't at 6%.
  Without enough history it falls back to 5% for a stock, 6% for a commodity, 10% for crypto.
- **Company or market?** Each big move is labelled **Company-specific**, **Mostly the market** or
  **Moving with its sector**.
- **Other flags:** a 52-week high or low (or near one), 25% or more below the high, a 50- or
  200-day average cross, heavy volume, a run of up or down days, earnings within a week, and a
  price within 2% of one of your alerts.
- **Likely stock splits** are recognised (e.g. −75% on normal volume), so you don't get a false
  crash alert.

**Explained alerts.** When something *material* happens to a watched ticker — a company-specific
move of 2.5× its usual size, entering your buy zone, falling 10% below your fair value, or a
52-week high or low on heavy volume — Lucius writes a short explanation: what happened, why (with
sources), what it means for you given your thesis and what you own, and points worth weighing. At
most 8 a day, and only when there's a sourced reason. **These are the only watch alerts sent to
your channels**; other flags stay in the app.

**Lucius's read.** Other live watch stories (earnings next week, a 200-day cross, a big but
ordinary move) get a sentence or two on what it means and what to watch next. Reads stay in the
app, are never pushed, and are capped at 20 a day by default.

**How far Lucius's opinion goes** — set in **Settings → Intelligence → AI analysis**:

- **Information only** (default): explanations and considerations, never a side.
- **Show a lean**: adds a bullish, bearish or neutral lean with its reason.
- **Allow suggestions**: adds at most one suggestion, framed against your own thesis and buy zone.

Any opinion is labelled *"Lucius's view, not financial advice"*, and every figure is checked
against the data.

**Story status.** Each story is **Needs attention** (a recent story with a real decision in it),
**Active** or **Monitoring**. One older than a week becomes **Needs recheck**, and its analysis is
marked *Historical analysis · verify before acting*. Market roundups and passing mentions don't
create stories.

## Asset pages: why a stock moved

Click any ticker to open its page. Under the chart, **Notable Price Movement** explains each
trading session: the close, the % change, the after-hours price, and a short paragraph on why it
moved, with the articles it's based on. While the market is open the top row updates live. A
session with no coverage says *"No notable coverage found"* instead of guessing.

The **Analysis** tab opens with **Key Issues**: the bull and bear case on each big question for
the stock. Lucius writes these in the background after you open the page, so a ticker you haven't
looked at before shows **Generating** until they arrive, even if you click **Refresh** in the
meantime. Refresh updates the news, facts and peers. If nothing is being written, you see
*"No key issues yet"*; click **Refresh now** to try again.

### Your Thesis

Every asset page has a **Your Thesis** card under **Your Position**: why you own the stock (or
would buy it) and the prices that matter to you. Click **Edit**, change what you like, and **Save
thesis**.

- For a stock you **own**, the thesis also has a **role** — **Core** (the long-term base of your
  portfolio), **Satellite** (an active bet around it) or **Speculative** (a small, high-risk bet);
  hover the **i** next to it for a reminder — plus **Thesis breaks if** (the business fact that
  would prove you wrong), **Stop below** and **Trim above**.
- For a stock you only **watch**, it's your reason, buy zone and fair value.

Prices are in the stock's own quote currency — the one its page shows, named once above the
price fields, e.g. *Prices in EUR* (crypto is quoted in USD). A new thesis for a stock you own starts as **Core**; change it
if it's a smaller bet.

A bar shows today's price against your levels, and each level lists how far away it is. The thesis
belongs to the stock, not to an account or a watchlist: it's shared by every account that holds
it, and it stays when you sell or stop watching.

## Run a scan now

Intel scans on a schedule. To scan now, click **Scan** at the top of the page, then **Scan now**.
A scan uses your web-search quota.

## What Lucius can do here

- Create, change or delete watchlists, topics and price alerts.
- Read or write your thesis for a stock — *"my thesis on ACME breaks if margins fall below 45%; stop at 120"* — after you confirm the values.
- Run a scan and summarise what it found.
- Explain your portfolio's risk signals and the news behind your biggest movers.
- Add, remove, turn on or off news sources.
- Dismiss a story you've dealt with.

Try: *"Add NVDA, AMD and AVGO to a new watchlist called Semis and alert me if any falls 8% in a
day."*

## Settings

Everything lives in **Settings → Intelligence** (or **Monitoring & alerts** in Watchlists &
monitors):

- **Monitoring** — **Monitoring active** (pause everything), **Price check** (every minute by
  default), **News scan interval** (every 12 hours by default), **RSS freshness**, **Outlet
  headlines**, and **Web search** with its budget per scan.
- **Alerts** — which channels each kind of alert goes to.
- **AI analysis on watched tickers** — **Explained alerts**, **Lucius's read on watch stories**,
  **Reads per day**, and **How far Lucius's opinion goes**.
- **Pulse summaries** — how much the **Holdings brief** and **Watchlist briefs** say, from 1
  (a headline) to 5 (a short narrative Lucius writes). Levels 1–4 cost nothing; 5 uses your AI
  provider.
- **Advanced** — crash thresholds for equities, commodities and crypto, and news-spike tuning.
  Only change these if the defaults are too noisy or too quiet.

## Troubleshooting

**No alerts on my phone** — check that the channel is set up and enabled in **Connectors**, and
ticked for that alert kind in **Settings → Intelligence → Alerts**. Remember only explained alerts
are pushed for watch signals; the rest stay in the app.

**A ticker has no price row or no flags** — the price feed doesn't recognise the symbol. Use the
feed's symbol (`BTC-USD`, `D05.SI`). A just-added ticker's trend and sector comparison appear after
a minute or two.

**"Last scanned" looks old** — check **Monitoring active** is on in Settings → Intelligence, then
click **Scan**. If scans keep failing, see [Logs](../operations/logs.md).

**A topic surfaces little** — give it clearer instructions and add related tickers.

**Bonds and options aren't in the Holdings pulse** — their prices don't come from the price feed
it reads.

## Related pages

- [Investments](investments.md)
- [Web search](../connectors/web-search.md)
- [Telegram](../connectors/telegram.md)
- [Automations](../lucius/automations.md)
