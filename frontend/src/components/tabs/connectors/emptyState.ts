// Copy for "the list has nothing to show" — pure, no React.
//
// This is the one deliberate ADDITION to the prototype's list (which renders a single
// generic `Empty`), and it exists because three real defects were found in the outgoing
// Connectors tab, all of them here:
//
//   1. A family whose rows were all filtered out rendered a BLANK section with no
//      explanation. Spec §1: selecting a family tab with nothing in it shows the standard
//      empty state with a Browse & add action, "not a bare page". Every input combination
//      below resolves to real copy, so there is no path to a blank list.
//   2. The copy was templated off the raw segment key, rendering literally "No mcp
//      connectors". Nothing here ever reads a key — only `FamilySection.title`.
//   3. The CTA opened the UNFILTERED directory, dumping a user who had explicitly
//      filtered to one family into the full catalog. `actionFamily` is always the family
//      they are already looking at.

import { FAMILY_SECTIONS, type FamilyKey, type FamilyTab, type StatusFilter } from './model'

/** One line on what a family is for — spec §1's lane cards, verbatim in substance. */
/** What each family is for — one line, shared by the empty state and an empty tab's picks. */
export const FAMILY_BLURB: Record<FamilyKey, string> = {
  channels: 'Channels are how Lucius reaches you — notifications, questions, and approvals.',
  data: 'Data sources bring balances, transactions, and positions in.',
  web: 'Web providers give Lucius live search and page reads.',
  mcp: 'MCP servers are third-party tools you point Lucius at.',
}

/** The family's human-readable title, or null on the All tab. NEVER the raw key. */
export function familyTitle(tab: FamilyTab): string | null {
  if (tab === 'all') return null
  return FAMILY_SECTIONS.find((s) => s.key === tab)?.title ?? null
}

export interface EmptyStateCopy {
  title: string
  body: string
  actionLabel: string
  /** The family the Browse & add action opens the directory ON — never widened to `all`. */
  actionFamily: FamilyTab
}

export interface EmptyStateInput {
  /** The masthead search box, raw — trimmed here. */
  query: string
  tab: FamilyTab
  statusFilter: StatusFilter
}

/**
 * Why the list is empty, and the way out. Total over its inputs: every combination
 * returns copy plus a pre-scoped action, so an empty list is always explained.
 */
export function emptyStateFor({ query, tab, statusFilter }: EmptyStateInput): EmptyStateCopy {
  const q = query.trim()
  const title = familyTitle(tab)
  const where = title ? ` in ${title}` : ''
  const action = { actionLabel: 'Browse & add', actionFamily: tab }

  if (q) {
    return {
      ...action,
      title: `No connectors match “${q}”`,
      body: 'Try a different search or filter, or browse the directory to add one.',
    }
  }

  if (statusFilter === 'attention') {
    return {
      ...action,
      title: `Nothing needs attention${where}`,
      body: 'Every connector here is working. A connector appears under Attention as soon as it errors.',
    }
  }

  if (statusFilter === 'connected') {
    return {
      ...action,
      title: `Nothing connected${where}`,
      body: 'No connector here is connected right now. Reconnect one that has an error, or browse the directory to add another.',
    }
  }

  if (title) {
    return {
      ...action,
      title: `Nothing in ${title} yet`,
      body: `${FAMILY_BLURB[tab as FamilyKey]} Browse the directory to add one.`,
    }
  }

  return {
    ...action,
    title: 'No connectors',
    body: 'Browse the directory to connect a channel, a data source, a web provider, or an MCP server.',
  }
}
