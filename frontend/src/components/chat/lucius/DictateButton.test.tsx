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
let client:
  | {
      startDictation?: () => Promise<void>
      stopDictation?: () => Promise<void>
    }
  | undefined
vi.mock('../../../platform/desktop', () => ({
  desktopBridge: () => (desktop ? {} : undefined),
  desktopClientBridge: () => client,
}))

import { DictateButton } from './DictateButton'
import { resetDictationForTests } from './useDictation'
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

/** A mic press; the desktop app's dictation asks macOS first, so it settles a beat later. */
async function press(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }))
  })
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
  resetDictationForTests()
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
    // Stopped, never aborted: an aborted one left the next dictation on an iPhone hearing
    // nothing.
    expect(rec.stop).toHaveBeenCalled()
    expect(rec.abort).not.toHaveBeenCalled()
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

  it('says when a start never really started, instead of going quiet', () => {
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
    act(() => FakeRecognition.last.onend?.()) // ended at once, nothing heard, no error
    expect(screen.getByRole('status').textContent).toMatch(/didn't start/)
  })

  it('tries once more when start() throws, and names an unknown error', () => {
    let throws = 1
    const w = window as unknown as { webkitSpeechRecognition?: unknown }
    const original = w.webkitSpeechRecognition
    class Flaky extends FakeRecognition {
      constructor() {
        super()
        if (throws-- > 0)
          this.start = vi.fn(() => {
            throw new Error('InvalidStateError')
          })
      }
    }
    w.webkitSpeechRecognition = Flaky
    try {
      render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
      fireEvent.click(screen.getByRole('button', { name: 'Dictate' }))
      expect(screen.getByRole('button', { name: 'Stop dictating' })).toBeTruthy()
      act(() => {
        FakeRecognition.last.onerror?.({ error: 'language-not-supported' })
        FakeRecognition.last.onend?.()
      })
      expect(screen.getByRole('status').textContent).toMatch(/language-not-supported/)
    } finally {
      w.webkitSpeechRecognition = original
    }
  })

  it('is not offered on an iPhone or iPad, where Safari gives a page one dictation', () => {
    const ua = vi.spyOn(navigator, 'userAgent', 'get')
    try {
      ua.mockReturnValue(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15',
      )
      const phone = render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
      expect(screen.queryByRole('button')).toBeNull()
      phone.unmount()
      // An iPad says it is a Mac, with a touch screen.
      ua.mockReturnValue('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15')
      Object.defineProperty(navigator, 'maxTouchPoints', { value: 5, configurable: true })
      render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
      expect(screen.queryByRole('button')).toBeNull()
      Reflect.deleteProperty(navigator, 'maxTouchPoints')
    } finally {
      ua.mockRestore()
    }
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
    await press('Dictate')
    expect(focusBox).toHaveBeenCalledBefore(startDictation)
    expect(startDictation).toHaveBeenCalledTimes(1)
    expect(FakeRecognition.last).toBe(before) // no browser recogniser was made
    await press('Stop dictating')
    // A refusal says how to turn it on.
    startDictation.mockImplementationOnce(() => Promise.reject(new Error('off')))
    await press('Dictate')
    expect(screen.getByRole('status').textContent).toMatch(/Turn on Dictation in System Settings/)
  })

  it('is hidden in the desktop app off a Mac, even connected to a server', () => {
    client = {} // a Windows or Linux window: no system dictation to start
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('ends macOS dictation on a second press and when a call starts, never on send', async () => {
    const startDictation = vi.fn(() => Promise.resolve())
    const stopDictation = vi.fn(() => Promise.resolve())
    client = { startDictation, stopDictation }
    const focusBox = vi.fn()
    const props = { focusBox, onText: vi.fn() }
    const { rerender } = render(<DictateButton {...props} empty />)
    // A second press stops it: starting again would not.
    await press('Dictate')
    await press('Stop dictating')
    expect(startDictation).toHaveBeenCalledTimes(1)
    expect(stopDictation).toHaveBeenCalledTimes(1)
    expect(focusBox).toHaveBeenCalledTimes(1) // the caret stays: nothing takes it out
    // Sending what it typed leaves it listening, the mic red: you dictate on, or tap to stop.
    await press('Dictate')
    rerender(<DictateButton {...props} empty={false} />)
    rerender(<DictateButton {...props} empty />)
    expect(stopDictation).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Stop dictating' })).toBeTruthy()
    // A call starting stops it: the call hears you itself.
    act(() => setVoiceCallForTests({ phase: 'live', sessionId: 's1' }))
    expect(stopDictation).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it("follows Esc, macOS's own stop", async () => {
    client = {
      startDictation: vi.fn(() => Promise.resolve()),
      stopDictation: vi.fn(() => Promise.resolve()),
    }
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    await press('Dictate')
    // The desktop app's start moves focus away and back: that is not a stop.
    act(() => {
      ;(document.activeElement as HTMLElement | null)?.blur()
    })
    expect(screen.getByRole('button', { name: 'Stop dictating' })).toBeTruthy()
    act(() => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy()
  })

  it('stays red when sending moves the chat into a conversation and brings a new box', async () => {
    client = {
      startDictation: vi.fn(() => Promise.resolve()),
      stopDictation: vi.fn(() => Promise.resolve()),
    }
    const first = render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    await press('Dictate')
    first.unmount()
    render(<DictateButton focusBox={vi.fn()} onText={vi.fn()} empty />)
    expect(screen.getByRole('button', { name: 'Stop dictating' })).toBeTruthy()
  })

  it('puts the caret back in the box when the window returns, as after "enable Dictation?"', async () => {
    client = {
      startDictation: vi.fn(() => Promise.resolve()),
      stopDictation: vi.fn(() => Promise.resolve()),
    }
    const focusBox = vi.fn()
    render(<DictateButton focusBox={focusBox} onText={vi.fn()} empty />)
    await press('Dictate')
    expect(focusBox).toHaveBeenCalledTimes(1)
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(focusBox).toHaveBeenCalledTimes(2)
    await press('Stop dictating')
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(focusBox).toHaveBeenCalledTimes(2) // not once it's stopped
  })
})
