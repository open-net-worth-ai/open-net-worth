# The public demo (`demo.opennetworth.ai`)

The demo is a real Open Net Worth instance running a **generated** book. Nothing in it belongs
to anybody: every account, transaction, holding, grant and budget line is fabricated by
`backend/scripts/demo/`, and the profile carries an `is_demo` flag that puts the "you're viewing
fabricated sample data" banner at the top of every screen.

The book is generated rather than checked in, and generated **relative to the day it is built**,
so it cannot go stale the way the old `backend/data/examples/` fixture did — see ADR
[`2026-08-23-the-demo-book-is-generated-not-a-fixture`](adr/2026-08-23-the-demo-book-is-generated-not-a-fixture.md)
for why that mattered enough to delete sixteen fixture files over.

## Run it

```bash
make demo                       # throwaway: fresh book in a temp dir, erased on Ctrl-C
./scripts/run_demo.sh --smoke   # build + verify + clean up, no servers (this is what CI runs)
```

Frontend on `:5175` (bound `0.0.0.0`, which is what the `demo.opennetworth.ai` tunnel points at),
backend on `:8175` (loopback only). There is **no login** — a visitor lands straight in the app.

### Why there is no password, and why that is safe

The launcher sets `DEMO_MODE=1` **and** `DEMO_OPEN_ACCESS=1`. Only the pair opens the instance
(`core.config.demo_open_access_active()`), and both are read from the process environment alone:
either flag by itself, a typo, a stored setting or a forged header still gets a 401 on every
instance that is not a demo. The book keeps a random password nobody is told (minted per launch by
`run_demo.sh`, once on the VM by `demo-aws.sh`) so it stays "configured". A visitor is the operator
of a fabricated book and nothing more, enforced by `api/demo_lockdown.py`:

- **Writes are an allowlist.** A visitor can create, edit and delete anything in the book itself
  (ledger, planning, investments, assets, equity, watchlists, profile). Any other write is refused
  with `403 demo_locked`, so a route nobody classified is closed, not open. A test walks every
  mutating route and fails on one that has not been reviewed.
- **Refused outright:** the Builder (a coding agent with a shell on the host), the Terminal, MCP
  servers and the inbound `/mcp` endpoint, extensions and skills, `POST /api/reset`, backup and
  whole-book import/export, the book-move protocol, Automations (create, edit, run), memory,
  notification channels, every provider/connector/integration config, secret reveal, the API token
  and iOS Shortcuts, every password or auth-setting change, turn checkpoints, host logs.
- **Files are open.** Visitors can upload and import statements, attach receipts, add chat
  attachments and Documents, and add asset photos and docs — the demo is public and says so, and
  parsing spends the visitor's and the instance's token budgets like any other model call.
- **Automations run, visitors can't change them.** The seeded scheduled and event-driven
  Automations fire on a demo so visitors see what they do; their tokens count toward the
  instance-wide daily ceiling (no visitor is charged) and run on the pinned instance model.
  Visitors cannot create, edit or run one. Lucius's memory is read-only (a fact learned from one
  visitor's chat would surface in the next).
- **Conversations are private.** The demo hands each browser an anonymous `onw_demo_visitor`
  cookie (it authenticates nothing). A visitor lists, reads and deletes only the conversations
  they started; Automation run conversations are listed for everyone but read-only (`read_only` on
  the listing), so the chat never opens one by default. A restart
  forgets who owned what, so older conversations become unreadable to everyone. A browser that
  refuses cookies loses its history.

Never set these two flags on a real book. See ADRs
[`2026-10-07-the-public-demo-needs-no-password`](adr/2026-10-07-the-public-demo-needs-no-password.md)
and [`2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard`](adr/2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard.md).

## Host it

```bash
ONW_DEMO_ROOT=/srv/onw-demo ./scripts/run_demo.sh
```

The book then lives in that directory and survives a restart. Re-running is a safe no-op — the
builder refuses a book that already has data — so a supervisor can restart the process freely.

**Refreshing.** Visitors can edit and delete things, so a long-lived demo drifts. Throw the book
away and build a new one, dated to whatever day you run it:

```bash
ONW_DEMO_ROOT=/srv/onw-demo ./scripts/run_demo.sh --rebuild
```

A nightly cron on that line is the whole maintenance story.

**Cleanup only ever removes a directory it created itself.** `ONW_DEMO_ROOT` is yours; Ctrl-C on
a hosted demo will not `rm -rf` it. Only the throwaway `mktemp` directory is erased on exit.

`--rebuild` does delete the root, so it refuses anything that is not recognisably a demo root:
`/`, your home directory, the repo, or a directory holding anything other than the `data`,
`cache` and `logs` the launcher itself creates. Point it at the wrong path and you get a
refusal, not an erased home directory.

## Lucius

Lucius runs on the **Claude Code CLI** by default — the `claude` login already on the machine
you are hosting from. No service account to create, no API key sitting on a public box.

```bash
ONW_DEMO_MODEL=none ./scripts/run_demo.sh                   # leave Lucius unconfigured
ONW_DEMO_MODEL=anthropic:claude-sonnet-5 ./scripts/run_demo.sh   # a separately-billed key
```

The launcher runs the app under `env -i`, so a public instance inherits nothing from your
shell — with one exception that is not a credential: **`USER`**. The CLI resolves its own login
through the macOS keychain, which needs `USER` to know whose keychain to open. Without it every
turn dies with *"Not logged in · Please run /login"* while the provider row still reports
**Ready** — a confusing failure, since nothing about the provider looks wrong.

Every other provider and connector credential is blanked unconditionally.
`ONW_DEMO_ANTHROPIC_API_KEY` is passed through as `ANTHROPIC_API_KEY` when you set it; your
ambient `ANTHROPIC_API_KEY` is **not**, so a public instance never inherits a billed key by
accident.

`DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` are pointed at the demo root, never at `~/.open-net-worth/`.

## Visitor limits

Since the demo runs on your own plan, the limits are what keep a visitor — or a crowd — from
drawing it down. They are active only while `DEMO_MODE` is on, so your own instance is never
throttled. Three of them, because each alone has a hole:

| Limit | Default | Env | Stops |
|---|---|---|---|
| Turns per window | 30 / 300s | `ONW_DEMO_TURNS_PER_WINDOW`, `ONW_DEMO_TURN_WINDOW_S` | one visitor hammering Send |
| Tokens per visitor per day | 3,000,000 | `ONW_DEMO_TOKENS_PER_IP_DAILY` | the patient one |
| Tokens per day, all visitors | 30,000,000 | `ONW_DEMO_TOKENS_TOTAL_DAILY` | fifty of them |

(On the AWS VM the same values are `DEMO_TURNS_PER_WINDOW`, `DEMO_TURN_WINDOW_S`,
`DEMO_TOKENS_PER_IP_DAILY` and `DEMO_TOKENS_TOTAL_DAILY` in `/srv/onw-demo-stack/.env`.)

A throttled visitor gets a 429 with a plain-language reason *before* any provider is reached.

**What counts.** A "turn" is a deliberate model action: a chat message, an approved tool, a
suggestion, a capture, an account description, a match judgement, an instrument enrichment, an
asset-brief refresh. Tokens are counted for **every** model call, whatever started it — the check
sits where every provider is built, so a screen that narrates, an import, or a background job
cannot spend past the budgets. A call with no visitor behind it (the app's own work) counts toward
the all-visitors ceiling only.

**Tokens, not dollars** — a Claude Code CLI turn records a computed list price, not a bill
(`cost_basis="plan_included"`), so a spend cap would never fire. Tokens are the quota that is
actually consumed either way.

**Who is a visitor.** The network address the trusted proxy saw — never the client-written start of
`X-Forwarded-For`, which a visitor can rotate to get a fresh bucket. `DEMO_TRUSTED_PROXY_HOPS`
(`ONW_DEMO_TRUSTED_PROXY_HOPS` for the launcher) says how many proxies in front of the app append
to that header; the visitor is that many entries from the **right**. Both shipped hosts set it to
`1` (the Cloudflare edge appends the connecting address). The default is `0`: the header is ignored
and the peer address is used, which cannot be spoofed but makes everyone behind one proxy share a
bucket — so set it to match your proxy chain, and check it by sending two requests with different
`X-Forwarded-For` prefixes and confirming they hit the same bucket.

**Why the instance-wide ceiling exists**: it is the one limit that holds even if the per-visitor
key is wrong (a misconfigured hop count, a client that reaches the app past the proxy). `run_demo.sh`
serves the frontend on `0.0.0.0`, so a LAN client that bypasses the tunnel can write the whole header.

Tokens are charged back from `ai/cost_tracker.record` against the session the visitor started, or
the visitor whose request caused the call, so they are real recorded usage, not an estimate. A turn
already running is never killed mid-way — the budget it overruns is enforced on the next request.

## Build the book without the servers

```bash
python3 -m backend.scripts.seed_demo                       # dry run — reports, writes nothing
python3 -m backend.scripts.seed_demo --commit               # build into the resolved DATA_DIR
python3 -m backend.scripts.seed_demo --commit --as-of 2026-08-23   # pin the anchor date
python3 -m backend.scripts.seed_demo --commit --force       # stack onto a non-empty book
python3 -m backend.scripts.seed_demo --commit --offline     # no provider lookup (reproducible)
```

`DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` come from the environment and are also what select the
database, so export all three to build into an isolated tree. There is no `--dest`: every store
is a table now, and a table is not a directory.

`make seed` is `--commit` against your own instance — useful for looking at a full app on a fresh
clone, and it refuses to touch a book that already has data.

## Prices

The instruments are real tickers on real price sources, so a demo with outbound network fetches
live prices the moment it boots and keeps doing so. The generated *history* is synthetic, and it
is **anchored on today's real price per ticker** so it walks to the number the live series then
continues from. That matters more than it sounds: anchoring on a guess instead leaves a visible
step at the join — an early build walked Bitcoin to a guessed 96,500 on the Friday while the live
fetch put it at 69,360 on the Sunday, and the portfolio chart showed the cliff.

The anchor is best-effort per symbol. An unreachable provider, a rate limit or an unknown symbol
falls back to that instrument's built-in anchor, and a host with no network at all builds a
complete, self-consistent book from the built-in anchors alone. `--offline` forces that path,
which is also what makes a build reproducible from `(date, seed)`.

One consequence worth knowing: **with live prices the book is not byte-reproducible**, because
the market moves. Everything else about it is — same date, same seed, same accounts, same
transactions, same budget.

## What's in the book

Anchored on the build date, reaching 30 months back.

| | |
|---|---|
| **Owner** | Alex Morgan, 35, married, USD base, retiring at 52 |
| **Accounts** | 15 — checking, joint, two savings, cash, EUR and SGD wallets, taxable brokerage, Roth IRA, 401(k), crypto exchange, apartment, credit card, mortgage, student loan |
| **Transactions** | ~3,000 — semi-monthly payroll with two raises, an annual bonus, quarterly EUR freelance invoices, an amortizing mortgage split into interest and principal, utilities, groceries, dining, subscriptions, two trips a year, a December gift spike, splits, FX transfers, ATM cash |
| **Investments** | 10 instruments (VOO, VXUS, SCHD, BND, AAPL, NVDA, GLDM, BTC, ETH + a manually-priced private employer), 291 DCA lots, 2 disposals with realized gains, 38 dividends, daily price history for all of them |
| **Equity** | A 1,600-unit RSU grant, four-year quarterly vest with a one-year cliff — five tranches vested, the rest excluded from net worth and reported as `unvested_base` |
| **Owned assets** | 36 — an apartment (property, mortgage-linked), a car (vehicle, with a doc and a re-mark automation), a disposed e-bike with real sale proceeds, a watch collection of 11 (Commons photos, credits in `backend/scripts/demo/photos/CREDITS.md`), and a 22-card Pokémon binder in a custom category. Each card's photo is its scan from the Pokémon TCG image CDN, fetched at build time and never committed (card art is copyrighted); an `--offline` build uses one licensed fallback photo |
| **Plan** | 19 envelopes in five groups, targets derived from the book's own median activity, 30 months of assignments, three sinking funds, 4 goals, 5 commitments |
| **Elsewhere** | A four-item watchlist and five dashboard views |

Merchant names are invented. That is deliberate on two counts: a public demo should never look
like it is showing somebody's real statement, and an invented roster is the honest signal that
the data is staged.

## Changing it

Everything lives in `backend/scripts/demo/`:

| File | What it owns |
|---|---|
| `_timeline.py` | The clock and the per-stream RNG. No generator reads `date.today()` itself. |
| `cast.py` | Who Alex is, the 15 accounts, the categories, the merchant roster, FX rates |
| `rows.py` | The four transaction shapes, so a generator reads as a story not as leg plumbing |
| `ledger_income.py` | Payroll, bonus, freelance, interest, mortgage/student loan, utilities, sweeps |
| `ledger_spending.py` | Essentials — groceries, dining, transport, subscriptions, health |
| `ledger_lifestyle.py` | Discretionary — shopping, fun, pets, gifts, trips, tax, cash, splits |
| `markets.py` | Instruments, price paths, the trade plan, the vesting ladder |
| `investments.py` | Writes all of that through the position/lot/income/equity chokes |
| `owned_assets.py` | The apartment, a vehicle, a watch and a disposed item, through `apply_owned_asset_change` — the mark ladder, the doc/photo attachments, the re-mark automation, the disposal |
| `planning.py` | Envelopes, derived targets, assignments, goals, commitments, watchlist |
| `commitments.py` | The commitment specs (pure data) |
| `commitments_property.py` | The flagship commitment: an off-plan apartment on a staged payment ladder |
| `dashboard.py` | The saved Overview views |
| `automations.py` | Event + scheduled automations, mixed statuses, so the tab looks used rather than empty |
| `assistant.py` | Watchlist alerts and the assistant's seeded memory |
| `build.py` | The orchestrator — the dependency order lives here |

After any change, run:

```bash
pytest tests/test_demo_book.py tests/test_demo_prices.py \
       tests/test_demo_seed_api.py tests/test_demo_launcher.py
```

`test_demo_book.py` is the reconciliation the demo has never otherwise had: every leg resolves
to a real account, no asset account is overdrawn on any end-of-day, the card stays inside its
limit, positions agree with their lots, nothing is left Uncategorized, no goal reads as already
finished, the net-worth chart has no cliff at its right edge, and Ready-to-Assign plus
everything assigned equals the cash backing it. It asserts properties rather than figures, so
re-tuning the book does not redden the suite — but breaking its arithmetic does.

The other three cover the price seam, the HTTP seed endpoint's refusal of a populated book, and
the launcher's two dangerous edges: a provider key must not reach a public instance by
inheritance, and `--rebuild` must not erase a directory it did not create.
