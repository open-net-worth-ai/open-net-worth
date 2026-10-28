// SplitView.tsx — the read-only face of a split transaction, shared by the register (desktop
// row + mobile card) and the detail drawer. A bare "Split • 6" said nothing about where the
// money went (owner, 2026-10-29): the chip leads with the largest line's category on a blend
// of every line's colour, and the lines carry the parent's sign so a split inflow never reads
// like six charges.

import type { Category, TxnSplit } from './types'
import { fCur } from './format'
import { ChevronUp, ChevronDown } from '../../../ui/icons'

/** The line that best answers "what was this for" — the largest; the first on a tie. */
function splitLead(splits: TxnSplit[]): TxnSplit | undefined {
  return splits.reduce<TxnSplit | undefined>(
    (best, s) => (!best || s.amount > best.amount ? s : best),
    undefined,
  )
}

export function SplitChip({
  splits,
  catMap = {},
  open,
  onToggle,
}: {
  splits: TxnSplit[]
  catMap?: Record<string, Category>
  open: boolean
  onToggle: () => void
}) {
  const lead = splitLead(splits)
  const c = lead ? catMap[lead.category] : undefined
  const more = splits.length - 1
  const tints = splits.map(
    (s) => `color-mix(in srgb, ${catMap[s.category]?.color || '#7c8595'} 22%, transparent)`,
  )
  const background = tints.length > 1 ? `linear-gradient(90deg, ${tints.join(', ')})` : tints[0]
  return (
    <button
      type="button"
      className="acct-split-chip"
      style={{ background }}
      aria-expanded={open}
      title={`Split across ${splits.length} categories — ${open ? 'hide' : 'show'} the lines`}
      onClick={(e) => {
        e.stopPropagation()
        onToggle()
      }}
    >
      <span className="acct-split-chip-lead">{c?.label || 'Uncategorized'}</span>
      {more > 0 && <span className="acct-split-chip-more">+{more}</span>}
      {open ? (
        <ChevronUp size={12} aria-hidden="true" />
      ) : (
        <ChevronDown size={12} aria-hidden="true" />
      )}
    </button>
  )
}

/** One row per split line: category, optional note, signed amount. */
export function SplitLines({
  splits,
  catMap = {},
  currency,
  sign,
  className,
}: {
  splits: TxnSplit[]
  catMap?: Record<string, Category>
  currency: string
  sign: '+' | '−'
  className: string
}) {
  return (
    <div className={'acct-splitlines ' + className}>
      {splits.map((s, i) => {
        const c = catMap[s.category]
        return (
          <div className="acct-splitline" key={i}>
            <i className="acct-splitline-dot" style={{ background: c?.color || '#7c8595' }} />
            <span className="acct-splitline-text">
              <span className="acct-splitline-cat">{c?.label || 'Uncategorized'}</span>
              {s.note && <span className="acct-splitline-note">{s.note}</span>}
            </span>
            <span className="acct-splitline-amt">
              {sign}
              {fCur(s.amount, currency, { cents: true }).replace('-', '')}
            </span>
          </div>
        )
      })}
    </div>
  )
}
