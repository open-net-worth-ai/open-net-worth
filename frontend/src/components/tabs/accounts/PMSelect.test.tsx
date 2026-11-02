// @vitest-environment jsdom
//
// The searchable select keeps its payee wording by default and takes another for other lists.

import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { PMSelect } from './ds'

afterEach(cleanup)

const options = [
  { value: '', label: 'None' },
  { value: 'a', label: 'Alpha' },
]

describe('PMSelect search wording', () => {
  it('says payees by default', () => {
    render(<PMSelect value="" options={options} onChange={() => {}} searchable ariaLabel="Pick" />)
    fireEvent.click(screen.getByRole('button', { name: 'Pick' }))
    fireEvent.change(screen.getByPlaceholderText('Search payees…'), { target: { value: 'zzz' } })
    expect(screen.getByText('No payees match.')).toBeTruthy()
  })

  it('takes the wording of whatever it lists', () => {
    render(
      <PMSelect
        value=""
        options={options}
        onChange={() => {}}
        searchable
        searchPlaceholder="Search categories…"
        emptyText="No category matches."
        ariaLabel="Pick"
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Pick' }))
    fireEvent.change(screen.getByPlaceholderText('Search categories…'), {
      target: { value: 'zzz' },
    })
    expect(screen.getByText('No category matches.')).toBeTruthy()
  })
})
