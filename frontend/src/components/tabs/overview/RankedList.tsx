// A ranked list — rows biggest to smallest, each with its own proportional bar and its
// amount printed on the row. Plain elements, not a chart: every figure is readable without a
// tooltip, the whole row (name included) is one tap target, and a screen reader can read it.
// Top merchants used a canvas bar chart, where the amounts hid behind a tooltip that a phone
// tap could never show (the tap navigated away first) and only the bar area answered taps.

import { ChevronRight } from 'lucide-react'
import { useMaskText } from '../../../utils/amountMask'
import { fBase, fBaseCompact, fPct } from '../../../utils/format'

export interface RankedRow {
  key: string
  label: string
  value: number
  /** Share of the whole, from the server. */
  pct: number
  color: string
}

export function RankedList({
  rows,
  onSelect,
  canSelect,
  selectLabel,
}: {
  rows: RankedRow[]
  /** Makes each row a button that does this. */
  onSelect?: (row: RankedRow) => void
  /** A row this returns false for stays a plain row (nothing to open). */
  canSelect?: (row: RankedRow) => boolean
  /** Accessible name of the action, e.g. "Show transactions". */
  selectLabel?: string
}) {
  const maskText = useMaskText()
  const max = Math.max(0, ...rows.map((r) => r.value))
  return (
    <ol className="ov-ranked">
      {rows.map((r) => {
        const selectable = !!onSelect && (canSelect ? canSelect(r) : true)
        const body = (
          <>
            <span className="ov-ranked-label">{r.label}</span>
            <span className="ov-ranked-amt money">
              <span className="ov-ranked-amt-full">{fBase(r.value)}</span>
              <span className="ov-ranked-amt-compact">{fBaseCompact(r.value)}</span>
            </span>
            <span className="ov-ranked-pct money">{fPct(r.pct)}</span>
            {selectable && <ChevronRight className="ov-ranked-go" size={14} aria-hidden="true" />}
            <span className="ov-ranked-track" aria-hidden="true">
              <i
                style={{ width: `${max > 0 ? (r.value / max) * 100 : 0}%`, background: r.color }}
              />
            </span>
          </>
        )
        return (
          <li key={r.key}>
            {selectable && onSelect ? (
              <button
                type="button"
                className="ov-ranked-row"
                onClick={() => onSelect(r)}
                aria-label={`${r.label}, ${maskText(fBase(r.value))}${selectLabel ? ` — ${selectLabel}` : ''}`}
              >
                {body}
              </button>
            ) : (
              <div className="ov-ranked-row">{body}</div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
