import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

export interface Column<R> {
  key: string
  label: string
  /** The value the column sorts by; a column without one is not sortable. */
  sortValue?: (row: R) => number | string | null
  cell: (row: R) => ReactNode
  /** The first column holds the stock and reads left; the figures read right. */
  left?: boolean
  className?: (row: R) => string | undefined
  /** Hidden on a phone, where only the essential figures fit. */
  wide?: boolean
  /** On a phone, the figure on the card's right; the others sit on the line beneath. */
  primary?: boolean
  /** An action cell (a menu, a button): on a phone it goes at the card's end. */
  action?: boolean
}

interface Props<R> {
  rows: R[]
  columns: Column<R>[]
  rowKey: (row: R) => string
  label: string
  /** The column the table opens sorted by, highest first (the rows' own order when absent). */
  initialSort?: string
  onRowClick?: (row: R) => void
  selectedKey?: string | null
}

const CARDS_BELOW_PX = 640

const compare = (a: number | string | null, b: number | string | null) => {
  if (a == null || b == null) return a == null ? (b == null ? 0 : 1) : -1
  return a > b ? 1 : a < b ? -1 : 0
}

/** The app's data table (Investments' `.mk-tbl`): sticky headers that sort on click with the
 *  same ▲▼ marks, tabular figures, the stock left and the numbers right. */
export function SortTable<R>({
  rows,
  columns,
  rowKey,
  label,
  initialSort,
  onRowClick,
  selectedKey,
}: Props<R>) {
  // Cards or a table by the width the table actually has, not the screen's: a tablet, or a
  // column beside the holdings ring, is too narrow for every column long before a phone is.
  const boxRef = useRef<HTMLDivElement>(null)
  const [narrow, setNarrow] = useState(false)
  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    const check = () => setNarrow(el.clientWidth < CARDS_BELOW_PX)
    check()
    const ro = new ResizeObserver(check)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const [sort, setSort] = useState<{ key: string | null; dir: 1 | -1 }>({
    key: initialSort ?? null,
    dir: -1,
  })
  const col = columns.find((c) => c.key === sort.key)
  const sorted = col?.sortValue
    ? [...rows].sort((a, b) => sort.dir * compare(col.sortValue!(a), col.sortValue!(b)))
    : rows
  const onSort = (key: string) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir > 0 ? -1 : 1 } : { key, dir: -1 }))
  const cls = (c: Column<R>, extra?: string) =>
    [c.left ? 'left' : '', c.wide ? 'fol-wide' : '', extra ?? ''].filter(Boolean).join(' ') ||
    undefined

  return (
    <div ref={boxRef} className="fol-tbl-box">
      {narrow ? (
        <CardList rows={sorted} {...{ columns, rowKey, label, onRowClick, selectedKey }} />
      ) : (
        <div className="mk-tbl-wrap">
          <div className="mk-tbl-scroll">
            <table className="mk-tbl fol-tbl" aria-label={label}>
              <thead>
                <tr>
                  {columns.map((c) =>
                    c.sortValue ? (
                      <th
                        key={c.key}
                        className={cls(c)}
                        aria-sort={
                          sort.key === c.key
                            ? sort.dir > 0
                              ? 'ascending'
                              : 'descending'
                            : undefined
                        }
                      >
                        <button type="button" className="fol-th" onClick={() => onSort(c.key)}>
                          {c.label}{' '}
                          <span className="ar">
                            {sort.key === c.key ? (sort.dir > 0 ? '▲' : '▼') : ''}
                          </span>
                        </button>
                      </th>
                    ) : (
                      <th key={c.key} className={cls(c, 'nostick')}>
                        {c.label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const k = rowKey(r)
                  return (
                    <tr
                      key={k}
                      className={`data${onRowClick ? ' drill' : ''}${selectedKey === k ? ' is-on' : ''}`}
                      onClick={onRowClick ? () => onRowClick(r) : undefined}
                      onKeyDown={
                        onRowClick
                          ? (e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                onRowClick(r)
                              }
                            }
                          : undefined
                      }
                      tabIndex={onRowClick ? 0 : undefined}
                      aria-selected={onRowClick ? selectedKey === k : undefined}
                    >
                      {columns.map((c) => (
                        <td key={c.key} className={cls(c, c.className?.(r))}>
                          {c.cell(r)}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

/** A phone has no room for columns: each row is a card — the stock on the left, the main
 *  figure on the right, the rest as small labelled figures beneath (as Investments does). */
function CardList<R>({
  rows,
  columns,
  rowKey,
  label,
  onRowClick,
  selectedKey,
}: Omit<Props<R>, 'initialSort'>) {
  const head = columns.find((c) => c.left) ?? columns[0]!
  const primary = columns.find((c) => c.primary)
  const action = columns.find((c) => c.action)
  const rest = columns.filter((c) => c !== head && c !== primary && c !== action && !c.wide)
  return (
    <ul className="fol-rows" aria-label={label}>
      {rows.map((r) => {
        const k = rowKey(r)
        const body = (
          <>
            <span className="fol-row-top">
              <span className="fol-row-head">{head.cell(r)}</span>
              {primary && (
                <span className={`fol-row-primary ${primary.className?.(r) ?? ''}`}>
                  {primary.cell(r)}
                </span>
              )}
            </span>
            {rest.length > 0 && (
              <span className="fol-row-meta">
                {rest.map((c) => (
                  <span key={c.key}>
                    <span className="fol-row-label">{c.label}</span>
                    <span className={c.className?.(r)}>{c.cell(r)}</span>
                  </span>
                ))}
              </span>
            )}
          </>
        )
        return (
          <li key={k} className={`fol-row${selectedKey === k ? ' is-on' : ''}`}>
            {onRowClick ? (
              <button
                type="button"
                className="fol-row-main"
                aria-pressed={selectedKey === k}
                onClick={() => onRowClick(r)}
              >
                {body}
              </button>
            ) : (
              <div className="fol-row-main">{body}</div>
            )}
            {action && <span className="fol-row-action">{action.cell(r)}</span>}
          </li>
        )
      })}
    </ul>
  )
}
