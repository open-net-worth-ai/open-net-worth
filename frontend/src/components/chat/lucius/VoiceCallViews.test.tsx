// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.fn(async () => ({ ok: true, json: async () => ({ ready: true, worker: {} }) }))
vi.mock('../../../api/client', () => ({ authFetch: () => authFetch() }))
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
import { VoiceCallPill } from './VoiceCallPill'
import { VoiceCallSlot } from './VoiceCallSlot'
import {
  resetVoiceCallForTests,
  setVoiceCallForTests,
  useVoiceCall,
  type CallState,
} from './voiceCall'
import { callStatus, pillMode } from './voiceCallStatus'

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
    setupOpen: false,
    view: 'stage',
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
