// @vitest-environment jsdom
//
// ScreenContextBar.test.tsx — render smoke tests (W2A).

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup, fireEvent, act } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  mockHandles.length = 0
  mockScreen.enabled = true
  mockScreen.setEnabled.mockClear()
})
import { ScreenContextBar } from './ScreenContextBar'

const { mockHandles, mockScreen } = vi.hoisted(() => ({
  mockHandles: [] as Array<{
    id: string
    type: string
    label: string
    descriptor: string
    source: string
  }>,
  mockScreen: { enabled: true, setEnabled: vi.fn() },
}))

vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: mockScreen.enabled,
    setEnabled: mockScreen.setEnabled,
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
  }),
}))

vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({
    title: 'Overview',
    facts: [['Screen', 'Overview']],
    raw: '',
    handles: mockHandles,
  }),
}))

vi.mock('../../../lucius/identity', () => ({
  hSvg: () => '',
}))

const EMPTY_VISIBLE_IDS = new Set<string>()
const CSS = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')

async function frames() {
  await act(async () => {
    await new Promise<void>((done) =>
      requestAnimationFrame(() => requestAnimationFrame(() => done())),
    )
  })
}

describe('ScreenContextBar', () => {
  it('renders the scope container', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    expect(container.querySelector('.lx-scope')).not.toBeNull()
  })

  it('shows "Screen awareness" by default', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    expect(container.textContent).toContain('Screen awareness')
  })

  it('shows "Screen awareness" regardless of recipient name', () => {
    const { container } = render(
      <ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} recipientName="Budget Bot" />,
    )
    expect(container.textContent).toContain('Screen awareness')
  })

  it('shows Peek button when not collapsed', () => {
    const { container } = render(
      <ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} collapsed={false} />,
    )
    const peekBtn = container.querySelector('.lx-scope-peek')
    expect(peekBtn).not.toBeNull()
    expect(peekBtn?.textContent).toBe('Peek')
  })

  it('hides Peek button when collapsed', () => {
    const { container } = render(
      <ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} collapsed={true} />,
    )
    expect(container.querySelector('.lx-scope-peek')).toBeNull()
  })

  it('shows the toggle switch', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    const toggle = container.querySelector('.lx-switch')
    expect(toggle).not.toBeNull()
    expect(toggle?.getAttribute('role')).toBe('switch')
  })

  it('toggle switch is "on" when enabled=true', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    // Mock returns enabled=true. Toggle should have class "on".
    expect(container.querySelector('.lx-switch.on')).not.toBeNull()
  })

  it('says what turning it off does, and that Lucius can still look', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    expect(document.body.querySelector('.lx-scope-notice')).toBeNull()

    fireEvent.click(container.querySelector('.lx-switch')!)

    expect(mockScreen.setEnabled).toHaveBeenCalledWith(false)
    const notice = document.body.querySelector('.lx-scope-notice')
    expect(notice?.getAttribute('role')).toBe('status')
    expect(notice?.textContent).toContain("won't be sent with messages")
    expect(notice?.textContent).toContain('can still look')
  })

  it('says what turning it on does, and lets the notice go', () => {
    vi.useFakeTimers()
    try {
      mockScreen.enabled = false
      const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)

      fireEvent.click(container.querySelector('.lx-switch')!)

      expect(mockScreen.setEnabled).toHaveBeenCalledWith(true)
      expect(document.body.querySelector('.lx-scope-notice')?.textContent).toContain(
        'sent with each message',
      )
      act(() => {
        vi.advanceTimersByTime(5000)
      })
      expect(document.body.querySelector('.lx-scope-notice')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([[false], [true]])('says what the switch would do while off (compact: %s)', (compact) => {
    mockScreen.enabled = false
    const { container } = render(
      <ScreenContextBar compact={compact} visibleIds={EMPTY_VISIBLE_IDS} />,
    )
    expect(container.textContent).toContain('Attach screen content as context')
    expect(container.textContent).not.toContain('Overview')
  })

  it('does not call the screen passed to Lucius in Peek while off', () => {
    mockScreen.enabled = false
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    fireEvent.click(container.querySelector('.lx-scope-peek')!)
    const peekbox = document.body.querySelector('.lx-scope-peekbox')
    expect(peekbox?.textContent).toContain('read only if he looks')
    expect(peekbox?.textContent).not.toContain('passed to Lucius')
  })

  it('peekbox is absent before clicking Peek', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    expect(container.querySelector('.lx-scope-peekbox')).toBeNull()
  })

  it('portals the Peek outside a clipping host and removes it when toggled closed', () => {
    const clippingHost = document.createElement('div')
    clippingHost.className = 'lx-side-foot'
    clippingHost.style.overflow = 'hidden'
    document.body.appendChild(clippingHost)
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />, {
      container: clippingHost,
    })
    const scope = container.querySelector<HTMLElement>('.lx-scope')!
    scope.getBoundingClientRect = () =>
      ({ top: 500, bottom: 540, left: 40, right: 360, width: 320, height: 40 }) as DOMRect
    const peekBtn = container.querySelector<HTMLButtonElement>('.lx-scope-peek')!

    fireEvent.click(peekBtn)

    const peekbox = document.body.querySelector('.lx-scope-peekbox')
    expect(peekbox).not.toBeNull()
    expect(peekbox?.tagName).toBe('DIV')
    expect(peekbox?.getAttribute('role')).toBe('region')
    expect(peekbox?.getAttribute('aria-labelledby')).toBeTruthy()
    expect(peekbox?.parentElement).toBe(document.body)
    expect(clippingHost.querySelector('.lx-scope-peekbox')).toBeNull()
    expect((peekbox as HTMLElement).style.top).toBe('250px')
    expect((peekbox as HTMLElement).style.left).toBe('39px')
    expect((peekbox as HTMLElement).style.width).toBe('322px')

    fireEvent.click(peekBtn)

    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
    clippingHost.remove()
  })

  it('uses the shared portal tier above the dock', () => {
    const start = CSS.indexOf('.lx-scope-peekbox {')
    const body = CSS.slice(start, CSS.indexOf('}', start))
    expect(body).toMatch(/z-index:\s*calc\(var\(--z-toast\) \+ 20\)/)
  })

  it('keeps compact Peek width flush with its anchor', () => {
    const { container } = render(<ScreenContextBar compact visibleIds={EMPTY_VISIBLE_IDS} />)
    const scope = container.querySelector<HTMLElement>('.lx-scope')!
    scope.getBoundingClientRect = () =>
      ({ top: 500, bottom: 540, left: 40, right: 360, width: 320, height: 40 }) as DOMRect

    fireEvent.click(container.querySelector<HTMLButtonElement>('.lx-aw-peek')!)

    const peekbox = document.body.querySelector<HTMLElement>('.lx-scope-peekbox')!
    expect(peekbox.style.left).toBe('40px')
    expect(peekbox.style.width).toBe('320px')
  })

  it('tracks anchor movement and stays reachable in a short visual viewport', async () => {
    vi.stubGlobal('visualViewport', {
      offsetTop: 20,
      offsetLeft: 0,
      width: 360,
      height: 180,
    })
    let top = 160
    let left = 350
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    const scope = container.querySelector<HTMLElement>('.lx-scope')!
    scope.getBoundingClientRect = () =>
      ({ top, bottom: top + 40, left, right: left + 100, width: 100, height: 40 }) as DOMRect
    fireEvent.click(container.querySelector<HTMLButtonElement>('.lx-scope-peek')!)

    const peekbox = document.body.querySelector<HTMLElement>('.lx-scope-peekbox')!
    expect(peekbox.style.top).toBe('24px')
    expect(peekbox.style.left).toBe('254px')
    expect(peekbox.style.maxHeight).toBe('130px')

    top = 120
    left = 10
    await frames()

    expect(peekbox.style.top).toBe('24px')
    expect(peekbox.style.left).toBe('9px')
    expect(peekbox.style.maxHeight).toBe('90px')
  })

  it('closes Peek on Escape, composer input, and autocomplete collapse', () => {
    const { container, rerender } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    const peekButton = container.querySelector<HTMLButtonElement>('.lx-scope-peek')!
    fireEvent.click(peekButton)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()

    fireEvent.click(peekButton)
    const composer = document.createElement('input')
    composer.className = 'chat-composer'
    document.body.appendChild(composer)
    fireEvent.input(composer)
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
    composer.remove()

    fireEvent.click(peekButton)
    rerender(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} collapsed />)
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
  })

  it('closes Peek when its owning host becomes inactive', () => {
    const { container, rerender } = render(
      <ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} active />,
    )
    fireEvent.click(container.querySelector<HTMLButtonElement>('.lx-scope-peek')!)
    expect(document.body.querySelector('.lx-scope-peekbox')).not.toBeNull()

    rerender(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} active={false} />)
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()

    rerender(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} active />)
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
  })

  it('loops keyboard focus between the Peek trigger, handles, and awareness switch', () => {
    mockHandles.push(
      {
        id: 'view:transactions',
        type: 'view',
        label: 'Transactions',
        descriptor: 'Current account activity',
        source: 'cash',
      },
      {
        id: 'view:budget',
        type: 'view',
        label: 'Budget',
        descriptor: 'Current budget',
        source: 'planning',
      },
    )
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    const trigger = container.querySelector<HTMLButtonElement>('.lx-scope-peek')!
    const awarenessSwitch = container.querySelector<HTMLButtonElement>('.lx-switch')!
    trigger.focus()
    fireEvent.click(trigger)
    expect(document.activeElement).toBe(trigger)

    fireEvent.keyDown(trigger, { key: 'Tab' })
    const handleButtons = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('.lx-handle'),
    )
    expect(handleButtons).toHaveLength(2)
    const firstHandle = handleButtons[0]!
    const lastHandle = handleButtons[1]!
    expect(document.activeElement).toBe(firstHandle)

    fireEvent.keyDown(firstHandle, { key: 'Tab' })
    lastHandle.focus()
    fireEvent.keyDown(lastHandle, { key: 'Tab' })
    expect(document.activeElement).toBe(awarenessSwitch)

    firstHandle.focus()
    fireEvent.keyDown(firstHandle, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(trigger)

    fireEvent.keyDown(trigger, { key: 'Tab' })
    fireEvent.keyDown(firstHandle, { key: 'Escape' })
    expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('consumes the Peek-closing Escape but lets the next Escape reach later document listeners', () => {
    const { container } = render(<ScreenContextBar visibleIds={EMPTY_VISIBLE_IDS} />)
    const trigger = container.querySelector<HTMLButtonElement>('.lx-scope-peek')!
    fireEvent.click(trigger)
    const laterEscapeListener = vi.fn()
    document.addEventListener('keydown', laterEscapeListener)

    try {
      fireEvent.keyDown(document.body.querySelector('.lx-scope-peekbox')!, { key: 'Escape' })
      expect(document.body.querySelector('.lx-scope-peekbox')).toBeNull()
      expect(laterEscapeListener).not.toHaveBeenCalled()

      fireEvent.keyDown(document, { key: 'Escape' })
      expect(laterEscapeListener).toHaveBeenCalledTimes(1)
    } finally {
      document.removeEventListener('keydown', laterEscapeListener)
    }
  })
})
