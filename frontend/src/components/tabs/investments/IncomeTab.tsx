import './income.css'
import { useDataVersion } from '../../../hooks/useDataVersion'
import {
  Badge,
  Button,
  Cluster,
  EmptyState,
  ErrorState,
  Money,
  SummaryBar,
  TabSkeleton,
} from '../../ui'
import { CalendarCheck, CalendarClock, DollarSign } from '../../../ui/icons'
import { fBase, fCurrency, fDate, fPct } from '../../../utils/format'
import {
  useIncomeView,
  type IncomeHistoryRow,
  type IncomePaymentRow,
  type IncomeSourceRow,
} from './useIncomeView'
import { EntityRow } from '../../../lucius/EntityRow'
import { LuciusCta } from '../../intro/LuciusCta'

// Source legend items — verbatim from prototype's INC_SRC.
const INC_SRC = {
  declared: {
    label: 'Declared rate',
    dot: 'var(--primary)',
    desc: 'The official rate the company has announced.',
  },
  fixedIncome: {
    label: 'Fixed income rate',
    dot: 'var(--warn)',
    desc: "The bond's coupon or T-bill's accretion — contractual, not estimated.",
  },
  trailing: {
    label: 'Trailing 12M',
    dot: 'var(--muted)',
    desc: 'No rate announced — estimated from the last 12 months paid.',
  },
} as const

interface Props {
  /** Bumped after a dividend is recorded so this tab refetches income data. */
  version?: number
  onAskLucius?: (text: string) => void
}

const INCOME_MESSAGE =
  'Help me record my dividends and interest. Ask which holdings pay me; I can send a ' +
  'screenshot or statement of my payments instead of typing.'

// ── Source list rows ──────────────────────────────────────────────────────────

function SourceRow({ row }: { row: IncomeSourceRow }) {
  const dot = INC_SRC[row.source]?.dot ?? 'var(--fg-3)'
  return (
    <div className="inc-src-row">
      <div className="inc-src-id">
        <span className="inc-src-sym">{row.sym}</span>
        <span className="inc-src-nm">{row.name}</span>
      </div>
      <div className="inc-src-bar">
        <span className="inc-dot" style={{ background: dot }} title={INC_SRC[row.source]?.label} />
        <span className="inc-src-track">
          <span className="inc-src-fill" style={{ width: `${row.barPct}%`, background: dot }} />
        </span>
      </div>
      <span className="inc-src-amt money">{fBase(row.annualBase)}</span>
    </div>
  )
}

// ── Payment table row ─────────────────────────────────────────────────────────

function PaymentRow({ row }: { row: IncomePaymentRow }) {
  return (
    <div className="inc-pay-row">
      <span>
        <span className="inc-pay-sym">{row.sym}</span>{' '}
        <span className="inc-pay-nm">{row.name}</span>
      </span>
      <span className="inc-pay-date">
        {row.exDate}
        {row.exDaysUntil >= 0 && row.exDaysUntil <= 10 && (
          <span className="inc-soon">in {row.exDaysUntil}d</span>
        )}
      </span>
      <span className="inc-pay-date">{row.payDate}</span>
      <span className="inc-pay-amt">
        <span
          className="inc-pay-status"
          title={
            row.status === 'scheduled'
              ? 'Confirmed — date announced'
              : 'Estimated — predicted from past payments'
          }
        >
          {row.status === 'scheduled' ? (
            <CalendarCheck size={14} strokeWidth={2} />
          ) : (
            <CalendarClock size={14} strokeWidth={2} />
          )}
        </span>
        <span className="money">{fBase(row.amountBase)}</span>
      </span>
    </div>
  )
}

// ── Persisted income history ─────────────────────────────────────────────────

function RecentIncomePanel({
  rows,
  total,
  expanded,
  onToggle,
}: {
  rows: IncomeHistoryRow[]
  total: number
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div className="mk-panel">
      <div className="mk-panel-head">
        <span className="mk-panel-title">Recent income</span>
        <span className="mk-panel-spacer" />
        <span className="mk-panel-sub">
          <span className="money">{total}</span> received
        </span>
      </div>
      <div className="mk-panel-body">
        {total === 0 ? (
          <EmptyState
            title="No income recorded yet"
            body="Record a dividend, interest payment, distribution, or DRP to see it here."
          />
        ) : (
          <>
            <div className="inv-event-table inv-event-table--income">
              <div className="inv-event-head" aria-hidden="true">
                <span>Date received</span>
                <span>Holding</span>
                <span>Kind</span>
                <span>Net</span>
                <span>Method</span>
              </div>
              {/* EntityRow renders the host <div> itself and stamps data-entity-id + draggable
                  + data-ctx-*, making an income event @-mentionable, right-clickable
                  ("Ask Lucius about this") and draggable into the dock. `income` is a
                  registered mention type (descriptors/equity_lots.py, order=19). */}
              {rows.map((row) => (
                <EntityRow
                  as="div"
                  className="inv-event-row"
                  key={row.eventId}
                  type="income"
                  id={row.eventId}
                  label={row.symbol}
                  item={row}
                  toFields={(it) => ({
                    symbol: it.symbol,
                    income_kind: it.kindLabel,
                    currency: it.amountCurrency,
                    paid_at: it.date,
                  })}
                  search={`${row.symbol} ${row.name ?? ''} ${row.kindLabel}`}
                  sublabel={`${row.kindLabel} • ${fDate(row.date, 'short', 'always')}`}
                >
                  <time className="inv-event-date" dateTime={row.date}>
                    {fDate(row.date, 'short', 'always')}
                  </time>
                  <span className="inv-event-holding" title={row.name || row.symbol}>
                    <strong>{row.symbol}</strong>
                    {row.name && <span className="inv-event-name">{row.name}</span>}
                    <span className="inv-event-mobile-meta"> • {row.kindLabel}</span>
                  </span>
                  <span className="inv-event-kind">{row.kindLabel}</span>
                  <span
                    className="inv-event-amount"
                    title={
                      row.amountBase !== null
                        ? undefined
                        : 'Base-currency FX unavailable; showing the native net amount'
                    }
                  >
                    <Money>
                      {row.amountBase !== null
                        ? fBase(row.amountBase)
                        : fCurrency(row.amountNative, row.amountCurrency)}
                    </Money>
                    {row.amountBase === null && (
                      <span className="inv-event-detail">native • FX unavailable</span>
                    )}
                  </span>
                  <span className="inv-event-method">
                    <Badge tone={row.deliveryLabel === 'Reinvested' ? 'neutral' : 'muted'}>
                      {row.deliveryLabel}
                    </Badge>
                  </span>
                </EntityRow>
              ))}
            </div>
            {total > 8 && (
              <Button variant="ghost" size="sm" className="inv-event-toggle" onClick={onToggle}>
                {expanded ? 'Show top 8 only' : `Show all ${total} income events`}
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function IncomeTab({ version = 0, onAskLucius }: Props) {
  // Local dividend saves bump `version`; any Lucius write bumps `anyWriteV`.
  const anyWriteV = useDataVersion('*')
  const view = useIncomeView(version + anyWriteV)

  if (view.loading) {
    return (
      <div className="mk-subt">
        <TabSkeleton variant="income" label="Loading income…" header={false} />
      </div>
    )
  }

  if (view.error) {
    return (
      <div className="mk-subt">
        <ErrorState message="Couldn't load income data." onRetry={view.retry} />
      </div>
    )
  }

  if (!view.hasAny) {
    return (
      <div className="mk-subt">
        <EmptyState
          fill
          icon={<DollarSign size={28} strokeWidth={1.5} aria-hidden="true" />}
          title="No income yet"
          body="Appears once you hold dividend stocks, ETFs, or bonds."
          action={
            <Cluster gap="--space-3" justify="center">
              <LuciusCta
                label="Add with Lucius"
                message={INCOME_MESSAGE}
                onAskLucius={onAskLucius}
              />
            </Cluster>
          }
        />
      </div>
    )
  }

  const shownSrc = view.showAllSrc ? view.showAllSrc_allRows : view.sourceRows
  const shownPays = view.showAllPay ? view.allPaymentRows : view.paymentRows
  const shownHistory = view.showAllHistory ? view.allHistoryRows : view.historyRows

  return (
    <div className="mk-subt">
      {view.cards.length > 0 && <SummaryBar cards={view.cards} />}

      <div className="inc-grid">
        {/* ── Left: "Where your income comes from" ── */}
        <div className="mk-panel">
          <div className="mk-panel-head">
            <span className="inc-titlerow">
              <span className="mk-panel-title">Where your income comes from</span>
              <span className="inc-info" tabIndex={0} role="button" aria-label="What is income?">
                i
                <span className="inc-info-pop">
                  Dividends, interest and fund distributions — what you've received, plus a 12-month
                  forward estimate from your current holdings.
                </span>
              </span>
            </span>
            <span className="mk-panel-spacer" />
            <span className="mk-panel-sub">
              {view.totalSourceCount} holdings • forecast next 12m
            </span>
          </div>
          <div className="mk-panel-body">
            <div className="inc-src">
              {shownSrc.map((row) => (
                <SourceRow key={row.positionId} row={row} />
              ))}
              {view.restSrcCount > 0 && !view.showAllSrc && (
                <button
                  type="button"
                  className="inc-src-row inc-src-row--agg"
                  onClick={() => view.setShowAllSrc(true)}
                  title="Show all holdings"
                >
                  <div className="inc-src-id">
                    <span className="inc-src-sym">+{view.restSrcCount} more holdings</span>
                    <span className="inc-src-nm">smaller contributors • tap to expand</span>
                  </div>
                  <div className="inc-src-bar">
                    <span className="inc-dot" style={{ background: 'var(--fg-3)' }} />
                    <span className="inc-src-track">
                      <span
                        className="inc-src-fill"
                        style={{
                          width: `${view.restSrcBarPct}%`,
                          background: 'var(--fg-3)',
                          opacity: 0.55,
                        }}
                      />
                    </span>
                  </div>
                  <span className="inc-src-amt money">{fBase(view.restSrcSum)}</span>
                </button>
              )}
            </div>
            {view.restSrcCount > 0 && (
              <button className="inc-showall" onClick={() => view.setShowAllSrc((v) => !v)}>
                {view.showAllSrc ? 'Show top 8 only' : `Show all ${view.totalSourceCount} holdings`}
              </button>
            )}
            <p className="inc-cap">
              Ranked by forecast contribution, excluding crypto and non-paying positions. Dot colour
              = rate source — see "How we estimate".
            </p>
          </div>
        </div>

        {/* ── Right: "Is your income growing?" ── */}
        <div className="mk-panel">
          <div className="mk-panel-head">
            <span className="mk-panel-title">Is your income growing?</span>
          </div>
          <div className="mk-panel-body">
            {view.growth ? (
              <>
                <div className="inc-cmp">
                  <div className="inc-cmp-row">
                    <div className="inc-cmp-top">
                      <span className="inc-cmp-k">Received • last 12m</span>
                      <span className="inc-cmp-v money">{fBase(view.growth.received)}</span>
                    </div>
                    <div className="inc-bar">
                      <span
                        className="inc-bar-fill"
                        style={{
                          width: `${view.growth.receivedBarPct}%`,
                          background: 'var(--good)',
                        }}
                      />
                    </div>
                  </div>
                  <div className="inc-cmp-row">
                    <div className="inc-cmp-top">
                      <span className="inc-cmp-k">Forecast • next 12m</span>
                      <span className="inc-cmp-v">
                        <span className="money">{fBase(view.growth.projected)}</span>
                        {view.growth.growthPct != null && (
                          <span className="money">
                            <Badge tone={view.growth.growthPct >= 0 ? 'pos' : 'neg'}>
                              {fPct(view.growth.growthPct, { sign: true })}
                            </Badge>
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="inc-bar">
                      <span
                        className="inc-bar-fill"
                        style={{
                          width: `${view.growth.projectedBarPct}%`,
                          background: 'var(--good)',
                          opacity: 0.5,
                        }}
                      />
                    </div>
                  </div>
                </div>
                <p className="inc-chart-cap">
                  Next 12 months vs. last 12 months, assuming today's holdings and rates hold
                  steady.
                </p>
              </>
            ) : (
              <p className="inc-chart-cap">
                {view.incomeBaseTotalsAvailable === false
                  ? 'Growth is unavailable until the missing exchange rate can convert every received payment.'
                  : 'Add holdings or record income to see year-over-year growth.'}
              </p>
            )}
            <div className="inc-legend">
              <span className="inc-legend-h">How we estimate</span>
              {(Object.keys(INC_SRC) as Array<keyof typeof INC_SRC>).map((k) => (
                <div className="inc-legend-item" key={k}>
                  <span className="inc-dot" style={{ background: INC_SRC[k].dot }} />
                  <div>
                    <div className="inc-legend-k">{INC_SRC[k].label}</div>
                    <div className="inc-legend-d">{INC_SRC[k].desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── Upcoming payments ── */}
      {view.hasCalendar && (
        <div className="mk-panel">
          <div className="mk-panel-head">
            <span className="mk-panel-title">Upcoming payments</span>
            <span className="mk-panel-spacer" />
            <span className="mk-panel-sub">
              next {view.calendarHorizonDays} days • {view.calendarCount} expected •{' '}
              <span className="money">{view.calendarTotalFmt}</span>
            </span>
          </div>
          <div className="mk-panel-body">
            {view.nextPayment && (
              <div className="inc-next">
                <div className="inc-next-l">
                  <span className="inc-next-tag">Next ex-dividend</span>
                  <span className="inc-next-main">
                    {view.nextPayment.sym} • {view.nextPayment.exRelDays}
                  </span>
                  <span className="inc-next-sub">
                    Own it through {view.nextPayment.exLabel} to receive this payout
                  </span>
                </div>
                <div className="inc-next-r">
                  <span className="inc-next-amt money">{fBase(view.nextPayment.amountFmt)}</span>
                  <span className="inc-next-pay">pays {view.nextPayment.payLabel}</span>
                </div>
              </div>
            )}
            <div className="inc-pay">
              <div className="inc-pay-head">
                <span>Holding</span>
                <span>Ex-dividend</span>
                <span>Pay date</span>
                <span style={{ textAlign: 'right' }}>Amount</span>
              </div>
              {shownPays.map((p) => (
                <PaymentRow key={p.positionId} row={p} />
              ))}
            </div>
            {view.totalPaymentCount > 8 && (
              <button className="inc-showall" onClick={() => view.setShowAllPay((v) => !v)}>
                {view.showAllPay ? 'Show fewer' : `Show all ${view.totalPaymentCount} payments`}
              </button>
            )}
            <p className="inc-chart-cap">
              Own the holding before its ex-dividend date to receive the payout.
              <br />
              Confirmed = date announced by the company.
              <br />
              Estimated = predicted from past payment timing.
            </p>
          </div>
        </div>
      )}

      <RecentIncomePanel
        rows={shownHistory}
        total={view.totalHistoryCount}
        expanded={view.showAllHistory}
        onToggle={() => view.setShowAllHistory((value) => !value)}
      />
    </div>
  )
}
