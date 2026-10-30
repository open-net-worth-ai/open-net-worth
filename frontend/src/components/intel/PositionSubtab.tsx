import { useEffect, useState } from 'react'
import { Panel } from '../ui'
import { authFetch } from '../../api/client'
import { LotsPanel } from '../shared/LotsPanel'
import type { WriteTarget } from '../shared/LotsPanel'
import {
  fAmountNative,
  fBase,
  fDate,
  fPct,
  fPrice,
  plClass,
  getCurrencySymbol,
  priceDigits,
} from '../../utils/format'
import type { AssetPosition } from '../../types/assetDetail'
import type { RealizedPnl, IncomeSummary } from '../../types/portfolio'

interface Props {
  position: AssetPosition
  /** Instrument ticker/symbol — labels each lot/disposal row's Lucius mention entity. */
  symbol: string
  instrumentId: string
  /** Position-scoped dividend/interest/distribution income — fetched once by AssetDetailView
   *  and shared with YourPositionCard. Null while loading, on error, or for a merged
   *  multi-account holding (no single position_id to scope to). */
  income: IncomeSummary | null
  onChanged?: () => void
  /** The instrument's asset class: a share's cost shows 2 decimals, a coin's up to 4. */
  assetClass?: string
}

function signed(n: number): string {
  return (n > 0 ? '+' : '') + fBase(n)
}

// Local calendar date (ISO) — a tranche dated today counts as "due", not "upcoming".
const todayIso = new Date().toISOString().slice(0, 10)

function Cell({
  label,
  value,
  valueTone,
  sub,
  subTone,
}: {
  label: string
  value: string
  valueTone?: string
  sub: string
  subTone?: string
}) {
  return (
    <div className="asd-pos-sum-cell">
      <div className="asd-pos-sum-k">{label}</div>
      <div className={`asd-pos-sum-v${valueTone ? ` ${valueTone}` : ''}`}>{value}</div>
      <div className={`asd-pos-sum-sub${subTone ? ` ${subTone}` : ''}`}>{sub}</div>
    </div>
  )
}

// The Position subtab body: a summary strip + the full acquisition/realized tables (LotsPanel,
// reused verbatim from the holdings table). Realized total is fetched once here for the strip's
// last cell — LotsPanel fetches its own copy for the table, which is acceptable (idempotent GET).
export function PositionSubtab({
  position,
  symbol,
  instrumentId,
  income,
  onChanged,
  assetClass,
}: Props) {
  const [fetchedRealized, setFetchedRealized] = useState<{ total: number; count: number } | null>(
    null,
  )
  const positionId = position.position_id

  useEffect(() => {
    // A holding merged across accounts has no single position_id to fetch by — its realized
    // events already arrived on `position.realized` (asset_detail.py pre-merges them), summed
    // below instead of fetched.
    if (!positionId) return
    let alive = true
    const params = new URLSearchParams({ position_id: positionId, with_predecessors: 'true' })
    authFetch(`/api/realized-pnl?${params.toString()}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: RealizedPnl) => {
        if (alive)
          setFetchedRealized({ total: d.realized_total_base ?? 0, count: (d.events ?? []).length })
      })
      .catch(() => {
        /* leave null → the cell shows an em-dash */
      })
    return () => {
      alive = false
    }
  }, [positionId])

  // Multi-account holding: sum the pre-merged events synchronously (same "known gains only"
  // rule LotsPanel's own realized-history total line uses) instead of a second network round
  // trip — the data already arrived with the page.
  const realized = positionId
    ? fetchedRealized
    : {
        total: position.realized.reduce((s, e) => s + (e.realized_gain_base ?? 0), 0),
        count: position.realized.length,
      }

  const sym = getCurrencySymbol(position.cost_currency)
  // A sub-unit cost takes fPrice's adaptive precision — SHIB at 0.0000241 read "$0.00".
  const avgCost = (n: number) =>
    n > 0 && n < 1
      ? fPrice(n, priceDigits(assetClass))
      : n.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: priceDigits(assetClass) ?? 4,
        })
  const hasPL = position.unrealized_pl_base != null && position.unrealized_pl_pct != null
  // cost basis in the base currency = market value − unrealized P&L (the inverse of the
  // backend's pl = market − cost); null when cost is unknown.
  const costBaseTotal =
    position.unrealized_pl_base != null
      ? position.market_value_base - position.unrealized_pl_base
      : null

  return (
    <>
      <div className="asd-pos-summary-wrap">
        <div
          className={`asd-pos-summary ${position.unvested ? 'asd-pos-summary--seven' : 'asd-pos-summary--six'}`}
        >
          <Cell
            label="Quantity"
            value={position.qty.toLocaleString(undefined, { maximumFractionDigits: 8 })}
            sub="shares"
          />
          <Cell
            label="Avg cost"
            value={position.cost_basis != null ? `${sym}${avgCost(position.cost_basis)}` : '—'}
            sub={costBaseTotal != null ? `basis ${fBase(costBaseTotal)}` : 'basis unknown'}
          />
          <Cell
            label="Market value"
            value={fBase(position.market_value_base)}
            sub={`${fPct(position.weight_pct)} of book`}
          />
          <Cell
            label="Unrealized P&L"
            value={hasPL ? signed(position.unrealized_pl_base!) : '—'}
            valueTone={hasPL ? plClass(position.unrealized_pl_base) : 'muted'}
            sub={hasPL ? fPct(position.unrealized_pl_pct!, { sign: true }) : ''}
            subTone={hasPL ? plClass(position.unrealized_pl_base) : undefined}
          />
          <Cell
            label="Realized P&L"
            value={realized ? signed(realized.total) : '—'}
            valueTone={realized ? plClass(realized.total) : 'muted'}
            sub={realized ? `${realized.count} disposal${realized.count === 1 ? '' : 's'}` : ''}
          />
          <Cell
            label="Dividends received"
            value={income ? fBase(income.trailing_12m_base) : '—'}
            sub="trailing 12m"
          />
          {position.unvested && (
            <Cell
              label="Unvested"
              value={`${position.unvested.units.toLocaleString(undefined, { maximumFractionDigits: 4 })} units`}
              sub={fBase(position.unvested.market_value_base)}
              subTone="muted"
            />
          )}
        </div>
      </div>

      {position.per_account.length > 1 && (
        <Panel className="asd-pos-accounts" title="Held at">
          <div style={{ overflowX: 'auto' }}>
            <table className="lots-table lots-table--accts">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Quantity</th>
                  <th>Market value</th>
                  <th>Avg cost</th>
                  <th>Cost basis</th>
                  <th>Unrealized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {position.per_account.map((a) => (
                  <tr key={a.account_id || a.account_name}>
                    <td>{a.account_name || '—'}</td>
                    <td>{a.quantity.toLocaleString(undefined, { maximumFractionDigits: 8 })}</td>
                    <td>{fBase(a.market_value_base)}</td>
                    <td>
                      {a.cost_basis != null
                        ? `${getCurrencySymbol(a.cost_currency)}${avgCost(a.cost_basis)}`
                        : '—'}
                    </td>
                    <td>
                      {a.cost_basis != null
                        ? `${getCurrencySymbol(a.cost_currency)}${fAmountNative(a.quantity * a.cost_basis, a.cost_currency)}`
                        : '—'}
                    </td>
                    <td
                      className={
                        a.unrealized_pl_base == null ? 'muted' : plClass(a.unrealized_pl_base)
                      }
                    >
                      {a.unrealized_pl_base == null ? '—' : signed(a.unrealized_pl_base)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {positionId ? (
        <LotsPanel
          positionId={positionId}
          instrumentId={instrumentId}
          symbol={symbol}
          costCcy={position.cost_currency}
          base={position.market_value_base}
          costBase={costBaseTotal}
          writeTargets={writeTargetsOf(position)}
          onChanged={onChanged}
        />
      ) : position.per_account.length > 1 ? (
        // Multi-account holding: no single position_id to fetch by, so LotsPanel renders the
        // already-merged lots/realized/income asset_detail.py combined across every
        // contributing position (per_account above shows the same accounts).
        <LotsPanel
          positionId={null}
          instrumentId={instrumentId}
          symbol={symbol}
          costCcy={position.cost_currency}
          base={position.market_value_base}
          costBase={costBaseTotal}
          merged={{ lots: position.lots, realized: position.realized, income: position.income }}
          writeTargets={writeTargetsOf(position)}
          onChanged={onChanged}
        />
      ) : (
        // Locked-only holding (an equity grant with no vested shares yet) — genuinely no
        // sellable lots exist, not a multi-account ambiguity.
        <Panel>
          <div className="lots-empty">
            This is an unvested equity grant — no sellable lots yet. See the vesting schedule below
            once it vests.
          </div>
        </Panel>
      )}

      {position.unvested && (position.unvested.tranches?.length ?? 0) > 0 && (
        <Panel className="asd-pos-accounts" title="Vesting schedule">
          <div style={{ overflowX: 'auto' }}>
            <table className="lots-table lots-table--accts">
              <thead>
                <tr>
                  <th>Vest date</th>
                  <th>Units</th>
                  <th>Est. value</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {position.unvested.tranches.map((t, i) => {
                  const status = t.vested ? 'vested' : t.date <= todayIso ? 'due' : 'upcoming'
                  return (
                    <tr key={`${t.date}-${i}`} className={t.vested ? 'muted' : undefined}>
                      <td>{t.date ? fDate(t.date, 'short', 'always') : '—'}</td>
                      <td>{t.units.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
                      <td>{t.value_base != null ? fBase(t.value_base) : '—'}</td>
                      <td>
                        <span className={`vest-badge vest-${status}`}>{status}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  )
}

function writeTargetsOf(position: AssetPosition): WriteTarget[] {
  return position.per_account
    .filter((a) => a.position_id)
    .map((a) => ({
      positionId: a.position_id as string,
      accountId: a.account_id,
      accountName: a.account_name,
    }))
}
