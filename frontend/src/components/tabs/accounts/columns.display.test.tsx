// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ColumnsPicker } from './columns'

afterEach(() => cleanup())

function open(props: { hidden?: Set<string>; showHidden?: boolean }) {
  const onReset = vi.fn()
  const onToggleShowHidden = vi.fn()
  render(
    <ColumnsPicker
      showAccount={false}
      hidden={props.hidden ?? new Set()}
      onToggle={vi.fn()}
      onReset={onReset}
      showHidden={props.showHidden ?? false}
      onToggleShowHidden={onToggleShowHidden}
    />,
  )
  fireEvent.click(screen.getByTitle('Display options'))
  return { onReset, onToggleShowHidden }
}

describe('Display picker', () => {
  it('is labelled Display and offers the hidden-rows switch', () => {
    open({})
    expect(screen.getByText('Display')).toBeTruthy()
    expect(screen.getByText('Show hidden transactions')).toBeTruthy()
  })

  it('Reset is disabled while everything is at its default', () => {
    open({})
    expect(screen.getByText('Reset')).toHaveProperty('disabled', true)
  })

  it('Reset restores the columns AND turns Show hidden off', () => {
    const { onReset, onToggleShowHidden } = open({ showHidden: true })
    fireEvent.click(screen.getByText('Reset'))
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(onToggleShowHidden).toHaveBeenCalledTimes(1)
  })
})
