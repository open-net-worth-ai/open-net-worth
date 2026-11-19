// Rebalance — rebalancing suggestions panel. Verbatim port of prototype Rebalance().
// Shows trades needed to bring each asset class back to its target allocation.

import { fBase } from '../../../utils/format'
import { MaskedText } from '../../ui/MaskedText'
import type { UntrackedExcluded } from '../../../types/aggregates'
import { UntrackedNote } from './UntrackedHoldings'

interface AllocRow {
  cls: string
  cur: number // ROUNDED, display only
  tgt: number // ROUNDED, display only
  // Unrounded, server-computed (backend/services/investments_book.py `_sections`) — MATH-38:
  // sizing a trade off the rounded cur/tgt compounded the rounding into real money (book
  // 1,000,000, current 32.6%, target 29.4% → the true trade is -32,000, the rounded one -40,000).
  driftPp: number
  tradeBase: number
}

interface Props {
  alloc: AllocRow[]
  // Drift (pp) at/above which a class is flagged — the server's ONE constant
  // (rebalance.DRIFT_FLAG_PP), never a number of our own (MATH-37).
  thresholdPp: number
  /** Brokerage total accounts' holdings not entered — out of the trades, said under them. */
  untracked?: UntrackedExcluded | null
}

function Card({
  title,
  meta,
  children,
}: {
  title: string
  meta?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="mk-card">
      <div className="mk-card-head">
        <span className="mk-card-title">{title}</span>
        {meta && <span className="mk-card-meta">{meta}</span>}
      </div>
      <div className="mk-card-body">{children}</div>
    </div>
  )
}

export function Rebalance({ alloc, thresholdPp, untracked }: Props) {
  const hasTargets = alloc.some((a) => a.tgt > 0)
  const untrackedNote = untracked ? <UntrackedNote u={untracked} action="rebalance" /> : null

  if (!hasTargets) {
    return (
      <Card title="Rebalancing" meta="no targets set">
        <div style={{ fontSize: 12.5, color: 'var(--faint)' }}>
          Set allocation targets in the panel above to see rebalancing suggestions.
        </div>
        {untrackedNote}
      </Card>
    )
  }

  // Trade size and direction come straight from the server (`driftPp`/`tradeBase`) — never
  // re-derived from the rounded `cur`/`tgt` display percents (MATH-38).
  const trades = alloc
    .filter((a) => Math.abs(a.driftPp) >= thresholdPp)
    .map((a) => ({
      act: a.driftPp > 0 ? 'trim' : 'buy',
      cls: a.cls,
      amt: a.tradeBase,
      why: `${a.cls} ${a.cur}% → ${a.driftPp > 0 ? 'trim' : 'top up'} toward ${a.tgt}%`,
    }))
    .sort((a, b) => Math.abs(b.amt) - Math.abs(a.amt))

  return (
    <Card
      title="Rebalancing"
      meta={trades.length ? `${trades.length} trade${trades.length === 1 ? '' : 's'}` : 'on target'}
    >
      {trades.length ? (
        <div className="mk-rb">
          {trades.map((t, i) => (
            <div className="mk-rb-row" key={i}>
              <span className={`mk-rb-act ${t.act}`}>{t.act === 'trim' ? 'TRIM' : 'BUY'}</span>
              <span className="mk-rb-cls">{t.cls}</span>
              <span
                className="mk-rb-amt mask"
                style={{ color: t.amt < 0 ? 'var(--bad)' : 'var(--good)' }}
              >
                {(t.amt < 0 ? '−' : '+') + fBase(Math.abs(t.amt))}
              </span>
              <span className="mk-rb-why">
                <MaskedText>{t.why}</MaskedText>
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: 'var(--faint)' }}>
          Already on target — no trades needed.
        </div>
      )}
      {trades.length > 0 && (
        <div className="mk-rb-foot">
          <div className="mk-rb-note">
            Suggested trades to place at your brokerage — ONW tracks your book, it doesn&apos;t
            execute.
          </div>
        </div>
      )}
      {untrackedNote}
    </Card>
  )
}
