// Publisher provenance — how we state who published a connector and what we can vouch for
// (spec §5). TWO shapes, for two genuinely different moments:
//
//  · `PublisherLine` — BROWSING. One attribution line ("Developed by Firecrawl ↗") and one
//    muted sentence. Used by the directory preview, whose job is "should I even look at
//    this"; the endpoint it would run lives in that page's quiet Details footer instead.
//  · `VendorTrust` — COMMITTING or INSPECTING. The boxed panel: publisher, provenance badge,
//    the endpoint verbatim in monospace, and the long note. Used by the add flow's review
//    step (you are about to trust this exact endpoint) and by an installed server's detail
//    page (this is the endpoint you are already running).
//
// Both read the same three-state `VerificationState`, and the copy for a state must stay
// consistent between them — which is why they share this file rather than drifting apart in
// two. Divergence D23 in PARITY_AND_GAPS records why the preview stopped rendering the panel.
//
// The panel takes flat props rather than a connector object, because its three callers hold
// three different objects (a catalog row, a probe result, a live connector view).

import type { JSX, ReactNode } from 'react'
import { Badge, type BadgeProps } from './primitives'
import type { VerificationState } from './model'

/** Our own name, as the catalog spells it. A connector WE publish is not a third party to
 *  ourselves, so it gets the attribution line with no trust sentence under it. */
export const FIRST_PARTY_PUBLISHER = 'Open Net Worth'
/** Our own homepage — the link target for the "Developed by Open Net Worth" half of a
 *  native connector's attribution line (owner correction, 2026-08-21: naming only the API
 *  vendor left the reader with no idea WE wrote the integration; naming only us left the
 *  API vendor uncredited — a native connector line states both). */
export const FIRST_PARTY_URL = 'https://opennetworth.ai'

// ── the external-link affordance ────────────────────────────────────────────

/** ↗ — small enough to sit inside a 13px line without pushing it. Deliberately NOT
 *  `Icons.link` (a 19px chain glyph): this marks "leaves the app", not "is a link". */
function ArrowUpRight(): JSX.Element {
  return (
    <svg
      width={11}
      height={11}
      viewBox="0 0 24 24"
      stroke="currentColor"
      fill="none"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 17 17 7" />
      <path d="M8 7h9v9" />
    </svg>
  )
}

/** An outbound link, always carrying the ↗. `rel="noopener noreferrer"` is not optional —
 *  every href here is a third party's, and several are self-reported by a server. */
export function ExternalLink({
  href,
  children,
}: {
  href: string
  children: ReactNode
}): JSX.Element {
  return (
    <a className="cn-extlink" href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <ArrowUpRight />
    </a>
  )
}

// ── PublisherLine: the browse-time shape ────────────────────────────────────

/**
 * One sentence per state, in the product's voice. They differ because the claims differ:
 * "we confirmed this is the vendor's" and "we have no idea who published this" are not the
 * same thing to a reader, and a boolean is exactly how the second gets presented as the
 * first (see `VerificationState`).
 *
 * Kept free of "community project" on purpose: the catalog marks first-party API
 * integrations (Plaid, IBKR) `unverified` too, and calling those community projects would be
 * false. What is true of every `unverified` entry is that we have not verified the publisher.
 */
const TRUST_SENTENCE: Record<VerificationState, string> = {
  verified:
    'We’ve verified this publisher. Open Net Worth still doesn’t control which tools a developer makes available, and can’t verify that they will work as intended or that they won’t change.',
  unverified:
    'Only use connectors from developers you trust. We haven’t verified this publisher, and Open Net Worth doesn’t control which tools a developer makes available or verify that they won’t change.',
  unknown:
    'Only use connectors from developers you trust. We don’t know who published this one — Open Net Worth can’t vouch for its code, the tools it makes available, or that they won’t change.',
}

export interface PublisherLineProps {
  /** The publisher, when one is KNOWN. Absent for a server the user points us at themselves —
   *  which must never borrow our name. Pair an absent publisher with `verification: 'unknown'`. */
  publisher?: string | null
  /** Where the publisher lives — the docs/product page, else the source repo. Absent for the
   *  large class of hosted vendor servers with neither, in which case the name renders as
   *  plain text. Never fabricate one: a wrong link here is a phishing surface. */
  href?: string
  verification: VerificationState
  /**
   * Whether this is an MCP entry — the ONLY kind that gets the trust sentence, and the ONLY
   * kind whose NAMED publisher appears on the attribution line at all.
   *
   * Spec §5 is titled "Vendor trust (MCP only)" and `CNR-TRUST-NATIVE-ABSENT` says a non-MCP
   * connector shows no trust panel; the sentence is that panel's browse-time half (D23), so it
   * is bound by the same rule. It was not, and the result was Serper — a Web provider WE build
   * and operate, against an API we chose — telling the reader "only use connectors from
   * developers you trust, we haven't verified this publisher". We author that integration:
   * there is no third party in the loop for the reader to be warned about, and the warning
   * spent the reader's attention on a risk that does not exist here.
   *
   * The attribution line's OWN wording is bound by the same distinction (owner correction,
   * 2026-08-21, simplified same day): a native line always reads "Developed by Open Net
   * Worth" — the vendor's own name (Tavily, Serper, …) is dropped from THIS line entirely
   * rather than named alongside it; it is still visible elsewhere on the card (name, logo,
   * description) and in the Details footer's "Vendor" field, so nothing is actually hidden,
   * this line just answers one question — "did WE build this" — cleanly. Only a real MCP
   * entry names its publisher here, because that is the one case "Developed by {name}" is
   * literally true.
   */
  mcp?: boolean
}

export function PublisherLine({
  publisher,
  href,
  verification,
  mcp = false,
}: PublisherLineProps): JSX.Element | null {
  const name = publisher?.trim() ?? ''
  if (!name && verification !== 'unknown') return null
  // Kept beside the `mcp` gate rather than folded into it: they are two different reasons.
  // `mcp` is "there is no third party here"; this is "the third party is us".
  const sentence = mcp && name !== FIRST_PARTY_PUBLISHER

  if (!name && !sentence) return null

  return (
    <div>
      {name ? (
        <div style={{ fontSize: 13, fontWeight: 400, color: 'var(--ink)' }}>
          Developed by{' '}
          {mcp ? (
            href ? (
              <ExternalLink href={href}>{name}</ExternalLink>
            ) : (
              <span>{name}</span>
            )
          ) : (
            <ExternalLink href={FIRST_PARTY_URL}>{FIRST_PARTY_PUBLISHER}</ExternalLink>
          )}
        </div>
      ) : null}
      {sentence ? (
        <div className="rd-help" style={{ marginTop: 4, fontSize: 12, lineHeight: 1.55 }}>
          {TRUST_SENTENCE[verification]}
        </div>
      ) : null}
    </div>
  )
}

// ── VendorTrust: the commit/inspect panel ───────────────────────────────────

export interface VendorTrustProps {
  /**
   * The publisher, when one is KNOWN. Omit (or pass an empty string) for a server the user
   * pointed us at themselves — the generic "Custom MCP server" flow claims no author, and
   * must never borrow ours. Callers must NOT pre-substitute a placeholder like "Unknown
   * publisher" here; pair an absent publisher with `verification: 'unknown'`.
   */
  developer?: string
  /** Three states, never a boolean — see `VerificationState`. */
  verification: VerificationState
  /** The URL or local command, verbatim — what you are actually trusting is the endpoint. */
  url?: string
}

// Spec §5 gives the note two shapes — one for a known publisher, one for a server the user
// specified. `unverified` takes the known-publisher note plus the one sentence that is the
// whole reason the state exists: we found a maintainer, and it is NOT the vendor.
const THIRD_PARTY_NOTE =
  'This is a third-party MCP server — its code runs outside Open Net Worth and isn’t audited by us. It can only reach the tools listed below; nothing else on this device.'

const NOT_VENDOR_OFFICIAL_NOTE =
  ' We haven’t confirmed it’s published by the vendor it integrates with — treat it as a community project.'

const UNKNOWN_NOTE =
  'You’re pointing Lucius at a server you specify. Open Net Worth can’t vouch for its code or the tools it exposes — review the URL and only add servers you trust.'

const BADGE: Record<VerificationState, { label: string; tone: BadgeProps['tone'] }> = {
  verified: { label: 'Verified publisher', tone: 'good' },
  unverified: { label: 'Unverified publisher', tone: 'warn' },
  unknown: { label: 'Unknown publisher', tone: 'warn' },
}

const NOTE: Record<VerificationState, string> = {
  verified: THIRD_PARTY_NOTE,
  unverified: THIRD_PARTY_NOTE + NOT_VENDOR_OFFICIAL_NOTE,
  unknown: UNKNOWN_NOTE,
}

export function VendorTrust({ developer, verification, url }: VendorTrustProps): JSX.Element {
  const publisher = developer?.trim() ?? ''
  const badge = BADGE[verification]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* "Vendor trust" is OUR name for this concept, not a label a reader needs — the panel
          answers "who published this", so it says so. Plain stacked content, no surrounding
          card — `.rd-code` below already carries its own background/border, and a box around
          the whole section added nothing "Tool permissions" (the section right below this
          one, also just a sublabel + plain content) didn't already get without one
          (owner review, 2026-08-18: "why do we have to nest it"). */}
      {publisher ? (
        <>
          <div className="cn-sublabel">Publisher</div>
          {/* `space-between` puts the name left and the badge right. */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              flexWrap: 'wrap',
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>{publisher}</div>
            <Badge tone={badge.tone}>{badge.label}</Badge>
          </div>
        </>
      ) : (
        // No name to put the badge opposite — with "Unknown publisher" already stated in the
        // badge (repeating it as a name would print the same words twice), the badge shares
        // the sublabel's OWN row instead of getting a whole row to itself: a lone pill on an
        // otherwise-empty line read as floating/unstyled rather than deliberate (owner review,
        // 2026-08-18, on the live app — "tis is ugly").
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div className="cn-sublabel">Publisher</div>
          <Badge tone={badge.tone}>{badge.label}</Badge>
        </div>
      )}
      {url ? <code className="rd-code">{url}</code> : null}
      <div className="rd-help" style={{ fontSize: 12 }}>
        {NOTE[verification]}
      </div>
    </div>
  )
}
