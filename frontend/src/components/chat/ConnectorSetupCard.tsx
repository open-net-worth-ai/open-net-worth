// The card Lucius's `connectors.open_setup` shows in the transcript — the connector's own logo,
// name, publisher and verification, and a Set up / Connect button — in place of a bare navigation
// chip (owner, 2026-10-31: "Claude shows a card"). Every field comes from OUR catalog, keyed by the
// command's id. No reason line: Lucius's own message sits right beside the card and says why in
// full, so a truncated copy of it only repeated it (owner, 2026-10-31).
// The button does exactly what the chip did: re-dispatch the command and go to Connectors, where
// the user fills in credentials and clicks Connect themselves.
import { CATALOG, MCP_INSTALL_PRESETS } from '../tabs/connectors/catalog'
import { icon } from '../tabs/connectors/icons'
import { connectorLogo } from '../tabs/connectors/logos'
import { preopenSignInWindow } from '../tabs/connectors/oauthSignIn'
import type { ReactNode } from 'react'
import { ArrowRight, Share2 } from '../../ui/icons'

/** `custom` opens the Add custom server form — the catalog's one generic entry. */
function entryFor(id: string) {
  return id === 'custom' ? CATALOG.find((e) => e.generic) : CATALOG.find((e) => e.id === id)
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
  const entry = entryFor(id)
  if (!entry) return fallback
  const logo = connectorLogo(entry.id)
  // No brand mark (Email, Webhook): its own icon in its own hue on a tint, as its Connectors
  // tile draws it. The custom server keeps the Connectors icon.
  const glyph = entry.generic ? null : icon(entry.icon)()
  // A hosted server that signs in over OAuth (IBKR, Webull…) connects in one click, like Claude's
  // connector cards: Connect opens the provider's sign-in. Anything needing a key or a local
  // command opens its form to fill in.
  const preset = MCP_INSTALL_PRESETS[entry.id]
  const signsIn = preset?.transport === 'remote' && preset.auth === 'oauth'
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
        <div className="cmd-setup-sub">
          {entry.generic ? entry.sub : entry.publisher ? `By ${entry.publisher}` : entry.sub}
        </div>
      </div>
      <button
        type="button"
        className="cmd-setup-btn"
        onClick={() => {
          if (signsIn) preopenSignInWindow()
          onOpen({ connect: signsIn })
        }}
      >
        {signsIn ? 'Connect' : 'Set up'}
        <ArrowRight size={12} aria-hidden="true" />
      </button>
    </div>
  )
}
