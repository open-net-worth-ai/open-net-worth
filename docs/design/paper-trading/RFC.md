# Paper Trading: technical solution

Date: 2026-11-15 · Status: draft for the owner's approval · Companions: [`PRD.md`](PRD.md),
[`mockups.html`](mockups.html)

## 1. Decision in one paragraph

A paper portfolio is a **separate simulated book**: its own tables, its own write choke, and its
own fill engine, priced from the market data the app already fetches. It is **not an order
path**. It never reaches a broker, never writes `accounts`, `transactions`, `positions` or
`lots`, and no net-worth, budgeting or Intel reader imports it. Import-linter contracts and a
property test enforce this. Every asset kind is an **adapter** behind one protocol (price, session,
fill, value, lifecycle, reservation), so supporting "every kind of investable asset" means adding
adapters, not branches. It lives in **Intelligence → Paper Trading**, behind the `paper_trading`
flag.

ADR to write at build time: `2026-11-xx-paper-trading-is-a-simulated-book-never-an-order-path`.
It amends nothing. It records two things:

- Why this is not the "second ledger fed by guesses" that ADR
  `2026-11-10-followed-portfolios-are-information-copying-is-an-automation` rejected. That
  ledger mirrored a real broker account, which the app could only guess. A paper book is the
  only authority on its own trades, and nothing about it is guessed.
- Why it is not the native order path that ADR `2026-07-19-remove-alpaca-trading-half` rejected.
  No code in this feature can send an order.

## 2. Layers and boundaries

```
api/paper_trading.py ─────────────┐   (router-level flag dependency → 404 when off)
ai/tools/paper_trading.py ────────┤
services/automations (templates) ─┤   (only these may import services.paper_trading)
scheduler.py (jobs) ──────────────┘
          │
services/paper_trading/
  service.py        apply_paper_change(change) — THE write choke, one locked_unit_of_work
  book.py           pure: events → holdings, lots, cash by currency, realised P&L, reservations
  engine.py         due orders + lifecycle → decisions → choke (fetch before the lock, decide inside)
  kinds/            one adapter per asset kind (§4); registry.kind_for(asset)
  quotes.py         batched PaperQuote per kind; reads services.prices / fx / instruments
  lifecycle.py      corporate actions, coupons, expiry, interest, maturity → event drafts
  performance.py    TWR from paper_values (+flows) via returns.twr; benchmark; your-book line
  views.py          shapes for API and tools (bounded at the tool)
  seed.py           "copy my holdings" / "copy a followed portfolio" → opening events
          │ reads only
services/prices · fx_cache · instruments · returns · portfolio_compare · lot_math · holdings_snapshot
core/options_math.py · core/bond_math.py   (new, pure, stdlib-only leaves)
db/paper_*_store.py
```

New import-linter contracts (CI-blocking, beside the existing five):

1. **Only four callers import paper trading.** `backend.services.paper_trading` may be
   imported only by `backend.api.paper_trading`, `backend.ai.tools.paper_trading`,
   `backend.services.automations` and the scheduler. Net worth, dashboard, investments_book,
   intel, briefing, returns, planning and the rest cannot see it.
2. **Paper Trading cannot reach a real write choke.** `backend.services.paper_trading` must not
   import `services.ledger`, `services.lots`, `services.positions`, `services.accounts`,
   `services.planning`, `services.options`, `services.owned_assets*`, or anything under
   `aggregation`.

Two small refactors make contract 2 possible without copying code:

- **`services/lot_math.py`** (new, pure). Move `select_lots_fifo`, `select_lots_specific`,
  `effective_unit_cost`, `classify_term`, `line_realized_gain` and `compute_disposal` out of
  `services/lots.py` unchanged. `lots.py` re-exports them, so its callers are untouched.
- **`services/holdings_snapshot.py`** (new, read-only). One function returns today's hydrated
  holdings as plain rows (`positions_service.hydrated()` plus options and owned assets at their
  marks). Only `seed.py` uses it, for "copy my holdings".
- **`services/portfolio_compare.py`** (new, pure). Lift `_overlap`, `_spread`, `_sectors`,
  `_levels`, `_line_risk`, `_correlation` and `_period_returns` from
  `followed_portfolios/compare.py`. Copy Trading and paper trading both call them, so "You vs
  them" and paper's Compare can never disagree.

## 3. Data model

All tables are book-scoped and created in one Alembic revision with a timestamp name,
`<yyyymmddhhmm>_paper_trading.py`. Ids come from `core.ids.new_id`.

| Table | Shape | Notes |
|---|---|---|
| `paper_portfolios` | `book_payload_table`: `id ppf_…`, `name`, `home_currency`, `benchmark`, `settings`, `origin {kind: cash\|holdings\|followed, ref}`, `status active\|archived`, `lineage_id`, `run_no`, `started_at`, `archived_at`, `created_by {owner\|lucius\|automation:<id>}` | **Start over** archives this row and inserts `run_no+1` with the same `lineage_id`. History is never rewritten. |
| `paper_contracts` | `book_payload_table`: `id pctr_…`, `kind option\|bond\|future\|manual`, plus the spec: option (`underlying_instrument_id`, `right`, `strike`, `expiry`, `multiplier`, `style`, `settlement`); bond (`isin`, `coupon`, `frequency`, `maturity`, `face`, `day_count`, `issuer_kind`); future (`root`, `month`, `multiplier`, `tick`, `initial_margin`, `maint_margin`); manual (`name`, `currency`, `proxy_instrument_id?`) | Assets that are not a shared `instrument`. A contract has no `quantity`; holdings come only from events. |
| `paper_orders` | Relational, with indexed `(book_id, status)` and `(portfolio_id, placed_at)`: `id pord_…`, `portfolio_id` FK cascade, `asset_key`, `side buy\|sell`, `intent open\|close` (options and shorts), `quantity` or `notional`, `order_type market\|limit\|stop\|nav`, `limit_price`, `stop_price`, `tif day\|gtc`, `extended_ok`, `status open\|filled\|cancelled\|rejected\|expired`, `state_reason`, `placed_at`, `expires_at`, `origin`, `note`, `fill JSON` | Real columns, because the engine queries open orders every tick. `fill` records `{price, basis, source, quote_as_of, session, slippage, spread_or_premium, commission, fx_rate, filled_at}`. |
| `paper_events` | Append-only ledger: `id pevt_…`, `portfolio_id`, `seq`, `at` (UTC), `kind`, `asset_key?`, `quantity_delta`, `cash_delta {ccy: amount}`, `price`, `price_basis`, `order_id?`, `fx_rate?`, `payload`, `idem_key` (unique per portfolio) | **The single source of truth.** Holdings, lots, cash and realised P&L are always derived from it (`book.py`). `idem_key` (e.g. `div:inst_x:2026-12-15`, `exp:pctr_y`) makes every lifecycle job safe to re-run. |
| `paper_values` | `(portfolio_id, date)` primary key, `value_home`, `cash_home`, `net_flow_home`, `bases {quote,modelled,synthetic,yours: value}` | Written by the end-of-day job, so the chart is stable and fast. `rebuild_values(portfolio_id)` recomputes it from events and price history. |

**Event kinds:**

- Phase 1: `deposit`, `withdraw`, `buy`, `sell`, `convert` (FX), `dividend`, `distribution`,
  `split`, `freeze`, `mark` (manual price), `fee`.
- Phase 2: `interest`, `coupon`, `maturity`, `expire`, `exercise`, `assign`.
- Phase 3: `borrow_fee`, `margin_interest`, `variation_margin`, `roll`, `liquidation`.

**`asset_key`** forms:

| Key | Assets |
|---|---|
| `inst:<instrument_id>` | Listed equity, ETF, fund, crypto, metal, index; listed bonds that have a feed |
| `ccy:<ISO>` | Currency cash |
| `ctr:<contract_id>` | Options, modelled bonds, futures, manual assets |

Listed assets resolve through `services.instruments.resolve_instrument`, the same seam watchlists
use. A paper trade may mint an instrument row, and `instruments` carries no ownership, so that
is harmless.

**Money.** Amounts are stored in the asset's currency, the same convention as the real book.
They are converted to the home currency in `services/` with `core.fx.to_base`, and the rate at
fill time is stamped on the event (the historical-rate lock).

## 4. Asset kinds: one protocol, one adapter each

```python
class PaperKind(Protocol):
    key: str                                   # "listed", "fund_nav", "crypto", "metal", "fx", "index",
                                               # "manual", "cash_yield", "option", "bond", "future"
    phase: int
    def owns(self, asset: PaperAsset) -> bool
    def quotes(self, assets: list[PaperAsset], now: datetime) -> dict[str, PaperQuote]  # batched, I/O
    def session(self, asset, now) -> Session      # is_open, next_open, closes_at, nav_cutoff
    def decide(self, order, quote, session, settings, book) -> Fill | Wait | Reject     # PURE
    def value(self, holding, quote) -> Money      # PURE (multiplier, unit, accrued interest)
    def lifecycle(self, holding, since, until) -> list[EventDraft]                       # PURE given data
    def reserve(self, order_or_holding, quote, settings) -> Reservation                  # PURE

@dataclass(frozen=True)
class PaperQuote:
    bid: float | None; ask: float | None; last: float | None; currency: str
    as_of: datetime; basis: Literal["quote", "nav", "modelled", "synthetic", "yours"]
    source: str; session_state: str; extended: bool
```

`decide`, `value`, `lifecycle` and `reserve` are pure. Each adapter is tested with a table of
quote, session, settings and expected decision, with no I/O.

| Adapter | Quotes from | Session | Decide (market order) | Lifecycle | New work |
|---|---|---|---|---|---|
| `listed` (stock, ETF, ETP, REIT, ADR) | `prices.fetch_live(..., record_history=False)` + `resolve_price_quote` for extended prints, when `extended_ok` | `prices.market_status(with_reopens=True)` per exchange | Ask/bid if present, else last ± slippage | Dividends, splits; other actions freeze | Capture `bid`/`ask` from the Yahoo v7 quote that `prices.py` already parses (additive fields). Corporate-action feed (below) |
| `fund_nav` | `fetch_history` daily close as the NAV | `nav_cutoff` (default 16:00 America/New_York, per fund override) | `Wait` until that day's NAV exists, then fill at it | Distributions (cash or reinvest setting) | Detect `asset_subclass == "mutualfund"` (Yahoo `quoteType MUTUALFUND`) |
| `crypto` | `fetch_live` CoinGecko route | Always open | Last ± slippage, 8 decimals | none | none |
| `metal` (physical) | Spot through the instrument's `quote_per_unit` | Spot hours (Sun 18:00 to Fri 17:00 New York) | Spot × (1 + dealer premium) for a buy, × (1 − discount) for a sell | Storage fee accrual (setting) | none |
| `fx` | `fetch_live(...)["fx"]` / `fx_cache` | 24/5 | Rate ± FX spread; writes a `convert` event | Cash yield per currency (phase 2) | none |
| `index` | Index level (`^GSPC`, …) | The index's session | Level, no fee; basis `synthetic` | none (price index) | Label only |
| `manual` | Newest `mark` event; with a proxy, the last mark × the proxy's return since that mark | Always open | The price the user enters, basis `yours` | none | Marks UI |
| `cash_yield` (P2) | The rate setting, or `^IRX` | — | — | Daily accrual, `interest` posted monthly | none |
| `option` (P2) | **Option chain** (new `services/market_data/option_chain.py`: Yahoo `v7/finance/options`, through the existing pinned fetch, cached 60 s). Fallback: `core/options_math.black_scholes` with one-year volatility from `risk_metrics.volatility_annualized`, basis `modelled`, and a default spread | The underlying's regular session | Buy at the ask, sell at the bid. Opening a short needs cover (§6) | `expire` / `exercise` / `assign` at the expiry close: ITM settles at intrinsic, in cash or in shares (the `settlement` setting), OTM expires at 0 | Chain client, options math, strike picker |
| `bond` (P2) | A quote when the instrument has a feed. Otherwise `core/bond_math` prices from yield (Treasury curve from `^IRX ^FVX ^TNX ^TYX`, linearly interpolated, + spread setting), basis `modelled` | Weekdays 08:00–17:00 New York | Clean price + accrued interest (`day_count`) | `coupon` on schedule, `maturity` repays face | Bond math, curve |
| `future` (P3) | Yahoo continuous (`ES=F`, `GC=F`, …). Specs from a small catalog `kinds/future_specs.py`, else user-entered | The contract's session | Last ± slippage × multiplier; reserves initial margin | Daily `variation_margin`; `roll` to the next month at expiry, with the price gap booked as roll cost | Spec catalog, margin |
| `short` / margin (P3) | Same as `listed` | Same | Sell to open; reserves margin | Daily `borrow_fee` and `margin_interest`; maintenance check each tick → `liquidation` | Margin model |

**Corporate actions** (new `services/market_data/corporate_actions.py`):

- **Source.** Yahoo chart `events=div,splits`, fetched once a day per held symbol and cached on
  disk. Same host, same pinned fetch.
- **Output.** Dividends and splits become events through `lifecycle`.
- **Safety net.** A gap of more than 35% between sessions that matches a common split ratio,
  with no split event, **freezes** the holding and raises an Activity row. Nothing is guessed.
- **Delistings.** A missing quote for 5 sessions marks a delisting, which also freezes.

## 5. Order lifecycle and the engine

```
place ─► validate (kind, quantity, cover) ─► quote (outside lock) ─► choke: re-check, decide
            │                                                         ├─ Fill   → order filled + events
            │                                                         ├─ Wait   → order open, state_reason
            └─ Reject (reason, available amount)                      └─ Reject → order rejected
engine tick (60 s, only while open orders or due lifecycle exist):
   batch quotes per kind ─► for each open order: decide ─► choke(fill|expire) per portfolio
daily (after each close, user clock for "today", exchange zone for sessions):
   lifecycle drafts (idempotent idem_key) ─► choke ─► paper_values snapshot
```

- **One write choke.** `apply_paper_change(change)` takes the ops `create`, `update`,
  `start_over`, `archive`, `delete`, `place`, `cancel`, `fill`, `expire`, `lifecycle`, `mark`.
  Every op runs inside one `locked_unit_of_work` over the paper tables. As in Copy Trading, the
  quote is fetched before the lock, and the book is re-read and re-checked inside it, so cover
  is checked against current cash.
- **Instant fills.** `place` decides synchronously, which meets the PRD's "fills within 2 s"
  criterion. The tick only handles orders that are waiting.
- **Fresh quotes only.** In an open session, a quote older than 20 minutes gives `Wait("No fresh
  price since 12:04")`. Fresh means its `as_of`, not when it was fetched.
- **Expiry.** A day order expires at the session close of its asset's exchange.
- **Load.** One batched `fetch_live` per tick, for the symbols with open orders only. With at most
  20 active portfolios, that is a small set. Nothing runs while the flag is off or there are no
  open orders.
- **Clock.** "Today" comes from `core.user_time`. Sessions use the exchange's time zone. Stored
  times are UTC.

## 6. Book derivation, cover and valuation

`book.derive(events)` is pure. It folds events into four things:

- **Lots per `asset_key`**, using `lot_math.select_lots_fifo` and `compute_disposal`. The lot
  dicts have the same shape as real lots, so the same tested math applies.
- **Cash per currency.**
- **Realised P&L.**
- **Frozen holdings.**

`book.free_cash(home)` is cash in the home currency, with other currencies counted only if
automatic conversion is on, minus every reservation:

- open buy orders, at their estimated cost;
- short options: the strike × multiplier for a cash-secured put; a covered call reserves the
  shares;
- margin requirements (phase 3).

Valuation calls each adapter's `value`, then `core.fx.to_base` to the home currency. It also
returns value **per basis** (quote, modelled, synthetic, yours), so the screen can show "of which
modelled: 4,210".

## 7. Performance and compare

- **TWR.** `returns.twr(series, flows)` over `paper_values`, where deposits and withdrawals are
  the flows.
- **Benchmark.** The portfolio's `benchmark`, defaulting to `profile.preferred_benchmark`. Its
  series comes from `followed_portfolios.performance.closes`.
- **Your book.** `returns_service.portfolio_returns_with_benchmark("ALL")`, clipped to the
  portfolio's start.
- **Compare.** `portfolio_compare` (§2) over `{symbol: weight}` built from paper holdings. Non-
  ticker assets go in their own slices, as Copy Trading does for crypto and funds.

## 8. API

Router `api/paper_trading.py` has a flag dependency at router level, as `followed_portfolios`
does. Each route validates, calls one service and shapes the result.

```
GET    /api/paper/portfolios                       list (cards: value, return, sparkline, open orders)
POST   /api/paper/portfolios                       create {name, start_with, cash, home_currency, benchmark, settings}
GET    /api/paper/portfolios/{id}                  page (figures, holdings by kind, open orders)
PATCH  /api/paper/portfolios/{id}                  rename / settings / archive
POST   /api/paper/portfolios/{id}/start-over
DELETE /api/paper/portfolios/{id}
POST   /api/paper/portfolios/{id}/orders/preview   no write: the ticket's live preview
POST   /api/paper/portfolios/{id}/orders           place
DELETE /api/paper/orders/{order_id}                cancel
GET    /api/paper/portfolios/{id}/activity?cursor=
GET    /api/paper/portfolios/{id}/performance?range=
GET    /api/paper/portfolios/{id}/compare?range=
POST   /api/paper/portfolios/{id}/marks            manual asset price
GET    /api/paper/positions?instrument_id=   paper positions in one asset, for the asset page
GET    /api/paper/assets/search?q=                 every kind: tickers, coins, metals, ccy, indices, ISIN, manual
GET    /api/paper/options/chain?underlying=&expiry=   (P2)
```

`orders/preview` and `place` share the same code: `decide` runs on the same quote, without the
write. The preview therefore always matches what a place would do, the same contract as
`ledger.resolve_transaction_row`.

## 9. Lucius

**Tools** (`ai/tools/paper_trading.py`). Tool names are final, with no aliases, so these must be
confirmed before the build:

| Tool | `mutates` | Notes |
|---|---|---|
| `get_paper_portfolios` | read | List, or one by id, with holdings, open orders and recent activity. Bounded at the tool. |
| `get_paper_results` | read | One portfolio or all of them, over a range: return against the benchmark and your real investments, realised and unrealised P&L, worst fall, swings, return per risk, overlap with your book, and per-trade outcomes (hit rate, best and worst trades). It reads `performance.py` and `portfolio_compare`, the same code as the screen, so Lucius and the page always agree. |
| `get_paper_activity` | read | Fills, journal notes, dividends, coupons, expiries and other lifecycle events. Filters by date, asset, kind and origin (owner, Lucius or an automation). Paginated and bounded at the tool. |
| `preview_paper_orders` | read | The same code as the API preview. Lucius sizes trades with it. |
| `create_paper_portfolio` | `("paper",)` | Start with cash, holdings or a followed portfolio. |
| `update_paper_portfolios` | `("paper",)` | Rename, settings, archive, start over. |
| `delete_paper_portfolios` | `("paper",)` | Seeded Ask. Delete is the one destructive op. |
| `place_paper_orders` | `("paper",)` | Batch, so sells go before buys. |
| `cancel_paper_orders` | `("paper",)` | |
| `record_paper_marks` | `("paper",)` | Manual asset prices. |

**Reading results.**

- **What a fill records.** Each fill stores the thesis stance at the moment of the trade, from
  `thesis_judgment.judge` with `held` passed in explicitly. That lets Lucius answer questions
  like "did the trades I placed in my buy zone work?" without reconstructing old prices.
- **The screen.** The paper pages ship a screen snapshot with handles (portfolio, holdings,
  orders, activity), as other screens do. "How is this one doing?" on a paper page then resolves
  through `get_screen_detail`.
- **Unattended runs.** All three paper read tools join `HEADLESS_ALLOWED_TOOLS`, so Automations
  can read results too.

**Permissions.**

- Add `SIMULATION_DOMAINS = frozenset({"paper"})` to `ai/_confirm_gate_v2.py`. A tool whose
  writes are all in that set is seeded **Always allow**, just as `WORKSPACE_DOMAINS` is.
  `delete_paper_portfolios` is the exception and declares `seed="ask"` explicitly.
- The owner can change any of these values. The old gate (`permissions_v2` off) keeps asking,
  because it is left untouched.

**Avoiding confusion between paper and real.**

- Every result carries `"simulated": true` and a `portfolio` name, and states amounts as "paper".
- `PAPER_RULE` (`ai/prompts`) says: paper results are never the owner's money, never part of a
  net-worth answer, and never a reason to place a real order unless the owner asks.
- Paper tools have no `connector=` and no `external_effect`.
- An eval case checks that "what's my net worth?", asked after paper trades, never includes them.

**Data refresh.** `mutates=("paper",)` makes a new data domain. `GET /api/events` then
revalidates only `paper` queries.

**SQL console.** The paper tables are readable through `run_sql_query`; they hold no secrets.
`describe_db_schema` notes "simulated, never part of net worth".

**Over MCP.** No extra work is needed to expose paper trading over MCP. The app's inbound MCP
server offers the whole tool registry (`backend/mcp/adapter.py`), so Claude Desktop, Claude Code
or any other client connected to Open Net Worth gets the paper tools with the same permissions,
staging and `simulated: true` results.

**Not a separate MCP server.** This was considered and rejected. A standalone paper-broker MCP
(the way Alpaca's MCP offers paper mode) would need its own copy of these, or calls back into the
app for them:

- prices, instruments and FX;
- theses and the real book used for comparison;
- storage.

The Intel screen would then read its own data through MCP. Making the paper tools look like a
broker's (`place_order` on two servers) would also blur paper and real, which is the most
common pitfall the research found. The import contracts in §2 give the isolation a separate
process would, without those costs.

## 10. Automations

- **Two new templates** in `automationTemplates.ts`:
  - `paper-copy-followed-portfolio`: weekly, reads `get_followed_portfolios` and
    `get_paper_portfolios`, then places orders with `place_paper_orders`.
  - `paper-strategy`: daily after the US close, with free-text rules.
- **`simulation_only: true`**, a new automation field set by both templates. The runner adds it
  to `AutomationApprovalContext`, and dispatch then **refuses** every tool that is not:
  - a read,
  - a `paper`-domain write, or
  - a workspace write.

  That refusal covers every external MCP tool and every real write. A paper automation can
  therefore never place a real order, even if its instructions are edited or injected. The flag
  is shown on the automation ("Paper only") and can be turned off only by recreating the
  automation from a non-paper template.
- **Copy Trading.** `autoCopy.ts` recognises paper copies by `simulation_only` plus their
  target portfolio, not by a sentence. The portfolio page shows **Paper-copy running** beside
  **Auto-copy**.

## 11. Frontend

- **Navigation.** `components/intel/intelTabs.ts` gets `PAPER_TAB = {key:'paper', label:'Paper
  trading'}`, inserted after Copy Trading by `Intel.tsx` under `useFlag('paper_trading')`. A
  remembered tab falls back to holdings when the flag is off, the same pattern as Copy Trading.
- **Screens** in `components/intel/paper/`:
  - `PaperTab` (cards, empty state, Archived);
  - `PaperPortfolioPage` (header, figures, `PaperChart`, sub-tabs Holdings / Orders / Activity /
    Compare);
  - `NewPaperPortfolioDialog`;
  - `OrderTicket`, with field sets per kind in `ticket/` (`ListedFields`, `FundFields`,
    `MetalFields`, `FxFields`, `ManualFields`, then `OptionFields`, `BondFields`,
    `FutureFields`), all driven by the kind from `assets/search`.
- **Reuse.** `PageTabs`, `SummaryBar`, `Money`, `Tag`, `Modal`, `BottomSheet`,
  `SegmentedControl`, `NumberField`, `MoneyField`, `Select`, `EmptyState`, `Sparkline`; Copy
  Trading's `YouVs` pattern for Compare; Investments' holdings row layout.
- **Entry points.**
  - Asset page: **Paper trade** button, and paper rows in the Position table.
  - Thesis card: **Test on paper**.
  - Copy Trading `PortfolioPage`: **Paper-copy**.
  - `cmdkAssemble`: two commands.
  - Chat tool-result card: a `paper` variant.
- **Paper identity** (mockups v2).
  - There is no mode and no special background. One component, `PaperChip`, goes beside the
    name of every paper object: portfolio, ticket, toast, chat card, automation summary, and
    the asset page's Position rows.
  - "Paper" is written into the labels.
  - New tokens `--paper` and `--paper-ink` go in the token source that `build-tokens.mjs` reads
    (dark and light). They are used only by the chip and the main paper action button.
  - `.paper-chip` is defined in `index.css`.
  - No borders except table rows; group with fills and space.
- **Asset page Position table.** It gets paper rows from `GET /api/paper/positions?instrument_id=`
  (a new read in `views.py`). Each row is labelled and never summed into the real totals.
- **Data.** Every read goes through `useQueryCache` with keys under `paper`. Nothing is held in
  local state without `useDataVersion`.

## 12. Flag, demo, security, privacy

- **Flag.** `paper_trading` in `core/feature_flags.py`: `kind="release"`, `default=False`,
  `remove_by` six months out. Off means:
  - routes 404;
  - tools refuse (`_refusal()` pattern);
  - engine and lifecycle jobs return at once;
  - UI hidden;
  - data kept.
- **Demo.**
  - Reads are allowed.
  - Every write route stays off the `demo_lockdown` allowlist, so it is refused.
  - The tools go in `_DEMO_REFUSED_TOOLS`, except the reads.
- **Outbound.** No new hosts. The option chain and corporate actions are new paths on Yahoo,
  through the existing pinned fetch and TLS context. Names from quotes pass through
  `sanitize_untrusted`.
- **Backups.** The paper tables are in the backup, the sanitised backup and checkpoints. They
  hold no secrets. A checkpoint restore rolls paper back along with everything else.
- **Security review.** Run `/security-review` on the `simulation_only` dispatch refusal and the
  new permission seed, because both decide what an automation can do unattended.

## 13. Tests

| Test | What it proves |
|---|---|
| `test_paper_kinds_<kind>.py` (one per adapter) | Table tests of `decide` / `value` / `lifecycle` / `reserve`: open, closed, stale, gap, limit, stop, NAV cutoff, premium, spread, modelled basis, expiry, coupon, split |
| `test_paper_isolation_property.py` (hypothesis) | Random op sequences over every kind leave net worth, its history, cash on hand, RTA, Investments figures, Intel risk signals, theses held state and the My holdings watchlist byte-identical |
| import-linter contracts (§2) | Nothing real imports paper; paper imports no real write choke |
| `test_paper_engine.py` | Fake clock and quotes: instant fill, wait to open, day-order expiry, idempotent lifecycle re-run |
| `test_paper_book.py` | Derivation matches the real `lot_math` on shared fixtures; free cash with reservations |
| `test_paper_tools.py` + eval | Every result `simulated: true`; batch sells before buys; net-worth answer excludes paper |
| `test_paper_automation_simulation_only.py` | An MCP order tool and a real write are refused inside a `simulation_only` run |
| `test_paper_flag_off.py` | 404s, refusals, jobs idle, data kept |
| `test_demo_route_classification` | Picks up the new routes automatically |
| vitest | Ticket field sets per kind; the Paper chip on every paper object; asset page totals exclude paper rows |
| `make qa-e2e` (ports 8090–8099) | Create, trade, wait for the open, fill, start over; phone layout |

## 14. Delivery plan

| Step | Contents | Size |
|---|---|---|
| 0 | ADR; flag; refactors (`lot_math`, `portfolio_compare`, `holdings_snapshot`) with no behaviour change; import contracts | S |
| 1a | Tables, store, `book.py`, choke, `listed` + `crypto` + `fx` adapters, engine, API, portfolio page, ticket, list | L |
| 1b | `fund_nav`, `metal`, `index`, `manual` adapters; corporate actions; seed from holdings / followed portfolio | M |
| 1c | Lucius tools, `SIMULATION_DOMAINS`, `simulation_only` automations, templates, entry points, help page | M |
| 2 | `cash_yield`, `bond` + `core/bond_math` + curve, `option` + chain client + `core/options_math` + strike picker | L |
| 3 | Margin model, `short`, `future` + spec catalog, uncovered options, liquidation | L |

Each step ships its tests and docs in the same commit, as the docs-impact hook requires.

## 15. Risks

| Risk | Mitigation |
|---|---|
| A Yahoo options or events endpoint changes (crumb, schema) | A modelled fallback for options. A freeze, not a guess, when corporate actions are missing. `market_data_observability` counters |
| A missed split corrupts P&L | The gap detector freezes the holding. The owner settles it with one click ("Apply 4:1 split") |
| Fills too good to be true | Defaults for slippage, spread and premium are on. Every fill shows its basis and quote age. An ADV warning. The help text |
| Lucius mixes paper and real | Tool naming, `simulated: true`, `PAPER_RULE`, the eval, `simulation_only` automations |
| "Every kind" stretches delivery | Adapters ship by phase. The protocol and tables cover all kinds from step 1a, so no migration is needed later |
| Float error in money | The same numeric conventions as the real book. Derivation is checked against `lot_math` fixtures |
