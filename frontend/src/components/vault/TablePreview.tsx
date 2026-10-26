import { useEffect, useRef, useState, type UIEvent } from 'react'
import { fetchVaultTable, type VaultItem, type VaultTable } from '../../api/vault'
import { NoPreview } from './NoPreview'
import { PreviewLoading } from './PreviewLoading'

function columnLetter(index: number): string {
  let n = index + 1
  let out = ''
  while (n > 0) {
    const r = (n - 1) % 26
    out = String.fromCharCode(65 + r) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}

type Grid = {
  key: string
  sheets: string[]
  sheet: string
  rows: string[][]
  columns: number
  hasMore: boolean
  total: number | null
  moreColumns: boolean
}
type Loaded = Grid | { key: string; error: string }

const fromPage = (key: string, t: VaultTable): Grid => ({
  key,
  sheets: t.sheets,
  sheet: t.sheet,
  rows: t.rows,
  columns: t.columns,
  hasMore: t.has_more,
  total: t.total_rows,
  moreColumns: t.more_columns,
})
const LOAD_AHEAD_PX = 240

function note(grid: Grid, more: 'idle' | 'loading' | 'failed'): string | null {
  const shown = grid.rows.length.toLocaleString()
  const of = grid.total ? ` of ${grid.total.toLocaleString()}` : ''
  if (more === 'failed') return 'Couldn’t load more rows — scroll to try again.'
  if (grid.hasMore)
    return `Rows 1–${shown}${of}${more === 'loading' ? ' — loading more…' : ' — scroll for more'}`
  if (grid.moreColumns)
    return `Showing the first ${grid.columns} columns — download it for the rest.`
  return null
}

/** A CSV or Excel file's rows, as the file has them — a small spreadsheet with column letters
 *  and row numbers, one sheet at a time. Scrolls inside its frame and loads the next page of
 *  rows as you near the bottom (owner: "why we show only 200?").
 *
 *  `highlightRow` (1-based, the FIRST sheet's numbering) marks one row and scrolls the frame to
 *  it, loading further pages until it exists — the import review points at the file row a
 *  statement row came from (owner, 2026-10-26: "scroll or review the files … before making
 *  decisions"). */
export function TablePreview({ item, highlightRow }: { item: VaultItem; highlightRow?: number }) {
  const [sheet, setSheet] = useState<string | undefined>(undefined)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [more, setMore] = useState<'idle' | 'loading' | 'failed'>('idle')
  // Synchronous, unlike `more`: two scroll events in one tick must not fetch a page twice.
  const inFlight = useRef(false)
  const frameRef = useRef<HTMLDivElement>(null)
  const key = `${item.id}:${sheet ?? ''}`
  useEffect(() => {
    let alive = true
    fetchVaultTable(item.id, sheet).then(
      (table) => alive && setLoaded(fromPage(key, table)),
      (e) => alive && setLoaded({ key, error: e instanceof Error ? e.message : String(e) }),
    )
    return () => {
      alive = false
    }
  }, [item.id, sheet, key])
  const current = loaded?.key === key ? loaded : null
  const grid = current && !('error' in current) ? current : null
  const onFirstSheet = !!grid && (grid.sheets.length === 0 || grid.sheet === grid.sheets[0])
  const target = onFirstSheet && highlightRow && highlightRow > 0 ? highlightRow : null
  const loadedRows = grid?.rows.length ?? 0
  const targetLoaded = target !== null && target <= loadedRows

  const loadMore = () => {
    if (!grid || !grid.hasMore || inFlight.current) return
    inFlight.current = true
    setMore('loading')
    fetchVaultTable(item.id, sheet, grid.rows.length).then(
      (page) => {
        inFlight.current = false
        // Only the page that starts where the loaded rows end — never one twice.
        setLoaded((prev) =>
          prev && prev.key === key && !('error' in prev) && prev.rows.length === page.offset
            ? {
                ...prev,
                rows: [...prev.rows, ...page.rows],
                columns: Math.max(prev.columns, page.columns),
                hasMore: page.has_more,
                total: page.total_rows ?? prev.total,
                moreColumns: prev.moreColumns || page.more_columns,
              }
            : prev,
        )
        setMore('idle')
      },
      () => {
        inFlight.current = false
        setMore('failed')
      },
    )
  }
  // Fetch pages until the highlighted row exists, then centre it inside the frame (never
  // scrollIntoView — that would scroll the page around the grid too).
  useEffect(() => {
    if (target === null) return
    if (!targetLoaded) {
      if (grid?.hasMore && more !== 'failed') loadMore()
      return
    }
    const frame = frameRef.current
    const row = frame?.querySelector<HTMLElement>(`tr[data-row="${target}"]`)
    if (frame && row) {
      const offset = row.getBoundingClientRect().top - frame.getBoundingClientRect().top
      frame.scrollTop += offset - frame.clientHeight / 2 + row.offsetHeight / 2
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the target and page count
  }, [target, targetLoaded, loadedRows])

  if (!current)
    return (
      <div className="vault-table-frame vault-table-frame--loading">
        <PreviewLoading />
      </div>
    )
  if ('error' in current) return <NoPreview item={item} reason={current.error} />
  const shown = current
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - LOAD_AHEAD_PX) loadMore()
  }
  const cols = Array.from({ length: shown.columns }, (_, i) => i)
  const status = note(shown, more)
  return (
    <div className="vault-table">
      <div
        className="vault-table-frame"
        role="region"
        aria-label={`${item.title}${shown.sheet ? ` — ${shown.sheet}` : ''}`}
        tabIndex={0}
        onScroll={onScroll}
        ref={frameRef}
      >
        <table className="vault-grid">
          <thead>
            <tr>
              <th aria-hidden="true" />
              {cols.map((c) => (
                <th key={c} scope="col">
                  {columnLetter(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.rows.map((row, r) => (
              <tr
                key={r}
                data-row={r + 1}
                className={r + 1 === target ? 'vault-grid-row--on' : undefined}
                aria-current={r + 1 === target ? 'true' : undefined}
              >
                <th scope="row">{r + 1}</th>
                {cols.map((c) => (
                  <td key={c} title={row[c] || undefined}>
                    {row[c] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.sheets.length > 1 && (
        <div className="vault-sheet-tabs" role="group" aria-label="Sheets">
          {shown.sheets.map((name) => (
            <button
              key={name}
              type="button"
              className="vault-sheet-tab"
              aria-pressed={name === shown.sheet}
              onClick={() => {
                inFlight.current = false
                setMore('idle')
                setSheet(name)
              }}
            >
              {name}
            </button>
          ))}
        </div>
      )}
      {status && <p className="vault-table-note">{status}</p>}
    </div>
  )
}
