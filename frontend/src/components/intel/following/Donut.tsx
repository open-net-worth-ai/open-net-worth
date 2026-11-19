import { useId, useState } from 'react'
import { donutParts } from './donutParts'
import type { PortfolioLogo, PortfolioPhoto } from './portfolioPhotos'

interface Props {
  weights: { symbol: string; weight: number }[]
  count: number
  size?: number
  /** The person behind it, in the middle (Moomoo's ring): the head rises over the ring. */
  photo?: PortfolioPhoto
  /** The firm's logo, on a white disc in the middle, where there is no portrait. */
  logo?: PortfolioLogo
  /** The card's figures have not arrived: a placeholder band where the slices will be, and the
   *  portrait or logo already in the middle (it is kept by the browser; only the figures wait). */
  loading?: boolean
}

/** What is inside, at a glance: the largest holdings filling a ring, sized against each other,
 *  the person behind it in the middle (else the count of every holding). Pointing at a slice
 *  names it and its real share. */
const COPY_LOADING = 'Loading its holdings'

/** The outline of a band between two radii and two angles. */
function band(c: number, ri: number, ro: number, a0: number, a1: number): string {
  const at = (rad: number, a: number) => `${c + rad * Math.cos(a)} ${c + rad * Math.sin(a)}`
  const large = a1 - a0 > Math.PI ? 1 : 0
  return `M ${at(ri, a0)} L ${at(ro, a0)} A ${ro} ${ro} 0 ${large} 1 ${at(ro, a1)} L ${at(ri, a1)} A ${ri} ${ri} 0 ${large} 0 ${at(ri, a0)} Z`
}

/** A whole ring (a single slice), as two circles filled even-odd. */
function ring(c: number, ri: number, ro: number): string {
  const circle = (rad: number) =>
    `M ${c - rad} ${c} a ${rad} ${rad} 0 1 0 ${rad * 2} 0 a ${rad} ${rad} 0 1 0 ${-rad * 2} 0`
  return `${circle(ro)} ${circle(ri)}`
}

export function Donut({ weights, count, size = 132, photo, logo, loading = false }: Props) {
  const c = size / 2
  // One ring for every card, a portrait or not, so the cards line up. The band leaves a wide
  // middle for the portrait; the largest slice stands out past the ring's edge, as Moomoo's does.
  const r = size * 0.37
  const stroke = size * 0.16
  const pop = size * 0.045
  const circ = 2 * Math.PI * r
  // Where a slice starts, as an angle from twelve o'clock, clockwise.
  const angle = (along: number) => (along / circ) * 2 * Math.PI - Math.PI / 2
  const all = donutParts(weights)
  const starts = all.map((_, i) => all.slice(0, i).reduce((n, p) => n + p.share * circ, 0))
  const [hover, setHover] = useState<number | null>(null)
  const shown = hover == null ? null : all[hover]
  const clip = useId()
  // The hole, and the photo standing in it: its shoulders fill the hole's circle and only the top
  // of its head rises past the hole's edge, into the ring, as Moomoo's portraits do.
  const hole = r - stroke / 2
  const ri = hole
  const side = hole * 2.25
  const px = c - side / 2
  const py = c + hole - side
  return (
    <svg
      className="fol-donut"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={
        loading
          ? COPY_LOADING
          : `${count} stocks; largest: ${all.map((p) => `${p.symbol} ${(p.weight * 100).toFixed(1)}%`).join(', ')}`
      }
      aria-busy={loading || undefined}
      onPointerLeave={() => setHover(null)}
    >
      {loading && <circle cx={c} cy={c} r={r} strokeWidth={stroke} className="fol-donut-skel" />}
      {/* Each slice is a filled band, and the separators are straight lines of one width drawn
          over them in the card's colour: a dashed stroke's gaps were wedges, narrow inside and
          wide outside (owner, 2026-11-12). */}
      {all.map((p, i) => {
        const ro = r + stroke / 2 + (i === 0 && all.length > 1 ? pop : 0)
        return (
          <path
            key={`${p.symbol}-${i}`}
            d={
              all.length === 1
                ? ring(c, ri, ro)
                : band(c, ri, ro, angle(starts[i]!), angle(starts[i]! + p.share * circ))
            }
            fill={p.color}
            fillRule="evenodd"
            opacity={hover == null || hover === i ? 1 : 0.35}
            onPointerEnter={() => setHover(i)}
          />
        )
      })}
      {all.length > 1 &&
        all.map((p, i) => {
          const a = angle(starts[i]!)
          const out = r + stroke / 2 + pop + 1
          return (
            <line
              key={`sep-${p.symbol}-${i}`}
              className="fol-donut-sep"
              x1={c + (ri - 1) * Math.cos(a)}
              y1={c + (ri - 1) * Math.sin(a)}
              x2={c + out * Math.cos(a)}
              y2={c + out * Math.sin(a)}
            />
          )
        })}
      {/* Each large slice is named on the ring, where Moomoo puts the company's logo: its ticker
          in a small dark badge at the slice's middle (a slice under 9% has no room for one). */}
      {all.map((p, i) => {
        if (p.share < 0.09) return null
        const rr = i === 0 && all.length > 1 ? r + pop / 2 : r
        const mid = ((starts[i]! + (p.share * circ) / 2) / circ) * 2 * Math.PI - Math.PI / 2
        const bx = c + rr * Math.cos(mid)
        const by = c + rr * Math.sin(mid)
        const br = stroke * 0.42
        return (
          <g key={`badge-${p.symbol}`} className="fol-donut-badge" aria-hidden="true">
            <circle cx={bx} cy={by} r={br} />
            <text
              x={bx}
              y={by}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={br * (p.symbol.length > 4 ? 0.62 : p.symbol.length > 3 ? 0.72 : 0.85)}
            >
              {p.symbol}
            </text>
          </g>
        )
      })}
      {photo && !shown ? (
        <>
          <defs>
            <clipPath id={clip}>
              <circle cx={c} cy={c} r={hole} />
              {/* Only the head rises over the ring: a band no wider than a head, so the photo's
                  own edges (and its shoulders) always stay inside the circle. */}
              <rect x={c - hole * 0.85} y={py} width={hole * 1.7} height={c - py} rx={hole * 0.6} />
            </clipPath>
          </defs>
          <circle cx={c} cy={c} r={hole} className="fol-donut-disc" />
          <image
            href={photo.src}
            x={px}
            y={py}
            width={side}
            height={side}
            clipPath={`url(#${clip})`}
            preserveAspectRatio="xMidYMax slice"
          ></image>
        </>
      ) : logo && !shown ? (
        <>
          <defs>
            <clipPath id={clip}>
              <circle cx={c} cy={c} r={hole - 1} />
            </clipPath>
          </defs>
          <circle cx={c} cy={c} r={hole - 1} className="fol-donut-logo-disc" />
          <image
            href={logo.src}
            x={c - hole}
            y={c - hole}
            width={hole * 2}
            height={hole * 2}
            clipPath={`url(#${clip})`}
          ></image>
        </>
      ) : (
        <>
          <text x={c} y={c + 4} className="fol-donut-n" textAnchor="middle">
            {shown ? `${(shown.weight * 100).toFixed(1)}%` : count}
          </text>
          <text x={c} y={c + 21} className="fol-donut-l" textAnchor="middle">
            {shown ? shown.symbol : 'stocks'}
          </text>
        </>
      )}
    </svg>
  )
}
