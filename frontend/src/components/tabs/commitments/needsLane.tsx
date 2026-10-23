// "Needs your attention" triage lane — ported verbatim from the design handoff's app.jsx
// `NeedsLane`: overdue installments, "looks paid" ledger-match suggestions, and due-soon
// actions, all in one consolidated, capped, expandable lane.
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Money } from '../../ui'
import { fBase } from '../../../utils/format'
import { BellIcon, CheckIcon, LinkIcon, TaskIcon } from './icons'
import { ArrowDown } from '../../../ui/icons'

export interface NeedItem {
  key: string
  kind: 'overdue' | 'confirm' | 'action'
  tone: 'pay' | 'overdue' | 'task'
  title: ReactNode
  desc: string
  cid?: string
  iid?: string
  aid?: string
  amt?: number
  payLabel?: string
  sug?: { commitment_id: string; installment_id: string; txn_id: string }
  fundingId?: string | null
}

interface NeedsLaneProps {
  items: NeedItem[]
  /** The server's overdue count and total (money out) — the figures the Dashboard's Next 30
   *  days card and the daily briefing quote. The lane also holds non-overdue items, so its own
   *  count alone is not "how many are overdue". */
  overdue: { count: number; total: number } | null
  cap: number | 'All'
  onConfirm: (it: NeedItem) => void
  onDismiss: (it: NeedItem) => void
  onAction: (it: NeedItem) => void
  onMiss: (it: NeedItem) => void
  onPay: (it: NeedItem) => void
  onOpenC: (id: string) => void
}

export function NeedsLane({
  items,
  overdue,
  cap,
  onConfirm,
  onDismiss,
  onAction,
  onMiss,
  onPay,
  onOpenC,
}: NeedsLaneProps) {
  const [showAll, setShowAll] = useState(false)
  if (!items.length) return null
  const CAP = cap === 'All' ? Infinity : cap
  const many = items.length > CAP
  const shown = many && !showAll ? items.slice(0, CAP) : items

  return (
    <section className="c2-needs">
      <div className="c2-needs-head">
        <BellIcon />
        <span className="c2-needs-title">Needs your attention</span>
        <span className="c2-needs-count">{items.length}</span>
        {overdue && (
          <span className="c2-needs-overdue">
            {overdue.count} overdue
            {overdue.total > 0 && (
              <>
                {' • '}
                <Money as="span">{fBase(overdue.total)}</Money>
              </>
            )}
          </span>
        )}
      </div>
      {shown.map((it) => (
        <div
          className={'c2-need' + (it.cid ? ' c3-need-open' : '')}
          key={it.key}
          role={it.cid ? 'button' : undefined}
          tabIndex={it.cid ? 0 : undefined}
          title={it.cid ? 'Open commitment' : undefined}
          onClick={
            it.cid
              ? (e) => {
                  if (!(e.target as HTMLElement).closest('button')) onOpenC(it.cid!)
                }
              : undefined
          }
          onKeyDown={
            it.cid
              ? (e) => {
                  if (e.key === 'Enter') onOpenC(it.cid!)
                }
              : undefined
          }
        >
          <span className={'c2-need-ic ' + it.tone}>
            {it.tone === 'pay' ? <LinkIcon /> : <TaskIcon />}
          </span>
          <div className="c2-need-main">
            <div className="c2-need-t">{it.title}</div>
            <div className="c2-need-d">{it.desc}</div>
          </div>
          <div className="c2-need-btns">
            {it.kind === 'confirm' ? (
              <>
                <Button variant="secondary" size="sm" onClick={() => onConfirm(it)}>
                  Confirm paid
                </Button>
                <Button variant="ghost" size="sm" onClick={() => onDismiss(it)}>
                  Not this
                </Button>
              </>
            ) : it.kind === 'overdue' ? (
              <button
                className="c2-need-icon-btn pay"
                aria-label={it.payLabel || 'Mark paid'}
                title={it.payLabel || 'Mark paid'}
                onClick={() => onPay(it)}
              >
                <CheckIcon />
              </button>
            ) : (
              <>
                <Button variant="secondary" size="sm" onClick={() => onAction(it)}>
                  Mark done
                </Button>
                <Button variant="ghost" size="sm" onClick={() => onMiss(it)}>
                  Not happening
                </Button>
              </>
            )}
          </div>
        </div>
      ))}
      {many && (
        <button className="c3-need-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? (
            'Show less'
          ) : (
            <>
              Show {items.length - CAP} more <ArrowDown size={12} aria-hidden="true" />
            </>
          )}
        </button>
      )}
    </section>
  )
}
