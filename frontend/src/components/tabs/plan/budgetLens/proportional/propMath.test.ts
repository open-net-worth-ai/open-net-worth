// The 50/30/20 hero's arithmetic (propMath.ts) on the book's real month shape: the groups are the
// book's Zero-Based groups, each with an optional share of income (owner, 2026-09-26).
import { describe, expect, it } from 'vitest'
import { SAVE_COLOR, derive, fmt, groupFigures, paceLabel, projectedEnvelope } from './propMath'
import type { PropEnvelope, PropMonth } from './propApi'

const env = (
  id: string,
  groupId: string,
  activity: number,
  over: Partial<PropEnvelope> = {},
): PropEnvelope => ({
  id,
  label: id,
  color: '#3b86dd',
  group_id: groupId,
  group: groupId,
  category_ids: ['cat_' + id],
  cap: null,
  activity,
  history: { last: 0, avg3: 0, last_by_day: 0, avg3_by_day: 0 },
  ...over,
})

type Grp = PropMonth['groups'][number]
const grp = (id: string, target: number | null, over: Partial<Grp> = {}): Grp => ({
  id,
  name: id[0]!.toUpperCase() + id.slice(1),
  target,
  excluded: false,
  synced: false,
  activity: 0,
  ...over,
})

/** A month whose `savings` is what the server computes: income less every counted group's
 *  spend and the unassigned spend; its target is what the counted shares leave. */
const month = (over: Partial<PropMonth> = {}): PropMonth => {
  const base: PropMonth = {
    month: '2026-09',
    status: 'current',
    day: 15,
    days_in_month: 30,
    configured: true,
    groups: [grp('bills', 50), grp('fun', 30)],
    savings: { amount: 0, target: null },
    income: { amount: 10000, source: 'profile', profile: 10000, actual: 0 },
    income_categories: [],
    unassigned: { uncategorized: 0, no_envelope: 0 },
    envelopes: [],
    ...over,
  }
  const counted = new Set(base.groups.filter((g) => !g.excluded).map((g) => g.id))
  const spent = base.envelopes
    .filter((e) => counted.has(e.group_id))
    .reduce((t, e) => t + e.activity, 0)
  const shares = base.groups.filter((g) => !g.excluded).reduce((t, g) => t + (g.target ?? 0), 0)
  return {
    ...base,
    savings: over.savings ?? {
      amount:
        base.income.amount - spent - base.unassigned.uncategorized - base.unassigned.no_envelope,
      target: shares > 0 ? 100 - shares : null,
    },
  }
}

describe('derive', () => {
  it('measures each of the book’s groups — what it spent, and its target as money', () => {
    const d = derive(month({ envelopes: [env('rent', 'bills', 3000), env('dining', 'fun', 500)] }))
    expect(d.groups.map((f) => [f.g.name, f.spent, f.targetAmt])).toEqual([
      ['Bills', 3000, 5000],
      ['Fun', 500, 3000],
    ])
    expect(d.outflow).toBe(3500)
    expect(d.hasSpend).toBe(true)
    expect(d.saveTarget).toBe(20)
  })

  it('an Excluded group sits outside — neither savings nor the spent line counts it', () => {
    const d = derive(
      month({
        groups: [grp('bills', 50), grp('oneoff', null, { excluded: true })],
        envelopes: [env('rent', 'bills', 3000), env('flat', 'oneoff', 7653)],
      }),
    )
    expect(d.excluded.map((f) => f.g.name)).toEqual(['Oneoff'])
    expect(d.outflow).toBe(3000)
    expect(d.saved).toBe(7000)
  })

  it('savings is what is left after EVERY counted outflow — unassigned spend included', () => {
    const d = derive(
      month({
        envelopes: [env('rent', 'bills', 3000), env('gift', 'fun', 200)],
        unassigned: { uncategorized: 300, no_envelope: 100 },
      }),
    )
    expect(d.saved).toBe(10000 - 3000 - 200 - 300 - 100)
    expect(d.outflow).toBe(3600)
  })

  it('spend past income leads, by how much', () => {
    const d = derive(month({ envelopes: [env('rent', 'bills', 8000), env('trip', 'fun', 2500)] }))
    expect(d.verdict).toEqual({ text: `${fmt(500)} over your income`, bad: true })
    expect(d.tone).toBe('bad')
  })

  it('a closed month is told in the past tense', () => {
    const d = derive(
      month({
        status: 'past',
        envelopes: [env('rent', 'bills', 8000), env('trip', 'fun', 2500)],
      }),
    )
    expect(d.verdict).toEqual({ text: `You spent ${fmt(500)} more than you earned`, bad: true })
    expect(d.tone).toBe('bad')
  })

  it('names EVERY group that is over when savings fall short — never just the worst one', () => {
    const d = derive(
      month({
        status: 'past',
        envelopes: [env('rent', 'bills', 6000), env('dining', 'fun', 3500)],
      }),
    )
    expect(d.verdict).toEqual({ text: 'Bills and Fun ate into your savings', bad: true })
    expect(d.tone).toBe('warn')
  })

  it('a group over while savings still make their goal says both, calmly', () => {
    const d = derive(
      month({
        groups: [grp('bills', 30), grp('fun', 30)],
        status: 'past',
        envelopes: [env('rent', 'bills', 4000), env('dining', 'fun', 500)],
      }),
    )
    expect(d.verdict).toEqual({ text: 'Bills went over, but you still saved 55%', bad: false })
    expect(d.tone).toBe('good')
  })

  it('a group that spent exactly its caps is not "over" in the verdict, though past its share', () => {
    const d = derive(
      month({
        groups: [grp('bills', 30), grp('fun', 30), grp('extra', null)],
        envelopes: [
          env('rent', 'bills', 1000),
          env('dining', 'fun', 1000),
          env('x', 'extra', 500, { cap: 500 }),
        ],
      }),
    )
    expect(d.groups.find((f) => f.g.id === 'extra')!.over).toBe(true)
    expect(d.verdict.text).not.toMatch(/Extra/)
  })

  it('gives the hero no status tint before anything is spent', () => {
    expect(derive(month()).tone).toBe(null)
  })

  it('a running month is on track when nothing is over', () => {
    const d = derive(month({ envelopes: [env('rent', 'bills', 2000), env('dining', 'fun', 300)] }))
    expect(d.verdict).toEqual({ text: 'On track this month', bad: false })
    expect(d.tone).toBe('good')
  })

  it('a running month with an envelope past its cap is not on track, and names it', () => {
    const d = derive(
      month({
        envelopes: [
          env('Rent', 'bills', 2600, { cap: 2000 }),
          env('Entertainment', 'fun', 450, { cap: 300 }),
          env('Groceries', 'bills', 100, { cap: 900 }),
        ],
      }),
    )
    expect(d.verdict).toEqual({
      text: 'Rent (S$600) and Entertainment (S$150) are over their caps',
      bad: true,
    })
    expect(d.tone).toBe('warn')
  })

  it('one envelope over its cap reads in the singular, a closed month in the past tense', () => {
    const envelopes = [env('Rent', 'bills', 2600, { cap: 2000 })]
    expect(derive(month({ envelopes })).verdict.text).toBe('Rent (S$600) is over its cap')
    expect(derive(month({ envelopes, status: 'past' })).verdict.text).toBe(
      'Rent (S$600) was over its cap',
    )
  })

  it('more than two over their caps names the two biggest and counts the rest', () => {
    const d = derive(
      month({
        envelopes: [
          env('A', 'bills', 1100, { cap: 1000 }),
          env('B', 'bills', 1300, { cap: 1000 }),
          env('C', 'fun', 500, { cap: 400 }),
        ],
      }),
    )
    expect(d.verdict.text).toBe('B (S$300), A (S$100) and 1 more are over their caps')
  })

  it('an envelope exactly at its cap is still on track', () => {
    const d = derive(month({ envelopes: [env('Rent', 'bills', 2000, { cap: 2000 })] }))
    expect(d.verdict).toEqual({ text: 'On track this month', bad: false })
  })

  it('a closed month short of its savings goal says so in the past tense', () => {
    const d = derive(
      month({
        status: 'past',
        envelopes: [env('rent', 'bills', 5000), env('dining', 'fun', 3000)],
        unassigned: { uncategorized: 900, no_envelope: 0 },
      }),
    )
    expect(d.verdict).toEqual({ text: 'Saved 11%, your goal was 20%', bad: true })
    expect(d.tone).toBe('warn')
  })

  it('a counted group with no share is held to 0% once another has one — it spends savings', () => {
    const d = derive(
      month({
        status: 'past',
        groups: [grp('bills', 50), grp('fun', 30), grp('extra', null)],
        envelopes: [env('rent', 'bills', 5000), env('dining', 'fun', 3000), env('x', 'extra', 900)],
      }),
    )
    expect(d.groups.map((f) => [f.g.name, f.share, f.over])).toEqual([
      ['Bills', 50, false],
      ['Fun', 30, false],
      ['Extra', 0, true],
    ])
    expect(d.verdict).toEqual({ text: 'Extra ate into your savings', bad: true })
    expect(d.tone).toBe('warn')
  })

  it('with no shares set there is no savings goal to miss', () => {
    const d = derive(
      month({
        status: 'past',
        groups: [grp('bills', null), grp('fun', null)],
        envelopes: [env('rent', 'bills', 5000), env('dining', 'fun', 4500)],
      }),
    )
    expect(d.saveTarget).toBeNull()
    expect(d.hasTargets).toBe(false)
    expect(d.verdict).toEqual({ text: 'Saved 5%', bad: false })
    expect(d.tone).toBe('good')
  })

  it('flags caps that add up to more than a group’s target', () => {
    const d = derive(
      month({
        envelopes: [
          env('rent', 'bills', 1000, { cap: 4000 }),
          env('food', 'bills', 500, { cap: 1500 }),
          env('dining', 'fun', 100, { cap: 300 }),
        ],
      }),
    )
    expect(d.verdict).toEqual({ text: 'Bills caps add up to more than 50%', bad: true })
    expect(d.tone).toBe('warn')
    expect(d.insights.map((t) => t.kind)).toEqual(['save'])
  })

  it('pace compares a running month with last month by the same day', () => {
    const d = derive(
      month({
        envelopes: [
          env('rent', 'bills', 2000, {
            history: { last: 2500, avg3: 2400, last_by_day: 1500, avg3_by_day: 1400 },
          }),
        ],
      }),
    )
    expect(d.pace).toEqual({ byDay: true, now: 2000, then: 1500 })
    expect(d.paceDiff).toBe(500)
    expect(d.hasCmp).toBe(true)
  })

  it('with no income it says what was spent', () => {
    const d = derive(
      month({
        income: { amount: 0, source: 'actual', profile: null, actual: 0 },
        envelopes: [env('rent', 'bills', 1200)],
      }),
    )
    expect(d.verdict).toEqual({ text: `${fmt(1200)} spent this month`, bad: false })
    expect(d.tone).toBe(null)
  })
})

describe('the savings line', () => {
  it('leads the list: a closed month says what was saved against the goal, in money', () => {
    const d = derive(
      month({
        status: 'past',
        envelopes: [env('rent', 'bills', 5000), env('dining', 'fun', 3000)],
        unassigned: { uncategorized: 900, no_envelope: 0 },
      }),
    )
    expect(d.insights[0]).toEqual({
      kind: 'save',
      key: 'save',
      color: SAVE_COLOR,
      amount: 1100,
      pct: 11,
      target: 20,
      gap: -900,
      running: false,
      early: false,
    })
  })

  it('a running month is measured by where its pace is heading', () => {
    // Half the month gone, S$3,000 spent with no history: S$6,000 by month-end.
    const d = derive(month({ envelopes: [env('rent', 'bills', 3000)] }))
    expect(d.insights[0]).toMatchObject({ amount: 4000, pct: 40, gap: 2000, running: true })
  })

  it('the rest of a running month goes as usual — a pass-through expects its payback', () => {
    const d = derive(
      month({
        envelopes: [
          // Paid out by now every month, paid back after: usually nets to nothing.
          env('loan', 'bills', 3868, {
            history: { last: 0, avg3: 0, last_by_day: 3868, avg3_by_day: 3868 },
          }),
          // One big trip three months ago must not forecast another in the days left.
          env('travel', 'fun', 1432, {
            history: { last: 0, avg3: 3399, last_by_day: 0, avg3_by_day: 3251 },
          }),
        ],
      }),
    )
    expect(d.insights[0]).toMatchObject({ amount: 10000 - 1432 - 148, running: true })
  })

  it('has nothing to say without an income', () => {
    const d = derive(
      month({
        income: { amount: 0, source: 'actual', profile: null, actual: 0 },
        envelopes: [env('rent', 'bills', 1200)],
      }),
    )
    expect(d.insights).toEqual([])
  })
})

describe('the running month projection (owner, 2026-11-02: "heading for 22%" on the 2nd)', () => {
  const hist = (over: Partial<PropEnvelope['history']>): PropEnvelope['history'] => ({
    last: 0,
    avg3: 0,
    last_by_day: 0,
    avg3_by_day: 0,
    ...over,
  })

  it('a monthly payment made early is not expected again on its usual date', () => {
    // Usually sent mid-month; this month on the 2nd.
    const e = env('sister', 'bills', 844, {
      history: hist({ avg3: 835, median3: 833, rest_median3: 833 }),
    })
    expect(projectedEnvelope(e, true, 0.07)).toBe(844)
  })

  it('a quiet start still expects the usual month, up to the usual month', () => {
    const e = env('dining', 'fun', 13, {
      history: hist({ avg3: 1171, avg3_by_day: 8, median3: 900, rest_median3: 880 }),
    })
    expect(projectedEnvelope(e, true, 0.07)).toBe(13 + 880)
  })

  it('one outlier month is not the usual month — the median is', () => {
    // A trip in one of three months: mean S$1,307, median S$0.
    const e = env('travel', 'fun', 0, {
      history: hist({ avg3: 1307, avg3_by_day: 2, median3: 0, rest_median3: 0 }),
    })
    expect(projectedEnvelope(e, true, 0.07)).toBe(0)
  })

  it('a payback already in is not expected twice', () => {
    // Paid out and back already this month; usually paid back later.
    const e = env('loan', 'bills', 0, {
      history: hist({ avg3: 0, avg3_by_day: 3868, median3: 0, rest_median3: -3868 }),
    })
    expect(projectedEnvelope(e, true, 0.5)).toBe(0)
  })

  it('a closed month is what it spent', () => {
    const e = env('rent', 'bills', 2150, { history: hist({ avg3: 3000, median3: 3000 }) })
    expect(projectedEnvelope(e, false, 1)).toBe(2150)
  })
})

describe('the first days of a month', () => {
  const early = (over: Partial<PropMonth> = {}) =>
    derive(month({ day: 2, days_in_month: 30, ...over }))
  // Usually S$6,000 a month on fun — a forecast 40% short of a 20% goal by itself.
  const heavy = env('dining', 'fun', 13, {
    history: {
      last: 6000,
      avg3: 6000,
      last_by_day: 0,
      avg3_by_day: 0,
      median3: 6000,
      rest_median3: 6000,
    },
  })

  it('gives no savings verdict on what is mostly history', () => {
    const d = early({ envelopes: [env('rent', 'bills', 2000), heavy] })
    expect(d.verdict).toEqual({ text: `Too early to call — ${fmt(2013)} spent so far`, bad: false })
    expect(d.tone).toBe(null)
    expect(d.insights[0]).toMatchObject({ kind: 'save', running: true, early: true })
  })

  it('still says what has already happened', () => {
    const d = early({ envelopes: [env('rent', 'bills', 9000), heavy] })
    expect(d.verdict).toEqual({ text: 'Bills is already over', bad: true })
    expect(d.tone).toBe('warn')
  })

  it('judges the forecast once the first week is over', () => {
    const d = derive(month({ day: 8, envelopes: [env('rent', 'bills', 2000), heavy] }))
    expect(d.verdict.text).toMatch(/^Heading for /)
  })
})

describe('what is up on the usual month', () => {
  const was = (usual: number, last = usual) => ({
    history: { last, avg3: usual, last_by_day: last, avg3_by_day: usual },
  })

  it('a group up on usual names the envelope behind most of it, and not that envelope again', () => {
    const d = derive(
      month({
        envelopes: [
          env('rent', 'bills', 2000, was(2000)),
          env('travel', 'fun', 2480, was(530, 380)),
          env('dining', 'fun', 400, was(380)),
        ],
      }),
    )
    expect(d.insights.filter((t) => t.kind === 'up')).toEqual([
      {
        kind: 'up',
        key: 'g:fun',
        color: d.groups[1]!.color,
        name: 'Fun',
        amount: 1970,
        usual: 910,
        last: 760,
        lead: { name: 'travel', amount: 1950 },
      },
    ])
  })

  it('an envelope up on its own is named even when its group is not', () => {
    const d = derive(
      month({
        envelopes: [
          env('groceries', 'bills', 600, was(300)),
          env('rent', 'bills', 1700, was(2000)),
        ],
      }),
    )
    const ups = d.insights.flatMap((t) => (t.kind === 'up' ? [[t.name, t.amount]] : []))
    expect(ups).toEqual([['groceries', 300]])
  })

  it('says nothing with no history to compare against', () => {
    const d = derive(month({ envelopes: [env('travel', 'fun', 5000)] }))
    expect(d.insights.filter((t) => t.kind === 'up')).toEqual([])
  })
})

describe('groupFigures', () => {
  it('Left adds up capped rows only — spend with no cap is never "over" anything', () => {
    const { ct, hasCaps, left } = groupFigures([
      env('rent', 'bills', 2400, { cap: 2400 }),
      env('dining', 'bills', 350, { cap: 500 }),
      env('gift', 'bills', 900),
    ])
    expect(ct).toBe(2900)
    expect(hasCaps).toBe(true)
    expect(left).toBe(150)
  })
})

describe('paceLabel', () => {
  it('names last month and, for a running month, the day', () => {
    expect(paceLabel(month(), true)).toBe('August by the 15th')
    expect(paceLabel(month(), false)).toBe('August')
  })
})

describe('an envelope excluded on its own', () => {
  it('leaves its group’s figures and is listed apart', () => {
    const d = derive(
      month({
        envelopes: [env('rent', 'bills', 3000), { ...env('loan', 'bills', 3868), excluded: true }],
      }),
    )
    expect(d.groups.find((f) => f.g.id === 'bills')!.spent).toBe(3000)
    expect(d.leftOut.map((e) => e.id)).toEqual(['loan'])
  })
})
