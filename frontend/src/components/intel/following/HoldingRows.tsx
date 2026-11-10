import { useState } from 'react'
import { fPct } from '../../../utils/format'
import type { FollowModel, HoldingChange, ModelHolding } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'

type Filter = 'all' | Exclude<HoldingChange, 'unchanged'>

const CHANGE: Record<Exclude<HoldingChange, 'unchanged'>, { cls: string; label: string }> = {
  new: { cls: 'up', label: 'New' },
  increased: { cls: 'up', label: 'Added' },
  reduced: { cls: 'down', label: 'Trimmed' },
}
const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'new', label: 'New' },
  { value: 'increased', label: 'Added' },
  { value: 'reduced', label: 'Trimmed' },
]

const pct = (n: number | null) => (n === null ? '—' : fPct(n, { fraction: true }))

function Row({ h, compare, top }: { h: ModelHolding; compare: boolean; top: number }) {
  const change = h.change === 'unchanged' ? null : CHANGE[h.change]
  return (
    <article className="fol-card">
      <div className="fol-grid">
        <div className="fol-id">
          <div className="fol-sym">{h.symbol}</div>
          <div className="fol-sub">{h.name}</div>
        </div>
        <span className="fol-track fol-wide" aria-hidden="true">
          <span className="fol-fill" style={{ width: `${Math.max(2, (h.weight / top) * 100)}%` }} />
        </span>
        <span className="fol-num">{pct(h.weight)}</span>
        <span className="fol-num fol-num--soft fol-wide">{compare ? pct(h.prev_weight) : ''}</span>
        {change ? <span className={`fol-pill ${change.cls}`}>{change.label}</span> : <span />}
      </div>
    </article>
  )
}

function Footnotes({ model }: { model: FollowModel }) {
  return (
    <>
      {model.exited.length > 0 && (
        <p className="fol-foot">
          {COPY.exited}{' '}
          {model.exited.map((x, i) => (
            <span key={x.symbol}>
              {i > 0 && ', '}
              <b>{x.symbol}</b> {x.name}
            </span>
          ))}
        </p>
      )}
      {model.unmatched.length > 0 && (
        <p className="fol-foot">
          {COPY.unmatched(model.unmatched.length)}{' '}
          {model.unmatched.map((x) => `${x.name} (${pct(x.weight)})`).join(', ')}
        </p>
      )}
    </>
  )
}

export function HoldingRows({ model }: { model: FollowModel }) {
  const [filter, setFilter] = useState<Filter>('all')
  const compare = model.exited.length > 0 || model.holdings.some((h) => h.change !== 'unchanged')
  const count = (f: Filter) =>
    f === 'all' ? model.holdings.length : model.holdings.filter((h) => h.change === f).length
  const rows = filter === 'all' ? model.holdings : model.holdings.filter((h) => h.change === filter)
  const top = Math.max(...model.holdings.map((h) => h.weight), 0.0001)
  return (
    <section className="fol-sec" aria-label={COPY.holdingsTitle}>
      <div className="fol-sec-head">
        <h3 className="fol-sec-title">{COPY.holdingsTitle}</h3>
        {compare && (
          <div className="fol-chips" role="tablist" aria-label={COPY.filterLabel}>
            {FILTERS.filter((f) => f.value === 'all' || count(f.value) > 0).map((f) => (
              <button
                key={f.value}
                type="button"
                role="tab"
                aria-selected={filter === f.value}
                className={`fol-chip${filter === f.value ? ' is-on' : ''}`}
                onClick={() => setFilter(f.value)}
              >
                {f.label}
                <span className="fol-chip-n">{count(f.value)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="fol-grid fol-head" aria-hidden="true">
        <span>Stock</span>
        <span className="fol-wide" />
        <span>Weight</span>
        <span className="fol-wide">{compare ? 'Last filing' : ''}</span>
        <span>{compare ? 'Change' : ''}</span>
      </div>
      {rows.map((h) => (
        <Row key={h.symbol} h={h} compare={compare} top={top} />
      ))}
      <Footnotes model={model} />
    </section>
  )
}
