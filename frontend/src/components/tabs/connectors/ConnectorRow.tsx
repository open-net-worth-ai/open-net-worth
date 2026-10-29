// One connector list row (spec §2) — ported VERBATIM from the prototype's `Row`.
//
// A row answers two questions and stops: WHAT is it, and IS IT WORKING. Everything else
// is one click away on the detail page. Deliberately absent, and not to be re-added: a
// Configure button (the whole row already opens the detail page), an enable/disable toggle
// (destructive-adjacent — it belongs where the consequences are visible), a Test button,
// a family label and a sub-line. The category chips (what it is FOR — `Brokerage`, `Search`)
// are the one exception, added on owner request (2026-10-29): with several MCP servers in one
// list, the name alone did not say what each was for.

import type { JSX, KeyboardEvent } from 'react'
import { EntityRow } from '../../../lucius/EntityRow'
import { Badge, Btn, Mono } from './primitives'
import { Icons } from './icons'
import { CategoryChips } from './DirectoryCard'
import type { ConnectorEntry } from './model'

export interface ConnectorRowProps {
  entry: ConnectorEntry
  /** The row IS the affordance — clicking anywhere opens the detail page. */
  onOpen: (entry: ConnectorEntry) => void
  /** The ONE row action, and only on a connector in an error state. */
  onReconnect: (entry: ConnectorEntry) => void
}

export function ConnectorRow({ entry, onOpen, onReconnect }: ConnectorRowProps): JSX.Element {
  // The prototype marks the row `role="button"` and `tabIndex={0}` but wires no key
  // handler, so a keyboard user can focus a control that does nothing. Activating on
  // Enter/Space is additive — it changes no markup and no pixel.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    onOpen(entry)
  }

  // EntityRow is a TRANSPARENT wrapper: it stamps `data-entity-id` + `data-ctx-*` and marks
  // the host draggable, which is what makes a connector `@`-mentionable in Lucius and
  // drag-droppable into the composer. It renders the host element itself, so it IS the
  // `.cn-row` grid rather than an extra layer inside `.cn-list` — wrapping instead would
  // make every row the only child of its own wrapper and silently kill the row separators.
  return (
    <EntityRow
      as="div"
      className="cn-row"
      type="connector"
      id={entry.id}
      label={entry.name}
      item={entry}
      toFields={(it) => ({
        name: it.name,
        kind: it.kind,
        enabled: it.enabled ? 'on' : 'off',
        status: it.statusText,
      })}
      sublabel={`${entry.statusText} • ${entry.enabled ? 'on' : 'off'}`}
      // Every connector chip rendered as the same grey glyph — the glyph identifies the TYPE,
      // and per-entity distinction comes from color, which this row never forwarded.
      color={entry.hue}
      onClick={() => onOpen(entry)}
      onKeyDown={onKeyDown}
      role="button"
      tabIndex={0}
    >
      <Mono icon={entry.icon} hue={entry.hue} logo={entry.logo} />
      <div className="cn-main">
        <div className="cn-name">
          <b>{entry.name}</b>
          {entry.tag ? <span className="cn-tag">{entry.tag}</span> : null}
          {entry.categories?.length ? <CategoryChips categories={entry.categories} /> : null}
        </div>
      </div>
      <div className="cn-meta">
        {entry.tone === 'good' ? (
          <span className="cn-statusgood" title={entry.statusText}>
            <Icons.checkcircle /> {entry.statusText}
          </span>
        ) : entry.tone === 'bad' ? (
          <span className="cn-statusbad" title={entry.statusText}>
            <Icons.warn /> {entry.statusText}
          </span>
        ) : (
          <Badge tone={entry.tone}>{entry.statusText}</Badge>
        )}
        {entry.attention ? (
          <span onClick={(e) => e.stopPropagation()}>
            <Btn size="xs" variant="primary" onClick={() => onReconnect(entry)}>
              Reconnect
            </Btn>
          </span>
        ) : null}
      </div>
    </EntityRow>
  )
}
