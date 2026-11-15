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

function posted(): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  return JSON.parse(String((call![1] as RequestInit).body)) as Record<string, unknown>
}

it('names the app by the server label and its host, with a warning about forwarded links', async () => {
  render(<ConsentPage />)
  expect(await screen.findByText('Connect Claude to your book?')).toBeTruthy()
  expect(screen.getByText(/Claude \(claude\.ai\) wants to read/)).toBeTruthy()
  expect(screen.getByText(/If someone sent you this link, choose Deny/)).toBeTruthy()
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/oauth/consent?request=req-abc')
})

it('allows read-only by default and needs the password typed on the page', async () => {
  render(<ConsentPage />)
  const allow = await screen.findByRole('button', { name: 'Allow read-only access' })
  expect((allow as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Allow read-only access' }))
  await waitFor(() =>
    expect(assign).toHaveBeenCalledWith('https://claude.ai/api/mcp/auth_callback?code=x'),
  )
  expect(posted()).toEqual({
    request: 'req-abc',
    decision: 'allow',
    password: 'pw',
    allow_write: false,
  })
})

it('grants changes only when the box is ticked', async () => {
  render(<ConsentPage />)
  fireEvent.click(await screen.findByLabelText(/Also let it make changes/))
  fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Allow reading and changes' }))
  await waitFor(() => expect(assign).toHaveBeenCalled())
  expect(posted().allow_write).toBe(true)
})

it('offers no changes box when the app only asked to read', async () => {
  fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
    !init || init.method !== 'POST'
      ? json(200, { ...DETAILS, requested_scopes: ['read'] })
      : json(200, {}),
  )
  render(<ConsentPage />)
  await screen.findByText('Connect Claude to your book?')
  expect(screen.queryByLabelText(/Also let it make changes/)).toBeNull()
})

it('says so when the password is wrong and stays on the page', async () => {
  fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
    !init || init.method !== 'POST'
      ? json(200, DETAILS)
      : json(401, { detail: 'invalid password' }),
  )
  render(<ConsentPage />)
  fireEvent.change(await screen.findByDisplayValue(''), { target: { value: 'bad' } })
  fireEvent.click(screen.getByRole('button', { name: 'Allow read-only access' }))
  expect(await screen.findByText('That password is not right.')).toBeTruthy()
  expect(assign).not.toHaveBeenCalled()
})

it('denies without a password', async () => {
  render(<ConsentPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Deny' }))
  await waitFor(() => expect(assign).toHaveBeenCalled())
  expect(posted()).toEqual({ request: 'req-abc', decision: 'deny' })
})

it('explains an expired request', async () => {
  fetchMock.mockImplementation(() => json(404, { detail: 'expired' }))
  render(<ConsentPage />)
  expect(await screen.findByText('This request no longer works')).toBeTruthy()
})
