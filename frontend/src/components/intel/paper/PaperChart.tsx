import { signedPct } from '../following/following.format'
import { usePaperPerformance } from './usePaper'

const W = 760
const H = 180

/** Return since the start: this portfolio, your real investments and the benchmark, as three
 *  lines. A line the server could not price yet (a new portfolio, a missing benchmark) is left
 *  out rather than drawn flat. */
export function PaperChart({ id, benchmark }: { id: string; benchmark: string }) {
  const perf = usePaperPerformance(id, 'ALL').data
  if (!perf || perf.series.length < 2) return null
  const lines = [
    {
      name: 'This portfolio',
      key: 'paper' as const,
      end: perf.return_pct,
      color: 'var(--ink)',
      w: 2.2,
    },
    {
      name: 'Your investments',
      key: 'yours' as const,
      end: perf.yours_return_pct,
      color: 'var(--primary)',
      w: 1.5,
    },
    {
      name: benchmark,
      key: 'benchmark' as const,
      end: perf.benchmark.return_pct,
      color: 'var(--faint)',
      w: 1.5,
    },
  ].filter((l) => perf.series.some((pt) => pt[l.key] != null))
  const values = lines.flatMap((l) =>
    perf.series.map((pt) => pt[l.key]).filter((v): v is number => v != null),
  )
  const lo = Math.min(0, ...values)
  const hi = Math.max(0, ...values)
  const n = perf.series.length
  const y = (v: number) => H - 8 - ((v - lo) / (hi - lo || 1)) * (H - 16)
  const points = (key: 'paper' | 'yours' | 'benchmark') =>
    perf.series
      .map((pt, i) => (pt[key] == null ? null : `${(i / (n - 1)) * W},${y(pt[key] as number)}`))
      .filter(Boolean)
      .join(' ')
  return (
    <div className="paper-chart">
      <div className="paper-legend">
        {lines.map((l) => (
          <span key={l.key}>
            <i style={{ background: l.color }} />
            {l.name} {signedPct(l.end)}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Return since the start"
      >
        {[...lines].reverse().map((l) => (
          <polyline
            key={l.key}
            points={points(l.key)}
            fill="none"
            stroke={l.color}
            strokeWidth={l.w}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
    </div>
  )
}
