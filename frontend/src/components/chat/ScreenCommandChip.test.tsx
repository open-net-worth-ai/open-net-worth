// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

const navigateMock = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))

import { _resetBusForTests, onScreenCommand } from '../../lucius/bus'
import { ScreenCommandChip } from './ScreenCommandChip'

beforeEach(() => {
  _resetBusForTests()
  navigateMock.mockClear()
})

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
    args: { id: 'mcp-webull', reason: 'Track your\u202e brokerage\n account' },
  }
  const { container } = render(<ScreenCommandChip command={command} route="connectors" />)
  expect(screen.getByText('Webull')).toBeTruthy()
  expect(screen.getByText('Verified')).toBeTruthy()
  expect(screen.getByText('By Webull')).toBeTruthy()
  // No reason line — Lucius's own message beside the card already says why.
  expect(container.querySelector('.cmd-setup-reason')).toBeNull()
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
