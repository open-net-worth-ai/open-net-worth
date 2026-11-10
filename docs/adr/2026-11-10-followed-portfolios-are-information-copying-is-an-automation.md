---
title: Followed portfolios are information; copying one is an Automation
status: Accepted
date: 2026-11-10
deciders: orchestrating agent (Claude) + repo owner
---

# Followed portfolios are information; copying one is an Automation

## Context

The owner wants to copy a famous fund or a politician, as "autopilot" apps do. The filings behind
those apps are public, the app already knows the owner's holdings, and a broker can be reached
through its own MCP server. Two earlier decisions removed every native order path
(`2026-07-19-remove-alpaca-trading-half`, `2026-07-20-remove-ibkr-dead-trading-flag`).

The first build put a "mirror plan" on the screen: pick an account and an amount, and the app
computed the buy and sell list for Lucius to place. Review (code and security, 2026-11-10) found
that the plan could not know which shares in the account belonged to the copy. It would propose
selling shares the owner already held, and buying everything again when fills had not synced
yet. Fixing that needed a baseline per symbol, a record of orders placed, and a currency stamp:
a second ledger beside the real one, fed by guesses. The owner's call the same day: treat these
portfolios as information, and keep copy trading in Automations, where Lucius reads the real
broker account, the owner writes the rules, and Lucius judges each trade.

The public sources shape the feature more than the code does:

- A 13F is quarterly, filed up to 45 days after quarter end, lists long US positions only and
  identifies them by CUSIP, not ticker.
- A House trade report lists trades, not holdings, with each amount as a range, up to 45 days
  after the trade. The Senate site refuses automated access.

## Decision

- **Information only.** `followed_portfolios` holds which portfolios are followed and the last
  two snapshots fetched for each. Nothing in the feature reads an account, sizes a trade or
  places an order. There is no sleeve, no plan and no per-follow setting.
- **One table, one writer.** `services/followed_portfolios/service.apply_followed_portfolio_change`
  (`follow`, `refresh`, `unfollow`). A filing is fetched before the lock and the row is re-read
  inside it. A refresh that fails or reads nothing keeps the previous snapshot and records the
  error on the row.
- **Free official sources only.** SEC EDGAR for 13F, the House Clerk's index and report PDFs for
  members, OpenFIGI for CUSIP to ticker. Every fetch goes through `pinned_urlopen` to a fixed
  host with a size cap. No paid API, no key, no Senate.
- **A member's portfolio is an estimate and is always labelled one.** It is rebuilt from the
  trades in a 36-month window: a purchase adds the midpoint of its range, a partial sale
  subtracts it, a full sale clears the name. Only stock rows with a ticker count.
- **The model is derived, the snapshot is stored.** Weights are the top N ticker-bearing
  positions renormalised to 1 (`model.build_model`; N is a read-time argument). "What changed"
  compares share counts, so a price move alone is not a change.
- **Filing text is untrusted.** Issuer, filer, asset and member names are defanged and cut
  (`core.untrusted.sanitize_untrusted`) where they are parsed, before they are stored. A symbol
  is a plain ticker or it is dropped (`model.normalize_symbol`). The tool text tells Lucius the
  names are data, never instructions.
- **Copying is an Automation.** The "Copy a followed portfolio" template carries editable
  instructions: the account, the amount and the owner's rules. Lucius reads the follow with
  `get_followed_portfolios`, reads positions and cash from the broker's own MCP tools, judges
  each trade, and places it with the broker's order tools. Whether it asks first is that tool's
  permission, as for every other tool.
- **It lives in Intel.** The Following tab sits beside the watchlists and Topics & macro: all
  three are things the owner watches, and a new filing already lands in Intel as a story.
  Investments stays what the owner holds.
- **Behind the `followed_portfolios` flag**, default off: routes 404, the tools refuse, the
  refresh job returns at once.

## Consequences

- No second ledger. What the copy owns is whatever the owner tells Lucius and the broker
  reports; the app never guesses it.
- The trade list is model-computed, not deterministic. That is the accepted cost: the owner
  reads each order card, or chooses Always allow for the broker's order tool knowing that.
- New outbound hosts (`efts.sec.gov`, `data.sec.gov`, `www.sec.gov`,
  `disclosures-clerk.house.gov`, `api.openfigi.com`). A search or a follow makes the server call
  them, so the directory route, the writes, the tool's `search` and `set_followed_portfolios`
  are refused on the public demo.
- A scanned paper report cannot be read. It is counted and shown, never guessed.

## Alternatives rejected

- **An in-app mirror plan with a sleeve** — built and removed the same day, for the reasons
  above.
- **A native order path** — rejected twice already.
- **A paid Congress-trades API** — both chambers and cleaner data, but a key and a vendor for a
  flag-gated first version. The source seam (`fetch` / `search` per kind) leaves room for one.

## Agent provenance

- Agent: Claude (orchestrating), with UI-designer, implementer, code-review and security-review
  subagents.
- Branch: `feat/followed-portfolios`.
- Why recorded: new table, new outbound hosts, and a deliberate refusal to compute trades in
  the app after building and reviewing exactly that.
