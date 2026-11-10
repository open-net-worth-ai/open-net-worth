---
title: Net worth
description: What counts toward your net worth, what's left out on purpose, and how cash on hand, investments and retirement are kept apart.
category: concepts
order: 60
audience: [user]
status: stable
related: [concepts/accounts-and-opening-balances, concepts/base-currency-and-fx, concepts/positions-lots-and-cost-basis, features/dashboard, troubleshooting/net-worth-looks-wrong]
keywords: [net worth, cash on hand, liquid, settlement cash, margin, retirement, unvested, liabilities, runway]
---

# Net worth

Your net worth is never typed in or stored. It's worked out fresh, every time you look, from
your accounts, holdings and assets, converted into your [base currency](base-currency-and-fx.md).

## What adds up to it

**What you own:**

- **Account balances** — Cash, Fixed deposit, Investment (the cash inside it), Retirement and
  Receivable accounts.
- **Holdings** — the current value of your stocks, funds, bonds, crypto and other investments,
  in any account, at the latest price.
- **Assets** — property, vehicles and anything else on the **Assets** screen, at the newest
  value you recorded.

**Minus what you owe:**

- **Credit** and **Loan** account balances — cards, mortgages, car and personal loans.

The Dashboard's **Net worth** headline includes retirement accounts. Everything is in one
currency, so a USD brokerage and a EUR mortgage add up correctly.

## Cash on hand

**Cash on hand** is the total of your **Cash**-type accounts — bank accounts, e-wallets and
physical cash. It's the money you could spend today, and it's the same number on every screen
that shows it.

It deliberately leaves out:

- **Fixed deposits** — locked for a term. (The Assets screen's **Cash & Savings** card shows
  cash on hand and fixed deposits together, as separate lines.)
- **Settlement cash** — the cash inside a brokerage or crypto exchange account. It belongs to
  your **Investments**. If it's negative because you're trading on margin, that's a loan secured
  by your holdings: it reduces what your investments are worth, never your spendable cash.
- **Credit card balances** — they're debt, handled by your budget, not subtracted here.
- **Untracked holdings** — on an account valued by its **Brokerage total** (experimental), the
  part of the total you haven't entered as holdings. It's invested, not cash, so it counts with
  your **Investments** (or **Retirement**) and never as settlement cash. See
  [Accounts](../features/accounts.md#investment-and-retirement-accounts-brokerage-total-or-live-holdings).

## Where each kind of money shows up

| Screen | What you see |
|---|---|
| **Dashboard** | **Net worth**, with **Cash on hand** beside it. The **Reachability** widget (add it when you customize the Dashboard) splits your assets into **Liquid now** (cash and freely sellable holdings), **Delayed** (yours but not reachable yet, like vested shares not yet delivered) and **Illiquid** (property, retirement funds, fixed deposits before maturity, money owed to you). |
| **Assets** | Cards for **Cash & Savings**, **Investments** (non-retirement brokerage and crypto: positions plus settlement cash), **Retirement** (its cash and holdings together) and **Receivables**, plus your property and other assets. |
| **Plan › Budget** | Only Cash and Credit accounts — see [Envelopes and budgeting](envelopes-and-budgeting.md). |
| **Plan › Cash Flow** | Your runway — how many months your money would last. It always starts from cash on hand. To include more, click **What-if** → **Projection settings** → **Runway & safety** and turn on **Fixed deposits** and/or **Investments (net of settlement cash)**. |

## What's left out, and why

**Unvested equity isn't counted.** Shares or RSUs you've been granted but that haven't vested
yet aren't yours — leave the job and they're gone — so counting them would overstate what you
have. They're shown separately, as **Unvested**, on the holding's Position tab and under
**Investments → Equity Compensation**. When a grant vests, the shares become an ordinary
holding and count like everything else.

Equity compensation is still a **beta** area of the app — double-check its figures. See
[Equity compensation](../features/equity-compensation.md).

## Related

- [Accounts and opening balances](accounts-and-opening-balances.md) — the seven account types
- [Positions, lots and cost basis](positions-lots-and-cost-basis.md)
- [Base currency and FX](base-currency-and-fx.md)
- [My net worth looks wrong](../troubleshooting/net-worth-looks-wrong.md)
