// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('./TerminalView', () => ({
  TerminalView: ({ onEnded }: { onEnded: (r: 'ended') => void }) => (
    <button onClick={() => onEnded('ended')}>fake terminal</button>
  ),
}))
const startGatewayReauth = vi.hoisted(() => vi.fn())
vi.mock('../../../../utils/gatewayReauth', async (orig) => ({
  ...(await orig<typeof import('../../../../utils/gatewayReauth')>()),
  startGatewayReauth,
}))

import { authFetch } from '../../../../api/client'
import { TerminalSection } from './TerminalSection'

const mockAuthFetch = vi.mocked(authFetch)

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(cleanup)

describe('TerminalSection', () => {
  it('shows the ownership notice and opens a terminal only when asked', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: true, reason: null, in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} gatewayMode />)

    expect(screen.getByText(/your own agreement with their provider/i)).toBeTruthy()
    const open = await screen.findByRole('button', { name: 'Open terminal' })
    expect(screen.queryByText('fake terminal')).toBeNull()
    fireEvent.click(open)
    expect(await screen.findByText('fake terminal')).toBeTruthy()
    fireEvent.click(screen.getByText('fake terminal'))
    expect(await screen.findByRole('button', { name: 'Open terminal' })).toBeTruthy()
  })

  it('explains when the terminal is switched off', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: false, reason: 'disabled', in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} />)
    expect(await screen.findByText(/switched off on this server/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open terminal' })).toBeNull()
  })

  it('asks for a code by email in the terminal screen and opens once it is confirmed', async () => {
    let fresh = false
    mockAuthFetch.mockImplementation(async () =>
      fresh
        ? json({ available: true, reason: null, in_use: false })
        : json({ code: 'fresh_session_required', reauth: 'https://cloud.example/r' }, 401),
    )
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/_gateway/confirm/send') return json({ sent_to: 'me@example.com' })
      expect(JSON.parse(String(init?.body))).toEqual({ code: '123456' })
      fresh = true
      return json({ ok: true })
    })
    vi.stubGlobal('fetch', fetchMock)
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))
      expect(await screen.findByText('Enter the code we sent to me@example.com.')).toBeTruthy()
      fireEvent.change(screen.getByLabelText('Code from the email'), {
        target: { value: '123456' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
      expect(await screen.findByRole('button', { name: 'Open terminal' })).toBeTruthy()
      expect(startGatewayReauth).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('shows a wrong code and lets them try again', async () => {
    mockAuthFetch.mockImplementation(async () => json({ code: 'fresh_session_required' }, 401))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path === '/_gateway/confirm/send'
          ? json({ sent_to: 'me@example.com' })
          : json({ error: "That code didn't work. Check it, or send a new one." }, 400),
      ),
    )
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))
      fireEvent.change(await screen.findByLabelText('Code from the email'), {
        target: { value: '000000' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
      expect(await screen.findByText(/didn't work/)).toBeTruthy()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('two-step accounts finish through the desktop app, not a navigation', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ code: 'fresh_session_required', reauth: 'https://cloud.example/r' }, 401),
    )
    window.history.replaceState(null, '', '/settings/terminal')
    const requestFreshSignIn = vi.fn().mockResolvedValue(undefined)
    window.onwDesktopClient = { version: 1, requestFreshSignIn } as never
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ fallback: '/_gateway/refresh?return=x' }, 409)),
    )
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))
      await waitFor(() => expect(requestFreshSignIn).toHaveBeenCalledWith('/settings/terminal'))
      expect(startGatewayReauth).not.toHaveBeenCalled()
    } finally {
      delete window.onwDesktopClient
      vi.unstubAllGlobals()
    }
  })

  it('says so when the desktop app cannot start the sign-in', async () => {
    mockAuthFetch.mockImplementation(async () => json({ code: 'fresh_session_required' }, 401))
    window.onwDesktopClient = {
      version: 1,
      requestFreshSignIn: vi.fn().mockRejectedValue(new Error('no')),
    } as never
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ fallback: '/_gateway/refresh?return=x' }, 409)),
    )
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))
      expect(await screen.findByText(/could not start the sign-in/i)).toBeTruthy()
    } finally {
      delete window.onwDesktopClient
      vi.unstubAllGlobals()
    }
  })
})
