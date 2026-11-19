// @vitest-environment jsdom
//
// Tests for the shared mentionable utilities introduced in the chip color + data completeness
// feature: parseTransactionSublabel (Part C shared helper) and isSafeCssColor (Part B guard).

import React, { type ReactElement } from 'react'
import { describe, it, expect } from 'vitest'
import {
  parseTransactionSublabel,
  isSafeCssColor,
  isMaskableAmountOrQuantity,
  renderMaskableSublabel,
} from './mentionable'

// ── parseTransactionSublabel (Part C) ────────────────────────────────────────

describe('parseTransactionSublabel', () => {
  it('returns empty object for an empty string', () => {
    expect(parseTransactionSublabel('')).toEqual({})
  })

  it('extracts chipAmt and chipAmtNeg=true from a signed-negative sublabel', () => {
    const result = parseTransactionSublabel('Grab • Meridian Bank • 2024-06-15 • -12.50 SGD')
    expect(result.chipAmt).toBe('-12.50 SGD')
    expect(result.chipAmtNeg).toBe(true)
    expect(result.chipDate).toBe('Jun 15')
  })

  it('extracts chipAmt and chipAmtNeg=false from a signed-positive sublabel', () => {
    const result = parseTransactionSublabel('Anthropic • Lighthouse Bank • 2026-05-01 • +8000 SGD')
    expect(result.chipAmt).toBe('+8000 SGD')
    expect(result.chipAmtNeg).toBe(false)
    expect(result.chipDate).toBe('May 1')
  })

  it('handles a sublabel with no date part gracefully (chipDate stays undefined)', () => {
    const result = parseTransactionSublabel('Grab • Meridian Bank • -12.50 SGD')
    expect(result.chipAmt).toBe('-12.50 SGD')
    expect(result.chipAmtNeg).toBe(true)
    expect(result.chipDate).toBeUndefined()
  })

  it('returns no chipAmt when the last part has no sign prefix', () => {
    const result = parseTransactionSublabel('Grab • Meridian Bank • 2024-06-15 • 12.50 SGD')
    expect(result.chipAmt).toBeUndefined()
    expect(result.chipAmtNeg).toBeUndefined()
  })

  it('ignores malformed sublabels (no separator)', () => {
    const result = parseTransactionSublabel('noseparatorhere')
    // Single-part sublabel: would be treated as the amount part; no sign → no chipAmt
    expect(result.chipAmt).toBeUndefined()
  })

  it('is consistent with handlePick parse logic for mixed sublabels', () => {
    // Simulate a sublabel with an extra payee segment (4 parts)
    const sublabel = 'Shopee • Shopee Food • Meridian Bank Debit • 2026-05-15 • -34.50 SGD'
    const result = parseTransactionSublabel(sublabel)
    expect(result.chipAmt).toBe('-34.50 SGD')
    expect(result.chipAmtNeg).toBe(true)
    expect(result.chipDate).toBe('May 15')
  })
})

// ── isSafeCssColor (Part B security guard) ───────────────────────────────────

describe('isSafeCssColor', () => {
  it('accepts 6-digit hex colors', () => {
    expect(isSafeCssColor('#ff5733')).toBe(true)
    expect(isSafeCssColor('#AABBCC')).toBe(true)
  })

  it('accepts 3-digit hex colors', () => {
    expect(isSafeCssColor('#f57')).toBe(true)
    expect(isSafeCssColor('#ABC')).toBe(true)
  })

  it('accepts 8-digit hex colors (with alpha)', () => {
    expect(isSafeCssColor('#ff573380')).toBe(true)
  })

  it('accepts CSS var() tokens with the --token pattern', () => {
    expect(isSafeCssColor('var(--lucius)')).toBe(true)
    expect(isSafeCssColor('var(--good)')).toBe(true)
    expect(isSafeCssColor('var(--some-long-token-name)')).toBe(true)
  })

  it('accepts short named CSS colors (alphabetic-only)', () => {
    expect(isSafeCssColor('red')).toBe(true)
    expect(isSafeCssColor('blue')).toBe(true)
    expect(isSafeCssColor('transparent')).toBe(true)
  })

  it('rejects expression() payloads', () => {
    expect(isSafeCssColor('expression(alert(1))')).toBe(false)
  })

  it('rejects javascript: payloads', () => {
    expect(isSafeCssColor('javascript:alert(1)')).toBe(false)
  })

  it('rejects arbitrary strings with spaces', () => {
    expect(isSafeCssColor('rgb(255, 87, 51)')).toBe(false)
  })

  it('rejects empty string', () => {
    expect(isSafeCssColor('')).toBe(false)
  })

  it('rejects a hash with invalid characters', () => {
    expect(isSafeCssColor('#gggggg')).toBe(false)
  })

  it('rejects var() without the -- prefix', () => {
    expect(isSafeCssColor('var(lucius)')).toBe(false)
  })
})

// ── isMaskableAmountOrQuantity & renderMaskableSublabel ─────────────────────

describe('isMaskableAmountOrQuantity', () => {
  it('recognizes currency-prefixed amounts', () => {
    expect(isMaskableAmountOrQuantity('SGD 14,710')).toBe(true)
    expect(isMaskableAmountOrQuantity('S$1,234.56')).toBe(true)
    expect(isMaskableAmountOrQuantity('$150.00')).toBe(true)
    expect(isMaskableAmountOrQuantity('+$5,800 USD')).toBe(true)
    expect(isMaskableAmountOrQuantity('−S$270.00')).toBe(true)
    expect(isMaskableAmountOrQuantity('−$1,155.00')).toBe(true)
    expect(isMaskableAmountOrQuantity('+đ30,000,000')).toBe(true)
    expect(isMaskableAmountOrQuantity('€100')).toBe(true)
    expect(isMaskableAmountOrQuantity('¥500')).toBe(true)
    expect(isMaskableAmountOrQuantity('£50')).toBe(true)
  })

  it('recognizes currency-suffixed amounts', () => {
    expect(isMaskableAmountOrQuantity('-18.50 SGD')).toBe(true)
    expect(isMaskableAmountOrQuantity('+14200 SGD')).toBe(true)
    expect(isMaskableAmountOrQuantity('12,345 SGD')).toBe(true)
    expect(isMaskableAmountOrQuantity('100 USD')).toBe(true)
    expect(isMaskableAmountOrQuantity('50.00 EUR')).toBe(true)
    expect(isMaskableAmountOrQuantity('−34.50 SGD')).toBe(true)
    expect(isMaskableAmountOrQuantity('−20.4 SGD')).toBe(true)
  })

  it('recognizes signed amounts and numeric quantities', () => {
    expect(isMaskableAmountOrQuantity('+100.00')).toBe(true)
    expect(isMaskableAmountOrQuantity('-50.00')).toBe(true)
    expect(isMaskableAmountOrQuantity('−12.50')).toBe(true)
    expect(isMaskableAmountOrQuantity('0.0273087')).toBe(true)
    expect(isMaskableAmountOrQuantity('1.4451210543637991')).toBe(true)
    expect(isMaskableAmountOrQuantity('12,345')).toBe(true)
  })

  it('recognizes holding share formulas and quantities', () => {
    expect(isMaskableAmountOrQuantity('100 shares')).toBe(true)
    expect(isMaskableAmountOrQuantity('50 units')).toBe(true)
    expect(isMaskableAmountOrQuantity('25.5 shares')).toBe(true)
    expect(isMaskableAmountOrQuantity('100 shares @ $150 USD')).toBe(true)
    expect(isMaskableAmountOrQuantity('10 shares @ $120.50')).toBe(true)
    expect(isMaskableAmountOrQuantity('0.5 BTC @ $60,000 USD')).toBe(true)
  })

  it('excludes dates, time spans, entity labels, and technical selectors', () => {
    expect(isMaskableAmountOrQuantity('2026-08-30')).toBe(false)
    expect(isMaskableAmountOrQuantity('2024-06-15')).toBe(false)
    expect(isMaskableAmountOrQuantity('Jun 2')).toBe(false)
    expect(isMaskableAmountOrQuantity('May 15')).toBe(false)
    expect(isMaskableAmountOrQuantity('1Y')).toBe(false)
    expect(isMaskableAmountOrQuantity('30d')).toBe(false)
    expect(isMaskableAmountOrQuantity('24h')).toBe(false)
    expect(isMaskableAmountOrQuantity('6m')).toBe(false)
    expect(isMaskableAmountOrQuantity('5y')).toBe(false)
    expect(isMaskableAmountOrQuantity('OCBC 360')).toBe(false)
    expect(isMaskableAmountOrQuantity('IBKR U7989526')).toBe(false)
    expect(isMaskableAmountOrQuantity('Maybank 2 Cards')).toBe(false)
    expect(isMaskableAmountOrQuantity('Gemini')).toBe(false)
    expect(isMaskableAmountOrQuantity('Coinbase')).toBe(false)
    expect(isMaskableAmountOrQuantity('Grab')).toBe(false)
    expect(isMaskableAmountOrQuantity('Skill • on')).toBe(false)
    expect(isMaskableAmountOrQuantity('asset_class:equity')).toBe(false)
    expect(isMaskableAmountOrQuantity('cat_123456789abc')).toBe(false)
    expect(isMaskableAmountOrQuantity('')).toBe(false)
  })
})

describe('renderMaskableSublabel', () => {
  it('returns null/empty input unchanged', () => {
    expect(renderMaskableSublabel(null)).toBeNull()
    expect(renderMaskableSublabel('')).toBe('')
  })

  it('wraps a single balance in .money', () => {
    const node = renderMaskableSublabel('12,345 SGD') as ReactElement<{
      className?: string
      children?: React.ReactNode
    }>
    expect(node?.props?.className).toBe('money')
    expect(node?.props?.children).toBe('12,345 SGD')
  })

  it('masks only the financial segments of composite sublabels', () => {
    const nodes = renderMaskableSublabel('SGD 14,710 • Gemini') as React.ReactNode[]
    expect(Array.isArray(nodes)).toBe(true)
    const [amtNode, sep, entity] = nodes as [
      ReactElement<{ className?: string; children?: React.ReactNode }>,
      string,
      string,
    ]
    expect(amtNode.props.className).toBe('money')
    expect(amtNode.props.children).toBe('SGD 14,710')
    expect(sep).toBe(' • ')
    expect(entity).toBe('Gemini')
  })

  it('masks transaction sublabels keeping payee, account, and date plain text', () => {
    const nodes = renderMaskableSublabel(
      'Grab • OCBC 360 • 2026-08-30 • -18.50 SGD',
    ) as React.ReactNode[]
    expect(Array.isArray(nodes)).toBe(true)
    expect(nodes[0]).toBe('Grab')
    expect(nodes[1]).toBe(' • ')
    expect(nodes[2]).toBe('OCBC 360')
    expect(nodes[3]).toBe(' • ')
    expect(nodes[4]).toBe('Aug 30, 2026')
    expect(nodes[5]).toBe(' • ')
    const last = nodes[6] as ReactElement<{ className?: string; children?: React.ReactNode }>
    expect(last.props.className).toBe('money')
    expect(last.props.children).toBe('-18.50 SGD')
  })
})
