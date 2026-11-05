// budgetLens/proportional/propCells.tsx — the leaf cells of a 50/30/20 envelope row, shared by the
// desktop table row (PropRows.tsx) and the phone card (PropCards.tsx): the same pattern as
// Zero-Based's CategoryRow, which hands the SAME leaf controls to both of its layouts
// (budgetCards.tsx) so a phone never loses what the desktop row shows (owner, 2026-09-25).
import { useState } from 'react'
import { useMaskText } from '../../../../../utils/amountMask'
import type { DragEvent } from 'react'
import { ActivityCell } from '../../budgetActivity'
import type { RawTxn } from '../../budgetActivity'
import type { RegisterFilter } from '../../budget'
import { EnvCatsBadge, EnvCatsEmptyHint } from '../../budget'
import { Kebab } from '../../budgetParts'
import type { KebabItem } from '../../budgetParts'
import { fFig, fSmart, parseMoneyInput } from '../../planData'
import { fmt } from './propMath'
import type { GroupFigures } from './propMath'
import type { PropEnvelope } from './propApi'
import type { RowDrop } from './propDnd'

export const Chev = ({ dir }: { dir: 'down' | 'right' }) => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    style={{ transform: dir === 'right' ? 'rotate(-90deg)' : 'none' }}
  >
    <path d="M3 4.5l3 3 3-3" />
  </svg>
)

export interface RowHandlers {
  month: string
  /** A past month is history: its caps are read-only. */
  capsLocked: boolean
  /** Phone width — rows render as Zero-Based's cards (budgetCards.tsx), not table rows. */
  mobile: boolean
  dragging: string | null
  /** The row a dragged envelope is over (and which half) — where it would land. */
  overRow: RowDrop | null
  catLabel: (key: string) => string
  catColor: (key: string) => string
  onDragStart: (id: string) => void
  onDragEnd: () => void
  onRowOver: (id: string) => (e: DragEvent) => void
  onRowDrop: (id: string, groupId: string) => (e: DragEvent) => void
  onEdit: (id: string) => void
  onCap: (id: string, v: number) => void
  onRemoveCategory: (envelopeId: string, categoryId: string) => void
  onOpenTxn?: (raw: RawTxn) => void
  onOpenRegister?: (filter: RegisterFilter) => void
}

/** A "—": this cell has no figure (no cap, or not sorted yet) — never a S$0.00 it doesn't have. */
export const Dash = () => <span className="pl-fig zero">—</span>

/** The cap, as the Zero-Based table's Assigned cell: a number that becomes an input on click. */
function CapInput({
  value,
  label,
  onChange,
}: {
  value: number
  label: string
  onChange: (v: number) => void
}) {
  const [edit, setEdit] = useState(false)
  const [draft, setDraft] = useState('')
  const commit = () => {
    setEdit(false)
    const next = parseMoneyInput(draft)
    if (next !== value) onChange(next)
  }
  return edit ? (
    <span
      className="pl-assign-edit-wrap"
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <input
        autoFocus
        className="pl-assign-edit"
        inputMode="decimal"
        aria-label={label}
        value={draft}
        draggable={false}
        onDragStart={(e) => e.preventDefault()}
        onChange={(e) => setDraft(e.target.value.replace(/[^0-9.]/g, ''))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            setEdit(false)
          }
        }}
      />
    </span>
  ) : (
    <button
      type="button"
      className={'pl-assign mask' + (value ? '' : ' zero')}
      title="Click to set a cap"
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation()
        setDraft(value ? String(value) : '')
        setEdit(true)
      }}
    >
      {value ? fFig(value).text : '—'}
    </button>
  )
}

/** Cap: read-only in a past month, else the click-to-edit cap. */
export function CapCell({ e, h }: { e: PropEnvelope; h: RowHandlers }) {
  const cap = e.cap ?? 0
  if (h.capsLocked && !cap) return <Dash />
  if (h.capsLocked) return <span className="pl-fig mask">{fFig(cap).text}</span>
  return (
    <CapInput value={cap} label={e.label + ' monthly cap'} onChange={(v) => h.onCap(e.id, v)} />
  )
}

/** Activity: Zero-Based's own cell (spend is negative there), with its transactions popover. */
export function ActivityFor({ e, h }: { e: PropEnvelope; h: RowHandlers }) {
  return (
    <ActivityCell
      c={{
        activity: -e.activity,
        label: e.label,
        color: e.color,
        categoryIds: e.category_ids,
        envelopeId: e.id,
      }}
      month={h.month}
      onOpenTxn={h.onOpenTxn}
      onOpenRegister={h.onOpenRegister}
    />
  )
}

/** Left: the cap less what's spent. No cap and nothing spent reads "—" — a limit it never had
 *  can't be under or over. No cap but spending shows that spend in the warning tint: money going
 *  out with no limit on it (the tint Zero-Based uses for a target not met). */
export function LeftCell({ e }: { e: PropEnvelope }) {
  const cap = e.cap ?? 0
  if (!cap)
    return e.activity > 0.005 ? (
      <span className="pl-avail mask under" title="No cap — spending with no limit on it">
        {fFig(-e.activity).text}
      </span>
    ) : (
      <Dash />
    )
  const left = cap - e.activity
  return (
    <span className={'pl-avail mask ' + (left < 0 ? 'bad' : left > 0 ? 'good' : 'zero')}>
      {fFig(left).text}
    </span>
  )
}

/** Zero-Based's category chip — or its "0" hint when the envelope has no category yet. */
export function CatsChip({ e, h }: { e: PropEnvelope; h: RowHandlers }) {
  if (!e.category_ids.length) return <EnvCatsEmptyHint />
  return (
    <EnvCatsBadge
      categoryIds={e.category_ids}
      catLabel={h.catLabel}
      catColor={h.catColor}
      onRemove={(key) => h.onRemoveCategory(e.id, key)}
      onAddCategories={() => h.onEdit(e.id)}
    />
  )
}

/** A group header's ⋮ — Zero-Based's own menu (budgetParts Kebab, owner 2026-09-26: "these things
 *  we can make the same style"): its share of income, in or out of the split, and — never for a
 *  group the app keeps, which Zero-Based locks too — its name and a new envelope in it. */
export function GroupKebab({
  f,
  onRename,
  actions,
}: {
  f: GroupFigures
  onRename: () => void
  actions: GroupActions
}) {
  const { g } = f
  const items: KebabItem[] = [
    ...(g.excluded ? [] : [{ label: 'Set shares of income…', onClick: actions.onSetShares }]),
    {
      label: g.excluded ? 'Count in the split' : 'Exclude from the split',
      onClick: () => actions.onToggleExcluded(g.id, !g.excluded),
    },
    ...(g.synced
      ? []
      : [
          { divider: true as const },
          { label: 'Rename group', onClick: onRename },
          { label: 'Add envelope', onClick: () => actions.onAddEnvelope(g.id, g.name) },
        ]),
  ]
  return <Kebab label={g.name + ' group options'} items={items} />
}

/** Dragging a group by its grip, as on Zero-Based — one order for both methods. */
export interface GroupDnd {
  dragging: string | null
  over: string | null
  start: (id: string) => (e: DragEvent) => void
  onOver: (id: string) => (e: DragEvent) => void
  onDrop: (id: string) => (e: DragEvent) => void
  end: () => void
}

/** Zero-Based's status line (budget.tsx CategoryRow), read against the cap: within it "Within
 *  cap. Spent S$645.18 of S$650", at it "Fully Spent", past it "Over cap" — the overage is the
 *  Left cell's own red figure, as Zero-Based says just "Overspent". */
export function CapStatus({ e }: { e: PropEnvelope }) {
  const cap = e.cap ?? 0
  const spent = e.activity
  if (!cap) return spent > 0.005 ? <span className="pl-cstatus">No cap</span> : null
  if (spent > cap + 0.005) return <span className="pl-cstatus over">Over cap</span>
  if (spent >= cap - 0.005) return <span className="pl-cstatus spent">Fully Spent</span>
  return (
    <span className="pl-cstatus funded">
      <span className="pl-cstatus-word">Within cap</span>
      {spent > 0.005 && (
        <span className="pl-cstatus-detail">
          . Spent <span className="mask">{fSmart(spent)}</span> of{' '}
          <span className="mask">{fSmart(cap)}</span>
        </span>
      )}
    </span>
  )
}

/** Zero-Based's bar, with the cap as its whole length: the share already spent hatched, what is
 *  left solid; past the cap, the cap's share green and the overage red. No cap, no bar. */
export function CapBar({ e }: { e: PropEnvelope }) {
  const maskText = useMaskText()
  const cap = e.cap ?? 0
  if (!cap) return null
  const spent = Math.max(0, e.activity)
  const title = maskText(`Spent ${fSmart(spent)} of a ${fSmart(cap)} cap`)
  if (spent > cap + 0.005) {
    const inCap = (cap / spent) * 100
    return (
      <span className="pl-tbar-wrap" title={title}>
        <span className="pl-tbar">
          <span className="pl-tbar-fill funded" style={{ width: inCap + '%' }} />
          <span className="pl-tbar-fill over" style={{ width: 100 - inCap + '%' }} />
        </span>
      </span>
    )
  }
  const used = Math.min(100, (spent / cap) * 100)
  return (
    <span className="pl-tbar-wrap" title={title}>
      <span className="pl-tbar">
        {used > 0 && <span className="pl-tbar-fill spent funded" style={{ width: used + '%' }} />}
        {used < 100 && <span className="pl-tbar-fill funded" style={{ width: 100 - used + '%' }} />}
      </span>
    </span>
  )
}

/** What a group header does besides folding: its share, in or out of the split, its name, a new
 *  envelope in it. */
export interface GroupActions {
  onSetShares: () => void
  onToggleExcluded: (groupId: string, excluded: boolean) => void
  onRename: (groupId: string, name: string) => void
  onAddEnvelope: (groupId: string, groupName: string) => void
}

/** "target 30% · S$5,222", "Excluded", "no target set" (held to 0% while other groups have one), or
 *  nothing while no group has a share. */
export function GroupShare({ f, income }: { f: GroupFigures; income: number }) {
  if (f.g.excluded) return <span className="t">Excluded</span>
  if (f.share === null) return null
  if (f.g.target === null)
    return (
      <span
        className="t"
        title="No share of income is set for this group, so its spending reduces what you save"
      >
        no target set
      </span>
    )
  return (
    <span className="t">
      target {f.g.target}%{income > 0 && f.targetAmt !== null ? ' · ' + fmt(f.targetAmt) : ''}
    </span>
  )
}
