// @vitest-environment jsdom
//
// A permission card's buttons (owner, 2026-10-12): Deny on the left as a real button; the allow
// scopes grouped on the right — with room, [Allow once] [Always allow] as separate buttons; on a
// narrow card one [Allow once ▾] split button.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { GrantActions } from './GrantActions'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function rowWidth(width: number) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      private cb: ResizeObserverCallback
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb
      }
      observe() {
        act(() => this.cb([{ contentRect: { width } } as ResizeObserverEntry], this as never))
      }
      disconnect() {}
    },
  )
}

function mount() {
  const handlers = { onOnce: vi.fn(), onAlways: vi.fn(), onDeny: vi.fn() }
  const { container } = render(<GrantActions busy={null} disabled={false} {...handlers} />)
  const labels = [...container.querySelectorAll('.change-preview-actions button')]
    .filter((b) => !b.closest('[role="menu"]'))
    .map((b) => b.textContent || b.getAttribute('aria-label'))
  return { container, labels, ...handlers }
}

function rowChildren(container: HTMLElement) {
  return [...(container.querySelector('.change-preview-actions')?.children ?? [])]
}

describe('permission card buttons', () => {
  it('on a wide card: Deny first on the left, both allow scopes grouped on the right', () => {
    rowWidth(360)
    const { container, labels, onAlways, onDeny } = mount()
    expect(labels).toEqual(['Deny', 'Allow once', 'Always allow'])
    expect(container.querySelector('.grant-split')).toBeNull()
    const [first, second] = rowChildren(container) as [Element, Element]
    expect(first.className).toContain('change-grant-btn--deny')
    expect(first.className).toContain('change-discard-btn') // a bordered button, not bare text
    expect(second.className).toContain('change-grant-allow')
    const always = screen.getByText('Always allow')
    expect(always.getAttribute('title')).toMatch(/every future call, any amount/)
    fireEvent.click(always)
    expect(onAlways).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByText('Deny'))
    expect(onDeny).toHaveBeenCalledOnce()
  })

  it('on a narrow card: Deny still first, the allow scopes grouped into a split button', () => {
    rowWidth(240)
    const { container, labels, onAlways } = mount()
    expect(labels).toEqual(['Deny', 'Allow once', 'Standing permission'])
    expect(screen.queryByText('Always allow')).toBeNull()
    fireEvent.click(screen.getByLabelText('Standing permission'))
    fireEvent.click(screen.getByText('Always allow'))
    expect(onAlways).toHaveBeenCalledOnce()
    expect(rowChildren(container)[0]!.textContent).toBe('Deny')
  })

  it('stays grouped where the width cannot be measured', () => {
    const { labels } = mount()
    expect(labels).toEqual(['Deny', 'Allow once', 'Standing permission'])
  })
})
