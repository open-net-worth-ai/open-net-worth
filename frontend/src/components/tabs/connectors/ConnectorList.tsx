// The connector list (spec §1 + §2) — ported VERBATIM from the prototype's `App` list body.
//
// Four family sections, each a count-badged heading over a bordered row list, each ending
// in its own "+ Add a …" link that opens the directory PRE-FILTERED to that family. A
// family with no rows renders no section; when that leaves nothing at all, the list shows
// a real empty state rather than a bare page (see `emptyState.ts` for the three defects
// that rule exists to close).

import type { JSX } from 'react'
import { Badge, Btn, Empty } from './primitives'
import { Icons } from './icons'
import { ConnectorRow } from './ConnectorRow'
import { emptyStateFor, FAMILY_BLURB } from './emptyState'
import { DirectoryCard } from './DirectoryCard'
import { matchesSearch, type DirectoryRow } from './catalog'
import {
  FAMILY_SECTIONS,
  inFamily,
  type ConnectorEntry,
  type FamilySection,
  type FamilyTab,
  type StatusFilter,
} from './model'

export interface ListFilters {
  query: string
  tab: FamilyTab
  statusFilter: StatusFilter
}

export interface VisibleSection {
  section: FamilySection
  entries: ConnectorEntry[]
}

/** `Attention` means an ACTUAL error — a connector the user deliberately disconnected is
 *  not a problem to be solved, so it never appears here (spec §1). */
function passesStatus(entry: ConnectorEntry, statusFilter: StatusFilter): boolean {
  if (statusFilter === 'all') return true
  if (statusFilter === 'connected') return entry.enabled && entry.tone === 'good'
  return entry.attention
}

function passesQuery(entry: ConnectorEntry, query: string): boolean {
  const q = query.trim()
  if (!q) return true
  return matchesSearch(`${entry.name} ${entry.sub} ${(entry.categories ?? []).join(' ')}`, q)
}

/**
 * The sections actually rendered. A connector appears under EVERY family it holds, and a
 * section with no surviving rows is dropped entirely — so an empty result here is the
 * signal to render the empty state.
 */
export function visibleSections(
  entries: ConnectorEntry[],
  { query, tab, statusFilter }: ListFilters,
): VisibleSection[] {
  const matched = entries.filter((e) => passesQuery(e, query) && passesStatus(e, statusFilter))
  return FAMILY_SECTIONS.filter((s) => tab === 'all' || s.key === tab)
    .map((section) => ({ section, entries: matched.filter((e) => inFamily(e, section.key)) }))
    .filter((s) => s.entries.length > 0)
}

export interface ConnectorListProps {
  entries: ConnectorEntry[]
  filters: ListFilters
  onOpen: (entry: ConnectorEntry) => void
  onReconnect: (entry: ConnectorEntry) => void
  /** Opens the directory scoped to a family — `all` only from the All tab. */
  onBrowse: (family: FamilyTab) => void
  /**
   * Opens the institution-search front door. Data sources gets a SECOND add link for it —
   * the one deliberate addition to spec §2's one-link-per-section rule, because "which
   * provider covers my bank" is a question the directory cannot answer: Plaid, Enable
   * Banking and Yodlee are four cards that all read "bank aggregation", and picking the
   * wrong one is a dead end you only discover after entering credentials.
   */
  onAddBank: () => void
  /** Connectors the search also matches that are not added yet — so a search for "email"
   *  finds Email even before it is set up, instead of "No connectors match". */
  suggestions?: DirectoryRow[]
  /** Open a suggestion's own page in Browse & add, where it is added. */
  onSuggest?: (row: DirectoryRow) => void
  /** Up to three not-yet-added entries per section, like the Assets screen's "Add more":
   *  on a family tab under that section's rows, on All as ONE block (`all`) after every
   *  section — never a block per section there. Absent while searching or filtering. */
  sectionSuggestions?: Partial<Record<FamilyTab, DirectoryRow[]>>
}

export function ConnectorList({
  entries,
  filters,
  onOpen,
  onReconnect,
  onBrowse,
  onAddBank,
  suggestions = [],
  onSuggest,
  sectionSuggestions,
}: ConnectorListProps): JSX.Element {
  const sections = visibleSections(entries, filters)
  const notAdded =
    suggestions.length > 0 && onSuggest ? (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="cn-colhead">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>Not added yet</span>
            <Badge>{suggestions.length}</Badge>
          </div>
        </div>
        <div className="cn-dirgrid">
          {suggestions.map((d) => (
            <DirectoryCard
              key={d.id}
              d={d}
              onAdd={onSuggest}
              onPreview={onSuggest}
              onConfigure={onSuggest}
            />
          ))}
        </div>
        <div>
          <button className="cn-addlink" onClick={() => onBrowse(filters.tab)}>
            <Icons.plus /> Browse & add
          </button>
        </div>
      </div>
    ) : null

  if (sections.length === 0 && notAdded) return notAdded
  if (sections.length === 0) {
    const copy = emptyStateFor(filters)
    const section = FAMILY_SECTIONS.find((s) => s.key === filters.tab)
    const picks = sectionSuggestions?.[filters.tab]
    // An empty family tab with picks to offer reads like a populated one — heading, one line on
    // what the tab is for, the suggestions, the usual add link — not a "Nothing here yet" block
    // followed by "…but here's something", which read as two contradicting messages.
    if (section && onSuggest && picks?.length) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="cn-colhead">
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>{section.title}</span>
          </div>
          <span className="rd-help">{FAMILY_BLURB[section.key]}</span>
          <SuggestedBlock
            rows={picks}
            onSuggest={onSuggest}
            label={`Suggested ${section.title.toLowerCase()}`}
          />
          <div>
            <button className="cn-addlink" onClick={() => onBrowse(section.key)}>
              <Icons.plus /> {section.add}
            </button>
          </div>
        </div>
      )
    }
    return (
      <Empty
        title={copy.title}
        body={copy.body}
        action={<Btn onClick={() => onBrowse(copy.actionFamily)}>{copy.actionLabel}</Btn>}
      />
    )
  }

  return (
    <>
      {sections.map(({ section, entries: rows }) => (
        <div key={section.key} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="cn-colhead">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12.5, fontWeight: 700 }}>{section.title}</span>
              <Badge>{rows.length}</Badge>
            </div>
          </div>
          <div className="cn-list">
            {rows.map((entry) => (
              <ConnectorRow
                key={`${entry.id}@${section.key}`}
                entry={entry}
                onOpen={onOpen}
                onReconnect={onReconnect}
              />
            ))}
          </div>
          {filters.tab !== 'all' ? (
            <SuggestedBlock
              rows={sectionSuggestions?.[section.key]}
              onSuggest={onSuggest}
              label={`Suggested ${section.title.toLowerCase()}`}
            />
          ) : null}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <button className="cn-addlink" onClick={() => onBrowse(section.key)}>
              <Icons.plus /> {section.add}
            </button>
            {/* Hidden for now (owner request, 2026-08-18) — the institution-search flow
                itself (InstitutionPickerModal, onAddBank) is left wired so this can come
                back with a one-line revert. `display: none` rather than deleting the
                button: out of the accessibility tree and tab order too, not just visually
                gone, with zero risk to the `onAddBank`/`.cn-addlink` tests exercising the
                still-live mechanism underneath. */}
            {section.key === 'data' ? (
              <button className="cn-addlink" onClick={onAddBank} style={{ display: 'none' }}>
                <Icons.search /> Find your bank
              </button>
            ) : null}
          </div>
        </div>
      ))}
      {filters.tab === 'all' ? (
        <SuggestedBlock
          rows={sectionSuggestions?.all}
          onSuggest={onSuggest}
          label="Suggested connectors"
        />
      ) : null}
      {notAdded}
    </>
  )
}

/** The Suggested heading + dashed cards — ONE rendering for a section, the All tab and an
 *  empty tab, so the three can't drift. Renders nothing when there is nothing to suggest. */
function SuggestedBlock({
  rows,
  onSuggest,
  label,
}: {
  rows?: DirectoryRow[]
  onSuggest?: (row: DirectoryRow) => void
  label: string
}): JSX.Element | null {
  if (!onSuggest || !rows?.length) return null
  return (
    <div className="cn-suggest" aria-label={label}>
      <div className="cn-suggest-h">Suggested</div>
      <div className="cn-suggestgrid">
        {rows.map((d) => (
          <DirectoryCard
            key={d.id}
            d={d}
            suggested
            onAdd={onSuggest}
            onPreview={onSuggest}
            onConfigure={onSuggest}
          />
        ))}
      </div>
    </div>
  )
}
