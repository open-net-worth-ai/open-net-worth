// The card Lucius's `connectors.open_setup` shows in the transcript — the connector's own logo,
// name, publisher and verification, and a Set up / Connect button — in place of a bare navigation
// chip (owner, 2026-10-31: "Claude shows a card"). Every field comes from OUR catalog, keyed by the
// command's id. No reason line: Lucius's own message sits right beside the card and says why in
// full, so a truncated copy of it only repeated it (owner, 2026-10-31).
// The button does exactly what the chip did: re-dispatch the command and go to Connectors, where
// the user fills in credentials and clicks Connect themselves. A connector that is already
// installed reads its LIVE state: Reconnect (or Authenticate) while it needs attention — going
// straight on to the sign-in — and Open once it works. "Connect" on a connector the user already
// has, whose sign-in had expired, read as if it had never been set up (owner, 2026-11-13).
import { mcpNeedsSignIn, toEntries } from '../tabs/connectors/adapter'
import { CATALOG, MCP_INSTALL_PRESETS } from '../tabs/connectors/catalog'
import { resolveDirectory } from '../tabs/connectors/directoryRows'
import { useConnectors } from '../tabs/connectors/useConnectors'
import { icon } from '../tabs/connectors/icons'
import { connectorLogo } from '../tabs/connectors/logos'
import { FIRST_PARTY_PUBLISHER } from '../tabs/connectors/VendorTrust'
import { preopenSignInWindow } from '../tabs/connectors/oauthSignIn'
import type { ReactNode } from 'react'
import { ArrowRight, Share2 } from '../../ui/icons'
import type { Connector } from '../tabs/connectors/types'

/** `custom` opens the Add MCP server form — the catalog's one generic entry. An installed id
 *  (`mcp:<server>`) resolves to the directory card it merged into, or its own row. */
function entryFor(id: string, connectors: Connector[]) {
  if (id === 'custom') return CATALOG.find((e) => e.generic)
  const row = resolveDirectory(connectors).find(
    (r) => !r.generic && (r.id === id || r.installedId === id),
  )
  return row ?? CATALOG.find((e) => e.id === id)
}

type CardAction = { label: string; connect: boolean; preopen: boolean }

/** What the button says and does, from live state. */
function actionFor(
  entry: { id: string; installed?: boolean; installedId?: string },
  connectors: Connector[],
): CardAction & { status?: string } {
  if (entry.installed) {
    const installedId = entry.installedId ?? entry.id
    const live = toEntries(connectors).find((e) => e.id === installedId)
    const wire = connectors.find((c) => c.id === installedId)
    if (live?.attention) {
      // Only a dead sign-in opens a window; any other failure retries the connection first.
      const signIn = !!wire && mcpNeedsSignIn(wire)
      return { label: live.fixLabel, connect: true, preopen: signIn, status: live.statusText }
    }
    return { label: 'Open', connect: false, preopen: false }
  }
  // A hosted server that signs in over OAuth (IBKR, Webull…) connects in one click, like Claude's
  // connector cards: Connect opens the provider's sign-in. Anything needing a key or a local
  // command opens its form to fill in.
  const preset = MCP_INSTALL_PRESETS[entry.id]
  const signsIn = preset?.transport === 'remote' && preset.auth === 'oauth'
  return { label: signsIn ? 'Connect' : 'Set up', connect: signsIn, preopen: signsIn }
}

export function ConnectorSetupCard({
  id,
  onOpen,
  fallback,
}: {
  id: string
  /** `connect`: go straight on to the provider's sign-in (Connect on a sign-in connector). */
  onOpen: (opts: { connect: boolean }) => void
  /** What to show when the id names nothing in the catalog — the plain chip. */
  fallback: ReactNode
}): ReactNode {
  const { connectors } = useConnectors()
  const entry = entryFor(id, connectors)
  if (!entry) return fallback
  const logo = connectorLogo(entry.id)
  // No brand mark (Email, Webhook): its own icon in its own hue on a tint, as its Connectors
  // tile draws it. The custom server keeps the Connectors icon.
  const glyph = entry.generic ? null : icon(entry.icon)()
  const action = actionFor(entry, connectors)
  // Who built it, by the connector page's rule (`PublisherLine`, owner 2026-08-21): an MCP
  // server's own publisher; a connector of ours is Open Net Worth's, whatever service it calls
  // (that service already names the card). "By Enable Banking" contradicted the page's
  // "Developed by Open Net Worth" (owner, 2026-11-14).
  const by = entry.generic
    ? entry.sub
    : !entry.mcp
      ? `By ${FIRST_PARTY_PUBLISHER}`
      : entry.publisher
        ? `By ${entry.publisher}`
        : entry.sub
  return (
    <div className="cmd-setup-card">
      <span
        className="cmd-setup-logo"
        aria-hidden="true"
        style={
          !logo && glyph
            ? {
                color: entry.hue,
                background: `color-mix(in srgb, ${entry.hue} 14%, transparent)`,
                borderColor: `color-mix(in srgb, ${entry.hue} 30%, transparent)`,
              }
            : undefined
        }
      >
        {logo ? <img src={logo} alt="" /> : (glyph ?? <Share2 size={16} />)}
      </span>
      <div className="cmd-setup-main">
        <div className="cmd-setup-name">
          {entry.name}
          {entry.verification === 'verified' ? (
            <span className="cmd-setup-badge">Verified</span>
          ) : null}
        </div>
        <div className="cmd-setup-sub">{action.status ? `${by} · ${action.status}` : by}</div>
      </div>
      <button
        type="button"
        className="cmd-setup-btn"
        onClick={() => {
          if (action.preopen) preopenSignInWindow()
          onOpen({ connect: action.connect })
        }}
      >
        {action.label}
        <ArrowRight size={12} aria-hidden="true" />
      </button>
    </div>
  )
}
