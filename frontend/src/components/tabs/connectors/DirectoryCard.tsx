// One Browse & add catalog card — ported VERBATIM from the prototype's own card component.
//
// Two rules of spec §6 live entirely in this file:
//
//  1. An INSTALLED entry is not hidden. It keeps its card and swaps the `+` for a settings
//     gear that opens its detail page. Hiding installed entries produced dead ends — a
//     Channels filter reading "No matches" purely because the one real channel was set up.
//  2. The verified marker is a "Verified" TEXT badge, never a green check: a checkmark
//     sitting beside an Add button read as "already added".

import type { JSX } from 'react'
import { Btn, Mono } from './primitives'
import { Icons } from './icons'
import { skipsPreview, type DirectoryRow } from './catalog'

export interface DirectoryCardProps {
  d: DirectoryRow
  /** Start the add flow — the MCP setup drawer, or the connector's own config. */
  onAdd: (entry: DirectoryRow) => void
  /** Open the preview page. Never reached for the generic MCP card (see `skipsPreview`). */
  onPreview: (entry: DirectoryRow) => void
  /** Open an already-installed entry's detail page. */
  onConfigure: (entry: DirectoryRow) => void
  /** A suggestion under a list section — the dashed "add this" slot the Assets screen's
   *  "Add more" tiles use, rather than a card in the directory grid. */
  suggested?: boolean
}

export function DirectoryCard({
  d,
  onAdd,
  onPreview,
  onConfigure,
  suggested = false,
}: DirectoryCardProps): JSX.Element {
  // Clicking the card body ALWAYS opens the entry's landing page — installed or not. Only the
  // action ON that page differs (Add vs. the installed entry's route-to-settings), so browsing
  // is one consistent gesture and an installed connector is still readable before you act on
  // it. The gear stays as the explicit shortcut for "skip the reading, take me to settings".
  // The generic MCP card is the one exception: it has nothing to preview, so it goes STRAIGHT
  // to the add form.
  const activate = () => {
    if (skipsPreview(d)) onAdd(d)
    else onPreview(d)
  }

  return (
    <div
      // `generic` is the one card with nothing specific behind it (no logo, no publisher, no
      // preview) — a dashed border marks it as a build-your-own slot, not a real product
      // sitting in the grid alongside Playwright MCP and the rest (owner review, 2026-08-19:
      // "the custom mcp server shouldn't be as other row").
      className={'cn-dircard' + (d.generic ? ' generic' : '') + (suggested ? ' suggested' : '')}
      onClick={activate}
      // The prototype leaves this div keyboard-inert. Handling Enter/Space is invisible to the
      // pixel diff and is required of a role="button" by the UI Quality Gate.
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          activate()
        }
      }}
      role="button"
      tabIndex={0}
      style={{ cursor: 'pointer' }}
    >
      <div className="cn-dircard-head">
        <Mono icon={d.icon} hue={d.hue} logo={d.logo} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="cn-dircard-name">
            {d.name}
            {d.verification === 'verified' ? (
              <span className="cn-verified-tag">Verified</span>
            ) : null}
            {d.tag ? <span className="cn-tag">{d.tag}</span> : null}
          </div>
        </div>
        {d.installed ? (
          <Btn
            size="xs"
            aria-label={'Configure ' + d.name}
            onClick={(e) => {
              e.stopPropagation()
              onConfigure(d)
            }}
          >
            <Icons.settings />
          </Btn>
        ) : (
          <Btn
            size="xs"
            variant="primary"
            aria-label={'Add ' + d.name}
            onClick={(e) => {
              e.stopPropagation()
              onAdd(d)
            }}
          >
            <Icons.plus />
          </Btn>
        )}
      </div>
      <div className="cn-dircard-desc">{d.sub}</div>
      {d.categories.length > 0 ? <CategoryChips categories={d.categories} /> : null}
    </div>
  )
}

/** What a connector is FOR, as small chips — the same topics the preview page lists. */
export function CategoryChips({ categories }: { categories: readonly string[] }): JSX.Element {
  return (
    <span className="cn-cats">
      {categories.map((c) => (
        <span key={c} className="cn-cat">
          {c}
        </span>
      ))}
    </span>
  )
}
