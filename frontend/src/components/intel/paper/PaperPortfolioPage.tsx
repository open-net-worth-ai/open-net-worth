import { useState } from 'react'
import { ActionMenu, Button, ConfirmDialog, PageTabs, Tag } from '../../ui'
import { Spark } from '../../../lucius/identity'
import { fCurrency, fDate } from '../../../utils/format'
import { SortTable, type Column } from '../following/SortTable'
import { NameCell } from '../following/NameCell'
import { Crumb } from '../following/Crumb'
import { signedPct, toneOf } from '../following/following.format'
import { Toast } from '../../tabs/accounts/Toast'
import { OrderTicket } from './OrderTicket'
import {
  assetOf,
  cancelOrder,
  dayOf,
  deletePortfolio,
  holdingValue,
  openMarketNow,
  openOrders,
  portfolioValue,
  rebasedEnd,
  returnSinceStart,
  startOver,
  todayMove,
  type Holding,
  type Order,
  type Portfolio,
  type Side,
} from './paperStore'

type Tab = 'holdings' | 'orders' | 'activity'
const money = (n: number) => fCurrency(n, 'USD')
const label = (s: string) => (s === 'XAU-G' ? 'Gold' : s)

export function PaperPortfolioPage({
  p,
  onBack,
  onOpen,
}: {
  p: Portfolio
  onBack: () => void
  onOpen: (id: string) => void
}) {
  const [tab, setTab] = useState<Tab>('holdings')
  const [ticket, setTicket] = useState<{ symbol?: string; side?: Side } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<'over' | 'delete' | null>(null)
  const value = portfolioValue(p)
  const ret = returnSinceStart(p)
  const today = todayMove(p)
  const pending = openOrders(p)
  const menu = [
    { label: 'Start over', onSelect: () => setConfirm('over') },
    { label: 'Prototype: open the market now', onSelect: () => openMarketNow() },
    { label: 'Delete', tone: 'danger' as const, onSelect: () => setConfirm('delete') },
  ]
  return (
    <div className="fol-page">
      <Crumb name={p.name} onBack={onBack} root="Paper Trading" />
      <Toast message={notice} onDone={() => setNotice(null)} />
      <div className="fol-tabs-sticky">
        <header className="fol-head">
          <div className="fol-id">
            <h2 className="fol-title paper-title">
              <span className="fol-title-name">{p.name}</span>
              <Tag tone="paper">Paper</Tag>
              <span className="fol-title-by">
                {p.source} · Day {dayOf(p)}
                {p.archived ? ' · Archived' : ''}
              </span>
            </h2>
          </div>
          <div className="fol-icons">
            <ActionMenu label={`More actions for ${p.name}`} title={p.name} items={menu} />
          </div>
          <div className="fol-cta paper-actions">
            {/* Lucius does it for real (through your broker), so it is the app's Lucius action:
                the primary button in Lucius's colour with its Spark, as Copy Trading's Auto-copy. */}
            <Button
              size="sm"
              variant="primary"
              className="fi-lucius"
              iconLeft={<Spark size={15} ariaHidden />}
            >
              Go live
            </Button>
            {!p.archived && (
              <Button size="sm" variant="paper" onClick={() => setTicket({})}>
                Paper trade
              </Button>
            )}
          </div>
        </header>
        <PageTabs
          ariaLabel={`${p.name} sections`}
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'holdings', label: 'Holdings' },
            { key: 'orders', label: 'Orders', badge: pending.length || undefined },
            { key: 'activity', label: 'Activity' },
          ]}
        />
      </div>
      <div className="fol-tabpanel">
        {tab === 'holdings' && (
          <div className="fol-sec">
            <div className="fol-keyfigs">
              <div className="fol-ret3">
                <Figure label="Paper value" value={money(value)} />
                <Figure
                  label="Return"
                  value={signedPct(ret)}
                  tone={toneOf(ret)}
                  sub={`VT ${signedPct(rebasedEnd(p.bench, p))}`}
                />
                <Figure label="Today" value={signedPct(today)} tone={toneOf(today)} />
                <Figure label="Paper cash" value={money(p.cash)} />
              </div>
              <div className="fol-cautions">
                <span className="fol-by">Not in your net worth</span>
              </div>
            </div>
            <PaperChart p={p} />
            <SortTable
              label={`${p.name} holdings`}
              rows={p.holdings}
              rowKey={(h) => h.symbol}
              initialSort="value"
              columns={holdingColumns(
                (symbol, side) => setTicket({ symbol, side }),
                !p.archived,
                value,
              )}
            />
          </div>
        )}
        {tab === 'orders' && (
          <div className="fol-sec">
            <SortTable
              label="Paper orders"
              rows={p.orders.filter((o) => o.status !== 'cancelled')}
              rowKey={(o) => o.id}
              columns={orderColumns((id) => cancelOrder(p.id, id))}
            />
            {p.orders.length === 0 && <p className="fol-by">No paper orders yet</p>}
          </div>
        )}
        {tab === 'activity' && (
          <div className="fol-sec paper-feed">
            {p.activity.map((a) => (
              <div key={a.id} className="paper-feed-item">
                <div>
                  <div>{a.text}</div>
                  {a.note && (
                    <div className="note">
                      “{a.note}” {a.by === 'lucius' ? 'Lucius' : 'You'}
                    </div>
                  )}
                </div>
                <div className="when">{fDate(a.at, 'short', 'auto')}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      <OrderTicket
        p={p}
        open={ticket != null}
        initial={ticket ?? undefined}
        onClose={() => setTicket(null)}
        onPlaced={(m) => {
          setTicket(null)
          setNotice(m)
        }}
      />
      <ConfirmDialog
        data={
          confirm === 'over'
            ? {
                title: `Start ${p.name} over?`,
                body: 'This run is kept under Archived runs.',
                confirmLabel: 'Start over',
              }
            : confirm === 'delete'
              ? {
                  title: `Delete ${p.name}?`,
                  body: 'Its orders and history are removed.',
                  confirmLabel: 'Delete',
                  danger: true,
                }
              : null
        }
        onConfirm={() => {
          const which = confirm
          setConfirm(null)
          if (which === 'over') onOpen(startOver(p.id))
          if (which === 'delete') {
            deletePortfolio(p.id)
            onBack()
          }
        }}
        onCancel={() => setConfirm(null)}
      />
    </div>
  )
}

function Figure({
  label,
  value,
  tone,
  sub,
}: {
  label: string
  value: string
  tone?: string
  sub?: string
}) {
  return (
    <div className="fol-ret3-item">
      <span className="fol-ret3-l">{label}</span>
      <span className={`fol-ret3-v ${tone ?? ''}`}>{value}</span>
      {sub && <span className="fol-keyfig-chg">{sub}</span>}
    </div>
  )
}

function holdingColumns(
  trade: (s: string, side: Side) => void,
  canTrade: boolean,
  total: number,
): Column<Holding>[] {
  const cols: Column<Holding>[] = [
    {
      key: 'asset',
      label: 'Asset',
      left: true,
      sortValue: (h) => h.symbol,
      cell: (h) => <NameCell symbol={label(h.symbol)} name={assetOf(h.symbol).name} held={false} />,
    },
    {
      key: 'qty',
      label: 'Quantity',
      sortValue: (h) => h.qty,
      cell: (h) => `${h.qty}${assetOf(h.symbol).unit ?? ''}`,
      wide: true,
    },
    {
      key: 'price',
      label: 'Price',
      sortValue: (h) => assetOf(h.symbol).price,
      cell: (h) => assetOf(h.symbol).price.toLocaleString(),
      wide: true,
    },
    {
      key: 'value',
      label: 'Value',
      sortValue: holdingValue,
      cell: (h) => money(holdingValue(h)),
      primary: true,
    },
    {
      key: 'ret',
      label: 'Return',
      sortValue: (h) => assetOf(h.symbol).price / h.cost - 1,
      cell: (h) => {
        const r = assetOf(h.symbol).price / h.cost - 1
        return <span className={toneOf(r)}>{signedPct(r)}</span>
      },
      secondary: true,
    },
    {
      key: 'weight',
      label: 'Weight',
      sortValue: holdingValue,
      cell: (h) => signedPct(holdingValue(h) / total).replace('+', ''),
      wide: true,
    },
  ]
  if (canTrade)
    cols.push({
      key: 'act',
      label: '',
      action: true,
      cell: (h) => (
        <Button size="dense" variant="secondary" onClick={() => trade(h.symbol, 'sell')}>
          Trade
        </Button>
      ),
    })
  return cols
}

function orderColumns(cancel: (id: string) => void): Column<Order>[] {
  return [
    {
      key: 'what',
      label: 'Order',
      left: true,
      cell: (o) => (
        <span>
          <b>
            {o.side === 'buy' ? 'Buy' : 'Sell'} {o.qty} {label(o.symbol)}
          </b>{' '}
          <span className="fol-by">{o.by === 'lucius' ? 'Lucius' : 'You'}</span>
        </span>
      ),
    },
    {
      key: 'type',
      label: 'Type',
      cell: (o) =>
        o.type === 'market'
          ? 'Market'
          : `${o.type === 'limit' ? 'Limit' : 'Stop'} ${o.price?.toFixed(2)}`,
      wide: true,
    },
    {
      key: 'status',
      label: 'Status',
      primary: true,
      cell: (o) =>
        o.status === 'filled'
          ? `Filled ${o.fillPrice?.toFixed(2)}`
          : o.type === 'market'
            ? 'Waiting for the open'
            : `Waiting for ${o.price?.toFixed(2)}`,
    },
    {
      key: 'act',
      label: '',
      action: true,
      cell: (o) =>
        o.status === 'open' ? (
          <Button size="dense" variant="ghost" onClick={() => cancel(o.id)}>
            Cancel
          </Button>
        ) : null,
    },
  ]
}

/** The made-up history, scaled so its last day is today's value (prototype). */
function endAt(history: number[], now: number): number[] {
  const last = history[history.length - 1] ?? now
  const start = history[0] ?? now
  return history.map((v) => start + ((v - start) * (now - start)) / (last - start || 1))
}

/** Three lines since the start: this portfolio, your investments, VT. */
function PaperChart({ p }: { p: Portfolio }) {
  const series = [
    {
      name: 'This portfolio',
      data: endAt(p.history, portfolioValue(p)),
      color: 'var(--ink)',
      w: 2.2,
    },
    { name: 'Your investments', data: p.book, color: 'var(--primary)', w: 1.5 },
    { name: 'VT', data: p.bench, color: 'var(--faint)', w: 1.5 },
  ]
  const all = series.flatMap((s) => s.data)
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const W = 760
  const H = 180
  const pts = (d: number[]) =>
    d
      .map(
        (v, i) =>
          `${(i / Math.max(1, d.length - 1)) * W},${H - 8 - ((v - lo) / (hi - lo || 1)) * (H - 16)}`,
      )
      .join(' ')
  return (
    <div className="paper-chart">
      <div className="paper-legend">
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color }} />
            {s.name} {signedPct((s.data[s.data.length - 1] ?? p.startCash) / p.startCash - 1)}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Return since the start"
      >
        {[...series].reverse().map((s) => (
          <polyline
            key={s.name}
            points={pts(s.data)}
            fill="none"
            stroke={s.color}
            strokeWidth={s.w}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
    </div>
  )
}
