// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const navigateMock = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
const installed = vi.hoisted(() => ({ rows: [] as unknown[] }))
vi.mock('../../api/client', () => ({
  authFetch: vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ connectors: installed.rows }) }),
  ),
}))

import { _resetBusForTests, onScreenCommand } from '../../lucius/bus'
import { clearQueryCache } from '../../state/queryCache'
import { MCP_INSTALL_PRESETS } from '../tabs/connectors/catalog'
import { ScreenCommandChip } from './ScreenCommandChip'

beforeEach(() => {
  _resetBusForTests()
  clearQueryCache()
  installed.rows = []
  navigateMock.mockClear()
})
afterEach(cleanup)

/** Webull's hosted server as the user already has it — merged into its catalog card by URL. */
function webullServer(oauth: { connected: boolean; needs_reauth: boolean }) {
  return {
    id: 'mcp:acme-webull',
    name: 'Webull',
    kind: 'mcp',
    enabled: true,
    config: {
      transport: 'remote',
      url: MCP_INSTALL_PRESETS['mcp-webull']!.url,
      auth_type: 'oauth',
      oauth,
      last_status: oauth.needs_reauth ? 'auth_error' : 'ok',
    },
    secrets_public: {},
    status: oauth.needs_reauth ? 'error' : 'active',
    error: oauth.needs_reauth ? 'Connection has expired.' : null,
    pausable: true,
    short_description: '',
    tools: [],
  }
}

it('renders the mapped label and, on click, re-dispatches the command and navigates', () => {
  const seen = vi.fn()
  onScreenCommand('plan.', seen)
  render(
    <ScreenCommandChip
      command={{ name: 'plan.stage_whatif', args: { retireAge: 45 } }}
      route="plan-outlook"
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /view on outlook/i }))
  expect(seen).toHaveBeenCalledWith({ name: 'plan.stage_whatif', args: { retireAge: 45 } })
  expect(navigateMock).toHaveBeenCalledOnce()
  // Must be the canonical /plan slug — the legacy /transactions alias redirects
  // WITHOUT forwarding search, which silently drops the ?sub=outlook deep-link.
  const navArg = navigateMock.mock.calls[0]?.[0] as { to?: string; search?: unknown }
  expect(navArg?.to).toBe('/plan')
  const searchFn = navArg?.search as (prev: Record<string, unknown>) => Record<string, unknown>
  expect(searchFn({})).toEqual({ sub: 'outlook' })
})

it('renders nothing for an unknown route key', () => {
  const { container } = render(
    <ScreenCommandChip command={{ name: 'x.y', args: {} }} route="nope" />,
  )
  expect(container.firstChild).toBeNull()
})

it('routes a parked Automation draft to the shared review editor screen', () => {
  render(
    <ScreenCommandChip
      command={{ name: 'automations.review_draft', args: { automation: {} } }}
      route="automations"
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /review automation/i }))
  expect(navigateMock.mock.calls.at(-1)?.[0]).toEqual(
    expect.objectContaining({ to: '/automations' }),
  )
})

// Owner, 2026-10-31: a suggested connector gets a card naming it — logo, name, publisher,
// verification — not a bare "Set up connector" chip. A sign-in connector
// connects in one click, like Claude's connector cards.
it('shows a sign-in connector as a card from OUR catalog; Connect opens the sign-in window', () => {
  const seen = vi.fn()
  onScreenCommand('connectors.', seen)
  const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
  const command = {
    name: 'connectors.open_setup',
    args: { id: 'mcp-webull' },
  }
  render(<ScreenCommandChip command={command} route="connectors" />)
  expect(screen.getByText('Webull')).toBeTruthy()
  expect(screen.getByText('Verified')).toBeTruthy()
  expect(screen.getByText('By Webull')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /connect/i }))
  // The window opens ON the click, so the sign-in a moment later isn't popup-blocked.
  expect(openSpy).toHaveBeenCalledWith('about:blank', 'onw-mcp-oauth', expect.any(String))
  expect(seen).toHaveBeenCalledWith({ ...command, args: { ...command.args, connect: true } })
  expect(navigateMock.mock.calls.at(-1)?.[0]).toEqual(
    expect.objectContaining({ to: '/connectors' }),
  )
  openSpy.mockRestore()
})

it('a key or local-command connector says Set up and opens its form, no sign-in window', () => {
  const seen = vi.fn()
  onScreenCommand('connectors.', seen)
  const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
  const command = { name: 'connectors.open_setup', args: { id: 'mcp-bybit' } }
  render(<ScreenCommandChip command={command} route="connectors" />)
  fireEvent.click(screen.getByRole('button', { name: /set up/i }))
  expect(openSpy).not.toHaveBeenCalled()
  expect(seen).toHaveBeenCalledWith(command)
  openSpy.mockRestore()
})

it('shows the custom-server card for id custom, and the plain chip for an id it cannot name', () => {
  const { unmount } = render(
    <ScreenCommandChip
      command={{ name: 'connectors.open_setup', args: { id: 'custom' } }}
      route="connectors"
    />,
  )
  expect(screen.getByText('Custom MCP server')).toBeTruthy()
  unmount()
  render(
    <ScreenCommandChip
      command={{ name: 'connectors.open_setup', args: { id: 'zzz' } }}
      route="connectors"
    />,
  )
  expect(screen.getByRole('button', { name: /set up connector/i })).toBeTruthy()
})

// Owner, 2026-11-13: an installed IBKR whose sign-in had expired got a card reading "Connect",
// as if it had never been set up. The card reads the connector's live state.
it('says Reconnect for an installed server whose sign-in expired, and goes on to the sign-in', async () => {
  installed.rows = [webullServer({ connected: true, needs_reauth: true })]
  const seen = vi.fn()
  onScreenCommand('connectors.', seen)
  const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
  const command = { name: 'connectors.open_setup', args: { id: 'mcp-webull' } }
  render(<ScreenCommandChip command={command} route="connectors" />)
  fireEvent.click(await screen.findByRole('button', { name: /^reconnect/i }))
  expect(screen.getByText('By Webull · Authentication issue')).toBeTruthy()
  expect(openSpy).toHaveBeenCalledWith('about:blank', 'onw-mcp-oauth', expect.any(String))
  expect(seen).toHaveBeenCalledWith({ ...command, args: { ...command.args, connect: true } })
  openSpy.mockRestore()
})

it('says Open for an installed server that works, and opens no sign-in window', async () => {
  installed.rows = [webullServer({ connected: true, needs_reauth: false })]
  const seen = vi.fn()
  onScreenCommand('connectors.', seen)
  const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
  // Lucius may name the installed id itself (get_connector_status's `fix`).
  const command = { name: 'connectors.open_setup', args: { id: 'mcp:acme-webull' } }
  render(<ScreenCommandChip command={command} route="connectors" />)
  fireEvent.click(await screen.findByRole('button', { name: /^open/i }))
  expect(screen.getByText('Webull')).toBeTruthy()
  expect(openSpy).not.toHaveBeenCalled()
  expect(seen).toHaveBeenCalledWith(command)
  openSpy.mockRestore()
})
