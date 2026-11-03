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
vi.mock('../../../platform/desktop', () => ({ desktopBridge: () => (desktop ? {} : undefined) }))

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
  resetVoiceCallForTests()
  delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
})

describe('DictateButton', () => {
  it('writes everything heard so far, however the browser splits it, and settles on stop', () => {
    const onText = vi.fn()
    render(<DictateButton onText={onText} empty={false} />)
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
    const { rerender } = render(<DictateButton onText={onText} empty={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    const rec = FakeRecognition.last
    act(() => rec.onresult?.({ resultIndex: 0, results: [result('send this', false)] }))
    rerender(<DictateButton onText={onText} empty />) // sent: the box emptied
    expect(rec.abort).toHaveBeenCalled()
    act(() => {
      rec.onresult?.({ resultIndex: 0, results: [result('send this now', true)] })
      rec.onend?.()
    })
    expect(onText.mock.calls).toEqual([['send this', false]])
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy()
  })

  it('says when the microphone is blocked', () => {
    render(<DictateButton onText={vi.fn()} empty />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    act(() => {
      FakeRecognition.last.onerror?.({ error: 'not-allowed' })
      FakeRecognition.last.onend?.()
    })
    expect(screen.getByRole('button', { name: /can't use the microphone/ })).toBeTruthy()
  })

  it('is not offered where it cannot work, nor during a voice call', () => {
    delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    const { unmount } = render(<DictateButton onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    ;(window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition =
      FakeRecognition
    desktop = true
    const second = render(<DictateButton onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
    second.unmount()
    desktop = false
    act(() => setVoiceCallForTests({ phase: 'live', sessionId: 's1' }))
    render(<DictateButton onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
