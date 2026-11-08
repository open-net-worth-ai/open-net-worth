import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { keepScreenOn } from './voiceWakeLock'

type Sentinel = EventTarget & { released: boolean; release: () => Promise<void> }

function sentinel(): Sentinel {
  const target = new EventTarget() as Sentinel
  target.released = false
  target.release = vi.fn(async () => {
    target.released = true
    target.dispatchEvent(new Event('release'))
  })
  return target
}

const flush = () => new Promise((resolve) => setTimeout(resolve))

function setVisibility(value: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('keepScreenOn', () => {
  let locks: Sentinel[]
  let request: ReturnType<typeof vi.fn>

  beforeEach(() => {
    locks = []
    request = vi.fn(async () => {
      const lock = sentinel()
      locks.push(lock)
      return lock
    })
    Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request } })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  afterEach(async () => {
    keepScreenOn(false)
    await flush()
    Reflect.deleteProperty(navigator, 'wakeLock')
  })

  it('holds the screen on for a call and lets it go after', async () => {
    keepScreenOn(true)
    await flush()
    expect(request).toHaveBeenCalledWith('screen')
    expect(locks[0]?.released).toBe(false)

    keepScreenOn(false)
    await flush()
    expect(locks[0]?.released).toBe(true)
  })

  it('asks once, however many times the call changes phase', async () => {
    keepScreenOn(true)
    keepScreenOn(true)
    await flush()
    keepScreenOn(true)
    await flush()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('takes it again on coming back, as hiding the page drops it', async () => {
    keepScreenOn(true)
    await flush()
    await locks[0]?.release()
    setVisibility('hidden')
    await flush()
    expect(request).toHaveBeenCalledTimes(1)

    setVisibility('visible')
    await flush()
    expect(request).toHaveBeenCalledTimes(2)
    expect(locks[1]?.released).toBe(false)
  })

  it("doesn't take it on coming back once the call is over", async () => {
    keepScreenOn(true)
    await flush()
    keepScreenOn(false)
    await flush()
    setVisibility('visible')
    await flush()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('lets go of a lock granted just after the call ended', async () => {
    let grant: (lock: Sentinel) => void = () => {}
    request.mockImplementationOnce(() => new Promise((resolve) => (grant = resolve)))
    keepScreenOn(true)
    keepScreenOn(false)
    const late = sentinel()
    grant(late)
    await flush()
    expect(late.released).toBe(true)
  })

  it('carries on when the browser refuses', async () => {
    request.mockRejectedValueOnce(new Error('low battery'))
    keepScreenOn(true)
    await expect(flush()).resolves.toBeUndefined()
  })

  it('does nothing in a browser without it', async () => {
    Reflect.deleteProperty(navigator, 'wakeLock')
    keepScreenOn(true)
    await flush()
    expect(request).not.toHaveBeenCalled()
  })
})
