// @vitest-environment jsdom
//
// Coming back from another app must not lose the conversation: iOS terminates a backgrounded app, so
// a quick look elsewhere used to come back as a fresh launch (owner report, 2026-11-07).

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../utils/pwa', () => ({ isStandalonePwa: () => true }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))
vi.mock('../utils/chatStore', () => ({ deleteMessages: vi.fn() }))

const ACTIVE = 'lucius_active_conversation'
const AWAY = 'lucius_pwa_last_away'
const MINUTE = 60 * 1000

async function launch() {
  vi.resetModules()
  return import('./useConversations')
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem(ACTIVE, 'the-call')
})

describe('relaunching the installed app', () => {
  it('stays on the conversation after a quick look at another app', async () => {
    localStorage.setItem(AWAY, String(Date.now() - 2 * MINUTE))
    await launch()
    expect(localStorage.getItem(ACTIVE)).toBe('the-call')
  })

  it('starts a new conversation after a real absence', async () => {
    localStorage.setItem(AWAY, String(Date.now() - 31 * MINUTE))
    await launch()
    expect(localStorage.getItem(ACTIVE)).not.toBe('the-call')
  })

  it('starts a new conversation when it never saw the app leave', async () => {
    await launch()
    expect(localStorage.getItem(ACTIVE)).not.toBe('the-call')
  })

  it('notes when the app goes to the background', async () => {
    localStorage.setItem(AWAY, String(Date.now() - 2 * MINUTE))
    await launch()
    localStorage.removeItem(AWAY)
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(Number(localStorage.getItem(AWAY))).toBeGreaterThan(0)
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  })

  it('keeps going on a reload of the same running app', async () => {
    sessionStorage.setItem('lucius_pwa_session_seen', '1')
    await launch()
    expect(localStorage.getItem(ACTIVE)).toBe('the-call')
  })
})
