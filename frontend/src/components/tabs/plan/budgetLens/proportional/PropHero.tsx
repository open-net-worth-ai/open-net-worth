// budgetLens/proportional/PropHero.tsx — the 50/30/20 hero. Two states: before anything is spent it
// shows the PLAN (each group's share of your income, and what's left to save); after, the REAL
// month — a verdict over two columns: one small tile per counted group and Savings (a bar of what
// it actually took, a tick at its share, "61% / 30%") on the left, what's spending more than usual
// on the right. The groups are the
// book's Zero-Based groups (2026-09-26).
import type { CSSProperties } from 'react'
import { Button } from '../../../../ui'
import { Spark } from '../MethodCard'
import { SAVE_COLOR, fmt, ordinal, paceLabel } from './propMath'
import type { HeroTone, Insight, PropDerived } from './propMath'
import type { PropMonth } from './propApi'

/** The hero's status tint (plan.css) — green on track, yellow warned, red past income. */
const TONE_CLASS: Record<HeroTone, string> = {
  good: ' pt-hero--good',
  warn: ' pt-hero--warn',
  bad: ' pt-hero--bad',
}

interface Share {
  key: string
  name: string
  color: string
  /** What it took, as a percent of income (or of spending, with no income). */
  pct: number
  target: number | null
  bad: boolean
}

function shares(d: PropDerived): Share[] {
  const base = d.income > 0 ? d.income : d.outflow || 1
  // A group with no envelopes and no share says nothing a bar could show.
  const rows: Share[] = d.counted
    .filter((f) => f.rows.length || f.g.target !== null)
    .map((f) => ({
      key: f.g.id,
      name: f.g.name,
      color: f.color,
      pct: (f.spent / base) * 100,
      target: d.income > 0 ? f.share : null,
      bad: f.over,
    }))
  if (d.income > 0)
    rows.push({
      key: 'savings',
      name: 'Savings',
      color: SAVE_COLOR,
      pct: (Math.max(0, d.saved) / d.income) * 100,
      target: d.saveTarget,
      bad: d.saved < 0 || (d.saveTarget !== null && (d.saved / d.income) * 100 < d.saveTarget),
    })
  return rows
}

/** Which income the savings are measured against, so this figure is never read as the same claim as
 *  Reports' last-month rate or Cash Flow's savings rate. A running month always uses the usual
 *  monthly income from the profile (this month's pay may not have arrived); a closed one the income
 *  that did arrive. */
function savingsBasis(m: PropMonth): string {
  const month = new Date(m.month + '-01T00:00:00').toLocaleDateString('en', { month: 'long' })
  if (m.income.source === 'actual') return `${month}, from the income that arrived`
  return `${month}, measured against your usual monthly income`
}

function savingsBasisTitle(m: PropMonth): string {
  if (m.income.source === 'actual') return 'The income that arrived in the month, less its spending'
  const arrived = m.income.actual
  const usual = `Uses your usual monthly income (${fmt(m.income.amount)})`
  return arrived > 0
    ? `${usual}; ${fmt(arrived)} has arrived so far this month.`
    : `${usual}; this month's income has not arrived yet.`
}

/** Savings against the goal, in money: "You saved S$5,363 (31%) — S$730 short of your 35% goal";
 *  a running month says where its pace is heading. */
function SavingsLine({ t, m }: { t: Extract<Insight, { kind: 'save' }>; m: PropMonth }) {
  const month = new Date(m.month + '-01T00:00:00').toLocaleDateString('en', { month: 'long' })
  const lead = t.early
    ? t.amount < 0
      ? `If ${month} goes like recent months, you'd spend ${fmt(-t.amount)} more than you earn`
      : `If ${month} goes like recent months, you'd save ${fmt(t.amount)}`
    : t.amount < 0
      ? `${t.running ? 'On pace to spend' : 'You spent'} ${fmt(-t.amount)} more than you ${t.running ? 'earn' : 'earned'}`
      : `${t.running ? 'On pace to save' : 'You saved'} ${fmt(t.amount)}`
  const goal =
    t.gap === null || t.target === null
      ? ''
      : Math.abs(t.pct - t.target) <= 1
        ? `, right on your ${t.target}% goal`
        : `, ${fmt(Math.abs(t.gap))} ${t.gap < 0 ? 'short of' : 'past'} your ${t.target}% goal`
  return (
    <span className="pt-ins-t" title={savingsBasisTitle(m)}>
      <b>{lead}</b>
      {t.amount >= 0 && ` (${t.pct}%)`}
      {goal}
      <span className="pt-ins-basis">{savingsBasis(m)}</span>
    </span>
  )
}

/** "Wants is S$2,300 above usual, led by Travel (+S$1,950)" — "was", once the month has closed;
 *  its title says what usual was. */
function InsightLine({ t, m, byDay }: { t: Insight; m: PropMonth; byDay: boolean }) {
  const past = m.status === 'past'
  if (t.kind === 'save') return <SavingsLine t={t} m={m} />
  if (t.kind === 'caps')
    return (
      <span className="pt-ins-t">
        {t.name} caps add{past ? 'ed' : ''} up to <b>{fmt(t.amount)}</b> more than {t.target}%
      </span>
    )
  const when = byDay ? ' by the ' + ordinal(m.day) : ''
  return (
    <span
      className="pt-ins-t"
      title={`Usual: ${fmt(t.usual)}, your 3-month average${when}. ${paceLabel(m, byDay)}: ${fmt(t.last)}.`}
    >
      <b>{t.name}</b> {past ? 'was' : 'is'} <b>{fmt(t.amount)}</b> above usual
      {t.lead && `, led by ${t.lead.name} (+${fmt(t.lead.amount)})`}
    </span>
  )
}

export function PropHero({
  m,
  d,
  onLucius,
  onConfigure,
  onCapsFromAverage,
}: {
  m: PropMonth
  d: PropDerived
  onLucius: (prompt: string) => void
  /** Opens Configure — where each group's share is set. */
  onConfigure: () => void
  /** Offered while nothing has a cap yet — every envelope's 3-month average, rounded up. */
  onCapsFromAverage?: () => void
}) {
  const { income } = d
  const monthName = new Date(m.month + '-01T00:00:00').toLocaleDateString('en', { month: 'long' })

  if (!d.hasSpend) {
    const planned = d.counted.filter((f) => f.g.target !== null)
    const noCaps = d.counted.every((f) => f.capSum === 0)
    // Suggesting caps is only an offer while an envelope still has none (owner, 2026-09-28).
    const uncapped = d.counted.some((f) => f.rows.some((e) => !(e.cap && e.cap > 0)))
    return (
      <div className="pt-hero pt-plan">
        <div className="pt-plan-top">
          <span className="pt-verdict">
            {d.isFuture ? `${monthName} hasn’t started` : 'Nothing spent yet'}
          </span>
          {income > 0 && <span className="pt-plan-inc">Your {fmt(income)} monthly income</span>}
          <span className="bo-spacer" />
          {!d.hasTargets ? (
            <Button variant="secondary" size="dense" onClick={onConfigure}>
              Give each group a share
            </Button>
          ) : (
            onCapsFromAverage &&
            noCaps && (
              <Button variant="secondary" size="dense" onClick={onCapsFromAverage}>
                Set caps from my average spending
              </Button>
            )
          )}
          {(!d.hasTargets || uncapped) && (
            <Button
              variant="primary"
              size="dense"
              className="fi-lucius"
              iconLeft={<Spark size={15} />}
              onClick={() =>
                onLucius(
                  d.hasTargets
                    ? 'Suggest caps for my envelopes that fit each group’s share of my income'
                    : 'Suggest a share of my income for each budget group, from my spending',
                )
              }
            >
              {d.hasTargets ? 'Let Lucius suggest caps' : 'Let Lucius suggest shares'}
            </Button>
          )}
        </div>
        {planned.length > 0 && income > 0 && (
          <>
            <div
              className="pt-plan-bar"
              style={
                {
                  gridTemplateColumns: [...planned.map((f) => f.g.target), d.saveTarget ?? 0]
                    .map((t) => `${t}fr`)
                    .join(' '),
                } as CSSProperties
              }
            >
              {planned.map((f) => (
                <span key={f.g.id} style={{ background: f.color }} />
              ))}
              <span style={{ background: SAVE_COLOR }} />
            </div>
            <div className="pt-plan-cols pt-plan-cols--wrap">
              {[
                ...planned.map((f) => ({ k: f.g.id, n: f.g.name, c: f.color, t: f.g.target! })),
                { k: 'savings', n: 'Savings', c: SAVE_COLOR, t: d.saveTarget ?? 0 },
              ].map((p) => (
                <div key={p.k} className="pt-plan-col">
                  <span className="k">
                    <i style={{ background: p.c }} />
                    <span>
                      {p.n} <b>{p.t}%</b>
                    </span>
                  </span>
                  <span className="v">
                    <b>{fmt(Math.round((income * p.t) / 100))}</b>{' '}
                    {p.k === 'savings' ? 'to save' : 'to spend'}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    )
  }

  const closed = d.isPast && m.income.source === 'actual'
  // Balanced rows: five tiles beside the insights sit 3 + 2, never 4 + 1.
  const tiles = shares(d)
  const cols = Math.ceil(tiles.length / Math.ceil(tiles.length / (d.insights.length ? 3 : 5)))
  const vs = paceLabel(m, d.pace.byDay)
  return (
    <div className={'pt-hero pt-plan' + (d.tone ? TONE_CLASS[d.tone] : '')}>
      <div className="pt-plan-top">
        <span className={'pt-verdict' + (d.tone === 'bad' ? ' bad' : d.verdict.bad ? ' warn' : '')}>
          {d.verdict.text}
        </span>
        <span className="bo-spacer" />
        {income > 0 && (
          <span
            className="pt-plan-inc"
            title={closed ? 'Against the income that actually arrived' : 'Against your usual month'}
          >
            {fmt(d.outflow)} spent of {fmt(income)}
          </span>
        )}
        {d.hasCmp && Math.abs(d.paceDiff) >= 1 && (
          <span
            className={'pt-plan-pace ' + (d.paceDiff > 0 ? 'up' : 'down')}
            title={`Spending so far, compared with ${vs}`}
          >
            <b>{(d.paceDiff > 0 ? '+' : '−') + fmt(Math.abs(d.paceDiff))}</b> spending vs{' '}
            {vs.split(' ')[0]!.slice(0, 3)}
            {d.pace.byDay ? ` by the ${ordinal(m.day)}` : ''}
          </span>
        )}
      </div>
      <div className="pt-hero-body">
        <div className="pt-shares" style={{ '--cols': cols } as CSSProperties}>
          {tiles.map((s) => {
            // A group whose refunds outran its spend took nothing: an empty bar, never a negative
            // width (the browser drops that and keeps the previous month's bar).
            const shown = Math.max(0, Math.min(100, s.pct))
            const within =
              s.target === null || s.key === 'savings' ? shown : Math.min(shown, s.target)
            return (
              <div key={s.key} className="pt-share">
                <span className="pt-share-l">
                  <i style={{ background: s.color }} />
                  <span>{s.name}</span>
                </span>
                <span
                  className="pt-share-bar"
                  style={{ '--c': s.color } as CSSProperties}
                  role="img"
                  aria-label={
                    `${s.name}: ${Math.round(s.pct)}% of ${income > 0 ? 'income' : 'spending'}` +
                    (s.target !== null ? `, target ${s.target}%` : '')
                  }
                >
                  {shown > within && <i className="over" style={{ width: shown + '%' }} />}
                  <i
                    className={'in' + (shown > within ? ' cut' : '')}
                    style={{ width: within + '%' }}
                  />
                  {!!s.target && <b className="tick" style={{ left: s.target + '%' }} />}
                </span>
                <span className="pt-share-n">
                  <b className={s.bad ? 'bad' : ''}>{Math.round(s.pct)}%</b>
                  {s.target !== null && ` / ${s.target}%`}
                </span>
              </div>
            )
          })}
        </div>
        {d.insights.length > 0 && (
          <ul className="pt-ins">
            {d.insights.map((t) => (
              <li key={t.key}>
                <i style={{ background: t.color }} />
                <InsightLine t={t} m={m} byDay={d.pace.byDay} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
