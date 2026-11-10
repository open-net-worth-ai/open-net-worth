// budgetLens/proportional/propMath.ts — the 50/30/20 hero's arithmetic, kept pure so it can be
// tested without a DOM. The groups are the book's Zero-Based groups (owner, 2026-09-26), each with
// an optional share of income; savings is what the counted groups leave. Two deliberate choices:
//   · savings also subtracts spend no row owns (uncategorized / in no envelope) — leaving it out
//     would silently overstate savings (the server's figure, `m.savings`, already does);
//   · a running month's end is what each envelope has spent plus what the rest of the month usually
//     adds — so rent paid on the 1st isn't extrapolated, a quiet start still projects a normal
//     month, and a pass-through (you pay, you're paid back) expects its usual payback.
import { fSmart } from '../../planData'
import type { PropEnvelope, PropGroup, PropMonth } from './propApi'

/** Money as the design writes it: whole units, "−" for negatives ("S$2,551", "−S$40"). 50/30/20
 *  is a plan in round numbers — cents only appear where a real transaction amount is shown
 *  (the Activity column). */
export const fmt = (n: number): string => fSmart(Math.round(n))

/** A colour per group, by its place — Zero-Based groups carry none of their own. No red and no
 *  green: on this screen red means over and green means savings, so a group wearing either read
 *  as a verdict it wasn't (owner, 2026-09-26: "why don't Others show red?"). */
export const GROUP_COLORS = [
  '#3b86dd',
  '#e0a032',
  '#a47ae2',
  '#22b8cf',
  '#d96fb0',
  '#6c74dc',
  '#c7883e',
  '#b0746b',
]
export const SAVE_COLOR = '#34b08a'
export const EXCLUDED_COLOR = '#6b7484'

const sum = (xs: number[]) => xs.reduce((t, x) => t + x, 0)

export interface GroupFigures {
  g: PropGroup
  color: string
  rows: PropEnvelope[]
  spent: number
  /** Its target as money — null without a target or an income to take it from. */
  targetAmt: number | null
  /** Its share of income as the month is judged: its own, or 0 for a counted group left without
   *  one while another group has one — savings' goal is what the shares leave, so it assumes that
   *  group spends nothing. Null while no group has a share. */
  share: number | null
  capSum: number
  /** Past its share by more than a rounding point. */
  over: boolean
}

/** What the verdict and the share rows can't say: savings against the goal, in money (always first
 *  — the month's bottom line); a group or envelope spending more than usual; a group's caps adding
 *  up past its target. */
export type Insight =
  | { kind: 'caps'; key: string; color: string; name: string; target: number; amount: number }
  | {
      kind: 'save'
      key: string
      color: string
      /** Saved — or, while the month runs, what its pace leaves. Below zero past income. */
      amount: number
      pct: number
      target: number | null
      /** Past the goal (+) or short of it (−), in money; null with no goal. */
      gap: number | null
      running: boolean
      /** The running month's first days — the figure is mostly recent months, said as such. */
      early: boolean
    }
  | {
      kind: 'up'
      key: string
      color: string
      name: string
      /** How far past usual — the 3-month average, by this day while the month runs. */
      amount: number
      usual: number
      last: number
      /** The envelope that rose most, when it carries at least half the group's rise — it can
       *  carry more than all of it, when another envelope fell. */
      lead: { name: string; amount: number } | null
    }

export interface PropDerived {
  income: number
  /** Every group, in the book's order. */
  groups: GroupFigures[]
  /** Envelopes excluded on their own — outside the split, whatever group they sit in. */
  leftOut: PropEnvelope[]
  counted: GroupFigures[]
  excluded: GroupFigures[]
  unassigned: number
  /** Every counted outflow — the counted groups plus unassigned spend; savings and the "spent
   *  of" line are measured against it. */
  outflow: number
  /** What income is left — the server's figure; below zero when spending passed income. */
  saved: number
  saveTarget: number | null
  hasTargets: boolean
  hasSpend: boolean
  isCurrent: boolean
  isPast: boolean
  isFuture: boolean
  hasCmp: boolean
  pace: { byDay: boolean; now: number; then: number }
  paceDiff: number
  verdict: { text: string; bad: boolean }
  /** The hero's status tint; null while there's no income or spend to judge by. */
  tone: HeroTone | null
  insights: Insight[]
}

export type HeroTone = 'good' | 'warn' | 'bad'

/** Each envelope by month-end: what it has spent, plus what the rest of the month usually adds —
 *  the median of what each of the last 3 months added after this day. Signed, so a pass-through
 *  expects its usual payback. (It was a floor, max(spent, average): one past trip in the average
 *  forecast S$1,967 more travel in the month's last four days, and a loan paid back every
 *  month-end was never paid back.) Two limits, owner 2026-11-02 ("it's just the new month and it
 *  says I am heading to 22%"):
 *   · the rest never carries an envelope past its usual (median) month — a payment made early is
 *     not expected again on its usual date, and a payback already in is not expected twice;
 *   · medians, not means — one trip or tax bill in three months is not the usual month.
 *  With no history at all, the pace so far carries on. */
export function projectedEnvelope(e: PropEnvelope, isCurrent: boolean, elapsed: number): number {
  if (!isCurrent) return e.activity
  const h = e.history
  if (h.avg3 === 0 && h.avg3_by_day === 0) return elapsed > 0 ? e.activity / elapsed : e.activity
  const usual = h.median3 ?? h.avg3
  const rest = h.rest_median3 ?? h.avg3 - h.avg3_by_day
  const toUsual = usual - e.activity
  const add =
    rest >= 0 ? Math.min(rest, Math.max(0, toUsual)) : Math.max(rest, Math.min(0, toUsual))
  return e.activity + add
}

function projectedSpend(rows: PropEnvelope[], isCurrent: boolean, elapsed: number): number {
  return sum(rows.map((e) => projectedEnvelope(e, isCurrent, elapsed)))
}

/** The first days of a month say little about where it ends: no savings verdict before this day,
 *  only what has happened (spend past income, a group or envelope already over). */
export const EARLY_DAYS = 7

function figuresOf(m: PropMonth): GroupFigures[] {
  const income = m.income.amount
  const anyShare = m.groups.some((g) => !g.excluded && g.target !== null)
  let shade = 0
  return m.groups.map((g) => {
    // An envelope excluded on its own leaves its group's figures (the table lists it apart).
    const rows = m.envelopes.filter((e) => e.group_id === g.id && !e.excluded)
    return {
      g,
      color: g.excluded ? EXCLUDED_COLOR : GROUP_COLORS[shade++ % GROUP_COLORS.length]!,
      rows,
      spent: sum(rows.map((e) => e.activity)),
      targetAmt: g.target !== null && income > 0 ? Math.round((income * g.target) / 100) : null,
      share: g.target ?? (anyShare && !g.excluded ? 0 : null),
      capSum: sum(rows.map((e) => e.cap ?? 0)),
      over: false,
    }
  })
}

/** Up to three things spending more than usual — usual being the 3-month average, by the same day
 *  while the month runs (owner, 2026-09-26: "which envelope/group is more than last month or the
 *  last 3 months"). A group names the envelope leading its rise, and that envelope isn't listed
 *  again; a one-envelope group is its envelope, so only the envelope is. */
function ups(counted: GroupFigures[], byDay: boolean, floor: number): Insight[] {
  const usual = (e: PropEnvelope) => (byDay ? e.history.avg3_by_day : e.history.avg3)
  const last = (e: PropEnvelope) => (byDay ? e.history.last_by_day : e.history.last)
  const rise = (e: PropEnvelope) => e.activity - usual(e)
  const notable = (amount: number, base: number) => amount >= Math.max(floor, base * 0.15)
  const out: Insight[] = []
  const named = new Set<string>()
  for (const f of counted) {
    if (f.rows.length < 2) continue
    const was = sum(f.rows.map(usual))
    const amount = f.spent - was
    if (!notable(amount, was)) continue
    const top = f.rows.reduce((a, b) => (rise(b) > rise(a) ? b : a))
    const lead = rise(top) >= amount / 2 ? { name: top.label, amount: rise(top) } : null
    if (lead) named.add(top.id)
    out.push({
      kind: 'up',
      key: 'g:' + f.g.id,
      color: f.color,
      name: f.g.name,
      amount,
      usual: was,
      last: sum(f.rows.map(last)),
      lead,
    })
  }
  for (const e of counted.flatMap((f) => f.rows))
    if (!named.has(e.id) && notable(rise(e), usual(e)))
      out.push({
        kind: 'up',
        key: 'e:' + e.id,
        color: e.color,
        name: e.label,
        amount: rise(e),
        usual: usual(e),
        last: last(e),
        lead: null,
      })
  return out.sort((a, b) => b.amount - a.amount).slice(0, 3)
}

const names = (fs: GroupFigures[]) => {
  const ns = fs.map((f) => f.g.name)
  return ns.length > 2 ? ns.slice(0, -1).join(', ') + ' and ' + ns.at(-1) : ns.join(' and ')
}

/** "Rent (S$600) is over its cap" · "Rent (S$600) and Dining (S$150) are over their caps" — the
 *  biggest two by name, the rest counted. */
function overCapText(over: { label: string; by: number }[], isPast: boolean): string {
  const shown = over.slice(0, 2).map((e) => `${e.label} (${fmt(e.by)})`)
  const rest = over.length - shown.length
  const who = rest > 0 ? `${shown.join(', ')} and ${rest} more` : shown.join(' and ')
  const were = over.length > 1 ? (isPast ? 'were' : 'are') : isPast ? 'was' : 'is'
  return `${who} ${were} over ${over.length > 1 ? 'their caps' : 'its cap'}`
}

export function derive(m: PropMonth): PropDerived {
  const income = m.income.amount
  const groups = figuresOf(m)
  const counted = groups.filter((f) => !f.g.excluded)
  const excluded = groups.filter((f) => f.g.excluded)
  const unassigned = m.unassigned.uncategorized + m.unassigned.no_envelope
  const spent = sum(counted.map((f) => f.spent))
  const outflow = spent + unassigned
  const saveTarget = m.savings.target
  const isCurrent = m.status === 'current'
  const isPast = m.status === 'past'
  const isFuture = m.status === 'future'

  const base = income || outflow || 1
  const pct = (n: number) => Math.round((n / base) * 100)
  for (const f of counted) f.over = f.share !== null && pct(f.spent) - f.share > 2

  // Pace: a running month compares to last month BY THE SAME DAY; a closed one, whole months.
  const byDay = isCurrent
  const countedRows = counted.flatMap((f) => f.rows)
  const then = sum(countedRows.map((e) => (byDay ? e.history.last_by_day : e.history.last)))
  const hasCmp = !isFuture && m.envelopes.some((e) => e.history.last > 0)

  const elapsed = isCurrent ? m.day / m.days_in_month : 1
  const projSave = income
    ? Math.round(income - projectedSpend(countedRows, isCurrent, elapsed) - unassigned)
    : 0
  const saveP = pct(isCurrent ? Math.max(0, projSave) : Math.max(0, m.savings.amount))

  // The month's bottom line first, and the whole of it: naming only the worst group read as the
  // whole story while spend had passed income (owner, 2026-09-25).
  // A group that spent exactly its caps ("S$0.00 left") planned that spend: it is past its share of
  // income, which its bar shows, but "over" in the verdict means money past what it was given
  // (persona QA, 2026-10-14: "Others is over" beside S$0.00 left).
  const overGroups = counted.filter((f) => {
    const { hasCaps, left } = groupFigures(f.rows)
    return f.over && !(hasCaps && left >= 0)
  })
  const overNames = names(overGroups)
  const are = overGroups.length > 1 ? 'are' : 'is'
  const overIncome = income > 0 && outflow > income
  // Early in a running month the forecast is mostly history: no savings verdict on it yet.
  const early = isCurrent && m.day <= EARLY_DAYS
  const shortOfGoal =
    income > 0 && !early && !overIncome && saveTarget !== null && saveP - saveTarget < -1
  const capsOver = counted.find((f) => f.targetAmt !== null && f.capSum > f.targetAmt) ?? null
  // An envelope past its own cap is not "on track", whatever its group's total says: Rent over by
  // S$600 beside a roomy Groceries nets to nothing at the group (persona QA round 7, 2026-10-14).
  const overCap = counted
    .flatMap((f) => f.rows)
    .filter((e) => e.cap && Math.round(e.activity - e.cap) >= 1)
    .map((e) => ({ label: e.label, by: Math.round(e.activity - e.cap!) }))
    .sort((a, b) => b.by - a.by)
  const capsOnly = income > 0 && !overIncome && !shortOfGoal && !overGroups.length && !!capsOver
  // A closed month is told in the past tense, as how it ended (owner, 2026-09-26).
  const verdict = !income
    ? { text: `${fmt(outflow)} spent${isPast ? '' : ' this month'}`, bad: false }
    : overIncome
      ? {
          text: isPast
            ? `You spent ${fmt(outflow - income)} more than you earned`
            : `${fmt(outflow - income)} over your income`,
          bad: true,
        }
      : shortOfGoal && overGroups.length
        ? {
            text: isPast
              ? `${overNames} ate into your savings`
              : `${overNames} ${are} eating into your savings`,
            bad: true,
          }
        : shortOfGoal
          ? {
              text: isCurrent
                ? `Heading for ${saveP}% savings, your goal is ${saveTarget}%`
                : `Saved ${saveP}%, your goal was ${saveTarget}%`,
              bad: true,
            }
          : overGroups.length
            ? {
                text: isPast
                  ? `${overNames} went over, but you still saved ${saveP}%`
                  : early
                    ? `${overNames} ${are} already over`
                    : `${overNames} ${are} over, but savings are on track`,
                bad: early,
              }
            : overCap.length
              ? { text: overCapText(overCap, isPast), bad: true }
              : capsOnly
                ? {
                    text: `${capsOver!.g.name} caps add${isPast ? 'ed' : ''} up to more than ${capsOver!.g.target}%`,
                    bad: true,
                  }
                : isPast
                  ? {
                      text:
                        saveTarget === null
                          ? `Saved ${saveP}%`
                          : `Saved ${saveP}%, your goal was ${saveTarget}%`,
                      bad: false,
                    }
                  : early
                    ? { text: `Too early to call — ${fmt(outflow)} spent so far`, bad: false }
                    : { text: isCurrent ? 'On track this month' : 'On target', bad: false }
  // Owner, 2026-09-26: "on track is green a bit, off track is yellow a bit … red a bit" — red
  // once spending passes income, yellow for every other warning the verdict gives, green when it
  // gives none.
  const tone: HeroTone | null =
    !income || spent <= 0 ? null : overIncome ? 'bad' : verdict.bad ? 'warn' : early ? null : 'good'

  const insights: Insight[] = []
  const saving = isCurrent ? projSave : m.savings.amount
  if (income > 0 && !isFuture)
    insights.push({
      kind: 'save',
      key: 'save',
      color: SAVE_COLOR,
      amount: saving,
      pct: pct(saving),
      target: saveTarget,
      gap: saveTarget === null ? null : Math.round(saving - (income * saveTarget) / 100),
      running: isCurrent,
      early,
    })
  if (hasCmp && !isFuture) insights.push(...ups(counted, byDay, Math.max(1, base * 0.005)))
  if (capsOver && !capsOnly)
    insights.push({
      kind: 'caps',
      key: 'caps:' + capsOver.g.id,
      color: capsOver.color,
      name: capsOver.g.name,
      target: capsOver.g.target!,
      amount: capsOver.capSum - capsOver.targetAmt!,
    })

  return {
    income,
    groups,
    leftOut: m.envelopes.filter((e) => e.excluded),
    counted,
    excluded,
    unassigned,
    outflow,
    saved: m.savings.amount,
    saveTarget,
    hasTargets: counted.some((f) => f.g.target !== null),
    hasSpend: spent > 0,
    isCurrent,
    isPast,
    isFuture,
    hasCmp,
    pace: { byDay, now: spent, then },
    paceDiff: spent - then,
    verdict,
    tone,
    insights,
  }
}

/** "25th". */
export const ordinal = (d: number): string =>
  d +
  (d % 10 === 1 && d !== 11
    ? 'st'
    : d % 10 === 2 && d !== 12
      ? 'nd'
      : d % 10 === 3 && d !== 13
        ? 'rd'
        : 'th')

/** "August by the 25th" / "August" — what the pace line compares against. */
export function paceLabel(m: PropMonth, byDay: boolean): string {
  const [y, mo] = m.month.split('-').map(Number)
  const prev = new Date(y!, (mo ?? 1) - 2, 1).toLocaleDateString('en', { month: 'long' })
  return prev + (byDay ? ' by the ' + ordinal(m.day) : '')
}

/** A group's own figures. Left adds up its rows, as Available does in Zero-Based: an envelope with
 *  no cap has no Left, so its spend is never "over" anything. */
export function groupFigures(list: PropEnvelope[]) {
  const ct = list.reduce((t, e) => t + (e.cap ?? 0), 0)
  const capped = list.filter((e) => e.cap)
  const left = capped.reduce((t, e) => t + (e.cap ?? 0) - e.activity, 0)
  return { ct, hasCaps: capped.length > 0, left }
}
