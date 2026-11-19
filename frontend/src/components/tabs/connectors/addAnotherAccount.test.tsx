// @vitest-environment jsdom

// A second copy of an MCP server (two IBKR logins, two Stripe keys) is a second server at the same
// URL. It starts from the ⋮ menu on the first one's settings page, opens the add form at that
// address with its name numbered, and warns that the vendor's sign-in may land on the first account
// again (owner, 2026-11-19 / 2026-11-20).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import { DirectoryModal } from './DirectoryModal'
import { sameAddressAs } from './directoryRows'
import { MCP_INSTALL_PRESETS } from './catalog'
import type { Connector } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
afterEach(cleanup)

const IBKR_URL = MCP_INSTALL_PRESETS['mcp-ibkr'].url!

function server(
  id: string,
  url: string,
  name = 'Interactive Brokers',
  tools: Connector['tools'] = [{ name: 'get_positions', description: '', source: 'mcp' }],
): Connector {
  return {
    id: `mcp:${id}`,
    name,
    kind: 'mcp',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { transport: 'http', url, auth_type: url === IBKR_URL ? 'oauth' : 'key' },
    secrets_public: {},
    tools,
  }
}

function openAnother(connectors: Connector[], anotherId: string) {
  return render(
    <DirectoryModal
      open
      onClose={() => {}}
      connectors={connectors}
      onFocusConnector={() => {}}
      anotherId={anotherId}
    />,
  )
}

describe('adding another account of a sign-in server', () => {
  it('numbers the name after every server already at that URL', () => {
    const one = [{ name: 'Interactive Brokers', url: IBKR_URL }]
    expect(sameAddressAs(IBKR_URL, one)).toEqual({
      of: 'Interactive Brokers',
      name: 'Interactive Brokers 2',
      signIn: false,
    })
    const two = [...one, { name: 'IBKR joint', url: IBKR_URL }]
    expect(sameAddressAs(IBKR_URL, two)?.name).toBe('Interactive Brokers 3')
    expect(sameAddressAs('https://mcp.acme.test/mcp', two)).toBeNull()
  })

  it('opens the add form at the same address, with a warning', () => {
    openAnother([server('api-ibkr-com', IBKR_URL)], 'mcp:api-ibkr-com')
    const note = screen.getByRole('note').textContent
    expect(note).toMatch(/You already added Interactive Brokers at this address/)
    expect(note).toMatch(/Sign out of Interactive Brokers in your browser first/)
    expect(screen.getByDisplayValue(IBKR_URL)).toBeTruthy()
  })

  it('goes Back to the settings page it was opened from', () => {
    const onFocusConnector = vi.fn()
    render(
      <DirectoryModal
        open
        onClose={() => {}}
        connectors={[server('api-ibkr-com', IBKR_URL)]}
        onFocusConnector={onFocusConnector}
        anotherId="mcp:api-ibkr-com"
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Back$/ }))
    expect(onFocusConnector).toHaveBeenCalledWith('mcp:api-ibkr-com')
  })

  it('works for a server that takes a key, without the sign-out warning', () => {
    const stripe = MCP_INSTALL_PRESETS['mcp-stripe'].url!
    openAnother([server('mcp-stripe-com', stripe, 'Stripe')], 'mcp:mcp-stripe-com')
    expect(screen.getByDisplayValue(stripe)).toBeTruthy()
    const note = screen.getByRole('note').textContent
    expect(note).toMatch(/You already added Stripe at this address/)
    expect(note).not.toMatch(/Sign out/)
  })

  it('works for a custom server that is not in the directory', () => {
    const url = 'https://mcp.acme-broker.test/v1/mcp'
    openAnother([server('acme', url, 'ACME Broker')], 'mcp:acme')
    expect(screen.getByDisplayValue(url)).toBeTruthy()
    expect(screen.getByRole('note').textContent).toMatch(/You already added ACME Broker/)
  })

  it('is no longer a second button on the directory card', () => {
    render(
      <DirectoryModal
        open
        onClose={() => {}}
        connectors={[server('api-ibkr-com', IBKR_URL)]}
        onFocusConnector={() => {}}
        previewId="mcp-ibkr"
      />,
    )
    expect(screen.getByRole('button', { name: 'Open settings' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add another' })).toBeNull()
  })

  it('warns when Add MCP server is pointed at an address already added', () => {
    render(
      <DirectoryModal
        open
        onClose={() => {}}
        connectors={[server('api-ibkr-com', IBKR_URL)]}
        onFocusConnector={() => {}}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add MCP server' }))
    expect(screen.queryByRole('note')).toBeNull()
    fireEvent.change(screen.getByPlaceholderText('https://mcp.example.com'), {
      target: { value: IBKR_URL },
    })
    // The form still says None, but the server already added signs in — so will this one.
    const note = screen.getByRole('note').textContent
    expect(note).toMatch(/You already added Interactive Brokers at this address/)
    expect(note).toMatch(/its own sign-in/)
    expect(note).toMatch(/Sign out of Interactive Brokers in your browser first/)
  })
})
