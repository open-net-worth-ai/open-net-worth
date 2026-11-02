// plan/rtaLines.ts — the ONE row builder behind both renderings of the Ready-to-Assign
// identity: the breakdown popover (`budgetRta.tsx`) and the fresh-start dialog's ledger
// (`freshStart.ts` → `FreshStartDialog.tsx`). ADR
// 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category:
//
//   Ready to Assign = cash on hand + credit on your cards − baseline − held in envelopes
//                     − held outside envelopes − card spending not covered this month
//                     − assigned in future months
//
// "Credit on your cards" (ADR 2026-11-02-a-card-in-credit-is-budget-money) is every card's
// balance above zero — an overpaid or refunded card holds your own money, which stays in the
// budget. A card that owes money adds nothing here.
//
// "Held in envelopes" is the SIGNED sum of every envelope's Available, so it can be NEGATIVE:
// an envelope HOLDS its own overspend until month-end, lending that room back until the roll
// settles it — which can put Ready to Assign ABOVE cash on hand. The row then renders with a
// '+' glyph, which is the truth, not a case to special-case away. "Held outside envelopes"
// (ADR 2026-09-10) is the same fact for CASH spending no envelope saw at all — uncategorized,
// or a category linked to none — which holds its own hole the same way; it is ≤ 0 by
// construction, so its row always reads as a credit. "Card spending not covered
// this month" is THIS month's uncovered card-spend activity alone — never a card's standing
// debt, which lives on the card and is paid down through its payment envelope. Sharing the
// builder is the point — the two surfaces used to drift on order, hideability and sign, so the
// same term read differently depending on which one you opened.

/** One term of the identity. */
export interface RtaLine {
  key: string
  label: string
  /** SIGNED, so the lines SUM to Ready to Assign — rendered UNSIGNED beside `op`. */
  value: number
  /** The value's own sign, rendered beside its unsigned amount; at $0 the term's structural
   *  glyph, so a subtracted term still reads as subtracted. An overdrawn cash balance, a
   *  negative cutover baseline, or envelopes net overspent (held < 0, so the term adds room
   *  back until month-end) therefore show the sign the total actually implies. */
  op: '+' | '−'
  /** Something worth acting on (card spending nothing has covered) or a defect. Absent = a
   *  plain fact: colour marks status here, never arithmetic, so what the envelopes hold is not
   *  painted like a problem. */
  tone?: 'warn'
  /** Which of YNAB's two sections this line belongs to — only set by `buildRtaFlowLines`.
   *  `buildRtaLines`'s rows leave this undefined; renderers that don't care about sections
   *  (the stock popover today, the rail) simply never read it. */
  section?: 'added' | 'deducted'
}

/** The identity's terms, in the currency the whole tab reports in. */
export interface RtaTerms {
  onBudgetCash: number
  /** Σ of every card's balance above zero — budget money beside cash. Absent/0 on most books. */
  cardCredit?: number
  /** SIGNED Σ of every envelope's Available — POSITIVE when money is reserved, NEGATIVE when
   *  envelopes are net overspent this month and are holding those holes until the roll. */
  heldInEnvelopes: number
  /** SIGNED, ≤ 0 — what CASH spending with no envelope at all (uncategorized, or a category
   *  linked to none) holds back until the roll charges it. */
  heldOutsideEnvelopes: number
  /** THIS month's uncovered card-spend activity. Not a balance: a prior month's uncovered
   *  swipe never reappears here — that debt lives on the card itself. */
  cardSpendNotCovered: number
  baseline: number
  assignedInFuture: number
  rta: number
}

// round-trip through cents so multi-term subtraction never leaves float dust visible.
function r2(n: number): number {
  return Math.round(n * 100) / 100
}

/** A subtracted term, rounded — and never `-0`, which is a real value a caller can assert on. */
function subtracted(n: number): number {
  const v = r2(n)
  return v === 0 ? 0 : -v
}

/** The glyph for a SIGNED line value; `dflt` is the term's structural sign, used only at $0. */
function glyph(v: number, dflt: '+' | '−'): '+' | '−' {
  return v > 0.005 ? '+' : v < -0.005 ? '−' : dflt
}

/** Cash on hand, minus everything already spoken for. Cash and what the envelopes hold are the
 *  identity's backbone and ALWAYS render — at $0, "Held in envelopes" reads as "nothing is
 *  spoken for", which is the fact the user came for, and when it is NEGATIVE it reads as a '+'
 *  because held overspending genuinely lends room back until month-end. The other three are
 *  terms most books never have, and three rows of "− $0.00" is noise, not transparency, so each
 *  hides at zero.
 *
 *  The sum is EXACT — the backend computes RTA from these same numbers — so there is no
 *  reconciling remainder by design. The "Unexplained" row appears only when a term went stale,
 *  and is named so it reads as a defect rather than one more budgeting fact. */
export function buildRtaLines(t: RtaTerms): RtaLine[] {
  const held = subtracted(t.heldInEnvelopes)
  const lines: RtaLine[] = [
    {
      key: 'cash',
      label: 'Cash on hand',
      value: r2(t.onBudgetCash),
      op: glyph(r2(t.onBudgetCash), '+'),
    },
    { key: 'held', label: 'Held in envelopes', value: held, op: glyph(held, '−') },
  ]
  const cardCredit = t.cardCredit ?? 0
  if (Math.abs(cardCredit) >= 0.005) {
    lines.splice(1, 0, {
      key: 'cardcredit',
      label: 'Credit on your cards',
      value: r2(cardCredit),
      op: '+',
    })
  }
  const hideable: Array<[key: string, label: string, value: number, tone?: 'warn']> = [
    ['heldoutside', 'Held outside envelopes', t.heldOutsideEnvelopes],
    ['cardspend', 'Card spending not covered this month', t.cardSpendNotCovered, 'warn'],
    ['baseline', 'Baseline', t.baseline],
    ['future', 'Assigned in future months', t.assignedInFuture],
  ]
  for (const [key, label, value, tone] of hideable) {
    if (Math.abs(value) < 0.005) continue
    const signed = subtracted(value)
    const line: RtaLine = { key, label, value: signed, op: glyph(signed, '−') }
    if (tone) line.tone = tone
    lines.push(line)
  }
  const residual = r2(
    t.rta -
      (t.onBudgetCash +
        cardCredit -
        t.heldInEnvelopes -
        t.heldOutsideEnvelopes -
        t.cardSpendNotCovered -
        t.baseline -
        t.assignedInFuture),
  )
  if (Math.abs(residual) > 0.01) {
    // The gap alone — never the balances that produced it. Privacy mode and a screen-share
    // must not be bypassed by whatever happens to be open in devtools.
    console.warn('[rtaLines] Ready to Assign does not match its own terms', { residual })
    lines.push({
      key: 'other',
      label: 'Unexplained — refresh',
      value: residual,
      op: residual < 0 ? '−' : '+',
      tone: 'warn',
    })
  }
  return lines
}

/** The flow-framed identity's terms — an exact rearrangement of `RtaTerms` (design doc §2.3),
 *  never a second independent computation. `rta` is the same displayed Ready-to-Assign the
 *  stock builder receives; the residual check below re-proves this decomposition sums to it
 *  every time the popover renders, exactly like `buildRtaLines`'s own check does today.
 *  `monthLabel`/`prevMonthLabel` are DISPLAY strings only (e.g. "August 2026") — never fed into
 *  any arithmetic here, only interpolated into copy, so a formatting change to `monthLabel()`
 *  cannot silently change a number. */
export interface RtaFlowTerms {
  carriedIn: number
  inflow: number
  assigned: number
  cashOverspendingPrev: number
  assignedInFuture: number
  rta: number
  monthLabel: string
  prevMonthLabel: string
}

/** YNAB's Added/Deducted flow ledger, built from `RtaFlowTerms` — literal YNAB wording per the
 *  owner's explicit "match YNAB, no deviation" (2026-09-13), confirmed via search-engine
 *  snippets of support.ynab.com/en_us/the-plan-header (design doc §8): "Ready to Assign Left
 *  Over from {Prev}", "Inflow: Ready to Assign Transactions in {Month}", "Cash Overspending in
 *  {Prev}". "Assigned in {Month}" is our own natural-language rendering of YNAB's DESCRIBED
 *  (not literally quoted) "dollars assigned to the spending categories this month" — the closest
 *  the search results gave. Every line's SECTION comes from its own VALUE's sign via the
 *  existing `glyph` helper — never a fixed slot — except `cashOverspendingPrev`, which is
 *  provably ≥ 0 (design doc §2.4's theorem: it contains zero card-caused overspending) and
 *  therefore ALWAYS Deducted, never sign-partitioned. Two previous-month lines render
 *  separately (owner decision, reversing the retired 2026-07 popover spec's fold) — see
 *  `PREV_CARRY_NOTE`'s sibling copy in `budgetRta.tsx` for why. */
// Owner ask, 2026-09-14: the popover's own month references drop the year ("August", not
// "August 2026") — `monthLabel`/`prevMonthLabel` keep the year everywhere ELSE they're used
// (the page header's own month nav), so this strips it right here rather than touching that
// shared formatter.
export function stripYear(label: string): string {
  return label.replace(/\s+\d{4}$/, '')
}

export function buildRtaFlowLines(t: RtaFlowTerms): RtaLine[] {
  const carriedIn = r2(t.carriedIn)
  const inflow = r2(t.inflow)
  const assigned = subtracted(t.assigned)
  const cashOverPrev = subtracted(t.cashOverspendingPrev)
  const month = stripYear(t.monthLabel)
  const prevMonth = stripYear(t.prevMonthLabel)
  const lines: RtaLine[] = [
    {
      key: 'inflow',
      label: `Inflow: Ready to Assign transactions in ${month}`,
      value: inflow,
      op: glyph(inflow, '+'),
      section: inflow < -0.005 ? 'deducted' : 'added',
    },
    {
      key: 'assigned',
      label: `Assigned in ${month}`,
      value: assigned,
      op: glyph(assigned, '−'),
      section: 'deducted',
    },
  ]
  // Owner ask: hide "left over from {Prev}" when it's genuinely $0 — unlike inflow/assigned,
  // which are always meaningful even at $0, a $0 carry-in says nothing a reader needs told.
  // `unshift`, not `push` — real YNAB shows "left over from" BEFORE "Inflow" whenever both
  // are present, so it has to land at the FRONT of the array, not wherever the code happens
  // to build it.
  if (Math.abs(t.carriedIn) >= 0.005) {
    lines.unshift({
      key: 'carriedin',
      label: `Ready to Assign left over from ${prevMonth}`,
      value: carriedIn,
      op: glyph(carriedIn, '+'),
      section: carriedIn < -0.005 ? 'deducted' : 'added',
    })
  }
  if (Math.abs(t.cashOverspendingPrev) >= 0.005) {
    lines.push({
      key: 'cashoverprev',
      label: `Cash overspending in ${prevMonth}`,
      value: cashOverPrev,
      op: glyph(cashOverPrev, '−'),
      section: 'deducted',
    })
  }
  if (Math.abs(t.assignedInFuture) >= 0.005) {
    const future = subtracted(t.assignedInFuture)
    lines.push({
      key: 'future',
      // Deliberately NOT literal — this is a clamped aggregate over potentially several
      // later months (design doc §7), so naming one specific month would be a false claim,
      // not just an imperfect one. The one place exact YNAB fidelity is not possible.
      label: 'Assigned in future months',
      value: future,
      op: glyph(future, '−'),
      section: 'deducted',
    })
  }
  const residual = r2(
    t.rta - (t.carriedIn + t.inflow - t.assigned - t.cashOverspendingPrev - t.assignedInFuture),
  )
  if (Math.abs(residual) > 0.01) {
    // The gap alone — never the balances that produced it (privacy mode + screen-share safe).
    console.warn('[rtaLines] Ready to Assign flow terms do not match its own terms', { residual })
    lines.push({
      key: 'other',
      label: 'Unexplained — refresh',
      value: residual,
      op: residual < 0 ? '−' : '+',
      tone: 'warn',
      section: 'deducted',
    })
  }
  return lines
}

/** Split a flow-line list into its two YNAB sections, in the order they were pushed. */
export function partitionRtaFlowLines(lines: RtaLine[]): { added: RtaLine[]; deducted: RtaLine[] } {
  return {
    added: lines.filter((l) => l.section === 'added'),
    deducted: lines.filter((l) => l.section !== 'added'),
  }
}
