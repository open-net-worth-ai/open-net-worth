import type { CSSProperties, FocusEvent, MouseEvent, ReactNode } from 'react'
import { useMaskText } from '../../utils/amountMask'

export type MeterVariant = 'linear' | 'ring'
export type MeterSize = 'sm' | 'md' | 'lg'
// 'accent' is kept for API completeness (some callers need to reproduce an exact pre-existing
// blue fill verbatim via `color`) — new call sites should prefer good/bad/warn/neutral: this app's
// colour rule is green/red = value, blue = action, and a meter shows a measurement, not an action.
export type MeterTone = 'accent' | 'good' | 'bad' | 'warn' | 'neutral'

export interface MeterSegment {
  id: string
  /** Share of the track this segment fills, 0–100. Rendered left→right in array order. */
  pct: number
  color?: string
  tone?: MeterTone
  /** Extra class(es) — e.g. a caller-owned domain color/pattern modifier. */
  className?: string
  ariaLabel?: string
  ariaExpanded?: boolean
  /** Native tooltip (rare — prefer `ariaLabel` + a rich popover). */
  title?: string
  /** Rendered inside the segment; hidden automatically once the segment is too narrow to hold it. */
  content?: ReactNode
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void
  onMouseEnter?: (e: MouseEvent<HTMLButtonElement>) => void
  onMouseLeave?: (e: MouseEvent<HTMLButtonElement>) => void
  onFocus?: (e: FocusEvent<HTMLButtonElement>) => void
  onBlur?: (e: FocusEvent<HTMLButtonElement>) => void
}

export interface MeterProps {
  variant?: MeterVariant
  size?: MeterSize
  /** 0–100. Mutually exclusive with `segments` (segments win if both are passed). Negative values
   *  clamp the rendered track to empty — see `tone`/`target` below for the negative flag. */
  value?: number
  /** Multi-segment track (linear only) — e.g. a funding-coverage bar. */
  segments?: MeterSegment[]
  /** Target marker, 0–100. Linear: a tick on the track. Ring: reserved, not yet drawn. */
  target?: number
  tone?: MeterTone
  /** Raw CSS color — wins over `tone` when both are given. */
  color?: string
  /** Ring only — renders the completion checkmark instead of the percentage arc. */
  complete?: boolean
  ariaLabel: string
  /** Accessible value text (e.g. "$120 of $200") — supplements the raw percentage. */
  valueText?: string
  className?: string
}

const TONE_COLOR: Record<MeterTone, string> = {
  accent: 'var(--accent)',
  good: 'var(--good)',
  bad: 'var(--bad)',
  warn: 'var(--warn)',
  neutral: 'var(--muted)',
}

function resolveColor(color: string | undefined, tone: MeterTone | undefined): string | undefined {
  if (color) return color
  if (tone) return TONE_COLOR[tone]
  return undefined
}

function clampPct(n: number | undefined): number {
  if (n == null || Number.isNaN(n)) return 0
  return Math.min(100, Math.max(0, n))
}

// Ring geometry — viewBox is fixed at "0 0 80 80" for every size (matches budgetTarget.tsx's two
// real rings exactly); only the rendered diameter/radius/stroke/text position vary. `sm`/`lg`
// reproduce the two real call sites verbatim; `md` is an unexercised, reasonable in-between.
interface RingGeometry {
  diameter: number
  radius: number
  strokeWidth: number
  textY: number
}
// Font-size per size is CSS-owned (.ui-meter__ring-text--sm/md/lg in index.css), not listed here.
const RING_SIZE: Record<MeterSize, RingGeometry> = {
  sm: { diameter: 70, radius: 28, strokeWidth: 6, textY: 45 },
  md: { diameter: 80, radius: 30, strokeWidth: 6, textY: 45 },
  lg: { diameter: 92, radius: 32, strokeWidth: 7, textY: 46 },
}
// Completion checkmark is one fixed drawing (in the same 80×80 space) — only the rendered
// diameter scales per size, so it never needs its own per-size r/path table.
const RING_COMPLETE_DIAMETER: Record<MeterSize, number> = { sm: 64, md: 74, lg: 84 }
const RING_OUTER_R = 31
const RING_INNER_R = 23
const RING_COMPLETE_STROKE = 5
const RING_CHECK_PATH = 'M30 40.5l7 7 13.5-14.5'

function Segment({ segment }: { segment: MeterSegment }) {
  const maskText = useMaskText()
  const title = segment.title && maskText(segment.title)
  const label = segment.ariaLabel && maskText(segment.ariaLabel)
  const interactive = !!(
    segment.onClick ||
    segment.onMouseEnter ||
    segment.onMouseLeave ||
    segment.onFocus ||
    segment.onBlur
  )
  const cls = `ui-meter__segment${segment.className ? ` ${segment.className}` : ''}`
  const bg = resolveColor(segment.color, segment.tone)
  const style: CSSProperties = {
    width: `${Math.max(0, segment.pct)}%`,
    ...(bg ? { background: bg } : {}),
  }
  const content = segment.content != null && (
    <span className="ui-meter__segment-content">{segment.content}</span>
  )

  if (interactive) {
    return (
      <button
        type="button"
        className={cls}
        style={style}
        title={title}
        aria-label={label}
        aria-expanded={segment.ariaExpanded}
        onClick={segment.onClick}
        onMouseEnter={segment.onMouseEnter}
        onMouseLeave={segment.onMouseLeave}
        onFocus={segment.onFocus}
        onBlur={segment.onBlur}
      >
        {content}
      </button>
    )
  }
  return (
    <div className={cls} style={style} title={title} aria-label={label}>
      {content}
    </div>
  )
}

function LinearMeter({
  size,
  value,
  segments,
  target,
  color,
  tone,
  ariaLabel,
  valueText,
  className,
}: MeterProps & { size: MeterSize }) {
  const maskText = useMaskText()
  const rootClass = `ui-meter ui-meter--linear ui-meter--${size}${className ? ` ${className}` : ''}`

  if (segments) {
    const totalPct = clampPct(segments.reduce((sum, s) => sum + s.pct, 0))
    return (
      <div
        className={rootClass}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(totalPct)}
        aria-label={maskText(ariaLabel)}
        aria-valuetext={valueText && maskText(valueText)}
      >
        {segments.map((seg) => (
          <Segment key={seg.id} segment={seg} />
        ))}
      </div>
    )
  }

  const hasValue = value != null
  const isNegative = hasValue && value < 0
  const pct = hasValue ? clampPct(value) : 0
  const fillColor = resolveColor(color, tone)
  // "Went negative" vs "nothing measured yet" both render an empty (0%) track — the flag is the
  // only visual difference, so it only fires when the caller has explicitly marked this bad AND
  // given it a target (a value with no target has no "negative" concept to flag).
  const showFlag = isNegative && tone === 'bad' && target != null

  return (
    <div
      className={rootClass}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      aria-label={maskText(ariaLabel)}
      aria-valuetext={valueText && maskText(valueText)}
    >
      <div
        className="ui-meter__fill"
        style={fillColor ? { width: `${pct}%`, background: fillColor } : { width: `${pct}%` }}
      />
      {target != null && (
        <div className="ui-meter__target" style={{ left: `${clampPct(target)}%` }} />
      )}
      {showFlag && (
        <span className="ui-meter__flag" aria-hidden="true">
          !
        </span>
      )}
    </div>
  )
}

function RingMeter({
  size,
  value,
  color,
  tone,
  complete,
  ariaLabel,
  valueText,
  className,
}: MeterProps & { size: MeterSize }) {
  const rootClass = `ui-meter ui-meter--ring ui-meter--${size}${className ? ` ${className}` : ''}`
  const pct = clampPct(value)
  const strokeColor = resolveColor(color, tone) ?? 'var(--good)'

  if (complete) {
    const diameter = RING_COMPLETE_DIAMETER[size]
    return (
      <div
        className={rootClass}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={100}
        aria-label={ariaLabel}
        aria-valuetext={valueText}
      >
        <svg viewBox="0 0 80 80" width={diameter} height={diameter} aria-hidden="true">
          <circle
            cx="40"
            cy="40"
            r={RING_OUTER_R}
            fill="none"
            stroke={strokeColor}
            strokeWidth={RING_COMPLETE_STROKE}
          />
          <circle cx="40" cy="40" r={RING_INNER_R} fill={strokeColor} />
          <path
            d={RING_CHECK_PATH}
            fill="none"
            stroke="#fff"
            strokeWidth={RING_COMPLETE_STROKE}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    )
  }

  const geom = RING_SIZE[size]
  const circumference = 2 * Math.PI * geom.radius
  return (
    <div
      className={rootClass}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      aria-label={ariaLabel}
      aria-valuetext={valueText}
    >
      <svg viewBox="0 0 80 80" width={geom.diameter} height={geom.diameter} aria-hidden="true">
        <circle
          className="ui-meter__ring-track"
          cx="40"
          cy="40"
          r={geom.radius}
          strokeWidth={geom.strokeWidth}
        />
        <circle
          className="ui-meter__ring-progress"
          cx="40"
          cy="40"
          r={geom.radius}
          strokeWidth={geom.strokeWidth}
          stroke={strokeColor}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct / 100)}
          transform="rotate(-90 40 40)"
        />
        <text
          x="40"
          y={geom.textY}
          textAnchor="middle"
          className={`ui-meter__ring-text ui-meter__ring-text--${size}`}
        >
          {Math.round(pct)}%
        </text>
      </svg>
    </div>
  )
}

/**
 * Meter — the shared progress/coverage visualization primitive: a single fill, a single fill with
 * a target tick, a multi-segment track, or a percentage/completion ring. Promoted from four
 * hand-rolled bars (Overview's budget-vs-actual + movers widgets, Commitments' funding coverage
 * bar, Plan's target ring) per CLAUDE.md's "a layout pattern used twice gets promoted to a
 * primitive" rule.
 */
export function Meter(props: MeterProps) {
  const size = props.size ?? 'md'
  if (props.variant === 'ring') return <RingMeter {...props} size={size} />
  return <LinearMeter {...props} size={size} />
}
