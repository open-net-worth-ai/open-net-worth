// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { setDisplayFormats } from '../../../utils/format'
import { fmtDay } from './TxnDetail'
import { AddDateField } from './pickers'

afterEach(() => setDisplayFormats({ date_format: null }))

describe('transaction drawer dates follow the Date-format setting', () => {
  it.each([
    ['dmy', '31/12/2026'],
    ['mdy', '12/31/2026'],
    ['iso', '2026-12-31'],
  ])('fmtDay in %s', (fmt, expected) => {
    setDisplayFormats({ date_format: fmt })
    expect(fmtDay('2026-12-31')).toBe(expected)
  })

  it.each([
    ['dmy', '31/12/2026'],
    ['iso', '2026-12-31'],
  ])('date picker label in %s', (fmt, expected) => {
    setDisplayFormats({ date_format: fmt })
    render(<AddDateField value="2026-12-31" onChange={() => {}} advance={() => {}} />)
    expect(screen.getByText(expected)).toBeTruthy()
  })
})
