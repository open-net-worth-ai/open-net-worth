// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
const dispatch = vi.fn()
vi.mock('../../../lucius/bus', () => ({ dispatchScreenCommand: (c: unknown) => dispatch(c) }))
let mobile = false
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => mobile }))
// No real room in jsdom.
vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: { ROOM_DELETED: 5 },
  createLocalAudioTrack: vi.fn(async () => ({ stop: vi.fn() })),
}))

import { useCallInSendSlot, VoiceCallButton } from './VoiceCallButton'
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
  describe("the composer's Send slot", () => {
    function Slot() {
      return useCallInSendSlot() ? <VoiceCallButton sessionId="s1" /> : <button>Send</button>
    }

    it('keeps Send while the voice_calls flag is off, and asks nothing of the server', () => {
      restore = overrideFlagsForTests({ voice_calls: false })
      render(<Slot />)
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy()
      expect(authFetch).not.toHaveBeenCalled()
    })

    it('keeps Send while calls are not set up', async () => {
      restore = overrideFlagsForTests({ voice_calls: true })
      respond(UNSET)
      render(<Slot />)
      await waitFor(() => expect(authFetch).toHaveBeenCalled())
      await act(async () => {})
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Set up voice calls' })).toBeNull()
    })

    it('holds Send until it knows, then Call once calls are ready', async () => {
      restore = overrideFlagsForTests({ voice_calls: true })
      respond(READY)
      render(<Slot />)
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy()
      expect(await screen.findByRole('button', { name: 'Call Lucius' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    })
  })

  it('is not there while the voice_calls flag is off', () => {
    restore = overrideFlagsForTests({ voice_calls: false })
    render(<VoiceCallButton sessionId="s1" />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('takes you to Voice calls in Connectors, not an error, while not set up', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(UNSET)
    render(<VoiceCallButton sessionId="s1" />)
    const phone = await screen.findByRole('button', { name: 'Set up voice calls' })
    expect(phone.className).toBe('lx-call-btn')
    fireEvent.click(phone)
    expect(dispatch).toHaveBeenCalledWith({
      name: 'connectors.open_setup',
      args: { id: 'voice' },
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/connectors' })
    expect(authFetch).not.toHaveBeenCalledWith('/api/voice/call', expect.anything())
  })

  it('says nothing about a worker that is down until you press Call', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(
      { ...READY, worker: { ...READY.worker, running: false, error: 'Could not start.' } },
      { ok: false, body: { detail: 'Voice calls are starting up. Try again in a few seconds.' } },
    )
    render(
      <>
        <VoiceCallNotice sessionId="s1" />
        <VoiceCallButton sessionId="s1" />
      </>,
    )
    const call = await screen.findByRole('button', { name: 'Call Lucius' })
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(call)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('starting up')
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(within(alert).queryByRole('button', { name: 'View logs' })).toBeNull()
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

  describe('where a call opens', () => {
    function pressCall(tone?: 'composer' | 'pill' | 'fab') {
      function View() {
        return <output>{useVoiceCall().view}</output>
      }
      restore = overrideFlagsForTests({ voice_calls: true })
      // Never answers: the call stays connecting, in the view it opened in.
      authFetch.mockImplementation((url: string) =>
        url === '/api/voice/config'
          ? Promise.resolve({ ok: true, json: async () => READY })
          : new Promise(() => {}),
      )
      render(
        <>
          <VoiceCallButton sessionId="s1" tone={tone} />
          <View />
        </>,
      )
      return screen.findByRole('button', { name: 'Call Lucius' })
    }
    afterEach(() => {
      mobile = false
    })

    it('opens full screen from the message box on a phone', async () => {
      mobile = true
      fireEvent.click(await pressCall())
      expect(screen.getByRole('status').textContent).toBe('stage')
    })

    it.each(['fab', 'pill'] as const)('stays small from the %s on a phone', async (tone) => {
      mobile = true
      fireEvent.click(await pressCall(tone))
      expect(screen.getByRole('status').textContent).toBe('bar')
    })

    it('stays a strip from the message box on a wide screen', async () => {
      fireEvent.click(await pressCall())
      expect(screen.getByRole('status').textContent).toBe('bar')
    })
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

describe('a refused microphone in the Mac desktop app', () => {
  afterEach(() => {
    delete (window as { onwDesktopClient?: unknown }).onwDesktopClient
  })

  it('offers System Settings, where macOS keeps the refusal', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(READY)
    const openMicrophoneSettings = vi.fn(() => Promise.resolve())
    ;(window as { onwDesktopClient?: unknown }).onwDesktopClient = {
      version: 1,
      openMicrophoneSettings,
    }
    const { setVoiceCallForTests } = await import('./voiceCall')
    act(() => setVoiceCallForTests({ problem: { kind: 'mic', message: 'Not allowed.' } }))
    render(<VoiceCallNotice sessionId="s1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open System Settings' }))
    expect(openMicrophoneSettings).toHaveBeenCalledTimes(1)
  })

  it('has no such button in a browser, which only the address bar can fix', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    respond(READY)
    const { setVoiceCallForTests } = await import('./voiceCall')
    act(() => setVoiceCallForTests({ problem: { kind: 'mic', message: 'Blocked.' } }))
    render(<VoiceCallNotice sessionId="s1" />)
    expect(screen.queryByRole('button', { name: 'Open System Settings' })).toBeNull()
  })
})
