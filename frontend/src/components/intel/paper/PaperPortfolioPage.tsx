import { useState } from 'react'
import { ActionMenu, Button, ConfirmDialog, ErrorState, PageTabs, TabSkeleton, Tag } from '../../ui'
import { AutopilotTag } from './AutopilotTag'
import { Spark } from '../../../lucius/identity'
import { fCurrency, fDate } from '../../../utils/format'
import { SortTable, type Column } from '../following/SortTable'
import { NameCell } from '../following/NameCell'
import { SectorPie } from '../following/SectorPie'
import { COPY } from '../following/following.copy'
import { Crumb } from '../following/Crumb'
import { signedPct, toneOf } from '../following/following.format'
import { Toast } from '../../tabs/accounts/Toast'
import { useAssetNav } from '../../../state/assetNav'
import {
  cancelPaperOrder,
  deletePaperPortfolio,
  startOverPaperPortfolio,
  type OrderSide,
  type PaperAsset,
  type PaperHolding,
  type PaperOrder,
} from '../../../api/paperTrading'
import { EditPortfolioDialog } from './EditPortfolioDialog'
import { MandateTab } from './MandateTab'
import { AutopilotTab } from './AutopilotTab'
import { OrderTicket } from './OrderTicket'
import { PaperChart } from './PaperChart'
import { PaperSource } from './PaperSource'
import { usePaperPage } from './usePaper'

type Tab = 'holdings' | 'orders' | 'activity' | 'mandate' | 'autopilot'

const assetOfHolding = (h: PaperHolding): PaperAsset => ({
  asset_key: h.asset_key,
  kind: h.kind,
  kind_label: h.kind_label,
  symbol: h.symbol ?? h.name,
  name: h.name,
  currency: h.currency,
  unit: h.unit,
  exchange: null,
})

export function PaperPortfolioPage({
  id,
  onBack,
  onOpen,
  onAskLucius,
}: {
  id: string
  onBack: () => void
  onOpen: (id: string) => void
  onAskLucius?: (text: string) => void
}) {
  const [tab, setTab] = useState<Tab>('holdings')
  const [ticket, setTicket] = useState<{ asset?: PaperAsset; side?: OrderSide } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<'over' | 'delete' | null>(null)
  const [editing, setEditing] = useState(false)
  // A sector picked on the ring lists only its holdings, as on Copy Trading.
  const [sector, setSector] = useState<string | null>(null)
  const { openAsset } = useAssetNav()
  const [pollOpen, setPollOpen] = useState(false)
  const page = usePaperPage(id, pollOpen)
  const data = page.data
  // Poll while an order waits, or while a review runs or is about to: its memo and trades
  // then appear on their own.
  const hasOpen = (data?.figures.open_orders ?? 0) > 0 || !!data?.autopilot?.review_soon
  if (hasOpen !== pollOpen) setPollOpen(hasOpen)

  if (page.initialLoading)
    return <TabSkeleton variant="holdings" label="Loading paper portfolio" header={false} />
  if (!data)
    return (
      <div className="fol-page">
        <Crumb name="Paper portfolio" onBack={onBack} root="Paper Trading" />
        <ErrorState
          message="This paper portfolio didn't load."
          onRetry={() => void page.refresh()}
        />
      </div>
    )

  const p = data.portfolio
  const f = data.figures
  const ccy = p.home_currency
  const money = (n: number | null) => (n == null ? '—' : fCurrency(n, ccy))
  const archived = p.status !== 'active'
  const shownHoldings = sector ? data.holdings.filter((h) => h.sector === sector) : data.holdings
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    try {
      await fn()
      if (done) setNotice(done)
    } catch (e) {
      setNotice((e as Error).message)
    }
  }
  const menu = [
    { label: 'Edit', onSelect: () => setEditing(true) },
    ...(archived ? [] : [{ label: 'Start over', onSelect: () => setConfirm('over') }]),
    { label: 'Delete', tone: 'danger' as const, onSelect: () => setConfirm('delete') },
  ]
  const goLive = () =>
    onAskLucius?.(
      `Help me do my paper portfolio "${p.name}" for real. Compare it with what I really hold, ` +
        'then draft the trades for my broker. Ask me before placing anything.',
    )

  return (
    <div className="fol-page">
      <Crumb name={p.name} onBack={onBack} root="Paper Trading" />
      <Toast message={notice} onDone={() => setNotice(null)} />
      <div className="fol-tabs-sticky">
        <header className="fol-head">
          <div className="fol-id">
            <h2 className="fol-title paper-title">
              <span className="fol-title-name">{p.name}</span>
              <Tag>Paper</Tag>
              {data.autopilot?.mode === 'auto' && <AutopilotTag />}
              <span className="fol-title-by">
                {p.source && (
                  <>
                    <PaperSource source={p.source} />
                    {' · '}
                  </>
                )}
                Day {p.day_count}
                {p.run_no > 1 ? ` · Run ${p.run_no}` : ''}
                {archived ? ' · Archived' : ''}
              </span>
            </h2>
          </div>
          <div className="fol-icons">
            <ActionMenu label={`More actions for ${p.name}`} title={p.name} items={menu} />
          </div>
          {!archived && (
            <div className="fol-cta paper-actions">
              {/* Lucius takes it to real money through your broker, so it is the app's Lucius
                  action: the primary button in Lucius's colour with its Spark. */}
              {onAskLucius && (
                <Button
                  size="sm"
                  variant="primary"
                  className="fi-lucius"
                  iconLeft={<Spark size={15} ariaHidden />}
                  onClick={goLive}
                >
                  Go live
                </Button>
              )}
              <Button size="sm" variant="primary" onClick={() => setTicket({})}>
                Paper trade
              </Button>
            </div>
          )}
        </header>
        <PageTabs
          ariaLabel={`${p.name} sections`}
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'holdings', label: 'Holdings' },
            { key: 'orders', label: 'Orders', badge: f.open_orders || undefined },
            { key: 'activity', label: 'Activity' },
            { key: 'mandate', label: 'Mandate', badge: data.mandate?.broken || undefined },
            { key: 'autopilot', label: 'Autopilot' },
          ]}
        />
      </div>
      <div className="fol-tabpanel">
        {tab === 'holdings' && (
          // Three sections spaced as Copy Trading's are: figures and chart, By sector, the table.
          <div className="paper-sections">
            <div className="fol-sec">
              <div className="fol-keyfigs">
                <div className="fol-ret3 paper-figs">
                  {/* The returns are Performance's, under its chart: each figure is said once. */}
                  <Figure label="Paper value" value={money(f.value)} />
                  <Figure label="Cash left" value={money(f.cash_home)} />
                </div>
              </div>
              <PaperChart id={id} name={p.name} benchmark={p.benchmark} orders={data.orders} />
            </div>
            {(data.sectors?.length ?? 0) > 1 && (
              <div className="fol-panel">
                <h2 className="fol-sec-title">By sector</h2>
                <SectorPie
                  tiles={(data.sectors ?? []).map((s) => ({
                    key: s.sector,
                    label: s.sector,
                    weight: s.weight,
                    change: null,
                  }))}
                  picked={sector}
                  onPick={setSector}
                  bySector
                  label="Holdings by sector. Tap a sector to list only its holdings."
                />
              </div>
            )}
            <div className="fol-sec">
              {data.holdings.length > 0 && (
                // Copy Trading's title; a picked sector names itself instead.
                <h2 className="fol-sec-title">
                  {sector
                    ? `${sector} (${shownHoldings.length})`
                    : COPY.allHoldingsTitle(data.holdings.length)}
                </h2>
              )}
              {data.holdings.length > 0 ? (
                <SortTable
                  label={`${p.name} holdings`}
                  rows={shownHoldings}
                  rowKey={(h) => h.asset_key}
                  initialSort="value"
                  // A row opens its asset's own page, as on Holdings. A currency or a contract
                  // has no page of its own.
                  onRowClick={(h) => {
                    const instrumentId = h.asset_key.startsWith('inst:') ? h.asset_key.slice(5) : ''
                    if (instrumentId)
                      openAsset({
                        instrumentId,
                        // A stock's ticker opens its quote; anything else opens by its id.
                        ticker: h.kind === 'listed' ? (h.symbol ?? undefined) : undefined,
                        label: h.name,
                      })
                  }}
                  columns={holdingColumns(
                    (h, side) => setTicket({ asset: assetOfHolding(h), side }),
                    !archived,
                    money,
                  )}
                />
              ) : (
                <p className="fol-by">No holdings yet</p>
              )}
            </div>
          </div>
        )}
        {tab === 'orders' && (
          <div className="fol-sec">
            {data.orders.length > 0 ? (
              <SortTable
                label="Paper orders"
                rows={data.orders}
                rowKey={(o) => o.id}
                columns={orderColumns(
                  (oid) => void run(() => cancelPaperOrder(oid), 'Paper order cancelled'),
                )}
              />
            ) : (
              <p className="fol-by">No paper orders yet</p>
            )}
          </div>
        )}
        {tab === 'activity' && (
          <div className="fol-sec paper-feed">
            {data.activity.map((a) => (
              <div key={a.id} className="paper-feed-item">
                <div>
                  <div>{a.label}</div>
                  {a.note && <div className="note">Why: {a.note}</div>}
                </div>
                <div className="when">{fDate(a.at, 'short', 'auto')}</div>
              </div>
            ))}
          </div>
        )}
        {tab === 'mandate' && (
          <MandateTab
            status={data.mandate}
            onEdit={archived ? undefined : () => setEditing(true)}
          />
        )}
        {tab === 'autopilot' && data.autopilot && (
          <AutopilotTab
            // Remount after a save, so its fields start from what is stored.
            key={`${data.autopilot.mode}-${data.autopilot.cadence}-${data.autopilot.instructions}`}
            autopilot={data.autopilot}
            mandate={p.mandate}
            archived={archived}
            portfolioId={id}
            onEditMandate={() => setEditing(true)}
            onNotice={setNotice}
          />
        )}
      </div>
      {!archived && ticket && (
        // Rendered only while open, so each opening mounts a fresh form.
        <OrderTicket
          portfolioId={id}
          portfolioName={p.name}
          homeCurrency={ccy}
          open
          initial={ticket}
          onClose={() => setTicket(null)}
          onPlaced={(toast) => {
            setTicket(null)
            setNotice(toast)
          }}
        />
      )}
      {editing && (
        <EditPortfolioDialog
          id={id}
          name={p.name}
          mandate={p.mandate}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            setNotice('Saved')
          }}
        />
      )}
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
          if (which === 'over')
            void startOverPaperPortfolio(id).then(
              (out) => onOpen(out.portfolio.id),
              (e: Error) => setNotice(e.message),
            )
          if (which === 'delete')
            void deletePaperPortfolio(id).then(onBack, (e: Error) => setNotice(e.message))
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
  trade: (h: PaperHolding, side: OrderSide) => void,
  canTrade: boolean,
  money: (n: number | null) => string,
): Column<PaperHolding>[] {
  const cols: Column<PaperHolding>[] = [
    {
      key: 'asset',
      label: 'Asset',
      left: true,
      sortValue: (h) => h.name,
      cell: (h) => <NameCell symbol={h.symbol} name={h.name} held={false} />,
    },
    {
      key: 'qty',
      label: 'Quantity',
      sortValue: (h) => h.quantity,
      cell: (h) => `${h.quantity.toLocaleString()}${h.unit ? ` ${h.unit}` : ''}`,
      wide: true,
    },
    {
      key: 'price',
      label: 'Price',
      sortValue: (h) => h.price,
      cell: (h) => (
        <span>
          {h.price != null ? h.price.toLocaleString() : '—'}
          {h.basis_label && <span className="fol-by"> {h.basis_label}</span>}
        </span>
      ),
      wide: true,
    },
    {
      key: 'value',
      label: 'Value',
      sortValue: (h) => h.value_home,
      cell: (h) => money(h.value_home),
      primary: true,
    },
    {
      key: 'ret',
      label: 'Return',
      sortValue: (h) => h.pnl_pct,
      cell: (h) => <span className={toneOf(h.pnl_pct)}>{signedPct(h.pnl_pct)}</span>,
      secondary: true,
    },
    {
      key: 'weight',
      label: 'Weight',
      sortValue: (h) => h.weight,
      cell: (h) => (h.weight != null ? `${(h.weight * 100).toFixed(1)}%` : '—'),
      wide: true,
    },
  ]
  if (canTrade)
    cols.push({
      key: 'act',
      label: '',
      action: true,
      cell: (h) => (
        // The row opens the asset's page: Trade is its own click.
        <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <Button
            size="dense"
            variant="secondary"
            disabled={!!h.frozen}
            onClick={() => trade(h, 'sell')}
          >
            Trade
          </Button>
        </span>
      ),
    })
  return cols
}

function orderStatus(o: PaperOrder): string {
  if (o.status === 'filled' && o.fill) return `Filled ${o.fill.price.toFixed(2)}`
  if (o.status !== 'open') return o.status.charAt(0).toUpperCase() + o.status.slice(1)
  if (o.order_type === 'limit' && o.limit_price != null)
    return `Waits for ${o.limit_price.toFixed(2)}`
  if (o.order_type === 'stop' && o.stop_price != null) return `Waits for ${o.stop_price.toFixed(2)}`
  if (o.order_type === 'nav') return "Fills at today's NAV"
  return 'Waits for the open'
}

function orderColumns(cancel: (id: string) => void): Column<PaperOrder>[] {
  return [
    {
      key: 'what',
      label: 'Order',
      left: true,
      cell: (o) => (
        <span>
          <b>
            {o.side === 'buy' ? 'Buy' : 'Sell'} {o.quantity ?? ''} {o.symbol ?? o.name}
          </b>{' '}
          <span className="fol-by">
            {o.origin === 'owner' ? 'You' : o.origin === 'autopilot' ? 'Autopilot' : 'Lucius'}
          </span>
        </span>
      ),
    },
    {
      key: 'type',
      label: 'Type',
      cell: (o) =>
        o.order_type === 'limit'
          ? `Limit ${o.limit_price?.toFixed(2)}`
          : o.order_type === 'stop'
            ? `Stop ${o.stop_price?.toFixed(2)}`
            : o.order_type === 'nav'
              ? 'At NAV'
              : 'Market',
      wide: true,
    },
    {
      key: 'status',
      label: 'Status',
      primary: true,
      // A short status; the server's full sentence (why it waits, until when) is its tooltip.
      cell: (o) => <span title={o.state_reason ?? undefined}>{orderStatus(o)}</span>,
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
