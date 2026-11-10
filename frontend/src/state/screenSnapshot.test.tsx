// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScreenSnapshotProvider, useScreenSnapshot } from './screenSnapshot'

const EVENT = 'onw:screen-awareness-default-changed'
const h = vi.hoisted(() => ({ accountDefault: true }))

vi.mock('../hooks/useChatSuggestionsEnabled', () => ({
  SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT: 'onw:screen-awareness-default-changed',
  useScreenAwarenessDefault: () => ({ enabled: h.accountDefault, loaded: true }),
}))

let current: ReturnType<typeof useScreenSnapshot>

function Probe() {
  current = useScreenSnapshot()
  return null
}

const mount = () =>
  render(
    <ScreenSnapshotProvider>
      <Probe />
    </ScreenSnapshotProvider>,
  )

describe('Screen awareness on this device', () => {
  beforeEach(() => {
    localStorage.clear()
    h.accountDefault = true
  })
  afterEach(cleanup)

  it('follows the account default until the device chooses', () => {
    h.accountDefault = false
    mount()
    expect(current.enabled).toBe(false)
  })

  it("keeps the device's own choice over the default, across reloads", () => {
    h.accountDefault = false
    const first = mount()
    act(() => current.setEnabled(true))
    expect(current.enabled).toBe(true)
    expect(localStorage.getItem('screen-awareness')).toBe('on')
    first.unmount()

    mount()
    expect(current.enabled).toBe(true)
  })

  it('honours a choice saved before the default existed', () => {
    localStorage.setItem('screen-awareness', 'off')
    mount()
    expect(current.enabled).toBe(false)
  })

  it('drops the choice when the default is changed in Settings', () => {
    localStorage.setItem('screen-awareness', 'off')
    mount()
    act(() => {
      window.dispatchEvent(new Event(EVENT))
    })
    expect(current.enabled).toBe(true)
    expect(localStorage.getItem('screen-awareness')).toBeNull()
  })

  it('follows another window of this browser turning it off', () => {
    mount()
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: 'screen-awareness', newValue: 'off' }),
      )
    })
    expect(current.enabled).toBe(false)
  })
})
