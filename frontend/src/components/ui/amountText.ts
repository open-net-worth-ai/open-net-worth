/** How MoneyField writes an amount to edit and reads one back — in the user's own number format. */
import { getBaseCurrency, getCurrencySymbol, getNumberLocale } from '../../utils/format'

interface Separators {
  group: string
  decimal: string
}

// The marks the user's number format writes amounts with — read from the same locale `fNum`
// and `fCurrency` format in, so a figure the app displays reads back as itself.
export function numberSeparators(): Separators {
  const parts = new Intl.NumberFormat(getNumberLocale()).formatToParts(1234567.5)
  const mark = (type: 'group' | 'decimal', fallback: string) =>
    parts.find((part) => part.type === type)?.value ?? fallback
  return { group: mark('group', ','), decimal: mark('decimal', '.') }
}

/** Raw digits to edit, with the format's own decimal mark ("1234,5" under the dot format). */
export function amountDraft(value: number | null): string {
  if (value == null) return ''
  return String(Math.round(value * 100) / 100).replace('.', numberSeparators().decimal)
}

const escaped = (mark: string) => mark.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// "1.234.567,89" under the dot format, "1,234,567.89" under the comma one — how the field
// itself displays an amount, so it must be accepted back. Spaces group in every format (never
// a decimal mark in any). Grouping must be whole thousands with one separator throughout, so
// a stray "3500,50" under the comma format is refused rather than read as 350050.
function plainDigits(text: string, { group, decimal }: Separators): string | null {
  const g = escaped(group)
  const d = escaped(decimal)
  // A first group never starts with 0: "0,500" is the other format's 0.5, never 500.
  const grouped = new RegExp(`^[1-9]\\d{0,2}(${g}|\\s)\\d{3}(?:\\1\\d{3})*(?:${d}\\d*)?$`)
  const plain = new RegExp(`^(?:\\d+(?:${d}\\d*)?|${d}\\d+)$`)
  if (!grouped.test(text) && !plain.test(text)) return null
  return text.replace(new RegExp(`${g}|\\s`, 'g'), '').replace(decimal, '.')
}

// "860,000" under the dot format is 860 by its decimal comma — and 860 thousand the way the
// comma format groups. A thousandfold difference is refused, never guessed.
function ambiguous(text: string, { decimal }: Separators): boolean {
  return new RegExp(`^[1-9]\\d{0,2}${escaped(decimal)}\\d{3}$`).test(text)
}

/** How a field's own currency may be typed around its figure, longest first: the symbol it
 *  shows, the base currency's code when that symbol is the base currency's (or it shows none),
 *  and a bare "$" for a dollar. Any other currency is refused, never read as this one —
 *  "¥2,000,000" in an S$ field is not S$2,000,000. */
export function currencyMarks(prefix?: string): string[] {
  const base = getBaseCurrency()
  const baseSymbol = getCurrencySymbol(base)
  const symbol = prefix ?? baseSymbol
  const marks = new Set([symbol.toLowerCase()])
  if (symbol === baseSymbol) marks.add(base.toLowerCase())
  if (symbol.endsWith('$')) marks.add('$')
  return [...marks].filter(Boolean).sort((a, b) => b.length - a.length)
}

// "S$ 820,000", "sgd820000", "820.000 S$" — the field's own currency, before or after the digits.
function withoutOwnCurrency(text: string, marks: string[]): string {
  let t = text.trim()
  const lead = marks.find((mark) => t.toLowerCase().startsWith(mark))
  if (lead) t = t.slice(lead.length).trimStart()
  const trail = marks.find((mark) => t.toLowerCase().endsWith(mark))
  if (trail) t = t.slice(0, -trail.length).trimEnd()
  return t
}
// "860k", "1.2m" — the shorthand people type for thousands and millions.
const SHORTHAND: Record<string, number> = { k: 1e3, m: 1e6 }

/** Reads what someone typed as an amount in their own number format — grouped thousands, the
 *  field's own currency around it, k/m shorthand — or null when it can't be read (never a guess). */
export function parseAmount(text: string, marks: string[] = currencyMarks()): number | null {
  const n = parseExact(text, marks)
  return n == null ? null : Math.round(n * 100) / 100
}

/** parseAmount without the rounding to cents — for a per-unit price, where a coin's 0.00001234
 *  is the figure itself, not dust to round away. */
export function parseExact(text: string, marks: string[] = currencyMarks()): number | null {
  let t = withoutOwnCurrency(text, marks)
  const scale = SHORTHAND[t.slice(-1).toLowerCase()]
  if (scale) t = t.slice(0, -1).trimEnd()
  const seps = numberSeparators()
  const digits = ambiguous(t, seps) ? null : plainDigits(t, seps)
  if (digits == null) return null
  const n = Number(digits) * (scale ?? 1)
  return Number.isFinite(n) ? n : null
}

/** The figure a refused thousandfold entry most likely meant, written in the user's own format
 *  ("17,000" under the dot format → "17.000") — null for any other text. */
export function thousandsReading(text: string, marks: string[] = currencyMarks()): string | null {
  const t = withoutOwnCurrency(text, marks)
  const seps = numberSeparators()
  return ambiguous(t, seps) ? t.replace(seps.decimal, seps.group) : null
}
