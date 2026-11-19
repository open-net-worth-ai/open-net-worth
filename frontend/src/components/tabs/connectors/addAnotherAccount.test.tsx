// @vitest-environment jsdom

// A second copy of a directory MCP server (two IBKR logins, two Stripe keys) is a second server at the same
// URL: the directory offers it on the already-added card, numbers its name, and warns that the
// vendor's sign-in may land on the first account again (owner, 2026-11-19).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import { DirectoryModal } from './DirectoryModal'
import { sameAddressAs } from './directoryRows'
import { MCP_INSTALL_PRESETS } from './catalog'
import type { Connector } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
afterEach(cleanup)

const IBKR_URL = MCP_INSTALL_PRESETS['mcp-ibkr']!.url!

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

function openPreview(connectors: Connector[], previewId: string) {
  return render(
    <DirectoryModal
      open
      onClose={() => {}}
      connectors={connectors}
      onFocusConnector={() => {}}
      previewId={previewId}
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

  it('offers Add another on the added card and opens the add form with a warning', () => {
    openPreview([server('api-ibkr-com', IBKR_URL)], 'mcp-ibkr')
    expect(screen.getByRole('button', { name: 'Open settings' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add another' }))
    const note = screen.getByRole('note').textContent
    expect(note).toMatch(/You already added Interactive Brokers at this address/)
    expect(note).toMatch(/Sign out of Interactive Brokers in your browser first/)
    expect(screen.getByDisplayValue(IBKR_URL)).toBeTruthy()
  })

  it('does not offer it before the first account is added', () => {
    openPreview([], 'mcp-ibkr')
    expect(screen.queryByRole('button', { name: 'Add another' })).toBeNull()
  })

  it('offers it for a server that takes a key too, without the sign-out warning', () => {
    const stripe = MCP_INSTALL_PRESETS['mcp-stripe']!.url!
    openPreview([server('mcp-stripe-com', stripe, 'Stripe')], 'mcp-stripe')
    fireEvent.click(screen.getByRole('button', { name: 'Add another' }))
    expect(screen.getByDisplayValue(stripe)).toBeTruthy()
    const note = screen.getByRole('note').textContent
    expect(note).toMatch(/You already added Stripe at this address/)
    expect(note).not.toMatch(/Sign out/)
  })

  it('does not offer it while the one already added has never connected', () => {
    const halfAdded = { ...server('api-ibkr-com', IBKR_URL, 'api.ibkr.com', []), enabled: false }
    openPreview([halfAdded], 'mcp-ibkr')
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
