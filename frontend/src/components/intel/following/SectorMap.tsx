import { UNCHANGED, type Tile } from './sectorTiles'

const pct1 = (n: number) => `${(n * 100).toFixed(1)}%`
// The first row takes the largest tiles until it holds this share of the weight; the rest
// share a second row. Two rows keep every tile wide enough for its label.
const FIRST_ROW_SHARE = 0.6
import { signedPct } from './following.format'

/** Green where they added, red where they sold, the deeper the bigger the change; grey when
 *  it did not move (the same mix of tokens as the Overview's holdings map). */
function tone(change: number | null): string {
  if (change == null || Math.abs(change) < UNCHANGED)
    return 'color-mix(in srgb, var(--line-strong) 70%, transparent)'
  const alpha = Math.min(62, 18 + Math.abs(change) * 90)
  return `color-mix(in srgb, var(${change > 0 ? '--good' : '--bad'}) ${alpha.toFixed(0)}%, transparent)`
}

/** A chip's swatch: the tile's colour at full strength, so it reads at 8px. */
const swatch = (change: number | null) =>
  change == null || Math.abs(change) < UNCHANGED
    ? 'var(--faint)'
    : change > 0
      ? 'var(--good)'
      : 'var(--bad)'

function rows(tiles: Tile[]): Tile[][] {
  const first: Tile[] = []
  let sum = 0
  for (const t of tiles) {
    if (first.length && sum >= FIRST_ROW_SHARE) break
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
  /** The chip list's own name (the map and the list are two groups). */
  keysLabel: string
}

/** The portfolio as a map: each tile's area is its share, its colour what they did with it
 *  last quarter. Tapping a tile shows only its holdings below; tapping it again shows all. */
export function SectorMap({ tiles, picked, onPick, label, keysLabel }: Props) {
  const total = tiles.reduce((n, t) => n + t.weight, 0) || 1
  return (
    <div className="fol-mapbox">
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
                  <button
                    key={t.key}
                    type="button"
                    className={`fol-map-tile${on ? ' is-on' : ''}${picked && !on ? ' is-dim' : ''}`}
                    style={{
                      flexGrow: (t.weight / (rowWeight || 1)) * 100,
                      background: tone(t.change),
                    }}
                    aria-pressed={on}
                    aria-label={words}
                    title={words}
                    onClick={() => onPick(on ? null : t.key)}
                  >
                    <span className="fol-map-name">{t.label}</span>
                    <span className="fol-map-fig">
                      {pct1(t.weight)}
                      {moved && <span>{signedPct(t.change)}</span>}
                    </span>
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>
      {/* Every tile as a chip: the map drops the words from a tile too small to carry them, so
        the full list (each with its share and change) lives here. A chip picks like its tile. */}
      <div className="fol-keys" role="group" aria-label={keysLabel}>
        {tiles.map((t) => {
          const on = picked === t.key
          const moved = t.change != null && Math.abs(t.change) >= UNCHANGED
          return (
            <button
              key={t.key}
              type="button"
              className={`fol-key-chip${on ? ' is-on' : ''}`}
              aria-pressed={on}
              onClick={() => onPick(on ? null : t.key)}
            >
              <i style={{ background: swatch(t.change) }} aria-hidden="true" />
              <span className="fol-key-name">{t.label}</span>
              <span>{pct1(t.weight)}</span>
              {moved && (
                <span className={t.change! > 0 ? 'up' : 'down'}>{signedPct(t.change)}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
