// owner_id → the connector's real brand mark, for the icon rail.
//
// The server already told us WHO owns a call (`display.owner_id`). Only the mark itself is a
// frontend asset, so only that last hop happens here — and it is a lookup by id, not a join
// across sources.
//
// Two cases, and the difference is a security property, not an implementation detail:
//
//   * A NATIVE connector's id IS its catalog id (`plaid`, `web_search:tavily`), so
//     `connectorLogo(id)` resolves it synchronously with no network at all.
//
//   * An installed MCP server's id is `mcp:<whatever the operator named it>`, which
//     `logos.ts` deliberately refuses to match — a server calling itself `stripe` must not
//     get to wear Stripe's mark. Its only legitimate join is BY ENDPOINT
//     (`catalogProvenanceFor`), which `adapter.ts::toEntry` already performs. So an MCP mark
//     needs the connectors read, and we fetch it ONLY when an MCP-owned call is actually on
//     screen — a conversation that never touches one pays nothing.
//
// No match is a normal outcome, not a failure: the row falls back to its verb glyph.

import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { fetchCachedQuery } from '../../../state/queryCache'
import { toEntries } from '../../tabs/connectors/adapter'
import type { IconKey } from '../../tabs/connectors/icons'
import { connectorLogo } from '../../tabs/connectors/logos'
import type { Connector } from '../../tabs/connectors/types'

const CONNECTORS_KEY = '/api/connectors'
const MCP_PREFIX = 'mcp:'
const CORE_OWNER = 'core'

async function fetchConnectors(): Promise<Connector[]> {
  const response = await authFetch(CONNECTORS_KEY)
  if (!response.ok) throw new Error(`${CONNECTORS_KEY} → HTTP ${response.status}`)
  return (await response.json()).connectors as Connector[]
}

/** What a connector looks like: its brand mark if it has one, else the category glyph and
 *  hue its own card uses. `email` and `webhook` are protocols, not brands, so they have no
 *  mark — and were falling back to a generic verb glyph while the Connectors screen showed
 *  them an envelope in their own colour. Same connector, two identities (owner, 2026-08-23). */
export interface OwnerLook {
  logo?: string
  icon?: IconKey
  hue?: string
}

/** Resolved once per app session and shared by every chat surface — the dock, the tab and
 *  the side panel render the same transcript and must not each pay for it. */
let ownerLooks: Record<string, OwnerLook> | null = null
let inFlight: Promise<void> | null = null
const listeners = new Set<() => void>()

function loadOwnerLooks(): void {
  if (ownerLooks !== null || inFlight) return
  inFlight = fetchCachedQuery<Connector[]>(CONNECTORS_KEY, fetchConnectors)
    .then((connectors) => {
      const next: Record<string, OwnerLook> = {}
      for (const entry of toEntries(connectors)) {
        next[entry.id] = {
          ...(entry.logo ? { logo: entry.logo } : {}),
          ...(entry.icon ? { icon: entry.icon as IconKey } : {}),
          ...(entry.hue ? { hue: entry.hue } : {}),
        }
      }
      ownerLooks = next
    })
    .catch(() => {
      // A missing mark is a cosmetic outcome, and retrying on every render of a long
      // transcript would be worse than the glyph fallback it degrades to.
      ownerLooks = {}
    })
    .finally(() => {
      inFlight = null
      listeners.forEach((fn) => fn())
    })
}

/** A connector's look from what is already loaded: a native id resolves its brand mark with
 *  no network; an MCP server's mark needs the connectors read (`useToolOwnerLogos`) to have
 *  landed first. For callers that render outside React — the composer builds its chips as DOM. */
export function connectorLook(id: string): OwnerLook {
  if (!id || id === CORE_OWNER) return {}
  const look = ownerLooks?.[id] ?? {}
  if (!look.logo && !id.startsWith(MCP_PREFIX)) {
    const direct = connectorLogo(id)
    if (direct) return { ...look, logo: direct }
  }
  return look
}

/**
 * A resolver for the owner ids currently on screen.
 *
 * `ownerIds` drives only WHETHER the connectors read is needed; the returned function is
 * stable in behaviour, so a row calls it during render without an effect of its own.
 *
 * The read fires for ANY connector-owned row, not just an MCP one. A native connector's
 * brand mark still resolves synchronously from its id (that join needs nothing from the
 * server), but its category glyph and hue are the server's to tell us — which is what gives
 * `email` its envelope instead of a generic verb glyph.
 */
export function useToolOwnerLogos(ownerIds: string[]): (ownerId: string) => OwnerLook {
  const needsFetch = ownerIds.some((id) => id && id !== CORE_OWNER)
  const [, bump] = useState(0)

  useEffect(() => {
    if (!needsFetch) return
    if (ownerLooks !== null) return
    const listener = () => bump((n) => n + 1)
    listeners.add(listener)
    loadOwnerLooks()
    return () => {
      listeners.delete(listener)
    }
  }, [needsFetch])

  // A native connector's id IS its catalog id, so its mark resolves with no network at all;
  // an MCP server's cannot (see the header note) and comes from the read.
  return connectorLook
}
