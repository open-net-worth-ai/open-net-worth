---
title: Retirement and FIRE outlook
description: See the odds your money lasts, the age you could safely retire, and how life events, extra income or a market crash change it.
category: features
order: 70
audience: [user]
status: beta
related: [features/budgeting, concepts/net-worth, features/settings]
keywords: [FIRE, retirement, outlook, monte carlo, retire early, life events, scenarios, withdrawal rate, what if]
---

# Retirement and FIRE outlook

**Plan → Outlook** projects your financial future from your real net worth: the odds your money
lasts your whole life, the earliest age you could safely retire, and what a house, a baby, an
inheritance or a market crash would do to that picture. It runs thousands of simulated market
paths, so you see a range of outcomes rather than one hopeful line.

**Beta:** fully working but newer than the ledger and budgeting. Use it to plan and compare
choices, not as a precise forecast.

## Set it up

The Outlook needs your **birth date**, what you **save each month** until you retire, and what you
expect to **spend each month in retirement**.

1. Open **Plan → Outlook**. If an answer is missing, a dialog asks for it.
2. Enter **Your birth date**, **Monthly saving now** (0 is fine if you save nothing yet) and
   **Monthly spending in retirement** (it starts from your current monthly spending, if you've set
   that in your profile). Without the saving, the outlook assumes your money only shrinks.
3. Click **Show my outlook**. Or click **Set up with Lucius** and he asks you one question at a
   time, saves the answers and walks you through the result. Lucius saves only the birth date you
   give him; if you give a month and year, he asks before saving the 1st of that month.

The answers are saved to **Settings → Profile** (About you and **FIRE settings**), where you can
change them later along with your withdrawal rate, expected return, volatility, target retirement
age, life expectancy and monthly saving. Return volatility and life expectancy show the values
the plan is using (15% and age 90) until you set your own.

**Your monthly saving is its own figure.** Saying "I save 1,200 a month" stores a saving of 1,200
and nothing else; it doesn't make 1,200 your income. Once you've given a saving, editing your
income or expenses in your profile never changes it. If you've never given one, the outlook uses
your income minus your expenses. A figure you give later, in **Settings → Profile** or to Lucius
(your saving, retirement age, spending, expected return, volatility, withdrawal rate or life
expectancy), replaces the one your saved FIRE plan was holding.

**Check it worked:** the headline shows your money-lasts odds with **Likely to last**, **At risk of running out** or
**Unlikely to last**, and the chart fills in. That verdict is about your money surviving
retirement. It is separate from the chart's **FIRE number reached** marker, which is the age your
typical path first reaches your FIRE number. A saved scenario whose retirement age differs from
your plan shows it beside its odds ("49% at 45"), so its odds aren't read against the headline's.

## Read your outlook

- **The headline** — the chance your money lasts, the **safe-to-retire age** (the earliest age
  with at least 80% odds) and your pot today. In **FIRE Settings**, when
  your withdrawal rate differs from the suggestion for your horizon, a line says which side you
  are on and why (a lower rate than suggested is the more cautious choice), with a one-click
  **Use** button for the suggested rate. **Ask Lucius how to improve** and **Ask Lucius if I'm
  saving enough** put the question in the Lucius chat for you to send.
- **The chart** — your net worth across your life: the band shows the likely range, with
  **Building wealth** before retirement and **Drawing down** after. Switch to **Outcomes by
  age** to see, at each age, the share of outcomes where you still have money, your money has
  run out, or you are no longer living. **Condition on survival** sets the odds of dying aside.
  The figure at the end of the line is where the typical path finishes; when it lands on a row
  of the money scale, that scale figure is hidden so the two don't print over each other.
- **Big amounts** read the same way on the chart, the tiles under it and **Cash Flow**: in
  millions, billions or trillions (₫24.95B, not ₫24946.08M), in your chosen number format.
- **Ask about it** — the suggested questions (*"What is my chance of outliving my money?"*, *"What
  if I retire 5 years earlier?"*) send the question to Lucius.

## Try a what-if

Everything below changes the chart straight away without touching your saved plan, until you save.
On a phone, or when the panel is collapsed, tap **What if?** to open it.

1. Under **Plan levers**, change **Retirement age**, **Monthly saving**, retirement spending,
   **Withdrawal rate**, **Plan until age**, **Expected return** or **Volatility** — or tick
   **Retire when I hit my FIRE number**.
2. Under **Events & income**:
   - **Market shocks** — tap **Crash**, **Weak returns**, **Lost decade** or **High inflation**
     and set the years it hits.
   - **Life events** — tap a preset (Home, New baby, Tuition, Inheritance, Sabbatical) to add it
     straight away, or **Custom** to name your own and click **Add to chart**. Then drag its flag
     on the chart to the age it happens; click it in the list to edit the amount.
   - **Guaranteed income** — click **Add income** for a pension, rental income or annuity.
3. Tap one of Lucius's **What if?** questions — such as *Retire at 55?*, *Buy a house in 2028?*
   or *Markets fall 30%?* — and he sets the levers for you. They show until you tap one; after
   that, ask Lucius in your own words.
4. To go back to your saved plan, click **↺ Reset**.

## Save and compare scenarios

- In **Scenarios**, **Save** makes the current levers your FIRE plan (and updates your
  FIRE settings). **Create** saves them as a separate named scenario instead.
- Click a saved scenario to load it; rename or delete it from its row.
- **Compare two plans** shows plan A against plan B on the same chart.

## What Lucius can do here

- Work out your FIRE number, progress and projected retirement age, and answer *"am I on track?"*
  — including what-ifs with different assumptions.
- Run the full simulation and explain the median outcome, the downside case and the chance of
  success. It is **the same simulation as this screen**, starting from your saved plan: ask him
  *"what are my odds?"* and you get exactly the headline percentage, and for a what-if he says
  which settings he changed from your plan. There is only one answer to a question about your
  plan, here and in chat.
- Stage a what-if on your chart for you to look at (nothing is saved until you save it).

## Things that trip people up

- **The odds are a range, not a prediction.** "Money lasts" is the share of simulated paths that
  didn't run out under your assumed return and volatility. Different assumptions give a
  different answer.
- **The headline can lag while you drag a lever** — it settles a moment after you let go. The
  same settings always give the same percentage: the simulation uses fixed random numbers, so it
  doesn't wobble from one visit to the next.
- **A pension or annuity is guaranteed income:** add it under **Guaranteed income** with the age
  your payouts start, and leave the end age empty if it pays for life. It only reduces what you
  draw from your pot once you've retired.
- **Money you can only spend on one thing counts in your pot** if you keep it in a Retirement
  account (a healthcare-only savings account, for example), so the pot can read higher than what
  you could spend freely. The outlook can't tell it apart from your other retirement accounts yet;
  ask Lucius to run your plan with the pot reduced by that balance.
- **Unvested equity is left out of every projection**, the same as in net worth.
- **It reads your profile.** An out-of-date income, spending or birth date gives an out-of-date
  outlook — keep **Settings → Profile** current.

## Related pages

- [Budgeting](budgeting.md)
- [Settings](settings.md)
- [Net worth](../concepts/net-worth.md)
