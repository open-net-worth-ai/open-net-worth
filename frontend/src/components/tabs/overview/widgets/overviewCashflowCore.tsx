// Overview v2 — Overview + Cash-flow-core widget group: needs attention, daily
// briefing, Ask Lucius, summary card, next 30 days, today's movers. Ported
// from the production Overview.tsx's risk-rule engine + upcoming-commitments
// list; briefing/movers now route through the shared WidgetShell like every
// other widget in this file (item 8 of the design-parity audit) — their own
// WealthBriefing/TopDailyMovers header chrome is suppressed via a `hideTitle`/
// `hideHeader` prop on those shared components so a rename only shows once.
//
// `lux-ask` is the "Ask Lucius" generated-widget type — its Ask/regenerate
// action opens the REAL Lucius panel/dock with the prompt (never a fabricated
// inline answer); see ADR 2026-07-22-overview-tab-redesign-faithful-port.md,
// decision 5.

import { useState } from 'react'
import type { ComponentType, ReactElement } from 'react'
import { fBase, fDate, fMonth } from '../../../../utils/format'
import { Button, MaskedText, Money, Skeleton, Text, TextArea } from '../../../ui'
import { WealthBriefing } from '../../../shared/WealthBriefing'
import { Spark } from '../../../../lucius/identity'
import { TopDailyMovers } from '../../../shared/TopDailyMovers'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { useAttentionFlags, type AttentionIcon } from '../attentionFlags'
import { useAnalytics, useCommitments, useNetWorthSeries, useValuedRows } from '../useWidgetData'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { TimelineStrip } from '../Timeline'

// ── Needs attention ──────────────────────────────────────────────────────────

// Small inline glyphs matching definitions.tsx's own `icon()` visual language
// (stroke, not fill) — ported from the prototype's `panels.jsx` `I.clock`/
// `I.gap`/`I.pie`/`I.card` (byte-identical paths); `cash` is a new equivalent
// for the two cash-related risks the prototype doesn't itself enumerate.
function RiskIcon({ name }: { name: AttentionIcon }): ReactElement {
  const common = {
    width: 15,
    height: 15,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.9,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  switch (name) {
    case 'clock':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3.2 2" />
        </svg>
      )
    case 'gap':
      return (
        <svg {...common}>
          <path d="M12 3l10 18H2z" />
          <line x1="12" y1="10" x2="12" y2="14.5" />
          <circle cx="12" cy="17.6" r=".4" fill="currentColor" />
        </svg>
      )
    case 'pie':
      return (
        <svg {...common}>
          <path d="M21 12A9 9 0 1 1 12 3" />
          <path d="M12 3a9 9 0 0 1 9 9h-9z" />
        </svg>
      )
    case 'card':
      return (
        <svg {...common}>
          <rect x="2.5" y="5.5" width="19" height="13" rx="2.2" />
          <path d="M2.5 10h19" />
        </svg>
      )
    case 'cash':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 6.5v11M15.5 9c0-1.4-1.5-2.5-3.5-2.5S8.5 7.6 8.5 9c0 3 7 1.6 7 4.5 0 1.4-1.6 2.5-3.5 2.5S8.5 15 8.5 13.4" />
        </svg>
      )
  }
}

function AllClearIcon(): ReactElement {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  )
}

const Attention: ComponentType<WidgetContentProps> = ({
  instance,
  def,
  onNavigate,
  onAskLucius,
}) => {
  const { flags: risks, loading } = useAttentionFlags()

  // "All clear" is a claim about data — never made before the data has loaded.
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={2} />
      </WidgetShell>
    )
  }
  const cap = rowCap(instance, def)
  const shownCount = cap ? Math.min(cap, risks.length) : risks.length
  const visible = risks.slice(0, shownCount)
  const extra = risks.length - shownCount

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={
        risks.length ? (
          <>
            <b style={{ color: 'var(--warn)' }}>{risks.length}</b> flag
            {risks.length === 1 ? '' : 's'}
          </>
        ) : undefined
      }
    >
      <div className="ov-risks">
        {visible.length ? (
          <>
            {visible.map((r) => (
              <div key={r.id} className={`ov-risk ov-risk--${r.sev}`}>
                <div className="ov-risk-ic">
                  <RiskIcon name={r.icon} />
                </div>
                <div className="ov-risk-tx">
                  {/* The title itself IS the "Review" action when a nav target exists —
                      one fewer persistent CTA competing with Ask Lucius/dismiss for a
                      compact row's limited width. */}
                  {r.nav && onNavigate ? (
                    <button
                      type="button"
                      className="ov-risk-title-link"
                      onClick={() => onNavigate(r.nav!.tab)}
                    >
                      <MaskedText>{r.title}</MaskedText>.
                    </button>
                  ) : (
                    <b>
                      <MaskedText>{r.title}</MaskedText>.
                    </b>
                  )}{' '}
                  <span>
                    <MaskedText>{r.desc}</MaskedText>
                  </span>
                </div>
                <div className="ov-risk-actions">
                  {onAskLucius && (
                    <button
                      type="button"
                      className="ov-risk-link ov-risk-link--ask"
                      onClick={() => onAskLucius(`Tell me more about: ${r.title}`)}
                      aria-label="Ask Lucius"
                      title="Ask Lucius"
                    >
                      <Spark size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
            {extra > 0 && (
              <div className="ov-more">
                +{extra} more flag{extra === 1 ? '' : 's'}. Make the widget taller to see them
              </div>
            )}
          </>
        ) : (
          <div className="ov-allclear">
            <span className="ov-allclear-ic">
              <AllClearIcon />
            </span>
            All clear. Nothing needs you today. Next check-in lands with tomorrow's briefing.
          </div>
        )}
      </div>
    </WidgetShell>
  )
}

// ── Daily briefing / Ask Lucius ──────────────────────────────────────────────

// Routed through WidgetShell (item 8) so a rename actually applies — same
// pattern as every other widget below. WealthBriefing's own `hideTitle`
// suppresses its redundant internal "Lucius" label ONLY; the date/settings/
// refresh/collapse controls it renders are real functionality, not title
// chrome, so they're kept exactly as-is, not hidden.
const Briefing: ComponentType<WidgetContentProps> = ({ instance, def, onAskLucius }) => (
  <WidgetShell
    title={instance.title}
    base={def.title}
    lead={
      <span className="ov-lucius-spark">
        <Spark size={15} />
      </span>
    }
  >
    <WealthBriefing hideTitle onChatSuggest={onAskLucius} />
  </WidgetShell>
)

const LuxAsk: ComponentType<WidgetContentProps> = ({ instance, def, onPatch, onAskLucius }) => {
  const [draft, setDraft] = useState((instance.config?.prompt as string) || '')
  const submit = () => {
    const q = draft.trim()
    if (!q) return
    onPatch({ config: { ...instance.config, prompt: q } })
    onAskLucius?.(q)
  }
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      lead={
        <span className="ov-lucius-spark">
          <Spark size={15} />
        </span>
      }
    >
      <div className="ov-lux-ask">
        <TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask Lucius anything about your money…"
          rows={3}
        />
        <div className="ov-lux-ask-actions">
          <Button
            size="sm"
            variant="primary"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={submit}
            disabled={!draft.trim()}
          >
            Ask Lucius
          </Button>
        </div>
        <div className="ov-lux-ask-suggestions">
          {[
            'Give me my morning money briefing',
            'How much am I spending on coffee?',
            'How many months of runway do I have?',
            'Is my NVDA position too big?',
          ].map((s) => (
            <button key={s} type="button" className="ov-lux-chip" onClick={() => setDraft(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>
    </WidgetShell>
  )
}

// ── Summary card ─────────────────────────────────────────────────────────────

const Summary: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  // Net worth is the ONE server-side implementation; the "this month" change is the server's
  // own month-to-date figure, computed WITHIN the history series. Differencing today's
  // `net_worth()` against a history point (what this card used to do) charges the known
  // reconciliation gap between those two computations to the month as if it were a real move.
  const { netWorth, loading: portfolioLoading, error: portfolioError } = useAnalytics()
  const { stats: networth } = useNetWorthSeries()
  const subtitle = fMonth(new Date(), 'long')

  if (portfolioLoading && netWorth == null) {
    return (
      <WidgetShell title={instance.title} base={def.title} subtitle={subtitle} bodyAlign="center">
        <Skeleton height={44} />
      </WidgetShell>
    )
  }
  if (portfolioError && netWorth == null) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Couldn't load your net worth — try reloading." />
      </WidgetShell>
    )
  }

  const total = netWorth ?? 0
  const delta = networth?.month_to_date?.delta ?? null
  return (
    <WidgetShell title={instance.title} base={def.title} subtitle={subtitle} bodyAlign="center">
      <div className="ov-summary">
        <Text as="span" size="xl" weight={700}>
          <Money as="span">{fBase(total)}</Money>
        </Text>
        {delta !== null && (
          <Text as="span" size="sm" tone={delta >= 0 ? 'good' : 'bad'}>
            {delta >= 0 ? '↑' : '↓'} <Money as="span">{fBase(Math.abs(delta))}</Money> this month
          </Text>
        )}
      </div>
    </WidgetShell>
  )
}

// ── Next 30 days ─────────────────────────────────────────────────────────────

// One colour per direction, the calendar's: money coming in is green, money going out red.
// A colour per kind (installment / action / bill / card) made the strip a legend to learn.
const INCOMING_KINDS = new Set(['income', 'funding'])
const kindColor = (kind: string): string =>
  INCOMING_KINDS.has(kind) ? 'var(--good)' : 'var(--bad)'

function daysFromToday(dateStr: string): number {
  const d = new Date(dateStr)
  const t = new Date()
  t.setHours(0, 0, 0, 0)
  return Math.round((d.getTime() - t.getTime()) / 86400000)
}

/** One line of the card: a commitment payment or funding draw, a recurring bill, a card's
 *  statement, or recurring income landing. */
interface UpcomingRow {
  date: string
  kind: string
  label: string
  sub: string
  amount: number | null
}

const Upcoming: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const {
    loading,
    upcoming: feed,
    overdueCount,
    overdueTotal,
    expectedIncome,
    expectedIncomeTotal,
    bills,
    cardBills,
    goingOutTotal,
  } = useCommitments()
  // The feed carries past-due items too. They are not "the next 30 days": one red line says
  // how many payments are past due and what they add up to (server-side, money going out
  // only), and links to Commitments; the list is what is actually coming up.
  // The list matches the title and the "going out" total: today through +30 days. What lies
  // further out isn't mentioned at all — "All commitments" is the way there; a "6 later" count
  // under a "Next 30 days" heading only raised the question of what it meant.
  // It is the next 30 days of money, not of commitments: recurring bills (rent, subscriptions)
  // and card statements due are listed and counted in "going out" beside commitments, and
  // recurring income (a salary on the 25th) sits in the same list with its own total.
  const upcoming: UpcomingRow[] = [
    ...feed
      .filter((e) => {
        const d = daysFromToday(e.date)
        return d >= 0 && d <= 30
      })
      .map((e) => ({
        date: e.date,
        kind: e.kind,
        label: e.label,
        sub: e.commitment_name,
        amount: e.amount_base,
      })),
    ...bills.map((e) => ({
      date: e.date,
      kind: 'bill',
      label: e.label,
      sub: 'Recurring bill',
      amount: e.amount_base,
    })),
    ...cardBills.map((e) => ({
      date: e.date,
      kind: 'card',
      label: e.label,
      sub: e.estimated ? 'Card bill (estimated)' : 'Card bill',
      amount: e.amount_base,
    })),
    ...expectedIncome.map((e) => ({
      date: e.date,
      kind: 'income',
      label: e.label,
      sub: 'Recurring income',
      amount: e.amount_base,
    })),
  ].sort((a, b) => a.date.localeCompare(b.date))
  const cap = rowCap(instance, def) ?? Infinity
  const visible = upcoming.slice(0, cap)
  // A book with no dated bill at all (nothing in the 120-day feed, nothing overdue) has not
  // said next month costs nothing: it has not said anything. "S$0.00 going out" read as the
  // former (persona QA round 7), so the headline gives way to an invitation to add due dates.
  const noDatedBills =
    feed.length === 0 && overdueCount === 0 && bills.length === 0 && cardBills.length === 0
  const overdueLine = overdueCount > 0 && (
    <button
      type="button"
      className="ov-upcoming-overdue"
      onClick={onNavigate ? () => onNavigate('commitments') : undefined}
      disabled={!onNavigate}
    >
      {overdueCount} overdue
      {overdueTotal > 0 && (
        <>
          {' · '}
          <Money as="span">{fBase(overdueTotal)}</Money>
        </>
      )}
    </button>
  )
  const footer = onNavigate && (
    <button type="button" className="ov-panel-link" onClick={() => onNavigate('commitments')}>
      {noDatedBills ? 'Add due dates' : 'All commitments'}
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    </button>
  )
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={3} />
      </WidgetShell>
    )
  }
  if (!upcoming.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        {overdueLine}
        <WidgetPlaceholder
          detail={
            noDatedBills
              ? 'No dated bills yet. Add due dates to a bill or installment and what is coming up shows here.'
              : overdueCount
                ? 'Nothing else due to pay in the next 30 days.'
                : 'Nothing due to pay in the next 30 days.'
          }
        />
      </WidgetShell>
    )
  }
  // The $ total of what goes out within the strip's own [0,30] axis (commitments due, by the
  // definition the Commitments masthead reads, plus bills and card statements), and of the
  // recurring income landing in it — both summed server-side.
  const strip = (
    <TimelineStrip
      axis={[0, 30]}
      ticks={[
        { pos: 0, label: 'today' },
        { pos: 30, label: '+30d' },
      ]}
      events={upcoming.map((e) => ({
        pos: daysFromToday(e.date),
        color: kindColor(e.kind),
        dateLabel: '',
        label: e.label,
      }))}
    />
  )
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={noDatedBills ? 'No dated bills yet' : undefined}
      subhead={strip}
      footer={footer}
    >
      {/* The two totals read as a pair, in the body with exact amounts: as a header meta,
          "S$22,537.28 going out" cut the title off on a phone. */}
      <div className="ov-upcoming-totals">
        {!noDatedBills && (
          <p className="ov-upcoming-out">
            <Money as="span">{fBase(goingOutTotal)}</Money> going out
          </p>
        )}
        {expectedIncomeTotal > 0 && (
          <p className="ov-upcoming-income">
            <Money as="span">{fBase(expectedIncomeTotal)}</Money> coming in
          </p>
        )}
        {overdueLine}
      </div>
      <div className="ov-upcoming">
        {visible.map((e, i) => {
          const days = daysFromToday(e.date)
          return (
            <div key={i} className="ov-upcoming-row">
              <span className="ov-upcoming-date">{fDate(e.date)}</span>
              <span className="ov-upcoming-dot" style={{ background: kindColor(e.kind) }} />
              <span className="ov-upcoming-label">
                {e.label} <span className="ov-upcoming-sub">• {e.sub}</span>
              </span>
              <span className="ov-upcoming-amt">
                {e.amount != null && <Money as="span">{fBase(e.amount)}</Money>}{' '}
                {days === 0 ? 'today' : `in ${days}d`}
              </span>
            </div>
          )
        })}
        {upcoming.length > cap && (
          <div className="ov-more">+{upcoming.length - cap} more in the next 30 days</div>
        )}
      </div>
    </WidgetShell>
  )
}

// ── Today's movers ───────────────────────────────────────────────────────────

// Routed through WidgetShell (item 8) so a rename actually applies, and
// The row cap (`rowCap`) is honored via TopDailyMovers' `limit` prop —
// the shared component owns the "+N more positions moved today" truncation
// notice (item 1), since it's the one place that knows the true combined
// gainers+losers count.
const Movers: ComponentType<WidgetContentProps> = ({ instance, def, onOpenAsset }) => {
  const { analytics: portfolio, loading } = useAnalytics()
  // Already collapsed per instrument, ordered and scaled by the server — positions of one
  // instrument are summed there, not here.
  const { byKey } = useValuedRows()
  const movers = portfolio?.movers
  // Each server entry carries the instrument's TOTAL move, % and value, keyed to its largest
  // position; that row only supplies the display fields (label, ticker, price), so a row's own
  // single-position figures never show beside the group's total.
  const moverRows = (movers?.rows ?? []).flatMap((m) => {
    const row = byKey.get(m.key)
    return row ? [{ ...row, _daily_pl: m.day_pl, _daily_plpct: m.day_pct, _base: m.value }] : []
  })
  const hasMovers = moverRows.length > 0
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={3} />
      </WidgetShell>
    )
  }
  return (
    <WidgetShell title={instance.title} base={def.title}>
      {hasMovers ? (
        <TopDailyMovers
          rows={moverRows}
          preAggregated
          maxAbs={movers?.max_abs}
          onOpenAsset={
            onOpenAsset ? (row) => onOpenAsset(row.label || row.symbol || '') : undefined
          }
          limit={rowCap(instance, def)}
          hideHeader
          variant="row"
        />
      ) : (
        <WidgetPlaceholder detail={<>No positions moved today.</>} />
      )}
    </WidgetShell>
  )
}

export const overviewCashflowCoreWidgetComponents: Record<
  string,
  ComponentType<WidgetContentProps>
> = {
  attention: Attention,
  briefing: Briefing,
  'lux-ask': LuxAsk,
  summary: Summary,
  upcoming: Upcoming,
  movers: Movers,
}
