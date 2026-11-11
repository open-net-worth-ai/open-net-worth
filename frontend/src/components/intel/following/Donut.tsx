import { useState } from 'react'
import { donutParts } from './donutParts'

interface Props {
  weights: { symbol: string; weight: number }[]
  count: number
  size?: number
}

/** What is inside, at a glance: the top holdings as a ring, the count in the middle. Pointing at
 *  a segment names it in the middle; where there is room a legend sits beside it (HoldingRows). */
export function Donut({ weights, count, size = 132 }: Props) {
  const c = size / 2
  const r = size * 0.4
  const stroke = size * 0.13
  const circ = 2 * Math.PI * r
  const gap = 2
  const all = donutParts(weights, count)
  const starts = all.map((_, i) => all.slice(0, i).reduce((n, p) => n + p.weight * circ, 0))
  const [hover, setHover] = useState<number | null>(null)
  const shown = hover == null ? null : all[hover]
  return (
    <svg
      className="fol-donut"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={all.map((p) => `${p.label} ${(p.weight * 100).toFixed(0)}%`).join(', ')}
      onPointerLeave={() => setHover(null)}
    >
      <g transform={`rotate(-90 ${c} ${c})`}>
        {all.map((p, i) => {
          const len = Math.max(0, p.weight * circ - gap)
          const seg = (
            <circle
              key={`${p.symbol}-${i}`}
              cx={c}
              cy={c}
              r={r}
              fill="none"
              stroke={p.color}
              strokeWidth={stroke}
              strokeDasharray={`${len} ${circ - len}`}
              strokeDashoffset={-starts[i]!}
              opacity={hover == null || hover === i ? 1 : 0.35}
              onPointerEnter={() => setHover(i)}
            />
          )
          return seg
        })}
      </g>
      <text x={c} y={c + 2} className="fol-donut-n" textAnchor="middle">
        {shown ? `${(shown.weight * 100).toFixed(0)}%` : count}
      </text>
      <text x={c} y={c + 16} className="fol-donut-l" textAnchor="middle">
        {shown ? shown.label : 'stocks'}
      </text>
    </svg>
  )
}
