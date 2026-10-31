/**
 * The maskable-money convention. Any element carrying this class masks to `***`
 * under `body.hide-amounts` (privacy mode) via the generic `body.hide-amounts
 * .money` rule in index.css — no per-selector allowlist. Use this helper to add
 * the class to an element that already has its own classes (a `<td>`, a flex
 * cell); for a plain wrapper prefer the <Money> ui primitive. Only the AMOUNT
 * should carry it — keep labels/dates/percentages off the masked element.
 */
export const MONEY_CLASS = 'money'
export const moneyClass = (...extra: (string | false | null | undefined)[]): string =>
  [MONEY_CLASS, ...extra].filter(Boolean).join(' ')

// Base/display currency — set once from profile.base_currency on app load.
let _baseCurrency = 'SGD'

export function setBaseCurrency(code: string): void {
  _baseCurrency = (code || 'SGD').toUpperCase()
}
export const getBaseCurrency = (): string => _baseCurrency

// ── Display formats (profile.date_format / number_format) ────────────────────
// Presentational only: these never change a stored value, they pick how one is WRITTEN.
// Each preference maps to the locale that produces that convention, so Intl keeps doing the
// formatting — we are choosing its locale rather than hand-rolling separators.
export type DateFormat = 'dmy' | 'mdy' | 'iso'
export type NumberFormat = 'comma' | 'space' | 'dot'

const DATE_LOCALES: Record<DateFormat, string> = {
  dmy: 'en-GB', // 31/12/2026
  mdy: 'en-US', // 12/31/2026
  iso: 'sv-SE', // 2026-12-31
}
const NUMBER_LOCALES: Record<NumberFormat, string> = {
  comma: 'en-US', // 1,234,567.89
  space: 'fr-FR', // 1 234 567,89
  dot: 'de-DE', // 1.234.567,89
}

// `undefined` means "follow the browser locale", which is what every screen did before these
// preferences existed — so an unset preference is not a behaviour change.
let _dateLocale: string | undefined
let _dateFormat: DateFormat | undefined
let _numberLocale: string | undefined

export function setDisplayFormats(prefs: {
  date_format?: string | null
  number_format?: string | null
}): void {
  const d = prefs.date_format as DateFormat | null | undefined
  const n = prefs.number_format as NumberFormat | null | undefined
  _dateLocale = d && DATE_LOCALES[d] ? DATE_LOCALES[d] : undefined
  _dateFormat = d && DATE_LOCALES[d] ? d : undefined
  _numberLocale = n && NUMBER_LOCALES[n] ? NUMBER_LOCALES[n] : undefined
}

/** The locale numbers and money are formatted in. `undefined` = the browser's. */
export const getNumberLocale = (): string | undefined => _numberLocale
/** The locale dates are formatted in. `undefined` = the browser's. */
export const getDateLocale = (): string | undefined => _dateLocale
/** The saved numeric-date preference. `undefined` = follow the browser. */
export const getDateFormat = (): DateFormat | undefined => _dateFormat

const NUMERIC_PLACEHOLDER: Record<DateFormat, string> = {
  dmy: 'dd/mm/yyyy',
  mdy: 'mm/dd/yyyy',
  iso: 'yyyy-mm-dd',
}
export const numericDatePlaceholder = (format: DateFormat): string => NUMERIC_PLACEHOLDER[format]

/** A typed numeric date in the user's format → "YYYY-MM-DD", or null while it is incomplete or
 *  not a real calendar day. Separators `/ . -` are all accepted; day and month may be one digit. */
export function parseNumericDate(text: string, format: DateFormat): string | null {
  const re =
    format === 'iso'
      ? /^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})$/
      : /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/
  const m = re.exec(text.trim())
  if (!m) return null
  const [a, b, c] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const [y, mo, d] = format === 'iso' ? [a, b, c] : format === 'mdy' ? [c, a, b] : [c, b, a]
  const probe = new Date(y, mo - 1, d)
  if (probe.getFullYear() !== y || probe.getMonth() !== mo - 1 || probe.getDate() !== d) return null
  return `${String(y).padStart(4, '0')}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** How a date is written: `numeric` is the user's Date-format preference (31/12/2026,
 *  12/31/2026 or 2026-12-31); `short` / `long` spell the month (6 Oct / 6 October). */
export type DateStyle = 'numeric' | 'short' | 'long'
/** `auto` drops the year for a date in the current calendar year. Numeric dates default to
 *  `always` — "10/06" is ambiguous — the month-name styles default to `auto`. */
export type YearMode = 'auto' | 'always' | 'never'

/** A "YYYY-MM-DD" value names a calendar day, so it is read as LOCAL — `new Date(iso)` would
 *  read it as UTC and show the day before in any timezone behind UTC. A full timestamp is an
 *  instant and is shown in the viewer's own timezone. Returns null for an unreadable value. */
export function parseDate(value: string | Date): Date | null {
  let d: Date
  if (value instanceof Date) d = value
  else {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
    d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value)
  }
  return Number.isNaN(d.getTime()) ? null : d
}

// A spelled-out date needs a locale that spells English month names; `iso` (sv-SE) would
// print Swedish ones, so it only governs the numeric style.
const textLocale = (): string | undefined =>
  _dateLocale === DATE_LOCALES.iso ? 'en-GB' : _dateLocale

// en-GB abbreviates September as "Sept"; every other month is three letters, so keep it uniform.
const spellMonth = (text: string): string => text.replace(/\bSept\b/, 'Sep')

/** The ONE way to write a calendar date for display. Follows the user's Date-format
 *  preference and never shows a year the reader already knows (see `YearMode`).
 *  Hours/minutes are `fTime` / `fDateTime`; month-only labels are `fMonth`. */
export function fDate(
  value: string | Date,
  style: DateStyle = 'short',
  year: YearMode = style === 'numeric' ? 'always' : 'auto',
): string {
  const d = parseDate(value)
  if (!d) return typeof value === 'string' ? value : ''
  const showYear =
    year === 'always' || (year === 'auto' && d.getFullYear() !== new Date().getFullYear())
  const y: Intl.DateTimeFormatOptions = showYear ? { year: 'numeric' } : {}
  if (style === 'numeric')
    return d.toLocaleDateString(_dateLocale, { day: '2-digit', month: '2-digit', ...y })
  return spellMonth(
    d.toLocaleDateString(textLocale(), {
      day: 'numeric',
      month: style === 'long' ? 'long' : 'short',
      ...y,
    }),
  )
}

/** "Oct 2026" / "October" from a Date or a "YYYY-MM" key — a month with no day. */
export function fMonth(
  value: string | Date,
  style: Exclude<DateStyle, 'numeric'> = 'short',
  year: YearMode = 'auto',
): string {
  const d = parseDate(/^\d{4}-\d{2}$/.test(String(value)) ? `${value}-01` : value)
  if (!d) return typeof value === 'string' ? value : ''
  const showYear =
    year === 'always' || (year === 'auto' && d.getFullYear() !== new Date().getFullYear())
  return spellMonth(
    d.toLocaleDateString(textLocale(), {
      month: style === 'long' ? 'long' : 'short',
      ...(showYear ? { year: 'numeric' } : {}),
    }),
  )
}

/** A date plus its clock time ("6 Oct • 14:05"). A date-only value has no time to show. */
export function fDateTime(
  value: string | Date,
  style: DateStyle = 'short',
  year?: YearMode,
): string {
  const d = parseDate(value)
  if (!d) return typeof value === 'string' ? value : ''
  const dateOnly = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
  return dateOnly ? fDate(d, style, year) : `${fDate(d, style, year)} • ${fTime(d)}`
}

/** Format an epoch-ms timestamp (or Date) as a clock time, same locale preference as
 *  `fDate` — `Intl` picks 12h/AM-PM vs 24h from the locale itself, never hardcoded.
 *  Hours and minutes only — several messages landing in the same minute display
 *  identically, which is fine; the underlying timestamps stay distinct regardless
 *  (chatReducer.tsAfter) for sort order, this is purely a display choice. */
export function fTime(value: number | Date): string {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString(_dateLocale, { hour: 'numeric', minute: '2-digit' })
}

/** Convert an amount from `ccy` into the base currency using a rate table whose rates
 *  are relative to a single pivot (any pivot — the division cancels it):
 *    amount * rate[ccy] / rate[base]
 *  Mirrors backend ledger_service.to_base. A missing rate passes through 1:1 (and warns)
 *  rather than zeroing the holding. Defaults `base` to the configured base currency. */
export function toBase(
  amount: number,
  ccy: string,
  fx: Record<string, number>,
  base: string = _baseCurrency,
): number {
  const c = (ccy || base).toUpperCase()
  const b = (base || _baseCurrency).toUpperCase()
  if (c === b) return amount
  const curRate = fx[c]
  const baseRate = fx[b]
  if (!curRate || !baseRate) {
    if (import.meta.env?.DEV) console.warn(`toBase: missing FX rate for ${c} or ${b}; passing 1:1`)
    return amount
  }
  return (amount * curRate) / baseRate
}

// Explicit symbols for currencies that Intl might render as an ambiguous bare '$'
// in some environments. All others delegate to Intl which handles 180+ currencies.
const UNAMBIGUOUS: Record<string, string> = {
  SGD: 'S$',
  AUD: 'A$',
  CAD: 'C$',
  HKD: 'HK$',
  NZD: 'NZ$',
}

/** Format any ISO 4217 currency using the browser's Intl API.
 *  Handles symbols, decimal places, and symbol placement for all currencies.
 *  e.g. fCurrency(36, 'SGD') → 'S$36.00', fCurrency(84000, 'VND') → '₫84,000' */
export function fCurrency(
  n: number,
  currency: string,
  opts?: { maximumFractionDigits?: number; minimumFractionDigits?: number },
): string {
  const code = (currency || _baseCurrency).toUpperCase()
  // Normalize -0 → 0 so Intl doesn't render "-€0.00"
  const value = n === 0 ? 0 : n
  try {
    // 'narrowSymbol' yields a short symbol (e.g. SGD → bare '$') regardless of the
    // browser locale; without it, a non-matching locale renders the ISO code ("SGD").
    const fmt = new Intl.NumberFormat(_numberLocale, {
      style: 'currency',
      currency: code,
      currencyDisplay: 'narrowSymbol',
      ...(opts?.maximumFractionDigits !== undefined
        ? { maximumFractionDigits: opts.maximumFractionDigits }
        : {}),
      ...(opts?.minimumFractionDigits !== undefined
        ? { minimumFractionDigits: opts.minimumFractionDigits }
        : {}),
    })
    // Swap the currency part wherever the locale places it (prefix or suffix) so the
    // dollar family stays unambiguous (SGD → S$) in every locale, not just leading-'$' ones.
    if (UNAMBIGUOUS[code]) {
      return fmt
        .formatToParts(value)
        .map((p) => (p.type === 'currency' ? UNAMBIGUOUS[code] : p.value))
        .join('')
    }
    return fmt.format(value)
  } catch {
    return (
      (n < 0 ? '-' : '') + Math.abs(n).toFixed(opts?.maximumFractionDigits ?? 2) + ' ' + currency
    )
  }
}

// Formats an amount in the configured base currency (profile.base_currency).
export const fBase = (n: number): string => fCurrency(n, _baseCurrency)

/** Base-currency amount rounded to whole units (no cents) — for headline figures
 *  (hero gaps, FIRE derivation, income chips) where trailing ".00" is noise. */
export const fBaseWhole = (n: number): string =>
  fCurrency(n, _baseCurrency, { maximumFractionDigits: 0, minimumFractionDigits: 0 })

/** Compact base-currency format for dense tables where full amounts don't fit
 *  (e.g. VND projections in the cash-flow grid). 2,008,437,944 → ₫2.01B, 50,400 → S$50.4K.
 *  Small values (<1000) render in full. Pair with a `title` carrying the exact fBase value. */
export function fBaseCompact(n: number): string {
  const code = _baseCurrency
  const value = n === 0 ? 0 : n
  try {
    const fmt = new Intl.NumberFormat(_numberLocale, {
      style: 'currency',
      currency: code,
      currencyDisplay: 'narrowSymbol',
      notation: 'compact',
      maximumFractionDigits: 2,
    })
    if (UNAMBIGUOUS[code]) {
      return fmt
        .formatToParts(value)
        .map((p) => (p.type === 'currency' ? UNAMBIGUOUS[code] : p.value))
        .join('')
    }
    return fmt.format(value)
  } catch {
    return fBase(n)
  }
}

/**
 * Bare grouped amount — NO currency symbol, exactly 2 decimals, grouped
 * thousands, and a TRUE minus sign U+2212 (−) for negatives (never the ASCII
 * hyphen). For the YNAB budget table, where the currency is implied by the
 * column header / global currency label, so per-row "S$" prefixes are dropped.
 *
 *   fBareAmount(1234.5)  → "1,234.50"
 *   fBareAmount(-89)     → "−89.00"
 *   fBareAmount(0)       → "0.00"   (callers wanting the em-dash sentinel use fFig)
 *
 * Grouping follows the number_format preference and otherwise falls back to 'en-US' — the
 * bare table stays grouping-stable (comma thousands, dot decimal) regardless of BROWSER
 * locale, but an explicit preference still wins. The currency context is supplied separately
 * by the header / summary card.
 *
 * Privacy: the string carries no symbol, but it still masks when placed inside
 * a `.money` element — the `::after` overlay replaces the visible content
 * regardless of the underlying text.
 */
export function fBareAmount(n: number): string {
  const v = n === 0 ? 0 : n
  const abs = Math.abs(v).toLocaleString(_numberLocale ?? 'en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return (v < 0 ? '−' : '') + abs
}

/** Em-dash sentinel for an exact-zero budget figure (rendered in faint ink). */
export const FIG_ZERO = '—' // —

/**
 * Budget-table figure: the bare amount plus the two facts the cell renderer
 * needs to style it. An EXACT zero (|n| < 0.005, i.e. rounds to 0.00) yields
 * the em-dash sentinel "—" and `isZero:true` so the cell paints it faint and
 * never prints "0.00" / "S$0.00". The 0.005 epsilon matches the budget tab's
 * other money comparisons (e.g. matchesFilter).
 *
 *   fFig(0)       → { text: "—",        isZero: true,  isNeg: false }
 *   fFig(1234.5)  → { text: "1,234.50", isZero: false, isNeg: false }
 *   fFig(-89)     → { text: "−89.00",   isZero: false, isNeg: true  }
 */
export function fFig(n: number): { text: string; isZero: boolean; isNeg: boolean } {
  if (Math.abs(n) < 0.005) return { text: FIG_ZERO, isZero: true, isNeg: false }
  return { text: fBareAmount(n), isZero: false, isNeg: n < 0 }
}

export const fNum = (n: number): string => Math.round(n).toLocaleString(_numberLocale)

/** Compact magnitude with a B/M/K suffix, no currency. 451000000000 → "451B",
 *  120234567 → "120.23M". Used for crypto market cap / token supply. */
export function fCompact(n: number, maxFrac = 2): string {
  try {
    return new Intl.NumberFormat(_numberLocale, {
      notation: 'compact',
      maximumFractionDigits: maxFrac,
    }).format(n)
  } catch {
    return fNum(n)
  }
}

/** Extract just the currency symbol for a given ISO 4217 code (uses base currency if omitted). */
export function getCurrencySymbol(currency?: string): string {
  const code = (currency || _baseCurrency).toUpperCase()
  if (UNAMBIGUOUS[code]) return UNAMBIGUOUS[code]
  try {
    const parts = new Intl.NumberFormat(_numberLocale, {
      style: 'currency',
      currency: code,
      currencyDisplay: 'narrowSymbol',
    }).formatToParts(0)
    return parts.find((p) => p.type === 'currency')?.value ?? code
  } catch {
    return code
  }
}

/** Full display name for a currency code (e.g. SGD → "Singapore Dollar"). Falls back to the code. */
export function getCurrencyName(currency?: string): string {
  const code = (currency || _baseCurrency).toUpperCase()
  try {
    return new Intl.DisplayNames(['en'], { type: 'currency' }).of(code) ?? code
  } catch {
    return code
  }
}

/** Format amount with currency-appropriate decimal places, no symbol — for pairing with an ISO code label. */
export function fAmountNative(n: number, currency?: string): string {
  const code = (currency || _baseCurrency).toUpperCase()
  const value = n === 0 ? 0 : n
  try {
    const opts = new Intl.NumberFormat(_numberLocale, {
      style: 'currency',
      currency: code,
    }).resolvedOptions()
    return value.toLocaleString(_numberLocale, {
      minimumFractionDigits: opts.minimumFractionDigits,
      maximumFractionDigits: opts.maximumFractionDigits,
    })
  } catch {
    return n.toFixed(2)
  }
}

/** A per-unit cost with no symbol: the currency's own decimals, except a sub-unit cost that
 *  those decimals would round to zero (SHIB at 0.0000241 → "0.00", as if it were free), which
 *  keeps fPrice's magnitude-adaptive precision. A genuine zero stays "0.00". */
export function fUnitCostNative(n: number, currency?: string): string {
  return n > 0 && n < 1 ? fPrice(n) : fAmountNative(n, currency)
}

/**
 * Format a percentage with EXACTLY 2 decimals — `12.30%`, `0.05%`, `0.00%`.
 *
 * The default input is an already-multiplied percent number (`fPct(12.3)` → "12.30%").
 * Options:
 *  - `fraction:true` — input is a 0–1 fraction (`fPct(0.123, {fraction:true})` → "12.30%").
 *  - `sign:true` — prefix a `+` on non-negative values (drift / P&L; the `-` is intrinsic).
 *
 * null / undefined / NaN → "—". A small non-zero value NEVER rounds to "0%"
 * (0.05% stays "0.05%"); an exact 0 reads "0.00%". 2 decimals always.
 */
export function fPct(
  n: number | null | undefined,
  opts: { fraction?: boolean; sign?: boolean } = {},
): string {
  if (n == null || isNaN(n)) return '—'
  const value = opts.fraction ? n * 100 : n
  const signPrefix = opts.sign && value >= 0 ? '+' : ''
  return signPrefix + value.toFixed(2) + '%'
}

/** The `maxFractionDigits` a price or per-unit cost gets for its asset class: 2 for anything known
 *  not to be crypto (owner, 2026-08-31 and 2026-10-15: "$186.0967" is needless precision for a
 *  share), the adaptive default for crypto and for a row whose class is unknown, so a coin's
 *  "$2.3456" or "$0.0000089" is never rounded away. */
export function priceDigits(assetClass?: string | null): number | undefined {
  return !assetClass || assetClass === 'crypto' ? undefined : 2
}

/** `maxFractionDigits`, when the CALLER passes it (never inferred from the value itself),
 *  caps every bucket below — a caller only passes it for an asset class that doesn't need
 *  sub-cent precision (an ordinary equity; owner ask 2026-08-31: "$186.0967"/"-0.0950" read
 *  as needless precision for PLTR, including its own sub-dollar CHANGE amount, not just the
 *  price). Omitted entirely (crypto, or any other caller that hasn't opted in), the existing
 *  magnitude-adaptive precision is untouched — a $0.0009 token still shows that exactly. */
export function fPrice(p: number | null | undefined, maxFractionDigits?: number): string {
  if (!p) return '—'
  // Always show at least 2 decimals for normal prices (e.g. 37.8 → "37.80"); keep
  // extra precision for sub-dollar / crypto values via the lower branches.
  if (p >= 1000)
    return p.toLocaleString(_numberLocale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (p >= 1)
    return p.toLocaleString(_numberLocale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: maxFractionDigits ?? 4,
    })
  if (maxFractionDigits != null)
    return p.toLocaleString(_numberLocale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: maxFractionDigits,
    })
  // The same fixed precision as before, written with the user's decimal mark ("0,00001234" under
  // the dot format) — toFixed only ever writes a point.
  const fixed = (digits: number) =>
    p.toLocaleString(_numberLocale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
  if (p >= 0.001) return fixed(4)
  if (p >= 0.000001) return fixed(8)
  return p.toExponential(2)
}

/** Per-unit price/cost: currency symbol + magnitude-adaptive precision (fPrice), so a
 *  sub-cent holding (SHIB ≈ $0.0000089) never rounds away to a misleading "$0.00" the way
 *  fCurrency's fixed 2-decimal formatting would. 0 / null / undefined → "—". */
export function fUnitPrice(
  n: number | null | undefined,
  currency?: string,
  maxFractionDigits?: number,
): string {
  if (!n) return '—'
  const sym = getCurrencySymbol(currency)
  // A currency with no real glyph (KWD, BHD…) falls back to its bare ISO code — needs the
  // NON-BREAKING space Intl's own `.format()` uses there ("KWD 1,234.56"), so the code
  // and number never wrap apart, and never "KWD1,234.56".
  const sep = /^[A-Z]{2,4}$/.test(sym) ? ' ' : ''
  return sym + sep + fPrice(n, maxFractionDigits)
}

export function plClass(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return 'muted'
  return n > 0 ? 'pos' : n < 0 ? 'neg' : 'muted'
}

/**
 * Build the sublabel string for a transaction mention row in the `@`-picker and staged chip.
 * Mirrors the backend format from `services/mentions/providers.search_transaction`:
 *   "{payee} · {account} · {date} · {±amount}" — empty parts dropped.
 *
 * Sign convention:
 *   income      → "+" prefix  (cash in)
 *   expense     → "−" prefix  (cash out, true minus U+2212 for display)
 *   other       → no prefix   (transfer / adjustment — net movement unclear from one leg)
 *
 * The amount uses fCurrency for locale-correct symbol and decimal formatting.
 * Pass `amount` as its absolute value; the sign is derived from `kind`.
 */
export function txnMentionSublabel({
  kind,
  payeeName,
  accountName,
  date,
  amount,
  currency,
  signed,
}: {
  kind: string
  payeeName?: string | null
  accountName?: string | null
  date: string
  amount: number
  currency: string
  /** Signed leg amount. When given, the sign comes from it (so a transfer reads −/+ per the
   *  leg, like the transaction list); otherwise it falls back to kind. */
  signed?: number
}): string {
  const sign =
    signed != null && signed !== 0
      ? signed < 0
        ? '−'
        : '+'
      : kind === 'income'
        ? '+'
        : kind === 'expense' || kind === 'investment'
          ? '−'
          : ''
  const amtStr = sign + fCurrency(Math.abs(amount), currency)
  const parts = [payeeName || '', accountName || '', date, amtStr].filter(Boolean)
  return parts.join(' • ')
}

/** Short, year-less date for a compact chip: "2026-06-02" → "Jun 2". Pure string parse
 *  (no Date/timezone). Falls back to the input when it isn't an ISO yyyy-mm-dd. */
export function fShortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return iso
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ]
  const month = months[parseInt(m[2]!, 10) - 1]
  if (!month) return iso
  return `${month} ${parseInt(m[3]!, 10)}`
}

export const fVND = (m: number): string =>
  '₫' + m.toLocaleString(_numberLocale, { maximumFractionDigits: 3 }) + 'M'

export function fDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600 || seconds % 3600 !== 0) return `${Math.round(seconds / 60)}m`
  return `${seconds / 3600}h`
}

export function schedItemBase(
  item: { vndM?: number; sgd?: number },
  fx: Record<string, number>,
): number {
  if (item.vndM != null) return toBase(item.vndM * 1e6, 'VND', fx)
  return item.sgd || 0
}

/**
 * Validate that a URL uses http or https. Returns the URL string if safe,
 * or null if the scheme is absent, javascript:, data:, or anything else.
 * Used to guard <a href> attributes before rendering untrusted URLs from
 * search results / LLM output.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  return /^https?:\/\//i.test(url.trim()) ? url : null
}

/**
 * Favicon URL for a news item, shared by NewsCardGrid + LatestDevelopments.
 * Prefers the publisher `domain` (resolved from the RSS <source url>) — for a Google-News-wrapped
 * link the article host is "news.google.com", so the domain gives the real publisher's favicon.
 * Only a clean host label is used (a malformed feed-supplied domain falls back to the
 * browser-parsed, always-clean article host).
 *
 * Routes through our own `/api/favicon` proxy (ADR 0025): the server resolves the site's declared
 * icon and caches it, so we get a reliable icon WITHOUT the browser beaconing the publisher to a
 * third party (Google/DuckDuckGo). Same-origin → the auth cookie rides along; a miss 404s and the
 * Favicon component shows its blue fallback.
 */
export function faviconUrl(url: string, domain?: string): string {
  const host = (domain || '').trim()
  let resolved = ''
  if (host && /^[a-z0-9.-]+$/i.test(host)) {
    resolved = host
  } else {
    try {
      resolved = new URL(url).hostname
    } catch {
      return ''
    }
  }
  return resolved ? `/api/favicon?domain=${encodeURIComponent(resolved.toLowerCase())}` : ''
}

/**
 * Relative time from an ISO timestamp string.
 * Shared by NewsCardGrid, LatestDevelopments, and any other component
 * that needs a "Xm ago" / "yesterday" / "Xd ago" label.
 */
export function timeAgo(iso: string): string {
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return `${s}s ago`
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    const days = Math.floor(h / 24)
    if (days === 1) return 'yesterday'
    if (days < 7) return `${days}d ago`
    const w = Math.floor(days / 7)
    if (w < 5) return `${w}w ago`
    const months = Math.floor(days / 30)
    if (months < 12) return `${months}mo ago`
    return `${Math.floor(days / 365)}y ago`
  } catch {
    return ''
  }
}

/**
 * A news item's timestamp: relative ("8h ago") inside the last 24h — genuinely more useful at
 * that scale than a bare date — and an absolute date ("Aug 28, 2026") beyond that. Vague
 * relative fuzz ("yesterday", "1w ago", "2w ago") for anything older reads worse than a real
 * date once the reader can no longer place it precisely. Reuses `timeAgo`'s own sub-day
 * formatting for the relative branch rather than a second implementation.
 */
export function newsTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const hours = (Date.now() - d.getTime()) / 3_600_000
  if (hours < 24) return timeAgo(iso)
  return fDate(d)
}

/**
 * A future instant as a day-relative label — "Today at 21:03", "Tomorrow at 8:31",
 * "Mon, 24 Aug at 09:00" beyond that. For the Lucius landing screen's Activity panel
 * (Scheduled section's next-run time) — `timeAgo` above is PAST-relative ("3h ago") and
 * reads wrong for a time that hasn't happened yet.
 */
export function fScheduleWhen(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  const now = new Date()
  if (sameDay(d, now)) return `Today at ${time}`
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  if (sameDay(d, tomorrow)) return `Tomorrow at ${time}`
  return `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })} at ${time}`
}

/**
 * The LOCAL calendar date as YYYY-MM-DD — for "today" defaults (add-row date, txn-create
 * fallback). Deliberately NOT `toISOString().slice(0, 10)`: that returns the UTC date, which is
 * a day behind local between local midnight and ~07:00 in any UTC-ahead timezone.
 *
 * NOTE: components/tabs/accounts/datePresets.ts (a separate in-flight lane) adds its own
 * `localIso` — dedupe onto this one when that lane lands.
 */
export function localIso(d: Date = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * Unicode flag emoji from a 2-letter ISO 3166-1 alpha-2 country code.
 * Returns '' for any other input (unknown, missing, or non-2-char code).
 */
export function countryFlag(code: string | null | undefined): string {
  if (!code || code.length !== 2) return ''
  const offset = 0x1f1e6 - 65
  return String.fromCodePoint(
    code.toUpperCase().charCodeAt(0) + offset,
    code.toUpperCase().charCodeAt(1) + offset,
  )
}

/** Plain quantity — keeps fractional units (crypto, partial shares) intact, unlike fNum
 * which rounds to an integer. */
export function fQty(n: number | string | null | undefined): string {
  if (n == null || n === '') return '—'
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 8 })
}
