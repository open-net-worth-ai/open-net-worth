// The FIRE projection card as a panel. The server projects from the book's own net worth (the
// old card sent back a rounded copy of the figure it had been given, and skipped the request
// when it was not positive) and rounds what the card prints.

import type { ReactNode } from 'react'
import { fBaseCompact, fCompact } from '../../../../utils/format'
import { Money, Skeleton, Text } from '../../../ui'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { OvChart, TICK, GRID, amountsHidden, areaFill, kTick, resolveColor } from '../charts'
import type { FireResult } from '../../../../hooks/useFire'
import type { PanelDefinition, PanelViewProps } from './panel'

export type FirePanel =
  | { state: 'no_net_worth' | 'no_spending' }
  | (FireResult & {
      state: 'ok'
      fi_age_rounded: number | null
      years_to_fire_rounded: number | null
    })

function HeadStat({ value, label, color }: { value: ReactNode; label: string; color?: string }) {
  return (
    <div>
      <div className="ov-ins-big" style={color ? { color } : undefined}>
        {value}
      </div>
      <span className="ov-t-meta">{label}</span>
    </div>
  )
}

function chartConfig(data: FireResult) {
  const ACC = resolveColor('var(--accent)')
  const WARN = resolveColor('var(--warn)')
  return {
    type: 'line' as const,
    data: {
      labels: data.trajectory.map((p) => String(p.age ?? p.year)),
      datasets: [
        {
          label: 'Net worth',
          data: data.trajectory.map((p) => p.nw),
          borderColor: ACC,
          backgroundColor: areaFill(ACC),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: true,
        },
        {
          label: 'FIRE number',
          data: data.trajectory.map((p) => p.target),
          borderColor: WARN,
          borderWidth: 1.2,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items: Array<{ dataIndex: number }>) => {
              const age = data.trajectory[items[0]?.dataIndex ?? 0]?.age
              return age != null ? `Age ${age}` : ''
            },
            label: (ctx: { dataset: { label?: string }; raw: unknown }) =>
              `${ctx.dataset.label}: ${amountsHidden() ? '***' : fBaseCompact(Number(ctx.raw))}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: TICK(), maxTicksLimit: 6 }, grid: { display: false } },
        y: { ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() } },
      },
    },
  }
}

function FireView({ instance, def, data, loading, error }: PanelViewProps<FirePanel>) {
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-ins-body">
        <Skeleton height={140} />
      </WidgetShell>
    )
  }
  if (error || !data || data.state !== 'ok') {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder
          detail={
            data?.state === 'no_net_worth'
              ? 'A projection needs a net worth above zero.'
              : 'Set your monthly retirement spending in Plan to see a projection.'
          }
        />
      </WidgetShell>
    )
  }
  const years = data.years_to_fire_rounded
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      bodyClass="ov-ins-body"
      meta={data.fi_age_rounded != null ? `FI ~${data.fi_age_rounded}` : 'FI —'}
    >
      <div className="ov-ins-headrow">
        <HeadStat
          value={<Money as="span">{fBaseCompact(data.fire_number)}</Money>}
          label="FIRE number"
        />
        <HeadStat
          value={years === 0 ? '0 yrs' : years != null ? `${fCompact(years, 1)} yrs` : '50+ yrs'}
          label="to independence"
          color="var(--good)"
        />
      </div>
      {data.current_age != null ? (
        <OvChart
          config={chartConfig(data)}
          sig={`${data.trajectory.length}|${data.fire_number}`}
          minHeight={110}
        />
      ) : (
        <Text as="p" size="sm" tone="faint">
          Add your date of birth in Profile to see ages
        </Text>
      )}
    </WidgetShell>
  )
}

export const firePanel: PanelDefinition<FirePanel> = {
  query: () => ({ source: 'panel_fire', params: {} }),
  signals: ['portfolio', 'ledger', 'accounts', 'assets', 'profile'],
  View: FireView,
}
