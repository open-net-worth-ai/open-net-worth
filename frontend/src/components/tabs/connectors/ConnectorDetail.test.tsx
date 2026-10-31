// @vitest-environment jsdom
//
// The detail page (spec §3) and the vendor-trust panel (spec §5). The sharpest contract
// here is the one the previous design got wrong: a server we did not publish must claim
// NO author — never "Developed by Open Net Worth" over unaudited third-party code.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { ConnectorDetail } from './ConnectorDetail'
import { buildToolGroups, type ConnectorDetailEntry } from './model'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', async (load) => ({
  ...(await load<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))

afterEach(() => {
  cleanup()
  navigate.mockReset()
})

function entry(overrides: Partial<ConnectorDetailEntry> = {}): ConnectorDetailEntry {
  const tools = overrides.tools ?? ['get_positions', 'place_order']
  return {
    id: 'ibkr',
    name: 'IBKR Flex',
    kind: 'ibkr',
    icon: 'investments',
    hue: 'var(--ibkr)',
    families: ['Data source'],
    statusText: 'Connected',
    tone: 'good',
    attention: false,
    enabled: true,
    configured: true,
    sub: 'Flex query • nightly import',
    desc: 'Imports positions, trades and cash activity nightly.',
    developer: 'Interactive Brokers',
    mcp: false,
    verification: 'unverified',
    tools,
    toolGroups: buildToolGroups(tools),
    prompts: ['Import today’s IBKR trades'],
    perms: {},
    ...overrides,
  }
}

function renderDetail(
  e: ConnectorDetailEntry,
  extra: Partial<Parameters<typeof ConnectorDetail>[0]> = {},
) {
  const handlers = {
    onClose: vi.fn(),
    onSetEnabled: vi.fn(),
    onReconnect: vi.fn(),
    onViewDetails: vi.fn(),
    onEditConnection: vi.fn(),
    onRefreshTools: vi.fn(),
    onRemove: vi.fn(),
    onUsePrompt: vi.fn(),
    onSetToolPermission: vi.fn(),
    onSetGroupPermission: vi.fn(),
    onConfigClosed: vi.fn(),
  }
  const { container } = render(<ConnectorDetail entry={e} {...handlers} {...extra} />)
  return { container, ...handlers }
}

/** Stands in for a real provider form — this page only hosts one, it never builds one. */
const FORM = <label htmlFor="fake-key">Client ID</label>

function openKebab(container: HTMLElement) {
  fireEvent.click(within(container).getByRole('button', { name: 'More actions' }))
  const pop = container.querySelector('.rd-kebab-pop')
  if (!pop) throw new Error('kebab did not open')
  return pop as HTMLElement
}

describe('the title is a back control, not a static string', () => {
  it('renders "Connectors" as a button that closes the page', () => {
    const { container, onClose } = renderDetail(entry())
    const back = within(container.querySelector('.rd-modal-title')!).getByRole('button')
    expect(back.textContent).toContain('Connectors')
    fireEvent.click(back)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('the header matches the landing page — one block, not two designs', () => {
  it('uses the shared identity block with the large tile', () => {
    const { container } = renderDetail(entry())
    const head = container.querySelector('.cn-idhead') as HTMLElement
    expect(head.querySelector('.cn-tile.lg')).toBeTruthy()
    expect(head.querySelector('.cn-idhead-name')?.textContent).toContain('IBKR Flex')
  })

  it('sizes the primary action up, and leaves the dense kebab alone (spec §9)', () => {
    const { container } = renderDetail(entry())
    expect(screen.getByRole('button', { name: 'Pause' }).className).toContain('rd-btn danger lg')
    // The compact icon-only action in a header is exactly what §9 excludes from any bump.
    expect(container.querySelector('.rd-kebab-btn')?.className).not.toContain('lg')
  })

  it('sizes Connect, Resume and Reconnect the same — the action changes, the weight does not', () => {
    renderDetail(
      entry({ enabled: false, configured: false, statusText: 'Disconnected', tone: '' }),
      { configForm: FORM },
    )
    expect(screen.getByRole('button', { name: 'Connect' }).className).toContain('lg')
    cleanup()
    renderDetail(entry({ enabled: false }))
    expect(screen.getByRole('button', { name: 'Resume' }).className).toContain('lg')
    cleanup()
    renderDetail(entry({ attention: true }))
    expect(screen.getByRole('button', { name: 'Reconnect' }).className).toContain('lg')
  })

  it('draws the real brand mark when the connector has one, and falls back when not', () => {
    const { container } = renderDetail(entry({ logo: '/connector-logos/ibkr.png' }))
    expect(container.querySelector('.cn-tile-logo')?.getAttribute('src')).toBe(
      '/connector-logos/ibkr.png',
    )
    cleanup()
    const plain = renderDetail(entry())
    expect(plain.container.querySelector('.cn-tile-logo')).toBeNull()
    expect(plain.container.querySelector('.cn-tile svg')).toBeTruthy()
  })
})

describe('identity', () => {
  it('shows name, status badge and the connector sub-line', () => {
    const { container } = renderDetail(entry({ tag: 'Default' }))
    expect(screen.getByText('IBKR Flex')).toBeTruthy()
    expect(container.querySelector('.rd-badge')?.textContent).toBe('Connected')
    expect(screen.getByText('Flex query • nightly import')).toBeTruthy()
    expect(container.querySelector('.cn-tag')?.textContent).toBe('Default')
  })
})

describe('the primary action — the only place a connector can be disabled', () => {
  it('offers Pause when connected, and writes ONLY after confirming', () => {
    const { onSetEnabled, container } = renderDetail(entry({ enabled: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(onSetEnabled).not.toHaveBeenCalled()
    // The confirm dialog is now STACKED on top of the page it was opened from — that page
    // stays mounted underneath, so both Pause buttons are on screen at once. Scope to the
    // confirm dialog's own footer, the second (topmost) `.rd-modal` in the DOM.
    const confirmDialog = container.querySelectorAll<HTMLElement>('.rd-modal')[1]!
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Pause' }))
    expect(onSetEnabled).toHaveBeenCalledWith(false)
  })

  it('Cancel on the Pause confirmation leaves the connector untouched', () => {
    const { onSetEnabled } = renderDetail(entry({ enabled: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onSetEnabled).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy()
  })

  it('offers Resume when disabled but already configured — no confirmation needed', () => {
    const { onSetEnabled } = renderDetail(
      entry({ enabled: false, tone: '', statusText: 'Disconnected' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(onSetEnabled).toHaveBeenCalledWith(true)
  })

  it('replaces it with Reconnect while erroring', () => {
    const { onReconnect } = renderDetail(
      entry({ tone: 'bad', statusText: 'Auth error', attention: true }),
    )
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(onReconnect).toHaveBeenCalledTimes(1)
  })

  // An OAuth reconnect is a popup + poll, not an instant call (RFC `2026-08-14-mcp-oauth`
  // §4.7's "Recovery is one button") — the page has to say so while it waits, rather than
  // sitting there looking clickable with nothing happening.
  it('says Reconnecting… and disables the button while an OAuth sign-in is in flight', () => {
    renderDetail(entry({ tone: 'bad', statusText: 'Auth error', attention: true, mcp: true }), {
      reconnecting: true,
    })
    const button = screen.getByRole('button', { name: 'Reconnecting…' })
    expect(button).toBeTruthy()
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
  })
})

describe('a caller that already said "set this up" lands ON the form', () => {
  const unconfigured = {
    enabled: false,
    configured: false,
    statusText: 'Disconnected',
    tone: '' as const,
  }

  it('opens the config view on the FIRST render — no Connect tap, no flash of the page behind', () => {
    const { container } = renderDetail(entry(unconfigured), {
      configForm: FORM,
      configureOnOpen: true,
    })
    expect(screen.getByText('Client ID')).toBeTruthy()
    // …and it really is the config VIEW: the title is the `‹ <name>` back control, the
    // footer is the lone Done, and the page's own primary action is not on screen.
    const title = within(container.querySelector('.rd-modal-title')!).getByRole('button')
    expect(title.textContent).toContain('IBKR Flex')
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull()
    expect(screen.queryByText('Tool permissions')).toBeNull()
  })

  it('backs out onto the detail page, exactly as if it had arrived there normally', () => {
    const { container, onConfigClosed } = renderDetail(entry(unconfigured), {
      configForm: FORM,
      configureOnOpen: true,
    })
    fireEvent.click(within(container.querySelector('.rd-modal-title')!).getByRole('button'))

    expect(screen.queryByText('Client ID')).toBeNull()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy()
    expect(screen.getByText('Tool permissions')).toBeTruthy()
    // The status behind the form may have changed while it was open, so the page refetches.
    expect(onConfigClosed).toHaveBeenCalledTimes(1)
  })

  it('ignores the intent for a connector that already HOLDS credentials — nothing to set up', () => {
    renderDetail(
      entry({ configured: true, enabled: false, statusText: 'Disconnected', tone: '' }),
      {
        configForm: FORM,
        configureOnOpen: true,
      },
    )
    expect(screen.queryByText('Client ID')).toBeNull()
    expect(screen.getByRole('button', { name: 'Resume' })).toBeTruthy()
  })

  it('ignores it for a connector with no form at all — an `.env`-managed one, or MCP', () => {
    renderDetail(entry(unconfigured), { configureOnOpen: true })
    expect(screen.getByRole('button', { name: 'Resume' })).toBeTruthy()
    expect(screen.getByText('Tool permissions')).toBeTruthy()
  })

  it('stays on the detail page when no caller asks — Connect is still the way in', () => {
    const { onSetEnabled } = renderDetail(entry(unconfigured), { configForm: FORM })
    expect(screen.queryByText('Client ID')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(screen.getByText('Client ID')).toBeTruthy()
    expect(onSetEnabled).not.toHaveBeenCalled()
  })
})

describe('the kebab', () => {
  it('offers View details • Refresh tools list • Remove connector for a native connector', () => {
    const { container } = renderDetail(entry())
    const items = Array.from(openKebab(container).querySelectorAll('.rd-kebab-item')).map(
      (i) => i.textContent,
    )
    expect(items).toEqual(['View details', 'Refresh tools list', 'Remove connector'])
  })

  it('adds Edit configuration for an MCP server only', () => {
    const { container } = renderDetail(entry({ mcp: true, url: 'https://mcp.example.com' }))
    const items = Array.from(openKebab(container).querySelectorAll('.rd-kebab-item')).map(
      (i) => i.textContent,
    )
    expect(items).toEqual([
      'View details',
      'Edit configuration',
      'Refresh tools list',
      'Remove connector',
    ])
  })

  it('wires each action', () => {
    const { container, onRemove } = renderDetail(entry())
    fireEvent.click(within(openKebab(container)).getByText('Remove connector'))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })
})

describe('Remove connector, confirmed — native and MCP get different copy', () => {
  it('confirms before calling onRemove when removable, and cross-references Pause', () => {
    const { container, onRemove } = renderDetail(entry(), { removable: true })
    fireEvent.click(within(openKebab(container)).getByText('Remove connector'))
    expect(onRemove).not.toHaveBeenCalled()

    const body = container.textContent || ''
    expect(body).toContain('This clears your saved credentials and resets its settings')
    expect(body).toContain("you'll need to set it up again from scratch")
    expect(body).toContain('use Pause instead')
    // The MCP-specific tool/permission wording never leaks into the native copy.
    expect(body).not.toContain('Lucius loses')

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it('shows the MCP-specific copy, also cross-referencing Pause, for an MCP server', () => {
    const { container } = renderDetail(entry({ mcp: true, tools: ['a', 'b'] }), {
      removable: true,
    })
    fireEvent.click(within(openKebab(container)).getByText('Remove connector'))

    const body = container.textContent || ''
    expect(body).toContain('Lucius loses the 2 tools it offers')
    expect(body).toContain('use Pause instead')
    expect(body).not.toContain('This clears your saved credentials')
  })

  it('Cancel leaves the connector in place', () => {
    const { container, onRemove } = renderDetail(entry(), { removable: true })
    fireEvent.click(within(openKebab(container)).getByText('Remove connector'))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onRemove).not.toHaveBeenCalled()
    // Back on the plain detail page — its own primary action is showing again.
    expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy()
  })
})

describe('the publisher panel is MCP-only (spec §5)', () => {
  it('is absent for a native connector', () => {
    const { container } = renderDetail(entry())
    expect(screen.queryByText('Publisher')).toBeNull()
    // Always "Developed by Open Net Worth" for a native connector (2026-08-21, simplified
    // same day) — IBKR is a real vendor, but WE wrote this integration; its own name is
    // dropped from this line entirely rather than named alongside ours.
    expect(container.textContent).toContain('Developed by Open Net Worth')
    expect(container.textContent).not.toContain('Interactive Brokers')
  })

  it('carries NO trust prose either, for a native connector (CNR-TRUST-NATIVE-ABSENT)', () => {
    // The panel was already gated on `mcp`; its PROSE half was not, so it fired on exactly the
    // wrong set — every native connector whose publisher is not us. The reported case is
    // Serper, a Web provider we build and operate, reading "Open Net Worth does not control
    // third-party servers". The developer line is the whole answer here.
    const { container } = renderDetail(entry({ id: 'web_search:serper', developer: 'Serper' }))
    expect(container.textContent).toContain('Developed by Open Net Worth')
    expect(container.textContent).not.toContain('Serper')
    expect(container.textContent).not.toContain('does not control third-party servers')
    expect(container.textContent).not.toContain('Only connectors you add are reachable')
  })

  it('says the same for a connector WE publish — the name, and nothing more', () => {
    const { container } = renderDetail(entry({ id: 'telegram', developer: 'Open Net Worth' }))
    expect(container.textContent).toContain('Developed by Open Net Worth')
    expect(container.textContent).not.toContain('third-party')
  })

  it('shows publisher, endpoint verbatim and the third-party note for a known publisher', () => {
    renderDetail(
      entry({
        mcp: true,
        developer: 'Firecrawl',
        verification: 'verified',
        url: 'https://mcp.firecrawl.dev',
      }),
    )
    expect(screen.getByText('Publisher')).toBeTruthy()
    expect(screen.getByText('Firecrawl')).toBeTruthy()
    expect(screen.getByText('Verified publisher')).toBeTruthy()
    // D23 took the panel off the directory preview, NOT off an installed server's detail
    // page: the endpoint is the thing you are trusting, and here you are already running it.
    expect(screen.getByText('https://mcp.firecrawl.dev')).toBeTruthy()
    expect(screen.getByText(/isn’t audited by us/)).toBeTruthy()
  })

  it('claims NO author for a server the user pointed us at', () => {
    renderDetail(
      entry({
        id: 'mcp:custom',
        name: 'Custom MCP server',
        mcp: true,
        developer: undefined,
        verification: 'unknown',
        url: 'https://mcp.local',
      }),
    )
    // the defect: a generic server reading "Developed by Open Net Worth"
    expect(screen.queryByText(/^Developed by/)).toBeNull()
    expect(screen.getByText('Unverified publisher')).toBeTruthy()
    expect(screen.getByText(/can’t vouch for its code/)).toBeTruthy()
  })

  it('marks an unverified known publisher as unverified', () => {
    renderDetail(entry({ mcp: true, developer: 'Community', verification: 'unverified' }))
    expect(screen.getByText('Unverified publisher')).toBeTruthy()
  })
})

describe('prompt suggestions seed the composer', () => {
  it('hands the tapped prompt back', () => {
    const { onUsePrompt } = renderDetail(entry())
    fireEvent.click(screen.getByText('Import today’s IBKR trades'))
    expect(onUsePrompt).toHaveBeenCalledWith('Import today’s IBKR trades')
  })

  it('omits the section when a connector suggests none', () => {
    renderDetail(entry({ prompts: [] }))
    expect(screen.queryByText('Prompt suggestions')).toBeNull()
  })
})

describe('the permission ladder is on EVERY connector, not just MCP', () => {
  it('renders for a native data source', () => {
    renderDetail(entry())
    expect(screen.getByText('Tool permissions')).toBeTruthy()
    expect(screen.getByText('Read-only tools')).toBeTruthy()
    expect(screen.getByText('Write & delete tools')).toBeTruthy()
  })

  it('starts every tool at Needs approval and persists a per-tool override', () => {
    const { onSetToolPermission } = renderDetail(entry())
    const btn = screen.getByRole('button', { name: 'place_order: Always allow' })
    expect(btn.getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(btn)
    expect(onSetToolPermission).toHaveBeenCalledWith('place_order', 'always_allow')
    expect(
      screen
        .getByRole('button', { name: 'place_order: Always allow' })
        .getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('reflects levels already stored on the server', () => {
    renderDetail(entry({ perms: { place_order: 'blocked' } }))
    expect(
      screen.getByRole('button', { name: 'place_order: Blocked' }).getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('explains a connector with no tools instead of showing an empty ladder', () => {
    const { container } = renderDetail(entry({ tools: [], toolGroups: [] }))
    expect(screen.getByText('Tool permissions')).toBeTruthy()
    expect(container.querySelector('.cn-permlist')).toBeNull()
    expect(screen.getByText(/gives Lucius no tools yet/)).toBeTruthy()
  })
})

describe('the Automate action', () => {
  it("opens a connected connector's template from the page header", () => {
    renderDetail(entry({ id: 'email', name: 'Email', kind: 'email' }))
    fireEvent.click(screen.getByRole('button', { name: 'Automate' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/automations',
      search: { template: 'mail_ledger' },
    })
  })

  it('maps a bank or broker to its sync template', () => {
    renderDetail(entry({ id: 'plaid', name: 'Plaid', kind: 'plaid' }))
    fireEvent.click(screen.getByRole('button', { name: 'Automate' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/automations',
      search: { template: 'sync_plaid' },
    })
  })

  it('offers no Automate for retired IBKR Flex — its sync template is not offered', () => {
    renderDetail(entry())
    expect(screen.queryByRole('button', { name: 'Automate' })).toBeNull()
  })

  it('is not offered for a connector with nothing to automate, or one that is paused', () => {
    renderDetail(entry({ id: 'telegram', name: 'Telegram', kind: 'telegram' }))
    expect(screen.queryByRole('button', { name: 'Automate' })).toBeNull()
    cleanup()
    renderDetail(entry({ enabled: false }))
    expect(screen.queryByRole('button', { name: 'Automate' })).toBeNull()
  })
})

describe('on a phone a running connector keeps Pause in the ⋮; only a fix reaches the footer', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows Done alone in the footer and Pause in the ⋮, in a compact header', () => {
    const { container, onSetEnabled } = renderDetail(entry({ enabled: true }))
    const head = container.querySelector('.cn-idhead') as HTMLElement
    expect(head.className).toContain('cn-idhead-compact')
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()

    const foot = container.querySelector('.rd-modal-foot') as HTMLElement
    expect(within(foot).getAllByRole('button')).toHaveLength(1)
    expect(within(foot).getByRole('button', { name: 'Done' }).className).toContain('primary')

    openKebab(container)
    fireEvent.click(screen.getByText('Pause connector'))
    const confirmDialog = container.querySelectorAll<HTMLElement>('.rd-modal')[1]!
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Pause' }))
    expect(onSetEnabled).toHaveBeenCalledWith(false)
  })

  it('promotes Resume to the footer, primary and last, beside a secondary Done', () => {
    const { container, onSetEnabled } = renderDetail(entry({ enabled: false }))
    const foot = container.querySelector('.rd-modal-foot') as HTMLElement
    const buttons = within(foot).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['Done', 'Resume'])
    expect(buttons[0]!.className).toContain('secondary')
    fireEvent.click(buttons[1]!)
    expect(onSetEnabled).toHaveBeenCalledWith(true)
  })
})
