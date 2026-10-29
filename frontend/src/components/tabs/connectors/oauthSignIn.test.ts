// @vitest-environment jsdom
/**
 * The sign-in controller when the browser opens the popup but hands back no handle to it.
 *
 * Arc and Dia show `window.open` popups in their own mini-window and return `null`. This used to
 * fail as "Your browser blocked the sign-in window" and stop polling — the user then finished
 * the vendor login, the callback persisted the server, and the add screen never noticed.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const mockBegin = vi.fn()
const mockPoll = vi.fn()
vi.mock('./mcpApi', () => ({
  beginOAuth: (...args: unknown[]) => mockBegin(...args),
  pollOAuth: (...args: unknown[]) => mockPoll(...args),
}))

import { startOAuthSignIn } from './oauthSignIn'

const AUTHORIZE_URL = 'https://auth.example.test/authorize?state=flow-1'

beforeEach(() => {
  vi.useFakeTimers()
  mockBegin.mockReset().mockResolvedValue({
    ok: true,
    flow_id: 'flow-1',
    authorize_url: AUTHORIZE_URL,
  })
  mockPoll.mockReset().mockResolvedValue({ status: 'pending', error: null })
  vi.spyOn(window, 'open').mockReturnValue(null)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('startOAuthSignIn with no popup handle', () => {
  it('keeps polling and finishes when the sign-in completes', async () => {
    const onOutcome = vi.fn()
    const onAuthorizeUrl = vi.fn()
    startOAuthSignIn({ url: 'https://mcp.example.test' }, onOutcome, onAuthorizeUrl)

    await vi.advanceTimersByTimeAsync(0)
    expect(onOutcome).not.toHaveBeenCalled()
    expect(onAuthorizeUrl).toHaveBeenCalledWith(AUTHORIZE_URL, false)

    mockPoll.mockResolvedValue({ status: 'ok', error: null })
    await vi.advanceTimersByTimeAsync(1500)
    expect(onOutcome).toHaveBeenCalledTimes(1)
    expect(onOutcome.mock.calls[0][0]).toMatchObject({ ok: true, error: null })
  })

  it('ends with the backend error once the staged flow expires', async () => {
    const onOutcome = vi.fn()
    startOAuthSignIn({ url: 'https://mcp.example.test' }, onOutcome)
    await vi.advanceTimersByTimeAsync(0)

    mockPoll.mockResolvedValue({
      status: 'error',
      error: 'This sign-in attempt has expired or is unknown.',
    })
    await vi.advanceTimersByTimeAsync(1500)
    expect(onOutcome).toHaveBeenCalledTimes(1)
    expect(onOutcome.mock.calls[0][0]).toMatchObject({
      ok: false,
      error: 'This sign-in attempt has expired or is unknown.',
    })
  })
})
