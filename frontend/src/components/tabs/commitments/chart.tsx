// Cushion chart — ported verbatim from the design handoff's masthead.jsx `Chart`: cushion
// line + area, payments-out/funding-in bars, a $0 line, a dashed cash-floor line, and a
// per-month hover tooltip. The one series the server doesn't provide per-item detail for
// (which named commitment/source moved in a given month) is a graceful degrade — the
// tooltip shows the month's net in/out totals instead of a line-item breakdown (see
// PARITY_AND_GAPS.md).
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { CoverageTimelineMonth } from '../../../types/commitments'
import { fBase } from '../../../utils/format'
import { fCompactCurrency as fCompact, monthLong, monthShort } from '../../../utils/commitments'
import { useElementWidth } from './useElementWidth'
import { useNarrow } from './useNarrow'

interface ChartProps {
  timeline: CoverageTimelineMonth[]
  floor: number
  gapIdx: number
}

// The tooltip used to flip to right-alignment only for the LAST THREE months, which silently
// assumed a desktop-width chart. At phone width the plot is ~390px while the tip is up to
// 250px, so any month from roughly the middle onward overflowed the card — and once the tip
// became tap-reachable that overflow turned into a real horizontal page scroll.
//
// Anchor to the hovered slot's centre and open AWAY from the nearer edge, capping the width at
// the space actually available on that side. Containment then holds at every width by
// construction, with no DOM measurement: a tip anchored at p% opening rightward can never be
// wider than (100 - p)%.
export function tipStyle(index: number, count: number): CSSProperties {
  const pct = ((index + 0.5) / Math.max(count, 1)) * 100
  const GUTTER = '8px'
  return pct <= 50
    ? { left: `${pct}%`, maxWidth: `min(250px, calc(${100 - pct}% - ${GUTTER}))` }
    : { right: `${100 - pct}%`, maxWidth: `min(250px, calc(${pct}% - ${GUTTER}))` }
}

export function Chart({ timeline, floor, gapIdx }: ChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const cells = timeline
  const n = cells.length || 1
  // A viewBox'd SVG at width:100% renders at width ÷ aspect-ratio, so the desktop 760×152 box
  // is only ~86px tall on a phone and the bar band collapses to unreadable nubs. Pinning a CSS
  // height was the wrong fix: preserveAspectRatio="none" then stretches Y alone, which turns
  // the data dots into ellipses and floats the bar band away from the plot it belongs to
  // (owner: "the bars are detached"). Give the narrow chart its own, nearly-square-scaled
  // viewBox instead — same drawing code, honest geometry, no stretch.
  //
  // 2026-08-23: the aspect ratio was also UNBOUNDED. Height is width ÷ ratio with nothing
  // capping it, so the same chart rendered ~148px tall in a 740px pane and ~276px in a
  // 1380px one — six months adrift in a near-empty plot at the wide end, bars reduced to
  // ~15px nubs at the narrow end. It was only ever the size it was spaced for at exactly
  // 760px. Feeding the MEASURED width in as the viewBox width makes the box map 1:1 to CSS
  // pixels: constant height at every pane width, every vertical constant below still tuned
  // exactly as it was, and extra width spent on room per month instead of on inflation.
  const narrow = useNarrow()
  const plotRef = useRef<HTMLDivElement>(null)
  const measuredW = useElementWidth(plotRef)
  const H = narrow ? 180 : 152
  const W = Math.round(measuredW) || (narrow ? 360 : 760)
  const slot = W / n
  const cx = (k: number) => slot * (k + 0.5)

  const balances = cells.map((c) => c.balance)
  const maxBal = Math.max(...balances, floor || 0, 0, 1)
  const minBal = Math.min(...balances, 0)
  const A_TOP = 8
  const A_BOT = narrow ? 112 : 104
  const pad = (maxBal - minBal) * 0.12 || 1
  const balY = (v: number) =>
    A_TOP + (A_BOT - A_TOP) * (1 - (v - (minBal - pad)) / (maxBal + pad - (minBal - pad)))
  const zeroY = balY(0)

  const outs = cells.map((c) => c.total_out)
  const ins = cells.map((c) => c.total_in)
  const maxMag = Math.max(...outs, ...ins, 1)
  // Funding-in bars grow UPWARD from `baseB`, so the top of the tallest one is `baseB - span`.
  // THAT is the edge that must sit just below the plot floor for the two bands to read as one
  // chart. The previous narrow values (A_BOT 116, baseB 132, span 28) put it at 104 — ABOVE
  // the plot floor — so tall bars poked into the plot while short ones floated in the dead
  // 16-unit gap below it: the bar band looked detached and inconsistent at the same time.
  // Keep `baseB - span` a few units under A_BOT, matching the desktop relationship (108 vs 104).
  const span = narrow ? 26 : 20
  const baseB = narrow ? A_BOT + 5 + span : 128
  const magH = (v: number) => (v / maxMag) * span

  // A tap has no "pointer moved away", so the SVG's onMouseLeave never fires on touch and an
  // opened tooltip would otherwise stay pinned until another slot is tapped. Dismiss on any
  // pointer-down outside the chart, and on Escape for keyboard/desktop parity.
  useEffect(() => {
    if (hover == null) return
    const onDown = (e: PointerEvent) => {
      if (!(e.target as HTMLElement | null)?.closest?.('.c2-chart-plot')) setHover(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setHover(null)
    }
    document.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [hover])

  const linePts = cells.map((c, k) => `${cx(k)},${balY(c.balance)}`).join(' ')
  const areaPts = `${cx(0)},${A_BOT} ${linePts} ${cx(n - 1)},${A_BOT}`
  const hc = hover != null ? cells[hover] : null

  return (
    <div className="c2-chart-body">
      {/* A dedicated wrapper sized to exactly the SVG's own rendered box (not
          .c2-chart-body's taller box, which also includes the month-label row below) — the
          $0/floor labels and the hover tooltip position via percentages of THIS box's height,
          so their offsets land relative to the chart plot, not the whole component. */}
      <div className="c2-chart-plot" ref={plotRef}>
        <svg
          className="c2-chart-svg"
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Projected cash cushion against committed payments"
          onMouseLeave={() => setHover(null)}
        >
          {/* The at-risk region: everything from the gap month to the end of the horizon.
              `height` derives from A_BOT (was a hardcoded 106) so the band still stops at the
              plot floor now that the narrow viewBox moves it — see useNarrow() above. */}
          {gapIdx >= 0 && (
            <rect
              x={cx(gapIdx) - slot / 2}
              y="0"
              width={W - (cx(gapIdx) - slot / 2)}
              height={A_BOT + 2}
              fill="color-mix(in srgb, var(--bad) 8%, transparent)"
            />
          )}
          {hover != null && (
            <rect
              x={slot * hover}
              y="0"
              width={slot}
              height={H - 8}
              fill="color-mix(in srgb, var(--ink) 4%, transparent)"
            />
          )}
          <line
            x1="0"
            y1={zeroY}
            x2={W}
            y2={zeroY}
            stroke="var(--line-strong)"
            strokeWidth="1"
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
          {floor > 0 && (
            <line
              x1="0"
              y1={balY(floor)}
              x2={W}
              y2={balY(floor)}
              stroke="color-mix(in srgb, var(--warn) 65%, transparent)"
              strokeWidth="1"
              strokeDasharray="5 4"
              vectorEffect="non-scaling-stroke"
            />
          )}
          <polygon points={areaPts} fill="color-mix(in srgb, var(--primary) 11%, transparent)" />
          <polyline
            points={linePts}
            fill="none"
            stroke="var(--primary)"
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          {cells.map((c, k) => (
            <circle
              key={k}
              cx={cx(k)}
              cy={balY(c.balance)}
              r={k === gapIdx || k === hover ? 4 : 2.6}
              fill={c.balance < 0 ? 'var(--bad)' : 'var(--primary)'}
              stroke="var(--bg)"
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <line
            x1="0"
            y1={baseB}
            x2={W}
            y2={baseB}
            stroke="var(--line)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
          {cells.map((c, k) => {
            const bw = Math.min(slot * 0.34, 16)
            return (
              <g key={'b' + k}>
                {c.total_out > 0 && (
                  <rect
                    x={cx(k) - bw / 2}
                    y={baseB}
                    width={bw}
                    height={magH(c.total_out)}
                    rx="1.5"
                    fill="var(--bad)"
                    opacity=".85"
                  />
                )}
                {c.total_in > 0 && (
                  <rect
                    x={cx(k) - bw / 2}
                    y={baseB - magH(c.total_in)}
                    width={bw}
                    height={magH(c.total_in)}
                    rx="1.5"
                    fill="var(--good)"
                    opacity=".85"
                  />
                )}
              </g>
            )
          })}
          {/* The per-month hit slots bound ONLY onMouseEnter, so on a phone the entire
              breakdown (payments out, funding in, ending balance) was unreachable and the
              chart degraded to a picture. onPointerDown covers mouse, touch and pen in one
              handler; tapping the open month closes it again, which is the only dismiss a
              touch user has (there is no pointer to move away). */}
          {cells.map((_c, k) => (
            <rect
              key={'h' + k}
              x={slot * k}
              y="0"
              width={slot}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(k)}
              onPointerDown={() => setHover((cur) => (cur === k ? null : k))}
            />
          ))}
        </svg>

        <span className="c2-chart-zero" style={{ bottom: `calc(${(1 - zeroY / H) * 100}% + 6px)` }}>
          <span className="money">$0</span> cash
        </span>
        {floor > 0 && (
          <span
            className="c2-chart-floor"
            style={{ bottom: `calc(${(1 - balY(floor) / H) * 100}% + 6px)` }}
          >
            cash floor <span className="money">{fCompact(floor)}</span>
          </span>
        )}

        {hc && (
          <div className="c2-tip" style={tipStyle(hover ?? 0, n)}>
            <div className="c2-tip-m">{monthLong(hc.year_month)}</div>
            {hc.total_out > 0 && (
              <div className="c2-tip-row">
                <i className="out" />
                <span className="n">Payments due</span>
                <span className="a neg">−{fCompact(hc.total_out)}</span>
              </div>
            )}
            {hc.total_in > 0 && (
              <div className="c2-tip-row">
                <i className="in" />
                <span className="n">Funding in</span>
                <span className="a pos">+{fCompact(hc.total_in)}</span>
              </div>
            )}
            {hc.total_out <= 0 && hc.total_in <= 0 && (
              <div className="c2-tip-none">No committed payments or draws</div>
            )}
            <div className="c2-tip-foot">
              <span>Cushion, end of month</span>
              <b className={hc.balance < 0 ? 'neg' : ''}>{fBase(hc.balance)}</b>
            </div>
          </div>
        )}
      </div>

      <div className="c2-chart-x" style={{ gridTemplateColumns: `repeat(${n}, 1fr)` }}>
        {cells.map((c, k) => (
          <span key={c.year_month} className={k === gapIdx ? 'gap' : ''}>
            {monthShort(c.year_month)}
          </span>
        ))}
      </div>
    </div>
  )
}
