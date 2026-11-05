// Commitment list — ported verbatim from the design handoff's rows3.jsx: filter tabs
// (Active / At risk / Goals / Completed) with live counts, a sort select, dual-currency rows,
// and a contextual empty state per filter.
import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ReactElement } from 'react'
import type { Commitment } from '../../../types/commitments'
import { moneyClass } from '../../../utils/format'
import {
  KIND_LABELS,
  daysUntil,
  fDual,
  fmtDate,
  nextInstallment,
  paidPct,
  remainingBase,
  remainingTotal,
  riskState,
} from '../../../utils/commitments'
import { getBaseCurrency } from '../../../utils/format'
import { CheckIcon, ChevIcon, DocIcon, KIND_ICON, TargetIcon } from './icons'
import { EntityRow } from '../../../lucius/EntityRow'

export type CommitmentFilter = 'all' | 'risk' | 'goals' | 'done'
export type CommitmentSort = 'next' | 'risk' | 'remaining'

interface FilterEmptyProps {
  filter: CommitmentFilter
  hasAny: boolean
  onClear: (f: CommitmentFilter) => void
}

function FilterEmpty({ filter, onClear }: FilterEmptyProps) {
  const map: Record<
    CommitmentFilter,
    {
      Icon: () => ReactElement
      tone: string
      t: string
      d: string
      cta: string | null
      target: CommitmentFilter
    }
  > = {
    risk: {
      Icon: CheckIcon,
      tone: 'good',
      t: 'Nothing at risk',
      d: 'Every active commitment has funding lined up for its payments — no shortfalls in the current outlook.',
      cta: 'View all active',
      target: 'all',
    },
    goals: {
      Icon: TargetIcon,
      tone: '',
      t: 'No savings goals',
      d: 'Goals track progress toward a target with regular contributions.',
      cta: 'View all active',
      target: 'all',
    },
    done: {
      Icon: DocIcon,
      tone: '',
      t: 'Nothing completed yet',
      d: 'Commitments you finish or cancel are archived here — nothing has landed here so far.',
      cta: 'View active',
      target: 'all',
    },
    all: {
      Icon: DocIcon,
      tone: '',
      t: 'No active commitments',
      d: 'Every commitment is completed or cancelled. Check the archive or start a new one.',
      cta: 'View completed',
      target: 'done',
    },
  }
  const cfg = map[filter] || map.all
  return (
    <div className="c3-fempty">
      <span className={'c3-fempty-ic ' + cfg.tone}>
        <cfg.Icon />
      </span>
      <div className="c3-fempty-t">{cfg.t}</div>
      <div className="c3-fempty-d">{cfg.d}</div>
      {cfg.cta && (
        <button className="c3-fempty-btn" onClick={() => onClear(cfg.target)}>
          {cfg.cta}
        </button>
      )}
    </div>
  )
}

function Row({
  c,
  fx,
  onOpen,
}: {
  c: Commitment
  fx: Record<string, number>
  onOpen: (id: string) => void
}) {
  const rs = riskState(c)
  const Icon = KIND_ICON[c.kind] || DocIcon
  const pct = paidPct(c)
  const remB = remainingBase(c, fx)
  const ni = nextInstallment(c)
  const base = getBaseCurrency()
  let nextEl: ReactElement = <b>All paid</b>
  if (c.status === 'cancelled') nextEl = <b>Cancelled</b>
  else if (ni) {
    const days = daysUntil(ni.due_date)
    const when =
      days < 0
        ? `${-days}d overdue`
        : days === 0
          ? 'today'
          : days <= 14
            ? `in ${days}d`
            : fmtDate(ni.due_date)
    const nextCls = days < 0 ? 'od' : days <= 14 ? 'soon' : ''
    nextEl = (
      <>
        Next <b className={moneyClass()}>{fDual(ni.amount, c.currency, fx)}</b> •{' '}
        <span className={nextCls}>{when}</span>
      </>
    )
  }
  const paidN = c.installments.filter((i) => i.status === 'paid').length

  // EntityRow is a TRANSPARENT wrapper: it renders the host <button> itself and stamps
  // data-entity-id + draggable + data-ctx-*, which is what makes a commitment @-mentionable,
  // right-clickable ("Ask Lucius about this") and draggable into the dock. Wrapping instead
  // of replacing would put a non-grid element between .c2-item and .c2-row and break the
  // row layout.
  return (
    <EntityRow
      as="button"
      className="c2-row"
      type="commitment"
      id={c.id}
      label={c.name || '(unnamed)'}
      item={c}
      toFields={(it) => ({
        name: it.name,
        kind: it.kind,
        currency: it.currency,
        status: it.status,
        note: it.note,
        created_at: it.created_at,
      })}
      search={`${c.name} ${KIND_LABELS[c.kind]} ${c.note}`}
      sublabel={`${KIND_LABELS[c.kind]} • ${c.status}`}
      onClick={() => onOpen(c.id)}
    >
      <span className="c2-row-ic">
        <Icon />
      </span>
      <span className="c2-row-main">
        <span className="c2-row-name">
          {c.name || '(unnamed)'}
          {c.currency !== base && <span className="c3-ccy">{c.currency}</span>}
        </span>
        <span className="c2-row-meta" style={{ display: 'block' }}>
          {KIND_LABELS[c.kind]} • {nextEl}
        </span>
      </span>
      <span className="c2-row-prog">
        <span className="lbl">
          {paidN} of {c.installments.length} paid
        </span>
        <span className="c2-meter">
          <i className={c.kind === 'goal' ? 'goal' : ''} style={{ width: pct + '%' }} />
        </span>
      </span>
      <span className="c2-row-chip">
        {rs === 'risk' && (
          <span className="c2-chip short">
            {fDual(c.coverage?.shortfall_base ?? 0, base, fx)} unfunded
          </span>
        )}
        {rs === 'ok' && <span className="c2-chip ok">Funded</span>}
        {rs === 'track' && <span className="c2-chip track">On track</span>}
        {rs === 'behind' && <span className="c2-chip behind">Behind</span>}
        {rs === 'off' && (
          <span className="c2-chip track">
            {c.status === 'cancelled' ? 'Cancelled' : 'Completed'}
          </span>
        )}
      </span>
      <span className="c2-row-amt">
        <span className={moneyClass('v')} style={{ display: 'block' }}>
          {fDual(remB, base, fx)}
        </span>
        <span className="u" style={{ display: 'block' }}>
          {c.currency !== base
            ? `${fDual(remainingTotal(c), c.currency, fx).split(' • ')[0]} left`
            : 'left'}
        </span>
      </span>
      <span className="c2-row-chev">
        <ChevIcon />
      </span>
    </EntityRow>
  )
}

interface CommitmentRowsProps {
  list: Commitment[]
  fx: Record<string, number>
  onOpen: (id: string) => void
  filter: CommitmentFilter
  setFilter: (f: CommitmentFilter) => void
  sort: CommitmentSort
  setSort: (s: CommitmentSort) => void
  /** Fires with this card's own rendered height whenever it changes (filter switch, data
   *  change, resize). The caller uses it to size an invisible spacer AFTER this card, tall
   *  enough that switching to a shorter filter never shrinks the page below the user's
   *  current scroll position — without the card itself ever rendering taller than its own
   *  content. */
  onHeightChange?: (px: number) => void
}

export function CommitmentRows({
  list,
  fx,
  onOpen,
  filter,
  setFilter,
  sort,
  setSort,
  onHeightChange,
}: CommitmentRowsProps) {
  const cardRef = useRef<HTMLDivElement>(null)

  // Measured with useLayoutEffect, not useEffect+ResizeObserver — a filter switch shrinks this
  // card SYNCHRONOUSLY as part of React's own commit, but ResizeObserver's callback fires in a
  // separate, later frame (after the browser has already painted the shrunk card). That gap
  // was the flicker: one visible frame of "shrunk, not yet compensated" before the caller's
  // spacer caught up and the page snapped back down. useLayoutEffect runs synchronously right
  // after this commit's DOM mutations, before the browser paints anything — so the caller's
  // resulting spacer update lands in the SAME paint, never a later one.
  useLayoutEffect(() => {
    const el = cardRef.current
    if (el && onHeightChange) onHeightChange(el.offsetHeight)
  })

  // A window/orientation resize isn't triggered by a React render at all, so it needs its own
  // (necessarily async) observer — this is the one case useLayoutEffect above can't cover.
  useEffect(() => {
    const el = cardRef.current
    if (!el || !onHeightChange) return
    const ro = new ResizeObserver(() => onHeightChange(el.offsetHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [onHeightChange])

  const riskCount = list.filter((c) => c.coverage?.at_risk && c.status === 'active').length
  const doneCount = list.filter((c) => c.status !== 'active').length

  let rows = list.filter((c) => {
    if (filter === 'risk') return !!c.coverage?.at_risk && c.status === 'active'
    if (filter === 'goals')
      return (c.kind === 'goal' || c.kind === 'auto_pay') && c.status === 'active'
    if (filter === 'done') return c.status !== 'active'
    return c.status === 'active'
  })
  rows = [...rows].sort((a, b) => {
    if (sort === 'remaining') return remainingBase(b, fx) - remainingBase(a, fx)
    if (sort === 'risk') {
      const ar = a.coverage?.at_risk ? a.coverage.shortfall_base : -1
      const br = b.coverage?.at_risk ? b.coverage.shortfall_base : -1
      if (ar !== br) return br - ar
    }
    const an = nextInstallment(a)
    const bn = nextInstallment(b)
    return (an ? an.due_date : '9999').localeCompare(bn ? bn.due_date : '9999')
  })

  const seg = (key: CommitmentFilter, label: string, count?: number | null) => (
    <button key={key} className={filter === key ? 'on' : ''} onClick={() => setFilter(key)}>
      {label}
      {count != null && count > 0 && <span className="pill">{count}</span>}
    </button>
  )

  return (
    <section>
      <div className="c2-sec-head">
        <span className="c2-sec-title">
          Commitments{' '}
          <span className="c2-sec-count">
            {rows.length} of {list.length}
          </span>
        </span>
        <div className="c2-sec-tools">
          <div className="c2-seg">
            {seg('all', 'Active')}
            {seg('risk', 'At risk', riskCount)}
            {seg('goals', 'Goals')}
            {seg('done', 'Completed', doneCount > 0 ? doneCount : null)}
          </div>
          <select
            className="c2-select c2-select--sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as CommitmentSort)}
            aria-label="Sort commitments"
          >
            <option value="next">Sort: Next payment</option>
            <option value="risk">Sort: Most at risk</option>
            <option value="remaining">Sort: Remaining</option>
          </select>
        </div>
      </div>
      <div className="c2-card" style={{ marginTop: 10 }} ref={cardRef}>
        <div className="c2-thead" aria-hidden="true">
          <span />
          <span>Commitment</span>
          <span>Progress</span>
          <span className="r">Status</span>
          <span className="r">Remaining</span>
          <span />
        </div>
        {rows.length === 0 && (
          <FilterEmpty
            filter={filter}
            hasAny={list.some((c) => c.status === 'active')}
            onClear={setFilter}
          />
        )}
        {rows.map((c) => (
          <div
            key={c.id}
            className={
              'c2-item' +
              (riskState(c) === 'risk' ? ' at-risk' : '') +
              (c.status !== 'active' ? ' c3-dim' : '')
            }
          >
            <Row c={c} fx={fx} onOpen={onOpen} />
          </div>
        ))}
      </div>
    </section>
  )
}
