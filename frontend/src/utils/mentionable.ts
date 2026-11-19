import React from 'react'
import { fDate, fShortDate } from './format'

// Detect whether a string is a financial amount, balance, holding quantity, or share formula
// that should be masked under privacy mode (body.hide-amounts).
export function isMaskableAmountOrQuantity(text: string): boolean {
  if (!text) return false
  const s = text.trim()
  if (!s) return false

  // 1. Exclude ISO dates: yyyy-mm-dd or yyyy/mm/dd or dd/mm/yyyy
  if (/^\d{4}[-/]\d{2}[-/]\d{2}$/.test(s) || /^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$/.test(s)) {
    return false
  }

  // 2. Exclude short dates: "Jun 2", "May 15", "Jan 01", "2 Jun", "15 May"
  const months = 'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec'
  if (
    new RegExp(`^(?:${months})\\s+\\d{1,2}$`, 'i').test(s) ||
    new RegExp(`^\\d{1,2}\\s+(?:${months})$`, 'i').test(s)
  ) {
    return false
  }

  // 3. Exclude time spans & durations: "1Y", "30d", "24h", "6m", "5y", "30s", "12mo"
  if (/^\d+\s*(?:d|w|m|mo|mos|y|yr|yrs|h|hr|hrs|min|mins|s|sec|secs)$/i.test(s)) {
    return false
  }

  // 4. Exclude key-value selectors or technical identifiers: "asset_class:equity", "type:account", "cat_..."
  if (s.includes(':') || /^cat_[0-9a-f]+$/i.test(s)) {
    return false
  }

  // 5. Holding formulas / share pricing expressions:
  // e.g. "100 shares @ $150 USD", "10 shares @ $120.50", "0.5 BTC @ $60,000 USD", "50 units @ 10 SGD"
  if (/^[\d,.]+\s*(?:shares|units|[A-Za-z]+)?\s*@\s*.+$/i.test(s)) {
    return true
  }

  // 6. Holding quantities: "100 shares", "50 units", "25.5 shares", "0.05 units"
  if (/^[+-−]?\s*[\d,]+(?:\.\d+)?\s*(?:shares|units)$/i.test(s)) {
    return true
  }

  // Supported currencies (symbols or standard 3/4-letter codes)
  const currList =
    'USD|SGD|EUR|GBP|JPY|AUD|CAD|CHF|CNY|HKD|NZD|SEK|KRW|NOK|MXN|INR|RUB|ZAR|TRY|BRL|TWD|DKK|PLN|THB|IDR|HUF|CZK|ILS|CLP|PHP|AED|COP|SAR|MYR|RON|VND|ARS|BTC|ETH|SOL|USDT|USDC'
  const currSymbols = `(?:[A-Za-z]{1,3}\\$|[$€£¥₩₹₽฿₫đ]|${currList})`

  // 7. Prefixed currency symbol or code:
  // "$150.00", "S$1,234.56", "SGD 14,710", "+$5,800 USD", "−S$270.00", "+đ30,000,000"
  const prefixCurr = new RegExp(
    `^[+-−]?\\s*${currSymbols}\\s*[+-−]?\\s*[\\d,]+(?:\\.\\d+)?(?:\\s*(?:${currList}))?$`,
    'i',
  )
  if (prefixCurr.test(s)) {
    return true
  }

  // 8. Suffixed currency symbol or code:
  // "-18.50 SGD", "+14200 SGD", "12,345 SGD", "100 USD", "50.00 EUR", "12.50 SGD"
  const suffixCurr = new RegExp(`^[+-−]?\\s*[\\d,]+(?:\\.\\d+)?\\s*${currSymbols}$`, 'i')
  if (suffixCurr.test(s)) {
    return true
  }

  // 9. Explicit signed amounts: "+100.00", "-50.00", "−12.50", "+14200", "-18.50"
  if (/^[+-−]\s*[\d,]+(?:\.\d+)?$/.test(s)) {
    return true
  }

  // 10. Decimal / crypto quantities or formatted pure numbers with decimal places:
  // e.g. "0.0273087", "1.4451210543637991", "0.50", "12,345.67"
  if (/^[\d,]+\.\d+$/.test(s)) {
    return true
  }

  // 11. Pure formatted numbers with commas (e.g. "12,345", "1,000,000")
  if (/^\d{1,3}(?:,\d{3})+$/.test(s)) {
    return true
  }

  return false
}

// Render a sublabel string, wrapping detected amount/quantity segments in <span className="money">
// while keeping entity labels, account names, and dates visible as plain text.
export function renderMaskableSublabel(sublabel?: string | null): React.ReactNode {
  if (!sublabel) return sublabel ?? null
  const parts = sublabel.split(' • ')
  if (parts.length === 1) {
    if (isMaskableAmountOrQuantity(sublabel)) {
      return React.createElement('span', { className: 'money' }, sublabel)
    }
    return sublabel
  }

  const nodes: React.ReactNode[] = []
  parts.forEach((part, i) => {
    if (i > 0) {
      nodes.push(' • ')
    }
    if (isMaskableAmountOrQuantity(part)) {
      nodes.push(React.createElement('span', { key: i, className: 'money' }, part))
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(part)) {
      nodes.push(fDate(part, 'short', 'always'))
    } else {
      nodes.push(part)
    }
  })
  return nodes
}

// Spread onto a data row so the Lucius @ picker can harvest it as an "on screen" entity.
// Pass `search` to make the row findable by anything visible beyond the short chip label
// (e.g. full instrument name, account name). The picker matches against it as an additional
// substring target; omitting it leaves existing label-only behaviour unchanged.
// Pass `sublabel` to give the picker row a rich second line (payee · account · date · amount)
// immediately from the DOM, without waiting for the 150ms-debounced backend response.
// Pass `color` to emit a data-entity-color attribute for account chips so the glyph is tinted
// to match the account's chosen color. Must be a safe CSS color value.
export function entityRefProps(
  type: string,
  value: string,
  label: string,
  search?: string,
  sublabel?: string,
  color?: string,
) {
  const base = { 'data-entity-id': `${type}:${value}`, 'data-entity-label': label }
  const withSub = sublabel ? { ...base, 'data-entity-sublabel': sublabel } : base
  const withColor = color ? { ...withSub, 'data-entity-color': color } : withSub
  if (!search) return withColor
  return { ...withColor, 'data-entity-search': search }
}

// Parsed transaction decorations extracted from a sublabel string.
export interface TransactionChipFields {
  chipDate?: string
  chipAmt?: string
  chipAmtNeg?: boolean
}

// Parse the date and signed amount from a transaction sublabel.
// Sublabel format: "{payee} · {account} · {date} · {signed amount}" where
// signed amount starts with +/- (e.g. "-184.20 SGD" or "+14200 SGD").
// Used by both the picker's handlePick and the drag parseRef so they never drift.
export function parseTransactionSublabel(sublabel: string): TransactionChipFields {
  if (!sublabel) return {}
  const parts = sublabel.split(' • ')
  const result: TransactionChipFields = {}
  const amtPart = parts[parts.length - 1] ?? ''
  // Accept ASCII '-' AND the typographic U+2212 minus that txnMentionSublabel emits — otherwise
  // expense/transfer amounts (which use U+2212) never extract and the chip shows no amount.
  if (/^[+−-]/.test(amtPart)) {
    result.chipAmt = amtPart
    result.chipAmtNeg = /^[−-]/.test(amtPart)
  }
  for (let i = parts.length - 2; i >= 0; i--) {
    const p = parts[i]?.trim() ?? ''
    if (/^\d{4}-\d{2}-\d{2}$/.test(p)) {
      // Compact, year-less date on the chip ("Jun 2"); the full date stays in the popover.
      result.chipDate = fShortDate(p)
      break
    }
  }
  return result
}

// Validate that a color string is a safe CSS color value before injecting into style.
// Accepts: #rgb, #rrggbb, #rgba, #rrggbbaa, CSS var(--token), named CSS colors.
// Rejects everything else (arbitrary strings, javascript:, etc.).
export function isSafeCssColor(value: string): boolean {
  if (!value) return false
  return (
    /^#[0-9a-fA-F]{3,8}$/.test(value) ||
    /^var\(--[a-z0-9-]+\)$/.test(value) ||
    /^[a-zA-Z]{2,30}$/.test(value)
  )
}
