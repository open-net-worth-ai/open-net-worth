// @vitest-environment jsdom
// On a touch screen a card's actions are a swipe, never a "⋯" menu (owner, 2026-11-12). With a
// mouse the menu stays: there is no swipe to find.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SortTable, type Column } from './SortTable'

const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')

function setTouch(touch: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: touch && query === '(hover: none) and (pointer: coarse)',
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia,
  })
}

afterEach(() => {
  cleanup()
  if (original) Object.defineProperty(window, 'matchMedia', original)
})

interface R {
  sym: string
}
const columns: Column<R>[] = [
  { key: 'name', label: 'Stock', left: true, cell: (r) => r.sym },
  { key: 'menu', label: '', action: true, cell: (r) => <button>{`Menu ${r.sym}`}</button> },
]

function table(onFix: (r: R) => void) {
  return render(
    <SortTable
      rows={[{ sym: 'ACME' }, { sym: 'BOLT' }]}
      columns={columns}
      rowKey={(r) => r.sym}
      label="Holdings"
      swipeActions={(r) =>
        r.sym === 'ACME'
          ? [{ label: 'Fix', ariaLabel: `Fix ${r.sym}`, onClick: () => onFix(r) }]
          : []
      }
    />,
  )
}

describe('SortTable cards: actions', () => {
  it('a touch screen swipes to a card action instead of drawing its menu', () => {
    setTouch(true)
    const onFix = vi.fn()
    table(onFix)
    expect(screen.queryByText('Menu ACME')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Fix ACME' }))
    expect(onFix).toHaveBeenCalledWith({ sym: 'ACME' })
    // A card with nothing to swipe keeps its own action cell.
    expect(screen.getByText('Menu BOLT')).toBeTruthy()
  })

  it('with a mouse the menu stays and nothing swipes', () => {
    setTouch(false)
    table(vi.fn())
    expect(screen.getByText('Menu ACME')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Fix ACME' })).toBeNull()
  })
})
