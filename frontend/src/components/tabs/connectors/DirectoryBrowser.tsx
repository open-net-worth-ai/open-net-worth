// Browse & add — the directory grid (spec §6). Ported VERBATIM from the `else` branch of the
// prototype's directory Modal body.
//
// ONE SCROLL LAYER, ONE FIXED HEIGHT. The previous design nested a fixed-height scrolling grid
// inside the modal's own scrolling body, so the search field and filters scrolled away while the
// results scrolled independently underneath. Here:
//
//   - search + family chips + sort are ONE sticky block pinned to the top of the modal body's
//     scroll (`position: sticky; top: -18px`, with negative margins so it spans the body's full
//     width and its own padding re-establishes the gutter);
//   - the results area holds a 480px floor, so filtering down to two results does not shrink the
//     dialog and the header/footer never move.
//
// Both are inline styles because the prototype styles them inline — connectors.css is a verbatim
// port of the prototype stylesheet and carries no rule for either.

import type { CSSProperties, JSX } from 'react'
import { DirectoryCard } from './DirectoryCard'
import { SECTION_SUGGESTION_COUNT } from './directoryRows'
import { Empty } from './primitives'
import { Icons } from './icons'
import { ArrowUpDown, ListFilter } from '../../../ui/icons'
import { CATEGORY_HUE, CONNECTOR_CATEGORIES, type ConnectorCategory } from './model'
import {
  DIRECTORY_FAMILY_CHIPS,
  filterDirectory,
  popularEntries,
  type DirectoryRow,
  type DirectoryFamilyFilter,
  type DirectorySort,
} from './catalog'

/** The shared floor for the results area AND every step of the MCP flow, so the dialog never
 *  jumps between them (spec §6 and §7 pin the same number). */
export const MODAL_BODY_MIN_HEIGHT = 480

/** Scroll a tapped category chip to the middle of its one-line row, so the chips past it
 *  (the ones cut off at the edge) come into view. Optional call: jsdom has no scrollIntoView. */
function revealChip(chip: HTMLElement): void {
  chip.scrollIntoView?.({ behavior: 'smooth', block: 'nearest', inline: 'center' })
}

export interface DirectoryBrowserProps {
  entries: DirectoryRow[]
  family: DirectoryFamilyFilter
  onFamily: (family: DirectoryFamilyFilter) => void
  query: string
  onQuery: (query: string) => void
  sort: DirectorySort
  onSort: (sort: DirectorySort) => void
  /** One category, or every one. A card's tag picks it too. */
  category: ConnectorCategory | 'all'
  onCategory: (category: ConnectorCategory | 'all') => void
  onAdd: (entry: DirectoryRow) => void
  onPreview: (entry: DirectoryRow) => void
  onConfigure: (entry: DirectoryRow) => void
}

export function DirectoryBrowser({
  entries,
  family,
  onFamily,
  query,
  onQuery,
  sort,
  onSort,
  category,
  onCategory,
  onAdd,
  onPreview,
  onConfigure,
}: DirectoryBrowserProps): JSX.Element {
  const shown = filterDirectory(entries, family, query, sort, category)
  // Only the categories something in this family actually carries — an option that can only
  // ever produce "No matches" is a dead end.
  const categories = CONNECTOR_CATEGORIES.filter((c) =>
    filterDirectory(entries, family, '', sort, c).some(() => true),
  )
  // Suggested — the same block, cards and rule as the Connectors list's (popular, not added yet,
  // never the generic slot, at most three). It replaced a "Popular" tile row that repeated
  // connectors already added and, unlike the grid, ignored Sort. A shortcut through the DEFAULT
  // view only — once the user has filtered or searched,
  // it is noise competing with the result they asked for.
  const suggested = popularEntries(entries)
    .filter((d) => !d.installed && !d.generic)
    .slice(0, SECTION_SUGGESTION_COUNT)
  const showSuggested =
    !query.trim() && family === 'all' && category === 'all' && suggested.length > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div
        style={{
          position: 'sticky',
          top: -18,
          zIndex: 2,
          background: 'var(--modal-bg)',
          margin: '-18px -20px 0',
          padding: '18px 20px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          borderBottom: '1px solid var(--line)',
        }}
      >
        <div className="cn-dirsearchrow">
          <span className="cn-dirsearch">
            <Icons.search />
            <input
              className="rd-input"
              type="search"
              placeholder="Search connectors…"
              value={query}
              onChange={(e) => onQuery(e.target.value)}
              aria-label="Search directory"
            />
          </span>
          {/* Phone only: Sort as an icon beside search (the text select below is hidden there),
              so the family chips get their row to themselves. A native select laid over the
              icon keeps the platform's own picker and its accessibility. */}
          {/* Phone only, like Sort: the category filter as an icon — the chip row below is
              hidden there, so the header is one line of family chips instead of two. */}
          <label className={'cn-sorticon' + (category !== 'all' ? ' on' : '')} title="Category">
            <ListFilter size={16} aria-hidden />
            <select
              value={category}
              onChange={(e) => onCategory(e.target.value as ConnectorCategory | 'all')}
              aria-label="Filter by category"
            >
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="cn-sorticon" title="Sort">
            <ArrowUpDown size={16} aria-hidden />
            <select
              value={sort}
              onChange={(e) => onSort(e.target.value as DirectorySort)}
              aria-label="Sort directory"
            >
              <option value="popular">Popular</option>
              <option value="az">A–Z</option>
            </select>
          </label>
        </div>
        <div className="cn-dirfilters">
          <div className="cn-chiprow">
            {DIRECTORY_FAMILY_CHIPS.map(([k, l]) => (
              <button
                key={k}
                className={'cn-chip' + (family === k ? ' on' : '')}
                aria-pressed={family === k}
                onClick={() => onFamily(k)}
              >
                {l}
              </button>
            ))}
          </div>
          <select
            className="rd-select cn-sortselect"
            style={{ width: 'auto' }}
            value={sort}
            onChange={(e) => onSort(e.target.value as DirectorySort)}
            aria-label="Sort directory"
          >
            <option value="popular">Sort: Popular</option>
            <option value="az">Sort: A–Z</option>
          </select>
        </div>
        {categories.length > 1 ? (
          <div className="cn-catrow" role="group" aria-label="Filter by category">
            <button
              type="button"
              className={'cn-cat pick' + (category === 'all' ? ' on' : '')}
              aria-pressed={category === 'all'}
              onClick={(e) => {
                revealChip(e.currentTarget)
                onCategory('all')
              }}
            >
              All
            </button>
            {categories.map((c) => (
              <button
                key={c}
                type="button"
                className={'cn-cat pick' + (category === c ? ' on' : '')}
                style={{ '--cat-hue': CATEGORY_HUE[c] } as CSSProperties}
                aria-pressed={category === c}
                onClick={(e) => {
                  revealChip(e.currentTarget)
                  onCategory(category === c ? 'all' : c)
                }}
              >
                {c}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          minHeight: MODAL_BODY_MIN_HEIGHT,
        }}
      >
        {showSuggested && (
          <div className="cn-suggest" aria-label="Suggested connectors">
            <div className="cn-suggest-h">Suggested</div>
            <div className="cn-suggestgrid swipe">
              {suggested.map((d) => (
                <DirectoryCard
                  key={d.id}
                  d={d}
                  suggested
                  onAdd={onAdd}
                  onPreview={onPreview}
                  onConfigure={onConfigure}
                  onCategory={onCategory}
                />
              ))}
            </div>
          </div>
        )}

        {/* Labels the full, sortable list only when Suggested sits above it — otherwise the
            grid is the whole view and needs no heading. */}
        {showSuggested ? <div className="cn-suggest-h">All connectors</div> : null}
        <div className="cn-dirgrid">
          {shown.map((d) => (
            <DirectoryCard
              key={d.id}
              d={d}
              onAdd={onAdd}
              onPreview={onPreview}
              onConfigure={onConfigure}
              onCategory={onCategory}
            />
          ))}
        </div>
        {shown.length === 0 ? (
          <Empty title="No matches" body="Try a different search or category." />
        ) : null}
      </div>
    </div>
  )
}
