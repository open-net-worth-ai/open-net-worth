// Tiny drawn charts for the setup nudge pictures (NudgePreview). Illustration only: fixed SAMPLE
// shapes, never a figure from the book. Colours come from the scene's `--nudge-hue`.

/** A net-worth-style line with a soft fill under it. */
export function Sparkline({ points, height = 34 }: { points: number[]; height?: number }) {
  const w = 100
  const max = Math.max(...points)
  const min = Math.min(...points)
  const xy = points.map((p, i) => {
    const x = (i / (points.length - 1)) * w
    const y = height - 3 - ((p - min) / (max - min || 1)) * (height - 6)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = xy[xy.length - 1]!.split(',')
  return (
    <svg className="setup-nudge-spark" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none">
      <polygon
        points={`0,${height} ${xy.join(' ')} ${w},${height}`}
        className="setup-nudge-spark-a"
      />
      <polyline points={xy.join(' ')} className="setup-nudge-spark-l" />
      <circle cx={last[0]} cy={last[1]} r="2.4" className="setup-nudge-spark-d" />
    </svg>
  )
}

/** Share of spending by category: a ring of arcs, the biggest in the scene's hue. */
export function Donut({ parts }: { parts: { label: string; pct: number }[] }) {
  const r = 15
  const c = 2 * Math.PI * r
  const lens = parts.map((p) => (p.pct / 100) * c)
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((a, b) => a + b, 0))
  return (
    <div className="setup-nudge-donut">
      <svg viewBox="0 0 40 40" width="44" height="44">
        {parts.map((p, i) => (
          <circle
            key={p.label}
            cx="20"
            cy="20"
            r={r}
            className={`setup-nudge-donut-s setup-nudge-donut-s--${i}`}
            strokeDasharray={`${lens[i]! - 1} ${c - lens[i]! + 1}`}
            strokeDashoffset={-starts[i]!}
          />
        ))}
      </svg>
      <div className="setup-nudge-legend">
        {parts.map((p, i) => (
          <span key={p.label}>
            <i className={`setup-nudge-donut-k setup-nudge-donut-k--${i}`} />
            {p.label}
          </span>
        ))}
      </div>
    </div>
  )
}

/** How far through a budget: a filled track with its label and figure. */
export function Progress({ label, value, pct }: { label: string; value: string; pct: number }) {
  return (
    <div className="setup-nudge-prog">
      <div className="setup-nudge-prog-h">
        <span>{label}</span>
        <b>{value}</b>
      </div>
      <div className="setup-nudge-prog-t">
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

/** Many things in, a few out: stacked bars narrowing to what matters. */
export function Funnel({ steps }: { steps: { label: string; pct: number }[] }) {
  return (
    <div className="setup-nudge-funnel">
      {steps.map((s, i) => (
        <div key={s.label} className="setup-nudge-funnel-r">
          <span
            className={`setup-nudge-funnel-b${i === steps.length - 1 ? ' setup-nudge-funnel-b--hi' : ''}`}
            style={{ width: `${s.pct}%` }}
          />
          <span className="setup-nudge-funnel-l">{s.label}</span>
        </div>
      ))}
    </div>
  )
}
