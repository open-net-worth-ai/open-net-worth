import { Tooltip } from '../../ui/Tooltip'
import { UNCHANGED, type Tile } from './sectorTiles'
import { PALETTE } from './donutParts'

const pct1 = (n: number) => `${(n * 100).toFixed(1)}%`
// The first row takes the largest tiles, stopping before the second row would hold less than
// this share of the weight (its height): a thin strip carried no names.
const MIN_SECOND_ROW_SHARE = 0.28
import { signedPct } from './following.format'

/** Green where they added, red where they sold, the deeper the bigger the change; grey when
 *  it did not move (the same mix of tokens as the Overview's holdings map). */
function tone(change: number | null): string {
  if (change == null || Math.abs(change) < UNCHANGED)
    return 'color-mix(in srgb, var(--line-strong) 70%, transparent)'
  const alpha = Math.min(62, 18 + Math.abs(change) * 90)
  return `color-mix(in srgb, var(${change > 0 ? '--good' : '--bad'}) ${alpha.toFixed(0)}%, transparent)`
}

function rows(tiles: Tile[]): Tile[][] {
  const total = tiles.reduce((n, t) => n + t.weight, 0) || 1
  const first: Tile[] = []
  let sum = 0
  for (const t of tiles) {
    if (first.length && (total - sum) / total <= MIN_SECOND_ROW_SHARE) break
    if (first.length && (total - sum - t.weight) / total < MIN_SECOND_ROW_SHARE) break
    first.push(t)
    sum += t.weight
  }
  const rest = tiles.slice(first.length)
  return rest.length ? [first, rest] : [first]
}

interface Props {
  tiles: Tile[]
  picked: string | null
  onPick: (key: string | null) => void
  label: string
}

/** The portfolio as a map: each tile's area is its share, its colour what they did with it
 *  last quarter. Tapping a tile, however small, names it above the list and shows only its
 *  holdings; tapping it again shows all. */
export function SectorMap({ tiles, picked, onPick, label }: Props) {
  const total = tiles.reduce((n, t) => n + t.weight, 0) || 1
  // Colour says what they did with each tile when the source says (a fund's share counts);
  // with no such data (a member's estimate) every tile would be the same grey, so each gets its
  // own colour instead, the palette the cards' rings use.
  const byChange = tiles.some((t) => t.change != null)
  const fill = (t: Tile, i: number) =>
    byChange
      ? tone(t.change)
      : `color-mix(in srgb, ${PALETTE[i % PALETTE.length]} 42%, transparent)`
  return (
    <div className="fol-map" role="group" aria-label={label}>
      {rows(tiles).map((row, i) => {
        // flex-grow values summing under 1 hand out only that fraction of the space: grow by
        // shares scaled to a whole, so the rows and their tiles always fill the map.
        const rowWeight = row.reduce((n, t) => n + t.weight, 0)
        return (
          <div key={i} className="fol-map-row" style={{ flexGrow: (rowWeight / total) * 100 }}>
            {row.map((t) => {
              const on = picked === t.key
              const moved = t.change != null && Math.abs(t.change) >= UNCHANGED
              const words = `${t.label}, ${pct1(t.weight)}${moved ? `, ${signedPct(t.change)} this quarter` : ''}`
              return (
                <Tooltip key={t.key} label={words}>
                  <button
                    type="button"
                    className={`fol-map-tile${on ? ' is-on' : ''}${picked && !on ? ' is-dim' : ''}`}
                    style={{
                      flexGrow: (t.weight / (rowWeight || 1)) * 100,
                      background: fill(t, tiles.indexOf(t)),
                    }}
                    aria-pressed={on}
                    aria-label={words}
                    onClick={() => onPick(on ? null : t.key)}
                  >
                    <span className="fol-map-name">{t.label}</span>
                    <span className="fol-map-fig">
                      {pct1(t.weight)}
                      {moved && <span>{signedPct(t.change)}</span>}
                    </span>
                  </button>
                </Tooltip>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
