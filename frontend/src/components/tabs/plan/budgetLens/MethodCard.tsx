// budgetLens/MethodCard.tsx — one budgeting method as a card, shared by the first-run chooser
// (pick mode) and the Compare methods modal (static). Ported from the Budget Onboarding (cards)
// design — budget_onboarding/compare.jsx + diagrams.jsx (owner, 2026-09-25). The diagrams are
// one illustrative month, obviously-round figures, never the book's real data.
import type { CSSProperties, ReactElement, ReactNode } from 'react'
import * as PD from '../planData'
import { FITS } from './methodsData'
import type { FitId, MethodMeta } from './methodsData'
import { LuciusMark } from '../../../../ui/LuciusMark'
import './methods.css'

const { fSmart } = PD

export const Spark = ({ size = 13 }: { size?: number }) => (
  <LuciusMark size={size} aria-hidden="true" />
)

export const Check = ({ size = 12 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="3"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M5 12l5 5L20 7" />
  </svg>
)

const FIT_ICON: Record<FitId, ReactNode> = {
  every: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="M3 10h18M9 10v10" />
    </svg>
  ),
  shape: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <path d="M12 3a9 9 0 1 0 9 9h-9z" />
      <path d="M12 3v9h9" />
    </svg>
  ),
}

// ── Diagrams ────────────────────────────────────────────────────────────────────────────

function Row({
  n,
  pct,
  v,
  tone,
  stop,
}: {
  n: string
  pct: number
  v: string
  tone?: 'zero' | 'warn' | 'pool'
  stop?: boolean
}) {
  return (
    <div className="bo-dg-row">
      <span className="n">{n}</span>
      {tone === 'zero' ? (
        <span className="bo-dg-done">
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
          Every dollar assigned
        </span>
      ) : (
        <span className={'bo-track' + (stop ? ' empty' : '')}>
          <span className={'bo-fill ' + (tone || '')} style={{ width: pct + '%' }} />
        </span>
      )}
      <span className={'v bo-fig' + (stop ? ' stop' : '') + (tone === 'zero' ? ' zero' : '')}>
        {v}
      </span>
    </div>
  )
}

/** Zero-Based: one pool drains to zero into envelopes with targets. */
function ZeroDiagram() {
  return (
    <div className="bo-dg" aria-label="Zero-based: income pool drained to zero into envelopes">
      <Row n="To assign" pct={0} v={fSmart(0)} tone="zero" />
      <div className="bo-dg-rule" />
      <Row n="Home" pct={100} v={fSmart(2800)} />
      <Row n="Food" pct={100} v={fSmart(1150)} />
      <Row n="Savings" pct={100} v={fSmart(900)} />
    </div>
  )
}

/** 50/30/20: one bar, three shares, targets as ticks. */
function ProportionDiagram({ needs = 52, wants = 22, tNeeds = 50, tWants = 30 }) {
  const save = Math.max(0, 100 - needs - wants)
  return (
    <div aria-label={`50/30/20: ${needs}% needs, ${wants}% wants, ${save}% savings`}>
      <div className="bo-barwrap">
        <div className="bo-bar">
          <span className="bo-bar-seg needs" style={{ flexBasis: needs + '%' }}>
            {needs}%
          </span>
          <span className="bo-bar-seg wants" style={{ flexBasis: wants + '%' }}>
            {wants}%
          </span>
          <span className="bo-bar-seg save" style={{ flexBasis: save + '%' }}>
            {save}%
          </span>
        </div>
        <span className="bo-tick" style={{ left: tNeeds + '%' }}>
          <span>{tNeeds}</span>
        </span>
        <span className="bo-tick" style={{ left: tNeeds + tWants + '%' }}>
          <span>{tNeeds + tWants}</span>
        </span>
      </div>
      <div className="bo-legend">
        <span>
          <i style={{ background: 'var(--bk-needs)' }} />
          Needs
        </span>
        <span>
          <i style={{ background: 'var(--bk-wants)' }} />
          Wants
        </span>
        <span>
          <i style={{ background: 'var(--bk-save)' }} />
          Savings
        </span>
      </div>
    </div>
  )
}

const DIAGRAM: Record<MethodMeta['id'], () => ReactElement> = {
  zero_based: ZeroDiagram,
  proportional: () => <ProportionDiagram />,
}

// ── Facts ───────────────────────────────────────────────────────────────────────────────

const Dots = ({ n }: { n: number }) => (
  <span className="bo-dots" aria-label={n + ' of 3'}>
    {[1, 2, 3].map((i) => (
      <i key={i} className={i <= n ? 'on' : ''} />
    ))}
  </span>
)

function Facts({ m }: { m: MethodMeta }) {
  return (
    <dl className="bo-facts">
      <dt>Effort</dt>
      <dd>
        <Dots n={m.effort} />
        <span className="dim">{['', 'Low', 'Medium', 'High'][m.effort]}</span>
      </dd>
      <dt>If I overspend</dt>
      <dd className={m.stops === 'reports' ? 'dim' : ''}>
        {m.stops === 'must-resolve' ? (
          <>
            <span className="bo-ok">
              <Check size={11} />
            </span>
            You move money to cover it
          </>
        ) : (
          <>
            <span className="bo-no">—</span>You see it at month end
          </>
        )}
      </dd>
      <dt>Leftover money</dt>
      <dd className={m.carry ? '' : 'dim'}>
        {m.carry === true ? (
          <>
            <span className="bo-ok">
              <Check size={11} />
            </span>
            Stays in its envelope
          </>
        ) : (
          <>
            <span className="bo-no">—</span>Not tracked
          </>
        )}
      </dd>
    </dl>
  )
}

// ── The card ────────────────────────────────────────────────────────────────────────────

export function MethodCard({
  m,
  mode,
  selected,
  fit,
  current,
  more,
  onSelect,
  onMore,
}: {
  m: MethodMeta
  /** `pick` — a radio in the first-run chooser; `static` — a read-only card in Compare. */
  mode: 'pick' | 'static'
  selected?: boolean
  /** The fit the user picked points at this method. */
  fit?: boolean
  /** This is the book's current method (Compare only). */
  current?: boolean
  more: boolean
  onSelect?: () => void
  onMore: () => void
}) {
  const pick = mode === 'pick'
  const Diagram = DIAGRAM[m.id]
  return (
    <div
      className={'bo-card' + (selected ? ' on' : '') + (pick ? '' : ' static')}
      onClick={pick ? onSelect : undefined}
      role={pick ? 'radio' : undefined}
      aria-checked={pick ? !!selected : undefined}
      tabIndex={pick ? 0 : undefined}
      onKeyDown={
        pick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect?.()
              }
            }
          : undefined
      }
    >
      <div className="bo-card-head">
        <span className="bo-card-name">{m.name}</span>
        {current && <span className="bo-tag bo-tag--cur">Current</span>}
        {fit && !current && <span className="bo-tag bo-tag--fit">Fits you</span>}
        {!fit && !current && m.id === 'proportional' && pick && (
          <span className="bo-tag bo-tag--fit">Easiest start</span>
        )}
        {pick && <span className="bo-radio" aria-hidden="true" />}
      </div>
      <p className="bo-card-hook">{m.hook}</p>
      <div className="bo-card-diagram">
        <Diagram />
      </div>
      <Facts m={m} />
      <div>
        <button
          type="button"
          className="bo-card-more"
          onClick={(e) => {
            e.stopPropagation()
            onMore()
          }}
        >
          {more ? 'Less' : 'Is this for me?'}
        </button>
        {more && (
          <dl className="bo-card-detail">
            <dt>Best if</dt>
            <dd>{m.forWhom}</dd>
            <dt>Effort</dt>
            <dd>{m.cost}</dd>
          </dl>
        )}
      </div>
    </div>
  )
}

/** "What do you want from a budget?" — picking one points at the method that fits it. */
export function FitPicker({
  value,
  onChange,
}: {
  value: FitId | null
  onChange: (f: FitId | null) => void
}) {
  return (
    <div className="bo-fit" role="group" aria-label="What do you want from a budget?">
      {FITS.map((f) => (
        <button
          key={f.id}
          type="button"
          className={value === f.id ? 'on' : ''}
          style={{ '--fit': f.color } as CSSProperties}
          onClick={() => onChange(value === f.id ? null : f.id)}
        >
          <span className="bo-fit-ic">{FIT_ICON[f.id]}</span>
          {f.label}
        </button>
      ))}
    </div>
  )
}
