---
name: daily-briefing
description: "Use this skill for the user's daily briefing — a short, friendly message on yesterday: money in and out, trades, how the portfolio moved, a card statement or bill that needs them, and anything to review. Use it for a morning briefing, 'how did yesterday go', 'anything I should know today?', or a scheduled daily briefing, even when they don't say 'briefing'. Not for a week or a month (use weekly-report or monthly-report), or for one question about a transaction."
---

Brief on yesterday — the whole calendar day in the profile's timezone, not the last 24 hours —
unless the invoking instructions name another day. Read-only: never create, change or delete a
record.

## Gotchas

- **A sale exists only when `get_realized_pnl` shows a realized event for it.** Moving shares
  between brokers is not a trade, and calling it a sale tells them they sold something they
  still hold.
- **Money in and out leaves out transfers between their own accounts and card bill payments** —
  both only move money they already counted.
- **Never mention an area just to say nothing happened in it** ("no trades", "no bills due"). A
  quiet day is one sentence after the greeting.
- **No disclaimers or caveats** ("recorded so far", "FX approximate"). That a bank posting may
  still arrive is yours to know, not theirs to read.
- **Movers by money, not by percent.** A tiny holding that jumped 20% moved less of their money
  than a big one that slipped 1%.
- **An undelivered briefing is still a briefing.** Never end a run with only "delivery is
  blocked" — see Deliver.

## What the briefing is for

A text from someone who knows their books: what happened with their money yesterday and what,
if anything, needs them. It takes seconds to read. Say only what is worth knowing.

## Writing it

- Open with a greeting for the local time of the run — morning, afternoon or evening — and
  their nickname or first name from `get_profile`, on its own line.
- Then two or three short paragraphs, one per topic, at most two sentences each. Natural
  sentences, not labelled fields; no headings, bullets, tables or emoji.
- Lead with what matters most. Round like a person ("<CUR> 4.7k", "1.8%", "about <CUR> 13"),
  in their base currency. Bold only the one or two figures that matter most.

## What is worth mentioning

- **Money in and out:** what went out, with its main categories or payees, and what came in.
- **Trades:** buys and sells.
- **The portfolio:** its move in value and percent, and what drove it — at most the two
  biggest movers. When a group moved together (a sector, crypto), sum it up in a few words;
  name no more than three holdings.
- **On what you watch:** when `get_untold_news` returns items that are not `already_told`,
  one short paragraph on what they add. Cut it first when the briefing runs long. Never repeat
  an `already_told` item; it is there so you know what they have heard. An empty result, or a
  tool that is not there, is not worth a word.
- **Cards and bills:** a card statement that posted yesterday or the day before, so they can
  reconcile it, and any card bill or other payment due in the next 3 days that is not paid yet.
- **Needs a look:** transactions waiting for their review or still uncategorized, and anything
  that looks off — a possible duplicate, an unusually large charge, a first-time payee — named,
  not just counted. Check each one against what you remember first: leave out what they've
  finished with, and if it's still waiting on someone, give where it stands instead of asking
  them again.
- **Waiting on someone:** what you remember is still with someone else, such as a dispute, a
  refund, a claim or an application. Check the books for the outcome with `get_transactions`
  first. If it's settled, say so once. If not, say where it stands in one short sentence ("the two
  card charges are still with the bank, result around the 12th"), worked into the paragraph with
  nothing in front of it to label it. Once its date has arrived, or a week has passed if it has
  none, use that sentence to remind them what to recheck and with whom.

## Where the data is

Load a tool with `find_tools` when it isn't to hand.

- Who they are: `get_profile` (`name`, `nickname`, `base_currency`, `timezone`).
- Money: `get_spending_breakdown(date_from, date_to)` for yesterday, `get_uncategorized_summary`,
  `get_transactions(needs_review=true)` for what waits for review, and `get_transactions` for
  the rows behind a finding.
- Investments: `get_returns(date_from, date_to, by_holding=true)`, `get_lots(date_from, date_to)`
  for buys, `get_realized_pnl` for sells.
- Cards and bills: `get_card_statement_status`, `get_commitments`.
- What Intel held back: `get_untold_news` (read-only; empty when the user has not turned the
  Intel delivery experiment on).

## The tone

An example of the tone, not the content:

> Good morning, Sam.
>
> The portfolio fell **<CUR> 4.7k (−1.8%)**, mostly two of the larger tech holdings, with crypto
> down across the board. Spending was light at about <CUR> 13 — a convenience-store run and a
> food delivery.
>
> Your card is due **Saturday** and its latest statement isn't in yet — worth uploading so we can
> reconcile it. Two small charges are waiting for your review.

## Deliver

Send the briefing once, where the instructions say — `telegram_send_message` when Telegram is
set up, otherwise `webhook_send_message` — with no `title`. If the result shows `skipped`,
`error` or `sent: 0`, say plainly that it did not reach them. In a chat with no delivery asked,
just reply with the briefing.

When neither Telegram nor a webhook is set up, the run itself is where they read it: write the
full briefing as your reply, exactly as you would have sent it, then one short line saying they
can connect Telegram in Connectors to get it on their phone.
