// @vitest-environment jsdom
//
// Publisher provenance (spec §5) — one test per state, for the PANEL.
//
// The state exists because a boolean cannot tell "we looked and found a community
// maintainer" apart from "we have no idea who this is". That collapse is not theoretical:
// an internal RFC described FRED MCP and SEC EDGAR MCP as *official* when both are one
// independent maintainer's projects. `unverified` and `unknown` must stay distinguishable
// in the badge AND in the note.
//
// The panel is now the COMMIT/INSPECT shape only — the add flow's review step and an
// installed server's detail page. The browse-time shape (`PublisherLine`) and the page that
// renders it are covered by `DirectoryPreview.test.tsx`; D23 records the split.

import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { VendorTrust } from './VendorTrust'

afterEach(cleanup)

describe('the panel heading is a label a reader needs', () => {
  it('says Publisher, never our internal "vendor trust"', () => {
    const { container } = render(<VendorTrust developer="Firecrawl" verification="verified" />)
    expect(screen.getByText('Publisher')).toBeTruthy()
    expect(container.textContent?.toLowerCase()).not.toContain('vendor trust')
  })
})

describe('verified — vendor-official', () => {
  it('badges the publisher as verified', () => {
    render(<VendorTrust developer="Microsoft (Playwright)" verification="verified" />)
    expect(screen.getByText('Microsoft (Playwright)')).toBeTruthy()
    expect(screen.getByText('Verified publisher')).toBeTruthy()
    expect(screen.queryByText('Unverified publisher')).toBeNull()
    expect(screen.queryByText('Unknown publisher')).toBeNull()
  })

  it('carries the third-party note WITHOUT the community caveat', () => {
    render(<VendorTrust developer="Firecrawl" verification="verified" />)
    expect(screen.getByText(/isn’t audited by us/)).toBeTruthy()
    expect(screen.queryByText(/community project/)).toBeNull()
  })
})

describe('unverified — real and known, but not vendor-official', () => {
  it('badges the publisher as unverified while still naming it', () => {
    render(<VendorTrust developer="Community" verification="unverified" />)
    expect(screen.getByText('Community')).toBeTruthy()
    expect(screen.getByText('Unverified publisher')).toBeTruthy()
    expect(screen.queryByText('Verified publisher')).toBeNull()
  })

  it('says plainly that we have NOT confirmed vendor authorship', () => {
    render(<VendorTrust developer="Community" verification="unverified" />)
    // the distinction a boolean destroyed: known maintainer ≠ vendor-official
    expect(screen.getByText(/community project/)).toBeTruthy()
    expect(screen.getByText(/haven’t confirmed it’s published by the vendor/)).toBeTruthy()
  })

  it('is NOT the same rendering as unknown', () => {
    const { container } = render(<VendorTrust developer="Community" verification="unverified" />)
    const unverified = container.textContent ?? ''
    cleanup()
    const second = render(<VendorTrust verification="unknown" />)
    expect(second.container.textContent).not.toBe(unverified)
  })
})

describe('unknown — no publisher at all', () => {
  it('claims no author and badges the publisher unknown', () => {
    render(<VendorTrust verification="unknown" />)
    expect(screen.getByText('Unknown publisher')).toBeTruthy()
    expect(screen.queryByText('Open Net Worth')).toBeNull()
  })

  it('uses the stronger "a server you specify" note, not the known-publisher one', () => {
    render(<VendorTrust verification="unknown" url="https://mcp.local" />)
    expect(screen.getByText(/can’t vouch for its code/)).toBeTruthy()
    expect(screen.queryByText(/isn’t audited by us/)).toBeNull()
  })

  it('treats a blank publisher string as no publisher', () => {
    render(<VendorTrust developer="   " verification="unknown" />)
    expect(screen.getByText('Unknown publisher')).toBeTruthy()
  })
})

/**
 * D6 dropped the name node for an unnamed publisher, leaving the badge as the header row's
 * only child. Two fixes since: `flex-end` kept it pinned to the named case's right edge
 * (consistent, but a lone pill over a wide empty gap read as an unstyled glitch, not a
 * deliberate corner — owner review, 2026-08-18). `flex-start` moved it to the row's start but
 * left it alone on its OWN row below the "Publisher" sublabel — still an orphan, just on the
 * other side (owner review, same day, on the live app — "tis is ugly"). It now shares the
 * sublabel's OWN row instead of getting a row of its own: "Publisher" states what the badge is
 * about, so the two read as one header rather than a label followed by a floating pill.
 */
describe('the trust badge sits naturally whether or not there is a name beside it', () => {
  /** The row the badge sits in. */
  const header = (c: HTMLElement) => c.querySelector('.rd-badge')?.parentElement

  it('shares the "Publisher" sublabel\'s own row when there is no name to sit opposite', () => {
    const { container } = render(<VendorTrust verification="unknown" />)
    const row = header(container)!
    expect(row.textContent).toContain('Publisher')
    expect(row.textContent).toContain('Unknown publisher')
  })

  it('still spaces name and badge apart when a publisher IS named', () => {
    const { container } = render(<VendorTrust developer="Firecrawl" verification="verified" />)
    expect(header(container)?.style.justifyContent).toBe('space-between')
  })
})

// The over-disclosure D23 removed was specific to BROWSING. Where you are committing to an
// endpoint, or inspecting one you already run, the exact URL or command line is the thing you
// are trusting (spec §5) and it stays — these assertions are the reason the panel survives at
// all, so they are the ones that must not quietly rot.
describe('the endpoint is what you are trusting', () => {
  it('renders the URL verbatim in monospace', () => {
    const { container } = render(
      <VendorTrust developer="Firecrawl" verification="verified" url="https://mcp.firecrawl.dev" />,
    )
    const code = container.querySelector('code.rd-code')
    expect(code?.textContent).toBe('https://mcp.firecrawl.dev')
  })

  it('renders a local command verbatim too', () => {
    const { container } = render(
      <VendorTrust verification="unknown" url="npx -y @vendor/ibkr-mcp" />,
    )
    expect(container.querySelector('code.rd-code')?.textContent).toBe('npx -y @vendor/ibkr-mcp')
  })

  it('omits the endpoint row when there is none', () => {
    const { container } = render(<VendorTrust developer="Community" verification="unverified" />)
    expect(container.querySelector('code.rd-code')).toBeNull()
  })
})
