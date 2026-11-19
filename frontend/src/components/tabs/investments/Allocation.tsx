// Allocation — asset class target mix panel. Verbatim port of prototype Allocation() (non-editing view).
// Shows allocation bars with target markers and drift badges.

import { useState } from 'react'
import { ASSET_CLASS_COLOR, ASSET_CLASS_LABEL } from '../../../utils/constants'
import { EntityRow } from '../../../lucius/EntityRow'
import type { DerivedBook } from './useInvestmentsBook'
import { Button } from '../../ui'
import { UntrackedSlice } from './UntrackedHoldings'

function clsDisplayName(cls: string): string {
  return ASSET_CLASS_LABEL[cls] ?? cls.charAt(0).toUpperCase() + cls.slice(1).replace(/_/g, ' ')
}

interface AllocRow {
  cls: string
  cur: number
  tgt: number
}

interface Props {
  alloc: AllocRow[]
  d: DerivedBook
  onSaveTargets?: (targets: Record<string, number>) => void
}

function clsColor(cls: string): string {
  return ASSET_CLASS_COLOR[cls] ?? 'var(--muted)'
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

export function Allocation({ alloc, d, onSaveTargets }: Props) {
  // Drift (pp) at/above which a class is flagged "off target" — the server's ONE constant
  // (rebalance.DRIFT_FLAG_PP), never a number of our own (MATH-37).
  const thresholdPp = d.thresholdPp
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Record<string, number | string>>({})

  const hasTargets = alloc.some((a) => a.tgt > 0)

  const start = () => {
    setDraft(Object.fromEntries(alloc.map((a) => [a.cls, a.tgt])))
    setEditing(true)
  }

  const sum = Object.values(draft).reduce<number>((s, v) => s + (Number(v) || 0), 0)
  const balanced = Math.round(sum) === 100

  if (editing) {
    return (
      <Card title="Adjust target mix">
        <div className="mk-tgt-edit">
          {alloc.map((a) => (
            <div className="mk-tgt-erow" key={a.cls}>
              <span className="mk-tgt-elbl">
                <span className="mk-atl-dot" style={{ background: clsColor(a.cls) }} />
                {clsDisplayName(a.cls)}
              </span>
              <input
                className="mk-tgt-input"
                type="number"
                min="0"
                max="100"
                value={draft[a.cls] ?? ''}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    [a.cls]:
                      e.target.value === ''
                        ? ''
                        : Math.max(0, Math.min(100, Number(e.target.value))),
                  }))
                }
              />
            </div>
          ))}
        </div>
        <div className="mk-tgt-sum">
          <span>Must total 100%</span>
          <b style={{ color: balanced ? 'var(--good)' : 'var(--warn)' }}>{Math.round(sum)}%</b>
        </div>
        <div className="mk-tgt-foot">
          <Button
            variant="primary"
            size="sm"
            disabled={!balanced}
            onClick={() => {
              if (!balanced) return
              // draft holds percentages (0–100); caller/backend expects fractions (0–1)
              onSaveTargets?.(
                Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v) / 100])),
              )
              setEditing(false)
            }}
          >
            Save targets
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </Card>
    )
  }

  return (
    <Card
      title={hasTargets ? 'Allocation vs target' : 'Allocation'}
      meta={
        <button className="mk-card-edit" onClick={start}>
          {hasTargets ? 'Adjust' : 'Set targets'}
        </button>
      }
    >
      <div className="mk-alloc">
        {alloc.map((a) => {
          const drift = a.cur - a.tgt
          const off = Math.abs(drift) >= thresholdPp
          return (
            // Lucius mention entity: each slice is a draggable allocation_slice (ref
            // "asset_class:<cls>") so it can be @-mentioned or dragged into the dock.
            <EntityRow
              key={a.cls}
              type="allocation_slice"
              id={`asset_class:${a.cls}`}
              label={clsDisplayName(a.cls)}
              sublabel={hasTargets ? `${a.cur}% • target ${a.tgt}%` : `${a.cur}%`}
              item={a}
              toFields={(it) => ({ axis: 'asset_class', selector: it.cls })}
            >
              <div className="mk-atl-top">
                <span className="mk-atl-dot" style={{ background: clsColor(a.cls) }} />
                <span className="mk-atl-nm">{clsDisplayName(a.cls)}</span>
                <span className="mk-atl-cur money">{a.cur}%</span>
                {hasTargets && (
                  <span
                    className="mk-atl-drift money"
                    style={{
                      color: off ? 'var(--warn)' : 'var(--faint)',
                      background: off
                        ? 'color-mix(in srgb, var(--warn) 13%, transparent)'
                        : 'var(--bg-2)',
                    }}
                  >
                    {(drift > 0 ? '+' : drift < 0 ? '−' : '') + Math.abs(drift)}
                  </span>
                )}
              </div>
              <div className="mk-atl-track">
                <div
                  className="mk-atl-fill"
                  style={{ width: Math.min(a.cur, 100) + '%', background: clsColor(a.cls) }}
                />
                {hasTargets && a.tgt > 0 && (
                  <div className="mk-atl-tgt" style={{ left: Math.min(a.tgt, 100) + '%' }} />
                )}
              </div>
            </EntityRow>
          )
        })}
        {d.untracked && <UntrackedSlice u={d.untracked} />}
      </div>
      <div className="mk-alloc-foot">
        {hasTargets ? (
          <>
            <span className="mk-tgt-key">
              <i /> target
            </span>
            <span>• over the investment book (excl. cash)</span>
          </>
        ) : (
          <span>No target set. Over the investment book (excl. cash).</span>
        )}
      </div>
    </Card>
  )
}
