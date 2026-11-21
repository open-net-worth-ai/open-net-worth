// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => true }))

import { AutomationMenu } from './AutomationMenu'
import { AnchoredMenu, ComboSelect } from './ComboSelect'

afterEach(cleanup)

const OPTIONS = [
  ['cat_a', 'Groceries'],
  ['cat_b', 'Household'],
] as const

describe('on a phone, every Rule picker and menu is a bottom sheet', () => {
  it('a single choice opens a sheet, and picking closes it with the value chosen', () => {
    const onChange = vi.fn()
    render(
      <ComboSelect
        id="pick"
        value=""
        options={OPTIONS}
        placeholder="Choose a category"
        onChange={onChange}
        onCreate={async () => null}
      />,
    )
    fireEvent.click(document.getElementById('pick')!)
    expect(screen.getByRole('dialog', { name: 'Choose a category' })).toBeTruthy()
    expect(document.querySelector('.rl-combo-pop')).toBeNull()
    fireEvent.click(screen.getByRole('option', { name: 'Household' }))
    expect(onChange).toHaveBeenCalledWith('cat_b')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('a multi choice stays open, toggles, and closes on Done', () => {
    const onChangeMany = vi.fn()
    render(
      <ComboSelect
        id="many"
        multiple
        value={['cat_a']}
        options={OPTIONS}
        placeholder="Choose a category"
        onChangeMany={onChangeMany}
      />,
    )
    fireEvent.click(document.getElementById('many')!)
    fireEvent.click(screen.getByRole('option', { name: 'Groceries' }))
    expect(onChangeMany).toHaveBeenCalledWith([])
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Add action and the automation menus open as sheets, and a tap does not reach the card', () => {
    const onSelect = vi.fn()
    const onCard = vi.fn()
    render(
      <>
        <AnchoredMenu
          label="Add action"
          trigger="Add action"
          items={[{ label: 'Add tags', onSelect }]}
        />
        <div onClick={onCard}>
          <AutomationMenu label="More actions" items={[{ label: 'Pause', onSelect }]} />
        </div>
      </>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add action' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add tags' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    onCard.mockClear()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pause' }))
    expect(onSelect).toHaveBeenCalledTimes(2)
    expect(onCard).not.toHaveBeenCalled()
  })
})
