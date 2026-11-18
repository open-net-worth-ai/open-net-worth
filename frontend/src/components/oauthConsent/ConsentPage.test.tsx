// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConsentPage } from './ConsentPage'

const DETAILS = {
  app_name: 'Claude',
  app_host: 'claude.ai',
  redirect_host: 'claude.ai',
  requested_scopes: ['read', 'write'],
  expires_at: '2026-11-16T12:10:00.000000Z',
  signed_in: true,
}

let fetchMock: ReturnType<typeof vi.fn>
let assign: ReturnType<typeof vi.fn>

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }))
}

beforeEach(() => {
  window.history.replaceState({}, '', '/oauth/consent?request=req-abc')
  fetchMock = vi.fn((_url: string, init?: RequestInit) => {
    if (!init || init.method !== 'POST') return json(200, DETAILS)
    return json(200, { redirect: 'https://claude.ai/api/mcp/auth_callback?code=x' })
  })
  vi.stubGlobal('fetch', fetchMock)
  assign = vi.fn()
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, assign, search: '?request=req-abc' },
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function consentPost(): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(
    ([url, init]) => url === '/api/oauth/consent' && (init as RequestInit)?.method === 'POST',
  )
  return JSON.parse(String((call![1] as RequestInit).body)) as Record<string, unknown>
}

it('names the app by the server label and its host, with a warning about forwarded links', async () => {
  render(<ConsentPage />)
  expect(await screen.findByText('Connect Claude to Open Net Worth?')).toBeTruthy()
  expect(screen.getByText('claude.ai')).toBeTruthy()
  expect(screen.getByText(/If someone sent you this link, choose Deny/)).toBeTruthy()
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/oauth/consent?request=req-abc')
})

it('asks to sign in first when this browser has no recent sign-in', async () => {
  let signedIn = false
  fetchMock.mockImplementation((url: string) => {
    if (url === '/api/oauth/consent/sign-in') {
      signedIn = true
      return json(200, { ok: true })
    }
    return json(200, { ...DETAILS, signed_in: signedIn })
  })
  render(<ConsentPage />)
  expect(await screen.findByText('Sign in to connect Claude')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Allow read access' })).toBeNull()
  fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('Connect Claude to Open Net Worth?')).toBeTruthy()
  const login = fetchMock.mock.calls.find(([url]) => url === '/api/oauth/consent/sign-in')!
  expect(JSON.parse(String((login[1] as RequestInit).body))).toEqual({
    request: 'req-abc',
    password: 'pw',
  })
})

it('says so when the sign-in password is wrong', async () => {
  fetchMock.mockImplementation((url: string) =>
    url === '/api/oauth/consent/sign-in'
      ? json(401, { detail: 'invalid password' })
      : json(200, { ...DETAILS, signed_in: false }),
  )
  render(<ConsentPage />)
  await screen.findByText('Sign in to connect Claude')
  fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: 'x' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('That password is not right.')).toBeTruthy()
})

it('allows read-only by default with no password on the consent card', async () => {
  render(<ConsentPage />)
  await screen.findByText('Connect Claude to Open Net Worth?')
  expect(document.querySelector('input[type="password"]')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Allow read access' }))
  await waitFor(() =>
    expect(assign).toHaveBeenCalledWith('https://claude.ai/api/mcp/auth_callback?code=x'),
  )
  expect(consentPost()).toEqual({ request: 'req-abc', decision: 'allow', allow_write: false })
})

it('grants changes only when the switch is on', async () => {
  render(<ConsentPage />)
  fireEvent.click(await screen.findByLabelText(/Also let it make changes/))
  fireEvent.click(screen.getByRole('button', { name: 'Allow read and write access' }))
  await waitFor(() => expect(assign).toHaveBeenCalled())
  expect(consentPost().allow_write).toBe(true)
})

it('offers no changes switch when the app only asked to read', async () => {
  fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
    !init || init.method !== 'POST'
      ? json(200, { ...DETAILS, requested_scopes: ['read'] })
      : json(200, {}),
  )
  render(<ConsentPage />)
  await screen.findByText('Connect Claude to Open Net Worth?')
  expect(screen.queryByLabelText(/Also let it make changes/)).toBeNull()
})

it('goes back to sign-in when the sign-in has gone stale', async () => {
  fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
    !init || init.method !== 'POST' ? json(200, DETAILS) : json(401, { detail: 'sign in again' }),
  )
  render(<ConsentPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Allow read access' }))
  expect(await screen.findByText('Sign in to connect Claude')).toBeTruthy()
  expect(assign).not.toHaveBeenCalled()
})

it('denies without signing in again', async () => {
  render(<ConsentPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Deny' }))
  await waitFor(() => expect(assign).toHaveBeenCalled())
  expect(consentPost()).toEqual({ request: 'req-abc', decision: 'deny' })
})

it('explains an expired request', async () => {
  fetchMock.mockImplementation(() => json(404, { detail: 'expired' }))
  render(<ConsentPage />)
  expect(await screen.findByText('This request no longer works')).toBeTruthy()
})

it('says to try again when the approve call gets no normal answer, and lets you retry', async () => {
  let calls = 0
  fetchMock.mockImplementation((_url: string, init?: RequestInit) => {
    if (!init || init.method !== 'POST') return json(200, DETAILS)
    calls += 1
    if (calls === 1) return Promise.reject(new TypeError('Failed to fetch'))
    return json(200, { redirect: 'https://claude.ai/api/mcp/auth_callback?code=x' })
  })
  render(<ConsentPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Allow read access' }))
  expect(await screen.findByText("Couldn't reach Open Net Worth. Try again.")).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Allow read access' }))
  await waitFor(() => expect(assign).toHaveBeenCalled())
})
