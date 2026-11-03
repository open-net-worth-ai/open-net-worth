// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: {},
  createLocalAudioTrack: vi.fn(),
}))
vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
let desktop = false
// The desktop app's window bridge, in either mode; `startDictation` only on a Mac.
let client: { startDictation?: () => Promise<void> } | undefined
vi.mock('../../../platform/desktop', () => ({
  desktopBridge: () => (desktop ? {} : undefined),
  desktopClientBridge: () => client,
}))

import { DictateButton } from './DictateButton'
import { resetVoiceCallForTests, setVoiceCallForTests } from './voiceCall'

type Handler = ((e: unknown) => void) | null
class FakeRecognition {
  static last: FakeRecognition
  lang = ''
  continuous = false
  interimResults = false
  onresult: Handler = null
  onerror: Handler = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn(() => this.onend?.())
  abort = vi.fn(() => this.onend?.())
  constructor() {
    FakeRecognition.last = this
  }
}

function result(transcript: string, isFinal: boolean) {
  return { isFinal, 0: { transcript } }
}

beforeEach(() => {
  ;(window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition =
    FakeRecognition
})
afterEach(() => {
  cleanup()
  desktop = false
  client = undefined
  resetVoiceCallForTests()
  delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
})

describe('DictateButton', () => {
  it('writes everything heard so far, however the browser splits it, and settles on stop', () => {
    const onText = vi.fn()
    render(<DictateButton focusBox={vi.fn()} onText={onText} empty={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    const rec = FakeRecognition.last
    expect(rec.start).toHaveBeenCalled()
    expect(rec.continuous && rec.interimResults).toBe(true)
    // Chrome: a result per phrase.
    act(() => rec.onresult?.({ resultIndex: 0, results: [result(' what did I ', false)] }))
    act(() =>
      rec.onresult?.({
        resultIndex: 1,
        results: [result('what did I spend', true), result('on coffee', false)],
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Stop dictating' }))
    expect(onText.mock.calls).toEqual([
      ['what did I', false],
      ['what did I spend on coffee', false],
      ['what did I spend on coffee', true],
    ])
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy()
  })

  it('stops, and writes nothing more, once the message it was writing is sent', () => {
    const onText = vi.fn()
    const { rerender } = render(<DictateButton focusBox={vi.fn()} onText={onText} empty={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    const rec = FakeRecognition.last
    act(() => rec.onresult?.({ resultIndex: 0, results: [result('send this', false)] }))
    rerender(<DictateButton focusBox={vi.fn()} onText={onText} empty />) // sent: the box emptied
    expect(rec.abort).toHaveBeenCalled()
    act(() => {
      rec.onresult?.({ resultIndex: 0, results: [result('send this now', true)] })
      rec.onend?.()
    })
    expect(onText.mock.calls).toEqual([['send this', false]])
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy()
  })

  it('says when the microphone is blocked', () => {
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    act(() => {
      FakeRecognition.last.onerror?.({ error: 'not-allowed' })
      FakeRecognition.last.onend?.()
    })
    expect(screen.getByRole('button', { name: /can't use the microphone/ })).toBeTruthy()
  })

  it('says why it failed beside the mic, then the mic is plain again', () => {
    vi.useFakeTimers()
    try {
      render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
      fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
      act(() => {
        FakeRecognition.last.onerror?.({ error: 'not-allowed' })
        FakeRecognition.last.onend?.()
      })
      expect(screen.getByRole('status').textContent).toMatch(/Chrome or Safari/)
      act(() => vi.advanceTimersByTime(6000))
      expect(screen.queryByRole('status')).toBeNull()
      const mic = screen.getByRole('button', { name: 'Dictate' })
      expect(mic.dataset.problem).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('stays in a browser with no speech service, saying why on every press', () => {
    const first = render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    act(() => {
      FakeRecognition.last.onerror?.({ error: 'network' })
      FakeRecognition.last.onend?.()
    })
    // Told where dictation works, not to try again.
    expect(screen.getByRole('status').textContent).toMatch(/can't turn speech into text/)
    // Typing is moving on: the tip goes, the mic stays, plain.
    act(() => {
      fireEvent.keyDown(document, { key: 'a' })
    })
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: 'Dictate' }).dataset.problem).toBeUndefined()
    first.unmount()
    // Nothing is remembered: a later visit offers it again.
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy()
  })

  it('is not offered where it cannot work, nor during a voice call', () => {
    delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    const { unmount } = render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    ;(window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition =
      FakeRecognition
    desktop = true
    const second = render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
    second.unmount()
    desktop = false
    act(() => setVoiceCallForTests({ phase: 'live', sessionId: 's1' }))
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it("hands over to macOS's own dictation in the Mac desktop app, never the browser's", async () => {
    const startDictation = vi.fn(() => Promise.resolve())
    client = { startDictation }
    const focusBox = vi.fn()
    const before = FakeRecognition.last
    render(<DictateButton focusBox={focusBox} onText={vi.fn()} empty />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    expect(focusBox).toHaveBeenCalledBefore(startDictation)
    expect(startDictation).toHaveBeenCalledTimes(1)
    expect(FakeRecognition.last).toBe(before) // no browser recogniser was made
    // A refusal says how to turn it on.
    startDictation.mockImplementationOnce(() => Promise.reject(new Error('off')))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    })
    expect(screen.getByRole('status').textContent).toMatch(/Turn on Dictation in System Settings/)
  })

  it('is hidden in the desktop app off a Mac, even connected to a server', () => {
    client = {} // a Windows or Linux window: no system dictation to start
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
