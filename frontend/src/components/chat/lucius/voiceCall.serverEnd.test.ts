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
  LEFT_GRACE_MS,
  PICKUP_TIMEOUT_MS,
  minimizeCall,
  resetVoiceCallForTests,
  startCall,
  useVoiceCall,
} from './voiceCall'
import { renderHook } from '@testing-library/react'

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
