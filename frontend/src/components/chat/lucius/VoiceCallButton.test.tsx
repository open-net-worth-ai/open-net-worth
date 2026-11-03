// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
const dispatch = vi.fn()
vi.mock('../../../lucius/bus', () => ({ dispatchScreenCommand: (c: unknown) => dispatch(c) }))
// No real room in jsdom.
vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: { ROOM_DELETED: 5 },
  createLocalAudioTrack: vi.fn(async () => ({ stop: vi.fn() })),
}))

import { VoiceCallButton } from './VoiceCallButton'
import { VoiceCallNotice } from './VoiceCallNotice'
import { VoiceCallSlot } from './VoiceCallSlot'
import { resetVoiceCallForTests, startCall, useVoiceCall } from './voiceCall'

const READY = {
  ready: true,
  worker: { flag_on: true, libraries_installed: true, running: true, error: '' },
}
const UNSET = {
  ready: false,
  worker: { flag_on: true, libraries_installed: true, running: false, error: '' },
}

function respond(config: unknown, call?: { ok: boolean; body: unknown }) {
  authFetch.mockImplementation(async (url: string) =>
    url === '/api/voice/config'
      ? { ok: true, json: async () => config }
      : { ok: call?.ok ?? false, json: async () => call?.body ?? {} },
  )
}

let restore: () => void = () => {}
beforeEach(() => {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn() },
    configurable: true,
  })
})
afterEach(() => {
  restore()
  cleanup()
  authFetch.mockReset()
  navigate.mockReset()
  dispatch.mockReset()
  resetVoiceCallForTests()
})

describe('VoiceCallButton', () => {
  it('is not there while the voice_calls flag is off', () => {
    restore = overrideFlagsForTests({ voice_calls: false })
    render(<VoiceCallButton sessionId="s1" />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('opens Voice calls setup over the current screen, not an error, while not set up', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(UNSET)
    function Probe() {
      return <span data-testid="setup">{String(useVoiceCall().setupOpen)}</span>
    }
    render(
      <>
        <Probe />
        <VoiceCallButton sessionId="s1" />
      </>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Set up voice calls' }))
    expect(screen.getByTestId('setup').textContent).toBe('true')
    expect(navigate).not.toHaveBeenCalled()
    expect(authFetch).not.toHaveBeenCalledWith('/api/voice/call', expect.anything())
  })

  it('calls INTO the open conversation; a refusal lands in the notice, not the toolbar', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(READY, { ok: false, body: { detail: "The voice worker isn't running." } })
    render(
      <>
        <VoiceCallNotice sessionId="s1" />
        <VoiceCallButton sessionId="s1" />
      </>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Call Lucius' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("The voice worker isn't running.")
    const [, init] = authFetch.mock.calls.find(([url]) => url === '/api/voice/call')!
    expect(JSON.parse(init.body)).toEqual({ session_id: 's1' })
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the call outlives the composer', () => {
  it('keeps going when the chat re-lays its composer (landing → conversation)', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    // A call that is still connecting: the API has not answered yet.
    authFetch.mockImplementation(() => new Promise(() => {}))
    function Probe() {
      return <span data-testid="phase">{useVoiceCall().phase}</span>
    }
    const { unmount } = render(
      <VoiceCallSlot busy={false}>
        <textarea aria-label="draft" />
      </VoiceCallSlot>,
    )
    await act(async () => void startCall('s1'))
    unmount() // the composer moves: the old footer goes away …
    render(
      <>
        <Probe />
        <VoiceCallSlot busy={false}>
          <textarea aria-label="draft" />
        </VoiceCallSlot>
      </>,
    )
    // … and the call is still on, its view shown, and the composer still there to type in.
    await waitFor(() => expect(screen.getByTestId('phase').textContent).toBe('connecting'))
    expect(screen.getByRole('group', { name: 'Voice call' })).toBeTruthy()
    expect(screen.getByLabelText('draft').closest('[hidden]')).toBeNull()
  })
})

describe('the call notice', () => {
  it("keeps LiveKit's own words folded under Details", async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(READY)
    const { setVoiceCallForTests } = await import('./voiceCall')
    act(() =>
      setVoiceCallForTests({
        problem: {
          kind: 'service',
          message: 'Your LiveKit project is out of inference credit.',
          detail: 'LLM token credit quota exceeded, category: MaxGatewayCredits',
        },
      }),
    )
    render(<VoiceCallNotice sessionId="s1" />)
    expect(screen.queryByText(/MaxGatewayCredits/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByText(/MaxGatewayCredits/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open LiveKit' }).getAttribute('href')).toBe(
      'https://cloud.livekit.io',
    )
  })
})

describe('the call notice once you move on', () => {
  it('clears when Lucius starts answering a message you sent', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(READY)
    const { setVoiceCallForTests } = await import('./voiceCall')
    act(() => setVoiceCallForTests({ problem: { kind: 'dropped', message: 'The call dropped.' } }))
    const { rerender } = render(<VoiceCallNotice sessionId="s1" busy={false} />)
    expect(screen.getByText('The call dropped.')).toBeTruthy()
    rerender(<VoiceCallNotice sessionId="s1" busy />)
    expect(screen.queryByText('The call dropped.')).toBeNull()
  })
})
