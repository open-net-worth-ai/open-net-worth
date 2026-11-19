import { useState } from 'react'
import { sliceColor } from './donutParts'
import { COPY } from './following.copy'
import { signedPct } from './following.format'
import { UNCHANGED, type Tile } from './sectorTiles'

// A share too small to show at one decimal reads as under 0.1%, never as 0.0%.
const pct1 = (n: number) => (n > 0 && n < 0.0005 ? '<0.1%' : `${(n * 100).toFixed(1)}%`)
const changeOf = (t: Tile) => (t.fresh ? COPY.legendNew : signedPct(t.change!))
// More than five slices split into two columns, read down the first, then the second.
const columns = (tiles: Tile[]) => {
  const all = tiles.map((t, i) => ({ t, i }))
  const half = Math.ceil(all.length / 2)
  return all.length > 5 ? [all.slice(0, half), all.slice(half)] : [all]
}
// The ring's hole fits about a dozen small letters; the legend beside it names the slice in full.
const short = (s: string) => (s.length > 13 ? `${s.slice(0, 12)}…` : s)

interface Props {
  tiles: Tile[]
  picked: string | null
  onPick: (key: string | null) => void
  label: string
  /** What a slice is: a sector, or a stock when sectors are unknown. */
  bySector: boolean
}

/** The portfolio as a ring of its sectors (or its stocks, when sectors are unknown), each slice
 *  its share, with a legend naming every slice, its share and what they did with it last
 *  quarter. Tapping a slice or a legend row shows only its holdings; tapping it again shows all. */
export function SectorPie({ tiles, picked, onPick, label, bySector }: Props) {
  const size = 152
  const c = size / 2
  const r = size * 0.38
  const stroke = size * 0.2
  const circ = 2 * Math.PI * r
  const gap = tiles.length > 1 ? 1.5 : 0
  const total = tiles.reduce((n, t) => n + t.weight, 0) || 1
  const [hover, setHover] = useState<string | null>(null)
  const focus = hover ?? picked
  const shown = tiles.find((t) => t.key === focus) ?? null
  const toggle = (key: string) => onPick(picked === key ? null : key)
  const withChange = tiles.some((t) => t.change != null)
  // Where each slice starts on the ring, as a share of the slices' own total (a holding with no
  // sector is left out of the ring, so its slices still close it).
  const starts = tiles.map(
    (_, i) => (tiles.slice(0, i).reduce((n, t) => n + t.weight, 0) / total) * circ,
  )
  return (
    <div className="fol-pie" role="group" aria-label={label}>
      <svg
        className="fol-pie-ring"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        aria-hidden="true"
        onPointerLeave={() => setHover(null)}
      >
        <g transform={`rotate(-90 ${c} ${c})`}>
          {tiles.map((t, i) => {
            const share = t.weight / total
            const len = Math.max(0.5, share * circ - gap)
            return (
              <circle
                key={t.key}
                cx={c}
                cy={c}
                r={r}
                fill="none"
                stroke={sliceColor(i)}
                strokeWidth={stroke}
                strokeDasharray={`${len} ${circ - len}`}
                strokeDashoffset={-starts[i]!}
                opacity={focus == null || focus === t.key ? 1 : 0.3}
                onPointerEnter={() => setHover(t.key)}
                onClick={() => toggle(t.key)}
              />
            )
          })}
        </g>
        <text x={c} y={c + 2} className="fol-pie-n" textAnchor="middle">
          {shown ? pct1(shown.weight) : tiles.length}
        </text>
        <text x={c} y={c + 18} className="fol-pie-l" textAnchor="middle">
          {shown
            ? short(shown.label)
            : `${bySector ? 'sector' : 'stock'}${tiles.length === 1 ? '' : 's'}`}
        </text>
      </svg>
      {/* Two columns where there is room (a phone stacks them), each headed so its figures say
          what they are: the share of the portfolio, and the change in shares held since the
          last report (a member's estimate has no share counts, so no such column). */}
      <div className={withChange ? 'fol-pie-legend' : 'fol-pie-legend fol-pie-legend--nochg'}>
        {columns(tiles).map((col, c) => (
          <ul key={c} className="fol-pie-col">
            <li className="fol-pie-head" aria-hidden="true">
              <span />
              <span>{bySector ? COPY.legendSector : COPY.legendStock}</span>
              <span>{COPY.legendWeight}</span>
              <span>{withChange ? COPY.legendChange : ''}</span>
            </li>
            {col.map(({ t, i }) => {
              const moved = t.change != null && Math.abs(t.change) >= UNCHANGED
              return (
                <li key={t.key}>
                  <button
                    type="button"
                    className="fol-pie-row"
                    aria-pressed={picked === t.key}
                    aria-label={`${t.label}, ${pct1(t.weight)}${moved ? `, ${changeOf(t)} this quarter` : ''}`}
                    data-dim={focus != null && focus !== t.key ? '' : undefined}
                    onPointerEnter={() => setHover(t.key)}
                    onPointerLeave={() => setHover(null)}
                    onClick={() => toggle(t.key)}
                  >
                    <span className="fol-pie-dot" style={{ background: sliceColor(i) }} />
                    <span className="fol-pie-name">{t.label}</span>
                    <span className="fol-pie-pct">{pct1(t.weight)}</span>
                    {/* Plain ink: red and green would read as a loss or a gain, and this is
                        how many shares they bought or sold, not what they made. */}
                    <span className="fol-pie-chg">{moved ? changeOf(t) : ''}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        ))}
      </div>
    </div>
  )
}
