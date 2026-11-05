// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// A LiveKit room we can drive: the page's handlers run when a test emits an event. Hoisted, as
// the mock below (hoisted itself) needs it.
type Handler = (...args: unknown[]) => void
const { FakeRoom } = vi.hoisted(() => {
  class FakeRoom {
    static last: FakeRoom
    handlers = new Map<string, Handler[]>()
    remoteParticipants = new Map<string, { identity: string }>()
    localParticipant = { identity: 'owner', publishTrack: vi.fn(async () => {}) }
    constructor() {
      FakeRoom.last = this
    }
    on(event: string, h: Handler) {
      this.handlers.set(event, [...(this.handlers.get(event) ?? []), h])
      return this
    }
    emit(event: string, ...args: unknown[]) {
      for (const h of this.handlers.get(event) ?? []) h(...args)
    }
    async connect() {}
    async disconnect() {
      this.emit('disconnected', 1)
    }
  }
  return { FakeRoom }
})

vi.mock('livekit-client', () => ({
  Room: FakeRoom,
  RoomEvent: {
    TrackSubscribed: 'trackSubscribed',
    ActiveSpeakersChanged: 'activeSpeakersChanged',
    ConnectionQualityChanged: 'connectionQualityChanged',
    ParticipantConnected: 'participantConnected',
    ParticipantDisconnected: 'participantDisconnected',
    Reconnecting: 'reconnecting',
    Reconnected: 'reconnected',
    Disconnected: 'disconnected',
  },
  Track: { Kind: { Audio: 'audio' } },
  DisconnectReason: { CLIENT_INITIATED: 1, ROOM_DELETED: 5 },
  createLocalAudioTrack: vi.fn(async () => ({ stop: vi.fn(), mediaStreamTrack: {} })),
}))
const serverProblem: { current: { kind: string; message: string } | null } = vi.hoisted(() => ({
  current: null,
}))
vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url === '/api/voice/config'
        ? { worker: { call_problem: serverProblem.current } }
        : { url: 'wss://acme.example', token: 't', session_id: 's1' },
  })),
}))
vi.mock('../../../lucius/bus', () => ({ emitConversationChanged: vi.fn() }))

import {
  hangUp,
  HEALTH_MS,
  LEFT_GRACE_MS,
  PICKUP_TIMEOUT_MS,
  minimizeCall,
  resetVoiceCallForTests,
  startCall,
  useVoiceCall,
} from './voiceCall'
import { page, saveNeedsReload, takeReloadNotice } from './voicePagePolicy'
import { renderHook } from '@testing-library/react'
import { authFetch } from '../../../api/client'

function state() {
  return renderHook(() => useVoiceCall()).result.current
}

async function liveCall() {
  await startCall('s1')
  const room = FakeRoom.last
  room.remoteParticipants.set('lucius', { identity: 'lucius' })
  room.emit('participantConnected')
  room.emit('activeSpeakersChanged', [{ identity: 'lucius' }])
  expect(state().phase).toBe('live')
  return room
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn() },
    configurable: true,
  })
})
afterEach(() => {
  serverProblem.current = null
  resetVoiceCallForTests()
  vi.useRealTimers()
})

describe('hanging up yourself', () => {
  it('says nothing when you hang up while it is still connecting', async () => {
    const connect = vi.spyOn(FakeRoom.prototype, 'connect').mockImplementationOnce(async () => {
      await hangUp()
      throw new Error('Client initiated disconnect')
    })
    await startCall('s1')
    connect.mockRestore()
    expect(state().problem).toBeNull()
    expect(state().phase).toBe('idle')
  })

  it('does not go on connecting when you hang up while the call is being asked for', async () => {
    const before = FakeRoom.last
    vi.mocked(authFetch).mockImplementationOnce(async () => {
      await hangUp()
      return {
        ok: true,
        json: async () => ({ url: 'wss://acme.example', token: 't', session_id: 's1' }),
      } as Response
    })
    await startCall('s1')
    expect(FakeRoom.last).toBe(before)
    expect(state().phase).toBe('idle')
    expect(state().problem).toBeNull()
  })

  it('still reports a connect that failed on its own', async () => {
    const connect = vi.spyOn(FakeRoom.prototype, 'connect').mockImplementationOnce(async () => {
      throw new Error('could not establish signal connection')
    })
    await startCall('s1')
    connect.mockRestore()
    expect(state().problem).toMatchObject({
      kind: 'setup',
      message: "Couldn't connect to your voice server. Check your internet and LiveKit settings.",
      detail: 'could not establish signal connection',
    })
  })
})

describe('a page whose security policy refuses the voice server', () => {
  function refused() {
    return vi.spyOn(FakeRoom.prototype, 'connect').mockImplementation(async () => {
      const refusal = new Event('securitypolicyviolation')
      Object.assign(refusal, { effectiveDirective: 'connect-src' })
      document.dispatchEvent(refusal)
      throw new Error('could not establish signal connection: Failed to fetch')
    })
  }
  afterEach(() => {
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  it('reloads once, and the reloaded page offers the call instead of an error', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {})
    refused()
    await startCall('s1')
    expect(reload).toHaveBeenCalledTimes(1)
    expect(state().problem).toBeNull()
    expect(takeReloadNotice()).toBe(true)
    expect(takeReloadNotice()).toBe(false) // shown once
  })

  it('says so plainly when a reload did not cure it, and never reloads again', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {})
    refused()
    await startCall('s1')
    await startCall('s1')
    expect(reload).toHaveBeenCalledTimes(1)
    expect(state().problem).toMatchObject({
      kind: 'other',
      message: 'Your browser blocked the connection to your voice server.',
      detail: 'could not establish signal connection: Failed to fetch',
    })
  })

  it('may reload again after a call has connected', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {})
    const connect = refused()
    await startCall('s1')
    connect.mockRestore()
    await startCall('s1')
    expect(state().phase).toBe('ringing')
    await hangUp()
    refused()
    await startCall('s1')
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('does not reload for a connection that failed some other way', async () => {
    const reload = vi.spyOn(page, 'reload').mockImplementation(() => {})
    vi.spyOn(FakeRoom.prototype, 'connect').mockImplementationOnce(async () => {
      throw new Error('could not establish signal connection: Failed to fetch')
    })
    await startCall('s1')
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('saving a voice server', () => {
  it('reloads only when the page would be left with the wrong policy', () => {
    const cloud = 'wss://acme-x1.livekit.cloud'
    expect(saveNeedsReload('', cloud)).toBe(false)
    expect(saveNeedsReload(cloud, 'wss://other.livekit.cloud')).toBe(false)
    expect(saveNeedsReload(cloud, cloud)).toBe(false)
    expect(saveNeedsReload('', 'wss://voice.acme.test:7443')).toBe(true)
    expect(saveNeedsReload(cloud, 'wss://voice.acme.test')).toBe(true)
    expect(saveNeedsReload('wss://voice.acme.test', cloud)).toBe(true)
    expect(saveNeedsReload('wss://voice.acme.test', 'wss://voice.acme.test:7443')).toBe(true)
    expect(saveNeedsReload('wss://voice.acme.test', '  wss://voice.acme.test  ')).toBe(false)
  })
})

describe('the server ending a call', () => {
  it('ends the call when Lucius leaves without closing the room (a crash, an app restart)', async () => {
    const room = await liveCall()
    room.remoteParticipants.clear()
    room.emit('participantDisconnected')
    expect(state().phase).toBe('live') // a goodbye still gets its moment to close the room
    await vi.advanceTimersByTimeAsync(LEFT_GRACE_MS)
    expect(state().phase).toBe('idle')
    expect(state().problem?.kind).toBe('dropped')
  })

  it('is an ordinary end when Lucius says goodbye and closes the room', async () => {
    const room = await liveCall()
    room.remoteParticipants.clear()
    room.emit('participantDisconnected')
    room.emit('disconnected', 5)
    await vi.advanceTimersByTimeAsync(LEFT_GRACE_MS)
    expect(state().phase).toBe('ended')
    expect(state().problem).toBeNull()
  })

  it('ends where the call was: a folded-down call does not reopen to say it ended', async () => {
    const room = await liveCall()
    minimizeCall()
    room.emit('disconnected', 5)
    expect(state().phase).toBe('ended')
    expect(state().view).toBe('bar')
  })

  it('says why when LiveKit refused the call, instead of just ringing', async () => {
    await startCall('s1')
    serverProblem.current = { kind: 'credit', message: 'LiveKit is out of inference credit.' }
    FakeRoom.last.emit('disconnected', 5) // the worker closed the room after the refusal
    await vi.advanceTimersByTimeAsync(0)
    expect(state().phase).toBe('idle')
    expect(state().problem).toEqual({
      kind: 'service',
      message: 'LiveKit is out of inference credit.',
    })
  })

  it('stops ringing when nobody picks up', async () => {
    await startCall('s1')
    expect(state().phase).toBe('ringing')
    await vi.advanceTimersByTimeAsync(PICKUP_TIMEOUT_MS)
    expect(state().phase).toBe('idle')
    expect(state().problem?.message).toMatch(/didn't pick up/)
  })
})

describe('starting a call', () => {
  it('starts folded, so a call from another screen never takes it over', async () => {
    await liveCall()
    expect(state().view).toBe('bar')
  })

  it('opens the full view when its caller asks for it', async () => {
    await startCall('s1', 'stage')
    expect(state().phase).toBe('ringing')
    expect(state().view).toBe('stage')
  })
})

describe('the connection rating', () => {
  const you = { identity: 'owner' }
  const lucius = { identity: 'lucius' }

  it("is the weaker of the two connections, yours and Lucius's", async () => {
    const room = await liveCall()
    expect(state().quality).toBeNull()
    room.emit('connectionQualityChanged', 'excellent', you)
    expect(state().quality).toBe('excellent')
    room.emit('connectionQualityChanged', 'poor', lucius)
    expect(state().quality).toBe('poor')
    room.emit('connectionQualityChanged', 'good', lucius)
    expect(state().quality).toBe('good')
  })

  it('drops a side LiveKit can no longer rate, instead of keeping its old rating', async () => {
    const room = await liveCall()
    room.emit('connectionQualityChanged', 'excellent', you)
    room.emit('connectionQualityChanged', 'poor', lucius)
    room.emit('connectionQualityChanged', 'unknown', lucius)
    expect(state().quality).toBe('excellent')
  })

  it('follows the device going offline and back, without waiting for LiveKit', async () => {
    await liveCall()
    expect(state().offline).toBe(false)
    window.dispatchEvent(new Event('offline'))
    expect(state().offline).toBe(true)
    window.dispatchEvent(new Event('online'))
    expect(state().offline).toBe(false)
  })

  it('stops following the network when the call ends', async () => {
    await liveCall()
    await hangUp()
    window.dispatchEvent(new Event('offline'))
    expect(state().offline).toBe(false)
  })

  it("doesn't carry over into the next call", async () => {
    const room = await liveCall()
    room.emit('connectionQualityChanged', 'poor', you)
    await hangUp()
    expect(state().quality).toBeNull()
    vi.advanceTimersByTime(4000)
    resetVoiceCallForTests()
    const next = await liveCall()
    next.emit('connectionQualityChanged', 'excellent', lucius)
    expect(state().quality).toBe('excellent')
  })
})

describe("Lucius's side of the call dropping", () => {
  const health = (answer: () => Promise<unknown>) =>
    vi.mocked(authFetch).mockImplementation(async (url) => {
      if (String(url).startsWith('/api/voice/call/health')) return answer() as Promise<Response>
      return { ok: true, json: async () => ({}) } as Response
    })
  const answers = (trouble: boolean) => async () => ({
    ok: true,
    status: 200,
    json: async () => ({ trouble }),
  })
  const askAgain = () => vi.advanceTimersByTimeAsync(HEALTH_MS)

  afterEach(() => {
    vi.mocked(authFetch).mockReset()
    vi.mocked(authFetch).mockImplementation(async (url) => {
      return {
        ok: true,
        json: async () =>
          url === '/api/voice/config'
            ? { worker: { call_problem: serverProblem.current } }
            : { url: 'wss://acme.example', token: 't', session_id: 's1' },
      } as Response
    })
  })

  it('follows whether Lucius is working, from the same answer', async () => {
    await liveCall()
    const reply = (working: boolean) => async () => ({
      ok: true,
      status: 200,
      json: async () => ({ trouble: false, working }),
    })
    health(reply(true))
    await askAgain()
    expect(state().luciusWorking).toBe(true)
    health(reply(false))
    await askAgain()
    expect(state().luciusWorking).toBe(false)
  })

  it('takes one lost reply for nothing, and two in a row for a dropped line', async () => {
    await liveCall()
    health(() => Promise.reject(new TypeError('Load failed')))
    await askAgain()
    expect(state().luciusTrouble).toBe(false)
    await askAgain()
    expect(state().luciusTrouble).toBe(true)
    expect(state().phase).toBe('live')
  })

  it('counts a tunnel that cannot reach the app, but not an answer from the app', async () => {
    await liveCall()
    health(async () => ({ ok: false, status: 530 }))
    await askAgain()
    await askAgain()
    expect(state().luciusTrouble).toBe(true)
    health(async () => ({ ok: false, status: 404 }))
    await askAgain()
    expect(state().luciusTrouble).toBe(false)
  })

  it('believes the app when it answers that the line is still broken, until it is back', async () => {
    await liveCall()
    health(answers(true))
    await askAgain()
    expect(state().luciusTrouble).toBe(true)
    health(answers(false))
    await askAgain()
    expect(state().luciusTrouble).toBe(false)
  })

  it('leaves a drop on this device to its own phase, and stops asking after the call', async () => {
    const room = await liveCall()
    room.emit('reconnecting')
    health(() => Promise.reject(new TypeError('Load failed')))
    await askAgain()
    await askAgain()
    expect(state().luciusTrouble).toBe(false)
    room.emit('reconnected')
    await hangUp()
    vi.mocked(authFetch).mockClear()
    await askAgain()
    expect(authFetch).not.toHaveBeenCalled()
  })
})
