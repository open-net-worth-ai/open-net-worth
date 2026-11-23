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
  it('shows the notice and the sign-in commands, and opens the terminal by itself', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: true, reason: null, in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} gatewayMode />)

    expect(screen.getByText(/your own agreement with their provider/i)).toBeTruthy()
    expect(screen.getByText('codex login --device-auth')).toBeTruthy()
    expect(await screen.findByText('fake terminal')).toBeTruthy() // no Open terminal click
    expect(screen.queryByRole('button', { name: 'Open terminal' })).toBeNull()
    fireEvent.click(screen.getByText('fake terminal')) // the session ends: a new one, same box
    expect(await screen.findByText('fake terminal')).toBeTruthy()
  })

  it('explains when the terminal is switched off', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: false, reason: 'disabled', in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} />)
    expect(await screen.findByText(/switched off on this server/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open terminal' })).toBeNull()
  })

  it('asks for a code in the terminal box and opens the terminal as soon as the 6th digit is in', async () => {
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
      expect(await screen.findByText(/Sent to me@example.com/)).toBeTruthy()
      fireEvent.change(screen.getByLabelText('Digit 1'), {
        target: { value: '123456' },
      })
      expect(await screen.findByText('fake terminal')).toBeTruthy() // opens right away
      expect(startGatewayReauth).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('a wrong code says so and clears the box for another try', async () => {
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
      fireEvent.change(await screen.findByLabelText('Digit 1'), {
        target: { value: '000000' },
      })
      expect(await screen.findByText(/didn't work/)).toBeTruthy()
      expect((screen.getByLabelText('Digit 1') as HTMLInputElement).value).toBe('')
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

  it('waits 30 seconds before another code, and moves box to box as you type', async () => {
    mockAuthFetch.mockImplementation(async () => json({ code: 'fresh_session_required' }, 401))
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ sent_to: 'me@example.com' })),
    )
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))
      const resend = await screen.findByRole('button', { name: /Send a new code in 30s/ })
      expect((resend as HTMLButtonElement).disabled).toBe(true)
      fireEvent.change(screen.getByLabelText('Digit 1'), { target: { value: '4' } })
      fireEvent.change(screen.getByLabelText('Digit 2'), { target: { value: '8' } })
      expect((screen.getByLabelText('Digit 1') as HTMLInputElement).value).toBe('4')
      expect((screen.getByLabelText('Digit 2') as HTMLInputElement).value).toBe('8')
      expect((screen.getByLabelText('Digit 3') as HTMLInputElement).value).toBe('')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
