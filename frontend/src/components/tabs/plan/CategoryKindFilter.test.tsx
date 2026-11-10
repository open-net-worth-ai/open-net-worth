// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import { CategoryKindFilter } from './CategoryKindFilter'
import { PlanningModal } from './PlanningModal'

afterEach(() => cleanup())

const OPTIONS = [
  { id: 'all', label: 'All', count: 20 },
  { id: 'expense', label: 'Expense', count: 16 },
  { id: 'income', label: 'Inflow', count: 4 },
]

describe('CategoryKindFilter', () => {
  it('starts closed, showing only the trigger button', () => {
    render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Filter categories' })).toBeTruthy()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('opens a listbox with every option and its count on click', () => {
    render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
    const listbox = screen.getByRole('listbox')
    expect(listbox.textContent).toContain('All')
    expect(listbox.textContent).toContain('16')
    expect(screen.getByRole('option', { name: /Expense/ }).getAttribute('aria-selected')).toBe(
      'false',
    )
    expect(screen.getByRole('option', { name: /^All/ }).getAttribute('aria-selected')).toBe('true')
  })

  it('picking an option calls onChange and closes the popover', () => {
    const onChange = vi.fn()
    render(<CategoryKindFilter options={OPTIONS} value="all" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
    fireEvent.click(screen.getByRole('option', { name: /Expense/ }))
    expect(onChange).toHaveBeenCalledWith('expense')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('marks the trigger active (and relabels it) once a non-"all" filter is selected', () => {
    render(<CategoryKindFilter options={OPTIONS} value="expense" onChange={vi.fn()} />)
    const btn = screen.getByRole('button', { name: 'Filter categories (active)' })
    expect(btn.className).toContain('is-active')
  })

  it('Escape closes the popover and returns focus to the trigger', () => {
    render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
    const btn = screen.getByRole('button', { name: 'Filter categories' })
    fireEvent.click(btn)
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(document.activeElement).toBe(btn)
  })
})

/** Same helper as ActionMenu.test.tsx / KebabMenu.test.tsx / TxnDetail.mobileSheet.test.tsx —
 *  `window.matchMedia` is a getter-only accessor in this project's jsdom setup, so the property
 *  must be redefined rather than assigned. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

describe('CategoryKindFilter — mobile bottom sheet', () => {
  it('below the phone breakpoint, opens as a .kebab-sheet, not the floating popover', () => {
    const restore = mockMobile(true)
    try {
      render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
      const listbox = screen.getByRole('listbox')
      expect(listbox.classList.contains('kebab-sheet')).toBe(true)
      expect(listbox.classList.contains('pl-menu')).toBe(false)
      expect(document.querySelector('.kebab-sheet-scrim')).toBeTruthy()
    } finally {
      restore()
    }
  })

  it('a downward drag on the handle past the distance threshold dismisses it', async () => {
    const restore = mockMobile(true)
    try {
      render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
      const sheet = document.querySelector('.kebab-sheet') as HTMLElement
      const handle = sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 300, configurable: true })
      fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
      expect(screen.queryByRole('listbox')).toBeNull()
    } finally {
      restore()
    }
  })

  it('picking an option in sheet mode still calls onChange and closes it', () => {
    const restore = mockMobile(true)
    try {
      const onChange = vi.fn()
      render(<CategoryKindFilter options={OPTIONS} value="all" onChange={onChange} />)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
      fireEvent.click(screen.getByRole('option', { name: /Expense/ }))
      expect(onChange).toHaveBeenCalledWith('expense')
      expect(screen.queryByRole('listbox')).toBeNull()
    } finally {
      restore()
    }
  })

  it('lays each row out as label · count · tick, never the count jammed onto the label', () => {
    const restore = mockMobile(true)
    try {
      render(<CategoryKindFilter options={OPTIONS} value="expense" onChange={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories (active)' }))
      const rows = screen.getAllByRole('option')
      expect(rows.every((r) => r.classList.contains('pl-catfilter-item'))).toBe(true)
      const expense = screen.getByRole('option', { name: /Expense/ })
      expect(expense.querySelector('.pl-catfilter-item-label')?.textContent).toBe('Expense')
      expect(expense.querySelector('.pl-catfilter-item-count')?.textContent).toBe('16')
      expect(expense.querySelector('.pl-catfilter-item-tick svg')).not.toBeNull()
      expect(expense.classList.contains('on')).toBe(true)
      const all = screen.getByRole('option', { name: /^All/ })
      // The slot is there on every row, empty — so the tick never shifts the count column.
      expect(all.querySelector('.pl-catfilter-item-tick')).not.toBeNull()
      expect(all.querySelector('.pl-catfilter-item-tick svg')).toBeNull()
      expect(all.classList.contains('on')).toBe(false)
    } finally {
      restore()
    }
  })

  it('opened inside a PlanningModal, Escape closes the sheet and leaves the modal up', () => {
    // Radix's Escape handler fires first (it registered first, capture, on document) and
    // dismisses the modal unless it finds [data-modal-escape-layer] in its own subtree.
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      render(
        <PlanningModal onClose={onClose} label="Manage categories">
          <CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />
        </PlanningModal>,
      )
      const wrap = document.querySelector('.pl-catfilter-wrap') as HTMLElement
      expect(wrap.hasAttribute('data-modal-escape-layer')).toBe(false)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
      expect(wrap.hasAttribute('data-modal-escape-layer')).toBe(true)
      fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
      expect(screen.queryByRole('listbox')).toBeNull()
      expect(onClose).not.toHaveBeenCalled()
      // The marker leaves with the sheet: the next Escape reaches the modal as before.
      expect(wrap.hasAttribute('data-modal-escape-layer')).toBe(false)
      fireEvent.keyDown(document.body, { key: 'Escape' })
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('pointer events on the sheet and its scrim never bubble to the surface that opened it', () => {
    // Portals bubble React events to their React ancestors: the parent PlanningModal's own
    // drag-to-dismiss hook would otherwise see every drag on this sheet as a drag on itself.
    const restore = mockMobile(true)
    try {
      const parent = vi.fn()
      render(
        <div
          onPointerDown={parent}
          onPointerMove={parent}
          onPointerUp={parent}
          onPointerCancel={parent}
        >
          <CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />
        </div>,
      )
      const trigger = screen.getByRole('button', { name: 'Filter categories' })
      fireEvent.pointerDown(trigger, { pointerId: 1 })
      expect(parent).toHaveBeenCalledTimes(1) // the wiring itself works
      fireEvent.click(trigger)
      const sheet = document.querySelector('.kebab-sheet') as HTMLElement
      const scrim = document.querySelector('.kebab-sheet-scrim') as HTMLElement
      for (const target of [
        sheet.querySelector('.kebab-sheet-handle-wrap') as HTMLElement,
        screen.getByRole('option', { name: /Expense/ }),
        scrim,
      ]) {
        fireEvent.pointerDown(target, { pointerId: 2, clientY: 100 })
        fireEvent.pointerMove(target, { pointerId: 2, clientY: 140 })
        fireEvent.pointerUp(target, { pointerId: 2, clientY: 140 })
        fireEvent.pointerCancel(target, { pointerId: 2 })
      }
      expect(parent).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('above the phone breakpoint, still opens the plain popover (unchanged)', () => {
    const restore = mockMobile(false)
    try {
      render(<CategoryKindFilter options={OPTIONS} value="all" onChange={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: 'Filter categories' }))
      const listbox = screen.getByRole('listbox')
      expect(listbox.classList.contains('pl-menu')).toBe(true)
      expect(listbox.classList.contains('kebab-sheet')).toBe(false)
    } finally {
      restore()
    }
  })
})
