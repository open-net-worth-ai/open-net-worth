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
    localParticipant = {
      identity: 'owner',
      trackPublications: new Map([['TR_mine', {}]]),
      publishTrack: vi.fn(async () => {}),
    }
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
    transcript: ((reader: unknown, from: { identity: string }) => Promise<void>) | null = null
    registerTextStreamHandler(_topic: string, h: FakeRoom['transcript']) {
      this.transcript = h
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
  createLocalAudioTrack: vi.fn(async () => ({
    stop: vi.fn(),
    mute: vi.fn(async () => {}),
    unmute: vi.fn(async () => {}),
    mediaStreamTrack: {},
  })),
}))
const serverProblem: { current: { kind: string; message: string } | null } = vi.hoisted(() => ({
  current: null,
}))
vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url === '/api/voice/config'
        ? { worker: { running: true, call_problem: serverProblem.current } }
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
  readLevels,
  resetVoiceCallForTests,
  restartCall,
  startCall,
  toggleMute,
  useVoiceCall,
} from './voiceCall'
import { page, saveNeedsReload, takeReloadNotice } from './voicePagePolicy'
import { renderHook } from '@testing-library/react'
import { createLocalAudioTrack } from 'livekit-client'
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
    expect(state().problem).toMatchObject({
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

describe('muting before Lucius picks up', () => {
  const openedMic = () => vi.mocked(createLocalAudioTrack).mock.results.at(-1)!.value

  it('mutes while it rings, and stays muted when he answers', async () => {
    await startCall('s1')
    expect(state().phase).toBe('ringing')
    await toggleMute()
    expect(state().muted).toBe(true)
    expect((await openedMic()).mute).toHaveBeenCalledOnce()
    const room = FakeRoom.last
    room.remoteParticipants.set('lucius', { identity: 'lucius' })
    room.emit('participantConnected')
    room.emit('activeSpeakersChanged', [{ identity: 'lucius' }])
    expect(state().phase).toBe('live')
    expect(state().muted).toBe(true)
  })

  it('holds a press made while the microphone is still opening', async () => {
    const calling = startCall('s1')
    expect(state().phase).toBe('connecting')
    await toggleMute()
    await calling
    expect(state().muted).toBe(true)
    expect((await openedMic()).mute).toHaveBeenCalledOnce()
  })

  it('unmutes the same way', async () => {
    await startCall('s1')
    await toggleMute()
    await toggleMute()
    expect(state().muted).toBe(false)
    expect((await openedMic()).unmute).toHaveBeenCalledOnce()
  })

  it('does nothing with no call on', async () => {
    await toggleMute()
    expect(state().muted).toBe(false)
  })
})

describe('how loud you are', () => {
  const openedMic = () => vi.mocked(createLocalAudioTrack).mock.results.at(-1)!.value

  it("uses LiveKit's level when the page's own reads nothing, as on an iPhone", async () => {
    const room = await liveCall()
    expect(readLevels().you).toBe(0)
    ;(room.localParticipant as unknown as { audioLevel: number }).audioLevel = 0.4
    expect(readLevels().you).toBe(0.4)
  })

  it('reads nothing while muted, whatever LiveKit last said', async () => {
    const room = await liveCall()
    ;(room.localParticipant as unknown as { audioLevel: number }).audioLevel = 0.4
    await toggleMute()
    expect(readLevels().you).toBe(0)
    await toggleMute()
    expect(readLevels().you).toBe(0.4)
  })

  describe('with Lucius on the speaker', () => {
    // A level for each side, by the track the meter is on: what the analyser would read.
    let levels: Map<unknown, number>

    beforeEach(() => {
      levels = new Map()
      vi.stubGlobal(
        'AudioContext',
        class {
          currentTime = 0
          destination = {}
          resume = vi.fn(async () => {})
          close = vi.fn(async () => {})
          createAnalyser = () => ({
            fftSize: 512,
            source: null as unknown,
            getFloatTimeDomainData(this: { source: unknown }, out: Float32Array) {
              out.fill(levels.get(this.source) ?? 0)
            },
          })
          createMediaStreamSource = (stream: { track: unknown }) => ({
            connect: (analyser: { source: unknown }) => {
              analyser.source = stream.track
            },
          })
          createGain = () => ({
            connect: vi.fn(),
            gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
          })
          createOscillator = () => ({
            frequency: { value: 0 },
            connect: vi.fn(),
            start: vi.fn(),
            stop: vi.fn(),
          })
        },
      )
      vi.stubGlobal(
        'MediaStream',
        class {
          track: unknown
          constructor(tracks: unknown[]) {
            this.track = tracks[0]
          }
        },
      )
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    async function onSpeaker() {
      await startCall('s1')
      const mine = (await openedMic()).mediaStreamTrack
      const his = {}
      FakeRoom.last.emit('trackSubscribed', {
        kind: 'audio',
        mediaStreamTrack: his,
        attach: () => document.createElement('audio'),
      })
      return { mine, his }
    }

    it('does not take his voice in the mic for you talking', async () => {
      const { mine, his } = await onSpeaker()
      levels.set(mine, 0.2) // the mic hears the speaker
      levels.set(his, 0.2)
      const heard = readLevels()
      expect(heard.lucius).toBeGreaterThan(0)
      expect(heard.you).toBe(0)
    })

    it('shows you when you cut in, louder than he is', async () => {
      const { mine, his } = await onSpeaker()
      levels.set(his, 0.1)
      levels.set(mine, 0.3)
      const heard = readLevels()
      expect(heard.you).toBeGreaterThan(0) // the mic's light still shows you
      expect(heard.luciusTalking).toBe(true) // and the wash knows the light is his
    })

    it('stays quiet through the gaps between his words, then shows you', async () => {
      const { mine, his } = await onSpeaker()
      levels.set(mine, 0.2)
      levels.set(his, 0.2)
      readLevels()
      levels.set(his, 0)
      levels.set(mine, 0) // the room's echo fades with him
      expect(readLevels().you).toBe(0)
      vi.advanceTimersByTime(600)
      levels.set(mine, 0.2)
      expect(readLevels().you).toBeGreaterThan(0)
    })

    it('shows you straight away while he is silent', async () => {
      const { mine } = await onSpeaker()
      levels.set(mine, 0.2)
      expect(readLevels().you).toBeGreaterThan(0)
    })
  })

  describe('after unmuting onto a new track', () => {
    let listeningTo: unknown[] = []

    beforeEach(() => {
      listeningTo = []
      vi.stubGlobal(
        'AudioContext',
        class {
          currentTime = 0
          destination = {}
          resume = vi.fn(async () => {})
          close = vi.fn(async () => {})
          createAnalyser = () => ({ fftSize: 0, getFloatTimeDomainData: vi.fn() })
          createMediaStreamSource = (stream: { track: unknown }) => {
            listeningTo.push(stream.track)
            return { connect: vi.fn() }
          }
          createGain = () => ({
            connect: vi.fn(),
            gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
          })
          createOscillator = () => ({
            frequency: { value: 0 },
            connect: vi.fn(),
            start: vi.fn(),
            stop: vi.fn(),
          })
        },
      )
      vi.stubGlobal(
        'MediaStream',
        class {
          track: unknown
          constructor(tracks: unknown[]) {
            this.track = tracks[0]
          }
        },
      )
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('listens to the new track, not the dead one', async () => {
      await startCall('s1')
      const track = await openedMic()
      const fresh = { id: 'reacquired' }
      track.unmute.mockImplementation(async () => {
        track.mediaStreamTrack = fresh
      })
      await toggleMute()
      await toggleMute()
      expect(listeningTo.at(-1)).toBe(fresh)
    })

    it('leaves it alone when the track stayed the same', async () => {
      await startCall('s1')
      const before = listeningTo.length
      await toggleMute()
      await toggleMute()
      expect(listeningTo).toHaveLength(before)
    })
  })
})

describe('restarting a call', () => {
  it('hangs up, runs the change, and calls back into the same conversation', async () => {
    const first = await liveCall()
    const seen: string[] = []
    await restartCall(async () => {
      seen.push(state().phase)
      expect(FakeRoom.last).toBe(first) // no new call until the change is done
    })
    expect(seen).toEqual(['connecting'])
    expect(FakeRoom.last).not.toBe(first)
    expect(state().phase).toBe('ringing')
    expect(state().sessionId).toBe('s1')
    expect(state().problem).toBeNull()
  })

  it('restarts a call still opening the microphone, and drops that first attempt', async () => {
    let openMic = (_mic: unknown) => {}
    vi.mocked(createLocalAudioTrack).mockImplementationOnce(
      () => new Promise((done) => (openMic = done)) as never,
    )
    const starting = startCall('s1')
    await restartCall(async () => {})
    expect(state().phase).toBe('ringing')
    const restarted = FakeRoom.last
    const stale = { stop: vi.fn() }
    openMic(stale)
    await starting
    expect(stale.stop).toHaveBeenCalledOnce()
    expect(FakeRoom.last).toBe(restarted)
    expect(state().phase).toBe('ringing')
  })

  it('keeps the full screen it was on', async () => {
    await startCall('s1', 'stage')
    await restartCall(async () => {})
    expect(state().view).toBe('stage')
  })

  it('only runs the change with no call on', async () => {
    const change = vi.fn(async () => {})
    await restartCall(change)
    expect(change).toHaveBeenCalledOnce()
    expect(state().phase).toBe('idle')
  })

  it('says so and stays hung up when the change is refused', async () => {
    const first = await liveCall()
    await restartCall(async () => {
      throw new Error('That voice cannot speak Hindi.')
    })
    expect(FakeRoom.last).toBe(first)
    expect(state().phase).toBe('idle')
    expect(state().problem?.message).toBe('That voice cannot speak Hindi.')
  })

  it('does not call back if you ended the call meanwhile', async () => {
    const first = await liveCall()
    await restartCall(async () => {
      await hangUp()
    })
    expect(FakeRoom.last).toBe(first)
    expect(state().phase).toBe('idle')
  })
})

describe('your words as you speak them', () => {
  const words = (text: string, final = false, track = 'TR_mine', after = Promise.resolve()) => ({
    info: {
      attributes: { 'lk.transcribed_track_id': track, 'lk.transcription_final': String(final) },
    },
    readAll: async () => {
      await after
      return text
    },
  })
  const say = async (reader: unknown, identity = 'lucius-agent') => {
    await FakeRoom.last.transcript!(reader, { identity })
  }

  it('shows them as they arrive, each update replacing the last', async () => {
    await liveCall()
    await say(words('how much'))
    expect(state().heard).toBe('how much')
    await say(words('how much did I spend'))
    expect(state().heard).toBe('how much did I spend')
  })

  it("knows them by your mic's track or your name, whoever relays them", async () => {
    await liveCall()
    await say(words('by track'))
    expect(state().heard).toBe('by track')
    await say(words('by name', false, 'TR_other'), 'owner')
    expect(state().heard).toBe('by name')
  })

  it("never shows Lucius's words as yours", async () => {
    await liveCall()
    await say(words('your balance is fine', false, 'TR_lucius'))
    expect(state().heard).toBe('')
  })

  it('keeps newer words when an older update finishes reading last', async () => {
    await liveCall()
    let finishOld = () => {}
    const old = say(words('how', false, 'TR_mine', new Promise<void>((done) => (finishOld = done))))
    await say(words('how much'))
    finishOld()
    await old
    expect(state().heard).toBe('how much')
  })

  it('clears soon after the final words, and at once when the call ends', async () => {
    const timers = vi.spyOn(window, 'setTimeout')
    await liveCall()
    await say(words('how much did I spend', true))
    expect(state().heard).toBe('how much did I spend')
    expect(timers.mock.calls.some(([, ms]) => ms === 1500)).toBe(true)
    timers.mockRestore()
    await say(words('still talking'))
    await hangUp()
    expect(state().heard).toBe('')
  })
})

describe('the sound of muting', () => {
  let played: number[] = []

  beforeEach(() => {
    played = []
    class FakeContext {
      currentTime = 0
      destination = {}
      resume = vi.fn(async () => {})
      close = vi.fn(async () => {})
      createAnalyser = () => ({ fftSize: 0, getFloatTimeDomainData: vi.fn() })
      createMediaStreamSource = () => ({ connect: vi.fn() })
      createGain = () => ({
        connect: vi.fn(),
        gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
      })
      createOscillator() {
        const osc = { frequency: { value: 0 }, connect: vi.fn(), start: vi.fn(), stop: vi.fn() }
        osc.start = vi.fn(() => void played.push(osc.frequency.value))
        return osc
      }
    }
    vi.stubGlobal('AudioContext', FakeContext)
    vi.stubGlobal('MediaStream', class {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function afterTheRing() {
    await startCall('s1')
    played = []
  }

  it('falls for muted and rises for live again', async () => {
    await afterTheRing()
    await toggleMute()
    expect(played).toEqual([660, 440])
    played = []
    await toggleMute()
    expect(played).toEqual([440, 660])
  })

  it('plays from the moment you press Call, before he picks up', async () => {
    await afterTheRing()
    expect(state().phase).toBe('ringing')
    await toggleMute()
    expect(played).toHaveLength(2)
  })

  it('stays quiet with no call on', async () => {
    await toggleMute()
    expect(played).toEqual([])
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

  it('reports what the mic is doing with each check, never sound', async () => {
    health(answers(false))
    await liveCall()
    await askAgain()
    const asked = vi
      .mocked(authFetch)
      .mock.calls.map(([url]) => String(url))
      .find((url) => url.startsWith('/api/voice/call/health'))!
    const report = new URL(asked, 'http://acme.test').searchParams.get('mic')
    expect(report).toMatch(
      /^track=\w+,system_muted=[01],enabled=[01],app_muted=[01],audio=\w+,peak=[\d.]+,server_peak=[\d.]+,page=\w+,streams=\d+,words=\d+$/,
    )
  })

  afterEach(() => {
    vi.mocked(authFetch).mockReset()
    vi.mocked(authFetch).mockImplementation(async (url) => {
      return {
        ok: true,
        json: async () =>
          url === '/api/voice/config'
            ? { worker: { running: true, call_problem: serverProblem.current } }
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
