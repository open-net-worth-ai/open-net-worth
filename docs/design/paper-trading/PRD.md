# Paper Trading: PRD

Date: 2026-11-15 · Status: draft for the owner's approval · Companions: [`RFC.md`](RFC.md)
(technical solution), [`mockups.html`](mockups.html) (UI design)

## Problem

Open Net Worth already covers everything around a trade except trying one out with fake money:

- **Ideas.** Investment theses (buy zone, fair value, breaks-if), watchlists, Intel stories.
- **Copy Trading.** Follow a 13F fund, a House member or an ARK ETF. Auto-copy hands real orders
  to a broker through Lucius.
- **Real orders.** Lucius can place them through the owner's broker MCP (Alpaca, IBKR, Kraken),
  each behind an approval card.

Without a practice step, the owner either trusts an idea with real money or keeps a spreadsheet.
The riskiest path is the newest one: an Automation where Lucius copies a portfolio or follows
rules every week. Today the first proof that such an Automation works costs real money.

Alpaca's data points the same way: people who practise first mostly go live within 30 to 60
days. Practice is a step toward trading, not a game.

## Who it is for

The owner of the book: a self-hoster who tracks their whole net worth across stocks, funds,
crypto, metals, bonds, cash in several currencies and illiquid assets. They want to:

1. **Test an idea before buying.** "If I'd bought NVDA in my buy zone last month, where would I
   be?" "Would a gold and T-bill sleeve have made my book calmer?"
2. **Let Lucius prove itself.** Run a copy Automation, or a rule such as "buy anything that
   enters its buy zone, sell below its stop", on paper for weeks before it gets a real account.
3. **Follow a famous portfolio forward.** Copy Berkshire on paper the day each 13F lands, with
   real fills and the real 45-day lag, and compare that with their own book.
4. **Try a change to their own book.** "What if I sold my tech overweight into VT and wrote
   covered calls on what's left?", starting from a copy of what they hold today.
5. **Learn an instrument before using it.** Options, bonds and futures behave differently from
   shares, and a paper portfolio is a safe place to find out how.

## Every investable asset

The owner's requirement is that paper trading support **every kind of investable asset**. The
design meets it in two ways:

- **Each kind is one adapter.** An adapter defines how the kind is priced, when it trades, how
  an order fills, and what happens to a holding over time. Adding a kind means adding an
  adapter, not a feature (see RFC §4).
- **Nothing is untradeable.** An asset with no price feed (private shares, a property, a fund
  with no ticker) is traded at prices you enter, the same way a manual asset works in the real
  book. The app always says so.

| Kind | Examples | Priced from | When it trades | How an order fills | Over time | Phase |
|---|---|---|---|---|---|---|
| **Stocks, ETFs, ETPs, REITs, ADRs** on any exchange the app can price | AAPL, VWCE.DE, 7203.T, O | Live quote (Yahoo) | That exchange's session, in its own time zone. Pre/post-market only if allowed | Bid/ask when the quote has them, else the last price plus or minus slippage | Dividends, splits, delisting (holding frozen at its last price) | 1 |
| **Mutual funds** (priced once a day) | VTSAX, UK OEICs | Daily NAV | Orders before the fund's cut-off (default 16:00 New York) | That day's NAV, published after the close (forward pricing). The order says "fills at tonight's price" | Distributions, paid as cash or reinvested | 1 |
| **Crypto**, including stablecoins | BTC, ETH, SOL, USDC | Live quote (CoinGecko, Yahoo) | Around the clock | Last price plus or minus slippage, to 8 decimals | None (staking rewards are not modelled) | 1 |
| **Physical precious metals** | Gold by the gram, ounce or tael | Spot price × unit | Spot hours (Sun evening to Fri evening, New York time) | Spot plus a **dealer premium** (default: buy +3%, sell −1%) | Optional storage fee, % a year | 1 |
| **Commodities through funds** | GLD, SGLN, USO | As stocks | As stocks | As stocks | As stocks | 1 |
| **Currencies** | Hold EUR, JPY or SGD cash | Live FX rate | Around the clock, Monday to Friday | Live rate plus or minus an **FX spread** (default 0.1%) | Interest per currency, if the cash yield is set | 1 |
| **Indices, as synthetic trackers** | S&P 500, Nikkei 225 | Index level | The index's session | The level, with no fee. Labelled **Synthetic**: no fund tracks it exactly, and a price index pays no dividends | None | 1 |
| **Private and manual-priced assets** | Pre-IPO shares, a rental flat, art, an unlisted fund | Prices you enter, or a **proxy** you pick (for example a REIT index for a property) | Any time | At the price you enter | Value moves only when you record a mark or the proxy moves. Labelled **Your price** | 1 |
| **Cash yield** | Idle cash, money-market sweep | A rate you set, or the 13-week T-bill yield | — | — | Interest builds daily and is paid monthly | 2 |
| **Bonds and bills** | US T-bills and notes, government and corporate bonds by ISIN | A quote where the source has one. Otherwise priced from the bond's terms and the Treasury curve plus a spread you set, labelled **Modelled** | Weekdays | Clean price plus accrued interest | Coupons paid on schedule, principal repaid at maturity | 2 |
| **Options** on stocks, ETFs and indices | NVDA Jan 2027 200 call | Option-chain bid/ask. Without a chain, priced from the underlying with Black-Scholes and one-year volatility, labelled **Modelled** | The underlying's regular session | Buy at the ask, sell at the bid. A modelled price gets a default spread | At expiry, in the money is exercised or assigned at intrinsic value, out of the money expires worthless. A short option must be covered by shares or reserved cash | 2 |
| **Short stock and margin** | Short TSLA, 2× leverage | As stocks | As stocks | As stocks | Borrow fee and margin interest charged daily. A margin call closes positions if equity falls below maintenance | 3 |
| **Futures** | ES, GC, CL, BTC futures | Front-month continuous price | The contract's session (nearly 24/5) | Last price plus or minus slippage, × the contract multiplier | Marked to market daily into cash, margin held, rolled to the next contract at expiry (the roll cost is shown) | 3 |
| **Uncovered short options** | Naked puts and calls | As options | As options | As options | Needs the margin model from phase 3 | 3 |

Not paper-tradeable, by nature:

- **Unvested equity compensation.** RSUs and options are granted, not bought. A vested share is
  a stock and can be paper-traded as one.
- **Fixed deposits.** Cash yield in phase 2 covers the same idea.

The phases order the work; they do not drop any kind. Each phase ships complete, with its
adapters, tests and help text, behind the same flag.

## Where it lives

**Intelligence → Paper Trading**, a tab right after **Copy Trading** (owner's decision, 2026-11-15).

| Option | Verdict | Why |
|---|---|---|
| **Intelligence tab** | **Chosen** | Intelligence is what you watch, not what you own, and Investments stays what you hold (ADR `2026-11-10-followed-portfolios-are-information-copying-is-an-automation`). A paper portfolio is an idea you watch play out. It sits beside Copy Trading, its main source. |
| Investments sub-tab | Rejected | Investments' summary, analysis and risk mean *your money*. A paper tab beside Holdings invites "is this real?" and pulls paper figures toward net-worth surfaces. |
| A Real/Paper switch over the whole app (Webull, eToro) | Rejected | Every investment screen would need two data sources. Mixing up the two modes is the most common way people place a real order by mistake. With Lucius able to place real orders, a global mode is the worst place to put that risk. |
| Paper as an account type | Rejected | The seven account types are the only classification axis, and every account counts in net worth. A paper account would need exceptions in net worth, cash, budgeting, Intel risk and the Dashboard. |
| A new top-level tab | Not now | The primary navigation is full and this is an occasional tool. Promote it later if it gets used a lot. |

Paper Trading can be started from wherever ideas start. Each entry point opens the order ticket
or the new-portfolio dialog already filled in:

- **Asset page**: a **Paper trade** button beside **Add holding**. The ticket shows the
  thesis's view ("in your buy zone"). For an optionable stock, the ticket also offers its
  options. The Position table lists the asset's paper positions under the real ones. Each is on
  its own row with the Paper chip and "not in your totals", and none is added into a real
  figure. This is the one place paper shows up on a real-money screen.
- **Copy Trading portfolio page**: **Paper-copy** beside **Auto-copy**.
- **Thesis card**: **Test on paper** when the ticker has a buy zone.
- **Lucius**: "paper trade 20 NVDA", "paper-sell a covered call on my paper AAPL", "start a
  paper portfolio that copies Pershing Square", "how is my gold sleeve doing?".
- **Automations**: two new templates, **Paper-copy a followed portfolio** and **Run a strategy on
  paper**.
- **Command palette**: "New paper portfolio", "Paper trade…".

## The experience

### How it looks

Paper Trading is a feature of the platform, not a mode. There is no Real/Paper switch, no
banner and no special background (owner's decision, 2026-11-15).

- **The object is marked, not the app.** A paper portfolio carries one small **Paper** chip
  wherever it appears.
- **"Paper" is written into the labels**: Paper value, Paper order, Place paper buy.
- **Paper pages use the same screens as Investments**: summary, chart, holdings.
- **The app stays clean.** No borders except table rows; groups are made with fills and space.
- **Short copy.** A figure gets a number and a short label ("Return +7.41%", "VT +3.08%"),
  never a sentence. Comparisons go in the chart legend; detail (fill basis, quote time, reasons)
  is one click away, on the order or in Activity.
- **One new accent, gold (`--paper`, #d8c79a), used in two places only**: the chip and the
  main paper action button (owner's decision, 2026-11-15).

See `mockups.html` (v2) and `VISUAL_RESEARCH.md`.

### 1. Start a paper portfolio

**New paper portfolio** opens a dialog:

- **Name**. Defaults to "Paper portfolio 1". From Copy Trading it is "Berkshire Hathaway
  (paper)".
- **Start with**, one of:
  - **Cash only**. Presets are 10,000, 100,000, or **the same as your investments** (rounded,
    e.g. 240,000). Any amount can be typed.
  - **A copy of my holdings**. Today's holdings at today's prices, every kind included: shares,
    funds, crypto, metals, options, bonds and manual assets at their current marks. Cash is not
    copied unless you add it. Cost is set to today's price, so profit and loss starts at zero.
  - **A followed portfolio**. Its current weights, bought with the cash you choose at today's
    prices.
- **Home currency**. Defaults to the base currency and cannot change after creation. Other
  currencies can be held as cash.
- **Benchmark**. Defaults to the preferred benchmark.
- **Realism** (collapsed, **Fill settings**), with defaults:

  | Setting | Default |
  |---|---|
  | Slippage | 5 bps |
  | Commission | 0, fixed or % per kind (e.g. 0.65 per options contract) |
  | FX spread | 0.1% |
  | Metal dealer premium | +3% / −1% |
  | Fractional shares | On |
  | Pre/post-market fills | Off |
  | Convert currency automatically when buying abroad | On |

  Phase 2 adds **cash yield**. Phase 3 adds **margin**: off, 2×, or 4× intraday.

The portfolio opens immediately. If it started from holdings or a followed portfolio, those
first buys show in Activity as **Opening**.

### 2. Trade

**Trade**, or a holding's row, opens the **order ticket**. It is a side sheet on desktop and a
bottom sheet on a phone.

1. **Buy / Sell**, then the **asset**. The asset search covers every kind: tickers, coins,
   metals, currencies, indices, bonds by ISIN, and **Add a manual asset**. Once a kind is
   picked, the ticket shows only what that kind needs:

   | Kind | The ticket asks for |
   |---|---|
   | Stocks, ETFs, crypto, indices | **Shares** (or coins) or **Amount**; **Market**, **Limit** or **Stop**; **Good for** today or until cancelled |
   | Mutual funds | **Amount** or **Units**. No order type: funds fill at the next NAV |
   | Metals | **Quantity** in the asset's unit (g, oz, tael) or **Amount**. The dealer premium is shown |
   | Currencies | **Convert** from one currency to another, with an amount. The FX spread is shown |
   | Options (phase 2) | Underlying, then **expiry**, then a **strike** picked from a chain with bid, ask and how far each strike is from the price. Then **Call/Put**, **contracts**, and **Buy to open / Sell to open / Buy to close / Sell to close**. A sell to open shows what covers it ("Covered by 100 AAPL" / "Reserves 18,000 cash") |
   | Bonds (phase 2) | **Face value**; price as **% of par**; yield to maturity shown |
   | Manual assets | **Price** (yours), **quantity** and an optional **proxy** |
   | Futures (phase 3) | **Contracts**; the multiplier and the margin held are shown |

2. **Why** (optional, 280 characters) is the journal note shown on the trade.
3. **Preview**, updated live:
   - the price it would fill at, with its source and age ("Ask 187.42 · 12:31 · Yahoo");
   - any premium, spread, slippage or commission;
   - the total, and the cash after;
   - the holding's weight after;
   - the thesis's view, when there is a thesis.
4. The button names the action: **Place paper buy**, **Place paper sell**, **Sell to open on
   paper**.

One line under the button says what happens next, so nothing is discovered later:

| Situation | The line says |
|---|---|
| Market open | "Fills now at about 187.51." |
| Market closed | "Market closed. Fills at the next open (Mon 09:30 New York), at the first price we see." |
| Mutual fund | "Fills at tonight's NAV, published after 16:00 New York." |
| Limit not reached | "Waits until the price reaches 180.00. You can cancel it in Orders." |
| Modelled price | "No live option quote. Priced by a model from AAPL's price and its volatility." |

A filled order shows the app's usual toast with the Paper chip in it: "Paper buy filled: 20 NVDA
at 187.51".

### 3. Watch it play out

The **portfolio page** has:

- **Header**: the name, the Paper chip, one line saying "Pretend money at real prices. Not part
  of your net worth.", its age ("Day 23"), and where it came from ("Copies
  Berkshire Hathaway" or "Run by Lucius: Buy-zone strategy"). A **⋯** menu holds Edit, Start
  over, Archive and Delete.
- **Figures**: **Value**; **Return since start**, beside the benchmark and **your real
  investments** over the same days; **Cash** across currencies; **Open orders**.
- **Chart**: three lines: the paper portfolio, your investments and the benchmark.
- **Tabs**:
  - **Holdings**: grouped by kind, like Investments → Holdings, with Trade on each row. Option
    rows show days to expiry and moneyness. Bond rows show yield and the next coupon. A manual
    row shows "Your price · marked 12 Oct".
  - **Orders**: open and recent orders, with Cancel.
  - **Activity**: every fill, dividend, coupon, split, expiry, interest payment and deposit,
    each with its journal note and fill details.
  - **Compare**: correlation, worst fall, swings, return per risk, and overlap with your book.
    These are the figures Copy Trading's "You vs them" already shows.

The **Paper Trading** tab lists portfolios as cards. Each card shows the name, value, return
since start against the benchmark, a sparkline, its source, and its count of open orders.
Archived portfolios are under **Archived**.

### 4. Start over without losing history

**Start over** archives the current run and starts a new one with the same settings and cash.
The old run stays readable under Archived with its full history. Only **Delete** removes a
portfolio, and it asks first. Nothing expires because it went unused. Lost history after a reset
is the most common complaint about TradingView and Webull.

### 5. Graduate to real money

**Do this for real** is on a paper trade, a holding, and the whole portfolio. It opens Lucius with
a drafted request: "Buy 20 NVDA in [account] like my paper trade." Lucius places it with the
broker's own tools, behind that tool's own permission. With no broker connected, he offers the
matching form instead: Add holding, Record option, or a manual asset. The owner places the
trade themselves and records it.

Open Net Worth never turns a paper order into a real one, and no paper setting can approve a
real order.

### 6. Lucius on paper

- Lucius places, cancels and reads paper orders with tools whose names all start with `paper`.
  Every result says *simulated*.
- Lucius can read everything the paper pages show, so he can answer questions like these:
  - "How is my buy-zone portfolio doing against VT and my real book?"
  - "Which paper trades worked, and why?"
  - "Did the trades I placed in my buy zone beat the rest?"
  - "Compare my three paper portfolios."

  He reads the same figures as the screen, so his numbers always match it. On a paper page,
  "how is this one doing?" refers to that portfolio.
- Paper orders are **allowed by default**, with no approval card, because no money moves. The
  owner can switch them to Ask in Permissions.
- An Automation can run a strategy on paper. Its summary ("Placed 3 paper orders, skipped 2: …")
  lands where Automation results land today.
- **Paper-copy a followed portfolio** works like Auto-copy against a paper portfolio. The
  portfolio's holdings are exactly known, which removes the reason the in-app mirror plan was
  dropped: not knowing which shares belonged to the copy.
- Lucius never calls a paper result the owner's money. Paper value never appears in his
  net-worth answers.

## How fills work, as the user is told

This text appears on the help page and in the ticket's info popover:

> Paper orders fill at the latest price the app sees for that asset, with your slippage, spread
> or premium added: a buy pays a little more, a sell gets a little less. A real order meets a
> live order book, so it can fill at a different price, partly, or not at all. The difference
> grows with a thin market, a large order or a fast move. Prices marked **Modelled** or **Your
> price** are estimates, not quotes. Paper results are not a track record.

The per-kind rules are in the asset table above. These rules are shared by every kind:

- **Market order, market open**: fills at once.
- **Market order, market closed**: waits, then fills at the first regular-session price after
  the open.
- **Limit**: fills when a price the app sees reaches the limit. It fills at the limit, or better
  only when the price gaps through it.
- **Stop**: becomes a market order when a price the app sees reaches the stop. It can fill worse
  than the stop after a gap.
- **No fresh price**: in an open market, a quote older than 20 minutes is not fresh. The order
  waits and shows why.
- **Size**: phases 1 and 2 have no liquidity model. An order above 5% of the asset's average
  daily volume shows "A real order this size would likely move the price", and still fills.
- **Cash and cover**: without margin, a buy can spend only free cash, i.e. cash not reserved by
  open orders or short options. A sell is capped at what is held.
- **Dividends, coupons, interest**: paid gross. Withholding tax is not modelled, and the help
  page says so.
- **Corporate actions**: a split adjusts quantity and cost per share on its effective date. Any
  other action (a spin-off, a merger) freezes the holding and asks the owner to settle it with a
  manual entry. It is never guessed.
- **Currency**: buying abroad converts at the live rate plus the FX spread, and the rate is
  shown on the fill. With automatic conversion off, a buy needs cash in that currency.

## Scope

### Phase 1: spot assets

- Paper portfolios: create (cash, a copy of holdings, a followed portfolio), edit, start over,
  archive, delete. Up to 20 active.
- Stocks, ETFs, ETPs, REITs and ADRs on every exchange the app prices.
- Mutual funds, crypto, physical metals, currencies, synthetic indices, and manual-priced assets.
- Market, limit and stop orders; long only.
- Dividends, splits, freezes for other corporate actions; multi-currency cash.
- The portfolio page, the order ticket, every entry point, the Lucius tools, both Automation
  templates, **Do this for real**.
- Help page `docs/product/features/paper-trading.md` and an ADR.
- Behind the `paper_trading` flag, default off until hardened.

### Phase 2: income and derivatives you can't lose more than you put in

- **Options**: long calls and puts; covered calls; cash-secured puts; defined-risk spreads
  (verticals, collars). Includes the option-chain source, the modelled-price fallback, expiry,
  exercise and assignment.
- **Bonds and bills** from a quote or modelled from the curve: accrued interest, coupons,
  maturity.
- **Cash yield**.

### Phase 3: leverage

- The margin model: initial and maintenance requirements, margin interest, margin calls.
- On top of it: short stock (borrow fee), futures (daily mark-to-market, roll), and uncovered
  short options.

### After phase 3

- **Strategy scorecard**: one row per Automation that ran on paper, with return, worst fall,
  trades and hit rate. It answers "would I trust this Automation with money?" and can feed
  `get_forecast_scorecard`.
- **Backtest**: start a paper portfolio in the past, reusing `followed_portfolios/copier.py`.
- A liquidity model with partial fills, using bid/ask size where the source has it.

### Non-goals

- **Any real order from this feature.** There is no native order path (ADRs
  `2026-07-19-remove-alpaca-trading-half` and
  `2026-11-10-followed-portfolios-are-information-copying-is-an-automation`). Real orders stay
  with the broker's MCP tools and their permissions.
- **Paper money in any real figure**: net worth, cash on hand, budgeting, Intel risk,
  concentration, today's move, the Dashboard, the daily briefing, or a thesis's "held" state.
  The asset page's Position table shows paper positions as separate, labelled rows only. Its
  totals stay real.
- **A broker's own paper account (Alpaca paper) as the engine.** It is US-only, needs an
  account, and covers only that broker's assets. "Every kind of asset" rules it out. The owner
  can still point a broker MCP at its own paper mode; that is a separate thing.
- Leaderboards, sharing, contests; tax lots and tax reports for paper trades.

## Anti-goals: failures even if it technically works

- A paper figure in any net-worth, cash or budget number, or Lucius quoting one as the owner's
  money.
- Paper and real looking alike anywhere: a ticket, a toast, a chat card, a holdings row, a
  notification.
- A paper result that looks better than reality because of an undisclosed perfect fill or price
  model.
- An asset kind that is quietly approximated: every **Modelled**, **Synthetic** and **Your
  price** value is labelled where it is shown.
- Losing a run's history by starting over or by not using it.
- A paper order that never fills and never says why.
- A second order path that reaches a broker.

## Acceptance criteria

1. **No effect on real figures.** Creating, trading, starting over or deleting any paper
   portfolio, of any asset kind, changes none of these:
   - net worth and its history;
   - cash on hand;
   - Ready to Assign;
   - Investments figures;
   - Intel risk signals;
   - a thesis's held or watched state;
   - the My holdings watchlist.

   A property test over random operation sequences proves it.
2. **Every kind has its own tests.** Each phase-1 kind (stock, mutual fund, crypto, metal,
   currency, index, manual) has an adapter test for each of these: a quote, its session, a
   market fill, a limit fill, a closed-market wait, and its lifecycle events.
3. **Market fills are recorded in full.** With the market open and a fresh quote, a market buy
   fills within 2 seconds at the ask, or at last × (1 + slippage). The fill stores the quote's
   price, source, age, session and pricing basis (quote, modelled, synthetic or yours).
4. **Closed markets and fund cut-offs are respected.** An order placed while the market is
   closed fills at the first regular-session price after it opens. A mutual-fund order fills
   at the NAV for the cut-off it met. Crypto fills at once at any hour.
5. **Limits and stops fill only when the price is reached.** A limit buy fills only after the
   app sees a price at or below the limit, and a stop sell only after it sees a price at or
   below the stop. Until then each shows its reason. An order good for today expires at that
   session's close.
6. **Orders the portfolio can't cover are refused.** A buy over free cash, or a sell over what
   is held, is refused with the available amount.
7. **Dividends and splits are applied on their dates.** A split adjusts quantity and cost per
   share on its effective date and leaves value unchanged. A dividend pays cash on its pay date.
   Any other corporate action freezes the holding and explains why.
8. **Estimated prices are always labelled.** **Modelled**, **Synthetic** and **Your price**
   values carry their label on every row, ticket, fill and chart tooltip.
9. **Starting over keeps history.** After **Start over**, the old run is still under Archived,
   with all its orders and activity readable.
10. **Paper is always labelled.** The Paper chip appears on every paper object, wherever it
    shows up: the portfolio, the order ticket, the filled toast, the chat result card, the
    Automation run summary, and its rows in the asset page's Position table. Its labels say
    "paper" ("Paper value", "Place paper buy").
11. **Lucius keeps paper and real apart.** Every result from a paper tool includes
    `"simulated": true`. No net-worth or portfolio answer includes paper figures; an eval case
    covers this.
12. **The flag turns it all off.** With the flag off: routes return 404, tools refuse, the fill
    job does nothing, the tab and entry points are hidden, and stored data stays.
13. **Phase 2 lifecycle works.** At expiry, an in-the-money paper option settles at intrinsic
    value and an out-of-the-money one expires worthless. A bond pays its coupons and principal
    on schedule.
14. **Phase 3 margin calls work.** When equity falls below maintenance, the margin call closes
    the largest losing position first and logs why in Activity.

## Measures of success

- The owner has at least one paper portfolio with trades on 3 or more separate days in its first
  month.
- At least one Automation runs on paper for 4 or more weeks before it is pointed at a broker.
- No report of paper figures in a real-money surface, or of paper and real being confused.

## Open questions for the owner

1. ~~**Placement.**~~ **Decided 2026-11-15 by the owner: Intelligence → Paper Trading.**
2. **Lucius's default permission for paper orders.** Allow (recommended, no money moves), or
   Ask like every other write?
3. **Public demo.** Recommend allowing reads and refusing paper writes, because every visitor
   shares one book.
4. **Default starting cash.** Recommend "the same as your investments" when you have holdings,
   else 10,000.
5. **Phase order.** Options and bonds (phase 2) before leverage (phase 3)? Recommended: phase 2
   is what a long-term holder uses (covered calls, T-bills), and leverage is the riskiest model
   to get right.
