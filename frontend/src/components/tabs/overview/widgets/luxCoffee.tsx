// Overview v2 — the "Coffee · weekly spend" widget (`lux-coffee`). The
// prototype's `LuciusCoffee` (overview_v2b/widgets2.jsx) is a worked example
// of a Lucius-BUILT custom widget, but its six weekly figures are a fixed
// demo array — CLAUDE.md forbids fabricating numbers, so this ports the
// SHAPE (weekly bars + an "avg $X/wk" meta figure) over a REAL query
// instead: expense transactions from the last 6 weeks whose description/
// payee/memo/category mentions a coffee word (SEARCH_TERM below, via
// GET /api/transactions/spend-by-week — services/spend_search.py over
// ledger's cross-store payee/category join, no client-side fabrication),
// bucketed into Monday-start weeks and summed in the base currency. A book
// with no matching purchases renders an honest empty state (WidgetPlaceholder),
// never a fake bar chart.
//
// Deliberately its OWN file rather than an addition to widgets/content.tsx —
// this widget needs a data fetch + weekly bucketing content.tsx's other
// widgets (text/image/embed/lux-html/lux-chart) don't, and a separate lane is
// fixing lux-html/lux-chart's picker reachability in that same file — this
// keeps the two diffs from ever touching the same lines.

import type { ComponentType } from 'react'
import { fBase, fDate } from '../../../../utils/format'
import { Money } from '../../../ui'
import { Spark } from '../../../../lucius/identity'
import { useSpendByWeek } from '../../../../hooks/serverState'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'

// Any of these words in a transaction's description, payee, memo or category. "coffee" alone
// missed most coffee shops: an "… Espresso" bar, a "Kopi …" stall, a café.
const SEARCH_TERM = 'coffee|espresso|cafe|café|kopi|latte|cappuccino'
const WEEK_COUNT = 6

function weekLabel(iso: string): string {
  return `w/c ${fDate(iso)}`
}

export const LuxCoffee: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  // The weekly buckets, the total, the average and the bar-scaling maximum are all summed by
  // the server (`GET /api/overview/spend-search`). The widget only labels the weeks.
  const search = useSpendByWeek(SEARCH_TERM, WEEK_COUNT)
  const loading = search.initialLoading
  const error = search.error && search.data == null ? "Couldn't load coffee spend." : ''
  const spark = (
    <span className="ov-lucius-spark">
      <Spark size={15} />
    </span>
  )

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} lead={spark}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  const weeks = search.data?.weeks ?? []
  const total = search.data?.total ?? 0
  if (error || total <= 0) {
    return (
      <WidgetShell title={instance.title} base={def.title} lead={spark}>
        <WidgetPlaceholder
          detail={error || `No coffee purchases in the last ${WEEK_COUNT} weeks.`}
        />
      </WidgetShell>
    )
  }
  const avg = search.data?.average ?? 0
  const max = search.data?.max || 1
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      lead={spark}
      meta={
        <>
          avg <Money as="span">{fBase(avg)}</Money>/wk
        </>
      }
    >
      {weeks.map((w) => (
        <div className="ov-env-row" key={w.key}>
          <span className="ov-env-nm">{weekLabel(w.start)}</span>
          <span className="ov-env-bar">
            <span
              className="ov-env-fill"
              style={{ width: `${(w.amount / max) * 100}%`, background: 'var(--accent)' }}
            />
          </span>
          <span className="ov-env-amt">
            <Money as="span">{fBase(w.amount)}</Money>
          </span>
        </div>
      ))}
    </WidgetShell>
  )
}

export const luxCoffeeWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  'lux-coffee': LuxCoffee,
}
