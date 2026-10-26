// ImportModal.tsx — every route (Lucius AND manual column mapping) previews + commits
// through the ONE server decision pipeline; the manual mapping is sent to the server as a
// TEACH (saved as the source's format profile) before the same decision preview runs.
import { Fragment, useState, useRef, useMemo, useEffect, useLayoutEffect } from 'react'
import { authFetch } from '../../../api/client'
import { Modal, Button, FormField, Select, EmptyState, SegmentedControl } from '../../ui'
import { Spark as LuciusSpark } from '../../../lucius/identity'
import { fCur, fromApiTxn } from './data'
import './statementImport.css'
import { ConfirmModal } from './ConfirmModal'
import { BookedScreen } from './BookedScreen'
import { Ic, Spark, I_CHECK, I_WARN } from './importIcons'
import { AddCategoryField, AddPayeeField, type PayeeOption } from './pickers'
import { catOptionsFor } from './categories'
import TxnDetail from './TxnDetail'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { X, ArrowLeftRight, Plus, ChevronDown, ChevronRight } from '../../../ui/icons'
import { fDate } from '../../../utils/format'
import { fetchVaultItem, type VaultItem } from '../../../api/vault'
import { PreviewLoading } from '../../vault/PreviewLoading'
import { DocumentPreview } from '../../vault/DocumentPreview'
import type { TxnDetailProps } from './TxnDetail'
import type { Account, ApiTxn, Category, FxRates, Txn, TxnActivity, SourceEntry } from './types'
import {
  newImportProgressToken,
  usePreviewProgress,
  useStatementParseStatus,
  fetchParseStatus,
} from '../../../hooks/useImportProgress'
import {
  FORMAT_LABEL,
  ACCEPT,
  needsMapping,
  detectFormat,
  parseCSVGrid,
  parseXlsxGrid,
  findHeaderRow,
  FLAG_LABEL,
  computeMap,
  mapComplete,
  existingTxnLine,
  transferLine,
  originalCharge,
  matchOptionLine,
  driftPhrase,
  afterImportProjection,
  mergeAdoptsStatementFacts,
  coverageVerdict,
  amountsVerdict,
  linkedAmountDrift,
  offByTitle,
  anchorPerspectiveAmount,
  anchorPerspectiveCurrency,
  PROGRESS_STAGE_ORDER,
  progressPhaseLabel,
  progressStageStatus,
  formatElapsed,
} from './importHelpers'
import type {
  ImportPreviewDraft,
  ImportPreviewResult,
  ColumnMap,
  LedgerCheckRow,
  ExistingSummaryLike,
  VerdictRowInput,
} from './importHelpers'

export type { ImportPreviewDraft, ImportPreviewResult }

// The Read step's rotating phase line, verbatim from the handoff prototype
// (`flow3.jsx` READ_PHASES). The real read takes longer than the animation, so the
// line CLAMPS on the last phase instead of looping — a phase that came back round
// would claim work that already finished.
/** How wide the viewer last dragged the file pane beside the review — a per-viewer convenience. */
const FILE_WIDTH_KEY = 'onw.import.fileWidth'
const FILE_MIN_PX = 280
const REVIEW_MIN_PX = 420

const READ_PHASES = [
  'Reading the statement',
  'Finding the transaction table',
  'Checking the statement against its own balances',
  'Applying your instructions',
  'Checking every row against your ledger',
]

// Mirrors TxnDetail.tsx's own KIND_LABEL — the matched-ledger-entry's kind, shown on the
// collapsed match line so its date + kind are visible without expanding.
const MATCH_KIND_LABEL: Record<string, string> = {
  expense: 'Expense',
  income: 'Income',
  transfer: 'Transfer',
  investment: 'Investment',
  adjustment: 'Balance adjustment',
}
/** Roughly how many characters `.imp3-arow-name` shows before its ellipsis bites: a fixed 252px
 * rail (statementImport.css `.imp3-arow-main`) at 12.5px/600. An expanded body restates the
 * description only ABOVE this, because below it the name above is already fully legible and
 * repeating it is noise (owner, 2026-07-31 — "Description: Cirrus AI"). Deliberately
 * conservative: over-showing a description costs a few words, under-showing hides the one field
 * that identifies the transaction. */
const NAME_RAIL_CHARS = 34

interface ImpSelectOption {
  value: string
  label: string
}

function ImpSelect({
  options,
  value,
  onChange,
  placeholder,
  ariaLabel,
}: {
  options: ImpSelectOption[]
  value: string
  onChange: (v: string) => void
  placeholder?: string
  ariaLabel?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const cur = options.find((o) => o.value === value)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', k)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', k)
    }
  }, [open])
  return (
    <div className="acct-acctsel imp-acctsel" ref={ref}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={'acct-acctsel-btn imp-acctsel-btn' + (open ? ' open' : '')}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="acct-acctsel-nm" style={cur ? undefined : { color: 'var(--faint)' }}>
          {cur ? cur.label : placeholder || 'Select…'}
        </span>
        <svg
          className="acct-acctsel-caret"
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="acct-acctsel-pop imp-acctsel-pop" role="listbox">
          {options.map((o) => (
            <button
              type="button"
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              className={'acct-acctsel-item' + (o.value === value ? ' sel' : '')}
              onClick={() => {
                onChange(o.value)
                setOpen(false)
              }}
            >
              <span className="acct-acctsel-check">{o.value === value ? '✓' : ''}</span>
              <span className="acct-acctsel-nm">{o.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Compact "filter by kind" control — a single icon button, not three standing chips, so a
 *  second filter dimension doesn't visually compete with the decision chips it sits beside. */
/** A category the review's rows carry, for the filter menu — `key` is the category id, or
 *  UNCATEGORIZED_KEY for rows with none. */
interface CategoryFilterOption {
  key: string
  label: string
  color: string
  n: number
}
const UNCATEGORIZED_KEY = '__uncategorized__'

function KindFilterButton({
  value,
  onChange,
  counts,
  categories,
  category,
  onCategoryChange,
}: {
  value: 'all' | 'expense' | 'income' | 'transfer'
  onChange: (v: 'all' | 'expense' | 'income' | 'transfer') => void
  counts: { expense: number; income: number; transfer: number }
  /** Owner, 2026-10-26: "the filters should support not just type but categories, including the
   *  uncategorized ones". */
  categories: CategoryFilterOption[]
  category: string
  onCategoryChange: (key: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', k)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', k)
    }
  }, [open])
  const options = (
    [
      { v: 'expense', label: 'Expense', n: counts.expense },
      { v: 'income', label: 'Income', n: counts.income },
      { v: 'transfer', label: 'Transfer', n: counts.transfer },
    ] satisfies Array<{ v: 'expense' | 'income' | 'transfer'; label: string; n: number }>
  ).filter((o) => o.n > 0)
  const showKinds = options.length >= 2
  const showCategories = categories.length >= 2 || category !== 'all'
  if (!showKinds && !showCategories) return null
  const active = value !== 'all' || category !== 'all'
  const catLabel = categories.find((c) => c.key === category)?.label
  return (
    <div className="imp3-kindfilter" ref={ref}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Filter by type or category"
        title={
          active
            ? `Filtering by ${[value !== 'all' ? value : null, catLabel].filter(Boolean).join(' · ')}`
            : 'Filter by type or category'
        }
        className={'imp3-kindfilter-btn' + (active ? ' on' : '')}
        onClick={() => setOpen((o) => !o)}
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
        </svg>
      </button>
      {open && (
        <div className="imp3-kindfilter-pop">
          {showKinds && (
            <div role="listbox" aria-label="Type">
              <div className="imp3-kindfilter-group">Type</div>
              <button
                type="button"
                role="option"
                aria-selected={value === 'all'}
                className={value === 'all' ? 'sel' : ''}
                onClick={() => {
                  onChange('all')
                  setOpen(false)
                }}
              >
                All types
              </button>
              {options.map((o) => (
                <button
                  type="button"
                  key={o.v}
                  role="option"
                  aria-selected={value === o.v}
                  className={value === o.v ? 'sel' : ''}
                  onClick={() => {
                    onChange(value === o.v ? 'all' : o.v)
                    setOpen(false)
                  }}
                >
                  {o.label} <i>{o.n}</i>
                </button>
              ))}
            </div>
          )}
          {showCategories && (
            <div role="listbox" aria-label="Category" className="imp3-kindfilter-cats">
              <div className="imp3-kindfilter-group">Category</div>
              <button
                type="button"
                role="option"
                aria-selected={category === 'all'}
                className={category === 'all' ? 'sel' : ''}
                onClick={() => {
                  onCategoryChange('all')
                  setOpen(false)
                }}
              >
                All categories
              </button>
              {categories.map((c) => (
                <button
                  type="button"
                  key={c.key}
                  role="option"
                  aria-selected={category === c.key}
                  className={category === c.key ? 'sel' : ''}
                  onClick={() => {
                    onCategoryChange(category === c.key ? 'all' : c.key)
                    setOpen(false)
                  }}
                >
                  <span className="imp3-kindfilter-cat">
                    <b style={{ background: c.color }} aria-hidden="true" />
                    {c.label}
                  </span>
                  <i>{c.n}</i>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Payee/category edit — a proper modal (owner: "let's have a pencil button to show and we
 *  open a modal to change the detail... do not create new fields") rather than inline fields
 *  bolted onto the row. Local payee/category state is fresh per mount — the caller keys this
 *  by the editing row's index so switching rows never leaks stale input between them. */
function EditDraftModal({
  d,
  catList,
  payeeOptions,
  transferAccounts,
  onManagePayees,
  onCreateCategory,
  onSave,
  onSetTransfer,
  onClose,
}: {
  d: ImportPreviewDraft
  catList: Category[]
  payeeOptions: PayeeOption[]
  /** The accounts this row can be a transfer with: same currency, not the statement's own. */
  transferAccounts: Account[]
  onManagePayees?: () => void
  onCreateCategory?: (prefill: string, kind: 'income' | 'expense', cb: (id: string) => void) => void
  onSave: (patch: { category?: string | null; payee?: string }) => void
  /** Type switch: an account books the row as a transfer with it; null books it plain again. */
  onSetTransfer: (accountId: string | null) => void
  onClose: () => void
}) {
  const [payee, setPayee] = useState(d.payee || '')
  const [category, setCategory] = useState(d.category || '')
  const outflow = d.amount < 0
  // A routed row's OTHER side: money out lands in `to`, money in comes from `from`.
  const routedTo = d.routed ? (outflow ? d.routed.to_account_id : d.routed.from_account_id) : ''
  const [type, setType] = useState<'plain' | 'transfer'>(d.routed ? 'transfer' : 'plain')
  const [transferTo, setTransferTo] = useState(routedTo || '')
  const plainLabel = outflow ? 'Expense' : 'Income'
  const save = () => {
    if (type === 'transfer') {
      if (transferTo !== routedTo) onSetTransfer(transferTo)
    } else {
      if (d.routed) onSetTransfer(null)
      onSave({ payee, category: category || null })
    }
    onClose()
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Edit payee & category"
      size="sm"
      footer={
        <>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={type === 'transfer' && !transferTo}
            onClick={save}
          >
            Save
          </Button>
        </>
      }
    >
      {/* The SAME field chrome TxnDetail's own edit form uses (acct-edit-*, accounts.css) —
          not the generic ui/FormField, which doesn't carry the width/spacing rules these
          picker components (.acct-payf/.acct-acctsel) depend on here. */}
      <div className="acct-edit-body">
        {/* Type (owner, 2026-10-26: "it's a transfer — we are still not allowing to change the
            type?"): Transfer swaps Payee + Category for the other account — a transfer between
            your own accounts books no payee or category; the other account is its counterpart. */}
        <div className="acct-edit-field">
          <div className="acct-edit-label">Type</div>
          <SegmentedControl
            ariaLabel="Type"
            options={[
              { value: 'plain', label: plainLabel },
              { value: 'transfer', label: 'Transfer' },
            ]}
            value={type}
            onChange={setType}
          />
        </div>
        {type === 'plain' && (
          <div className="acct-edit-field">
            <div className="acct-edit-label">Payee</div>
            <AddPayeeField
              value={payee}
              onChange={(v) => setPayee(v)}
              options={payeeOptions}
              accounts={[]}
              fromAccountId={null}
              onSelectTransfer={() => {}}
              onManagePayees={onManagePayees}
              advance={() => {}}
            />
          </div>
        )}
        {type === 'transfer' ? (
          <div className="acct-edit-field">
            <div className="acct-edit-label">{outflow ? 'To account' : 'From account'}</div>
            <Select
              aria-label={outflow ? 'To account' : 'From account'}
              value={transferTo}
              onChange={(e) => setTransferTo(e.target.value)}
              placeholder="Choose an account"
              options={transferAccounts.map((a) => ({ value: a.id, label: a.name }))}
            />
            <div className="imp3-edit-hint">
              {transferAccounts.length === 0
                ? `No other ${d.currency} account to transfer with.`
                : 'Books one transfer between the two accounts — never spending or income.'}
            </div>
          </div>
        ) : (
          <div className="acct-edit-field">
            <div className="acct-edit-label">Category</div>
            <AddCategoryField
              value={category}
              onChange={(k) => setCategory(k)}
              options={catOptionsFor(d.kind === 'income' ? 'income' : 'expense', catList)}
              onCreate={
                onCreateCategory
                  ? (prefill, cb) =>
                      onCreateCategory(prefill, d.kind === 'income' ? 'income' : 'expense', cb)
                  : undefined
              }
              advance={() => {}}
              clearable
            />
          </div>
        )}
      </div>
    </Modal>
  )
}

export interface LinkPickerOption {
  id: string
  /** The whole searchable + rendered string for this candidate entry. */
  line: string
  /** Already consumed by another row's manual link — dimmed and unpickable. Never set on a
   *  server-sent `alternatives` option: the engine only offers entries no other row claimed,
   *  so every one of them is genuinely pickable (RFC 2026-07-29 §D3). */
  taken?: boolean
  /** This is the row's CURRENT partner — marked `aria-selected` in the listbox. */
  selected?: boolean
  /** Appended, quieter, after `line`: why this option is special (current pick, taken). */
  hint?: string
}

/** LinkPicker — ported VERBATIM from the handoff prototype (review3.jsx `LinkPicker`).
 *
 * ONE picker for every "which ledger entry does this row mean" question: `Link to existing`
 * on an unmatched/left-out row, and `Change match` on a row the engine already matched (the
 * handoff's "Link to existing on every row" change — the engine's pick is a suggestion, not
 * the only option). It replaces the two re-derived pickers this file used to carry
 * (`ManualLinkPicker` + the `or match: N other entries ▾` TwinPicker), whose different
 * chrome and wording were exactly the inconsistent-CTA-vocabulary problem the handoff calls
 * out; production semantics — the steal/swap resolution and the `taken` guard — are kept, as
 * option `hint`s and the disabled state.
 *
 * Fixed-position so a section's `overflow: clip` can't clip it; flips upward near the bottom;
 * searchable once the list is long (the prototype's `options.length > 4` gate). Escape /
 * outside-click / scroll close; Escape stops propagation so the Modal stays open. */
function LinkPicker({
  label,
  listLabel,
  options,
  onPick,
  variant,
}: {
  label: string
  listLabel: string
  options: LinkPickerOption[]
  onPick: (id: string) => void
  /** Opt into the unified `imp3-actbtn` bordered-pill CTA chrome (handoff: any row-level
   *  action shares one family, never a bare underlined text link). */
  variant?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [pos, setPos] = useState<Record<string, number | string> | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  // Phone: the list is a docked bottom sheet, not a popover anchored to the row. The popover
  // auto-focused its search (raising the iOS keyboard), and the resulting pan/scroll tripped
  // its own close-on-scroll — it flashed and vanished before the entries could be read.
  const isMobile = useIsMobile()
  const toggle = () => {
    if (!open && ref.current && !isMobile) {
      const r = ref.current.getBoundingClientRect()
      const up = r.bottom > window.innerHeight - 280
      setPos({
        left: Math.max(8, Math.min(r.left, window.innerWidth - 368)),
        ...(up
          ? { top: 'auto', bottom: window.innerHeight - r.top + 4 }
          : { bottom: 'auto', top: r.bottom + 4 }),
      })
    }
    setQ('')
    setOpen((o) => !o)
  }
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
      }
    }
    // The popover is position-FIXED, so any scroll of the page behind it would leave it
    // stranded away from its trigger — hence closing on scroll. One deliberate deviation from
    // the prototype: a scroll INSIDE the popover's own (max-height, overflow-y:auto) list must
    // NOT close it. The prototype's blanket capture-phase listener makes a long list
    // unusable — you cannot scroll to the entry you came for.
    const s = (e: Event) => {
      if (popRef.current && e.target instanceof Node && popRef.current.contains(e.target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', k, true)
    // The phone sheet is not anchored to anything a scroll could strand it from.
    if (!isMobile) window.addEventListener('scroll', s, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', k, true)
      window.removeEventListener('scroll', s, true)
    }
  }, [open, isMobile])
  const ql = q.trim().toLowerCase()
  const shown = ql ? options.filter((o) => o.line.toLowerCase().includes(ql)) : options
  const list = (
    <>
      {options.length > 4 && (
        <input
          autoFocus={!isMobile}
          className="imp3-lpick-search"
          type="text"
          placeholder="Search entries"
          aria-label="Search entries"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onMouseDown={(e) => e.stopPropagation()}
        />
      )}
      {shown.length === 0 ? (
        <div className="imp3-lpick-empty">
          {options.length === 0 ? 'No uncovered entries in this period.' : 'Nothing matches'}
        </div>
      ) : (
        shown.map((o) => (
          <button
            type="button"
            key={o.id}
            role="option"
            aria-selected={!!o.selected}
            disabled={!!o.taken}
            onClick={() => {
              onPick(o.id)
              setOpen(false)
            }}
          >
            {o.line}
            {o.hint && <span className="imp3-lpick-taken"> {o.hint}</span>}
          </button>
        ))
      )}
    </>
  )
  return (
    <div className="imp3-lpick" ref={ref}>
      <button
        type="button"
        className={'imp3-lpick-btn' + (variant ? ' ' + variant : '')}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={toggle}
      >
        <ArrowLeftRight size={13} aria-hidden="true" /> {label}
      </button>
      {open && isMobile && (
        <>
          <div className="imp3-lpick-scrim" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            ref={popRef}
            data-modal-escape-layer
            className="imp3-lpick-sheet"
            role="dialog"
            aria-label={listLabel}
          >
            <div className="imp3-lpick-sheet-head">
              <span className="imp3-lpick-sheet-t">{label}</span>
              <button
                type="button"
                className="imp3-lpick-sheet-x"
                aria-label="Close"
                onClick={() => setOpen(false)}
              >
                <X size={18} strokeWidth={1.6} aria-hidden="true" />
              </button>
            </div>
            <div className="imp3-lpick-sheet-list" role="listbox" aria-label={listLabel}>
              {list}
            </div>
          </div>
        </>
      )}
      {open && !isMobile && pos && (
        <div
          ref={popRef}
          data-modal-escape-layer
          className="imp3-lpick-pop"
          style={{ position: 'fixed', ...pos }}
          role="listbox"
          aria-label={listLabel}
        >
          {list}
        </div>
      )}
    </div>
  )
}

/** LuciusNote — ported from the handoff prototype (review3.jsx `LuciusNote`): the violet
 * transparency panel under the verdict, where Lucius says what it did with this file.
 *
 * The prototype's fixture carried one hand-written `scenario.summary` sentence. Production has
 * no such field and must NOT invent one, so this renders the real things the server told us
 * about THIS read — the format-profile provenance, the flagged-row heads-up, and the
 * multi-currency wallet routing — in the design's own panel. Nothing renders when the server
 * said nothing (no empty violet box). */
function LuciusNote({ preview }: { preview: ImportPreviewResult }) {
  const fp = preview.formatProfile
  // The "already known, nothing changed" case is deliberately NOT a profile fact here — it's
  // the lowest-value case (no news: same format as always) and already redundant with the
  // facts line's own "read from memory" cost tell right above this panel. Rendering a whole
  // bordered box just to repeat that wastes space for zero new information (owner, 2026-07-27).
  // Only NEWS-worthy profile states earn a line: first-time learn, or a changed layout.
  const profile = !fp
    ? null
    : fp.learned
      ? 'Learned this file’s format — next time it imports instantly, no AI needed.'
      : fp.relearned
        ? 'This source changed its layout — re-learned and saved.'
        : null
  const a = preview.anomaly || { intra_batch_duplicate: 0, amount_anomaly: 0 }
  const flagged = [
    a.intra_batch_duplicate ? `${a.intra_batch_duplicate} repeated in the file` : null,
    a.amount_anomaly
      ? `${a.amount_anomaly} unusually large amount${a.amount_anomaly === 1 ? '' : 's'}`
      : null,
  ]
    .filter(Boolean)
    .join(' • ')
  const routing =
    preview.routing && Object.keys(preview.routing).length > 1 ? preview.routing : null
  if (!profile && !flagged && !routing) return null
  return (
    <div className="imp3-lunote">
      <i>✦</i>
      <div className="imp3-lunote-b">
        {profile && <span>{profile}</span>}
        {flagged && (
          <span>
            <b>{flagged}.</b> They’ll still be imported — just marked “needs review” in your
            register so you can check them.
          </span>
        )}
        {routing && (
          <span>
            Multi-currency statement — rows route to the wallet holding each currency:{' '}
            {Object.entries(routing).map(([ccy, r], i) => (
              <span key={ccy}>
                {i > 0 ? ' • ' : ''}
                {ccy} →{' '}
                {r
                  ? r.account_name || r.account_id
                  : 'no account — pick per row or they stay left out'}
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  )
}

const I_FILE =
  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/>'
/** The picked file's size, in the handoff's own `58 KB` / `1.2 MB` shape (`imp3-file-sub`). */
function fSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  return kb < 1024 ? `${Math.round(kb)} KB` : `${(kb / 1024).toFixed(1)} MB`
}
/** The line under "Opening <file>": how long it has been, and — once it is clearly not instant —
 *  how long it can take (owner, 2026-10-26: the old "Opening your review" only repeated the
 *  title above it). */
function OpeningReviewLine() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(id)
  }, [])
  if (seconds < 4) return <>Usually a few seconds</>
  return <>Still opening — {seconds}s. A long statement can take up to 30 seconds.</>
}

export interface ReopenOptions {
  instructions?: string
  rememberInstructions?: boolean
  forceReparse?: boolean
  background?: boolean
}

export interface ImportModalProps {
  open: boolean
  onClose: () => void
  initialFile?: File
  /** Reopen an already-parsed (or still-parsing) statement BY ID — the statements list's
   *  "Review →" action, or opening the modal straight into a background parse's live
   *  progress. Skips source/choose/configure entirely: `parsing` shows the same processing
   *  UI (polled by id) and auto-transitions once `ready`; `ready` reopens straight to the
   *  review step. Never set together with a fresh upload. */
  resumeStatementId?: string
  /** The resumed statement's filename, for the "Lucius is reading <b>{name}</b>" line — the
   *  caller (a statements-list row) already has it; resuming never re-reads the record just
   *  to redisplay its own name. */
  resumeFilename?: string
  /** When the resumed statement was uploaded — its Choose step says "Read 2 Jun". */
  resumeUploadedAt?: string
  /** Server call backing `resumeStatementId`: `POST /api/statements/{id}/preview` — reads
   *  the record's own file, no re-upload. `opts` carries a "Read again": the typed
   *  instructions, and `forceReparse` + `background` to re-read it as an async job. */
  onReopenPreview?: (
    statementId: string,
    accountId?: string,
    opts?: ReopenOptions,
  ) => Promise<ImportPreviewResult>
  /** "Continue in background" (B20 async) — the processing step's own low-emphasis action,
   *  and, during `processing`, the SAME thing the modal's ✕ / Escape / backdrop now do: the
   *  server-side parse keeps running regardless, so there is nothing left to warn about
   *  losing (the old "Stop reading?" confirm never fires for `processing` any more). The
   *  modal closes itself exactly as any other exit does; this callback is purely the
   *  parent's side effect (typically its own toast — "We'll keep reading…"). */
  onContinueInBackground?: (statementId: string | null, filename?: string) => void
  accounts: Account[]
  defaultAccountId?: string
  /** Server category list — used to render category pills and the row category editor. */
  categories?: Category[]
  /** Pre-keyed category map derived from categories — for O(1) label/colour lookup. */
  catMap?: Record<string, Category>
  /** Server decision preview (`/api/statements/preview`). `mapping` (manual route, CSV/XLSX
   *  only) TEACHES the server the user's column mapping — saved as the source's format
   *  profile — before the same decision preview runs. `instructions` (SI-67) is remembered
   *  on the same profile and threaded, fenced, into the two LLM calls — unless `remember`
   *  is false (one-off: this import only, saved instructions untouched). `forceReparse`
   *  busts the cached parse — a fresh AI read, billed again. `forceRelearn` (the "force
   *  reprocess" checkbox) ignores this source's saved format profile entirely — even a
   *  sticky manual-origin one — and makes Lucius re-detect the schema from scratch.
   *  `accountIds` is the FULL chip set (anchor first): the pipeline still routes against the
   *  single `accountId`, but the server remembers the set on the format profile so the next
   *  import of this layout pre-selects the same wallets. */
  onPreview: (
    file: File | null,
    url: string,
    accountId: string,
    mapping?: Record<string, string>,
    instructions?: string,
    remember?: boolean,
    forceReparse?: boolean,
    forceRelearn?: boolean,
    accountIds?: string[],
    /** Client-minted (B20): rides the same request so `GET .../preview-progress/{token}`
     *  can be polled while this call is in flight. Omitted on the quiet single-row
     *  re-preview (`keepRowDecisions`), which shows no processing step to poll for. */
    progressToken?: string,
  ) => Promise<ImportPreviewResult>
  /** Prefill probe (`/api/statements/source-peek`): fingerprint the picked file server-side
   *  (zero AI, no writes) and return what this source taught us — its saved instructions
   *  (SI-67) and `accountIds`, the accounts it was last imported into (anchor first). The
   *  choose step pre-fills its box and its account chips, so saved guidance is visible and
   *  editable instead of invisibly applied and a monthly statement doesn't make the owner
   *  re-pick the same wallets. Optional; failures read as an unknown source. */
  onSourcePeek?: (file: File) => Promise<{
    known: boolean
    instructions: string
    formatLabel?: string | null
    accountIds?: string[]
  }>
  /** Decision-based commit (RFC D7): one atomic server batch against the cached preview —
   *  the ONLY commit path out of this modal, every format. */
  // `deleteIds` = the ledger entries the reviewer marked for deletion in the "In your ledger,
  // not on this statement" panel. They ride the SAME commit as the row decisions (the server
  // validates them against this statement's own uncovered set), so the whole screen has one
  // write moment and the footer's promise holds.
  onDecisionCommit: (
    statementId: string,
    accountId: string,
    decisions: Array<Record<string, unknown>>,
    deleteIds?: string[],
    sourceFile?: File | null,
  ) => Promise<{
    created: number
    merged: number
    duplicates: number
    skipped: number
    batchId?: string | null
    deleted?: number
    deleteErrors?: Array<{ id: string; error: string }>
    refusedDuplicates?: Array<{
      description?: string | null
      date?: string | null
      duplicate_of?: string | null
      reason?: string | null
    }>
  }>
  /** "Mark all as reviewed" (SI-79): clears `needs_review` on every row this commit just
   *  CREATED (never a merged/linked row — those never carry the flag). Offered on the Done
   *  screen only when `result.created > 0` and a batch id came back from the commit. */
  onMarkImportReviewed?: (batchId: string) => Promise<{ updated: number }>
  /** Offered on the Done screen alongside "Mark all as reviewed" whenever a batch id came back
   *  from the commit; the parent closes this modal and opens the register filtered to that
   *  batch — a filter that unions the batch's created rows with the rows it only linked/merged. */
  onViewImportBatch?: (batchId: string, label?: string) => void
  /** Undo import on the Booked screen: removes the rows this commit added and restores every
   *  entry it linked into (POST /api/transactions/batches/{id}/rollback). Omitted, no Undo. */
  onUndoImport?: (batchId: string) => Promise<{ deleted: number; unmerged: number }>
  /** Existing-transaction drawer (SI-50): clicking a match line's name/sub-line opens the
   *  REAL register drawer (TxnDetail) for that row. All optional — the register (AccountsTab)
   *  wires the same endpoints its own drawer uses; omitted, the drawer still opens (fetched via
   *  GET /api/transactions/{id}) but edit/delete/reviewed/manage-payees/create-category/activity
   *  fall back to safe no-ops. */
  payeeOptions?: TxnDetailProps['payeeOptions']
  payeeById?: Record<string, string>
  /** Profile `import_review_default`: the review band's pre-selected action. Absent/'merge'
   *  → merge (factory default, owner 2026-07-17: a probable match pre-selects linking —
   *  reference stamped, labels kept, re-imports L0-dedupe); 'skip' is the explicit opt-out
   *  (decide each row manually). Strong/weak bands are never governed. */
  importReviewDefault?: 'skip' | 'merge'
  baseCurrency?: string
  rates?: FxRates
  onSaveTxn?: (id: string, patch: Record<string, unknown>) => Promise<void>
  onDeleteTxn?: (id: string) => Promise<void>
  onMarkTxnReviewed?: (id: string) => Promise<void>
  onManagePayees?: () => void
  onCreateCategory?: TxnDetailProps['onCreateCategory']
  onLoadTxnActivity?: (id: string) => Promise<TxnActivity>
  /** Same optional-with-fallback contract as onLoadTxnActivity above. */
  onLoadTxnSourceEntries?: (id: string) => Promise<SourceEntry[] | null>
  /** "Unlink source" (CDS-6) from this nested drawer. Omitted, the action fails loudly (the
   *  drawer's own inline error + Retry) rather than pretending success — unlike the read-only
   *  fallbacks above, there is no safe no-op for a write. */
  onDetachTxnSourceRef?: TxnDetailProps['onDetachSourceRef']
}

const FALLBACK_ACTIVITY: TxnActivity = {
  source: 'manual',
  addedBy: 'You',
  createdAt: '',
  updatedAt: '',
  activity: [],
}

type MatchAction = 'skip' | 'merge' | 'new'

// A row the match engine paired with something already in the ledger (a manual entry, an
// earlier import, or a bank-sync line) — carries the existing row's id + summary on the flag.
const matchFlag = (d: ImportPreviewDraft) =>
  (d.flags || []).find(
    (f) =>
      (f.type === 'near_duplicate' ||
        f.type === 'transfer_pair' ||
        f.type === 'transfer_leg_echo') &&
      f.existing_id,
  )
// Structural verdict: the SERVER's band, verbatim (RFC 2026-07-29 §D8 — there is exactly one
// match engine now, and the client re-derives nothing). Absent/unknown band → 'review', never
// 'strong': a missing field must never buy a row the auto-link treatment.
const matchBand = (d: ImportPreviewDraft): 'strong' | 'weak' | 'review' | 'none' => {
  const f = matchFlag(d)
  if (!f) return 'none'
  return f.band === 'strong' ? 'strong' : f.band === 'weak' ? 'weak' : 'review'
}
// What can this row merge into — the ENGINE's flag, or an override/manual link the owner
// asserted. One resolver feeds actionFor/outcomeLine/the commit payload/the pair row.
type ResolvedExisting = { id: string; summary: ExistingSummaryLike }
// Filter chips (owner directive 2026-07-16, supersedes the non-filtering jump-nav): single-
// select, All default. Band keys partition the non-dup statement rows; 'ledger' focuses the
// ledger-only block; a dup (L0) row renders under All only — it is inert everywhere else.
// 'offby' is NOT a band — the TRANSIENT drift filter (owner motivation 2026-07-17): it
// exists only while some linked row keeps a drifted figure, and shows exactly those rows.
// 'weak' is the same CATEGORY of lens: a weak row already belongs to an outcome partition
// (Import, or Will link once linked), so this chip only makes the open possible-matches
// reachable — it never owns a row and never feeds `Needs you` (SIUM-CHIP-WEAK).
type ChipKey =
  | 'all'
  | 'strong'
  | 'needs'
  | 'new'
  | 'skip'
  | 'ledger'
  | 'offby'
  | 'uncovered'
  | 'weak'

export function ImportModal({
  open,
  onClose,
  initialFile,
  resumeStatementId,
  resumeFilename,
  resumeUploadedAt,
  onReopenPreview,
  onContinueInBackground,
  accounts,
  defaultAccountId,
  categories: catList = [],
  catMap: catMapProp = {},
  onPreview,
  onSourcePeek,
  onDecisionCommit,
  onMarkImportReviewed,
  onViewImportBatch,
  onUndoImport,
  payeeOptions,
  payeeById,
  importReviewDefault,
  baseCurrency,
  rates,
  onSaveTxn,
  onDeleteTxn,
  onMarkTxnReviewed,
  onManagePayees,
  onCreateCategory,
  onLoadTxnActivity,
  onLoadTxnSourceEntries,
  onDetachTxnSourceRef,
}: ImportModalProps) {
  const [step, setStep] = useState<
    'source' | 'choose' | 'processing' | 'configure' | 'preview' | 'done'
  >(() => (resumeStatementId ? 'processing' : 'source'))
  // Confirm before discarding a real review-in-progress — a tap outside, ✕, or the Decide
  // step's own Back button used to discard every row decision instantly, no warning. Only
  // asked once real work exists to lose (the Decide step) — nothing has been booked yet
  // regardless, so earlier steps close/step back exactly as before.
  const [pendingExit, setPendingExit] = useState<null | 'close' | 'back'>(null)
  // Bumped once per genuinely fresh preview load (never on an in-place preview mutation, e.g.
  // Undo's amount-fix revert) — the reliable "did a new statement just load" signal a plain
  // `[preview]`/`[preview?.statementId]` effect dependency can't give: object identity changes
  // on ANY setPreview call, and statementId can be legitimately undefined (a client-mapped
  // import never gets one), so neither fires exactly once per real load.
  const [previewGeneration, setPreviewGeneration] = useState(0)
  // The commit round-trip can take a few seconds — the button itself is the only affordance
  // the whole step offers.
  const [committing, setCommitting] = useState(false)
  const [route, setRoute] = useState<'lucius' | 'manual'>('lucius')
  const [file, setFile] = useState<File | null>(null)
  const [, setGrid] = useState<string[][] | null>(null)
  const [url, setUrl] = useState('')
  const [format, setFormat] = useState('csv')
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<string[][]>([])
  const [accountId, setAccountId] = useState(defaultAccountId || '')
  const [amountMode, setAmountMode] = useState<'single' | 'split'>('single')
  const [map, setMap] = useState<ColumnMap>({
    date: '',
    description: '',
    amount: '',
    inflow: '',
    outflow: '',
    category: '',
    status: '',
  })
  // "Re-read from scratch" (the handoff's ONE known-source checkbox, `flow3.jsx`'s `fresh`:
  // "fresh format detection + fresh processing"). It drives BOTH server flags at once:
  //   force_reparse  — re-uploading the SAME file bytes otherwise returns the Statement
  //                    record's CACHED parse (StatementStore.create dedupes by content hash),
  //                    so a fix to the parse/guard logic never surfaces (cheap, zero AI);
  //   force_relearn  — ignore this source's saved format profile entirely, even a sticky
  //                    manual-origin one with a known gap, and re-detect the schema (an LLM
  //                    call). There is otherwise no way to escape a stuck profile.
  // The pre-v3 UI exposed these as two separate checkboxes; the design collapses them into
  // the one control that means "forget everything you know about this source" (PARITY_AND_GAPS).
  const [fresh, setFresh] = useState(false)
  // The accounts this statement covers, beyond the anchor (`accountId`). The server takes ONE
  // `account_id` — an ANCHOR that fans out to a currency→account route — so extra chips act
  // through the per-row destination seam instead: after the preview, a row the server left
  // unrouted is assigned the chip whose currency matches it. See PARITY_AND_GAPS.md.
  const [extraAcctIds, setExtraAcctIds] = useState<string[]>([])
  const [acctPickOpen, setAcctPickOpen] = useState(false)
  // The URL / Google-Sheet source, revealed by the dropzone's own quiet link.
  const [urlOpen, setUrlOpen] = useState(false)
  // SI-67 source peek: whether the server recognised this file's fingerprint — the handoff's
  // "I know this source." banner. FOUR states, not two: a probe that FAILED (or was never
  // wired) is not the same answer as a confirmed-unknown source, and the difference is
  // load-bearing, because "Re-read from scratch" is the only control that sets `force_relearn`
  // — the one escape from a stuck format profile. Hiding it behind a confirmed `known` would
  // let a network hiccup take the escape hatch away with it.
  const [peekState, setPeekState] = useState<'pending' | 'known' | 'unknown' | 'unavailable'>(
    'pending',
  )
  const sourceKnown = peekState === 'known'
  const [savedInstructions, setSavedInstructions] = useState('')
  const [preview, setPreview] = useState<ImportPreviewResult | null>(null)
  const [result, setResult] = useState<{
    created: number
    merged?: number
    duplicates: number
    skipped: number
    accounts: number
    transfers?: number
    batchId?: string | null
    deleted?: number
    leftOut?: number
    unreadable?: number
  } | null>(null)
  // "Mark all as reviewed" (SI-79 Done-screen action) — only ever offered when this commit
  // created new rows (a merge/link never gets the review flag, so there's nothing to clear).
  const [reviewMarkState, setReviewMarkState] = useState<'idle' | 'pending' | 'done'>('idle')
  const [reviewMarkError, setReviewMarkError] = useState('')
  const [dragOver, setDragOver] = useState(false),
    [error, setError] = useState('')
  const [showSkipped, setShowSkipped] = useState(false),
    [includeEarlier, setIncludeEarlier] = useState(false)
  // Payee/category edit modal — the draft ARRAY INDEX (updateDraft's own indexing, not
  // rowIndex) currently open for editing, or null when closed. A pencil trigger in the row's
  // expanded detail opens it; the row's existing name/category text stays plain display (owner:
  // "do not create new fields" — a labeled inline field pair read as clutter bolted onto the row).
  const [editingDraftIndex, setEditingDraftIndex] = useState<number | null>(null)
  // Per-row decision for a draft that matched something already in the ledger: skip (default —
  // don't double-book), merge (fold into the existing row, statement amount wins), or new.
  const [matchAction, setMatchAction] = useState<Record<number, MatchAction>>({})
  const [pairInclude, setPairInclude] = useState<Record<number, boolean>>({})
  // Manual link (owner 2026-07-18): the user asserts THIS statement row IS an existing
  // uncovered ledger entry the dedup engine never proposed — the live ExampleSub case (a booked
  // "TD subscription renewal" −₫250,000 on 06-11 vs the statement's "gotinder…" −₫150,000 on
  // 06-14: same charge, 3 days + ~3.8% apart, below every auto-match gate). Keyed by the
  // draft's server-stable rowIndex, value = the picked entry's id + its summary. resolveExisting
  // checks it LAST (after the engine flag), so an engine match always wins; the row then flows
  // through the existing matched-row machinery as a review-band pair.
  const [manualLink, setManualLink] = useState<
    Record<number, { id: string; summary: ExistingSummaryLike }>
  >({})
  // "Change match" — the ONE override concept (RFC 2026-07-29 §D8: the old `twinPick` folded
  // into this). The engine's pick is a suggestion; picking one of the row's server-sent
  // `alternatives` re-points it. Keyed like manualLink, but resolveExisting checks it FIRST —
  // it must WIN over the engine's own matchFlag, not defer to it. "Back to Lucius's pick"
  // clears it.
  const [matchOverride, setMatchOverride] = useState<
    Record<number, { id: string; summary: ExistingSummaryLike }>
  >({})
  // The verdict panel collapses to one summary row by default; expand for the per-question detail.
  const [verdictOpen, setVerdictOpen] = useState(false)
  const [doneDiff, setDoneDiff] = useState<{ core: number; boundary: number } | null>(null)
  // Ledger-only rows the reviewer MARKED for deletion. Staged, like every other decision on
  // this screen — the footer's "nothing is written until you confirm" is a promise Delete used
  // to break, firing an irreversible DELETE per row before the user had committed anything.
  //
  // This is deliberately NOT the old `checkRemoved` renamed. That flag meant "this entry is
  // gone from the ledger" — a fact, set only after a successful write — and its one writer no
  // longer exists. A mark means "delete this WHEN I confirm", which is a different claim with a
  // different lifetime: the row stays on screen, marked and undoable, and the entry is still
  // real until commit. Conflating "I've dealt with this" with "delete this from my ledger" is
  // exactly the mistake that would make a mis-marked row silently destructive.
  const [pendingDelete, setPendingDelete] = useState<Record<string, boolean>>({})
  // Boundary bucket (§4): collapsed by default — a quiet month stays quiet.
  const [boundaryOpen, setBoundaryOpen] = useState(false)
  const [otherStatementOpen, setOtherStatementOpen] = useState(false)
  // "Link to a new row" on a row that is already linked: confirm before moving its link.
  const [relinkAsk, setRelinkAsk] = useState<{
    row: string
    from: string
    to: string
    run: () => void
  } | null>(null)
  // Targeted jump: the DRAFT ARRAY INDEX to scroll to and flash-highlight. A jump into a row
  // the active chip filters out switches back to All first (same handler), so by the time the
  // flash effect runs post-render the target is in the DOM.
  const [flashIndex, setFlashIndex] = useState<number | null>(null)
  // Per-id reason a staged delete did NOT happen at commit (server `delete_errors`) — shown on
  // the Done screen, never swallowed: "delete 8" must not quietly become "deleted 7".
  const [deleteErrors, setDeleteErrors] = useState<Array<{ id: string; error: string }>>([])
  // Rows the LEDGER refused because they duplicate something already booked (server
  // `refused_duplicates`). Same contract as deleteErrors: the user asked for these and did not
  // get them, so the Done screen names them — a silent shortfall is the failure shape the
  // duplicate write-block was added to prevent, not one it is allowed to introduce.
  const [refusedDuplicates, setRefusedDuplicates] = useState<
    Array<{
      description?: string | null
      date?: string | null
      duplicate_of?: string | null
      reason?: string | null
    }>
  >([])
  const tableRef = useRef<HTMLDivElement>(null)
  // The active filter chip — 'all' shows the whole flat statement list + the ledger-only block.
  const [activeChip, setActiveChip] = useState<ChipKey>('all')
  // A second, independent filter dimension (by kind) — ANDed with the decision chip above,
  // not a replacement for it.
  const [kindFilter, setKindFilter] = useState<'all' | 'expense' | 'income' | 'transfer'>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [expandedRows, setExpandedRows] = useState<Record<number, boolean>>({})
  // Handoff queue receipts are explicit user decisions, separate from a band's safe default:
  // cards stay in place after a choice and can be undone without losing their context.
  const [queueDecided, setQueueDecided] = useState<Record<number, boolean>>({})
  // The handoff's read-check card can correct a parsed amount in place. This is UI state
  // only; applying it goes through updateDraft, so the existing touched-field decision seam
  // remains the sole way the corrected amount reaches the server.
  const [queueFixingIndex, setQueueFixingIndex] = useState<number | null>(null)
  const [queueFixValue, setQueueFixValue] = useState('')
  const [queueAmountCorrected, setQueueAmountCorrected] = useState<Record<number, boolean>>({})
  const [queueFixOriginal, setQueueFixOriginal] = useState<
    Record<number, { amount: number; touched: boolean }>
  >({})
  const [ledgerExpanded, setLedgerExpanded] = useState<Record<string, boolean>>({})
  // "Leave it" is a decision too: the entry says it stays (owner, 2026-10-26: only "Will delete"
  // showed, so a row you'd answered looked untouched). Nothing is sent — keeping is the default.
  const [keptLedger, setKeptLedger] = useState<Record<string, boolean>>({})
  const [sectionOpen, setSectionOpen] = useState({
    needs: true,
    statement: true,
    ledger: true,
    uncovered: true,
  })
  // Unmatched rows used to have no per-row Leave-out control. Keep that choice at the same
  // decision seam as every other row so confirmImport still emits the real server `skip`.
  const [plainSkip, setPlainSkip] = useState<Record<number, boolean>>({})
  // Needs-your-eyes rows (low confidence / guard note): offered but excluded until included.
  const [eyesInclude, setEyesInclude] = useState<Record<number, boolean>>({})
  // SI-68: a row the Lucius pass marked `skip` (per the owner's instructions) is offered but
  // excluded until included — same shape as eyesInclude, independent toggle.
  const [luciusSkipInclude, setLuciusSkipInclude] = useState<Record<number, boolean>>({})
  const [luciusUndoing, setLuciusUndoing] = useState<Record<number, boolean>>({})
  // Row fields the USER touched in review — only these ride as decision `edits`.
  const [touched, setTouched] = useState<
    Record<number, { category?: boolean; amount?: boolean; account?: boolean; payee?: boolean }>
  >({})
  const [phase, setPhase] = useState(0)
  // B20 live progress: minted fresh per real preview (never for the quiet single-row
  // re-preview, which shows no processing step). `usePreviewProgress` is inert while this is
  // null, and while `step` isn't 'processing' — an old server that never answers the
  // progress endpoint just leaves `previewProgress` null forever, so the render below falls
  // back to the `phase`/READ_PHASES timer above exactly as it always has. Named distinctly
  // from the header's own `progress` breadcrumb element further down this component.
  const [progressToken, setProgressToken] = useState<string | null>(null)
  const [bgPending, setBgPending] = useState(false)
  // The statement file beside the review (owner, 2026-10-26: "a preview … where i can scroll or
  // review the files … before making decisions"). Off until asked for, every time ("it's not
  // always needed"); the width it is dragged to is remembered. A phone flips with tabs instead.
  const isMobile = useIsMobile()
  const [showFile, setShowFile] = useState(false)
  const [fileWidth, setFileWidth] = useState<number | null>(() => {
    try {
      const px = Number(localStorage.getItem(FILE_WIDTH_KEY))
      return px >= FILE_MIN_PX ? px : null
    } catch {
      return null
    }
  })
  const [phoneView, setPhoneView] = useState<'review' | 'file'>('review')
  const [fileItem, setFileItem] = useState<{ id: string; item: VaultItem | null } | null>(null)
  const [fileRow, setFileRow] = useState<number | undefined>(undefined)
  const {
    progress: previewProgress,
    stale: progressStale,
    elapsedS,
  } = usePreviewProgress(progressToken, step === 'processing')
  // SI-67 upload-time instructions: typed on the choose step, remembered per source when
  // the checkbox stays on (unchecked = one-off, this import only); the review meta line
  // re-opens them for edit (saved via PUT — applies to the NEXT parse).
  const [instructions, setInstructions] = useState('')
  // Auto-grow the Tell Lucius textarea to fit its content (a long saved instruction, prefilled
  // from a known source, used to sit cramped in the fixed 3-row box with its own inner
  // scrollbar — owner, 2026-07-27). Capped by the CSS max-height; grows on every value change,
  // whether typed or prefilled.
  const instrTextareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = instrTextareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [instructions])
  const [rememberInstr, setRememberInstr] = useState(true)
  const [instrSaving, setInstrSaving] = useState(false)
  const [instrErr, setInstrErr] = useState('')
  // The handoff's "teach" affordance: the FIRST time the user overrides one of Lucius's calls,
  // offer to remember why for this source. `idle` → `offer` (an override happened) → `editing`
  // → `saved`; `done` = declined, never asked again this import. `taught` survives the commit
  // so the Booked screen can honestly say the instruction was saved.
  const [teach, setTeach] = useState<'idle' | 'offer' | 'editing' | 'done' | 'saved'>('idle')
  const [teachText, setTeachText] = useState('')
  const [taught, setTaught] = useState(false)
  // SI-67 prefill: the box currently shows the source's SAVED instructions (dim hint on) —
  // cleared the moment the user types. peekSeq drops a slow probe reply that outlived its
  // file pick; instrTyped keeps a slow reply from clobbering text typed meanwhile.
  const [instrPrefilled, setInstrPrefilled] = useState(false)
  const peekSeq = useRef(0)
  const instrTyped = useRef(false)
  // Existing-transaction drawer (SI-50): the match line's "existing transaction" may not be on
  // the currently-loaded register page, so it's fetched on demand — keyed by id like the
  // ledger-check panel's removing/removeFailed below.
  const [drawerTxn, setDrawerTxn] = useState<Txn | null>(null)
  const [drawerLoading, setDrawerLoading] = useState<Record<string, boolean>>({})
  const [drawerFailed, setDrawerFailed] = useState<Record<string, boolean>>({})
  const fileRef = useRef<HTMLInputElement>(null)
  const acctPickRef = useRef<HTMLSpanElement>(null)
  const account = useMemo(
    () => accounts.find((a) => a.id === accountId) || accounts[0],
    [accounts, accountId],
  )
  const fileSize = file ? fSize(file.size) : ''
  const colOpts = useMemo(
    () => [{ value: '', label: '— none —' }, ...columns.map((c) => ({ value: c, label: c }))],
    [columns],
  )
  const acctOpts = useMemo(
    () =>
      accounts.map((a) => ({
        value: a.id,
        label: a.name + ' (' + a.currency + ')',
      })),
    [accounts],
  )
  // SI-66 teach popover option lists: transfer targets = same-currency accounts minus the
  // row's own (routing requires same currency, never self); categories = the real ones.
  const checkRowById = useMemo(() => {
    const m = new Map<string, LedgerCheckRow>()
    ;(preview?.ledgerCheck?.rows || []).forEach((r) => m.set(r.id, r))
    // Boundary entries are ALSO valid manual-link targets — the commit guard accepts any
    // uncovered id (the "not on this statement" set = rows + boundary).
    ;(preview?.ledgerCheck?.boundary || []).forEach((r) => {
      if (!m.has(r.id)) m.set(r.id, r)
    })
    return m
  }, [preview])
  useEffect(() => {
    if (step !== 'processing') return
    setPhase(0)
    const id = setInterval(() => setPhase((p) => Math.min(p + 1, READ_PHASES.length - 1)), 520)
    return () => clearInterval(id)
  }, [step])
  // The account picker closes on a click outside itself — the prototype's own handler
  // (`flow3.jsx` DropStep), mousedown so it beats the option button's own click.
  useEffect(() => {
    if (!acctPickOpen) return
    const h = (e: MouseEvent) => {
      if (
        acctPickRef.current &&
        e.target instanceof Node &&
        !acctPickRef.current.contains(e.target)
      )
        setAcctPickOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [acctPickOpen])
  // Targeted jump (§ jumpToStatementRow): the effect runs after the render that applied any
  // chip switch, so the target node is queryable — scroll it into view and flash it.
  useEffect(() => {
    if (flashIndex == null) return
    const el = tableRef.current?.querySelector(`[data-draft-index="${flashIndex}"]`)
    if (!el) {
      setFlashIndex(null)
      return
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('imp-row-flash')
    const t = setTimeout(() => {
      el.classList.remove('imp-row-flash')
      setFlashIndex(null)
    }, 2000)
    return () => clearTimeout(t)
  }, [flashIndex])
  function reset() {
    // A fresh start ("Import another statement", "Try another file") leaves the reopened
    // statement behind for good — otherwise the next file is read while the resume path also
    // reopens the old one, and the two statements' reviews race (QA 2026-10-25).
    setDroppedResumeId(resumeStatementId ?? null)
    setPendingAttachId(null)
    setReopenFailed(null)
    setReopenAccount('')
    setRereading(false)
    reopenArgs.current = {}
    setStep('source')
    setRoute('lucius')
    setFile(null)
    setGrid(null)
    setUrl('')
    setFormat('csv')
    setColumns([])
    setRows([])
    setAmountMode('single')
    setMap({
      date: '',
      description: '',
      amount: '',
      inflow: '',
      outflow: '',
      category: '',
      status: '',
    })
    setPreview(null)
    setResult(null)
    setError('')
    setShowSkipped(false)
    setIncludeEarlier(false)
    setMatchAction({})
    setEyesInclude({})
    setPairInclude({})
    setTouched({})
    setManualLink({})
    setMatchOverride({})
    setAccountId(defaultAccountId || '')
    setPendingDelete({})
    setDeleteErrors([])
    setExtraAcctIds([])
    setAcctPickOpen(false)
    setFresh(false)
    setUrlOpen(false)
    setPeekState('pending')
    setSavedInstructions('')
    setBoundaryOpen(false)
    setActiveChip('all')
    setInstructions('')
    setRememberInstr(true)
    setInstrSaving(false)
    setInstrErr('')
    setTeach('idle')
    setTeachText('')
    setTaught(false)
    setInstrPrefilled(false)
    instrTyped.current = false
    peekSeq.current++
    setLuciusSkipInclude({})
    setLuciusUndoing({})
    setExpandedRows({})
    setQueueDecided({})
    setQueueFixingIndex(null)
    setQueueFixValue('')
    setQueueAmountCorrected({})
    setQueueFixOriginal({})
    setLedgerExpanded({})
    setPlainSkip({})
    setSectionOpen({ needs: true, statement: true, ledger: true, uncovered: true })
    setReviewMarkState('idle')
    setReviewMarkError('')
  }
  function close() {
    reset()
    onClose()
  }
  // "Continue in background" (B20 async) — the processing step's own action AND, since the
  // read is a server-side thread that outlives the connection regardless (sync or async), the
  // SAME thing the modal's ✕ / Escape / backdrop now do during `processing` (see requestClose
  // below): there is nothing left to warn about losing, so this never confirms. The id
  // resolves from whichever source knows it: a resumed statement already carries it; a fresh
  // upload only learns it once the server mints one, riding the live progress snapshot.
  function continueInBackground() {
    const sid = liveResumeId || previewProgress?.statementId || null
    // Tapped before the server has minted the statement's id (the first second of an upload):
    // hand off only once it is known, or the app could never say when THIS read is ready
    // (QA 2026-10-25 — the read went untracked and no "Ready to review" ever came).
    if (!sid && progressToken) {
      setBgPending(true)
      return
    }
    handOffToBackground(sid)
  }
  function handOffToBackground(sid: string | null) {
    setBgPending(false)
    // The reopened statement's name only while it is still the one on screen — after "Import
    // another" the read in flight is the newly picked file's.
    onContinueInBackground?.(sid, (liveResumeId ? resumeFilename : undefined) || file?.name)
    close()
  }
  // The Modal's ✕ / backdrop / Escape all funnel through this ONE onClose (Radix's
  // onOpenChange) — intercepting it here covers "tap outside" and "cancel" both, in one place.
  // Waiting for the id (see continueInBackground): hand off the moment it lands; a read that
  // finishes first just stays open on its review; after 6s give up and close untracked.
  const liveStatementId = previewProgress?.statementId || null
  useEffect(() => {
    if (!bgPending) return
    if (step !== 'processing') {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the read finished first; drop the wait
      setBgPending(false)
      return
    }
    if (liveStatementId) {
      handOffToBackground(liveStatementId)
      return
    }
    const timer = window.setTimeout(() => handOffToBackground(null), 6000)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the id/step only
  }, [bgPending, liveStatementId, step])
  function requestClose() {
    if (step === 'processing') {
      continueInBackground()
      return
    }
    if (step === 'preview' && !committing) {
      setPendingExit('close')
      return
    }
    close()
  }
  function requestBack() {
    // A reopened statement's Choose step offers "Back to review", which restores this review
    // exactly as left — nothing is lost, so there is nothing to confirm.
    if (resuming && !file) {
      setStep('choose')
      return
    }
    setPendingExit('back')
  }
  function confirmExit() {
    const action = pendingExit
    setPendingExit(null)
    if (action === 'close') close()
    else if (action === 'back') setStep(route === 'manual' ? 'configure' : 'choose')
  }

  /** Everything that belongs to the PREVIOUS source, cleared the moment a new one is chosen —
   *  by EITHER route. Both entry points must run this: `fresh` in particular is a paid flag
   *  (`force_relearn` re-detects the schema via an LLM call), and it is only ever visible
   *  while its own checkbox renders. Leaving it set while switching to a source whose
   *  checkbox is not on screen would fire a billed re-detection the user can neither see nor
   *  cancel. Clearing the other source's handle is the same invariant on the wire: the server
   *  refuses a request carrying both a file and a source_url (422). */
  function resetPickedSource() {
    setError('')
    setColumns([])
    setRows([])
    setGrid(null)
    setMap({
      date: '',
      description: '',
      amount: '',
      inflow: '',
      outflow: '',
      category: '',
      status: '',
    })
    // A lingering prefill belongs to the PREVIOUS source — clear it (typed text stays sticky).
    if (instrPrefilled) setInstructions('')
    setInstrPrefilled(false)
    instrTyped.current = false
    setSavedInstructions('')
    setFresh(false)
  }

  async function pickFile(f: File) {
    resetPickedSource()
    const fmt = detectFormat(f.name)
    setFile(f)
    setUrl('')
    setUrlOpen(false)
    setFormat(fmt)
    // What the SERVER said about the previous file is stale; the probe re-answers below.
    setPeekState('pending')
    const seq = ++peekSeq.current
    try {
      if (fmt === 'csv') {
        const text = await f.text()
        const g = parseCSVGrid(text)
        setGrid(g)
        setupManual(g)
      } else if (fmt === 'xlsx') {
        const g = await parseXlsxGrid(f)
        setGrid(g)
        setupManual(g)
      }
    } catch {
      /* manual mapping just won't prefill */
    }
    setStep('choose')
    void peekSavedInstructions(f, seq)
  }

  const consumedNativeFile = useRef<File | null>(null)
  useEffect(() => {
    if (!open || !initialFile || consumedNativeFile.current === initialFile) return
    consumedNativeFile.current = initialFile
    void pickFile(initialFile)
    // A native choice is consumed once, including React StrictMode's effect replay.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile])

  /** SI-67 prefill: ask the server (fingerprint-only — zero AI, no writes) whether this
   * source is known and pre-fill its saved instructions, so remembered guidance is visible
   * and editable instead of invisibly applied. Best-effort: a failure, a stale reply, or
   * text typed meanwhile leaves the box alone. `known` also drives the handoff's "I know
   * this source." banner, which is true whether or not any instructions were saved. */
  async function peekSavedInstructions(f: File, seq: number) {
    if (!onSourcePeek) {
      setPeekState('unavailable')
      return
    }
    try {
      const p = await onSourcePeek(f)
      if (seq !== peekSeq.current) return
      setPeekState(p.known ? 'known' : 'unknown')
      setSavedInstructions(p.instructions || '')
      prefillAccounts(p.accountIds || [])
      if (instrTyped.current || !p.instructions) return
      setInstructions(p.instructions)
      setInstrPrefilled(true)
    } catch {
      // Probe only — never blocks the choose step. But a FAILED probe means we do not know
      // whether this source is remembered, so the re-read escape hatch stays reachable.
      if (seq === peekSeq.current) setPeekState('unavailable')
    }
  }

  function setupManual(g: string[][]) {
    if (!g || !g.length) return
    const hr = Math.max(0, findHeaderRow(g))
    const cols = (g[hr] || []).map((c, i) =>
      (String(c).trim() || 'Column ' + (i + 1)).replace(/\s+/g, ' '),
    )
    setColumns(cols)
    setRows(g.slice(hr + 1))
    const m = computeMap(cols)
    if (m.inflow || m.outflow) {
      setAmountMode('split')
      setMap({
        date: m.date || '',
        description: m.description || '',
        amount: '',
        inflow: m.inflow || '',
        outflow: m.outflow || '',
        category: m.category || '',
        status: m.status || '',
      })
    } else {
      setAmountMode('single')
      setMap({
        date: m.date || '',
        description: m.description || '',
        amount: m.amount || '',
        inflow: '',
        outflow: '',
        category: m.category || '',
        status: m.status || '',
      })
    }
  }

  function loadUrl() {
    const u = url.trim()
    if (!u) return
    resetPickedSource()
    const fmt = detectFormat(u.split('?')[0] ?? '') || 'csv'
    setFile(null)
    setFormat(u.includes('output=csv') || u.includes('docs.google') ? 'csv' : fmt)
    // The source peek fingerprints FILE BYTES (`onSourcePeek` takes a File; there is no URL
    // probe), so for a hosted source we genuinely cannot tell whether its layout is
    // remembered. That is `unavailable`, not `unknown` — the difference matters, because
    // `unavailable` keeps "Re-read from scratch" on screen. It is the only control that sets
    // `force_relearn`, and a URL source can be stuck on a bad saved format profile exactly
    // like an uploaded one. Claiming "I know this source" would assert something no probe
    // ever said; claiming "unknown" would silently remove the escape hatch.
    setPeekState('unavailable')
    peekSeq.current++ // drop any in-flight probe from the file this URL replaces
    setStep('choose')
  }

  /** The extra "Into accounts" chips, made real (the design: "a statement can cover several —
   * each row routes to the right one"). The server's own currency→account routing runs first
   * and always wins; this only fills rows it left with NO destination, and only from a chip
   * whose currency matches the row's own — the commit hard-fails a cross-currency pick, and
   * the native balance fold is currency-blind, so matching is not optional. Each fill is
   * marked `touched.account` so it rides the existing per-row `edits.account_id` seam.
   *
   * A leg of an intra-statement FX PAIR is left ALONE, even when it has no destination. Two
   * independent reasons, either one sufficient:
   *   1. The commit books a pair as ONE two-leg transfer via `decisions._transfer_draft`,
   *      which reads BOTH accounts off `ctx.route` and never looks at `edits.account_id` —
   *      so a fill here would promise a destination the write silently ignores.
   *   2. Filling it flips the row's FATE. `isUnrouted` goes false the moment
   *      `touched.account && destId` holds, so a leg that would have committed as an explicit
   *      `skip` instead emits `action: "create"` — turning a deliberately-left-out row into a
   *      booked one. Not routing it keeps the safe skip.
   * Routing a pair properly is backend work (`_transfer_draft` would have to honour the edit),
   * not something a port invents. */
  function routeUnroutedDrafts(res: ImportPreviewResult, anchorId: string) {
    const chips = (anchorId ? [anchorId, ...extraAcctIds] : extraAcctIds).filter(
      (id) => id && id !== anchorId,
    )
    if (!chips.length) return
    const byCurrency = new Map<string, string>()
    chips.forEach((id) => {
      const a = accounts.find((x) => x.id === id)
      const ccy = (a?.currency || '').toUpperCase()
      if (a && ccy && !byCurrency.has(ccy)) byCurrency.set(ccy, a.id)
    })
    const filled: Record<number, { account?: boolean }> = {}
    res.drafts.forEach((d, i) => {
      if (d.destId || d.pair) return
      const target = byCurrency.get((d.currency || '').toUpperCase())
      const a = target ? accounts.find((x) => x.id === target) : undefined
      if (!a) return
      d.destId = a.id
      d.destName = a.name
      d.preRecon = !!(a.lastReconciled && d.date < a.lastReconciled)
      filled[d.rowIndex ?? i] = { account: true }
    })
    if (Object.keys(filled).length) setTouched((t) => ({ ...t, ...filled }))
  }

  async function runServerPreview(
    routeKind: 'lucius' | 'manual',
    overrideAccountId?: string,
    mapping?: Record<string, string>,
    forceReparse?: boolean,
    forceRelearnFlag?: boolean,
    keepRowDecisions?: boolean,
  ) {
    const acct = overrideAccountId || accountId
    // Through setAnchorAccount, not setAccountId: this is the THIRD writer of the anchor
    // (the "Preview into {name}" suggestion), and if the suggested account is already an
    // extra chip, a raw write would leave a stale copy of the anchor in the extras.
    if (overrideAccountId) setAnchorAccount(overrideAccountId)
    setRoute(routeKind)
    setError('')
    // `keepRowDecisions` (undoLucius's own call) is a quiet, free, single-row correction
    // against the already-cached parse — the full "Lucius is reading…" step would imply a
    // fresh (possibly billed) re-read, when nothing but one row's routing changed. The row's
    // own "undo" button already shows its own busy state meanwhile.
    // A fresh token per real preview — never for the quiet single-row re-preview, which shows
    // no processing step and so has nothing to poll for.
    const token = keepRowDecisions ? null : newImportProgressToken()
    setProgressToken(token)
    if (!keepRowDecisions) setStep('processing')
    try {
      // Anchor first, then the extras — the server stores the ORDER and restores [0] as the
      // anchor on the next import of this layout. `acct` (not `accountId`) leads: an
      // override picked on this very call is the anchor the user just chose.
      const chips = [acct, ...extraAcctIds.filter((id) => id !== acct)].filter(Boolean)
      const res = await onPreview(
        file,
        url,
        acct,
        mapping,
        instructions.trim() || undefined,
        rememberInstr,
        forceReparse,
        forceRelearnFlag,
        chips,
        token || undefined,
      )
      if (res.attached && res.statementId) {
        // Code review round B, M4: this exact file is already `parsing` on the server's own
        // async pool (someone else's upload, or a retry of this one) — the sync request
        // ATTACHED rather than starting a second parse. `setStep('processing')` already ran
        // above; switching the resume target is all that's needed — the resume effects (keyed
        // off `activeResumeId`) take it from here exactly as a `resumeStatementId` prop would.
        setPendingAttachId(res.statementId)
        return
      }
      if (res.needsAccount) {
        // Every call site requires an explicit `accountId` before it fires (the Choose step's
        // CTA, the Configure step's mapping shortcuts), so this is not the normal path \u2014 only
        // reachable if the chosen account stopped resolving between selection and this call
        // (e.g. deleted concurrently). Surface it plainly rather than silently proceeding.
        setError(
          'Couldn\u2019t preview this statement for the selected account \u2014 choose a different one and try again (the file is already read, no extra AI cost).',
        )
        setStep('choose')
        return
      }
      // Every per-row decision map holds SUBSTANTIVE state keyed by the stable `rowIndex` —
      // it survives a re-preview correctly UNLESS this function itself throws it away.
      // `keepRowDecisions` is set ONLY by undoLucius's own single-row re-preview, where every
      // OTHER row's decision must survive intact. A genuinely fresh read (a new file, or the
      // same file re-mapped/re-read from Choose/Back) has no prior decisions worth keeping —
      // ALL of them clear, not just some, or a re-read of the same file silently replays the
      // last session's edits instead of starting clean. Cleared BEFORE routeUnroutedDrafts
      // (not after): that call makes its OWN legitimate `touched` writes for this fresh
      // preview's auto-routed rows, which a later clear would wipe right back out.
      if (!keepRowDecisions) {
        setQueueAmountCorrected({})
        setQueueFixOriginal({})
        setPlainSkip({})
        setMatchAction({})
        setPairInclude({})
        setManualLink({})
        setMatchOverride({})
        setEyesInclude({})
        setLuciusSkipInclude({})
        setTouched({})
        setPendingDelete({})
      }
      routeUnroutedDrafts(res, acct)
      setExpandedRows({})
      setQueueDecided({})
      setQueueFixingIndex(null)
      setQueueFixValue('')
      setLedgerExpanded({})
      setPreviewGeneration((g) => g + 1)
      setSectionOpen({ needs: true, statement: true, ledger: true, uncovered: true })
      setPreview(res)
      setShowSkipped(false)
      setIncludeEarlier(false)
      setStep('preview')
    } catch (e) {
      // Surface the server's reason (e.g. "AI reader unavailable — add a provider") instead of
      // a generic line; the body is a {detail} envelope, fall back to the raw text / generic.
      let msg = ''
      if (e instanceof Error && e.message) {
        try {
          msg = (JSON.parse(e.message)?.detail as string) || ''
        } catch {
          msg = e.message
        }
      }
      setError(msg || 'Could not read the file — try again or map columns yourself.')
      setStep(routeKind === 'manual' ? 'configure' : 'choose')
    }
  }

  function runLucius(overrideAccountId?: string) {
    void runServerPreview('lucius', overrideAccountId, undefined, fresh, fresh)
  }

  // ── B20 async: reopen a statement BY ID (resumeStatementId) ─────────────────────
  // A smaller, separate tail from runServerPreview's — a reopen never has a mapping/teach
  // flow, a "needs an account" retry loop, or force-reparse/relearn checkboxes to thread
  // through, so duplicating the handful of state resets here is safer than folding a second
  // caller shape into that already-delicate function.
  async function applyReopenedPreview(res: ImportPreviewResult, acct: string) {
    routeUnroutedDrafts(res, acct)
    setQueueAmountCorrected({})
    setQueueFixOriginal({})
    setPlainSkip({})
    setMatchAction({})
    setPairInclude({})
    setManualLink({})
    setMatchOverride({})
    setEyesInclude({})
    setLuciusSkipInclude({})
    setTouched({})
    setPendingDelete({})
    setExpandedRows({})
    setQueueDecided({})
    setQueueFixingIndex(null)
    setQueueFixValue('')
    setLedgerExpanded({})
    setPreviewGeneration((g) => g + 1)
    setSectionOpen({ needs: true, statement: true, ledger: true, uncovered: true })
    setPreview(res)
    setShowSkipped(false)
    setIncludeEarlier(false)
    // A reopened statement has no file to probe for its source's saved instructions — the
    // review itself says which ones it was read with, so Back shows (and Read again re-sends)
    // exactly those.
    if (res.instructions?.text && !instrTyped.current) {
      setInstructions(res.instructions.text)
      setRememberInstr(res.instructions.remembered !== false)
      setSavedInstructions(res.instructions.remembered === false ? '' : res.instructions.text)
    }
    setStep('preview')
  }

  // The reopen is one heavy server call, and two effects below both land on it for the same
  // `ready` statement (the one-shot status check and the polled snapshot) — a second POST for
  // an id already in flight only doubles the work the first is waiting on.
  const reopeningId = useRef<string | null>(null)
  // A reopen that fails stays ON the reopen — its own message, a retry, and an account picker
  // only when the server genuinely could not tell. It used to drop to the Choose step with no
  // file in it: a nameless "CSV" chip and a "Checking if this source is remembered…" probe that
  // could never finish (owner, 2026-10-25).
  const [reopenFailed, setReopenFailed] = useState<{ kind: 'account' | 'load' | 'read' } | null>(
    null,
  )
  const [reopenAccount, setReopenAccount] = useState('')
  const [resumePollEpoch, setResumePollEpoch] = useState(0)
  // "Read again" on a reopened statement: the instructions it was re-read with, so the review
  // fetched once it is ready is the one built for them (the stored review is keyed by them).
  const reopenArgs = useRef<ReopenOptions>({})
  const [rereading, setRereading] = useState(false)
  async function resumeReopen(statementId: string, account?: string) {
    if (!onReopenPreview || reopeningId.current === statementId) return
    reopeningId.current = statementId
    setReopenFailed(null)
    try {
      const chosen = account || accountId || undefined
      const opts = reopenArgs.current
      const res = await (Object.keys(opts).length
        ? onReopenPreview(statementId, chosen, opts)
        : onReopenPreview(statementId, chosen))
      // The server attached this call to a read still running: watch it again until it ends.
      if (res.attached) {
        setResumePollEpoch((e) => e + 1)
        return
      }
      if (res.needsAccount) {
        setReopenFailed({ kind: 'account' })
        return
      }
      const acct = res.resolvedAccountId || chosen || accountId
      if (res.resolvedAccountId || chosen) setAnchorAccount((res.resolvedAccountId || chosen)!)
      await applyReopenedPreview(res, acct)
      setRereading(false)
    } catch {
      setReopenFailed({ kind: 'load' })
    } finally {
      reopeningId.current = null
    }
  }

  /** "Read again" from a reopened statement's Choose step: re-read the saved file as a
   *  background job with what is on screen now, then open the review it builds. */
  async function readAgain() {
    if (!activeResumeId || !onReopenPreview) return
    reopenArgs.current = {
      instructions: instructions.trim() || undefined,
      rememberInstructions: rememberInstr,
    }
    setError('')
    setReopenFailed(null)
    setRereading(true)
    try {
      await onReopenPreview(activeResumeId, accountId || undefined, {
        ...reopenArgs.current,
        forceReparse: true,
        background: true,
      })
    } catch {
      setRereading(false)
      setReopenFailed({ kind: 'load' })
      setStep('processing')
      return
    }
    // Only once the server has the new read: switching first let the status poll see the OLD
    // "ready" and reopen the review this read is replacing (QA 2026-10-25).
    setStep('processing')
  }

  // A resume target has TWO writers: the `resumeStatementId` prop (opened straight from the
  // statements list) and `pendingAttachId` (code review round B, M4 — a plain SYNC upload
  // whose async job was already `parsing` server-side attaches to it rather than starting a
  // second one; see `runServerPreview`'s `res.attached` check below). Same downstream
  // handling either way — once attached, there is no "whose upload was it" left to track.
  const [pendingAttachId, setPendingAttachId] = useState<string | null>(null)
  const [droppedResumeId, setDroppedResumeId] = useState<string | null>(null)
  const liveResumeId =
    resumeStatementId && resumeStatementId !== droppedResumeId ? resumeStatementId : null
  const activeResumeId = liveResumeId || pendingAttachId
  const resuming = !!activeResumeId
  // Polled by the STATEMENT's own id, never a client token — this is what lets reopening
  // survive a page reload the live token-based channel never could. 1s cadence matches the
  // live sync experience; the statements list itself polls its own rows more gently (4s).
  const {
    snapshot: resumeSnapshot,
    stale: resumeStale,
    elapsedS: resumeElapsedS,
  } = useStatementParseStatus(
    resuming ? (activeResumeId as string) : null,
    resuming && step === 'processing' && !reopenFailed,
    1000,
    resumePollEpoch,
  )

  // Jump to `processing` the moment a resume target appears — react.dev's own "adjusting
  // state when a prop changes without an Effect" pattern (see usePreviewProgress's identical
  // `trackedKey` use), not a setState-in-effect: this must land in THIS render, before the
  // effect below's async work even starts, or the dropzone flashes for a frame first.
  const [resumeKey, setResumeKey] = useState('')
  const rKey = `${open}:${activeResumeId ?? ''}`
  if (resuming && rKey !== resumeKey) {
    setResumeKey(rKey)
    setStep('processing')
  }

  useEffect(() => {
    if (!open || !activeResumeId) return
    let cancelled = false
    ;(async () => {
      const status = await fetchParseStatus(activeResumeId)
      if (cancelled) return
      if (status?.parseStatus === 'ready') await resumeReopen(activeResumeId)
      else if (status && status.parseStatus !== 'parsing') {
        setReopenFailed({ kind: 'read' })
      }
      // 'parsing', or a poll miss: stay on `processing` — the effect below drives the
      // eventual auto-transition once the ongoing poll reports `ready`.
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot per open/id, not a sync
  }, [open, activeResumeId])

  useEffect(() => {
    if (!resuming || step !== 'processing') return
    if (resumeSnapshot?.parseStatus === 'ready') {
      // resumeReopen is async; every setState it makes happens after an `await`, in a later
      // microtask, never synchronously inside this effect body — the rule's static check
      // can't see through the call to know that.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void resumeReopen(activeResumeId as string)
    } else if (
      resumeSnapshot?.parseStatus === 'failed' ||
      resumeSnapshot?.parseStatus === 'interrupted'
    ) {
      setReopenFailed({ kind: 'read' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the polled status only
  }, [resumeSnapshot?.parseStatus, resuming, step])

  // ── "Into accounts" chips ───────────────────────────────────────────────────────
  // `accountId` stays the ANCHOR the server receives; extras are additional wallets the
  // statement covers. Removing the anchor promotes the next chip, so the list never has a
  // hole and the invariant "extras only exist alongside an anchor" always holds.
  // The dedupe is not belt-and-braces: `accountId` has a SECOND writer — the manual-mapping
  // `configure` step's own account select, which predates the chips and knows nothing about
  // them. Picking there an account that is already an extra chip, then going Back, would
  // otherwise render the same id twice (duplicate React keys) and make its ✕ un-removable,
  // since both copies resolve to the anchor branch of removeAccount.
  const acctIds = useMemo(
    () => (accountId ? [accountId, ...extraAcctIds.filter((id) => id !== accountId)] : []),
    [accountId, extraAcctIds],
  )
  function addAccount(id: string) {
    setAcctPickOpen(false)
    if (!accountId) {
      setAccountId(id)
      return
    }
    if (id === accountId || extraAcctIds.includes(id)) return
    setExtraAcctIds((list) => [...list, id])
  }
  function removeAccount(id: string) {
    if (id !== accountId) {
      setExtraAcctIds((list) => list.filter((x) => x !== id))
      return
    }
    const [next, ...rest] = extraAcctIds
    setAccountId(next || '')
    setExtraAcctIds(rest)
  }
  /** Restore the chips this source was last imported into (source-peek → the format
   *  profile's remembered set, anchor first).
   *
   *  ADDITIVE, never destructive. `accountId` may already hold `defaultAccountId` — the
   *  account whose page the import was opened from — and that live context is a stronger
   *  signal than last month's memory, so it KEEPS the anchor and the remembered ids join as
   *  extras. Only when nothing is picked yet does the remembered anchor become the anchor.
   *  Ids are filtered against the live account list: an account deleted or renamed away
   *  since the last import silently drops out instead of rendering a dead chip. */
  function prefillAccounts(remembered: string[]) {
    const live = remembered.filter((id) => accounts.some((a) => a.id === id))
    if (!live.length) return
    // The anchor is computed here, not inside the updater, so the extras can be kept free of
    // it — `removeAccount`'s anchor branch promotes `extraAcctIds[0]`, and an anchor that is
    // also its own first extra would make its ✕ a no-op.
    const rememberedAnchor = live[0]
    if (!rememberedAnchor) return
    const anchor = accountId || rememberedAnchor
    if (!accountId) setAccountId(anchor)
    setExtraAcctIds((list) => {
      const next = list.filter((id) => id !== anchor)
      live.forEach((id) => {
        if (id !== anchor && !next.includes(id)) next.push(id)
      })
      return next
    })
  }
  /** The anchor's OTHER writer (the `configure` step's select). Keeps the chip invariant by
   *  dropping the newly-picked id from the extras, so the two writers can never disagree. */
  function setAnchorAccount(id: string) {
    setAccountId(id)
    setExtraAcctIds((list) => list.filter((x) => x !== id))
  }

  function currentMap(): Partial<ColumnMap> {
    return amountMode === 'single'
      ? { date: map.date, description: map.description, amount: map.amount }
      : { date: map.date, description: map.description, inflow: map.inflow, outflow: map.outflow }
  }

  /** The mapping payload the manual route TEACHES the server (column NAMES; the category
   * and status columns ride along — the server resolves category values against the live
   * category store, and derives which status value means "completed" from the data itself
   * (there's no picker here for enumerating success values; see profiles.py). */
  function manualMapping(): Record<string, string> {
    const out: Record<string, string> = {}
    Object.entries(currentMap()).forEach(([k, v]) => {
      if (v) out[k] = v
    })
    if (map.category) out.category = map.category
    if (map.status) out.status = map.status
    return out
  }

  function runManualPreview() {
    setError('')
    // Only CSV/XLSX have client-mappable columns. Every other format (PDF, OFX, QIF, MT940,
    // CAMT) is structured — the same server pipeline runs with no mapping to teach.
    if (format !== 'csv' && format !== 'xlsx') {
      runLucius()
      return
    }
    if (!rows.length || !mapComplete(currentMap())) {
      setError('Complete the column mapping first.')
      return
    }
    // The mapping is SAVED server-side as this source's format profile (SI-16), then the
    // standard decision preview runs — the manual user gets the byte-identical review
    // (bands, weak twins, ledger check), and the next import of this layout needs no
    // mapping at all (format-memory hit).
    void runServerPreview('manual', undefined, manualMapping())
  }

  function updateDraft(i: number, patch: Partial<ImportPreviewDraft>) {
    setPreview((p) => {
      if (!p) return p
      const drafts = p.drafts.slice()
      const base = drafts[i]
      if (!base) return p
      const d = { ...base, ...patch }
      if (patch.amount !== undefined) d.kind = d.amount >= 0 ? 'income' : 'expense'
      const ri = base.rowIndex ?? i
      setTouched((t) => ({
        ...t,
        [ri]: {
          ...t[ri],
          ...(patch.category !== undefined ? { category: true } : null),
          ...(patch.amount !== undefined ? { amount: true } : null),
          ...(patch.destId !== undefined ? { account: true } : null),
          ...(patch.payee !== undefined ? { payee: true } : null),
        },
      }))
      if (patch.destId !== undefined) {
        const a = accounts.find((x) => x.id === patch.destId)
        if (a) {
          d.destName = a.name
          d.preRecon = !!(a.lastReconciled && d.date < a.lastReconciled)
        }
      }
      // An explicit payee edit is the new source of truth for the row's displayed name —
      // clear the AI-resolved payee_name hint too, or rowTitle's own priority (enrich hint
      // over raw payee) keeps showing the OLD name and silently demotes the user's edit to
      // the subtitle line instead (reported case: edited "Đối tác PocketPay" → "RideGo", the
      // title stayed "Đối tác PocketPay" and "RideGo" showed underneath it instead).
      if (patch.payee !== undefined && d.enrich) d.enrich = { ...d.enrich, payee_name: null }
      drafts[i] = d
      return {
        ...p,
        drafts,
        importable: drafts.filter((x) => !x.dup).length,
        duplicates: drafts.filter((x) => x.dup).length,
      }
    })
  }

  async function confirmImport() {
    if (!preview || !account || committing) return
    // ONE confirmation for the whole destructive set, naming the count — replacing the per-row
    // dialog the staged flow removed. Deletions are the only irreversible thing this commit
    // does that the user cannot undo from the import screen afterwards.
    if (
      markedForDelete.length > 0 &&
      !window.confirm(
        `Delete ${markedForDelete.length} transaction${markedForDelete.length === 1 ? '' : 's'} from your ledger?` +
          '\n\nThis is part of confirming the import and cannot be undone from this screen.',
      )
    )
      return
    if (preview.statementId) {
      setCommitting(true)
      setError('')
      // Decision flow (the ONLY commit path — every format): send per-row deviations from
      // the defaults; the server applies ONE atomic batch against its cached parse (no row
      // data re-sent, no cap).
      const decisions: Array<Record<string, unknown>> = []
      preview.drafts.forEach((d, i) => {
        if (d.rowIndex === undefined) return
        const ri = d.rowIndex
        const act = actionFor(d, i)
        const t = touched[ri] || {}
        const edits: Record<string, unknown> = {}
        // Whatever category id this row is showing — a manual pick, a matched rule, a payee
        // default, or the ✦ AI hint — is what gets booked. No touched-gate: the row's displayed
        // category already IS the decision once the user hits Confirm without changing it.
        if (d.category) edits.category = d.category
        if (t.amount) edits.amount = d.amount
        if (t.account && d.destId) edits.account_id = d.destId
        if (t.payee) edits.payee = d.payee ?? ''
        if (d.dup) return // hard L0 — server skips regardless
        if (isUnrouted(d, i)) {
          decisions.push({ row: d.rowIndex, action: 'skip' })
          return
        }
        if (isAbsorbedLeg(d, i)) return // safely represented by the partner's two-leg transfer
        if (plainSkip[ri]) {
          decisions.push({ row: d.rowIndex, action: 'skip' })
          return
        }
        const resolved = resolveExisting(d)
        if (resolved) {
          // A weak row's Merge sends the TWIN's id (resolveExisting) — the commit guard
          // re-derives the same twin pairing server-side and validates it (never trusted raw).
          if (act === 'merge')
            decisions.push({
              row: d.rowIndex,
              action: 'merge',
              existing_id: resolved.id,
              ...(Object.keys(edits).length ? { edits } : null),
            })
          else if (act === 'new')
            decisions.push({
              row: d.rowIndex,
              action: 'create',
              ...(Object.keys(edits).length ? { edits } : null),
            })
          else decisions.push({ row: d.rowIndex, action: 'skip' })
          return
        }
        // Pre-reconciliation is a structural date-boundary gate — like isUnrouted above, it
        // must be checked before needs-eyes' own default (now Import) or a pre-recon row would
        // silently slip into the actionable queue whenever it also happened to read
        // low-confidence, defeating "pre-reconciliation rows stay behind the one explicit
        // global scope control."
        if (d.preRecon && !includeEarlier) {
          decisions.push({ row: d.rowIndex, action: 'skip' })
          return
        }
        if (d.needsEyes) {
          // Default is Import, not skip (owner 2026-07-28: a guard-flagged row with no ledger
          // match should default to booking it, not disappearing silently) — an explicit
          // `false` is the only way to land on skip; the server no longer supplies that default.
          if (eyesInclude[ri] !== false)
            decisions.push({
              row: d.rowIndex,
              action: 'create',
              ...(Object.keys(edits).length ? { edits } : null),
            })
          else decisions.push({ row: d.rowIndex, action: 'skip' })
          return
        }
        if (d.lucius?.op === 'skip') {
          if (luciusSkipInclude[ri])
            decisions.push({
              row: d.rowIndex,
              action: 'create',
              ...(Object.keys(edits).length ? { edits } : null),
            })
          else if (d.pair?.role === 'in') decisions.push({ row: d.rowIndex, action: 'skip' })
          return // server default-skips a Lucius-pass skip op without an explicit decision
        }
        if (d.pair?.role === 'in') {
          decisions.push({
            row: d.rowIndex,
            action: 'create',
            ...(Object.keys(edits).length ? { edits } : null),
          })
          return
        }
        if (Object.keys(edits).length) decisions.push({ row: d.rowIndex, action: 'create', edits })
      })
      let out: {
        created: number
        merged?: number
        duplicates?: number
        skipped?: number
        transfers?: number
        batchId?: string | null
        deleted?: number
        deleteErrors?: Array<{ id: string; error: string }>
        refusedDuplicates?: Array<{
          description?: string | null
          date?: string | null
          duplicate_of?: string | null
          reason?: string | null
        }>
      }
      try {
        out = await onDecisionCommit(
          preview.statementId,
          accountId,
          decisions,
          markedForDelete,
          file,
        )
      } catch (e) {
        let msg = e instanceof Error ? e.message : ''
        try {
          msg = (JSON.parse(msg)?.detail as string) || msg
        } catch {
          /* raw text is fine */
        }
        setError(msg || 'Import failed — nothing was booked. Check the rows and try again.')
        setCommitting(false)
        return
      }
      const acctsTouched = new Set(
        preview.drafts.filter((d, i) => !isExcluded(d, i)).map((d) => d.destId || accountId),
      ).size
      setResult({
        created: out.created,
        merged: out.merged,
        duplicates: out.duplicates ?? preview.duplicates ?? 0,
        skipped: out.skipped ?? 0,
        accounts: Math.max(1, acctsTouched),
        transfers: out.transfers ?? 0,
        batchId: out.batchId ?? null,
        deleted: out.deleted ?? 0,
        // What the review's footer said ("leave out 4"), not the server's skip total — that
        // also counts rows the reader dropped from the file (owner, 2026-10-26: "21 left out by
        // me?" after leaving out 4).
        leftOut: preview.drafts.filter((d, i) => isExcluded(d, i)).length,
        unreadable: preview.skipped?.length ?? 0,
      })
      // A delete the server refused must SURFACE — the Done screen says so rather than letting
      // "delete 8" quietly become "deleted 7".
      setDeleteErrors(out.deleteErrors ?? [])
      setRefusedDuplicates(out.refusedDuplicates ?? [])
      setReviewMarkState('idle')
      setReviewMarkError('')
      setDoneDiff(null)
      // the inverse check: ledger rows in this period the statement does NOT contain
      authFetch(`/api/statements/${preview.statementId}/ledger-diff`)
        .then(async (r) => (r.ok ? r.json() : null))
        .then((body) => {
          if (body?.status === 'ok')
            setDoneDiff({
              // Entries another imported statement backs aren't "not on this statement".
              core: (body.rows || []).filter((r: { on_statements?: unknown }) => !r.on_statements)
                .length,
              boundary: (body.boundary || []).length,
            })
        })
        .catch(() => {
          /* advisory — the done screen works without it */
        })
      setCommitting(false)
      setStep('done')
      return
    }
    // Every server preview carries a statement_id (the decision pipeline caches the parse);
    // a missing one means the preview is stale — re-run it, never fall back to a side door.
    setError('This preview is stale — run the preview again before importing.')
  }

  // "Mark all as reviewed" (SI-79 Done-screen action) — a real write, not local-only state
  // (unlike the design handoff's mockup): clears needs_review on every row this commit just
  // created. A failure leaves the button clickable again rather than silently eating it.
  // "Account · period · N rows" under the Booked title.
  function bookedContext() {
    const parts: string[] = []
    if (result && result.accounts > 1) parts.push(`${result.accounts} accounts`)
    else if (account?.name) parts.push(account.name)
    const ps = preview?.meta?.period_start
    const pe = preview?.meta?.period_end
    if (ps) parts.push(`${fDate(ps)} – ${pe ? fDate(pe) : '…'}`)
    const rowsRead = preview ? (preview.total ?? preview.drafts.length) : 0
    if (rowsRead) parts.push(`${rowsRead} row${rowsRead === 1 ? '' : 's'}`)
    return parts.join(' · ')
  }

  // After Undo import the ledger is back to exactly what the review described, so the same
  // review — with every decision the user made — is shown again rather than re-read.
  function reviewAgain() {
    setResult(null)
    setDoneDiff(null)
    setDeleteErrors([])
    setRefusedDuplicates([])
    setReviewMarkState('idle')
    setReviewMarkError('')
    setPendingDelete({})
    setStep('preview')
  }

  async function markAllReviewed() {
    if (!result?.batchId || !onMarkImportReviewed) return
    setReviewMarkState('pending')
    setReviewMarkError('')
    try {
      await onMarkImportReviewed(result.batchId)
      setReviewMarkState('done')
    } catch (e) {
      setReviewMarkState('idle')
      setReviewMarkError(
        e instanceof Error ? e.message : 'Could not mark rows as reviewed — try again.',
      )
    }
  }

  // Ledger-only Delete (owner, 2026-08-01: "can we also mark them as delete and only do in the
  // confirm button instead of delete one by one?"). Pure local state — no round-trip, no
  // per-row confirm dialog. The deletes ride on `confirmImport` with everything else, so the
  // footer's promise holds and the whole set is undoable until then.
  function toggleDeleteMark(id: string) {
    setKeptLedger((current) => {
      if (!current[id]) return current
      const next = { ...current }
      delete next[id]
      return next
    })
    setPendingDelete((current) => {
      const next = { ...current }
      if (next[id]) delete next[id]
      else next[id] = true
      return next
    })
  }
  function markAllForDelete(ids: string[], on: boolean) {
    setPendingDelete((current) => {
      const next = { ...current }
      ids.forEach((id) => {
        if (on) next[id] = true
        else delete next[id]
      })
      return next
    })
  }
  // Existing-transaction drawer (SI-50): fetch by id (it may not be on the currently-loaded
  // register page) and open the real TxnDetail drawer above this modal. Opening/closing it
  // never touches any preview/decision state above, so per-row Skip/Merge/Import-new choices
  // survive untouched.
  async function openExistingTxn(id: string) {
    setDrawerFailed((m) => ({ ...m, [id]: false }))
    setDrawerLoading((m) => ({ ...m, [id]: true }))
    try {
      const r = await authFetch(`/api/transactions/${id}`)
      if (!r.ok) throw new Error('not found')
      setDrawerTxn(fromApiTxn((await r.json()) as ApiTxn))
    } catch {
      setDrawerFailed((m) => ({ ...m, [id]: true }))
    } finally {
      setDrawerLoading((m) => ({ ...m, [id]: false }))
    }
  }
  // Saving from the drawer re-fetches rather than hand-merging the patch — keeps the drawer's
  // own display authoritative without duplicating TxnDetail's patch→row mapping here. The
  // import PREVIEW behind it is deliberately NOT refreshed: the commit-time guard re-derives
  // every match server-side against the live ledger, so a stale preview flag can never mis-merge.
  async function saveDrawerTxn(id: string, patch: Record<string, unknown>) {
    if (onSaveTxn) await onSaveTxn(id, patch)
    await openExistingTxn(id)
  }
  // Same re-fetch-rather-than-hand-merge shape as saveDrawerTxn above — awaits the write (and
  // its audit event) before refreshing the drawer, so the reopened TxnDetail's activity effect
  // (keyed on the `txn` object reference) never re-fetches ahead of the update it's showing.
  async function markDrawerTxnReviewed(id: string) {
    if (onMarkTxnReviewed) await onMarkTxnReviewed(id)
    await openExistingTxn(id)
  }
  // Same shape again — awaits the detach write, then re-fetches the row. Without this, the
  // drawer's own `drawerTxn.externalRefs` never changed after a detach, so unlinking the LAST
  // entry left the "Source" heading orphaned (hasSourceRef still true off the stale row) until
  // the drawer was closed and reopened. `onDetachTxnSourceRef` omitted still fails LOUDLY (the
  // drawer's own inline error + Retry) — there's no safe no-op for a write, so this throws
  // rather than silently no-op-refreshing.
  async function detachDrawerSourceRef(txn: Txn, source: string, ref: string) {
    if (!onDetachTxnSourceRef) throw new Error('Unlink is unavailable here')
    await onDetachTxnSourceRef(txn, source, ref)
    await openExistingTxn(txn.id)
  }
  // SI-67: edit the remembered instructions from the review meta line. Saves via PUT —
  // steers the NEXT fresh parse of this source; never re-parses or re-bills here.
  async function saveInstructions(override?: string): Promise<boolean> {
    if (!preview?.statementId) return false
    const text0 = override ?? ''
    setInstrSaving(true)
    setInstrErr('')
    try {
      const r = await authFetch(`/api/statements/${preview.statementId}/instructions`, {
        method: 'PUT',
        body: JSON.stringify({ text: text0 }),
      })
      if (!r.ok) throw new Error(await r.text())
      const body = (await r.json()) as { saved: boolean; text: string }
      if (!body.saved && body.text)
        throw new Error(
          'Couldn’t remember these for this source (it has no stable fingerprint) — typed instructions still apply at upload time.',
        )
      const text = body.text
      setPreview((p) =>
        p
          ? {
              ...p,
              instructions: text
                ? {
                    text,
                    lines: text.split('\n').filter((l) => l.trim()).length,
                    used_in_parse: false,
                  }
                : null,
            }
          : p,
      )
      setInstructions(text)
      if (text.trim()) setTaught(true)
      return true
    } catch (e) {
      let msg = e instanceof Error ? e.message : ''
      try {
        msg = (JSON.parse(msg)?.detail as string) || msg
      } catch {
        /* raw text is fine */
      }
      setInstrErr(msg || 'Could not save instructions.')
      return false
    } finally {
      setInstrSaving(false)
    }
  }
  // SI-68: undo ONE row's Lucius-pass op (``row``) or all of them (omitted) — a server
  // round-trip (the applied ops are cached on the statement record and re-applied at
  // commit, so "not these" must be recorded server-side, not just hidden client-side),
  // then the standard free re-preview against the cached parse. Every OTHER row's decision
  // state survives untouched: every per-row map below is keyed by the row's stable, server-
  // assigned `rowIndex` (never the array position `preview.drafts` happens to occupy), so the
  // re-preview's fresh draft array — which can reorder, grow or shrink — can never misalign a
  // decision onto the wrong row. Only the ONE undone row's own pairing/match state is dropped:
  // its shape just changed (a routed transfer / stamped category is back to a plain expense or
  // income), so a manual link or match override recorded against its old shape may no longer
  // make sense. This used to be a blanket reset of every map on the statement —
  // real, reported data loss on a 100+-row statement (fixing one row's routing silently
  // discarded every other row's Import/Leave-out/merge decision).
  /** Accounts a row can be a transfer with: same currency, live, not the statement's own. */
  function transferAccountsFor(d: ImportPreviewDraft): Account[] {
    const own = d.destId || accountId
    return accounts.filter(
      (a) =>
        a.id !== own &&
        !a.archived &&
        (a.currency || '').toUpperCase() === (d.currency || '').toUpperCase(),
    )
  }
  /** The Edit window's Type switch: saved on the statement, then the review re-reads the cached
   *  parse (no AI) so the row shows — and Confirm books — exactly the chosen type. */
  async function setRowType(row: number | undefined, accountId: string | null) {
    if (!preview?.statementId || row == null) return
    setLuciusUndoing((m) => ({ ...m, [row]: true }))
    try {
      const r = await authFetch(`/api/statements/${preview.statementId}/row-type`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ row, account_id: accountId }),
      })
      if (!r.ok) throw new Error(await r.text())
      await runServerPreview(route, undefined, undefined, undefined, undefined, true)
    } catch {
      setError('Could not change the row type — try again.')
    } finally {
      setLuciusUndoing((m) => ({ ...m, [row]: false }))
    }
  }

  async function undoLucius(row?: number) {
    if (!preview?.statementId) return
    const key = row ?? -1
    setLuciusUndoing((m) => ({ ...m, [key]: true }))
    try {
      const qs = row != null ? `?row=${row}` : ''
      const r = await authFetch(`/api/statements/${preview.statementId}/lucius-ops${qs}`, {
        method: 'DELETE',
      })
      if (!r.ok) throw new Error(await r.text())
      if (row != null) {
        const drop = <T,>(m: Record<number, T>): Record<number, T> => {
          if (!(row in m)) return m
          const next = { ...m }
          delete next[row]
          return next
        }
        setPairInclude(drop)
        setManualLink(drop)
        setMatchOverride(drop)
      }
      setQueueFixingIndex(null)
      setQueueFixValue('')
      await runServerPreview(route, undefined, undefined, undefined, undefined, true)
    } catch {
      // best-effort — a failed undo leaves the row's provenance chip in place; retry-able
    } finally {
      setLuciusUndoing((m) => ({ ...m, [key]: false }))
    }
  }

  const fileStatementId = step === 'preview' ? preview?.statementId : undefined
  const fileWanted = !!fileStatementId && (isMobile ? phoneView === 'file' : showFile)
  const filePaneItem =
    fileStatementId && fileItem?.id === fileStatementId ? fileItem.item : undefined
  function toggleShowFile() {
    setShowFile((v) => !v)
  }
  function saveFileWidth(px: number) {
    try {
      localStorage.setItem(FILE_WIDTH_KEY, String(Math.round(px)))
    } catch {
      /* private window / blocked storage — the width just isn't remembered */
    }
  }
  /** The divider between review and file: drag, arrow keys, double-click for the default split. */
  function clampFileWidth(px: number, body: HTMLElement) {
    return Math.max(FILE_MIN_PX, Math.min(px, body.clientWidth - REVIEW_MIN_PX))
  }
  function startFileResize(e: React.PointerEvent<HTMLDivElement>) {
    const handle = e.currentTarget
    const body = handle.parentElement
    if (!body) return
    e.preventDefault()
    handle.setPointerCapture(e.pointerId)
    let last = fileWidth
    const move = (ev: PointerEvent) => {
      last = clampFileWidth(body.getBoundingClientRect().right - ev.clientX, body)
      setFileWidth(last)
    }
    const end = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', end)
      handle.removeEventListener('pointercancel', end)
      if (last != null) saveFileWidth(last)
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', end)
    handle.addEventListener('pointercancel', end)
  }
  function nudgeFileWidth(e: React.KeyboardEvent<HTMLDivElement>) {
    const step = e.key === 'ArrowLeft' ? 32 : e.key === 'ArrowRight' ? -32 : 0
    const body = e.currentTarget.parentElement
    const pane = body?.querySelector<HTMLElement>('.imp3-filepane')
    if (!step || !body || !pane) return
    e.preventDefault()
    const next = clampFileWidth(pane.getBoundingClientRect().width + step, body)
    setFileWidth(next)
    saveFileWidth(next)
  }
  function resetFileWidth() {
    setFileWidth(null)
    try {
      localStorage.removeItem(FILE_WIDTH_KEY)
    } catch {
      /* nothing remembered to clear */
    }
  }
  /** A click on a review row points the file pane at the row of the file it came from. */
  function pointFileAtRow(e: React.MouseEvent) {
    const row = (e.target as HTMLElement).closest('[data-draft-index]')
    const draft = row && preview?.drafts[Number(row.getAttribute('data-draft-index'))]
    if (draft && draft.sourceRow) setFileRow(draft.sourceRow)
  }
  // The statement's Documents item, fetched once per statement while its pane is wanted. A
  // statement with no stored file (or a failed fetch) gets null — the pane then says so.
  useEffect(() => {
    if (!fileWanted || !fileStatementId || fileItem?.id === fileStatementId) return
    let alive = true
    fetchVaultItem(`statement-${fileStatementId}`).then(
      (item) => alive && setFileItem({ id: fileStatementId, item }),
      () => alive && setFileItem({ id: fileStatementId, item: null }),
    )
    return () => {
      alive = false
    }
  }, [fileWanted, fileStatementId, fileItem?.id])

  if (!open) return null
  const canManualPreview = !!accountId && (!needsMapping(format) || mapComplete(currentMap()))
  const earlierRows = preview ? preview.drafts.filter((d) => !d.dup && d.preRecon) : []
  // A row whose EFFECTIVE resolved existing is a user-asserted manual link — the engine flag
  // takes precedence (resolveExisting's order), so this is true only when nothing shadows it.
  // Drives the review-band promotion, the distinct headline, the unlink control, and the
  // ledger-only drop.
  const isManualLinked = (d: ImportPreviewDraft): boolean =>
    d.rowIndex != null && !!manualLink[d.rowIndex] && !matchFlag(d)
  // "Change match" (owner directive): distinct from isManualLinked above — that one is only
  // ever true when the engine had NO opinion to begin with. This is true when the user
  // re-pointed an already engine-matched row onto one of its server-sent `alternatives`;
  // always coexists with a truthy matchFlag, never with isManualLinked.
  const isMatchOverridden = (d: ImportPreviewDraft): boolean =>
    d.rowIndex != null && !!matchOverride[d.rowIndex]
  // The band the row RENDERS at = the server's band (`matchBand`) plus the one client-side
  // branch the server cannot know about: a manual link the owner asserted.
  const pairBand = (d: ImportPreviewDraft): 'strong' | 'review' | 'weak' | 'none' => {
    const b = matchBand(d)
    if (b !== 'none') return b
    // A user-asserted manual link renders through the review-band pair-card machinery; the
    // profile default ladder in actionFor then defaults it to Merge (owner 2026-07-18).
    return isManualLinked(d) ? 'review' : 'none'
  }
  // Resolution order (RFC 2026-07-29 §D8): matchOverride → matchFlag → manualLink.
  const resolveExisting = (d: ImportPreviewDraft): ResolvedExisting | undefined => {
    // Highest priority (owner directive): a "Change match" override on an already-matched
    // row — unlike manualLink below (checked LAST, since it only ever fires where nothing
    // else resolved), this one must SUPERSEDE the engine's own matchFlag pick.
    const mo = d.rowIndex != null ? matchOverride[d.rowIndex] : undefined
    if (mo) return { id: mo.id, summary: mo.summary }
    const f = matchFlag(d)
    if (f?.existing_id) return { id: f.existing_id, summary: f.existing ?? {} }
    // Checked LAST (owner 2026-07-18): the user's own manual link.
    const ml = d.rowIndex != null ? manualLink[d.rowIndex] : undefined
    return ml ? { id: ml.id, summary: ml.summary } : undefined
  }
  // The server-sent option list for this row's `Change match` picker, VERBATIM: no client
  // re-sort, re-filter or dimming (SIUM-ALT-PICKER). Empty ⇒ no control renders at all.
  const alternativesFor = (d: ImportPreviewDraft) => matchFlag(d)?.alternatives || []
  // A flag's merge exception describes the engine candidate that supplied it, not any
  // replacement the user later picks. Reusing transfer_pair/transfer_leg_echo semantics
  // after Change match would make the preview disagree with apply_import_merge.
  const resolvedFlagType = (d: ImportPreviewDraft, resolved: ResolvedExisting | undefined) => {
    const flag = matchFlag(d)
    return flag?.existing_id && flag.existing_id === resolved?.id ? flag.type : undefined
  }
  // No wallet holds this row's currency — it can never book until the user picks an account.
  const isUnrouted = (d: ImportPreviewDraft, i: number) =>
    (d.flags || []).some((f) => f.type === 'no_account_for_currency') &&
    !(touched[d.rowIndex ?? i]?.account && d.destId)
  // Default action per band: strong → merge; review/unbanded resolvable → the profile's
  // `import_review_default` (owner 2026-07-17, second decision: factory default MERGE for
  // all users — a probable match pre-selects the durable resolution: reference stamped,
  // re-imports L0-dedupe, labels kept; 'skip' is the explicit opt-out for users who want
  // to decide each row; the band keeps its amber question treatment either way — it is a
  // pre-selection, never a silent write); weak → new (its SAFE DEFAULT: a possible match
  // never silently links, which is exactly why a weak row can stay out of `Needs you`);
  // everything else imports.
  const reviewDefault: MatchAction = importReviewDefault === 'skip' ? 'skip' : 'merge'
  const actionFor = (d: ImportPreviewDraft, i: number): MatchAction =>
    matchAction[d.rowIndex ?? i] ??
    (pairBand(d) === 'strong'
      ? 'merge'
      : pairBand(d) === 'weak'
        ? 'new'
        : resolveExisting(d) || pairBand(d) === 'review'
          ? reviewDefault
          : 'new')
  const pairPartner = (d: ImportPreviewDraft): ImportPreviewDraft | undefined => {
    if (!preview || !d.pair) return undefined
    return preview.drafts.find((row) => row.rowIndex === d.pair!.partner_index)
  }
  // A parser-level FX pair is only one ledger transfer when neither leg is already claimed
  // by an ordinary expense/income. Folding across such a match would either lose the IN leg
  // (OUT merge/skip) or duplicate its balance (IN match hidden behind an OUT create).
  const pairHasOrdinaryMatch = (d: ImportPreviewDraft): boolean =>
    [d, pairPartner(d)].some((row) => {
      if (!row) return false
      const resolved = resolveExisting(row)
      return !!resolved && resolved.summary.kind !== 'transfer'
    })
  const isAbsorbedLeg = (d: ImportPreviewDraft, i: number) =>
    d.pair?.role === 'in' && !pairInclude[d.rowIndex ?? i] && !pairHasOrdinaryMatch(d)
  // A WEAK row is deliberately absent here (SIUM-CHIP-WEAK / SIUM-REVIEW-QUEUE): it has a
  // safe default (Import — nothing links silently), so parking it in `Needs you` would demand
  // attention for a row that needs none and inflate the one count that must stay honest.
  const belongsToDecisionQueue = (d: ImportPreviewDraft, i: number) => {
    const band = pairBand(d)
    return (
      !d.dup &&
      !isUnrouted(d, i) &&
      !isAbsorbedLeg(d, i) &&
      !(d.preRecon && !includeEarlier) &&
      (band === 'review' || (d.needsEyes && !resolveExisting(d)))
    )
  }
  // Every row starts pre-decided with its own current default action already reads as
  // selected/booked, no exceptions for a weak-twin match or a needs-eyes read with no match
  // (both used to stay genuinely undecided until an explicit tap). Runs once per fresh preview load
  // (previewGeneration, not preview/statementId — see its own declaration for why), using the
  // fully reactive pairBand/resolveExisting/belongsToDecisionQueue so weak-twin assignment
  // (computed from ledgerCheck data, not just matchFlag) is already correct by the time this
  // fires. Undo still works normally afterward — it only deletes this row's queueDecided
  // entry, and this effect never re-adds one later (it's keyed on the load, not on state).
  // useLayoutEffect, not useEffect: this must land before the browser paints, or the first
  // frame flashes every row as undecided (Needs-you at its full count) for one tick before
  // snapping to the decided state.
  useLayoutEffect(() => {
    if (!preview) return
    const next: Record<number, boolean> = {}
    preview.drafts.forEach((d, i) => {
      if (belongsToDecisionQueue(d, i)) next[d.rowIndex ?? i] = true
    })
    setQueueDecided(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per loaded statement
  }, [previewGeneration])
  // Manual-link affordance gate (owner 2026-07-18): a plain UNMATCHED "New" row — one the
  // engine/weak/manual resolver hasn't already claimed, and not a dup / FX-pair leg / routed /
  // Lucius-op / needs-eyes / unrouted / absorbed row (all of which carry their own controls) —
  // can be user-linked to an existing uncovered entry. Once linked, resolveExisting is truthy,
  // so this returns false and the row is a review-band pair card instead of a plain row.
  const luciusSettlesTheRow = (d: ImportPreviewDraft): boolean =>
    !!d.lucius && d.lucius.op !== 'reject_match'
  const canManualLinkRow = (d: ImportPreviewDraft, i: number): boolean =>
    // What counts is the row's CURRENT decision, not what the engine first proposed (owner,
    // 2026-10-26): any row not set to Link is unclaimed and may link to an entry the user picks.
    (!resolveExisting(d) || actionFor(d, i) !== 'merge') && linkableShape(d, i)
  /** A row whose kind can take a link at all — whatever it is linked to right now. */
  const linkableShape = (d: ImportPreviewDraft, i: number): boolean =>
    d.rowIndex != null &&
    !d.dup &&
    !d.pair &&
    !d.routed &&
    !luciusSettlesTheRow(d) &&
    !isUnrouted(d, i) &&
    !isAbsorbedLeg(d, i)
  /** Outcome/link line (band-agnostic — one function for all three bands whenever the
   * resolved action is merge or new). */
  const outcomeLine = (d: ImportPreviewDraft, i: number): string | null => {
    const act = actionFor(d, i)
    if (act === 'skip') return null
    if (act === 'new') return 'Adds a second transaction next to the one you already have'
    const resolved = resolveExisting(d)
    if (!resolved) return null
    const flagType = resolvedFlagType(d, resolved)
    const ex = resolved.summary
    if (flagType === 'transfer_leg_echo' || ex?.kind === 'transfer') {
      // The statement is authoritative for the leg on its OWN account and silent about the
      // other, so only that leg is re-amounted (ledger.apply_import_merge, facts="statement").
      const legDrifted = typeof ex?.amount === 'number' && Math.abs(ex.amount - d.amount) > 0.004
      return legDrifted
        ? `Links to your existing transfer • this side adopts the statement’s ${fCur(Math.abs(d.amount), d.currency, { cents: true })} • the other leg stays as booked`
        : 'Links to your existing transfer • its legs stay as booked'
    }
    // transfer_pair merge (decision_commit → apply_import_merge, facts="statement"): the two
    // legs carry DIFFERENT kinds (expense vs income) by construction, so the merge's own
    // kind-match gate never fires — date/amount are NOT rewritten, no transfer is rebuilt. Say
    // what actually happens: the existing row is marked cleared and absorbs the statement's
    // reference (future re-imports dedupe against it); this statement row itself never books.
    if (flagType === 'transfer_pair')
      return 'Marks it cleared & links the statement’s reference • date, amount, category & notes stay as they are • this row won’t be booked on its own'
    const dateChanged = !!ex?.date && ex.date !== d.date
    const amountChanged = typeof ex?.amount === 'number' && Math.abs(ex.amount - d.amount) > 0.004
    // Foreign-charge gap-fill (apply_import_merge: "copied in when the existing row lacks
    // one" — never overwrites an original amount the existing row already has).
    const draftOrigAmt = d.original_amount ?? d.origAmount
    const draftOrigCcy = d.original_currency ?? d.origCurrency
    const addsOriginal = draftOrigAmt != null && !!draftOrigCcy && ex?.original_amount == null
    const frags: string[] = []
    if (dateChanged) frags.push(`${ex!.date!.slice(5)} → ${d.date.slice(5)}`)
    if (amountChanged)
      frags.push(
        `${fCur(ex!.amount!, ex!.currency || d.currency, { cents: true })} → ${fCur(d.amount, d.currency, { cents: true })}`,
      )
    if (addsOriginal)
      frags.push(
        `adds the original charge (${fCur(draftOrigAmt!, draftOrigCcy!, { cents: true })})`,
      )
    if (!frags.length) frags.push('Marks it settled')
    frags.push('keeps your category & notes')
    return frags.join(' • ')
  }
  // SI-64 per-row ledger effect — mirrors confirmImport's decision ladder EXACTLY (keep the
  // two in lockstep): dup → unrouted → FX fold → resolved-match action → pre-reconciled →
  // needs-eyes → create. Feeds the pure coverage/amounts verdicts.
  const verdictInput = (d: ImportPreviewDraft, i: number): VerdictRowInput => {
    const base = { amount: d.amount, currency: d.currency }
    if (d.dup) return { effect: 'dup', ...base }
    const resolved = resolveExisting(d)
    const ex = resolved
      ? { amount: resolved.summary.amount, currency: resolved.summary.currency }
      : null
    // A linked TRANSFER's summary amount is the from-leg's unsigned magnitude (possibly the
    // OTHER currency) — resolve the anchor-side leg so kept-figure math compares like with
    // like (reported cases: false "Off by ₫350,000" / "Off by ₫1,499,900").
    const kept = resolved
      ? anchorPerspectiveAmount(resolved.summary, d.destId || accountId, d.amount)
      : null
    const keptCcy = resolved
      ? anchorPerspectiveCurrency(resolved.summary, d.destId || accountId, d.currency)
      : null
    if (isUnrouted(d, i))
      return {
        effect: 'skip',
        ...base,
        existing: ex,
        existingKept: kept,
        existingKeptCurrency: keptCcy,
      }
    if (isAbsorbedLeg(d, i)) {
      const partnerIndex =
        preview?.drafts.findIndex((row) => row.rowIndex === d.pair!.partner_index) ?? -1
      const partner =
        partnerIndex >= 0 && partnerIndex !== i ? preview?.drafts[partnerIndex] : undefined
      const partnerEffect = partner ? verdictInput(partner, partnerIndex).effect : 'skip'
      const partnerRepresented =
        !!partner &&
        (partnerEffect === 'merge' ||
          partnerEffect === 'new' ||
          partnerEffect === 'dup' ||
          !!resolveExisting(partner))
      return {
        effect: partnerRepresented ? 'absorbed' : 'skip',
        ...base,
      }
    }
    if (plainSkip[d.rowIndex ?? i])
      return {
        effect: 'skip',
        ...base,
        existing: ex,
        existingKept: kept,
        existingKeptCurrency: keptCcy,
      }
    if (resolved) {
      const act = actionFor(d, i)
      if (act === 'merge')
        return {
          effect: 'merge',
          ...base,
          existing: ex,
          existingKept: kept,
          existingKeptCurrency: keptCcy,
          mergeAdopts: mergeAdoptsStatementFacts(
            d.kind,
            resolved.summary.kind,
            resolvedFlagType(d, resolved),
          ),
        }
      if (act === 'new') return { effect: 'new', ...base }
      return {
        effect: 'skip',
        ...base,
        existing: ex,
        existingKept: kept,
        existingKeptCurrency: keptCcy,
      }
    }
    // Pre-reconciliation is a structural date-boundary gate — like isUnrouted above, it must
    // win over needs-eyes' own default (now Import, owner 2026-07-28) or a pre-recon row would
    // silently slip into the actionable queue whenever it also happened to read low-confidence,
    // defeating "pre-reconciliation rows stay behind the one explicit global scope control."
    if (d.preRecon && !includeEarlier) return { effect: 'skip', ...base }
    if (d.needsEyes)
      return eyesInclude[d.rowIndex ?? i] !== false
        ? { effect: 'new', ...base }
        : { effect: 'skip', ...base }
    if (d.lucius?.op === 'skip')
      return luciusSkipInclude[d.rowIndex ?? i]
        ? { effect: 'new', ...base }
        : { effect: 'skip', ...base }
    if (d.pair?.role === 'in') return { effect: 'new', ...base }
    return { effect: 'new', ...base }
  }
  // ONE derivation per render feeds every Off-by surface (owner motivation 2026-07-17):
  // the verdict inputs, the per-row drift map (draft array index → linkedAmountDrift), and
  // the amounts verdict itself. amountsVerdict consumes the same helper, so verdict kept ==
  // pill count == rendered markers by construction — the counting cross-walk, extended.
  const verdictInputs: VerdictRowInput[] = []
  const driftByIndex = new Map<number, { statement: number; kept: number; diff: number }>()
  preview?.drafts.forEach((d, i) => {
    const vr = verdictInput(d, i)
    verdictInputs.push(vr)
    const drift = linkedAmountDrift(vr)
    if (drift) driftByIndex.set(i, drift)
  })
  const amtV = amountsVerdict(verdictInputs)
  // The verdict line's own "Off by" amount string — the filter pill echoes it verbatim; a
  // cancelled-out net (kept rows, empty deltas) reads ±0.
  const offByAmount = Object.keys(amtV.deltas).length
    ? Object.entries(amtV.deltas)
        .map(([ccy, v]) => fCur(Math.abs(v), ccy, { cents: true }))
        .join(' • ')
    : '±0'
  // Prefer the RESOLVED payee's clean name over the raw statement text — a payee-resolution
  // match already exists on the wire as `enrich.payee_name` for every row, it just never
  // surfaced anywhere. Falls back to the raw payee/description when nothing resolved.
  const rowTitle = (d: ImportPreviewDraft) => d.enrich?.payee_name || d.payee || d.description || ''
  // The raw bank text stays visible as a subtitle whenever the title above it is the resolved
  // name (or otherwise differs) — never hide the source line, only prefer the friendlier one.
  const rowSubtitle = (d: ImportPreviewDraft) => {
    const t = rowTitle(d)
    return [d.payee, d.description].find((v): v is string => !!v && v !== t) || null
  }
  // The per-row Off-by marker — rendered wherever a matched row's action controls render
  // (pair-card head + both plain-row match branches); non-null exactly when the verdict
  // counts the row as kept at your figures.
  const offByChip = (i: number, currency: string) => {
    const drift = driftByIndex.get(i)
    if (!drift) return null
    return (
      <span className="imp-offby" title={offByTitle(verdictInputs[i]!) || undefined}>
        Off by {fCur(Math.abs(drift.diff), currency, { cents: true })}
      </span>
    )
  }
  // Uncovered entries a manual link consumed — dropped from the "not on statement" list (they
  // ARE accounted for once linked). Client-only: the list is derived from the server preview,
  // so this pre-commit removal is the only thing that takes a manually-linked entry off the
  // panel before the batch commits.
  const manualLinkedIds = new Set<string>()
  if (preview)
    preview.drafts.forEach((d) => {
      if (d.rowIndex != null && isManualLinked(d)) manualLinkedIds.add(manualLink[d.rowIndex]!.id)
    })
  // Same reasoning, for a "Change match" override: the re-pointed-to entry is accounted for
  // and drops off the "not on statement" panel, even though the engine's own pick left it
  // uncovered (it was never THAT row's match to begin with).
  const matchOverrideIds = new Set<string>()
  if (preview)
    preview.drafts.forEach((d) => {
      if (d.rowIndex != null && matchOverride[d.rowIndex])
        matchOverrideIds.add(matchOverride[d.rowIndex]!.id)
    })
  // The RE-INSERTION half of the panel (RFC 2026-07-29 §D3 amendment (c)). `ledger_check.rows`
  // is a pure set difference, so the server already SUBTRACTED the entry a weak flag claimed;
  // a weak row that books as NEW makes that entry genuinely uncovered again, and it has to come
  // back or the SI-44 reconciliation tie-out silently under-reports. Re-inserted is the summary
  // the SERVER already sent, under the id the SERVER already chose — no tolerance, no scoring,
  // no claim arbitration. This is bookkeeping, NOT a second matcher.
  //
  // The trigger is the row's own DECISION, not a separate dismissal control (the standalone
  // "Not the same" button is gone — owner, 2026-07-31; ADR 2026-07-31-weak-import-releases-
  // the-entry). On a maybe-match, "Import" (keep both) IS the assertion that the two are not
  // the same thing, so it releases; "Same one — link" re-claims it, and "Leave out" keeps the
  // claim (nothing double-books — the entry already covers the row). EVERY band now (owner,
  // 2026-10-26: Import on a strong match booked a second Grab while the first one vanished from
  // view): a strong/review row's default is Link, so Import on it is always the owner's own
  // call, never a default. A row re-pointed at a different entry releases its original one too.
  const dismissedWeakEntries: LedgerCheckRow[] = preview
    ? preview.drafts.flatMap((d, i) => {
        const flag = matchFlag(d)
        const resolved = resolveExisting(d)
        const bookedNew = verdictInputs[i]?.effect === 'new'
        const repointed = !!flag?.existing_id && !!resolved && resolved.id !== flag.existing_id
        if (!bookedNew && !repointed) return []
        const out: LedgerCheckRow[] = []
        if (flag?.existing_id && (bookedNew || repointed))
          out.push({
            ...(flag.existing ?? {}),
            id: flag.existing_id,
            note: flag.existing?.note ?? undefined,
          } as LedgerCheckRow)
        if (bookedNew && resolved && resolved.id !== flag?.existing_id)
          out.push({
            ...resolved.summary,
            id: resolved.id,
            note: resolved.summary.note ?? undefined,
          })
        return out
      })
    : []
  // A release beats the two "this entry is spoken for" drops below. They can only collide on ONE
  // shape — the same weak row carrying a `Change match` override AND set back to Import — and
  // there the row's live decision is the truth: it books as new, so whatever it points at is
  // uncovered. (No cross-row collision is reachable: a claimed entry is in neither
  // `manualLinkOptions` nor another row's server-sent `alternatives`.)
  //
  // A row marked for deletion is NOT filtered out here: unlike a linked entry (which is
  // accounted for and genuinely leaves the panel), a marked one is still in the ledger and
  // still uncovered until commit. It stays put, rendered as marked, so the mark is visible and
  // undoable — the row vanishing on click was the old write-through behaviour's tell.
  const releasedIds = new Set(dismissedWeakEntries.map((e) => e.id))
  const allLedgerOnlyRows = preview
    ? [
        ...(preview.ledgerCheck?.rows || []),
        // Deduped by id: harmless today (the server excludes a claimed entry by construction),
        // and inert-but-honest if that ever stops holding.
        ...dismissedWeakEntries.filter(
          (e) => !(preview.ledgerCheck?.rows || []).some((r) => r.id === e.id),
        ),
      ].filter(
        (r) => releasedIds.has(r.id) || (!manualLinkedIds.has(r.id) && !matchOverrideIds.has(r.id)),
      )
    : []
  // An entry ANOTHER statement we hold already backs isn't "not on this statement" in any sense
  // that matters — overlapping exports of one account repeat each other's days (owner,
  // 2026-10-26: "why are these linked to another statement already but still shown here?").
  // It leaves the count, the warning and Mark all; it stays reachable in a quiet group.
  const ledgerOnlyRows = allLedgerOnlyRows.filter((r) => !r.on_statements)
  const onOtherStatementRows = allLedgerOnlyRows.filter((r) => !!r.on_statements)
  // The staged delete set, in panel order — the footer counts it and the commit sends exactly
  // this. Derived from what is ON SCREEN, never from the raw `pendingDelete` map: an entry that
  // left the panel (linked to a row after being marked) must not still be queued for deletion.
  const markedForDelete = allLedgerOnlyRows.filter((r) => pendingDelete[r.id]).map((r) => r.id)
  // Coverage question 1's exact residue — a row with NO way into the ledger at all. Its own
  // dedicated, always-visible section (not just a filter chip) so it's directly findable
  // rather than requiring a click-through + scroll to notice (owner: "hard to know, hard to
  // find"). Mirrors coverageVerdict's own "uncovered" predicate exactly.
  const uncoveredRows = preview
    ? preview.drafts
        .map((d, i) => ({ d, i }))
        .filter(({ i }) => {
          const vr = verdictInputs[i]
          return !!vr && vr.effect === 'skip' && !vr.existing
        })
    : []
  // All outcome surfaces consume the same per-row effect ladder as the commit payload above.
  // In particular, an absorbed FX leg never inflates "import", while a resolved match wins
  // before needs-eyes/Lucius/pre-reconciliation guards exactly as it does in confirmImport.
  const rowEffect = (i: number) => verdictInputs[i]!.effect
  const willCreate = (_d: ImportPreviewDraft, i: number) => rowEffect(i) === 'new'
  const willMerge = (_d: ImportPreviewDraft, i: number) => rowEffect(i) === 'merge'
  const isAbsorbed = (_d: ImportPreviewDraft, i: number) => rowEffect(i) === 'absorbed'
  const isExcluded = (_d: ImportPreviewDraft, i: number) =>
    rowEffect(i) === 'skip' || rowEffect(i) === 'dup'
  const excludedRows = preview ? preview.drafts.filter((d, i) => isExcluded(d, i)) : []
  // Exactly ONE primary filter owns every statement row. Outcome-changing decisions win
  // where the old structural bands would lie (a strong row explicitly kept as new is New;
  // any explicit skip is Left out). Review rows remain in the Needs-you work queue after a
  // non-skip receipt; a WEAK row never enters it at all, so it partitions by its OUTCOME like
  // any other row (Import by default, Will link once linked) and is reachable through the
  // transient `Possible matches` lens instead. An absorbed FX leg belongs with Will link
  // because it folds into the partner transfer; its scan chip still says "Folded into
  // transfer" explicitly.
  const effectiveChipKey = (
    d: ImportPreviewDraft,
    i: number,
  ): Exclude<ChipKey, 'all' | 'ledger' | 'offby' | 'uncovered' | 'weak'> => {
    const effect = rowEffect(i)
    const belongsToQueue = belongsToDecisionQueue(d, i)
    // A Needs-you row moves to its outcome chip the moment it's decided — Left out / Will
    // link / New — so the Needs-you count only ever reflects genuinely undecided rows.
    // Gated on queueDecided specifically, not just the current effect — an undecided row
    // still shows its live default action (e.g. "Same one — link" pre-selected) without
    // that alone moving it out of the queue.
    const decided = !!queueDecided[d.rowIndex ?? i]
    if (belongsToQueue && !decided) return 'needs'
    if (effect === 'skip' || effect === 'dup') return 'skip'
    if (effect === 'absorbed') return 'strong'
    if (d.pair) return effect === 'merge' ? 'strong' : 'new'
    // A needsEyes row WITH a resolved match (the "resolved match wins" rule — belongsToQueue's
    // own needsEyes clause requires no match) is never re-classified as 'needs' here: it would
    // inflate the chip count for a row that's already excluded from queueItems by
    // belongsToDecisionQueue, leaving the badge showing a row the queue itself never renders
    // (owner repro 2026-07-28: "needs you still show 2, but when i tap on it, nothing is shown").
    return effect === 'merge' ? 'strong' : 'new'
  }
  const chipCount = (key: Exclude<ChipKey, 'all' | 'ledger' | 'offby' | 'uncovered' | 'weak'>) =>
    preview
      ? preview.drafts.reduce((count, d, i) => count + (effectiveChipKey(d, i) === key ? 1 : 0), 0)
      : 0
  // The `Possible matches` LENS population (SIUM-CHIP-WEAK): weak rows whose possibility is
  // still OPEN — a dismissed one has left the band entirely, a linked one is settled under
  // Will link. When that empties the chip stops rendering and the shared fallback below
  // returns the filter to All rather than stranding an empty list.
  const openWeakRows = preview
    ? preview.drafts
        .map((d, i) => ({ d, i }))
        .filter(({ d, i }) => pairBand(d) === 'weak' && actionFor(d, i) !== 'merge')
    : []
  const chipPop: Record<Exclude<ChipKey, 'all'>, number> = {
    strong: chipCount('strong'),
    needs: chipCount('needs'),
    new: chipCount('new'),
    skip: chipCount('skip'),
    ledger: ledgerOnlyRows.length,
    offby: driftByIndex.size,
    uncovered: uncoveredRows.length,
    weak: openWeakRows.length,
  }
  // Kind counts — a second, independent filter dimension (ANDed with the decision chip, not
  // a replacement for it), so they're always the FULL statement's counts regardless of which
  // decision chip is active.
  // `d.kind` on the wire is only ever 'income'/'expense' (mapServerPreview derives it from the
  // amount's sign) — a routed row's real kind lives on `d.routed` instead, so that has to win
  // first. A resolved transfer_pair/transfer_leg_echo match ALSO books as a transfer once
  // Linked (apply_import_transfer_pair_merge / the existing-transfer merge path) — that only
  // holds while the current decision is actually Link (merge); Import/Leave out on the same
  // row means it never becomes one, so this must mirror actionFor exactly.
  const effectiveKind = (d: ImportPreviewDraft, i: number): 'expense' | 'income' | 'transfer' => {
    if (d.routed) return 'transfer'
    const resolved = resolveExisting(d)
    if (resolved && actionFor(d, i) === 'merge') {
      const flagType = resolvedFlagType(d, resolved)
      if (
        flagType === 'transfer_pair' ||
        flagType === 'transfer_leg_echo' ||
        resolved.summary.kind === 'transfer'
      )
        return 'transfer'
    }
    return d.kind === 'income' ? 'income' : 'expense'
  }
  const kindCount = (k: 'expense' | 'income' | 'transfer') =>
    preview
      ? preview.drafts.reduce((count, d, i) => count + (effectiveKind(d, i) === k ? 1 : 0), 0)
      : 0
  const kindPop = {
    expense: kindCount('expense'),
    income: kindCount('income'),
    transfer: kindCount('transfer'),
  }
  // A transfer has no category (its cell names the other account), so it never joins one —
  // the Type filter's Transfer finds those.
  const rowCategoryKey = (d: ImportPreviewDraft, i: number) =>
    effectiveKind(d, i) === 'transfer' ? null : d.category || UNCATEGORIZED_KEY
  const categoryPop: CategoryFilterOption[] = (() => {
    if (!preview) return []
    const counts = new Map<string, number>()
    preview.drafts.forEach((d, i) => {
      const key = rowCategoryKey(d, i)
      if (key) counts.set(key, (counts.get(key) || 0) + 1)
    })
    return Array.from(counts, ([key, n]) => ({
      key,
      n,
      label: key === UNCATEGORIZED_KEY ? 'Uncategorized' : catMapProp[key]?.label || key,
      color: key === UNCATEGORIZED_KEY ? 'var(--faint)' : catMapProp[key]?.color || 'var(--faint)',
    })).sort((a, b) =>
      a.key === UNCATEGORIZED_KEY
        ? -1
        : b.key === UNCATEGORIZED_KEY
          ? 1
          : b.n - a.n || a.label.localeCompare(b.label),
    )
  })()
  // The handoff keeps decision state visible at every list size; a one-row statement still
  // benefits from an explicit Left out / Needs you state instead of an implicit view.
  const chipsEnabled = !!preview
  // EFFECTIVE chip, derived (no effect — this sits below the early return): a selected chip
  // whose population empties out from under it (the last ledger-only row deleted while its
  // chip is active) falls back to All instead of stranding a hidden list.
  const chip: ChipKey =
    !chipsEnabled || (activeChip !== 'all' && chipPop[activeChip] === 0) ? 'all' : activeChip
  // Dynamic (outcome) counts — what happens if commit is clicked right now.
  const links = preview ? preview.drafts.filter((d, i) => willMerge(d, i)).length : 0
  const imports = preview ? preview.drafts.filter((d, i) => willCreate(d, i)).length : 0
  const awaitingDecisions = preview
    ? preview.drafts.reduce((count, d, i) => {
        const decided = !!queueDecided[d.rowIndex ?? i]
        return count + (belongsToDecisionQueue(d, i) && !decided ? 1 : 0)
      }, 0)
    : 0
  /** Toggle semantics (owner 2026-07-16, all banded rows): re-tapping the EXPLICITLY selected
   * chip clears the override so the row returns to its band default — the reverting headline +
   * After column is the feedback (a strong row's un-chipped merge default is reachable again
   * after Skip). A tap matching the band default with no override set still records it. */
  const toggleMatchAction = (i: number, a: MatchAction) => {
    const draft = preview?.drafts[i]
    if (!draft) return
    const ri = draft.rowIndex ?? i
    const clearsOverride = matchAction[ri] === a
    // Handoff `teach`: overriding one of Lucius's calls is the moment to ask whether there's a
    // rule behind it. Offered ONCE per import, and only on a real override (not on re-tapping
    // the already-selected default back off).
    if (!clearsOverride && teach === 'idle') setTeach('offer')
    setMatchAction((m) => {
      if (m[ri] === a) {
        const next = { ...m }
        delete next[ri]
        return next
      }
      return { ...m, [ri]: a }
    })
    if (pairBand(draft) === 'review') {
      setQueueDecided((current) => {
        if (clearsOverride) {
          const next = { ...current }
          delete next[ri]
          return next
        }
        return { ...current, [ri]: true }
      })
    }
  }
  /** Bulk-set EXACTLY the rows handed in — always the array the list under the bulk bar is
   * rendering, so what the bar moves is what the user is looking at. The bar's label is gone
   * (2026-07-31) but this signature stays: re-deriving a population here is the defect, and
   * without a number on screen nothing would catch it. Its predecessor DID re-derive one
   * (`effectiveChipKey === 'strong'`, i.e. everything that ends up linking) while the label
   * counted a narrower one (`pairBand === 'strong'`, a confident engine match only): the bar
   * said 18 and acted on 37, so "Import all
   * as new" would have booked 37 duplicates and "Skip all" discarded 19 decisions the user and
   * Lucius had already made (reported case, a Meridian Bank statement import).
   * Rows with an explicit outcome of their own already belong to Import / Left out, so they are
   * not in this list and a Will-link bulk action still cannot reach across the filter and
   * overwrite that financial decision. An FX leg folded into its partner transfer IS in the
   * list, but its outcome is owned by the pair's Include control rather than by a
   * link/import/leave-out action, so it keeps folding and keeps saying so on the row. */
  const setAllForRows = (rows: Array<{ d: ImportPreviewDraft; i: number }>, a: MatchAction) => {
    if (!preview || !rows.length) return
    setMatchAction((m) => {
      const next = { ...m }
      rows.forEach(({ d, i }) => {
        next[d.rowIndex ?? i] = a
      })
      return next
    })
    // A review-band row is decided by a bulk action exactly as by a per-row tap, so it leaves
    // the Needs-you queue the same way (mirrors toggleMatchAction).
    setQueueDecided((current) => {
      const next = { ...current }
      rows.forEach(({ d, i }) => {
        if (pairBand(d) === 'review') next[d.rowIndex ?? i] = true
      })
      return next
    })
    // Nothing linking survives the bulk action outside the rows it touched: commit the visual
    // fallback to state so that re-tapping one row cannot resurrect the stale Will-link filter
    // and hide the untouched siblings. A narrower bulk (a kind filter is on) leaves the filter
    // alone — there are still rows under it to see.
    const mutated = new Set(rows.map(({ d, i }) => d.rowIndex ?? i))
    const survivors = preview.drafts.some(
      (d, i) => !mutated.has(d.rowIndex ?? i) && effectiveChipKey(d, i) === 'strong',
    )
    if (a !== 'merge' && !survivors)
      setActiveChip((current) => (current === 'strong' ? 'all' : current))
  }
  // Waterfall/verdict jump: activate the term's chip (empty target falls back to All) and
  // scroll to the list. Below the chip gate there is nothing to filter — scroll only. When
  // the chip resolves to exactly one row, flash it too — scrolling to the top of a filtered
  // list of one doesn't read as "here it is" on its own.
  const jumpToChip = (k: Exclude<ChipKey, 'all'>) => {
    if (chipsEnabled) setActiveChip(chipPop[k] > 0 ? k : 'all')
    if (
      k !== 'offby' &&
      k !== 'ledger' &&
      k !== 'uncovered' &&
      k !== 'weak' &&
      chipPop[k] === 1 &&
      preview
    ) {
      const i = preview.drafts.findIndex((d, idx) => effectiveChipKey(d, idx) === k)
      if (i >= 0) {
        setFlashIndex(i)
        return
      }
    }
    tableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const stepIdx =
    step === 'source' || step === 'choose'
      ? 0
      : step === 'processing' || step === 'configure'
        ? 1
        : 2
  const progress = (
    <span className="imp3-crumbs" aria-label="Import progress">
      {['Choose', 'Read', 'Decide'].map((label, i) => (
        <span
          key={label}
          className={'imp3-crumb' + (i === stepIdx ? ' on' : '') + (i < stepIdx ? ' done' : '')}
        >
          <i aria-hidden="true" />
          <span>{label}</span>
        </span>
      ))}
    </span>
  )
  // The handoff is a focused review canvas, not an app-width manager. `xl` preserves its
  // 960px composition while the responsive rules collapse compare rows below that width.
  const modalSize = step === 'preview' ? 'workspace' : step === 'configure' ? 'lg' : 'md'

  // B20 processing-step render: `activeProgress` is null until the FIRST poll lands (or
  // forever, for an old server that doesn't answer the endpoint) — that absence is the
  // feature-detect, and the fallback is exactly today's fake-timer phrasing, untouched.
  // Reopening a statement by id (`resuming`) polls a DIFFERENT endpoint (by statement id,
  // never a client token) but the snapshot SHAPE is identical, so every render below reads
  // through this one unified trio regardless of which source it came from.
  const activeProgress = resuming ? (resumeSnapshot?.progress ?? null) : previewProgress
  const activeStale = resuming ? resumeStale : progressStale
  const activeElapsedS = resuming ? resumeElapsedS : elapsedS
  // Reopening a statement whose read is already finished only FETCHES its kept review — no
  // "Lucius is reading", no stage bar or elapsed clock left over from the finished read, no
  // "Continue in background". A resume the server still reports as `parsing` reads as before.
  const openingReview = resuming && !rereading && resumeSnapshot?.parseStatus !== 'parsing'
  // Back from a reopened statement's review: its own file, not an empty picker.
  const reopenChoose = resuming && !file
  const reopenSub = [
    resumeUploadedAt ? `Read ${fDate(resumeUploadedAt)}` : null,
    preview ? `${preview.total} row${preview.total === 1 ? '' : 's'}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  const liveStage = activeProgress?.stages[activeProgress.stages.length - 1] ?? null
  const readingPhase = activeProgress
    ? activeStale
      ? 'Still working — losing touch with the progress feed.'
      : progressPhaseLabel(
          liveStage?.stage || '',
          liveStage?.page,
          liveStage?.pages,
          liveStage?.label,
        )
    : READ_PHASES[Math.min(phase, READ_PHASES.length - 1)]
  const readingRetryText =
    liveStage && liveStage.stage === 'read' && liveStage.retry
      ? `Retrying page ${liveStage.page} (attempt ${liveStage.retry + 1} of 3)`
      : null
  const readingHint =
    !!liveStage &&
    liveStage.stage === 'read' &&
    !!liveStage.page &&
    Date.now() - Date.parse(liveStage.started_at) >= 90_000

  function renderFooter() {
    // Choose and Read carry no footer: the handoff puts the Choose step's CTA INSIDE the
    // pane (`imp3-drop-cta`), reaches "back" through the file row's own Change control, and
    // gives the Read step no controls at all — the Modal's ✕ is the way out of both.
    if (step === 'source' || step === 'choose' || step === 'processing') return null
    if (step === 'configure')
      return (
        <>
          <Button size="sm" variant="ghost" onClick={() => setStep('choose')}>
            Back
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!canManualPreview}
            onClick={runManualPreview}
          >
            Preview import
          </Button>
        </>
      )
    if (step === 'preview') {
      // Footer CTA grammar (§7.2): links lead, then imports — describes what commit actually
      // DOES, never a "still pending" count (needs-you rows default to skip, invisible here).
      const frags: string[] = []
      if (links > 0) frags.push(`link ${links}`)
      if (imports > 0) frags.push(`import ${imports}`)
      const deletes = markedForDelete.length
      // Staged deletions ARE an outcome: a statement with nothing to book but 8 marked
      // duplicates must still be confirmable, and must not read "Nothing to book".
      const nothingHappens = links === 0 && imports === 0 && deletes === 0
      const canFile = nothingHappens && !!preview?.statementId
      // This exact file is already filed and the review would write nothing: there is nothing
      // to confirm, so the footer offers Close instead of re-filing it (owner, 2026-10-06).
      const closeOnly = canFile && !!preview?.alreadyImported
      const leaveOuts = excludedRows.length
      const outcomeFrags = [
        links > 0 ? `Link ${links}` : null,
        imports > 0 ? `import ${imports}` : null,
        leaveOuts > 0 ? `leave out ${leaveOuts}` : null,
        awaitingDecisions > 0 ? `${awaitingDecisions} awaiting you` : null,
      ].filter((fragment): fragment is string => !!fragment)
      // Just "Confirm" — the footer note right next to it already spells out the
      // link/import/leave-out breakdown, so repeating it on the button itself was redundant.
      // Filing an all-left-out statement is a real production outcome the prototype has no
      // equivalent for, so it keeps its own honest label instead of reading as a no-op.
      const label =
        frags.length || deletes > 0
          ? 'Confirm'
          : closeOnly
            ? 'Close'
            : canFile
              ? 'File statement — nothing new'
              : 'Nothing to book'
      // ONE honest outcome sentence on the left, the commit CTA on the right — green (`ready`)
      // only once every row is decided, otherwise the primary blue.
      const ready = awaitingDecisions === 0 && !nothingHappens
      return (
        <div className="imp3-foot">
          <span className="imp3-foot-note">
            {closeOnly ? (
              <>
                <b>Already filed</b> — nothing new to book.
              </>
            ) : (
              <>
                <b>{outcomeFrags.join(' • ') || 'Nothing to book'}</b>
                {/* Deletions are DESTRUCTIVE and get their own emphasised term rather than another
                comma-separated fragment — buried in the run of "link 1 · import 3" it reads as
                one more bookkeeping outcome. "from your ledger" is load-bearing: it is what
                separates this from "leave out", which only means "don't import". */}
                {deletes > 0 && (
                  <>
                    {' • '}
                    <b className="imp3-foot-del">delete {deletes} from your ledger</b>
                  </>
                )}{' '}
                — nothing is written until you confirm.
                {awaitingDecisions > 0 && (
                  <span className="imp3-foot-wait">
                    {' '}
                    {awaitingDecisions} undecided row{awaitingDecisions === 1 ? '' : 's'} book the
                    shown default{awaitingDecisions === 1 ? '' : 's'}.
                  </span>
                )}
              </>
            )}
          </span>
          <button type="button" className="imp3-btn2" disabled={committing} onClick={requestBack}>
            Back
          </button>
          <button
            type="button"
            className={'imp3-commit' + (ready ? ' ready' : '')}
            disabled={(nothingHappens && !canFile) || committing}
            aria-busy={committing}
            onClick={closeOnly ? close : confirmImport}
          >
            {committing && <span className="imp3-commit-spin" aria-hidden="true" />}
            {committing ? 'Booking…' : label}
          </button>
        </div>
      )
    }
    return null
  }

  return (
    <>
      {/* Backdrop and drag dismissal are independently disabled: neither a stray click on the
        page BEHIND this modal nor a pull may end the import (owner, 2026-08-09: "a lot of times
        i misclicked"). An import holds work that cannot be recovered by reopening — a paid LLM
        read in flight, or a reviewed set of decisions not yet committed. Leaving is still
        available, but only through a deliberate control (✕ / Escape / Cancel), and those
        confirm via `requestClose`. */}
      <Modal
        open={open}
        onClose={requestClose}
        size={modalSize}
        title="Import a statement"
        subtitle={progress}
        footer={renderFooter()}
        closeOnBackdrop={false}
        dragToDismiss={false}
        className={fileStatementId && !isMobile && showFile ? 'imp3-modal--file' : undefined}
        headerActions={
          fileStatementId && isMobile ? (
            <SegmentedControl
              ariaLabel="Review or statement file"
              options={[
                { value: 'review', label: 'Review' },
                { value: 'file', label: 'File' },
              ]}
              value={phoneView}
              onChange={setPhoneView}
            />
          ) : undefined
        }
      >
        <div
          className={'imp' + (fileWanted && isMobile ? ' imp--behind-file' : '')}
          onClick={fileStatementId ? pointFileAtRow : undefined}
        >
          {error && (
            <div className="imp-error">
              <Ic d={I_WARN} size={14} /> {error}
            </div>
          )}

          {/* ── Choose, unpicked (handoff DropStep, `!picked`) ──────────────────────────
            The centred dropzone with a one-line format list, and the URL / Google-Sheet
            source as its own quiet control BELOW it — not a peer tab. */}
          {step === 'source' && (
            <div className="imp3-body">
              <button
                type="button"
                className={'imp3-drop' + (dragOver ? ' over' : '')}
                aria-label="Choose a statement file or drop it here"
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  const f = e.dataTransfer.files[0]
                  if (f) pickFile(f)
                }}
                onClick={() => fileRef.current?.click()}
              >
                <svg
                  width="26"
                  height="26"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="imp3-drop-ic"
                  aria-hidden="true"
                >
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <path d="M17 8l-5-5-5 5" />
                  <path d="M12 3v12" />
                </svg>
                <span className="imp3-drop-t">
                  Drop a statement here, or <span className="lk">browse</span>
                </span>
                <span className="imp3-drop-d">
                  PDF, CSV, XLSX, OFX, QIF, MT940, CAMT — scanned PDFs work too
                </span>
              </button>
              <input
                ref={fileRef}
                className="imp-file-input"
                type="file"
                accept={ACCEPT}
                tabIndex={-1}
                aria-label="Statement file"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) pickFile(f)
                  e.target.value = ''
                }}
              />
              {urlOpen ? (
                <div className="imp3-urlbox">
                  <input
                    type="url"
                    aria-label="Statement URL"
                    placeholder="https://docs.google.com/…/pub?output=csv"
                    value={url}
                    onChange={(e) => {
                      setUrl(e.target.value)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') loadUrl()
                    }}
                  />
                  <div className="imp3-urlbox-acts">
                    <span className="imp3-drop-cost">
                      A hosted CSV, OFX, QIF, MT940, CAMT, XLSX or PDF link.
                    </span>
                    <button
                      type="button"
                      className="imp3-btn2"
                      onClick={() => {
                        setUrlOpen(false)
                        setError('')
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="imp3-lubtn"
                      disabled={!url.trim()}
                      onClick={loadUrl}
                    >
                      Load
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="imp3-urllink"
                  onClick={() => {
                    setUrlOpen(true)
                    setError('')
                  }}
                >
                  or paste a URL / Google Sheet link
                </button>
              )}
            </div>
          )}

          {/* ── Choose, picked (handoff DropStep, `picked`) ─────────────────────────── */}
          {step === 'choose' && (
            <div className="imp3-body">
              {reopenChoose ? (
                <div className="imp3-file">
                  <span className="imp3-file-badge">
                    {FORMAT_LABEL[detectFormat(resumeFilename || '')]}
                  </span>
                  <div>
                    <div className="imp3-file-nm">{resumeFilename || 'Statement'}</div>
                    {reopenSub && <div className="imp3-file-sub">{reopenSub}</div>}
                  </div>
                </div>
              ) : (
                <div className="imp3-file">
                  <span className="imp3-file-badge">{FORMAT_LABEL[format]}</span>
                  <div>
                    <div className="imp3-file-nm">{file ? file.name : url}</div>
                    {!!fileSize && <div className="imp3-file-sub">{fileSize}</div>}
                  </div>
                  <button
                    type="button"
                    className="imp3-file-x"
                    onClick={() => {
                      setStep('source')
                      setUrlOpen(!file)
                    }}
                  >
                    Change
                  </button>
                </div>
              )}
              {/* Checking whether this source's format is remembered — this box used to render
                nothing at all while the probe was in flight, so the "I know this source"
                banner appeared to pop in from nowhere. Same box shape as the resolved banner
                below, so there's no layout jump once it resolves. */}
              {peekState === 'pending' && !reopenChoose && (
                <div className="imp3-known pending" role="status" aria-live="polite">
                  <span className="imp3-known-spin" aria-hidden="true" />
                  <div className="imp3-known-t">Checking if this source is remembered…</div>
                </div>
              )}
              {sourceKnown && !reopenChoose && (
                <div className="imp3-known">
                  <span className="imp3-known-ic">
                    <Spark />
                  </span>
                  {/* The prototype's copy, verbatim. The peek also returns the profile's own
                    format label, but it is a raw column-header string ("STT No. · Ngày/TNX
                    Date") that reads as noise here — and on the Decide step's LuciusNote too
                    (2026-07-27 fix: dropped there as well, not just here). */}
                  <div className="imp3-known-t">
                    <b>I know this source.</b> Layout is remembered.
                  </div>
                  <label
                    className="imp3-known-cb"
                    title="Forget everything remembered about this source for this run: re-detect the format from scratch AND re-read the file instead of reusing the cached parse."
                  >
                    <input
                      type="checkbox"
                      checked={fresh}
                      onChange={(e) => setFresh(e.target.checked)}
                    />{' '}
                    Re-read from scratch
                  </label>
                </div>
              )}
              {/* The probe could not answer (it failed, or was never wired), so we do not know
                whether this source is remembered. The banner would be a false claim, but
                withholding its checkbox would take the ONLY control that sets `force_relearn`
                — the one escape from a stuck format profile — away over a network hiccup. */}
              {peekState === 'unavailable' && !reopenChoose && (
                <label
                  className="imp3-remember"
                  title="Forget everything remembered about this source for this run: re-detect the format from scratch AND re-read the file instead of reusing the cached parse."
                >
                  <input
                    type="checkbox"
                    checked={fresh}
                    onChange={(e) => setFresh(e.target.checked)}
                  />{' '}
                  Re-read from scratch
                </label>
              )}
              <div className="imp3-cfg">
                <div className="imp3-cfg-row">
                  <span className="imp3-cfg-l">
                    Into accounts
                    <em>a statement can cover several — each row routes to the right one</em>
                  </span>
                  <div className="imp3-acctchips">
                    {acctIds.map((id) => {
                      const a = accounts.find((x) => x.id === id)
                      if (!a) return null
                      return (
                        <span key={id} className="imp3-achip">
                          {a.name}
                          <button
                            type="button"
                            aria-label={`Remove ${a.name}`}
                            onClick={() => removeAccount(id)}
                          >
                            <X size={11} aria-hidden="true" />
                          </button>
                        </span>
                      )
                    })}
                    {accounts.some((a) => !acctIds.includes(a.id)) && (
                      <span className="imp3-acctpick" ref={acctPickRef}>
                        <button
                          type="button"
                          className="imp3-achip add"
                          onClick={() => setAcctPickOpen(!acctPickOpen)}
                        >
                          {acctIds.length ? (
                            <>
                              <Plus size={12} aria-hidden="true" /> Add account
                            </>
                          ) : (
                            'Select account'
                          )}
                        </button>
                        {acctPickOpen && (
                          <div className="imp3-lpick-pop" role="listbox" aria-label="Accounts">
                            {accounts
                              .filter((a) => !acctIds.includes(a.id))
                              .map((a) => (
                                <button
                                  type="button"
                                  key={a.id}
                                  role="option"
                                  aria-selected="false"
                                  onClick={() => addAccount(a.id)}
                                >
                                  {a.name}
                                  <span className="imp3-acct-ccy">{a.currency}</span>
                                </button>
                              ))}
                          </div>
                        )}
                      </span>
                    )}
                  </div>
                </div>
                {/* The prototype spells this row `imp3-cfg-row top`; `top` is vestigial — no
                  rule anywhere in import3.css backs it (a leftover from an earlier two-column
                  layout), and carrying it would mean allowlisting a generic global token. */}
                <div className="imp3-cfg-row">
                  <span className="imp3-cfg-l">
                    Tell Lucius
                    <em>
                      bank quirks, context, corrections — these make the read noticeably better
                    </em>
                  </span>
                  <div className="imp3-cfg-note">
                    <textarea
                      ref={instrTextareaRef}
                      rows={3}
                      maxLength={1000}
                      aria-label="Tell Lucius"
                      placeholder={
                        'e.g. Rows with MOMO.0352 are transfers to my wallet, never expenses.\nDates are DD/MM. Amounts in the ‘Foreign’ column are the original currency, not SGD.'
                      }
                      value={instructions}
                      onChange={(e) => {
                        instrTyped.current = true
                        setInstrPrefilled(false)
                        setInstructions(e.target.value)
                      }}
                    />
                    {/* The prototype's own `imp3-cache-hint` slot, directly under the textarea —
                      only the ported "changed" notice; owner directive 2026-07-27 dropped the
                      production-only "pre-filled" notice and the SI-68 honesty caption below it
                      as unnecessary text beyond the handoff. */}
                    {sourceKnown &&
                      !instrPrefilled &&
                      instructions.trim() !== savedInstructions.trim() &&
                      !fresh && (
                        <div className="imp3-cache-hint">
                          <Spark /> Instructions changed — Lucius processes the file again with
                          these.
                        </div>
                      )}
                    <label
                      className="imp3-remember"
                      title="Checked: saved on this source's format profile and auto-applied to every future import of it. Unchecked: applies to this import only."
                    >
                      <input
                        type="checkbox"
                        checked={rememberInstr}
                        onChange={(e) => setRememberInstr(e.target.checked)}
                      />{' '}
                      Remember for this source
                    </label>
                  </div>
                </div>
              </div>
              {/* REMOVED 2026-07-27 (owner directive — match the handoff exactly, no affordance
                the prototype has no slot for). This was the ONLY entry point into the
                `configure` step (manual column mapping, SI-65) — that step's code, tests
                (`ImportModal.manualRoute.test.tsx`), and `runManualPreview`/`canManualPreview`
                are left in place, now UI-unreachable, pending a separate decision on whether to
                restore reachability or delete the feature outright. See
                `docs/adr/2026-07-27-statement-import-drop-account-auto-detect.md`'s follow-up
                note and `frontend/design_handoffs/statement_import_handoff/PARITY_AND_GAPS.md`
                deviation #9. */}
              {reopenChoose ? (
                <div className="imp3-drop-cta imp3-reopen-acts">
                  <Button
                    variant="secondary"
                    disabled={!acctIds.length || rereading}
                    onClick={readAgain}
                  >
                    {rereading ? 'Starting…' : 'Read again'}
                  </Button>
                  <Button variant="primary" disabled={!preview} onClick={() => setStep('preview')}>
                    Back to review
                  </Button>
                </div>
              ) : (
                <div className="imp3-drop-cta">
                  <span className="imp3-drop-cost">You review everything before it books</span>
                  <Button
                    variant="primary"
                    className="fi-lucius"
                    iconLeft={<LuciusSpark size={15} ariaHidden />}
                    disabled={!acctIds.length}
                    onClick={() => runLucius()}
                  >
                    Read the statement
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* ── Read (handoff ReadStep) ─────────────────────────────────────────────── */}
          {step === 'processing' && resuming && reopenFailed && (
            <div className="imp3-body imp3-reading" role="alert">
              <div className="imp3-reading-t">
                Couldn’t open <b>{resumeFilename || 'this statement'}</b>
              </div>
              <div className="imp3-phase">
                {reopenFailed.kind === 'account'
                  ? 'Which account is this statement for? Nothing is re-read — the review opens for it.'
                  : reopenFailed.kind === 'read'
                    ? 'Its read didn’t finish. Re-run it from the account’s Statements list.'
                    : 'The review didn’t load. Nothing was lost — try again.'}
              </div>
              {reopenFailed.kind === 'account' && (
                <div className="imp3-reopen-acct">
                  <FormField label="Account">
                    <Select
                      options={[{ value: '', label: 'Choose an account' }, ...acctOpts]}
                      value={reopenAccount}
                      onChange={(e) => setReopenAccount(e.target.value)}
                    />
                  </FormField>
                </div>
              )}
              {reopenFailed.kind === 'read' ? (
                <Button variant="secondary" size="sm" onClick={close}>
                  Close
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  disabled={reopenFailed.kind === 'account' && !reopenAccount}
                  onClick={() =>
                    void resumeReopen(activeResumeId as string, reopenAccount || undefined)
                  }
                >
                  {reopenFailed.kind === 'account' ? 'Open review' : 'Try again'}
                </Button>
              )}
            </div>
          )}
          {step === 'processing' && !(resuming && reopenFailed) && (
            <div className="imp3-body imp3-reading">
              <div className="imp3-orb">
                <Spark />
              </div>
              <div className="imp3-reading-t">
                {openingReview ? 'Opening' : 'Lucius is reading'}{' '}
                <b>{file ? file.name : resuming ? resumeFilename || 'your file' : 'your file'}</b>
              </div>
              <div
                className={'imp3-phase' + (activeStale ? ' imp3-phase--stale' : '')}
                role="status"
              >
                {openingReview ? <OpeningReviewLine /> : readingPhase}
              </div>
              {/* Live stage bar, elapsed clock, retry/hint lines — all feature-detected on
                  `activeProgress`: an old server that never answers the progress endpoint
                  leaves this whole block unrendered, and the phase line above is all there
                  is, exactly like before B20. */}
              {activeProgress && !openingReview ? (
                <>
                  <div
                    className="imp3-stagebar"
                    role="img"
                    aria-label={`Progress: ${readingPhase}`}
                  >
                    {PROGRESS_STAGE_ORDER.map((s) => (
                      <span
                        key={s}
                        className={'seg ' + progressStageStatus(activeProgress, s)}
                        title={progressPhaseLabel(s, null, null)}
                      />
                    ))}
                  </div>
                  {readingRetryText && <div className="imp3-retry">{readingRetryText}</div>}
                  <div className="imp3-elapsed">{formatElapsed(activeElapsedS)} elapsed</div>
                  {readingHint && (
                    <div className="imp3-hint">
                      This page is taking longer than usual — still working.
                    </div>
                  )}
                </>
              ) : null}
              {!openingReview && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="imp3-bgbtn"
                  disabled={bgPending}
                  onClick={() => continueInBackground()}
                >
                  {bgPending ? 'Continuing…' : 'Continue in background'}
                </Button>
              )}
            </div>
          )}

          {step === 'configure' && (
            <div className="imp-pane">
              <div className="imp-srcline">
                <span className="imp-srcic">
                  <Ic d={I_FILE} size={15} />
                </span>
                <span className="imp-srcname">{file ? file.name : url}</span>
                <span className="imp-srcfmt">{FORMAT_LABEL[format]}</span>
                <button className="imp-srcchange" onClick={() => setStep('choose')}>
                  Back
                </button>
              </div>
              <FormField label="Import into account">
                <ImpSelect
                  options={acctOpts}
                  value={accountId}
                  onChange={setAnchorAccount}
                  placeholder="Choose an account…"
                  ariaLabel="Import into account"
                />
              </FormField>
              {needsMapping(format) && columns.length ? (
                <>
                  <div className="imp-maphead">
                    <span className="imp-maphead-t">Map columns</span>
                    <Button
                      size="sm"
                      variant="primary"
                      className="fi-lucius"
                      iconLeft={<LuciusSpark size={15} ariaHidden />}
                      disabled={!accountId}
                      onClick={() => runLucius()}
                    >
                      Let Lucius do it instead
                    </Button>
                  </div>
                  <div className="imp-map">
                    <FormField label="Date column">
                      <Select
                        options={colOpts}
                        value={map.date}
                        onChange={(e) => setMap((m) => ({ ...m, date: e.target.value }))}
                      />
                    </FormField>
                    <FormField label="Description column">
                      <Select
                        options={colOpts}
                        value={map.description}
                        onChange={(e) => setMap((m) => ({ ...m, description: e.target.value }))}
                      />
                    </FormField>
                  </div>
                  <FormField label="Amount">
                    <div className="imp-amtmode">
                      <button
                        className={'imp-seg' + (amountMode === 'single' ? ' on' : '')}
                        onClick={() => setAmountMode('single')}
                      >
                        Single column
                      </button>
                      <button
                        className={'imp-seg' + (amountMode === 'split' ? ' on' : '')}
                        onClick={() => setAmountMode('split')}
                      >
                        Inflow / outflow
                      </button>
                    </div>
                  </FormField>
                  {amountMode === 'single' ? (
                    <FormField label="Amount column">
                      <Select
                        options={colOpts}
                        value={map.amount}
                        onChange={(e) => setMap((m) => ({ ...m, amount: e.target.value }))}
                      />
                    </FormField>
                  ) : (
                    <div className="imp-map">
                      <FormField label="Money in (inflow)">
                        <Select
                          options={colOpts}
                          value={map.inflow}
                          onChange={(e) => setMap((m) => ({ ...m, inflow: e.target.value }))}
                        />
                      </FormField>
                      <FormField label="Money out (outflow)">
                        <Select
                          options={colOpts}
                          value={map.outflow}
                          onChange={(e) => setMap((m) => ({ ...m, outflow: e.target.value }))}
                        />
                      </FormField>
                    </div>
                  )}
                  <div className="imp-map">
                    <FormField label="Category column (optional)">
                      <Select
                        options={colOpts}
                        value={map.category}
                        onChange={(e) => setMap((m) => ({ ...m, category: e.target.value }))}
                      />
                    </FormField>
                    <FormField label="Status column (optional)">
                      <Select
                        options={colOpts}
                        value={map.status}
                        onChange={(e) => setMap((m) => ({ ...m, status: e.target.value }))}
                      />
                    </FormField>
                  </div>
                  {map.status && (
                    <div className="imp-lucard-note">
                      Rows whose status differs from this column&apos;s most common value (e.g. a
                      failed or reversed transaction) are skipped automatically.
                    </div>
                  )}
                </>
              ) : (
                <div className="imp-structured">
                  <span className="imp-structured-ic">
                    <Ic d={I_CHECK} size={16} sw={2.4} />
                  </span>
                  <div>
                    <div className="imp-structured-t">
                      {FORMAT_LABEL[format]} — no column mapping
                    </div>
                    <div className="imp-structured-d">
                      {needsMapping(format)
                        ? 'Couldn’t read columns from this file directly.'
                        : FORMAT_LABEL[format] + ' is a structured format.'}
                    </div>
                    <Button
                      size="sm"
                      variant="primary"
                      className="fi-lucius"
                      style={{ marginTop: 10 }}
                      iconLeft={<LuciusSpark size={15} ariaHidden />}
                      disabled={!accountId}
                      onClick={() => runLucius()}
                    >
                      Import with Lucius instead
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {step === 'preview' &&
            preview &&
            (() => {
              const lc = preview.ledgerCheck
              // A manually-linked entry is accounted for — drop it from the boundary bucket too
              // (same reasoning as the ledgerOnlyRows drop above), so it never shows as an
              // unaccounted near-the-cut-off row after the user linked it.
              const boundary = ((lc?.boundary || []) as LedgerCheckRow[]).filter(
                (r) => !manualLinkedIds.has(r.id) && !matchOverrideIds.has(r.id),
              )
              type Totals = {
                account_name?: string
                currency?: string
                ledger_net: number
                statement_net: number
                statement_unbooked_net: number
                uncovered_net: number
                boundary_net: number
                drift: number
                tie: boolean
              }
              const t0 = ((lc as unknown as { totals?: Totals[] })?.totals || [])[0]
              const signedCur = (v: number) =>
                (v >= 0 ? '+' : '−') + fCur(Math.abs(v), t0?.currency || 'SGD', { cents: true })
              // Manual-link option lists (owner 2026-07-18). Forward: for a New statement row, every
              // uncovered entry (rows + boundary via checkRowById) not deleted from this screen —
              // rendered via existingTxnLine with the note prepended so the user recognizes it (and
              // can filter on it); a weak twin or another row's manual link already holding it shows
              // dimmed + unpickable. Reverse: the UNMATCHED New rows a "not on statement" entry can be
              // linked to. `id` is the entry id (forward) / the draft rowIndex (reverse).
              const manualLinkOptions = (d: ImportPreviewDraft) =>
                Array.from(checkRowById.values())
                  .filter((r) => !pendingDelete[r.id])
                  .map((r) => ({
                    id: r.id,
                    line:
                      (r.note ? r.note + ' ' : '') +
                      existingTxnLine(r, accounts, catMapProp, d.currency, {
                        anchorAccountId: d.destId || accountId,
                      }),
                    taken: manualLinkedIds.has(r.id) || matchOverrideIds.has(r.id),
                  }))
              // Rows already linked are offered too (owner, 2026-10-26): picking one MOVES its link
              // here after a confirm, and the entry it held goes back to "not on this statement".
              const linkedTo = (d: ImportPreviewDraft, i: number) => {
                const resolved = resolveExisting(d)
                if (!resolved || actionFor(d, i) !== 'merge') return null
                const amt = resolved.summary.amount
                return (
                  (resolved.summary.note || 'another entry') +
                  (amt != null
                    ? ` (${fCur(amt, resolved.summary.currency || d.currency, { cents: true })})`
                    : '')
                )
              }
              const linkableDrafts = preview.drafts
                .map((d, i) => ({ d, i }))
                .filter(({ d, i }) => linkableShape(d, i))
                .map(({ d, i }) => {
                  const original = originalCharge(d, d.currency)
                  const holds = linkedTo(d, i)
                  return {
                    id: String(d.rowIndex),
                    line:
                      `${d.date.slice(5)} • ${rowTitle(d)} • ${fCur(d.amount, d.currency, { cents: true })}` +
                      (original ? ` (${original})` : ''),
                    taken: false,
                    hint: holds ? `• linked to ${holds}` : undefined,
                    holds,
                    title: `${rowTitle(d)} (${fCur(d.amount, d.currency, { cents: true })})`,
                  }
                })
              // Row FORM (unchanged from the sectioned build): a dup (L0), an FX-pair leg (its
              // fold/echo controls need the partner-row context of the table), or an unmatched row
              // keeps the plain table rendering; everything else with a resolvable counterpart
              // renders the full pair-row anatomy. Rendered as ONE flat list in the STATEMENT'S
              // OWN ORDER (owner directive 2026-07-16): parse order = rowIndex on server previews,
              // array order on client-mapped ones — never regrouped into Matched/New sections.
              const withIdx = preview.drafts.map((d, i) => ({ d, i }))
              const ordered = withIdx
                .slice()
                .sort((a, b) => (a.d.rowIndex ?? a.i) - (b.d.rowIndex ?? b.i))
              const isPairDraft = (d: ImportPreviewDraft, i: number) =>
                !d.dup &&
                pairBand(d) !== 'none' &&
                (!d.pair || d.pair.role === 'out' || !isAbsorbedLeg(d, i))
              const openWeakIndices = new Set(openWeakRows.map(({ i }) => i))
              const byDecisionChip =
                chip === 'all'
                  ? ordered
                  : chip === 'ledger'
                    ? []
                    : chip === 'uncovered'
                      ? []
                      : chip === 'offby'
                        ? ordered.filter(({ i }) => driftByIndex.has(i))
                        : // A LENS, not a partition — statement order preserved, nothing else shown.
                          chip === 'weak'
                          ? ordered.filter(({ i }) => openWeakIndices.has(i))
                          : ordered.filter(({ d, i }) => effectiveChipKey(d, i) === chip)
              const visibleStmt = byDecisionChip.filter(
                ({ d, i }) =>
                  (kindFilter === 'all' || effectiveKind(d, i) === kindFilter) &&
                  (categoryFilter === 'all' || rowCategoryKey(d, i) === categoryFilter),
              )
              const showLedgerOnly = chip === 'all' || chip === 'ledger'
              // Its own dedicated section only under its OWN chip — not under All too, unlike the
              // ledger-only section — because (unlike a ledger row) an uncovered row already has a
              // home in the main All list; showing it a second time there would duplicate the row
              // AND make it jump between two DOM locations the instant a decision resolves it
              // (e.g. Import), which broke mid-interaction element references.
              const showUncovered = chip === 'uncovered'
              // Ledger-only taxonomy — TWO cases, decided by ID EQUALITY, never by matching
              // (RFC 2026-07-29 §D3 amendment (d)). `booked` = some statement row in THIS preview
              // is an L0 duplicate OF THIS ENTRY (`dupOf` is already on the wire), i.e. an earlier
              // import already booked it; everything else is an orphan. The old third case
              // ("claimed" — a possible match is linked above, resolve it there first) is gone by
              // construction: a claimed entry is no longer in `ledger_check.rows` at all, and the
              // claiming row now carries a real, actionable weak-band pair card of its own.
              const ledgerOnlyCase = (r: LedgerCheckRow): 'booked' | 'orphan' =>
                preview.drafts.some((d) => d.dup && d.dupOf === r.id) ? 'booked' : 'orphan'
              const renderLedgerOnlyRow = (r: LedgerCheckRow) => {
                const kase = ledgerOnlyCase(r)
                const explanation =
                  kase === 'booked'
                    ? 'Probably a duplicate — your ledger already has this from an earlier import.'
                    : r.kind === 'transfer'
                      ? 'A transfer between your own accounts — it may simply be recorded on the other account’s statement. Open it to check both legs before deleting.'
                      : 'A real card charge should appear on its own statement — if it’s not on the next one either, it’s likely a double-booking. Open it to inspect.'
                const opening = !!drawerLoading[r.id]
                const expanded = !!ledgerExpanded[r.id]
                // Which of YOUR accounts holds this entry. It held the closed line's one free line
                // until 2026-07-31, when the owner asked for "the description + any statement that
                // these transactions already belong to" — and `origin` won that slot: the routed
                // account is the SAME string on every row of a single-account import (zero
                // information), while the origin is what differs per row and what the owner is
                // actually asking. The account keeps its place in the expanded body, where it still
                // earns its space on a multi-account reconciliation. An unresolvable account is
                // OMITTED rather than shown as a raw id — the same degradation `existingTxnLine`
                // already uses for this projection.
                const acctName = r.account_id
                  ? accounts.find((a) => a.id === r.account_id)?.name
                  : undefined
                // A transfer keeps its own identity in the category cell and NEVER reads
                // "Uncategorized" (owner, 2026-07-17) — same treatment as the pair/plain rows.
                const transferIdentity = transferLine(r, accounts, catMapProp)
                const catLabel =
                  transferIdentity ??
                  (r.category ? catMapProp[r.category]?.label || r.category : 'Uncategorized')
                /* `acct-stmtview-check-row` (accounts.css) is a legacy FLAT one-line row —
               `display: flex; align-items: center` — meant for StatementsSection's own
               summary rows and the boundary rows below (both non-expandable). This row has
               an expandable `.imp3-arow-body` (hint + actions) like every other disclosure
               row; carrying that legacy class too forced the header line AND the body to
               lay out as flex SIBLINGS instead of stacking, shoving the body into a narrow,
               vertically-centered column on the right. `.imp3-arow` alone (block layout, its
               own bottom border) is the correct — and, elsewhere, the ONLY — class an
               expandable row needs. */
                const marked = !!pendingDelete[r.id]
                return (
                  <article key={r.id} className={'imp3-arow' + (marked ? ' marked-delete' : '')}>
                    <button
                      type="button"
                      className="imp3-arow-line"
                      aria-expanded={expanded}
                      aria-label={r.note || 'Ledger transaction'}
                      onClick={() =>
                        setLedgerExpanded((current) => ({ ...current, [r.id]: !current[r.id] }))
                      }
                    >
                      <span className="imp3-arow-date">{r.date?.slice(5) || '—'}</span>
                      {/* No native `title` on the name: the browser's own tooltip covered the row
                      beneath it on every truncated description (owner, 2026-07-31). The name
                      ellipsizes cleanly instead; the FULL text is still announced — it is this
                      row's aria-label — and the entry itself opens from the body below. */}
                      <span className="imp3-arow-main">
                        <span className="imp3-arow-name">{r.note || '—'}</span>
                        {/* The row's ONE free line answers a real production question — "which
                        statement is this already ON?" (2026-07-31). Resolved SERVER-SIDE from
                        `external_refs` (services/statements/ledger_statement_links.py) so this
                        line and the `import_transactions` tool surface say the same words; the
                        client neither looks an id up nor phrases anything, "+N more" included.
                        It is deliberately NOT batch-derived: `batch_id` answers where the
                        row came FROM, so it rendered "Cirrus AI USD invoices 2 Jun – 10 Jun
                        2026" for a row whose statement was CardStatement_Jun2026 — and it is
                        blind to every row a statement LINKED rather than created. Null → no
                        line. */}
                        {r.on_statements && (
                          <span className="imp3-arow-desc">{r.on_statements.label}</span>
                        )}
                      </span>
                      <span className="imp3-arow-status">
                        {/* A marked row states its STAGED outcome where its status normally reads —
                        the same slot every other row uses to say what confirm will do to it. */}
                        {marked ? (
                          <span className="imp3-dchip willdelete">Will delete</span>
                        ) : keptLedger[r.id] ? (
                          <span className="imp3-dchip keep">Will keep</span>
                        ) : (
                          <span className="imp3-dchip ledger">Ledger only</span>
                        )}
                        {/* "Not on this statement" usually means "it's on the adjacent one" — this
                        chip is what tells those two apart. Server-decided edge + distance; the
                        client renders the label verbatim. */}
                        {r.near_cutoff && (
                          <span className="imp3-dchip cutoff">{r.near_cutoff.label}</span>
                        )}
                      </span>
                      <span className="imp3-arow-catcol">
                        <span
                          className={'imp3-cat' + (!r.category && !transferIdentity ? ' none' : '')}
                        >
                          <i
                            style={{
                              background: transferIdentity
                                ? 'var(--muted)'
                                : catMapProp[r.category || '']?.color || 'var(--faint)',
                            }}
                          />
                          <span>{catLabel}</span>
                        </span>
                      </span>
                      <span className="imp3-arow-amtwrap">
                        <b className="imp3-arow-amt">
                          {r.amount != null
                            ? fCur(r.amount, r.currency || 'SGD', { cents: true })
                            : '—'}
                        </b>
                        {(() => {
                          const o = originalCharge(r, r.currency || 'SGD')
                          return o ? (
                            <span
                              className="imp3-orig"
                              title="Original charge — before currency conversion"
                            >
                              {o}
                            </span>
                          ) : null
                        })()}
                      </span>
                      <svg
                        className={'imp3-chev' + (expanded ? ' open' : '')}
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </button>
                    {expanded && (
                      <div className="imp3-arow-body">
                        {/* Handoff `LedgerOnlyRow`: the same row anatomy as every other row — the
                      closed line collapses, the body holds the hint plus the actions, all in
                      the one CTA family. */}
                        {/* ONE line: description • account • statement (• its bank reference) — owner,
                      2026-07-31: "show description, account, and externalrefs (only if it's a
                      statement) on one row together!". It replaced a three-row label/value grid
                      whose "Already on" row fired for non-statements and whose "Description" row
                      restated a name that was already legible above.

                      The description term is CONDITIONAL, and it is the only one that is: the
                      other two are absent from the closed line entirely, while the description is
                      already the name above and only worth restating when that copy was clipped.
                      `.imp3-arow-name` is a hard ellipsis on a fixed 190px rail with no tooltip
                      left, so a long descriptor is genuinely unreadable up there and a short one
                      genuinely is not — hence a width budget rather than a DOM measurement (which
                      would be a layout re-derivation, and unmeasurable in jsdom anyway). */}
                        {(() => {
                          const desc = (r.note || '').length > NAME_RAIL_CHARS ? r.note : null
                          const parts = [
                            desc,
                            acctName,
                            r.on_statements?.label,
                            r.on_statements?.reference ? `ref ${r.on_statements.reference}` : null,
                          ].filter(Boolean)
                          return parts.length ? (
                            <div className="imp3-lc-facts">{parts.join(' • ')}</div>
                          ) : null
                        })()}
                        {/* A marked row replaces its hint with what confirm will DO — the hint reasons
                      about whether to act, and that question is answered once marked. */}
                        <div className="imp3-lc-hint">
                          {marked
                            ? 'Marked — this will be deleted from your ledger when you confirm.'
                            : explanation}
                        </div>
                        <div className="imp3-lc-acts">
                          <button
                            type="button"
                            className="imp3-lc-btn imp"
                            disabled={opening}
                            onClick={() => openExistingTxn(r.id)}
                          >
                            Open{opening ? '…' : ''}
                          </button>
                          {!marked && linkableDrafts.length > 0 && (
                            <LinkPicker
                              label="Link to a new row"
                              listLabel="Link a statement row to this entry"
                              options={linkableDrafts}
                              onPick={(idx) => {
                                const rowIndex = Number(idx)
                                const draftIndex = preview.drafts.findIndex(
                                  (draft) => draft.rowIndex === rowIndex,
                                )
                                const option = linkableDrafts.find((o) => o.id === idx)
                                const link = () => {
                                  // A row that carries an engine match resolves THAT first, so a
                                  // manual link would be ignored — re-point its match instead.
                                  const picked = draftIndex >= 0 ? preview.drafts[draftIndex] : null
                                  if (picked && matchFlag(picked)?.existing_id)
                                    setMatchOverride((current) => ({
                                      ...current,
                                      [rowIndex]: { id: r.id, summary: r },
                                    }))
                                  else
                                    setManualLink((current) => ({
                                      ...current,
                                      [rowIndex]: { id: r.id, summary: r },
                                    }))
                                  if (draftIndex >= 0) {
                                    setPlainSkip((current) => {
                                      const next = { ...current }
                                      delete next[rowIndex]
                                      return next
                                    })
                                    setMatchAction((current) => ({
                                      ...current,
                                      [rowIndex]: 'merge',
                                    }))
                                    setQueueDecided((current) => ({ ...current, [rowIndex]: true }))
                                  }
                                }
                                if (option?.holds)
                                  setRelinkAsk({
                                    row: option.title,
                                    from: option.holds,
                                    to:
                                      (r.note || 'this entry') +
                                      (r.amount != null
                                        ? ` (${fCur(r.amount, r.currency || account?.currency || 'SGD', { cents: true })})`
                                        : ''),
                                    run: link,
                                  })
                                else link()
                              }}
                            />
                          )}
                          {/* Undo sits exactly where Delete was, in the same slot and the same button
                        family as every other staged decision's undo on this screen — one tap
                        back, no dialog, because nothing has happened yet. */}
                          {marked ? (
                            <button
                              type="button"
                              className="imp3-lc-btn"
                              onClick={() => toggleDeleteMark(r.id)}
                            >
                              Undo
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="imp3-lc-btn danger"
                              onClick={() => toggleDeleteMark(r.id)}
                            >
                              Delete
                            </button>
                          )}
                          {!marked &&
                            (keptLedger[r.id] ? (
                              <button
                                type="button"
                                className="imp3-lc-btn"
                                onClick={() =>
                                  setKeptLedger((current) => {
                                    const next = { ...current }
                                    delete next[r.id]
                                    return next
                                  })
                                }
                              >
                                Undo
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="imp3-lc-btn"
                                onClick={() => {
                                  setKeptLedger((current) => ({ ...current, [r.id]: true }))
                                  setLedgerExpanded((current) => ({ ...current, [r.id]: false }))
                                }}
                              >
                                Leave it
                              </button>
                            ))}
                        </div>
                        {drawerFailed[r.id] && (
                          <div className="acct-stmtview-check-err">Couldn’t open — try again.</div>
                        )}
                      </div>
                    )}
                  </article>
                )
              }
              // Boundary bucket (SI-51): account · category · which statement it is already on ·
              // how near the cut-off — date/amount already have their own columns below, so they're
              // not repeated here. Pure visibility: no twin, no verdict, no action button.
              // The last two terms are SERVER strings (`on_statements.label`, `near_cutoff.label`)
              // and replace the old `existingSourcePhrase(r.source)`, which spent the line on the
              // provenance vocabulary ("ai"/"agent") instead of naming the statement the entry is
              // already on — the whole point of the bucket (owner, 2026-07-31). Both terms drop out
              // when the server has no answer, rather than degrading to a guess.
              const boundaryDetail = (r: LedgerCheckRow) => {
                const acctName = r.account_id
                  ? accounts.find((a) => a.id === r.account_id)?.name
                  : undefined
                // A transfer row reads "Transfer" (degraded — a boundary row matched nothing, so
                // there is no statement side to name its counter-leg from), never "Uncategorized".
                const catLabel =
                  transferLine(r, accounts, catMapProp) ??
                  (r.category ? catMapProp[r.category]?.label || r.category : 'Uncategorized')
                return [acctName, catLabel, r.on_statements?.label, r.near_cutoff?.label]
                  .filter(Boolean)
                  .join(' • ')
              }
              const renderBoundaryRow = (r: LedgerCheckRow) => {
                const opening = !!drawerLoading[r.id]
                return (
                  <div key={r.id} className="acct-stmtview-check-row">
                    <span className="nums">{r.date}</span>
                    <span className="acct-stmtview-check-note">
                      <button
                        type="button"
                        className="imp-txn-link"
                        disabled={opening}
                        onClick={() => openExistingTxn(r.id)}
                      >
                        {r.note || '—'}
                        {opening ? ' • opening…' : ''}
                      </button>
                      <div className="imp-desc-sub">{boundaryDetail(r)}</div>
                      {drawerFailed[r.id] && (
                        <div className="acct-stmtview-check-err">Couldn’t open — try again.</div>
                      )}
                    </span>
                    <span className="imp-uncat">{r.source || r.kind || 'row'}</span>
                    <span className="nums">
                      {r.amount != null ? fCur(r.amount, r.currency || 'SGD', { cents: true }) : ''}
                      {(() => {
                        const o = originalCharge(r, r.currency || 'SGD')
                        return o ? (
                          <span
                            className="imp3-orig"
                            title="Original charge — before currency conversion"
                          >
                            {o}
                          </span>
                        ) : null
                      })()}
                    </span>
                  </div>
                )
              }
              // Collapsed by default (a quiet month stays quiet); grouped by the edge the SERVER
              // decided each entry sits near (`near_cutoff.edge`).
              //
              // This replaced a client-side `r.date > lc.period.end` test, which was dead code:
              // `period` on the wire is the ALREADY-WIDENED window and `_collect_uncovered` only
              // emits rows inside it, so nothing could ever be greater than its end and the
              // "dated after the close" group never rendered once. The edge now comes from the
              // same computation that files the row here at all — one definition, one answer,
              // and the same one the AI tool reads (owner, 2026-07-31).
              const renderBoundarySection = () => {
                if (!boundary.length) return null
                const opening = boundary.filter((r) => r.near_cutoff?.edge === 'start')
                const closing = boundary.filter((r) => r.near_cutoff?.edge !== 'start')
                return (
                  <div className="acct-stmtview-check-boundary">
                    <button
                      type="button"
                      className="acct-stmtview-check-boundary-head"
                      onClick={() => setBoundaryOpen((o) => !o)}
                      aria-expanded={boundaryOpen}
                    >
                      <span className="acct-stmtview-check-boundary-t">
                        {boundary.length} entr{boundary.length === 1 ? 'y sits' : 'ies sit'} near
                        the cut-off — likely the previous/next statement.
                      </span>
                      <span className="acct-stmtview-check-caret">
                        {boundaryOpen ? (
                          <ChevronDown size="1em" aria-hidden="true" />
                        ) : (
                          <ChevronRight size="1em" aria-hidden="true" />
                        )}
                      </span>
                    </button>
                    {boundaryOpen && (
                      <>
                        {opening.length > 0 && (
                          <div className="acct-stmtview-check-group">
                            Near the opening cut-off — likely the previous statement
                          </div>
                        )}
                        {opening.map(renderBoundaryRow)}
                        {closing.length > 0 && (
                          <div className="acct-stmtview-check-group">
                            Near the closing cut-off — likely the next statement
                          </div>
                        )}
                        {closing.map(renderBoundaryRow)}
                      </>
                    )}
                  </div>
                )
              }
              const renderOtherStatementSection = () => {
                if (!onOtherStatementRows.length) return null
                const n = onOtherStatementRows.length
                return (
                  <div className="acct-stmtview-check-boundary">
                    <button
                      type="button"
                      className="acct-stmtview-check-boundary-head"
                      onClick={() => setOtherStatementOpen((o) => !o)}
                      aria-expanded={otherStatementOpen}
                    >
                      <span className="acct-stmtview-check-boundary-t">
                        {n} entr{n === 1 ? 'y is' : 'ies are'} on another statement you imported —
                        nothing to fix.
                      </span>
                      <span className="acct-stmtview-check-caret">
                        {otherStatementOpen ? (
                          <ChevronDown size="1em" aria-hidden="true" />
                        ) : (
                          <ChevronRight size="1em" aria-hidden="true" />
                        )}
                      </span>
                    </button>
                    {otherStatementOpen && onOtherStatementRows.map(renderLedgerOnlyRow)}
                  </div>
                )
              }
              // One matched audit row: compact statement + ledger evidence; disclosure reveals
              // statement ↔ ledger comparison, one decision group, then a full-width After import
              // result. That result is a LIVE projection of the post-commit ledger and
              // afterImportProjection mirrors
              // apply_import_merge exactly) — it recomputes on every chip flip by construction.
              const renderPairRow = ({ d, i }: { d: ImportPreviewDraft; i: number }) => {
                const band = pairBand(d) as 'strong' | 'review' | 'weak'
                const resolved = resolveExisting(d)
                if (!resolved) return null
                const ex = resolved.summary
                const act = actionFor(d, i)
                const line = outcomeLine(d, i)
                const opening = !!drawerLoading[resolved.id]
                const openFailed = !!drawerFailed[resolved.id]
                // A user-asserted manual link (review band, no engine flag) reads as YOUR decision,
                // never the engine's amber "Already in your account?" question.
                const manualLinked = isManualLinked(d)
                // Same "your decision" wording for an already-matched (strong/review) row the
                // user re-pointed via "Change match" — the engine's original pick never gets a
                // say once overridden.
                const overridden = isMatchOverridden(d)
                // NO `headline` const any more. It built the "✓ Will link · via agent · <engine
                // reason>" verdict sub-line that the collapsed match line and the expanded
                // comparison card both rendered; both are gone (owner, 2026-07-29 — "the top one
                // shows too much info" / "let's not show that too"). The decision chip on the
                // statement row carries the verdict; the FLAG_LABEL branch went with it.
                // `manualLinked` / `overridden` are still read by the override controls.
                //
                // The match picker's options are the SERVER's own `alternatives` for this row,
                // rendered verbatim (RFC 2026-07-29 §D8). No client eligibility pass, no steal, no
                // swap, no dimming: the engine only offers entries no other row claimed, so each
                // one is genuinely pickable AND already inside `decision_commit`'s acceptance set
                // (path 2, the freshly re-derived uncovered set). An empty list renders NO control
                // at all rather than a dead affordance that opens onto nothing (SIUM-ALT-NONE).
                const pickOptions = alternativesFor(d)
                  .filter((alt) => !pendingDelete[alt.id])
                  .map((alt) => ({
                    id: alt.id,
                    line: matchOptionLine(alt.existing ?? {}, d.amount, d.currency),
                    selected: alt.id === resolved.id,
                    hint: alt.id === resolved.id ? '• current' : undefined,
                  }))
                // Re-point onto one of the row's own alternatives — an ordinary `matchOverride`,
                // the ONE override concept (the retired `twinPick` used to be a second one).
                const pickAlternative = (id: string) => {
                  const alt = alternativesFor(d).find((a) => a.id === id)
                  if (!alt || d.rowIndex == null) return
                  setMatchOverride((current) => ({
                    ...current,
                    [d.rowIndex!]: { id, summary: alt.existing ?? {} },
                  }))
                  setMatchAction((current) => ({ ...current, [d.rowIndex!]: 'merge' }))
                  if (band !== 'strong')
                    setQueueDecided((current) => ({ ...current, [d.rowIndex!]: true }))
                }
                // Transfer identity (owner 2026-07-17): the statement side's own account anchors
                // the existing transfer's unnamed to-leg; a routed draft's to-leg is the hint.
                const transferCtx = {
                  anchorAccountId: d.destId || accountId,
                  counterHint: d.routed?.to_account_id,
                }
                // A row LINKED TO a matched existing transfer (transfer_leg_echo/transfer_pair) has
                // no `d.routed` of its own — only the MATCHED entry knows it's a transfer at all —
                // so fall back to naming the counterpart from the existing summary's own account fields.
                const stmtTransfer =
                  transferLine(d, accounts, catMapProp, { compact: true }) ||
                  (ex.kind === 'transfer'
                    ? transferLine(ex, accounts, catMapProp, { compact: true, ...transferCtx })
                    : null)
                const proj = afterImportProjection({
                  action: act,
                  draft: d,
                  existing: ex,
                  flagType: resolvedFlagType(d, resolved),
                  catMap: catMapProp,
                  accounts,
                  anchorAccountId: transferCtx.anchorAccountId,
                })
                const origAmt = d.original_amount ?? d.origAmount
                const origCcy = d.original_currency ?? d.origCurrency
                const isTransfer = !!stmtTransfer || ex.kind === 'transfer'
                // One consistent word per action across the whole app: link, import, leave out.
                // Whether a Link actually adopts the statement's date/amount is already visible
                // in the After-import preview below. The WEAK band spells its link out — "Same
                // one — link" answers the question a maybe-match actually poses (SIUM-WEAK-LINK),
                // where a bare "Link" would read as a verdict the engine never reached. The
                // decision CHIP still says Link/Import/Leave out: it names the outcome, and the
                // outcome is identical across bands.
                const chips: Array<[MatchAction, string]> = [
                  ['merge', band === 'weak' ? 'Same one — link' : 'Link'],
                  ['new', 'Import'],
                  ['skip', 'Leave out'],
                ]
                // The handoff's rule: the closed row's verdict chip uses the EXACT wording of the
                // override control underneath it, so the two can never disagree.
                const decisionLabel =
                  act === 'merge' ? 'Link' : act === 'new' ? 'Import' : 'Leave out'
                const effectiveCategory =
                  act === 'skip'
                    ? null
                    : act === 'merge'
                      ? ((ex as ExistingSummaryLike & { category?: string | null }).category ??
                        null)
                      : d.category
                const categoryLabel =
                  act === 'skip'
                    ? '—'
                    : isTransfer
                      ? stmtTransfer || 'Transfer'
                      : effectiveCategory
                        ? catMapProp[effectiveCategory]?.label || effectiveCategory
                        : 'Uncategorized'
                // Off-by rows expand by default under their own filter — the whole point of that
                // view is to review the drift without an extra click per row; an explicit
                // collapse still overrides it.
                const expanded = expandedRows[d.rowIndex ?? i] ?? chip === 'offby'
                const ledgerAmount = anchorPerspectiveAmount(
                  ex,
                  transferCtx.anchorAccountId,
                  d.amount,
                )
                const ledgerCurrency = anchorPerspectiveCurrency(
                  ex,
                  transferCtx.anchorAccountId,
                  d.currency,
                )
                const amountDrifts =
                  typeof ledgerAmount === 'number' && Math.abs(ledgerAmount - d.amount) > 0.004
                // The matched entry's OWN pre-conversion charge. Without it the ledger side showed
                // only its converted figure while the statement side showed both, so an identical
                // ~US$8 charge read as a mismatch (reported case: statement $8.25 /
                // −S$11.02 against a ledger entry rendered as a bare −S$10.65).
                const ledgerOriginal = originalCharge(ex, ledgerCurrency)
                const ledgerDate = (ex as ExistingSummaryLike & { date?: string }).date
                // Handoff `Cmp`: the DATE is highlighted on both sides only when the two disagree —
                // an identical date stays quiet rather than drawing the eye to a non-difference.
                const dateDrifts = !!ledgerDate && ledgerDate !== d.date
                const triggerDecision =
                  band === 'review' && !queueDecided[d.rowIndex ?? i]
                    ? 'Needs decision'
                    : decisionLabel
                return (
                  <article
                    key={i}
                    data-draft-index={i}
                    className={'imp3-arow imp-pair-row ' + band + (act === 'skip' ? ' skip' : '')}
                  >
                    <div
                      role="button"
                      tabIndex={0}
                      className="imp3-arow-line"
                      aria-expanded={expanded}
                      aria-label={`${rowTitle(d)}, ${triggerDecision}, ${categoryLabel}, ${fCur(d.amount, d.currency, { cents: true })}. Matched ledger transaction available. ${expanded ? 'Collapse details' : 'Expand details'}`}
                      onClick={() =>
                        setExpandedRows((rows) => ({ ...rows, [d.rowIndex ?? i]: !expanded }))
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setExpandedRows((rows) => ({ ...rows, [d.rowIndex ?? i]: !expanded }))
                        }
                      }}
                    >
                      <span className="imp3-arow-date">{d.date.slice(5)}</span>
                      <span className="imp3-arow-main">
                        <span className="imp3-arow-name" title={rowTitle(d)}>
                          {rowTitle(d)}
                        </span>
                        {rowSubtitle(d) && (
                          <span className="imp3-arow-desc" title={rowSubtitle(d) || undefined}>
                            {rowSubtitle(d)}
                          </span>
                        )}
                      </span>
                      <span className="imp3-arow-status">
                        <span className={'imp3-dchip ' + (act === 'merge' ? 'link' : act)}>
                          {decisionLabel}
                        </span>
                        {band === 'review' && !queueDecided[d.rowIndex ?? i] && (
                          <span className="imp3-qchip open">needs you</span>
                        )}
                        {/* Muted, never amber and never green: amber on this screen means "we are
                        blocking on you", and a weak row is explicitly NOT blocking — it
                        already carries a safe default (SIUM-WEAK-QUIET). */}
                        {band === 'weak' && (
                          <span className="imp3-qchip maybe">Possible match</span>
                        )}
                      </span>
                      <span className="imp3-arow-catcol">
                        {act !== 'skip' && (
                          <span
                            className={
                              'imp3-cat' + (!effectiveCategory && !isTransfer ? ' none' : '')
                            }
                            title={categoryLabel}
                          >
                            <i
                              style={{
                                background: isTransfer
                                  ? 'var(--muted)'
                                  : catMapProp[effectiveCategory || '']?.color || 'var(--faint)',
                              }}
                            />
                            <span>{categoryLabel}</span>
                          </span>
                        )}
                      </span>
                      {/* No pencil on a transfer row: the modal it opens edits payee & category, and a
                      transfer has neither — it routes to an ACCOUNT (owner, 2026-07-29, where the
                      pencil also collided with the wider "Transfer ← <account>" chip). */}
                      {!d.dup && !isTransfer && (
                        <button
                          type="button"
                          className="imp3-editpencil"
                          title="Edit payee & category"
                          aria-label={`Edit payee and category for ${rowTitle(d)}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditingDraftIndex(i)
                          }}
                        >
                          <svg
                            width="12"
                            height="12"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                          </svg>
                        </button>
                      )}
                      <span className="imp3-arow-amtwrap">
                        <b
                          className={
                            'imp3-arow-amt' +
                            (d.amount > 0 ? ' pos' : '') +
                            (act === 'skip' ? ' dim' : '')
                          }
                        >
                          {d.amount < 0 ? '' : '+'}
                          {fCur(d.amount, d.currency, { cents: true })}
                        </b>
                        {origAmt && origCcy && origCcy !== d.currency ? (
                          <span className="imp3-orig">
                            {fCur(origAmt, origCcy, { cents: true })}
                          </span>
                        ) : null}
                      </span>
                      <svg
                        className={'imp3-chev' + (expanded ? ' open' : '')}
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </div>
                    {/* WEAK band — the SUGGESTION line (SIUM-WEAK-QUIET). Deliberately weaker
                    than the strong `imp3-arow-match` line beside it: dashed and dimmed, a
                    `?`/`Maybe` glyph instead of the solid `⇄ Ledger` tag, and an amount that
                    is neither bolded nor drift-coloured. It states the possibility and opens
                    the entry; it no longer carries a dismissal of its own — the row's own
                    decision control answers the question (Import = not the same, and the
                    matched entry goes back to the ledger-only list). The drift phrase is
                    CLIENT arithmetic over the two figures already on screen — never the
                    engine's own `flag.reason`, which is not rendered anywhere. */}
                    {!expanded && band === 'weak' && (
                      <div className="imp3-arow-maybe">
                        <button
                          type="button"
                          className="imp3-arow-maybe-main"
                          disabled={opening}
                          aria-label={`Open possible ledger match ${ex.note || 'transaction'}`}
                          onClick={() => openExistingTxn(resolved.id)}
                        >
                          <span className="imp3-arow-maybe-ic" aria-hidden="true">
                            ?
                          </span>
                          <span className="imp3-arow-maybe-tag">Maybe</span>
                          <span className="imp3-arow-maybe-name">{ex.note || '—'}</span>
                          <span className="imp3-arow-maybe-facts">
                            <span>{ledgerDate ? ledgerDate.slice(5) : '—'}</span>
                            {driftPhrase(d.amount, ledgerAmount) && (
                              <span>{driftPhrase(d.amount, ledgerAmount)}</span>
                            )}
                          </span>
                          <span className="imp3-arow-maybe-amt">
                            {typeof ledgerAmount === 'number'
                              ? fCur(ledgerAmount, ledgerCurrency, { cents: true })
                              : '—'}
                            {ledgerOriginal && (
                              <span
                                className="imp3-orig"
                                title="Original charge — before currency conversion"
                              >
                                {ledgerOriginal}
                              </span>
                            )}
                          </span>
                        </button>
                      </div>
                    )}
                    {/* Collapsed only — never once expanded, regardless of decision or band.
                    Expanding reveals the full ON THE STATEMENT / IN YOUR LEDGER comparison
                    below, which restates the same name/date/amount/Off-by facts (plus its own
                    "open this transaction" link) — this one-liner adds nothing once that's
                    open. Choosing Import drops it: the matched entry isn't part of that
                    outcome anymore. The weak band has its own quieter line above. */}
                    {!expanded && band !== 'weak' && act !== 'new' && (
                      <button
                        type="button"
                        className="imp3-arow-match"
                        disabled={opening}
                        aria-label={`Open matched ledger entry ${ex.note || 'transaction'}`}
                        onClick={() => openExistingTxn(resolved.id)}
                      >
                        <span className="imp3-arow-match-ic">
                          <ArrowLeftRight size={13} aria-hidden="true" />
                        </span>
                        <span className="imp3-arow-match-main">
                          <span className="imp3-arow-match-name">
                            <span className="imp3-arow-match-tag">Ledger</span>
                            {ex.note || '—'}
                          </span>
                          {/* Date + kind, always visible without expanding — enough to judge "is
                        this a complete match" or "I need to tap in for more". The date
                        HIGHLIGHTS when it drifts from the statement's own — same treatment as
                        the expanded comparison. */}
                          <span className="imp3-arow-match-facts">
                            <span className={dateDrifts ? 'hi' : ''}>
                              {ledgerDate ? ledgerDate.slice(5) : '—'}
                            </span>
                            <span aria-hidden="true">•</span>
                            <span>{MATCH_KIND_LABEL[ex.kind || ''] || ex.kind || '—'}</span>
                          </span>
                          {/* NO verdict sub-line. The "✓ Will link • via agent • <engine reason>"
                        line that used to sit here is gone for good (owner, 2026-07-29: "these,
                        ensure we show like the one at the bottom, not at the top. the top one
                        shows too much info"). The band verdict is already carried by the
                        decision chip on the statement row above, and the trailing clause was
                        raw matcher diagnostics ("wording differs (review); unique pairing in a
                        complete statement window (books-close)") that means nothing to a user.
                        The engine's `reason` is still produced by services/matching.py for
                        logs and tests — it is simply never rendered. */}
                        </span>
                        {offByChip(i, d.currency)}
                        <span className={'imp3-arow-match-amt' + (amountDrifts ? ' drift' : '')}>
                          {typeof ledgerAmount === 'number'
                            ? fCur(ledgerAmount, ledgerCurrency, { cents: true })
                            : '—'}
                          {ledgerOriginal && (
                            <span
                              className="imp3-orig"
                              title="Original charge — before currency conversion"
                            >
                              {ledgerOriginal}
                            </span>
                          )}
                        </span>
                      </button>
                    )}
                    {expanded && (
                      <div className="imp3-arow-body">
                        {/* Same removal as the collapsed line (owner, 2026-07-29 via the coordinator:
                      "let's not show that too") — the expanded card carried the identical
                      "✓ Will link · via agent · <engine reason>" clause. The Off-by marker is
                      NOT part of it: that's a real amount-discrepancy fact, not explanatory
                      noise about the match, so it still renders below. */}
                        {(() => {
                          const off = offByChip(i, d.currency)
                          return off && <div className="imp3-detail-note">{off}</div>
                        })()}
                        {/* The handoff's `Cmp`: statement ↔ ledger, differing facts highlighted, a
                      dashed mid rail carrying either `=` or the Δ drift pill. */}
                        <div className="imp3-cmp">
                          <div className="imp3-cmp-col imp-pair-side stmt">
                            <div className="imp3-cmp-h">On the statement</div>
                            <div className="imp3-cmp-name">{d.payee || d.description}</div>
                            {d.payee && d.description && d.payee !== d.description && (
                              <div className="imp3-cmp-src">{d.description}</div>
                            )}
                            <div className="imp3-cmp-facts">
                              <span className={dateDrifts ? 'hi' : ''}>{d.date.slice(5)}</span>
                              {origAmt && origCcy && origCcy !== d.currency ? (
                                <span
                                  className="imp3-orig"
                                  title="Original charge — before currency conversion"
                                >
                                  {fCur(origAmt, origCcy, { cents: true })}
                                </span>
                              ) : null}
                              <b className={'nums' + (amountDrifts ? ' hi' : '')}>
                                {d.amount < 0 ? '' : '+'}
                                {fCur(d.amount, d.currency, { cents: true })}
                              </b>
                            </div>
                            {stmtTransfer && <span className="imp3-cmp-src">{stmtTransfer}</span>}
                          </div>
                          <div className="imp3-cmp-mid">
                            {amountDrifts && typeof ledgerAmount === 'number' ? (
                              ledgerCurrency === d.currency ? (
                                <span
                                  className="imp3-cmp-drift"
                                  title={`Statement ${fCur(d.amount, d.currency, { cents: true })} • yours ${fCur(ledgerAmount, ledgerCurrency, { cents: true })}`}
                                >
                                  Δ{' '}
                                  {fCur(Math.abs(ledgerAmount - d.amount), d.currency, {
                                    cents: true,
                                  }).replace('−', '')}
                                </span>
                              ) : (
                                // Different currencies — subtracting the raw figures would fabricate a
                                // number in neither currency (reported case: a ₫325,650 row matched to
                                // an unrelated S$16.75 entry showed "Δ ₫325,670" — not a real amount).
                                <span
                                  className="imp3-cmp-drift"
                                  title="Different currencies — not directly comparable"
                                >
                                  ≠ different currency
                                </span>
                              )
                            ) : (
                              <span className="imp3-cmp-eq" aria-hidden="true">
                                =
                              </span>
                            )}
                          </div>
                          <div className="imp3-cmp-col imp-pair-side ledger">
                            <div className="imp3-cmp-h">In your ledger</div>
                            <div className="imp3-cmp-name">
                              <button
                                type="button"
                                className="imp3-cmp-lk"
                                title="Open this transaction"
                                disabled={opening}
                                onClick={() => openExistingTxn(resolved.id)}
                              >
                                {ex.note || '—'}
                              </button>
                              {/* The existing entry's cleared/pending status is a fact about that
                            ledger row, not about whether it's the right match for this
                            statement row, so it doesn't belong in the comparison. */}
                            </div>
                            <div className="imp3-cmp-src">
                              {existingTxnLine(ex, accounts, catMapProp, d.currency, {
                                ...transferCtx,
                                omitFacts: true,
                              })}
                            </div>
                            {openFailed && (
                              <span className="imp-rowmatch-drawererr">
                                Couldn’t open — try again.
                              </span>
                            )}
                            {/* Same slot, same class, same placement as the statement side's own
                          original above — the two columns must be directly comparable. */}
                            <div className="imp3-cmp-facts">
                              <span className={dateDrifts ? 'hi' : ''}>
                                {ledgerDate ? ledgerDate.slice(5) : '—'}
                              </span>
                              {ledgerOriginal && (
                                <span
                                  className="imp3-orig"
                                  title="Original charge — before currency conversion"
                                >
                                  {ledgerOriginal}
                                </span>
                              )}
                              <b className={amountDrifts ? 'hi' : ''}>
                                {typeof ledgerAmount === 'number'
                                  ? fCur(ledgerAmount, ledgerCurrency, { cents: true })
                                  : '—'}
                              </b>
                            </div>
                          </div>
                        </div>
                        {/* One CTA family, one line, left-aligned (handoff): the segmented decision
                      control, then the link/unlink override — never a right-floated text link. */}
                        <div className="imp3-actrow">
                          <span
                            className="imp3-seg"
                            role="radiogroup"
                            aria-label={`What happens to ${rowTitle(d)}`}
                          >
                            {chips.map(([action, label]) => (
                              <button
                                key={action}
                                type="button"
                                role="radio"
                                aria-checked={act === action}
                                className={act === action ? 'on' : ''}
                                onClick={() => toggleMatchAction(i, action)}
                              >
                                {label}
                              </button>
                            ))}
                          </span>
                          {manualLinked && (
                            <button
                              type="button"
                              className="imp3-actbtn"
                              onClick={() => {
                                setManualLink((current) => {
                                  const next = { ...current }
                                  delete next[d.rowIndex!]
                                  return next
                                })
                                setMatchAction((current) => {
                                  const next = { ...current }
                                  delete next[d.rowIndex!]
                                  return next
                                })
                                setQueueDecided((current) => {
                                  const next = { ...current }
                                  delete next[d.rowIndex!]
                                  return next
                                })
                              }}
                            >
                              Unlink
                            </button>
                          )}
                          {/* ONE `Change match` control for every band, driven by the SERVER's
                        `alternatives` — never rendered when that list is empty, so it can
                        never be a dead affordance that opens onto nothing (SIUM-ALT-NONE).
                        Every option stays live: the engine only offers entries no other row
                        claimed, so there is nothing to dim. */}
                          {pickOptions.length > 0 && d.rowIndex != null && (
                            <LinkPicker
                              label="Change match"
                              variant="imp3-actbtn"
                              listLabel="Match with a different ledger entry"
                              options={pickOptions}
                              onPick={pickAlternative}
                            />
                          )}
                          {/* Import on a matched row: its entry is released to "not on this
                        statement", and the row may link to any entry there instead — the same
                        uncovered set the commit guard accepts a manual link to. */}
                          {act !== 'merge' &&
                            d.rowIndex != null &&
                            manualLinkOptions(d).length > 0 && (
                              <LinkPicker
                                label="Link to existing"
                                variant="imp3-actbtn"
                                listLabel="Link this row to a different ledger entry"
                                options={manualLinkOptions(d).map((o) => ({
                                  ...o,
                                  hint: o.taken ? '• linked to another row' : undefined,
                                }))}
                                onPick={(id) => {
                                  const summary = checkRowById.get(id)
                                  if (!summary || d.rowIndex == null) return
                                  setMatchOverride((current) => ({
                                    ...current,
                                    [d.rowIndex!]: { id, summary },
                                  }))
                                  setMatchAction((current) => ({
                                    ...current,
                                    [d.rowIndex!]: 'merge',
                                  }))
                                }}
                              />
                            )}
                          {overridden && d.rowIndex != null && (
                            <button
                              type="button"
                              className="imp3-actbtn"
                              aria-label="Back to Lucius's pick"
                              onClick={() =>
                                setMatchOverride((current) => {
                                  const next = { ...current }
                                  delete next[d.rowIndex!]
                                  return next
                                })
                              }
                            >
                              Back to Lucius’s pick
                            </button>
                          )}
                        </div>
                        {/* Handoff `After`: the truthful projection of what the ledger holds once
                      committed. One inline row per resulting record (name / date / category /
                      amount / badge chips), never a stack of bordered cards. */}
                        <div className="imp3-after">
                          <span className="imp3-after-l">
                            {act === 'merge'
                              ? 'After link'
                              : act === 'new'
                                ? 'After import'
                                : 'After'}
                            {proj.count ? ` • ${proj.count}` : ''}
                          </span>
                          {proj.records.map((rec, ri) => (
                            <span key={ri} className={'imp3-after-rec imp-after-rec ' + rec.tag}>
                              <b className="imp-after-title">{rec.title}</b>
                              {rec.date && (
                                <span className="nums">
                                  {rec.date.old && (
                                    <>
                                      <s className="imp-after-old">{rec.date.old}</s>
                                      <span className="imp-after-arrow">→</span>
                                    </>
                                  )}
                                  <span className={rec.date.old ? 'imp-after-new' : ''}>
                                    {rec.date.value}
                                  </span>
                                </span>
                              )}
                              <span>{rec.category}</span>
                              {rec.amount && (
                                <span className="nums amt">
                                  {rec.amount.old && (
                                    <>
                                      <s className="imp-after-old">{rec.amount.old}</s>
                                      <span className="imp-after-arrow">→</span>
                                    </>
                                  )}
                                  <span className={rec.amount.old ? 'imp-after-new' : ''}>
                                    {rec.amount.value}
                                  </span>
                                </span>
                              )}
                              {rec.original && (
                                <span className="imp-after-orig">
                                  {rec.original.added ? '+ original charge ' : 'original '}
                                  {rec.original.value}
                                </span>
                              )}
                              {rec.badges.map((b) => (
                                <em key={b}>{b}</em>
                              ))}
                            </span>
                          ))}
                          {/* Handoff: when the decision books NOTHING the panel says exactly that,
                        in warn tone. With records present the records ARE the outcome — the
                        design deliberately doesn't restate it in prose. */}
                          {proj.records.length === 0 && (
                            <span className="imp3-after-none">
                              {line || 'Nothing books — this row won’t be in your ledger.'}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </article>
                )
              }
              const renderPlainRow = ({ d, i }: { d: ImportPreviewDraft; i: number }) => {
                const excluded = isExcluded(d, i)
                const absorbed = isAbsorbed(d, i)
                const ordinaryPairConflict = d.pair?.role === 'in' && pairHasOrdinaryMatch(d)
                const explicitlySeparatedPairLeg =
                  d.pair?.role === 'in' && !!pairInclude[d.rowIndex ?? i]
                const separatePairLeg =
                  d.pair?.role === 'in' && (!!ordinaryPairConflict || !!explicitlySeparatedPairLeg)
                const pairLeftOutWithPartner =
                  d.pair?.role === 'in' &&
                  excluded &&
                  !ordinaryPairConflict &&
                  !explicitlySeparatedPairLeg
                // Off-by rows expand by default under their own filter — an explicit collapse
                // still overrides it.
                const expanded = expandedRows[d.rowIndex ?? i] ?? chip === 'offby'
                const bodyOpen = expanded
                const title = rowTitle(d)
                const subtitle = rowSubtitle(d)
                const fullText = d.description || title
                const reason = d.dup
                  ? 'Already imported — protected by its external reference.'
                  : pairLeftOutWithPartner
                    ? 'Left out because its paired transfer row is not represented in the ledger.'
                    : // Lucius's `reason` only explains an EXCLUSION when its op actually excluded the
                      // row (skip) — a set_category/route_transfer op's reason justifies what it did to
                      // the row, not why it's left out, and showing it here as if it were "why excluded"
                      // was misleading (owner repro: a set_category reason like "Example Ride payment -
                      // transport" isn't an explanation for the row being left out at all).
                      // The engine's own `flag.reason` used to sit next in this chain; it is out for
                      // good (owner, 2026-07-29 — "please stop showing these lucius thing/reason to
                      // users"). It was raw matcher diagnostics, never written for a reader. Lucius's
                      // own plain-language reason above it stays; the chain now falls through to the
                      // parser's row notes and then the honest generic sentence.
                      (d.lucius?.op === 'skip' ? d.lucius?.reason : null) ||
                      (d.notes || [])[0] ||
                      (excluded && d.needsEyes
                        ? 'Low-confidence read, no matching entry — review before importing.'
                        : excluded
                          ? 'Excluded by the current decision.'
                          : '')
                // A row linked to a matched existing transfer (a weak twin or manual link picking
                // one — the engine's own transfer_leg_echo/transfer_pair matches always carry a
                // band and render via renderPairRow instead) has no `d.routed` of its own, so fall
                // back to naming the counterpart from the existing summary's own account fields.
                const resolvedForCategory = resolveExisting(d)
                const transferCategoryLine =
                  transferLine(d, accounts, catMapProp, { compact: true }) ||
                  (resolvedForCategory?.summary.kind === 'transfer'
                    ? transferLine(resolvedForCategory.summary, accounts, catMapProp, {
                        compact: true,
                        anchorAccountId: d.destId || accountId,
                        counterHint: d.routed?.to_account_id,
                      })
                    : null)
                const category =
                  transferCategoryLine ||
                  (d.category ? catMapProp[d.category]?.label || d.category : 'Uncategorized')
                const ri = d.rowIndex ?? i
                const includeRow = () => {
                  setPlainSkip((current) => {
                    const next = { ...current }
                    delete next[ri]
                    return next
                  })
                  if (d.needsEyes) {
                    setEyesInclude((current) => ({ ...current, [ri]: true }))
                    setQueueDecided((current) => ({ ...current, [ri]: true }))
                  }
                  if (d.lucius?.op === 'skip')
                    setLuciusSkipInclude((current) => ({ ...current, [ri]: true }))
                }
                const leaveOut = () => {
                  setPlainSkip((current) => ({ ...current, [ri]: true }))
                  if (d.needsEyes) {
                    setEyesInclude((current) => ({ ...current, [ri]: false }))
                    setQueueDecided((current) => ({ ...current, [ri]: true }))
                  }
                  if (d.lucius?.op === 'skip')
                    setLuciusSkipInclude((current) => ({ ...current, [ri]: false }))
                }
                const canImportDirectly =
                  !isUnrouted(d, i) && !isAbsorbedLeg(d, i) && !(d.preRecon && !includeEarlier)
                const manualOptions = canManualLinkRow(d, i) ? manualLinkOptions(d) : []
                // "Fix the amount" (a misread/blurry statement figure) used to live ONLY on the
                // Needs-you queue card — now that a needsEyes row auto-decides at load (owner,
                // 2026-07-28), that card never renders, which made the correction unreachable
                // entirely (owner call: port it here rather than accept the loss, since a genuinely
                // misread amount stays wrong regardless of the row's current Import/Leave out
                // choice). Shares the same top-level fix state the old queue card used, keyed by
                // the row's stable `rowIndex` (falling back to the array position for a row the
                // legacy/non-decision path never stamped one onto).
                // An unresolved FX-pair OUT leg keeps the queue card's own exclusion (`twoChoicePairOut`)
                // — its amount is cross-checked against its IN partner, not independently correctable.
                const canFixAmount = !!d.needsEyes && d.pair?.role !== 'out'
                const fixingAmount = canFixAmount && queueFixingIndex === ri
                const beginAmountFix = () => {
                  setQueueFixingIndex(ri)
                  setQueueFixValue(Math.abs(d.amount).toFixed(2))
                  setQueueFixOriginal((current) =>
                    current[ri]
                      ? current
                      : { ...current, [ri]: { amount: d.amount, touched: !!touched[ri]?.amount } },
                  )
                }
                const applyAmountFix = () => {
                  const parsed = Number(queueFixValue)
                  if (!Number.isFinite(parsed) || parsed < 0) return
                  const corrected = d.amount < 0 ? -Math.abs(parsed) : Math.abs(parsed)
                  updateDraft(i, { amount: corrected })
                  setPlainSkip((current) => {
                    const next = { ...current }
                    delete next[ri]
                    return next
                  })
                  setEyesInclude((current) => ({ ...current, [ri]: true }))
                  setQueueDecided((current) => ({ ...current, [ri]: true }))
                  setQueueAmountCorrected((current) => ({ ...current, [ri]: true }))
                  setQueueFixingIndex(null)
                  setQueueFixValue('')
                }
                // Handoff: an unmatched row's decision is the SAME segmented Import / Leave out
                // radiogroup every other row gets, on the same `imp3-actrow` line, with
                // `Link to existing` beside it in the one bordered-pill CTA family. A left-out row
                // shows `Import` (never "Import anyway" — the handoff's own copy change: the
                // system's leave-out call can be wrong too).
                const decisionActions =
                  !d.dup &&
                  !absorbed &&
                  !explicitlySeparatedPairLeg &&
                  (canImportDirectly || !excluded || manualOptions.length > 0) ? (
                    <>
                      <div className="imp3-actrow">
                        {excluded ? (
                          canImportDirectly && (
                            <button
                              type="button"
                              className="imp3-actbtn imp3-lc-btn imp"
                              onClick={includeRow}
                            >
                              Import
                            </button>
                          )
                        ) : (
                          <span
                            className="imp3-seg"
                            role="radiogroup"
                            aria-label={`What happens to ${title}`}
                          >
                            <button
                              type="button"
                              role="radio"
                              aria-checked="true"
                              className="on"
                              onClick={includeRow}
                            >
                              Import
                            </button>
                            <button
                              type="button"
                              role="radio"
                              aria-checked="false"
                              onClick={leaveOut}
                            >
                              Leave out
                            </button>
                          </span>
                        )}
                        {canFixAmount && !fixingAmount && (
                          <button
                            type="button"
                            className="imp3-actbtn"
                            title="Correct a blurry or misread statement figure."
                            onClick={beginAmountFix}
                          >
                            Fix the amount
                          </button>
                        )}
                        {manualOptions.length > 0 && (
                          <LinkPicker
                            label="Link to existing"
                            listLabel="Link this row to an existing ledger entry"
                            variant="imp3-actbtn"
                            options={manualOptions.map((o) => ({
                              ...o,
                              hint: o.taken ? '• linked to another row' : undefined,
                            }))}
                            onPick={(id) => {
                              const summary = checkRowById.get(id)
                              if (!summary) return
                              setManualLink((current) => ({
                                ...current,
                                [d.rowIndex!]: { id, summary },
                              }))
                              setPlainSkip((current) => {
                                const next = { ...current }
                                delete next[ri]
                                return next
                              })
                              setMatchAction((current) => ({ ...current, [ri]: 'merge' }))
                              setQueueDecided((current) => ({ ...current, [ri]: true }))
                            }}
                          />
                        )}
                      </div>
                      {fixingAmount && (
                        <div
                          className="imp3-fixrow"
                          role="group"
                          aria-label={`Correct amount for ${title}`}
                        >
                          <span>{d.currency}</span>
                          <input
                            autoFocus
                            inputMode="decimal"
                            type="number"
                            min="0"
                            step="0.01"
                            aria-label={`Correct amount for ${title}`}
                            value={queueFixValue}
                            onChange={(event) => setQueueFixValue(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                event.preventDefault()
                                applyAmountFix()
                              }
                            }}
                          />
                          <button type="button" className="imp3-fixgo" onClick={applyAmountFix}>
                            Use this
                          </button>
                          <button
                            type="button"
                            className="imp3-fixno"
                            onClick={() => {
                              setQueueFixingIndex(null)
                              setQueueFixValue('')
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                    </>
                  ) : null
                const decisionLabel = absorbed
                  ? 'Folded into transfer'
                  : excluded
                    ? 'Left out'
                    : separatePairLeg
                      ? 'Import separately'
                      : 'Import'
                return (
                  <article
                    key={i}
                    data-draft-index={i}
                    data-scan-index={i}
                    className={
                      'imp3-arow plain' +
                      (excluded ? ' skip' : '') +
                      (absorbed ? ' absorbed' : '') +
                      (d.dup ? ' dup' : '')
                    }
                  >
                    {/* A `role="button"` div, not a real `<button>` — the pencil edit trigger below
                    needs to nest a genuine interactive control inside this row (invalid HTML
                    inside a real button), and this is the standard accessible pattern for that:
                    same "button" semantics/query behavior, manual Enter/Space activation. */}
                    <div
                      role="button"
                      tabIndex={0}
                      className="imp3-arow-line"
                      aria-expanded={bodyOpen}
                      aria-label={`${title}, ${decisionLabel}, ${excluded ? 'no category applied' : category}, ${fCur(d.amount, d.currency, { cents: true })}. ${bodyOpen ? 'Collapse details' : 'Expand details'}`}
                      onClick={() =>
                        setExpandedRows((current) => ({ ...current, [ri]: !bodyOpen }))
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setExpandedRows((current) => ({ ...current, [ri]: !bodyOpen }))
                        }
                      }}
                    >
                      <span className="imp3-arow-date">{d.date.slice(5)}</span>
                      <span className="imp3-arow-main">
                        <span className="imp3-arow-name" title={title}>
                          {title}
                        </span>
                        {subtitle && (
                          <span className="imp3-arow-desc" title={subtitle}>
                            {subtitle}
                          </span>
                        )}
                      </span>
                      <span className="imp3-arow-status imp3-plain-decision">
                        {/* One chip family for every decision state (owner: same font size,
                        capitalization — "Import"/"Left out" read as two different chip
                        systems when one was uppercase-and-bold and the other wasn't). */}
                        <span
                          className={
                            'imp3-dchip ' +
                            (excluded ? 'skip' : d.dup ? 'dup' : absorbed ? 'absorbed' : 'new')
                          }
                        >
                          {decisionLabel}
                        </span>
                        {/* A needsEyes row auto-decides on load like everything else, so the old
                        "Needs your eyes · N%" badge next to an already-decided Import/Left out
                        chip read as two contradicting states on the same row. Once decided,
                        the main chip alone is the row's status — nothing else to add. A dup
                        row is decided the moment it's flagged (hard L0, never routed through
                        the interactive queue), so queueDecided never gets set for it — check
                        d.dup directly too, or the badge lingers forever next to "Left out". */}
                        {d.needsEyes && !queueDecided[ri] && !d.dup && (
                          <span className="imp3-qchip open">
                            Needs your eyes
                            {typeof d.confidence === 'number'
                              ? ` • ${Math.round(d.confidence * 100)}%`
                              : ''}
                          </span>
                        )}
                      </span>
                      <span className="imp3-arow-catcol">
                        {luciusUndoing[d.rowIndex ?? -1] ? (
                          <span className="imp3-cat imp3-cat--busy">
                            <span className="imp3-commit-spin" aria-hidden="true" />
                            <span>Updating…</span>
                          </span>
                        ) : (
                          !excluded && (
                            <span className="imp3-cat" title={category}>
                              <i
                                style={{
                                  background: d.category
                                    ? catMapProp[d.category]?.color || 'var(--faint)'
                                    : 'var(--faint)',
                                }}
                              />
                              <span>{category}</span>
                            </span>
                          )
                        )}
                      </span>
                      {/* The pencil opens Edit payee & category, whose Type switch also turns a row
                      into a transfer and back — so a routed row keeps it; an engine-paired
                      transfer (no `routed`) has nothing to edit. */}
                      {!d.dup && !absorbed && (!transferCategoryLine || !!d.routed) && (
                        <button
                          type="button"
                          className="imp3-editpencil"
                          title="Edit payee & category"
                          aria-label={`Edit payee and category for ${title}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditingDraftIndex(i)
                          }}
                        >
                          <svg
                            width="12"
                            height="12"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                          </svg>
                        </button>
                      )}
                      <span className="imp3-arow-amtwrap">
                        <b
                          className={
                            'imp3-arow-amt' +
                            (d.amount > 0 ? ' pos' : '') +
                            (excluded ? ' dim' : '')
                          }
                        >
                          {d.amount < 0 ? '' : '+'}
                          {fCur(d.amount, d.currency, { cents: true })}
                        </b>
                        {/* A plain (unmatched) statement row carries an original just as often as a
                        matched one — it was simply never rendered here, unlike the pair row. */}
                        {(() => {
                          const o = originalCharge(d, d.currency)
                          return o ? (
                            <span
                              className="imp3-orig"
                              title="Original charge — before currency conversion"
                            >
                              {o}
                            </span>
                          ) : null
                        })()}
                      </span>
                      <svg
                        className={'imp3-chev' + (bodyOpen ? ' open' : '')}
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </div>
                    {/* Collapsed only — expanding the row reveals the full ON THE STATEMENT / IN
                    YOUR LEDGER comparison card below, which restates the SAME facts (name,
                    date, amount, the Off-by delta) at a glance instead of a one-line summary. */}
                    {!bodyOpen &&
                      !!matchFlag(d) &&
                      (!d.pair || d.pair.role !== 'in' || !absorbed) &&
                      (() => {
                        const flag = matchFlag(d)
                        const existing = flag?.existing
                        const existingId = flag?.existing_id
                        const action = actionFor(d, i)
                        const outcome = outcomeLine(d, i)
                        if (!flag) return null
                        return (
                          <div className="imp3-arow-match imp-rowmatch">
                            <span className="imp3-arow-match-ic" aria-hidden="true">
                              <ArrowLeftRight size={13} />
                            </span>
                            <span className="imp3-arow-match-main">
                              <span className="imp3-arow-match-name">
                                <span className="imp3-arow-match-tag">Ledger</span>
                                {existing && existingId ? (
                                  <button
                                    type="button"
                                    className="imp3-cmp-lk"
                                    disabled={!!drawerLoading[existingId]}
                                    aria-label={`Open matched ledger entry ${existing.note || 'transaction'}`}
                                    onClick={() => openExistingTxn(existingId)}
                                  >
                                    {existing.note || '—'}
                                  </button>
                                ) : (
                                  existing?.note || '—'
                                )}
                              </span>
                              <span className="imp3-arow-match-src">
                                <span
                                  className={
                                    'imp-rowmatch-txt' + (action === 'merge' ? ' link' : '')
                                  }
                                >
                                  {action === 'merge'
                                    ? 'will link'
                                    : action === 'new'
                                      ? 'importing as new'
                                      : 'left out'}
                                </span>
                                {existing &&
                                  ` • ${existingTxnLine(existing, accounts, catMapProp, d.currency, { anchorAccountId: d.destId || accountId, counterHint: d.routed?.to_account_id })}`}
                                {outcome && (
                                  <span className="imp-rowmatch-outcome"> • {outcome}</span>
                                )}
                              </span>
                            </span>
                            {offByChip(i, d.currency)}
                            <span className="imp3-arow-match-amt">
                              {existing?.amount != null
                                ? fCur(existing.amount, existing.currency || d.currency, {
                                    cents: true,
                                  })
                                : '—'}
                              {(() => {
                                const o = existing
                                  ? originalCharge(existing, existing.currency || d.currency)
                                  : null
                                return o ? (
                                  <span
                                    className="imp3-orig"
                                    title="Original charge — before currency conversion"
                                  >
                                    {o}
                                  </span>
                                ) : null
                              })()}
                            </span>
                          </div>
                        )
                      })()}
                    {/* No AI-provenance chrome — the row's category pill already shows the final
                    category (Lucius set_category and skip both just land in the normal
                    `category`/excluded state, nothing extra to show). A row Lucius routed as a
                    transfer is the one case with no other way back to plain — that lives as
                    ONE line alongside "Books as transfer" below, inside the same expanded
                    detail box rather than a separate standalone block. */}
                    {/* Inside the row's collapsible part like everything else in it (owner,
                    2026-10-26: a left-out row's reason and Import / Link stayed on screen while
                    its chevron said collapsed). The pill and struck amount still say "left out". */}
                    {excluded && reason && bodyOpen && (
                      <div className="imp3-arow-body">
                        {d.dup && d.dupOf ? (
                          <button
                            type="button"
                            className="imp3-lo-why imp3-lo-why-btn"
                            disabled={!!drawerLoading[d.dupOf]}
                            onClick={() => openExistingTxn(d.dupOf!)}
                          >
                            <span className="imp3-lo-ic" aria-hidden="true">
                              ⚠
                            </span>
                            <span className="imp3-lo-t">{reason}</span>
                            <span className="imp3-lo-open" aria-hidden="true">
                              {drawerLoading[d.dupOf] ? (
                                'Opening…'
                              ) : (
                                <>
                                  Open <ChevronRight size={12} />
                                </>
                              )}
                            </span>
                          </button>
                        ) : (
                          <div className="imp3-lo-why">
                            <span className="imp3-lo-ic" aria-hidden="true">
                              ⚠
                            </span>
                            <span className="imp3-lo-t">{reason}</span>
                          </div>
                        )}
                        {decisionActions}
                      </div>
                    )}
                    {excluded && !reason && bodyOpen && decisionActions}
                    {bodyOpen && (
                      <div className="imp3-arow-body">
                        <div className="imp3-detail-grid">
                          <div className="imp3-detail-copy">
                            {fullText.length > NAME_RAIL_CHARS && (
                              <p className="imp3-arow-full">{fullText}</p>
                            )}
                            {!!matchFlag(d) &&
                              (!d.pair || d.pair.role !== 'in' || !absorbed) &&
                              (() => {
                                const flag = matchFlag(d)
                                const outcome = outcomeLine(d, i)
                                if (!flag) return null
                                const action = actionFor(d, i)
                                return (
                                  <div className="imp3-detail-note">
                                    <span
                                      className={
                                        'imp-rowmatch-txt' + (action === 'merge' ? ' link' : '')
                                      }
                                    >
                                      {action === 'merge'
                                        ? 'will link'
                                        : action === 'new'
                                          ? 'importing as new'
                                          : 'left out'}
                                    </span>
                                    {outcome && <span> • {outcome}</span>}
                                    {offByChip(i, d.currency)}
                                  </div>
                                )
                              })()}
                            {/* The curated FLAG_LABEL chip only — the engine's raw `flag.reason`
                          that used to trail it is gone (owner, 2026-07-29). */}
                            {(d.flags || []).map((flag, index) => (
                              <div key={index} className="imp3-detail-note">
                                <span className={'imp-flag ' + flag.type}>
                                  {FLAG_LABEL[flag.type] || flag.type}
                                </span>
                              </div>
                            ))}
                            {d.routed && (
                              <div className="imp3-detail-note">
                                <span>
                                  Books as transfer: {d.routed.from_account_name || '?'} →{' '}
                                  {d.routed.to_account_name || '?'}
                                </span>
                                {d.lucius?.op === 'route_transfer' && (
                                  // A real server round-trip (removes the routing, re-previews
                                  // against the cached parse — free, no new AI read) — owner asked
                                  // whether tapping it "calls backend or something... takes time to
                                  // respond". A plain "…" text swap read as nothing happening; an
                                  // actual spinner (matching Confirm's own) makes the wait legible.
                                  <button
                                    type="button"
                                    className="imp3-actbtn"
                                    aria-busy={!!luciusUndoing[d.rowIndex ?? -1]}
                                    disabled={!!luciusUndoing[d.rowIndex ?? -1]}
                                    onClick={() => undoLucius(d.rowIndex)}
                                  >
                                    {luciusUndoing[d.rowIndex ?? -1] && (
                                      <span className="imp3-commit-spin" aria-hidden="true" />
                                    )}
                                    {luciusUndoing[d.rowIndex ?? -1]
                                      ? 'Updating…'
                                      : `Not a transfer — book as ${d.amount < 0 ? 'an expense' : 'income'} instead`}
                                  </button>
                                )}
                              </div>
                            )}
                            {d.pair?.role === 'in' &&
                              (pairLeftOutWithPartner ? (
                                <div className="imp3-detail-note">
                                  Left out with its paired transfer row; restore the OUT decision
                                  before folding this leg.
                                </div>
                              ) : ordinaryPairConflict ? (
                                <div className="imp3-detail-note">
                                  Reviewed separately because one paired leg matches an ordinary
                                  ledger entry, not a two-leg transfer.
                                </div>
                              ) : (
                                <div className="imp3-detail-note">
                                  <span>
                                    Folds into the transfer on row{' '}
                                    {preview.drafts.findIndex(
                                      (row) => row.rowIndex === d.pair!.partner_index,
                                    ) + 1 || ''}
                                  </span>
                                  <button
                                    type="button"
                                    className="imp3-actbtn"
                                    onClick={() => {
                                      if (pairInclude[ri]) {
                                        setPairInclude((current) => {
                                          const next = { ...current }
                                          delete next[ri]
                                          return next
                                        })
                                        setMatchAction((current) => {
                                          const next = { ...current }
                                          delete next[ri]
                                          return next
                                        })
                                      } else {
                                        setPairInclude((current) => ({ ...current, [ri]: true }))
                                        setPlainSkip((current) => {
                                          const next = { ...current }
                                          delete next[ri]
                                          return next
                                        })
                                        setMatchAction((current) => ({ ...current, [ri]: 'new' }))
                                      }
                                    }}
                                  >
                                    {pairInclude[ri] ? 'Fold in' : 'Book separately'}
                                  </button>
                                </div>
                              ))}
                          </div>
                        </div>
                        {!excluded && decisionActions}
                      </div>
                    )}
                  </article>
                )
              }
              const queueItems = ordered.filter(
                ({ d, i }) => effectiveChipKey(d, i) === 'needs' && belongsToDecisionQueue(d, i),
              )
              const renderQueueCard = (
                { d, i }: { d: ImportPreviewDraft; i: number },
                position: number,
              ) => {
                const resolved = resolveExisting(d)
                // No `band` here any more: the queue holds review-band and needs-eyes rows only
                // (a weak row has a safe default and never enters it), so nothing below branches
                // on the band.
                const ri = d.rowIndex ?? i
                const decided = !!queueDecided[ri]
                const fixingAmount = queueFixingIndex === ri
                const twoChoicePairOut = !resolved && d.pair?.role === 'out'
                const action = resolved
                  ? actionFor(d, i)
                  : eyesInclude[ri] !== false
                    ? 'new'
                    : 'skip'
                const title = rowTitle(d)
                // `matchFlag(d)?.reason` led this chain until 2026-07-29 — the engine's raw
                // matcher diagnostics ("wording differs (review); unique pairing in a complete
                // statement window (books-close)"). Owner: "please stop showing these
                // thing/reason to users". Falls through to the parser's own row note, then an
                // honest generic prompt; no replacement copy invented.
                const reason = (d.notes || [])[0] || 'Check the statement row before booking it.'
                const setQueueChoice = (choice: MatchAction) => {
                  if (resolved) setMatchAction((current) => ({ ...current, [ri]: choice }))
                  else {
                    setPlainSkip((current) => {
                      const next = { ...current }
                      delete next[ri]
                      return next
                    })
                    setEyesInclude((current) => ({ ...current, [ri]: choice === 'new' }))
                  }
                  setQueueDecided((current) => ({ ...current, [ri]: true }))
                }
                const undoQueueChoice = () => {
                  setQueueDecided((current) => {
                    const next = { ...current }
                    delete next[ri]
                    return next
                  })
                  setQueueAmountCorrected((current) => {
                    const next = { ...current }
                    delete next[ri]
                    return next
                  })
                  const original = queueFixOriginal[ri]
                  if (original) {
                    setPreview((current) => {
                      if (!current) return current
                      const drafts = current.drafts.slice()
                      const draft = drafts[i]
                      if (!draft) return current
                      drafts[i] = {
                        ...draft,
                        amount: original.amount,
                        kind: original.amount >= 0 ? 'income' : 'expense',
                      }
                      return { ...current, drafts }
                    })
                    setTouched((current) => {
                      const next = { ...current }
                      const row = { ...(next[ri] || {}) }
                      if (original.touched) row.amount = true
                      else delete row.amount
                      if (Object.keys(row).length) next[ri] = row
                      else delete next[ri]
                      return next
                    })
                    setQueueFixOriginal((current) => {
                      const next = { ...current }
                      delete next[ri]
                      return next
                    })
                  }
                  if (resolved)
                    setMatchAction((current) => {
                      const next = { ...current }
                      delete next[ri]
                      return next
                    })
                  else {
                    setPlainSkip((current) => {
                      const next = { ...current }
                      delete next[ri]
                      return next
                    })
                    setEyesInclude((current) => {
                      const next = { ...current }
                      delete next[ri]
                      return next
                    })
                  }
                }
                const beginAmountFix = () => {
                  setQueueFixingIndex(ri)
                  setQueueFixValue(Math.abs(d.amount).toFixed(2))
                  setQueueFixOriginal((current) =>
                    current[ri]
                      ? current
                      : { ...current, [ri]: { amount: d.amount, touched: !!touched[ri]?.amount } },
                  )
                }
                const applyAmountFix = () => {
                  const parsed = Number(queueFixValue)
                  if (!Number.isFinite(parsed) || parsed < 0) return
                  const corrected = d.amount < 0 ? -Math.abs(parsed) : Math.abs(parsed)
                  updateDraft(i, { amount: corrected })
                  setPlainSkip((current) => {
                    const next = { ...current }
                    delete next[ri]
                    return next
                  })
                  setEyesInclude((current) => ({ ...current, [ri]: true }))
                  setQueueDecided((current) => ({ ...current, [ri]: true }))
                  setQueueAmountCorrected((current) => ({ ...current, [ri]: true }))
                  setQueueFixingIndex(null)
                  setQueueFixValue('')
                }
                // The queue only ever holds REVIEW-band and needs-eyes rows now (a weak row has a
                // safe default and stays out — SIUM-REVIEW-QUEUE), so `Change match` here is the
                // ordinary manual-link list, and a re-point is the ordinary `matchOverride`.
                const changeOptions = manualLinkOptions(d)
                const hasChangeAlternative =
                  !!resolved &&
                  changeOptions.some((option) => option.id !== resolved.id && !option.taken)
                const changeQueueMatch = (id: string) => {
                  const summary = checkRowById.get(id)
                  if (!summary || d.rowIndex == null) return
                  if (isManualLinked(d))
                    setManualLink((current) => ({ ...current, [d.rowIndex!]: { id, summary } }))
                  else
                    setMatchOverride((current) => ({ ...current, [d.rowIndex!]: { id, summary } }))
                  setMatchAction((current) => ({ ...current, [d.rowIndex!]: 'merge' }))
                  setQueueDecided((current) => ({ ...current, [d.rowIndex!]: true }))
                }
                const projection = resolved
                  ? afterImportProjection({
                      action,
                      draft: d,
                      existing: resolved.summary,
                      flagType: resolvedFlagType(d, resolved),
                      catMap: catMapProp,
                      accounts,
                      anchorAccountId: d.destId || accountId,
                    })
                  : null
                const anchorId = d.destId || accountId
                const existingValue = resolved
                  ? anchorPerspectiveAmount(resolved.summary, anchorId, d.amount)
                  : null
                const existingCurrency = resolved
                  ? resolved.summary.kind === 'transfer'
                    ? resolved.summary.to_account_id === anchorId
                      ? resolved.summary.to_currency || resolved.summary.currency || d.currency
                      : resolved.summary.account_id === anchorId
                        ? resolved.summary.from_currency || resolved.summary.currency || d.currency
                        : resolved.summary.currency || d.currency
                    : resolved.summary.currency || d.currency
                  : d.currency
                const existingAmount =
                  typeof existingValue === 'number'
                    ? fCur(existingValue, existingCurrency, { cents: true })
                    : 'its current amount'
                // Handoff Cmp: BOTH sides of the comparison show a date + amount footer — the
                // ledger side was missing its date entirely (owner, 2026-07-27: "why don't we show
                // date for all on your ledger view").
                const existingDate = resolved
                  ? (resolved.summary as ExistingSummaryLike & { date?: string }).date
                  : null
                const statementAmount = fCur(d.amount, d.currency, { cents: true })
                const mergeAdopts =
                  !!resolved &&
                  mergeAdoptsStatementFacts(
                    d.kind,
                    resolved.summary.kind,
                    resolvedFlagType(d, resolved),
                  )
                const receipt = resolved
                  ? action === 'merge'
                    ? mergeAdopts
                      ? `linked • ${existingAmount} → ${statementAmount}`
                      : `linked • yours stays ${existingAmount}`
                    : action === 'new'
                      ? 'keeps both • 2 entries'
                      : `left out • yours stays ${existingAmount}`
                  : action === 'new'
                    ? `${statementAmount} • confirmed${ri != null && queueAmountCorrected[ri] ? ' • corrected' : ''}`
                    : 'left out'
                // Handoff `QueueCard`: stays FULLY EXPANDED whether decided or not — deciding
                // never collapses or removes a card, so the list can't jump out from under the
                // user. Once decided the chosen option highlights ('on'), and a ✓ receipt appears
                // in the header with Undo right there.
                // Compact one-row segmented control (owner, 2026-07-27: matching the matched-row's
                // own Link / Import / Leave out tabs) — replaces the stacked
                // .imp3-choice cards; the "why"/"Lucius's call" text that used to sit under each
                // option lives in its title tooltip now instead.
                const origLine = originalCharge(d, d.currency)
                const existingOriginal = resolved
                  ? originalCharge(resolved.summary, existingCurrency)
                  : null
                const queueTransfer = transferLine(d, accounts, catMapProp, { compact: true })
                const queueCategory =
                  queueTransfer ||
                  (d.category ? catMapProp[d.category]?.label || d.category : 'Uncategorized')
                return (
                  <article
                    key={i}
                    className={'imp3-qcard' + (decided ? ' done' : '')}
                    data-draft-index={i}
                  >
                    <div className="imp3-qcard-top">
                      <span className="imp3-qn">
                        {position} of {queueItems.length}
                      </span>
                      <span className="imp3-qtitle">
                        {title} • {d.date.slice(5)} •{' '}
                        <b>{fCur(d.amount, d.currency, { cents: true })}</b>
                        {origLine && (
                          <span
                            className="imp3-orig"
                            title="Original charge — before currency conversion"
                          >
                            {' '}
                            ({origLine})
                          </span>
                        )}
                      </span>
                      {decided ? (
                        <span className="imp3-qcard-done">
                          <i aria-hidden="true">✓</i>
                          {receipt}
                        </span>
                      ) : (
                        !resolved && (
                          <span className="imp3-conf">
                            Needs your eyes
                            {typeof d.confidence === 'number'
                              ? ` • ${Math.round(d.confidence * 100)}%`
                              : ''}
                          </span>
                        )
                      )}
                      {/* Same rule as the two row surfaces — a routed transfer has no payee/category
                      to edit — even though this card's header has no collision to fix. */}
                      {!d.dup && !queueTransfer && (
                        <button
                          type="button"
                          className="imp3-editpencil"
                          title="Edit payee & category"
                          aria-label={`Edit payee and category for ${title}`}
                          onClick={() => setEditingDraftIndex(i)}
                        >
                          <svg
                            width="12"
                            height="12"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                          </svg>
                        </button>
                      )}
                      {decided && (
                        <button type="button" className="imp3-undo" onClick={undoQueueChoice}>
                          Undo
                        </button>
                      )}
                    </div>
                    {resolved ? (
                      <div className="imp3-cmp">
                        <div className="imp3-cmp-col imp-pair-side stmt">
                          <div className="imp3-cmp-h">On the statement</div>
                          <div className="imp3-cmp-name">{d.payee || d.description}</div>
                          <div className="imp3-cmp-src">{d.description}</div>
                          <div className="imp3-cmp-facts">
                            <span>{d.date.slice(5)}</span>
                            {origLine && (
                              <span
                                className="imp3-orig"
                                title="Original charge — before currency conversion"
                              >
                                {origLine}
                              </span>
                            )}
                            <b>{fCur(d.amount, d.currency, { cents: true })}</b>
                          </div>
                        </div>
                        <div className="imp3-cmp-mid">
                          {typeof existingValue === 'number' &&
                          Math.abs(existingValue - d.amount) > 0.004 ? (
                            existingCurrency === d.currency ? (
                              <span
                                className="imp3-cmp-drift"
                                title={`Statement ${statementAmount} • yours ${existingAmount}`}
                              >
                                Δ{' '}
                                {fCur(Math.abs(existingValue - d.amount), d.currency, {
                                  cents: true,
                                }).replace('−', '')}
                              </span>
                            ) : (
                              <span
                                className="imp3-cmp-drift"
                                title="Different currencies — not directly comparable"
                              >
                                ≠ different currency
                              </span>
                            )
                          ) : (
                            <span className="imp3-cmp-eq" aria-hidden="true">
                              =
                            </span>
                          )}
                        </div>
                        <div className="imp3-cmp-col imp-pair-side ledger">
                          <div className="imp3-cmp-h">In your ledger</div>
                          <div className="imp3-cmp-name">
                            <button
                              type="button"
                              className="imp3-cmp-lk"
                              title="Open this transaction"
                              onClick={() => openExistingTxn(resolved.id)}
                            >
                              {resolved.summary.note || '—'}
                            </button>
                          </div>
                          <div className="imp3-cmp-src">
                            {existingTxnLine(resolved.summary, accounts, catMapProp, d.currency, {
                              anchorAccountId: d.destId || accountId,
                              omitFacts: true,
                            })}
                          </div>
                          <div className="imp3-cmp-facts">
                            <span>{existingDate ? existingDate.slice(5) : '—'}</span>
                            {existingOriginal && (
                              <span
                                className="imp3-orig"
                                title="Original charge — before currency conversion"
                              >
                                {existingOriginal}
                              </span>
                            )}
                            <b>{existingAmount}</b>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="imp3-cmp-src pad">{d.description}</div>
                    )}
                    <div className="imp3-why">
                      <i aria-hidden="true">✦</i>
                      <span>{reason}</span>
                    </div>
                    {resolved && hasChangeAlternative && (
                      <div className="imp3-actrow">
                        <LinkPicker
                          label="Change match"
                          listLabel={`Change the ledger match for ${title}`}
                          options={changeOptions.map((o) => ({
                            id: o.id,
                            line: o.line,
                            taken: o.taken,
                            selected: o.id === resolved.id,
                            hint:
                              o.id === resolved.id
                                ? '• current'
                                : o.taken
                                  ? '• matched to another row'
                                  : undefined,
                          }))}
                          onPick={changeQueueMatch}
                        />
                        {isMatchOverridden(d) && (
                          <button
                            type="button"
                            className="imp3-lpick-btn"
                            aria-label="Back to Lucius's pick"
                            onClick={() => {
                              if (d.rowIndex == null) return
                              setMatchOverride((current) => {
                                const next = { ...current }
                                delete next[d.rowIndex!]
                                return next
                              })
                              setMatchAction((current) => ({ ...current, [d.rowIndex!]: 'merge' }))
                              setQueueDecided((current) => ({ ...current, [d.rowIndex!]: true }))
                            }}
                          >
                            Back to Lucius’s pick
                          </button>
                        )}
                      </div>
                    )}
                    <span
                      className="imp3-seg"
                      role="group"
                      aria-label={`Choose what happens to ${title}`}
                    >
                      {resolved && (
                        <button
                          type="button"
                          aria-pressed={action === 'merge'}
                          className={action === 'merge' ? 'on' : ''}
                          title={
                            (!decided && action === 'merge' ? 'Lucius’s call • ' : '') +
                            (mergeAdopts
                              ? `${existingAmount} → ${statementAmount} • cleared ✓`
                              : 'Stays as booked • cleared ✓')
                          }
                          onClick={() => setQueueChoice('merge')}
                        >
                          {mergeAdopts
                            ? `Same one — update to ${statementAmount}`
                            : 'Same one — link'}
                        </button>
                      )}
                      <button
                        type="button"
                        aria-pressed={action === 'new'}
                        className={action === 'new' ? 'on' : ''}
                        title={
                          (!decided && resolved && action === 'new' ? 'Lucius’s call • ' : '') +
                          (resolved ? '2 entries' : 'flagged for review')
                        }
                        onClick={() => setQueueChoice('new')}
                      >
                        {resolved ? 'Different — keep both' : `Include ${statementAmount}`}
                      </button>
                      {!resolved && !twoChoicePairOut && !fixingAmount && (
                        <button
                          type="button"
                          title="Correct a blurry or misread statement figure."
                          onClick={beginAmountFix}
                        >
                          Fix the amount
                        </button>
                      )}
                      <button
                        type="button"
                        aria-pressed={action === 'skip'}
                        className={action === 'skip' ? 'on' : ''}
                        title={
                          (!decided && resolved && action === 'skip' ? 'Lucius’s call • ' : '') +
                          (resolved
                            ? `Yours stays ${existingAmount}`
                            : 'Nothing from this statement row will be booked.')
                        }
                        onClick={() => setQueueChoice('skip')}
                      >
                        Leave out
                      </button>
                    </span>
                    {!resolved && !twoChoicePairOut && fixingAmount && (
                      <div
                        className="imp3-fixrow"
                        role="group"
                        aria-label={`Correct amount for ${title}`}
                      >
                        <span>{d.currency}</span>
                        <input
                          autoFocus
                          inputMode="decimal"
                          type="number"
                          min="0"
                          step="0.01"
                          aria-label={`Correct amount for ${title}`}
                          value={queueFixValue}
                          onChange={(event) => setQueueFixValue(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault()
                              applyAmountFix()
                            }
                          }}
                        />
                        <button type="button" className="imp3-fixgo" onClick={applyAmountFix}>
                          Use this
                        </button>
                        <button
                          type="button"
                          className="imp3-fixno"
                          onClick={() => {
                            setQueueFixingIndex(null)
                            setQueueFixValue('')
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                    {/* Always shown — previews the CURRENTLY selected action (the pre-picked
                    default, or an explicit override), not just after an explicit click.
                    Production-only, deliberate deviation from the handoff's own QueueCard
                    (which gates <After> behind an explicit decision) — owner, 2026-07-27:
                    "the after import, must be shown" / "remove the interim state". Booking
                    itself still only happens at the final Confirm & book step. */}
                    <div className="imp3-after">
                      <span className="imp3-after-l">
                        {action === 'merge'
                          ? 'After link'
                          : action === 'new'
                            ? 'After import'
                            : 'After'}
                        {projection && projection.records.length > 1
                          ? ` • ${projection.records.length} records`
                          : ''}
                      </span>
                      {projection && projection.records.length > 0 ? (
                        projection.records.map((record, ri) => (
                          <span key={ri} className={'imp3-after-rec imp-after-rec ' + record.tag}>
                            <b>{record.title}</b>
                            {record.date && <span>{record.date.value}</span>}
                            <span>{record.category}</span>
                            {record.amount && (
                              <span className="amt">
                                {record.amount.old && (
                                  <>
                                    <s className="imp-after-old">{record.amount.old}</s>
                                    <span className="imp-after-arrow">→</span>
                                  </>
                                )}
                                <span className={record.amount.old ? 'imp-after-new' : ''}>
                                  {record.amount.value}
                                </span>
                              </span>
                            )}
                            {/* The pair row's own After strip already rendered this; the queue
                            card's copy silently dropped it. Same class, same wording. */}
                            {record.original && (
                              <span className="imp-after-orig">
                                {record.original.added ? '+ original charge ' : 'original '}
                                {record.original.value}
                              </span>
                            )}
                            {record.badges.map((b) => (
                              <em key={b}>{b}</em>
                            ))}
                          </span>
                        ))
                      ) : action === 'new' ? (
                        <span className="imp3-after-rec">
                          <b>{title}</b>
                          <span>{d.date.slice(5)}</span>
                          <span>{queueCategory}</span>
                          <span className="amt">{statementAmount}</span>
                          {origLine && <span className="imp-after-orig">original {origLine}</span>}
                          <em>new entry</em>
                        </span>
                      ) : (
                        <span className="imp3-after-none">
                          Nothing books — this row won’t be in your ledger.
                        </span>
                      )}
                    </div>
                  </article>
                )
              }
              return (
                <div className="imp-pane imp-pane--ws imp3-scroll">
                  {preview.drafts.length === 0 ? (
                    <EmptyState
                      title="No transactions found in this statement"
                      body="Lucius couldn't find any rows to import — try a different file, or map columns yourself."
                      action={
                        <Button variant="secondary" size="sm" onClick={reset}>
                          Try another file
                        </Button>
                      }
                    />
                  ) : (
                    <>
                      {(() => {
                        // Statement-side facts live HERE, not on a card row (owner decision 2026-07-16:
                        // ONE counting surface per meaning — outcomes belong to the verdict + footer,
                        // bands to the chips). Rows-read always; already-imported/skipped only when
                        // nonzero, dim; the skipped fact keeps the removed card's view/hide toggle.
                        const rowsRead = preview.total ?? preview.drafts.length
                        const dups = preview.duplicates ?? 0
                        // The real total (server's `skipped` int) — `preview.skipped` itself is a
                        // bounded first-10 DISPLAY sample (`skip_samples` on the wire), so its `.length`
                        // silently undercounts past 10.
                        const nSkipped = preview.skippedTotal ?? (preview.skipped ?? []).length
                        const shown = accounts.find(
                          (a) => a.id === (preview.resolvedAccountId || accountId),
                        )
                        const reconciled = preview.reconcile?.status === 'ok'
                        return (
                          // The handoff's facts line: the account · the printed period ·
                          // ✓ reconciled · the AI read cost pushed to the far right. Production's extra
                          // honest facts (rows read / already imported / skipped / instructions) ride the
                          // same line, dimmed, after the design's own.
                          <div className="imp3-facts">
                            {shown && <span>{shown.name}</span>}
                            {preview.meta?.period_start && (
                              <span>
                                {preview.meta.period_start} → {preview.meta.period_end || '…'}
                              </span>
                            )}
                            {reconciled && (
                              <span
                                className="imp3-facts-ok"
                                title="Every row checks out against the statement’s own printed balances"
                              >
                                ✓ reconciled
                              </span>
                            )}
                            {preview.meta?.payment_due_date && (
                              <span>
                                Due {preview.meta.payment_due_date}
                                {preview.meta.amount_due != null
                                  ? ` • ${fCur(preview.meta.amount_due, preview.meta.currency || account?.currency || 'SGD', { cents: true })}`
                                  : ''}
                                {preview.meta.minimum_payment
                                  ? ` • min ${fCur(preview.meta.minimum_payment, preview.meta.currency || account?.currency || 'SGD', { cents: true })}`
                                  : ''}
                              </span>
                            )}
                            {preview.meta?.rewards?.balance != null && (
                              <span>
                                {preview.meta.rewards.balance.toLocaleString()}{' '}
                                {preview.meta.rewards.unit || 'points'}
                              </span>
                            )}
                            <span className="imp3-facts-dim">
                              {rowsRead} row{rowsRead === 1 ? '' : 's'} read
                            </span>
                            {dups > 0 && (
                              <span
                                className="imp3-facts-dim"
                                title="Booked by a previous import of this file (matched by reference id) — they will not import again"
                              >
                                {dups} already imported
                              </span>
                            )}
                            {/* "You've imported this exact file before" — a quiet fact on this
                                line, not a floating toast: the toast sat at the viewport's
                                tab-bar clearance, which inside this sheet is the footer, so the
                                footer painted over half of it. Anything already booked stays
                                deduped either way. */}
                            {preview.alreadyImported && (
                              <span
                                className="imp3-facts-dim"
                                title="You've imported this exact file before. Anything already booked stays deduped."
                              >
                                file imported before
                                {preview.alreadyImported.imported_at
                                  ? ` ${fDate(preview.alreadyImported.imported_at, 'short', 'always')}`
                                  : ''}
                              </span>
                            )}
                            {nSkipped > 0 && (
                              <span className="imp3-facts-dim">
                                {nSkipped} skipped •{' '}
                                <button
                                  type="button"
                                  className="imp3-facts-btn"
                                  aria-expanded={showSkipped}
                                  onClick={() => setShowSkipped((v) => !v)}
                                >
                                  {showSkipped ? 'hide' : 'view'}
                                </button>
                              </span>
                            )}
                            {typeof preview.llmCostUsd === 'number' && (
                              <span
                                className="imp3-facts-cost"
                                title="What reading this file cost in AI calls — also counted in Settings → AI costs"
                              >
                                {preview.llmCostUsd > 0
                                  ? `AI read ≈ $${preview.llmCostUsd.toFixed(3)}`
                                  : 'read from memory'}
                                {' • '}
                                <button
                                  type="button"
                                  className="imp3-facts-btn"
                                  title="Re-runs the AI read of this file (under the current instructions) and bills again"
                                  onClick={() =>
                                    runServerPreview(route, undefined, undefined, true)
                                  }
                                >
                                  Re-read
                                </button>
                              </span>
                            )}
                          </div>
                        )
                      })()}
                      {/* Right where "view" was tapped, not buried past the verdict/list — a user
                toggling this from the facts line had no reason to expect it only shows up
                after scrolling all the way to the bottom. */}
                      {(preview.skipped ?? []).length > 0 && showSkipped && (
                        <div className="imp-skipped">
                          <div className="imp-skipped-t">
                            {preview.skippedTotal ?? (preview.skipped ?? []).length} skipped row
                            {(preview.skippedTotal ?? (preview.skipped ?? []).length) === 1
                              ? ''
                              : 's'}{' '}
                            — not imported
                            {/* The list itself is a bounded first-10 sample (`skip_samples` on the wire)
                      — say so once the true total runs past what's actually listed below,
                      rather than letting the count and the list quietly disagree. */}
                            {(preview.skippedTotal ?? 0) > (preview.skipped ?? []).length &&
                              ` (showing first ${(preview.skipped ?? []).length})`}
                          </div>
                          {(preview.skipped ?? []).map((s, i) => (
                            <div key={i} className="imp-skipped-row">
                              <span className="imp-skipped-desc">
                                {s.description || (s.line != null ? 'Row ' + s.line : 'Row')}
                              </span>
                              <span className="imp-skipped-reason">{s.reason}</span>
                              {Number.isFinite(s.amount) && (
                                <span className="imp-skipped-amt">
                                  {(s.amount || 0) < 0 ? '' : '+'}
                                  {fCur(s.amount || 0, s.currency || account?.currency || 'SGD', {
                                    cents: true,
                                  })}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      {/* The instructions-applied note, the format-profile provenance, the flagged-row
                heads-up and the multi-currency routing note are all "what Lucius wants you to
                know about this read" — they render together in the design's own <LuciusNote>
                panel below the verdict, ONE box (owner, 2026-07-27: two adjacent violet boxes
                read as duplicated, not two distinct pieces of information). */}
                      <div className="imp3-verdict">
                        {(() => {
                          // The two-question verdict (SI-64, owner's words): "after importing, is
                          // everything ok? would all statement transactions be in my ledger, and would
                          // the amounts match" — computed LIVE from the current row choices (same
                          // reactivity as the After column: every chip/toggle/pick re-runs this render).
                          // Inputs + the amounts verdict are the component-level verdictInputs/amtV —
                          // the same objects the Off-by markers and pill read.
                          const cov = coverageVerdict(verdictInputs)
                          const amt = amtV
                          const perCur = (per: Record<string, number>, signed: boolean) =>
                            Object.entries(per)
                              .map(
                                ([ccy, v]) =>
                                  (signed ? (v >= 0 ? '+' : '−') : '') +
                                  fCur(Math.abs(v), ccy, { cents: true }),
                              )
                              .join(' • ')
                          // The tie-out waterfall ("Show the math") is GONE from the UI (owner, 2026-07-31:
                          // "let's not show the math for now, i don't know if it's really correct, everything
                          // looks always add up! doesn't make sense"). They are right — the waterfall's drift
                          // term is SOLVED FOR server-side (services/statements/ledger_diff.py) as whatever
                          // makes the identity balance, so its "adds up" tick was tautological and silently
                          // absorbed any real discrepancy. The backend totals stay (a genuine independent
                          // query, and the ledger-only section's own subtotal still reads them).
                          const covFrags = [
                            cov.linked > 0
                              ? `${cov.linked} linked to entr${cov.linked === 1 ? 'y' : 'ies'} you already had`
                              : null,
                            cov.imported > 0 ? `${cov.imported} imported new` : null,
                            cov.dups > 0 ? `${cov.dups} already booked` : null,
                            cov.absorbed > 0
                              ? `${cov.absorbed} folded into transfer${cov.absorbed === 1 ? '' : 's'}`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(' • ')
                          // The handoff's verdict shape: one recessed panel, one LINE per question, each
                          // line a round status badge + a bold headline + a dim `<em>` breakdown. The
                          // production reconcile check, the ledger-side residue and the honesty caveat
                          // ride the SAME line vocabulary rather than inventing their own — a verdict
                          // reads as a verdict whichever question it answers.
                          const recon = preview.reconcile
                          const lineOf = (
                            tone: 'ok' | 'warn' | 'wait' | 'info',
                            node: React.ReactNode,
                          ) => ({ tone, node })
                          // Line 0 — does the statement parse against its own printed balances? A
                          // production fact the prototype's fixture always had as `✓ reconciled` on the
                          // facts line; only its FAILURE modes need a verdict line.
                          const reconLine =
                            recon && recon.status !== 'ok'
                              ? (() => {
                                  // "Not available" isn't a warning — there's simply nothing to check the read
                                  // against, so it gets a neutral/gray treatment instead of the amber ⚠ used
                                  // for the two genuine problems. `.imp3-vline.info` mirrors `.ok`/`.wait`/
                                  // `.warn`'s own pattern.
                                  const unavailable =
                                    recon.status !== 'off' && recon.status !== 'ok_if_flipped'
                                  return lineOf(
                                    unavailable ? 'info' : 'warn',
                                    <div
                                      className={'imp3-vline ' + (unavailable ? 'info' : 'warn')}
                                    >
                                      <span className="imp3-vic" aria-hidden="true">
                                        {unavailable ? '•••' : '⚠'}
                                      </span>
                                      <div>
                                        <b>
                                          {recon.status === 'off'
                                            ? `Statement is off by ${fCur(Math.abs(recon.delta || 0), recon.currency || account?.currency || 'SGD', { cents: true })} against its own balances`
                                            : recon.status === 'ok_if_flipped'
                                              ? 'Statement reconciles only with money in/out flipped'
                                              : 'Balance check not available for this statement'}
                                        </b>
                                        <em>
                                          {recon.status === 'off'
                                            ? 'Some rows may be missing or doubled — worth a look before booking.'
                                            : recon.status === 'ok_if_flipped'
                                              ? 'Check the amount direction on a row or two.'
                                              : 'Nothing to check the read against, so nothing is proven complete.'}
                                        </em>
                                      </div>
                                    </div>,
                                  )
                                })()
                              : null
                          // Line 1 — coverage: every statement row's representative post-commit.
                          const coverageLine =
                            awaitingDecisions > 0
                              ? lineOf(
                                  'wait',
                                  <div className="imp3-vline wait imp-verdict-line">
                                    <span className="imp3-vic" aria-hidden="true">
                                      {awaitingDecisions}
                                    </span>
                                    <div>
                                      <b>
                                        {awaitingDecisions} transaction
                                        {awaitingDecisions === 1 ? '' : 's'} still need your call
                                      </b>
                                      <em>Review each card below before booking.</em>
                                    </div>
                                  </div>,
                                )
                              : cov.ok
                                ? lineOf(
                                    'ok',
                                    <div className="imp3-vline ok imp-verdict-line">
                                      <span className="imp3-vic" aria-hidden="true">
                                        ✓
                                      </span>
                                      <div>
                                        <b>Every statement transaction will be in your ledger</b>
                                        {covFrags && <em>{covFrags}</em>}
                                      </div>
                                    </div>,
                                  )
                                : lineOf(
                                    'warn',
                                    <div className="imp3-vline warn imp-verdict-line">
                                      <span className="imp3-vic" aria-hidden="true">
                                        ⚠
                                      </span>
                                      <button
                                        type="button"
                                        className="imp3-voff"
                                        onClick={() => jumpToChip('uncovered')}
                                      >
                                        <b>
                                          {cov.uncovered} statement transaction
                                          {cov.uncovered === 1 ? '' : 's'} won{'’'}t be anywhere
                                          after import
                                        </b>
                                        <em>
                                          Left out with no ledger match. Click to see{' '}
                                          {cov.uncovered === 1 ? 'it' : 'them'}.
                                        </em>
                                      </button>
                                    </div>,
                                  )
                          // Line 2 — amounts: projected post-commit figures vs the statement's own. The
                          // warn form is the handoff's `imp3-voff` BUTTON: clicking it filters the list
                          // to exactly the drifted rows via the offby chip.
                          const amountsLine =
                            awaitingDecisions > 0
                              ? lineOf(
                                  'wait',
                                  <div className="imp3-vline wait imp-verdict-line">
                                    <span className="imp3-vic" aria-hidden="true">
                                      …
                                    </span>
                                    <div>
                                      <b>
                                        Amounts settle once you decide the {awaitingDecisions} row
                                        {awaitingDecisions === 1 ? '' : 's'} above
                                      </b>
                                      <em>Linking adopts the statement’s figures.</em>
                                    </div>
                                  </div>,
                                )
                              : amt.represented > 0
                                ? amt.ok
                                  ? lineOf(
                                      'ok',
                                      <div className="imp3-vline ok imp-verdict-line">
                                        <span className="imp3-vic" aria-hidden="true">
                                          ✓
                                        </span>
                                        <div>
                                          <b>Amounts match the statement to the cent</b>
                                          <em>
                                            These transactions will total {perCur(amt.totals, true)}{' '}
                                            in your ledger.
                                          </em>
                                        </div>
                                      </div>,
                                    )
                                  : lineOf(
                                      'warn',
                                      <div className="imp3-vline warn imp-verdict-line">
                                        <span className="imp3-vic" aria-hidden="true">
                                          ⚠
                                        </span>
                                        <button
                                          type="button"
                                          className="imp3-voff"
                                          onClick={() => jumpToChip('offby')}
                                        >
                                          <b>
                                            {Object.keys(amt.deltas).length > 0
                                              ? `Your ledger ends ${offByAmount} off the statement`
                                              : `${amt.kept} row${amt.kept === 1 ? '' : 's'} kept at your original figure${amt.kept === 1 ? '' : 's'}`}
                                          </b>
                                          <em>
                                            {amt.kept} row{amt.kept === 1 ? ' keeps' : 's keep'}{' '}
                                            your older figure{amt.kept === 1 ? '' : 's'}
                                            {Object.keys(amt.deltas).length > 0
                                              ? ''
                                              : ' — the differences cancel out'}{' '}
                                            — choose Link &amp; update to adopt the settled ones.
                                            Click to see them.
                                          </em>
                                        </button>
                                      </div>,
                                    )
                                : null
                          // Line 3 (only when nonzero) — the ledger-side residue; the detail (which
                          // rows, why, their Delete/Show-match action) lives ONLY below (SI-61).
                          // A withheld ledgerCheck means the section below never renders and the
                          // 'ledger' chip never exists, so this line (which jumps to it) must not.
                          const ledgerLine =
                            ledgerOnlyRows.length > 0 && !preview.ledgerCheck?.withheld
                              ? lineOf(
                                  'warn',
                                  <div className="imp3-vline warn imp-verdict-line">
                                    <span className="imp3-vic" aria-hidden="true">
                                      ⚠
                                    </span>
                                    <button
                                      type="button"
                                      className="imp3-voff"
                                      onClick={() => jumpToChip('ledger')}
                                    >
                                      <b>
                                        {ledgerOnlyRows.length} ledger{' '}
                                        {ledgerOnlyRows.length === 1 ? 'entry' : 'entries'} from
                                        this period{' '}
                                        {ledgerOnlyRows.length === 1 ? 'isn’t' : 'aren’t'} on this
                                        statement
                                      </b>
                                      <em>
                                        Click to see {ledgerOnlyRows.length === 1 ? 'it' : 'them'}.
                                      </em>
                                    </button>
                                  </div>,
                                )
                              : null
                          // There is NO "your instructions were not applied" line here any more, and that
                          // is deliberate. It existed because a failed pass used to return
                          // `luciusPass.status === 'unavailable'` and let the preview render anyway —
                          // a warning banner above a live Confirm button. The owner's verdict (2026-08-09)
                          // was that a warning is the wrong resolution: "if it failed, it should fail, it
                          // shouldn't show an unfinished response". The pass now RAISES, so this screen is
                          // only ever reached by a read whose instructions WERE applied, and there is no
                          // failure state left for a line to report.
                          const lines = [reconLine, coverageLine, amountsLine, ledgerLine].filter(
                            (
                              l,
                            ): l is {
                              tone: 'ok' | 'warn' | 'wait' | 'info'
                              node: React.ReactNode
                            } => l !== null,
                          )
                          const warnCount = lines.filter((l) => l.tone === 'warn').length
                          const waitCount = lines.filter((l) => l.tone === 'wait').length
                          // One collapsed summary row (worst tone wins) — expand for the per-question detail.
                          const summaryTone = waitCount > 0 ? 'wait' : warnCount > 0 ? 'warn' : 'ok'
                          const summaryIcon =
                            summaryTone === 'wait' ? '…' : summaryTone === 'warn' ? '⚠' : '✓'
                          // "Ready to import" is a LIE when the commit would write nothing — the owner re-read
                          // a fully-booked statement and got a green "ready to import" above a list where all
                          // 41 rows read "Already imported". Same test the footer uses for its
                          // "File statement — nothing new" CTA, so the headline and the button can never
                          // disagree. Reached only on the ok tone, i.e. no warnings and no pending decisions,
                          // so "already in your ledger" is the only way to have nothing to do.
                          const nothingToDo =
                            links === 0 && imports === 0 && markedForDelete.length === 0
                          const summaryText =
                            waitCount > 0
                              ? `${awaitingDecisions} transaction${awaitingDecisions === 1 ? '' : 's'} still need your call`
                              : warnCount > 0
                                ? `${warnCount} thing${warnCount === 1 ? '' : 's'} to check before importing`
                                : nothingToDo
                                  ? 'Nothing new — this statement is already in your ledger'
                                  : 'Looks good — ready to import'
                          return (
                            <>
                              <button
                                type="button"
                                className={'imp3-vsummary ' + summaryTone}
                                aria-expanded={verdictOpen}
                                onClick={() => setVerdictOpen((o) => !o)}
                              >
                                <span className="imp3-vic" aria-hidden="true">
                                  {summaryIcon}
                                </span>
                                <b>{summaryText}</b>
                                <svg
                                  width="13"
                                  height="13"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2.4"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  aria-hidden="true"
                                >
                                  <path d={verdictOpen ? 'M18 15l-6-6-6 6' : 'M6 9l6 6 6-6'} />
                                </svg>
                              </button>
                              {verdictOpen && (
                                <div className="imp3-vdetail">
                                  {lines.map((l, i) => (
                                    <Fragment key={i}>{l.node}</Fragment>
                                  ))}
                                  {!lc && (
                                    <div className="imp3-verdict-note">
                                      No printed period on this statement — the ledger cross-check
                                      needs one.
                                    </div>
                                  )}
                                </div>
                              )}
                            </>
                          )
                        })()}
                      </div>
                      {/* An incomplete parse or a statement that doesn't reconcile against its
                own balances can't tell "not on this statement" from "never read" — the server
                withholds ledger_check entirely (no rows, no boundary) rather than guess (ADR
                2026-09-09-an-incomplete-parse-withholds-the-ledger-diff), so no band/bulk-delete
                renders below. This is the one on-screen explanation, reusing the
                pre-reconciliation-date banner's box verbatim (never `.imp-error`/`var(--bad)` —
                the rows already read are still safe to import). */}
                      {preview.ledgerCheck?.withheld &&
                        (() => {
                          const lc = preview.ledgerCheck!
                          const n = lc.pages_incomplete || 0
                          const s = n === 1 ? '' : 's'
                          const body =
                            lc.withheld === 'parse_incomplete'
                              ? `${n} page${s} of this statement didn’t finish reading, so we ` +
                                `can’t tell what’s missing from it yet. The rows we did read are ` +
                                `shown above and are safe to import — re-read the file to try ` +
                                `the incomplete page${s} again.`
                              : lc.withheld === 'parse_unverified'
                                ? `This statement was read before completeness checks existed, ` +
                                  `so we can’t tell what’s missing from it yet. The rows are ` +
                                  `still safe to import — re-read the file to enable the ` +
                                  `ledger comparison.`
                                : `This statement is off by ` +
                                  `${fCur(Math.abs(lc.delta ?? 0), lc.currency || account?.currency || 'SGD', { cents: true })} ` +
                                  `against its own balances, so we can’t trust a ledger-only ` +
                                  `comparison yet. The rows above are still safe to import — ` +
                                  `re-read the file, or double-check the statement’s totals.`
                          return (
                            <div className="imp-recon">
                              <div className="imp-recon-txt">
                                <b>Can’t check what’s already in your ledger yet</b> {body}
                              </div>
                              <div className="imp-recon-action">
                                <button
                                  type="button"
                                  className="imp3-facts-btn"
                                  onClick={() =>
                                    runServerPreview(route, undefined, undefined, true)
                                  }
                                >
                                  Re-read the file
                                </button>
                              </div>
                            </div>
                          )
                        })()}
                      {/* HIDDEN 2026-07-27 (owner: "AI still needed for categorization and stuff" — the
                fp.learned copy overclaims "no AI needed" when category suggestions etc. can
                still call Lucius after the format is learned; "I will work on it later, for
                now, not show it"). LuciusNote itself is kept intact for when the copy is
                revisited — the `false &&` keeps this a normal (checked, not dead-code-elided)
                expression rather than an unreachable statement, so it still type-checks. */}
                      {/* eslint-disable-next-line @typescript-eslint/no-non-null-assertion -- dead branch, never executes */}
                      {false && <LuciusNote preview={preview!} />}
                      {earlierRows.length > 0 && (
                        <div className="imp-recon">
                          <div className="imp-recon-txt">
                            <b>
                              {earlierRows.length} transaction
                              {earlierRows.length === 1 ? ' is' : 's are'} dated before{' '}
                              {account?.name} was last reconciled ({account?.lastReconciled}).
                            </b>
                          </div>
                          <label className="imp-recon-tog">
                            <input
                              type="checkbox"
                              checked={includeEarlier}
                              onChange={(e) => setIncludeEarlier(e.target.checked)}
                            />{' '}
                            Include {earlierRows.length} earlier transaction
                            {earlierRows.length === 1 ? '' : 's'}
                          </label>
                        </div>
                      )}
                      {chipsEnabled &&
                        (() => {
                          // Filter chips (owner directive 2026-07-16): single-select, All default, counts
                          // = the §8 structural populations (exactly what selecting the chip shows). Only
                          // non-empty band chips render; order is the confidence gradient. Labels are
                          // band-true, never outcome-shaped (owner 2026-07-16: "Matched"/"New", not
                          // "Will link"/"To import" — those read as the verdict's outcome counts, which
                          // they are not: bands are stable as actions flip).
                          const allChips: Array<{
                            key: ChipKey
                            label: string
                            n: number
                            cls: string
                          }> = [
                            { key: 'all', label: 'All', n: preview.drafts.length, cls: '' },
                            // Exception-first, per the handoff's own chip order (review3.jsx) and the
                            // stylesheet's own header comment: what needs you comes before what is settled.
                            { key: 'needs', label: 'Needs you', n: chipPop.needs, cls: ' needs' },
                            { key: 'strong', label: 'Will link', n: chipPop.strong, cls: ' link' },
                            { key: 'new', label: 'Import', n: chipPop.new, cls: '' },
                            { key: 'skip', label: 'Left out', n: chipPop.skip, cls: ' skip' },
                            {
                              key: 'uncovered',
                              label: 'Not in ledger',
                              n: uncoveredRows.length,
                              cls: ' skip',
                            },
                            {
                              key: 'ledger',
                              label: 'Not on statement',
                              n: ledgerOnlyRows.length,
                              cls: '',
                            },
                          ]
                          // A withheld ledgerCheck has an UNKNOWN "not on statement" count, not
                          // a zero one — the chip is dropped outright rather than shown at 0.
                          const chips = allChips.filter(
                            (c) =>
                              c.key === 'all' ||
                              (c.n > 0 && !(c.key === 'ledger' && preview.ledgerCheck?.withheld)),
                          )
                          // The handoff's filter bar: rounded pills, the count in its own `<i>` badge,
                          // active = filled surface2 + border, and — on Needs you only — a warn `<em>`
                          // dot while anything is still undecided.
                          return (
                            <nav className="imp3-filter" aria-label="Filter statement rows">
                              <div className="imp3-filter-scroll">
                                {chips.map((c) => (
                                  <button
                                    key={c.key}
                                    type="button"
                                    aria-pressed={chip === c.key}
                                    className={chip === c.key ? 'on' : ''}
                                    onClick={() =>
                                      setActiveChip(
                                        chip === c.key && c.key !== 'all' ? 'all' : c.key,
                                      )
                                    }
                                  >
                                    {/* The handoff's `All` button is bare text — no count badge at all
                          (review3.jsx:597, hardcoded outside the counted-chip map entirely). */}
                                    {c.key === 'all' ? (
                                      c.label
                                    ) : (
                                      <>
                                        {c.label} {c.n > 0 && <i>{c.n}</i>}
                                      </>
                                    )}
                                    {c.key === 'needs' && awaitingDecisions > 0 && (
                                      <em title={`${awaitingDecisions} undecided`} />
                                    )}
                                  </button>
                                ))}
                                {/* The transient Off-by pill (owner motivation 2026-07-17): present only
                      while some linked row keeps a drifted figure; amount/count mirror the
                      verdict line (same helper, same map). Click toggles the drift filter;
                      the ✕ (active only) and the All chip both clear it. Same pill chrome as
                      every other chip — the handoff has ONE CTA family, warn-tinted here. */}
                                {chipPop.offby > 0 && (
                                  <button
                                    type="button"
                                    aria-pressed={chip === 'offby'}
                                    className={'offby' + (chip === 'offby' ? ' on' : '')}
                                    title="Rows whose post-import figure will differ from the statement — click to see only them"
                                    onClick={() =>
                                      setActiveChip(chip === 'offby' ? 'all' : 'offby')
                                    }
                                  >
                                    Off by {offByAmount} <i>{chipPop.offby}</i>
                                    {chip === 'offby' && (
                                      <span className="x" aria-hidden="true">
                                        <X size={10} />
                                      </span>
                                    )}
                                  </button>
                                )}
                                {/* The transient `Possible matches` LENS (SIUM-CHIP-WEAK) — the same
                      category of chip as Off by, and deliberately NOT one of the partitioning
                      chips above: a weak row already lives under Import (or Will link once
                      linked). Present only while some possibility is still open; it vanishes
                      once every weak row is dismissed or linked, and never renders at all on
                      a statement with no weak flag. Muted, never amber. */}
                                {chipPop.weak > 0 && (
                                  <button
                                    type="button"
                                    aria-pressed={chip === 'weak'}
                                    className={'maybe' + (chip === 'weak' ? ' on' : '')}
                                    title="Rows the engine thinks MIGHT already be in your ledger — click to see only them"
                                    onClick={() => setActiveChip(chip === 'weak' ? 'all' : 'weak')}
                                  >
                                    Possible matches <i>{chipPop.weak}</i>
                                    {chip === 'weak' && (
                                      <span className="x" aria-hidden="true">
                                        <X size={10} />
                                      </span>
                                    )}
                                  </button>
                                )}
                              </div>
                              {/* A second, independent filter — by kind — ANDed with the decision chip
                      above. Right-aligned, single icon trigger (not standing chips) so it
                      doesn't compete visually with the decision chips beside it. Kept OUTSIDE
                      .imp3-filter-scroll: that div's overflow-x:auto implicitly forces
                      overflow-y:auto too, which would clip this button's popover. */}
                              {/* Show file sits with the filter (owner, 2026-10-26: the header
                      button was "too hidden"); a phone has Review | File tabs instead. */}
                              <div className="imp3-filter-tools">
                                <KindFilterButton
                                  value={kindFilter}
                                  onChange={setKindFilter}
                                  counts={kindPop}
                                  categories={categoryPop}
                                  category={categoryFilter}
                                  onCategoryChange={setCategoryFilter}
                                />
                                {fileStatementId && !isMobile && (
                                  <button
                                    type="button"
                                    className={'imp3-filetoggle' + (showFile ? ' on' : '')}
                                    aria-pressed={showFile}
                                    onClick={toggleShowFile}
                                  >
                                    <Ic d={I_FILE} size={15} />
                                    {showFile ? 'Hide file' : 'Show file'}
                                  </button>
                                )}
                              </div>
                            </nav>
                          )
                        })()}
                      {(() => {
                        // Contextual bulk bar: the Will-link chip surfaces its bulk actions directly
                        // above the list; under every other chip no bulk bar renders. It carries NO label
                        // of its own (owner 2026-07-31) — the `Will link` section header right beneath it
                        // already states the count, and a second number up here was what made the bar lie.
                        // The population rule is UNCHANGED and still load-bearing: the buttons mutate
                        // `visibleStmt`, the exact rows rendered beneath them, kind filter included. Do
                        // NOT re-derive a population here — nothing on screen would catch it drifting now.
                        if (chip !== 'strong' || !visibleStmt.length) return null
                        // One consistent word per action across the whole app — link, import, leave out —
                        // in the same order as every row's own decision control underneath.
                        const bulk: Array<[MatchAction, string]> = [
                          ['merge', 'Link all'],
                          ['new', 'Import all'],
                          ['skip', 'Leave all out'],
                        ]
                        return (
                          <div className="imp-pair-bandhead">
                            {bulk.map(([a, label]) => (
                              <button
                                key={label}
                                type="button"
                                className="imp-seg"
                                onClick={() => setAllForRows(visibleStmt, a)}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        )
                      })()}
                      <div className="imp3-review-list" ref={tableRef}>
                        {chip === 'needs' ? (
                          // The handoff's Needs-you queue: a plain header (not a section box) with the
                          // decided/total tally and the 1/2/3 keyboard hint, then the persistent cards.
                          <div className="imp3-queue" id="statement-import-needs">
                            <div className="imp3-queue-h">
                              <span className="imp3-queue-t">Needs you</span>
                              <span className="imp3-queue-n">
                                {awaitingDecisions > 0
                                  ? `${queueItems.length - awaitingDecisions} of ${queueItems.length} decided`
                                  : 'all decided ✓'}
                              </span>
                            </div>
                            {queueItems.map((item, index) => renderQueueCard(item, index + 1))}
                          </div>
                        ) : (
                          chip !== 'ledger' &&
                          chip !== 'uncovered' &&
                          (() => {
                            // Under a band filter the section NAMES the band it is showing (the
                            // prototype's own per-band section titles), and takes that band's badge tone;
                            // under All it is the one "statement — top to bottom" list.
                            // Handoff `sumStr` (review3.jsx): the New section's sub is the total of what it
                            // books, per currency — not blank. A blank sub leaves nothing to push the
                            // chevron to the row's right edge (only `imp3-sec-sub` carries `margin-left:
                            // auto`), so it used to land pinned right next to the title instead.
                            const newSub = (() => {
                              const byCcy = new Map<string, number>()
                              for (const { d } of visibleStmt)
                                byCcy.set(d.currency, (byCcy.get(d.currency) || 0) + d.amount)
                              return Array.from(byCcy.entries())
                                .map(([ccy, amt]) => fCur(amt, ccy, { cents: true }))
                                .join(' • ')
                            })()
                            const sec =
                              chip === 'strong'
                                ? {
                                    tone: 'link',
                                    title: 'Already in your ledger — will link',
                                    sub: 'categories & notes kept',
                                  }
                                : chip === 'new'
                                  ? { tone: 'new', title: 'Import', sub: newSub }
                                  : chip === 'skip'
                                    ? {
                                        tone: 'skip',
                                        title: 'Left out',
                                        sub: 'won’t book — you can override',
                                      }
                                    : chip === 'offby'
                                      ? {
                                          tone: 'skip',
                                          title: 'Kept at your original figures',
                                          sub: 'choose Link to adopt the settled ones',
                                        }
                                      : {
                                          tone: 'stmt',
                                          title: 'The statement — top to bottom',
                                          sub:
                                            awaitingDecisions > 0
                                              ? `${awaitingDecisions} need you`
                                              : '',
                                        }
                            return (
                              <section className={'imp3-sec ' + sec.tone}>
                                <button
                                  type="button"
                                  className="imp3-sec-h"
                                  aria-expanded={sectionOpen.statement}
                                  aria-controls="statement-import-rows"
                                  onClick={() =>
                                    setSectionOpen((current) => ({
                                      ...current,
                                      statement: !current.statement,
                                    }))
                                  }
                                >
                                  <span className="imp3-sec-badge">{visibleStmt.length}</span>
                                  <span className="imp3-sec-t">{sec.title}</span>
                                  {sec.sub && <span className="imp3-sec-sub">{sec.sub}</span>}
                                  <svg
                                    className={'imp3-chev' + (sectionOpen.statement ? ' open' : '')}
                                    width="13"
                                    height="13"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2.4"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                  >
                                    <path d="M6 9l6 6 6-6" />
                                  </svg>
                                </button>
                                {sectionOpen.statement && (
                                  <div className="imp3-sec-body" id="statement-import-rows">
                                    {visibleStmt.map((item) =>
                                      isPairDraft(item.d, item.i)
                                        ? renderPairRow(item)
                                        : renderPlainRow(item),
                                    )}
                                  </div>
                                )}
                              </section>
                            )
                          })()
                        )}
                        {showLedgerOnly &&
                          !preview.ledgerCheck?.withheld &&
                          (ledgerOnlyRows.length > 0 ||
                            boundary.length > 0 ||
                            onOtherStatementRows.length > 0) && (
                            <section className="imp3-sec ledger imp-pair-sect">
                              <button
                                type="button"
                                className="imp3-sec-h"
                                aria-expanded={sectionOpen.ledger}
                                aria-controls="statement-import-ledger-only"
                                onClick={() =>
                                  setSectionOpen((current) => ({
                                    ...current,
                                    ledger: !current.ledger,
                                  }))
                                }
                              >
                                <span className="imp3-sec-badge">{ledgerOnlyRows.length}</span>
                                <span className="imp3-sec-t">
                                  In your ledger, not on this statement
                                </span>
                                {t0 && t0.uncovered_net !== 0 && (
                                  <span className="imp3-sec-sub">
                                    {signedCur(t0.uncovered_net)}
                                  </span>
                                )}
                                <svg
                                  className={'imp3-chev' + (sectionOpen.ledger ? ' open' : '')}
                                  width="13"
                                  height="13"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2.4"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  aria-hidden="true"
                                >
                                  <path d="M6 9l6 6 6-6" />
                                </svg>
                              </button>
                              {sectionOpen.ledger && (
                                <div className="imp3-sec-body" id="statement-import-ledger-only">
                                  {/* Bulk marking, in the SAME shape as the Will-link bulk bar above
                        (`.imp-pair-bandhead` + `.imp-seg`, buttons only, no label of its own):
                        a reported case had ~5 duplicate ride charges, which is 5 taps
                        through the disclosure rows. No checkboxes, no selection mode — the
                        buttons act on exactly the entries rendered beneath them, which is the
                        same population rule the existing bar uses. */}
                                  {ledgerOnlyRows.length > 1 && (
                                    <div className="imp-pair-bandhead">
                                      <button
                                        type="button"
                                        className="imp-seg"
                                        onClick={() =>
                                          markAllForDelete(
                                            ledgerOnlyRows.map((r) => r.id),
                                            true,
                                          )
                                        }
                                      >
                                        Mark all {ledgerOnlyRows.length} for deletion
                                      </button>
                                      {markedForDelete.length > 0 && (
                                        <button
                                          type="button"
                                          className="imp-seg"
                                          onClick={() =>
                                            markAllForDelete(
                                              ledgerOnlyRows.map((r) => r.id),
                                              false,
                                            )
                                          }
                                        >
                                          Clear marks
                                        </button>
                                      )}
                                    </div>
                                  )}
                                  {ledgerOnlyRows.map(renderLedgerOnlyRow)}
                                  {renderBoundarySection()}
                                  {renderOtherStatementSection()}
                                </div>
                              )}
                            </section>
                          )}
                        {/* Coverage question 1's exact residue, in its own always-visible section (not
                  just a filter chip) — the mirror image of "In your ledger, not on this
                  statement" above, same header-chip chrome (badge/title/chevron), so both
                  sides of the mismatch are equally findable rather than one being a section
                  and the other a click-through (owner: "hard to know, hard to find"). */}
                        {showUncovered && uncoveredRows.length > 0 && (
                          <section className="imp3-sec skip imp-pair-sect">
                            <button
                              type="button"
                              className="imp3-sec-h"
                              aria-expanded={sectionOpen.uncovered}
                              aria-controls="statement-import-uncovered"
                              onClick={() =>
                                setSectionOpen((current) => ({
                                  ...current,
                                  uncovered: !current.uncovered,
                                }))
                              }
                            >
                              <span className="imp3-sec-badge">{uncoveredRows.length}</span>
                              <span className="imp3-sec-t">
                                On the statement, not in your ledger
                              </span>
                              <span className="imp3-sec-sub">
                                not in your ledger — won’t be anywhere after import
                              </span>
                              <svg
                                className={'imp3-chev' + (sectionOpen.uncovered ? ' open' : '')}
                                width="13"
                                height="13"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2.4"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                aria-hidden="true"
                              >
                                <path d="M6 9l6 6 6-6" />
                              </svg>
                            </button>
                            {sectionOpen.uncovered && (
                              <div className="imp3-sec-body" id="statement-import-uncovered">
                                {uncoveredRows.map((item) =>
                                  isPairDraft(item.d, item.i)
                                    ? renderPairRow(item)
                                    : renderPlainRow(item),
                                )}
                              </div>
                            )}
                          </section>
                        )}
                      </div>
                      {/* Handoff `teach`: after an override, offer to remember the reason for this
                source. Wired to the REAL instructions endpoint (the same PUT the facts-line
                editor uses), never a local-only note. Absent until an override happens.
                HIDDEN 2026-07-27 (owner: "remove this from UI") — logic kept intact. */}
                      {false && teach !== 'idle' && teach !== 'done' && preview!.statementId && (
                        <div className="imp3-teach">
                          {teach === 'saved' ? (
                            <span className="imp3-teach-t">
                              <i>✦</i>Saved — applies to future imports from this source.
                            </span>
                          ) : (
                            <>
                              <span className="imp3-teach-t">
                                <i>✦</i>Remember something about {account?.name || 'this source'}’s
                                statements?
                              </span>
                              {teach === 'editing' ? (
                                <div className="imp3-teach-edit">
                                  <textarea
                                    autoFocus
                                    rows={2}
                                    maxLength={1000}
                                    aria-label="Instruction for this source"
                                    placeholder="e.g. Shopee orders often settle a few dollars higher with shipping — link them when the dates are close."
                                    value={teachText}
                                    onChange={(e) => setTeachText(e.target.value)}
                                  />
                                  {instrErr && <div className="imp3-teach-err">{instrErr}</div>}
                                  <button
                                    type="button"
                                    className="imp3-teach-save"
                                    disabled={!teachText.trim() || instrSaving}
                                    onClick={() => {
                                      const merged = [
                                        preview!.instructions?.text || '',
                                        teachText.trim(),
                                      ]
                                        .filter(Boolean)
                                        .join('\n')
                                      void saveInstructions(merged).then((ok) => {
                                        if (ok) setTeach('saved')
                                      })
                                    }}
                                  >
                                    {instrSaving ? 'Saving…' : 'Save for this source'}
                                  </button>
                                </div>
                              ) : (
                                <span className="imp3-teach-acts">
                                  <button
                                    type="button"
                                    className="imp3-teach-yes"
                                    onClick={() => setTeach('editing')}
                                  >
                                    Add an instruction
                                  </button>
                                  <button
                                    type="button"
                                    className="imp3-teach-no"
                                    onClick={() => setTeach('done')}
                                  >
                                    No thanks
                                  </button>
                                </span>
                              )}
                            </>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )
            })()}

          {step === 'done' && result && (
            <BookedScreen
              result={result}
              filename={file?.name || resumeFilename || url || 'this statement'}
              context={bookedContext()}
              deleteErrors={deleteErrors}
              refusedDuplicates={refusedDuplicates}
              doneDiff={doneDiff}
              taught={taught}
              reviewMarkState={reviewMarkState}
              reviewMarkError={reviewMarkError}
              onMarkAllReviewed={onMarkImportReviewed ? markAllReviewed : undefined}
              onViewInRegister={
                result.batchId && onViewImportBatch
                  ? () =>
                      onViewImportBatch(
                        result.batchId!,
                        `Import: ${file?.name || resumeFilename || url || 'this statement'}`,
                      )
                  : undefined
              }
              onUndo={onUndoImport}
              onReviewAgain={reviewAgain}
              onImportAnother={reset}
              onDone={close}
            />
          )}
        </div>
        {fileWanted && !isMobile && (
          <div
            className="imp3-filesplit"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize the file pane"
            tabIndex={0}
            onPointerDown={startFileResize}
            onKeyDown={nudgeFileWidth}
            onDoubleClick={resetFileWidth}
          />
        )}
        {fileWanted && (
          <aside
            className="imp3-filepane"
            aria-label="Statement file"
            style={fileWidth != null && !isMobile ? { flex: `0 1 ${fileWidth}px` } : undefined}
          >
            <div className="imp3-filepane-head">
              <span className="imp3-file-badge">
                {FORMAT_LABEL[detectFormat(filePaneItem?.filename || resumeFilename || '')] ||
                  'FILE'}
              </span>
              <span className="imp3-filepane-name">
                {filePaneItem?.filename || resumeFilename || file?.name || 'Statement'}
              </span>
              {fileRow && <span className="imp3-filepane-row">Row {fileRow}</span>}
              {!isMobile && (
                <button
                  type="button"
                  className="imp3-filepane-close"
                  aria-label="Close the file"
                  onClick={toggleShowFile}
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <div className="imp3-filepane-body">
              {filePaneItem ? (
                <DocumentPreview item={filePaneItem} highlightRow={fileRow} />
              ) : filePaneItem === null ? (
                <div className="imp3-filepane-hint">
                  This statement's file isn't stored, so it can't be shown here.
                </div>
              ) : (
                <PreviewLoading />
              )}
            </div>
          </aside>
        )}
      </Modal>
      {/* Existing-transaction drawer (SI-50) — opened from a matched-ledger-entry link INSIDE
        this modal, so it must stack above it, not behind it. TxnDetail and this Modal both
        render through Radix `Dialog.Portal` straight to `document.body` now, so an ancestor
        wrapper's stacking context can't reach the portalled drawer at all — `liftAboveModal`
        sets the z-index directly on the drawer's own root instead (see TxnDetail.tsx). */}
      {drawerTxn && (
        <TxnDetail
          txn={drawerTxn}
          accounts={accounts}
          categories={catList}
          payeeOptions={payeeOptions ?? []}
          payeeById={payeeById}
          baseCurrency={baseCurrency || account?.currency || 'SGD'}
          rates={rates ?? {}}
          liftAboveModal
          onClose={() => setDrawerTxn(null)}
          onSave={saveDrawerTxn}
          onDelete={(tx) => {
            if (!window.confirm(`“${tx.description || 'This transaction'}” will be removed.`))
              return
            void (onDeleteTxn ? onDeleteTxn(tx.id) : Promise.resolve()).then(() =>
              setDrawerTxn(null),
            )
          }}
          onMarkReviewed={(tx) => {
            void markDrawerTxnReviewed(tx.id)
          }}
          onManagePayees={onManagePayees ?? (() => {})}
          onCreateCategory={onCreateCategory ?? (() => {})}
          onLoadActivity={onLoadTxnActivity ?? (async () => FALLBACK_ACTIVITY)}
          onLoadSourceEntries={onLoadTxnSourceEntries ?? (async () => null)}
          onDetachSourceRef={detachDrawerSourceRef}
        />
      )}
      {editingDraftIndex !== null && preview?.drafts[editingDraftIndex] && (
        <EditDraftModal
          key={editingDraftIndex}
          d={preview.drafts[editingDraftIndex]!}
          catList={catList}
          payeeOptions={payeeOptions ?? []}
          onManagePayees={onManagePayees}
          onCreateCategory={onCreateCategory}
          onSave={(patch) => updateDraft(editingDraftIndex, patch)}
          transferAccounts={transferAccountsFor(preview.drafts[editingDraftIndex]!)}
          onSetTransfer={(accountId) =>
            void setRowType(preview.drafts[editingDraftIndex]!.rowIndex, accountId)
          }
          onClose={() => setEditingDraftIndex(null)}
        />
      )}
      {/* `processing` no longer reaches this confirm at all (B20): the read is server-side
        and outlives the connection regardless, so closing during it is "Continue in
        background", not "abandon a paid read" — see requestClose/continueInBackground. */}
      <ConfirmModal
        open={relinkAsk !== null}
        title="Move this link?"
        body={
          relinkAsk
            ? `${relinkAsk.row} is linked to ${relinkAsk.from}. Link it to ${relinkAsk.to} instead? ${relinkAsk.from} goes back to “In your ledger, not on this statement”.`
            : ''
        }
        confirmLabel="Move link"
        onConfirm={() => {
          relinkAsk?.run()
          setRelinkAsk(null)
        }}
        onClose={() => setRelinkAsk(null)}
      />
      <ConfirmModal
        open={pendingExit !== null}
        title={
          pendingExit === 'back' ? 'Go back and lose this review?' : 'Close and lose this review?'
        }
        body="Nothing has been booked yet — your row-by-row decisions (Import, Leave out, Link) on this statement aren't saved anywhere. Leaving now discards all of them; you'd need to read the file again."
        confirmLabel={pendingExit === 'back' ? 'Go back' : 'Discard review'}
        danger
        onConfirm={confirmExit}
        onClose={() => setPendingExit(null)}
      />
    </>
  )
}
