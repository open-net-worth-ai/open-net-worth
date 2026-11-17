import { paletteAt } from '../../../utils/constants'
import { moneyClass } from '../../../utils/format'
import type { VisualBlock } from '../../../types/chat'
import { formatVisualValue } from './format'

type ChartBlock = Extract<VisualBlock, { type: 'chart' }>

const num = (v: number | null | undefined): v is number => typeof v === 'number' && isFinite(v)

/** Positive right / negative left from a centre line. Widths are geometry only. */
export function VisualDiverging({ block }: { block: ChartBlock }) {
  const values = block.series[0]?.values ?? []
  const peak = Math.max(0, ...values.filter(num).map(Math.abs))
  return (
    <div className="ivis-rows">
      {block.labels.map((label, i) => {
        const v = values[i]
        const w = num(v) && peak > 0 ? (Math.abs(v) / peak) * 50 : 0
        return (
          <div className="ivis-row" key={i}>
            <span className="ivis-row-label">{label}</span>
            <span className="ivis-div-track">
              <span
                className={'ivis-div-bar ' + (num(v) && v < 0 ? 'neg' : 'pos')}
                style={
                  num(v) && v < 0
                    ? { right: '50%', width: `${w}%` }
                    : { left: '50%', width: `${w}%` }
                }
              />
            </span>
            <span className={moneyClass('ivis-row-value')}>
              {num(v) ? formatVisualValue(v, block.format, block.currency) : '—'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/** One 100%-style horizontal bar of parts, widths proportional to the values. */
export function VisualStack({ block }: { block: ChartBlock }) {
  const values = block.series[0]?.values ?? []
  const parts = block.labels
    .map((label, i) => ({ label, value: values[i], color: paletteAt(i) }))
    .filter(
      (p): p is { label: string; value: number; color: string } => num(p.value) && p.value > 0,
    )
  const total = parts.reduce((t, p) => t + p.value, 0)
  return (
    <div className="ivis-stack">
      <div className="ivis-stack-bar" role="img" aria-label={parts.map((p) => p.label).join(', ')}>
        {parts.map((p, i) => (
          <span key={i} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="ivis-legend">
        {parts.map((p, i) => (
          <span key={i} className="ivis-legend-item">
            <i style={{ background: p.color }} />
            {p.label}
            <span className={moneyClass('ivis-legend-value')}>
              {formatVisualValue(p.value, block.format, block.currency)}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

/** Per row, a bar of value against its target; the figure sits right-aligned. */
export function VisualProgress({ block }: { block: ChartBlock }) {
  const values = block.series[0]?.values ?? []
  return (
    <div className="ivis-rows">
      {block.labels.map((label, i) => {
        const v = values[i]
        const target = block.targets?.[i]
        const w = num(v) && num(target) && target > 0 ? Math.min(100, (v / target) * 100) : 0
        const over = num(v) && num(target) && v > target
        return (
          <div className="ivis-prog" key={i}>
            <div className="ivis-prog-head">
              <span className="ivis-row-label">{label}</span>
              <span className={moneyClass('ivis-row-value')}>
                {num(v) ? formatVisualValue(v, block.format, block.currency) : '—'}
                {num(target) && (
                  <span className="ivis-row-of">
                    {' / '}
                    {formatVisualValue(target, block.format, block.currency)}
                  </span>
                )}
              </span>
            </div>
            <span className="ivis-prog-track">
              <span
                className={'ivis-prog-fill' + (over ? ' over' : '')}
                style={{ width: `${w}%` }}
              />
            </span>
          </div>
        )
      })}
    </div>
  )
}
