// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.fn(async () => ({ ok: true, json: async () => ({ ready: true, worker: {} }) }))
vi.mock('../../../api/client', () => ({ authFetch: () => authFetch() }))
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../../../state/luciusChat', () => ({
  useLuciusChat: () => ({
    conversations: [],
    activeId: '',
    conversationModel: '',
    sendMessage: vi.fn(),
  }),
}))
vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: { ROOM_DELETED: 5 },
  createLocalAudioTrack: vi.fn(),
}))
let mobile = false
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => mobile }))

import { VoiceCallButton } from './VoiceCallButton'
import { VoiceCallFab } from './VoiceCallFab'
import { VoiceCallPill } from './VoiceCallPill'
import { VoiceCallSlot } from './VoiceCallSlot'
import {
  resetVoiceCallForTests,
  setVoiceCallForTests,
  useVoiceCall,
  type CallState,
} from './voiceCall'
import { callSignal, callStatus, orbState, pillMode } from './voiceCallStatus'

const LIVE: Partial<CallState> = { phase: 'live', sessionId: 's1', connectedAt: Date.now() }

let restore: () => void = () => {}
afterEach(() => {
  restore()
  cleanup()
  mobile = false
  resetVoiceCallForTests()
})

function call(over: Partial<CallState>): CallState {
  return {
    phase: 'idle',
    sessionId: null,
    muted: false,
    connectedAt: null,
    endedSeconds: null,
    problem: null,
    view: 'stage',
    luciusTrouble: false,
    quality: null,
    offline: false,
    luciusWorking: false,
    heard: '',
    heardDone: false,
    heardShown: false,
    spoken: '',
    spokenDone: false,
    luciusSpeaking: false,
    spokenLines: [],
    ...over,
  }
}

describe('call status', () => {
  it.each([
    [call({}), false, 'rest'],
    [call({ phase: 'connecting' }), false, 'calling'],
    [call({ phase: 'ringing' }), true, 'calling'],
    [call({ phase: 'live' }), false, 'live'],
    [call({ phase: 'live' }), true, 'attention'],
    [call({ phase: 'reconnecting' }), true, 'reconnecting'],
    [call({ phase: 'ended', endedSeconds: 74 }), false, 'ended'],
    [call({ problem: { kind: 'mic', message: 'x' } }), false, 'problem'],
  ])('the pill reads %o (approval %s) as %s', (state, approval, mode) => {
    expect(pillMode(state, approval)).toBe(mode)
  })

  it('says only what is worth saying, never who is speaking', () => {
    const now = Date.now()
    const live = call({ phase: 'live', connectedAt: now - 134_000 })
    expect(callStatus(live, now, false)).toMatchObject({ line: '', short: '2:14' })
    expect(callStatus(live, now, true)).toMatchObject({
      line: 'Lucius is working…',
      short: 'Working…',
    })
    expect(callStatus({ ...live, muted: true }, now, false).line).toBe('')
  })

  it('says what a bad connection means, over everything else on the line', () => {
    const now = Date.now()
    const live = call({ phase: 'live', connectedAt: now - 134_000, luciusTrouble: true })
    expect(callStatus(live, now, true)).toMatchObject({
      line: 'Poor connection. Lucius may not hear you.',
      stripLine: 'Poor connection',
      short: 'Poor connection',
      time: '2:14',
    })
    expect(orbState(live, true)).toBe('reconnecting')
    expect(pillMode(live, true)).toBe('poor')
    expect(callStatus(call({ phase: 'reconnecting' }), now, false).line).toBe(
      'Your connection dropped. Reconnecting…',
    )
  })
})

describe('signal bars', () => {
  const live = { phase: 'live' as const, connectedAt: Date.now() }

  it.each([
    ['excellent', 4],
    ['good', 3],
    ['poor', 1],
    ['lost', 0],
  ] as const)('reads a %s connection as %i bars', (quality, bars) => {
    expect(callSignal(call({ ...live, quality }))?.bars).toBe(bars)
  })

  it('shows nothing until LiveKit has rated the call, or outside one', () => {
    expect(callSignal(call(live))).toBeNull()
    expect(callSignal(call({ phase: 'ringing', quality: 'excellent' }))).toBeNull()
    expect(callSignal(call({ phase: 'ended', quality: 'excellent' }))).toBeNull()
  })

  it('shows no signal the moment the device has no network, whatever LiveKit last said', () => {
    const now = Date.now()
    const down = call({ ...live, connectedAt: now - 134_000, quality: 'excellent', offline: true })
    expect(callSignal(down)?.bars).toBe(0)
    expect(callStatus(down, now, true)).toMatchObject({
      line: 'Your connection dropped. Reconnecting…',
      short: 'Reconnecting…',
      time: '2:14',
    })
    expect(orbState(down, false)).toBe('reconnecting')
    expect(pillMode(down, true)).toBe('reconnecting')
    // Yours outranks "Lucius may not hear you": with no network, asking the app fails as well.
    expect(callStatus({ ...down, luciusTrouble: true }, now, false).line).toContain('Your')
  })

  it('never shows full bars beside a warning that the connection is bad', () => {
    expect(callSignal(call({ ...live, quality: 'excellent', luciusTrouble: true }))?.bars).toBe(1)
    expect(callSignal(call({ phase: 'reconnecting', quality: 'excellent' }))?.bars).toBe(0)
  })
})

describe('VoiceCallSlot', () => {
  const slot = (visible = true) =>
    render(
      <VoiceCallSlot busy={false} visible={visible}>
        <textarea aria-label="composer" />
      </VoiceCallSlot>,
    )

  it('shows the call stage in place of the composer on a computer', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    slot()
    const stage = screen.getByRole('group', { name: 'Voice call' })
    expect(within(stage).getByRole('button', { name: 'End call' })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('leaves the name to the strip heading on a poor connection, and keeps it for a screen reader', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, luciusTrouble: true }))
    const { container } = slot()
    expect(container.querySelector('.lx-callstage-name')?.textContent).toBe('Lucius')
    expect(container.querySelector('.lx-callstage-sub')?.textContent).toBe('Poor connection')
    expect(container.querySelector('.lx-callstage-line')?.textContent).toBe(
      'Poor connection. Lucius may not hear you.',
    )
  })

  it('draws the bars with a name a screen reader can say', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, quality: 'poor' }))
    slot()
    const bars = screen.getByRole('img', { name: 'Connection quality: poor' })
    expect(bars.getAttribute('data-bars')).toBe('1')
  })

  it('takes the whole screen on a phone, and minimising goes back to the bar', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    mobile = true
    act(() => setVoiceCallForTests(LIVE))
    slot()
    const dialog = screen.getByRole('dialog', { name: 'Voice call with Lucius' })
    expect(dialog.parentElement).toBe(document.body)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Minimise call' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open call' }))
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('Esc folds the phone call down to the strip, never hangs up', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    mobile = true
    act(() => setVoiceCallForTests(LIVE))
    function Phase() {
      return <span data-testid="phase">{useVoiceCall().phase}</span>
    }
    render(<Phase />)
    slot()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Open call' })).toBeTruthy()
    expect(screen.getByTestId('phase').textContent).toBe('live')
  })

  it('on a computer the strip is the only form: nothing to minimise to', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, view: 'bar' }))
    slot()
    expect(screen.getByRole('group', { name: 'Voice call' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Minimise call' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open call' })).toBeNull()
  })

  it('keeps the message box during a call', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    slot()
    expect(screen.getByLabelText('composer').closest('[hidden]')).toBeNull()
  })

  it('on a phone, Type folds the call down to the bar over the message box', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    mobile = true
    act(() => setVoiceCallForTests(LIVE))
    slot()
    fireEvent.click(screen.getByRole('button', { name: 'Type a message' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: 'Open call' })).toBeTruthy()
  })

  it('gives the Send slot back mid-call: no Call button while one is on', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    const { rerender } = render(<VoiceCallButton sessionId="s1" />)
    expect(await screen.findByRole('button', { name: 'Call Lucius' })).toBeTruthy()
    act(() => setVoiceCallForTests(LIVE))
    rerender(<VoiceCallButton sessionId="s1" />)
    expect(screen.queryByRole('button', { name: 'Call Lucius' })).toBeNull()
  })

  it('mounts no call view on a collapsed or hidden surface', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    slot(false)
    expect(screen.queryByRole('button', { name: 'End call' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open call' })).toBeNull()
  })
})

describe("the phone's round button", () => {
  const fab = (onLuciusTab = false, onOpenChat = vi.fn()) =>
    render(<VoiceCallFab onLuciusTab={onLuciusTab} onOpenChat={onOpenChat} />)

  it('is not there with the flag off, or on the Lucius tab, which has the call itself', () => {
    restore = overrideFlagsForTests({ voice_calls: false })
    act(() => setVoiceCallForTests({ ...LIVE, view: 'bar' }))
    const { container, unmount } = fab()
    expect(container.querySelector('.lx-call-fab')).toBeNull()
    unmount()
    restore()
    restore = overrideFlagsForTests({ voice_calls: true })
    const onTab = fab(true)
    expect(onTab.container.querySelector('.lx-call-fab')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a folded call as a button that opens the full call screen in place', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, view: 'bar' }))
    const onOpenChat = vi.fn()
    fab(false, onOpenChat)
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open call' }))
    const stage = screen.getByRole('dialog', { name: 'Voice call with Lucius' })
    expect(within(stage).getByRole('button', { name: 'End call' })).toBeTruthy()
    expect(onOpenChat).not.toHaveBeenCalled()
    fireEvent.click(within(stage).getByRole('button', { name: 'Minimise call' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: 'Open call' })).toBeTruthy()
  })

  it('shows the call at a glance: the clock, or what is going on instead', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    const now = Date.now()
    act(() =>
      setVoiceCallForTests({ ...LIVE, connectedAt: now - 134_000, view: 'bar', quality: 'good' }),
    )
    fab()
    const pill = screen.getByRole('button', { name: 'Open call' })
    expect(within(pill).getByRole('status').textContent).toMatch(/^2:1\d$/)
    expect(within(pill).queryByRole('img')).toBeNull()
    act(() => setVoiceCallForTests({ luciusWorking: true }))
    expect(within(pill).getByRole('status').textContent).toBe('Working…')
    act(() => setVoiceCallForTests({ luciusWorking: false, luciusTrouble: true }))
    expect(within(pill).getByRole('status').textContent).toBe('Poor connection')
  })

  it('takes Type to the chat, the only place with a message box', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    const onOpenChat = vi.fn()
    fab(false, onOpenChat)
    fireEvent.click(screen.getByRole('button', { name: 'Type a message' }))
    expect(onOpenChat).toHaveBeenCalledTimes(1)
  })

  it('says why a call failed, wherever you started it', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ problem: { kind: 'other', message: 'It did not start.' } }))
    fab()
    expect(screen.getByRole('alert').textContent).toContain('It did not start.')
  })
})

describe('VoiceCallPill', () => {
  const handlers = () => ({ onAsk: vi.fn(), onHoverOpen: vi.fn(), onOpenDock: vi.fn() })

  it('rests as Call beside Ask Lucius: two buttons, neither inside the other', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    const h = handlers()
    render(<VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...h} />)
    const callBtn = await screen.findByRole('button', { name: 'Call Lucius' })
    const ask = screen.getByRole('button', { name: 'Ask Lucius' })
    expect(callBtn.contains(ask) || ask.contains(callBtn)).toBe(false)
    fireEvent.click(ask)
    expect(h.onAsk).toHaveBeenCalled()
  })

  it.each([
    ['excellent', false],
    ['good', false],
    ['poor', true],
    ['lost', true],
  ] as const)('with a %s connection, showing bars is %s', (quality, shown) => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, quality }))
    const { container } = render(
      <VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...handlers()} />,
    )
    expect(container.querySelector('.lx-callstage-signal') !== null).toBe(shown)
  })

  it('leaves focus alone when the connection changes', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, quality: 'excellent' }))
    render(<VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...handlers()} />)
    act(() => setVoiceCallForTests({ luciusTrouble: true }))
    expect(document.activeElement).toBe(document.body)
    act(() => setVoiceCallForTests({ luciusTrouble: false, phase: 'reconnecting' }))
    expect(document.activeElement).toBe(document.body)
  })

  it('says a poor connection in words as well as bars', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests({ ...LIVE, quality: 'excellent', luciusTrouble: true }))
    const { container } = render(
      <VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...handlers()} />,
    )
    expect(screen.getByRole('status').textContent).toBe('Poor connection')
    expect(container.querySelector('.lx-callstage-signal')?.getAttribute('data-bars')).toBe('1')
  })

  it('holds the whole call: clock, Mute and End, without opening the dock', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    const h = handlers()
    render(<VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...h} />)
    expect(screen.getByRole('button', { name: 'Mute' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'End call' })).toBeTruthy()
    // The clock may tick over while a busy machine renders: any of the first ten seconds.
    expect(screen.getByRole('status').textContent).toMatch(/^0:0\d$/)
    expect(h.onOpenDock).not.toHaveBeenCalled()
  })

  it('asks for approval it cannot show, and Review opens the chat on the call bar', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() => setVoiceCallForTests(LIVE))
    function View() {
      return <span data-testid="view">{useVoiceCall().view}</span>
    }
    const h = handlers()
    render(
      <>
        <View />
        <VoiceCallPill sessionId="s1" busy={false} needsApproval {...h} />
      </>,
    )
    expect(screen.getByRole('status').textContent).toBe('Needs approval')
    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    expect(h.onOpenDock).toHaveBeenCalled()
    expect(screen.getByTestId('view').textContent).toBe('bar')
  })

  it('shows a blocked microphone in the pill, with Try again and a way to dismiss it', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    act(() =>
      setVoiceCallForTests({ problem: { kind: 'mic', message: 'The microphone is blocked.' } }),
    )
    render(<VoiceCallPill sessionId="s1" busy={false} needsApproval={false} {...handlers()} />)
    expect(screen.getByRole('alert').textContent).toBe("Can't use the microphone")
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.getByRole('button', { name: 'Ask Lucius' })).toBeTruthy()
  })
})

describe('the pill with a refused microphone in the Mac desktop app', () => {
  afterEach(() => {
    delete (window as { onwDesktopClient?: unknown }).onwDesktopClient
  })

  it('opens System Settings instead of calling again', () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    const openMicrophoneSettings = vi.fn(() => Promise.resolve())
    ;(window as { onwDesktopClient?: unknown }).onwDesktopClient = {
      version: 1,
      openMicrophoneSettings,
    }
    act(() => setVoiceCallForTests({ problem: { kind: 'mic', message: 'Not allowed.' } }))
    render(
      <VoiceCallPill
        sessionId="s1"
        busy={false}
        needsApproval={false}
        onAsk={vi.fn()}
        onHoverOpen={vi.fn()}
        onOpenDock={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }))
    expect(openMicrophoneSettings).toHaveBeenCalledTimes(1)
  })
})
