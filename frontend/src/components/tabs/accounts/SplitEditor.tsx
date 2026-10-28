// SplitEditor.tsx — inline multi-category split editor for the transaction detail drawer.
//
// A split allocates one expense/income leg across ≥2 categories. The backend contract
// (services/ledger._validate_splits) requires every line to carry a valid category + a
// positive amount, and Σ amounts == the leg amount (±0.01). This editor surfaces that
// contract live: a remaining pill turns "balanced" only when the lines sum to the total,
// and the parent gates "Save changes" on the same rule (see EditBody.splitValid).
import { fCur } from './data'
import type { Category } from './types'
import { AddCategoryField } from './pickers'
import { X, Plus } from '../../../ui/icons'

/** One editable split line. Amount is a STRING while typing (allows "12." mid-entry); the
 *  parent parses to a number on save. */
export interface EditSplit {
  category: string
  amount: string
  note: string
}

const round2 = (n: number) => Math.round(n * 100) / 100

export interface SplitEditorProps {
  lines: EditSplit[]
  onChange: (lines: EditSplit[]) => void
  /** The leg total the lines must sum to (abs, in `currency`). */
  total: number
  currency: string
  options: (Category & { group: string })[]
  onCreateCategory: (prefill: string, cb: (id: string) => void) => void
  /** Called when the user removes the last line or clicks "Unsplit" — the parent exits split mode. */
  onUnsplit: () => void
}

export function SplitEditor({
  lines,
  onChange,
  total,
  currency,
  options,
  onCreateCategory,
  onUnsplit,
}: SplitEditorProps) {
  const assigned = lines.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0)
  const remaining = round2(total - assigned)
  const balanced = Math.abs(remaining) <= 0.005

  const patchLine = (i: number, patch: Partial<EditSplit>) =>
    onChange(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const removeLine = (i: number) => {
    const next = lines.filter((_, j) => j !== i)
    if (next.length === 0) onUnsplit()
    else onChange(next)
  }
  const addLine = () =>
    onChange([...lines, { category: '', amount: remaining > 0 ? String(remaining) : '', note: '' }])

  return (
    <div className="acct-edit-field acct-edit-splitwrap" data-ctx-ignore>
      <div className="acct-edit-splithead">
        <span className="acct-edit-label">
          Split • {lines.length} {lines.length === 1 ? 'category' : 'categories'}
        </span>
        <button type="button" className="acct-edit-unsplit" onClick={onUnsplit}>
          Unsplit
        </button>
      </div>
      <div className="acct-edit-splitrows">
        {lines.map((line, i) => (
          <div className="acct-edit-splitrow" key={i}>
            <div className="acct-edit-splitmain">
              <div className="acct-edit-splitcat">
                <AddCategoryField
                  value={line.category}
                  options={options}
                  clearable
                  onChange={(id: string) => patchLine(i, { category: id })}
                  onCreate={(prefill: string, cb: (id: string) => void) =>
                    onCreateCategory(prefill, cb)
                  }
                  advance={() => {}}
                />
              </div>
              <div className="acct-edit-splitamt">
                <span className="acct-edit-splitccy">{currency}</span>
                <input
                  className="acct-edit-splitamt-in"
                  type="number"
                  inputMode="decimal"
                  value={line.amount}
                  onChange={(e) => patchLine(i, { amount: e.target.value })}
                  placeholder="0.00"
                  aria-label="Split amount"
                />
              </div>
              <button
                type="button"
                className="acct-edit-splitrm"
                onClick={() => removeLine(i)}
                title="Remove this line"
                aria-label="Remove split line"
              >
                <X size={12} aria-hidden="true" />
              </button>
            </div>
            <input
              className="acct-edit-splitnote"
              value={line.note}
              onChange={(e) => patchLine(i, { note: e.target.value })}
              placeholder="Memo (optional)"
              aria-label="Split memo"
            />
          </div>
        ))}
      </div>
      <div className="acct-edit-splitfoot">
        <button type="button" className="acct-edit-splitadd" onClick={addLine}>
          <Plus size={12} aria-hidden="true" /> Add line
        </button>
        <span className={`acct-edit-splitremain${balanced ? ' ok' : ''}`}>
          {balanced ? 'Balanced' : `Remaining ${fCur(remaining, currency, { cents: true })}`}
        </span>
      </div>
    </div>
  )
}

export default SplitEditor
