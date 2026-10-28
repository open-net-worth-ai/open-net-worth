// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { InvestmentThesis } from '../../types/assetDetail'
import { setDisplayFormats } from '../../utils/format'

const saveInvestmentThesis = vi.fn()
vi.mock('../../api/investmentTheses', () => ({
  saveInvestmentThesis: (...args: unknown[]) => saveInvestmentThesis(...args),
}))

import { ThesisEditor } from './ThesisEditor'
import { formatPrice, groupPrice, parsePrice, priceText } from './thesisFields'

// Thesis prices are per share or per coin: they follow the user's number format (comma, dot or
// space — Settings → Display) and the ticker's currency symbol, and are never rounded to cents.

const NNBSP = ' ' // fr-FR's thousands mark, which the space format writes

afterEach(() => {
  cleanup()
  saveInvestmentThesis.mockReset()
  setDisplayFormats({})
})

const FORMATS = [
  {
    name: 'comma',
    big: '232,323,223',
    mixed: '1,234.5',
    tiny: '0.00001234',
    btc: '65,000.5',
  },
  {
    name: 'dot',
    big: '232.323.223',
    mixed: '1.234,5',
    tiny: '0,00001234',
    btc: '65.000,5',
  },
  {
    name: 'space',
    big: `232${NNBSP}323${NNBSP}223`,
    mixed: `1${NNBSP}234,5`,
    tiny: '0,00001234',
    btc: `65${NNBSP}000,5`,
  },
] as const

describe.each(FORMATS)('thesis prices under the $name number format', (f) => {
  const use = () => setDisplayFormats({ number_format: f.name })

  it('writes a stored price the way the format does, every decimal kept', () => {
    use()
    expect(formatPrice(232323223)).toBe(f.big)
    expect(formatPrice(1234.5)).toBe(f.mixed)
    expect(formatPrice(0.00001234)).toBe(f.tiny)
    expect(formatPrice(65000.5)).toBe(f.btc)
  })

  it('reads back exactly what it writes — no rounding to cents', () => {
    use()
    for (const v of [0.00001234, 0.000000412, 0.4567, 1, 18.5, 1234.5, 65000.5, 232323223]) {
      expect(parsePrice(formatPrice(v))).toBe(v)
    }
  })

  it('reads the format’s own grouping, and plain digits', () => {
    use()
    expect(parsePrice(f.big)).toBe(232323223)
    expect(parsePrice(f.mixed)).toBe(1234.5)
    expect(parsePrice(f.tiny)).toBe(0.00001234)
    expect(parsePrice('232323223')).toBe(232323223)
  })

  it('groups a typed price once the box is left', () => {
    use()
    expect(groupPrice('232323223')).toBe(f.big)
    expect(groupPrice(f.tiny)).toBe(f.tiny)
  })

  it('refuses zero, blanks and nonsense rather than guessing', () => {
    use()
    expect(parsePrice('')).toBeNull()
    expect(parsePrice('0')).toBeNull()
    expect(parsePrice('abc')).toBeNull()
  })
})

describe('format-specific reading', () => {
  it('comma: a European-style "1.234,5" is refused, never read as 1.2345 or 12345', () => {
    setDisplayFormats({ number_format: 'comma' })
    expect(parsePrice('1.234,5')).toBeNull()
    expect(parsePrice('3500,50')).toBeNull()
    expect(parsePrice('860,000')).toBe(860000)
  })

  it('dot: "860,000" is refused as a thousandfold ambiguity, and a dot-decimal is not a decimal', () => {
    setDisplayFormats({ number_format: 'dot' })
    expect(parsePrice('860,000')).toBeNull()
    expect(parsePrice('0.5')).toBeNull()
    expect(parsePrice('0,5')).toBe(0.5)
  })

  it('space: an ordinary typed space groups too', () => {
    setDisplayFormats({ number_format: 'space' })
    expect(parsePrice('232 323 223')).toBe(232323223)
    expect(parsePrice('1 234,5')).toBe(1234.5)
  })

  it('a keystroke filter keeps only the format’s marks and one decimal mark', () => {
    setDisplayFormats({ number_format: 'comma' })
    expect(priceText('$1,2a34.5.6')).toBe('1,234.56')
    setDisplayFormats({ number_format: 'dot' })
    expect(priceText('€1.2a34,5,6')).toBe('1.234,56')
  })
})

const THESIS: InvestmentThesis = {
  id: 'th_1',
  instrument_id: 'inst_x',
  role: 'speculative',
  reason: null,
  breaks_if: null,
  stop_below: 0.00001234,
  buy_below: null,
  buy_above: null,
  fair_value: 232323223,
  trim_above: null,
  created_at: '2026-10-29T00:00:00Z',
  updated_at: '2026-10-29T00:00:00Z',
}

function editor(currency: string, thesis: InvestmentThesis | null = THESIS) {
  saveInvestmentThesis.mockImplementation(async (_id: string, fields: object) => ({
    ...THESIS,
    ...fields,
  }))
  const onSaved = vi.fn()
  const r = render(
    <ThesisEditor
      instrumentId="inst_x"
      thesis={thesis}
      held
      currency={currency}
      onSaved={onSaved}
      onCancel={vi.fn()}
    />,
  )
  const box = (label: string) => r.getByLabelText(label) as HTMLInputElement
  const symbolOf = (label: string) =>
    box(label).closest('.ui-money-field')?.querySelector('.ui-money-field__prefix')?.textContent
  return { ...r, box, symbolOf, onSaved }
}

describe('ThesisEditor — the ticker’s currency and the user’s number format together', () => {
  it.each([
    ['USD', '$'],
    ['EUR', '€'],
    ['GBP', '£'],
    ['JPY', '¥'],
  ])('a %s ticker shows %s inside every price box', (currency, symbol) => {
    const { symbolOf } = editor(currency)
    for (const label of [
      'Stop below',
      'Trim above',
      'Buy at or under',
      '…but not under',
      'Fair value',
    ])
      expect(symbolOf(label)).toBe(symbol)
  })

  it.each(FORMATS)('$name: opens stored prices in the format, tiny coin price intact', (f) => {
    setDisplayFormats({ number_format: f.name })
    const { box } = editor('USD')
    expect(box('Stop below').value).toBe(f.tiny)
    expect(box('Fair value').value).toBe(f.big)
  })

  it.each(FORMATS)(
    '$name: a price typed in the format is grouped on leaving and saved exactly',
    async (f) => {
      setDisplayFormats({ number_format: f.name })
      const { box, getByText } = editor('USD', null)
      fireEvent.change(box('Trim above'), { target: { value: '65000' } })
      fireEvent.blur(box('Trim above'))
      expect(box('Trim above').value).toBe(formatPrice(65000))
      fireEvent.change(box('Stop below'), { target: { value: f.tiny } })
      fireEvent.blur(box('Stop below'))
      fireEvent.change(box('Fair value'), { target: { value: f.mixed } })
      fireEvent.click(getByText('Save thesis'))
      await waitFor(() => expect(saveInvestmentThesis).toHaveBeenCalled())
      const sent = saveInvestmentThesis.mock.calls[0]?.[1]
      expect(sent?.trim_above).toBe(65000)
      expect(sent?.stop_below).toBe(0.00001234)
      expect(sent?.fair_value).toBe(1234.5)
    },
  )

  it('dot: a habit-typed "0.5" is flagged, not saved as 5', () => {
    setDisplayFormats({ number_format: 'dot' })
    const { box, getByText } = editor('USD', null)
    fireEvent.change(box('Stop below'), { target: { value: '0.5' } })
    expect(getByText('Stop below must be a positive number.')).toBeTruthy()
    fireEvent.click(getByText('Save thesis'))
    expect(saveInvestmentThesis).not.toHaveBeenCalled()
  })
})
