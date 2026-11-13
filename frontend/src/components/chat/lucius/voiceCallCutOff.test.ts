// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: { ROOM_DELETED: 5 },
  createLocalAudioTrack: vi.fn(),
}))

import { CUT_OFF_SETTLE_MS, clearCallOn, markCallOn, watchForCutOff } from './voiceCallCutOff'

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
  vi.resetModules()
})

describe('a call the page was taken away from', () => {
  it('is said once on the next page, for the conversation it was in', () => {
    markCallOn('s1')
    const onCutOff = vi.fn()
    watchForCutOff(onCutOff)
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    expect(onCutOff).toHaveBeenCalledWith('s1')
    const again = vi.fn()
    watchForCutOff(again)
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    expect(again).not.toHaveBeenCalled()
  })

  it('is not said for a call that ended in the page, or that is still on in another tab', () => {
    markCallOn('s1')
    clearCallOn()
    const ended = vi.fn()
    watchForCutOff(ended)
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    expect(ended).not.toHaveBeenCalled()

    markCallOn('s2')
    const elsewhere = vi.fn()
    watchForCutOff(elsewhere)
    vi.advanceTimersByTime(1_500)
    markCallOn('s2') // the other tab's call refreshes its mark
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    expect(elsewhere).not.toHaveBeenCalled()
  })

  it('is old news after a while', () => {
    markCallOn('s1')
    vi.advanceTimersByTime(11 * 60_000)
    const onCutOff = vi.fn()
    watchForCutOff(onCutOff)
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    expect(onCutOff).not.toHaveBeenCalled()
  })

  it('opens the next page with a notice that offers to call again', async () => {
    markCallOn('s1')
    vi.resetModules()
    const call = await import('./voiceCall')
    vi.advanceTimersByTime(CUT_OFF_SETTLE_MS)
    const problem = renderHook(() => call.useVoiceCall()).result.current.problem
    expect(problem).toMatchObject({ kind: 'dropped', sessionId: 's1' })
    expect(problem?.message).toMatch(/closed or reloaded/)
  })
})
