// @vitest-environment jsdom

// A second copy of a directory MCP server (two IBKR logins, two Stripe keys) is a second server at the same
// URL: the directory offers it on the already-added card, numbers its name, and warns that the
// vendor's sign-in may land on the first account again (owner, 2026-11-19).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import { DirectoryModal } from './DirectoryModal'
import { anotherAccountName, resolveDirectory } from './directoryRows'
import { MCP_INSTALL_PRESETS } from './catalog'
import type { Connector } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
afterEach(cleanup)

const IBKR_URL = MCP_INSTALL_PRESETS['mcp-ibkr'].url!

function server(id: string, url: string, name = 'Interactive Brokers'): Connector {
  return {
    id: `mcp:${id}`,
    name,
    kind: 'mcp',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { transport: 'http', url },
    secrets_public: {},
    tools: [{ name: 'get_positions', description: '', source: 'mcp' }],
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
    const one = [server('api-ibkr-com', IBKR_URL)]
    const ibkr = resolveDirectory(one).find((r) => r.id === 'mcp-ibkr')!
    expect(anotherAccountName(one, ibkr)).toBe('Interactive Brokers 2')
    const two = [...one, server('api-ibkr-com-2', IBKR_URL, 'IBKR joint')]
    expect(anotherAccountName(two, ibkr)).toBe('Interactive Brokers 3')
  })

  it('offers Add another on the added card and opens the add form with a warning', () => {
    openPreview([server('api-ibkr-com', IBKR_URL)], 'mcp-ibkr')
    expect(screen.getByRole('button', { name: 'Open settings' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add another' }))
    expect(screen.getByRole('note').textContent).toMatch(/Sign out of this service/)
    expect(screen.getByDisplayValue(IBKR_URL)).toBeTruthy()
  })

  it('does not offer it before the first account is added', () => {
    openPreview([], 'mcp-ibkr')
    expect(screen.queryByRole('button', { name: 'Add another' })).toBeNull()
  })

  it('offers it for a server that takes a key too, without the sign-out warning', () => {
    const stripe = MCP_INSTALL_PRESETS['mcp-stripe'].url!
    openPreview([server('mcp-stripe-com', stripe, 'Stripe')], 'mcp-stripe')
    fireEvent.click(screen.getByRole('button', { name: 'Add another' }))
    expect(screen.getByDisplayValue(stripe)).toBeTruthy()
    expect(screen.queryByRole('note')).toBeNull()
  })
})
