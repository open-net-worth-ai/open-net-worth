import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  toBase,
  setBaseCurrency,
  setDisplayFormats,
  fPct,
  fBareAmount,
  fFig,
  FIG_ZERO,
  fBase,
  fBaseWhole,
  fBaseCompact,
  fPrice,
  fUnitPrice,
  fCompact,
  txnMentionSublabel,
  localIso,
  newsTime,
  fDate,
  fMonth,
  fDateTime,
  parseNumericDate,
  getDateFormat,
  priceDigits,
} from './format'

describe('fBaseCompact — canonical compact base-currency formatter', () => {
  it('formats a comma-locale VND billion with the correct compact magnitude', () => {
    setBaseCurrency('VND')
    setDisplayFormats({ number_format: 'comma' })

    expect(fBaseCompact(2_008_437_944)).toBe('₫2.01B')
  })
})

describe('fCompact — B/M/K magnitude suffix (crypto market cap / supply)', () => {
  it('abbreviates billions and millions', () => {
    expect(fCompact(451_000_000_000)).toBe('451B')
    expect(fCompact(120_200_000)).toBe('120.2M')
  })
  it('handles thousands and small numbers', () => {
    expect(fCompact(1500)).toBe('1.5K')
    expect(fCompact(999)).toBe('999')
  })
})

afterEach(() => {
  setBaseCurrency('SGD')
  setDisplayFormats({})
})

describe('fPrice — always ≥2 decimals for normal prices, more for sub-dollar', () => {
  it('pads normal prices to 2 decimals (37.8 → "37.80")', () => {
    expect(fPrice(37.8)).toBe('37.80')
    expect(fPrice(38)).toBe('38.00')
  })
  it('keeps extra precision up to 4 decimals for prices ≥1', () => {
    expect(fPrice(37.8567)).toBe('37.8567')
    expect(fPrice(37.856)).toBe('37.856')
    expect(fPrice(2.3456)).toBe('2.3456')
  })
  it('thousands keep grouping + 2 decimals', () => {
    expect(fPrice(1234.5)).toBe('1,234.50')
  })
  it('sub-dollar / crypto values keep their finer precision', () => {
    expect(fPrice(0.5)).toBe('0.5000')
    expect(fPrice(0.00001234)).toBe('0.00001234')
  })
  it('sub-dollar prices use the user’s decimal mark, not always a point', () => {
    setDisplayFormats({ number_format: 'dot' })
    expect(fPrice(0.5)).toBe('0,5000')
    expect(fPrice(0.00001234)).toBe('0,00001234')
    setDisplayFormats({ number_format: 'space' })
    expect(fPrice(0.00001234)).toBe('0,00001234')
    setDisplayFormats({ number_format: 'comma' })
    expect(fPrice(0.00001234)).toBe('0.00001234')
  })
  it('null / zero render as an em dash', () => {
    expect(fPrice(0)).toBe('—')
    expect(fPrice(null)).toBe('—')
    expect(fPrice(undefined)).toBe('—')
  })
})

describe('fUnitPrice — symbol + adaptive precision, never rounds a sub-cent coin to "$0.00"', () => {
  it('prefixes the given currency symbol, not the base currency', () => {
    setBaseCurrency('SGD')
    expect(fUnitPrice(37.8, 'USD')).toBe('$37.80')
    expect(fUnitPrice(37.8, 'EUR')).toBe('€37.80')
  })
  it('keeps a sub-cent price visible instead of rounding to "$0.00" (SHIB-style coin)', () => {
    expect(fUnitPrice(0.0000089, 'USD')).toBe('$0.00000890')
  })
  it('0 / null / undefined render as an em dash, not "$—"', () => {
    expect(fUnitPrice(0, 'USD')).toBe('—')
    expect(fUnitPrice(null, 'USD')).toBe('—')
    expect(fUnitPrice(undefined, 'USD')).toBe('—')
  })
})

describe('fBaseWhole — base-currency amount with no cents', () => {
  it('drops the .00 that fBase shows', () => {
    setBaseCurrency('SGD')
    expect(fBase(108000)).toBe('S$108,000.00')
    expect(fBaseWhole(108000)).toBe('S$108,000')
    expect(fBaseWhole(1000)).toBe('S$1,000')
  })
  it('rounds to whole units', () => {
    setBaseCurrency('SGD')
    expect(fBaseWhole(33913.49)).toBe('S$33,913')
    expect(fBaseWhole(33913.51)).toBe('S$33,914')
  })
  it('normalizes -0 and renders zero without decimals', () => {
    setBaseCurrency('SGD')
    expect(fBaseWhole(0)).toBe('S$0')
    expect(fBaseWhole(-0)).toBe('S$0')
  })
})

describe('fBareAmount — bare grouped amount, no currency symbol, true minus', () => {
  it('formats with grouped thousands and exactly 2 decimals', () => {
    expect(fBareAmount(1234.5)).toBe('1,234.50')
    expect(fBareAmount(1000000)).toBe('1,000,000.00')
    expect(fBareAmount(0)).toBe('0.00')
  })
  it('uses a TRUE minus (U+2212), never an ASCII hyphen', () => {
    expect(fBareAmount(-89)).toBe('−89.00')
    expect(fBareAmount(-89).startsWith('−')).toBe(true)
    expect(fBareAmount(-89).includes('-')).toBe(false)
  })
  it('never emits a currency symbol', () => {
    expect(fBareAmount(50)).not.toMatch(/[$₫€£]/)
    expect(fBareAmount(50)).not.toMatch(/S\$/)
  })
  it('grouping is locale-stable (en-US) regardless of base currency', () => {
    setBaseCurrency('EUR')
    expect(fBareAmount(1234.5)).toBe('1,234.50')
  })
})

describe('fFig — budget-table figure with zero/sign metadata', () => {
  it('returns the em-dash sentinel for exact (rounds-to-) zero', () => {
    expect(fFig(0)).toEqual({ text: FIG_ZERO, isZero: true, isNeg: false })
    expect(fFig(0.004)).toEqual({ text: FIG_ZERO, isZero: true, isNeg: false }) // below epsilon
    expect(fFig(-0.0049)).toEqual({ text: FIG_ZERO, isZero: true, isNeg: false })
    expect(FIG_ZERO).toBe('—') // em-dash
  })
  it('a value at/above the 0.005 epsilon is a real figure, not the sentinel', () => {
    expect(fFig(0.005)).toEqual({ text: '0.01', isZero: false, isNeg: false }) // rounds up to 0.01
  })
  it('positive values: bare number, not zero, not negative', () => {
    expect(fFig(1234.5)).toEqual({ text: '1,234.50', isZero: false, isNeg: false })
  })
  it('negative values: true-minus text + isNeg flag', () => {
    expect(fFig(-89)).toEqual({ text: '−89.00', isZero: false, isNeg: true })
  })
})

describe('toBase', () => {
  // Rates relative to a single pivot. The pivot cancels in amount * rate[ccy] / rate[base],
  // so the same table converts correctly into any base.
  const fx = { USD: 1.35, EUR: 1.6, VND: 0.00005, SGD: 1 }

  it('returns the amount unchanged when ccy === base', () => {
    expect(toBase(100, 'EUR', fx, 'EUR')).toBe(100)
  })

  it('converts via the pivot for a non-SGD base (USD → EUR)', () => {
    // 100 USD → EUR = 100 * 1.35 / 1.6 = 84.375
    expect(toBase(100, 'USD', fx, 'EUR')).toBeCloseTo(84.375, 5)
  })

  it('converts a tiny-unit currency into a non-SGD base (VND → USD)', () => {
    // 1,000,000,000 VND → USD = 1e9 * 0.00005 / 1.35 ≈ 37,037
    expect(toBase(1_000_000_000, 'VND', fx, 'USD')).toBeCloseTo(37_037.037, 1)
  })

  it('round-trips: base→other→base is identity', () => {
    const eur = toBase(250, 'SGD', fx, 'EUR')
    expect(toBase(eur, 'EUR', fx, 'SGD')).toBeCloseTo(250, 6)
  })

  it('passes through 1:1 (not zero) when rate[base] is missing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // 'GBP' base absent from the table → must NOT zero the holding.
    expect(toBase(500, 'USD', fx, 'GBP')).toBe(500)
    warn.mockRestore()
  })

  it('passes through 1:1 when the holding currency is missing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(toBase(500, 'XYZ', fx, 'SGD')).toBe(500)
    warn.mockRestore()
  })

  it('defaults base to the configured base currency', () => {
    setBaseCurrency('EUR')
    // 100 USD → configured base EUR = 100 * 1.35 / 1.6
    expect(toBase(100, 'USD', fx)).toBeCloseTo(84.375, 5)
  })
})

describe('fPct', () => {
  it('formats an already-multiplied percent with 2 decimals', () => {
    expect(fPct(12.3)).toBe('12.30%')
    expect(fPct(100)).toBe('100.00%')
  })

  it('renders exactly 0 as 0.00%, never an empty/dash', () => {
    expect(fPct(0)).toBe('0.00%')
  })

  it('NEVER rounds a small non-zero value to 0% (the load-bearing case)', () => {
    expect(fPct(0.05)).toBe('0.05%')
    expect(fPct(0.004)).toBe('0.00%') // 0.004 rounds to 0.00 at 2dp — but it WAS sub-0.005
    expect(fPct(0.006)).toBe('0.01%')
  })

  it('treats a 0–1 input as a fraction with {fraction:true}', () => {
    expect(fPct(0.123, { fraction: true })).toBe('12.30%')
    expect(fPct(0.0005, { fraction: true })).toBe('0.05%')
    expect(fPct(1, { fraction: true })).toBe('100.00%')
  })

  it('signs non-negative values with {sign:true} (the - is intrinsic)', () => {
    expect(fPct(14.2, { sign: true })).toBe('+14.20%')
    expect(fPct(0, { sign: true })).toBe('+0.00%')
    expect(fPct(-3.5, { sign: true })).toBe('-3.50%')
    expect(fPct(-3.5)).toBe('-3.50%') // no + without {sign}, the - stays
  })

  it('combines fraction + sign', () => {
    expect(fPct(0.142, { fraction: true, sign: true })).toBe('+14.20%')
    expect(fPct(-0.04, { fraction: true, sign: true })).toBe('-4.00%')
  })

  it('returns an em-dash for null / undefined / NaN', () => {
    expect(fPct(null)).toBe('—')
    expect(fPct(undefined)).toBe('—')
    expect(fPct(NaN)).toBe('—')
  })
})

describe('txnMentionSublabel — transaction @ picker sublabel', () => {
  afterEach(() => setBaseCurrency('SGD'))

  it('income row: + prefix, all parts joined', () => {
    // "Salary · Checking · 2025-06-01 · +S$1,000.00"
    const result = txnMentionSublabel({
      kind: 'income',
      payeeName: 'Employer',
      accountName: 'Checking',
      date: '2025-06-01',
      amount: 1000,
      currency: 'SGD',
    })
    expect(result).toContain('Employer')
    expect(result).toContain('Checking')
    expect(result).toContain('2025-06-01')
    expect(result.startsWith('Employer • Checking • 2025-06-01 • +')).toBe(true)
  })

  it('expense row: − prefix (true minus U+2212)', () => {
    const result = txnMentionSublabel({
      kind: 'expense',
      payeeName: 'Coffee Shop',
      accountName: 'Wallet',
      date: '2025-06-15',
      amount: 5.5,
      currency: 'USD',
    })
    expect(result).toContain('Coffee Shop')
    expect(result).toContain('Wallet')
    // The amount segment must use the true minus sign (U+2212), not an ASCII hyphen
    const amtPart = result.split(' • ').at(-1) ?? ''
    expect(amtPart.startsWith('−')).toBe(true)
    expect(amtPart.includes('-')).toBe(false)
  })

  it('transfer row: no sign prefix', () => {
    const result = txnMentionSublabel({
      kind: 'transfer',
      payeeName: null,
      accountName: 'Savings',
      date: '2025-06-20',
      amount: 200,
      currency: 'SGD',
    })
    const amtPart = result.split(' • ').at(-1) ?? ''
    // No + or − prefix
    expect(amtPart.startsWith('+')).toBe(false)
    expect(amtPart.startsWith('−')).toBe(false)
    expect(amtPart.startsWith('-')).toBe(false)
  })

  it('empty payee is dropped from the parts (no leading •)', () => {
    const result = txnMentionSublabel({
      kind: 'expense',
      payeeName: null,
      accountName: 'Card',
      date: '2026-01-01',
      amount: 10,
      currency: 'SGD',
    })
    expect(result.startsWith('Card')).toBe(true)
    expect(result).not.toContain('null')
  })

  it('empty account is dropped from the parts', () => {
    const result = txnMentionSublabel({
      kind: 'income',
      payeeName: 'Freelance',
      accountName: null,
      date: '2026-03-05',
      amount: 500,
      currency: 'SGD',
    })
    expect(result.startsWith('Freelance • 2026-03-05')).toBe(true)
  })
})

describe('localIso — LOCAL calendar date, not UTC (timezone bug regression)', () => {
  const originalTz = process.env.TZ
  afterEach(() => {
    if (originalTz === undefined) delete process.env.TZ
    else process.env.TZ = originalTz
    vi.useRealTimers()
  })

  it('in a UTC-ahead timezone, returns the LOCAL date even when it differs from the UTC date', () => {
    process.env.TZ = 'Asia/Singapore' // UTC+8, no DST
    // 23:30 UTC on 2026-07-12 is already 07:30 on 2026-07-13 in Singapore.
    const instant = new Date('2026-07-12T23:30:00.000Z')
    // Prove the raw `toISOString().slice(0, 10)` approach WOULD give the wrong (UTC) day.
    expect(instant.toISOString().slice(0, 10)).toBe('2026-07-12')
    // localIso must report the local day instead.
    expect(localIso(instant)).toBe('2026-07-13')
  })

  it('defaults to the current local instant when called with no argument', () => {
    process.env.TZ = 'Asia/Singapore'
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T23:30:00.000Z'))
    expect(localIso()).toBe('2026-07-13')
  })
})

describe('newsTime — relative under 24h, absolute date beyond that', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders a sub-24h timestamp as relative, matching timeAgo', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-29T12:00:00.000Z'))
    expect(newsTime('2026-08-29T04:00:00.000Z')).toBe('8h ago')
  })

  it('renders anything 24h or older as an absolute date, not vague relative fuzz', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-29T12:00:00.000Z'))
    // A day old ("yesterday" in the old relative-only rendering) now reads as a real date.
    expect(newsTime('2026-08-28T12:00:00.000Z')).toBe('Aug 28')
    // A week+ old ("1w ago" in the old rendering) too; a prior-year item keeps its year.
    expect(newsTime('2026-08-15T12:00:00.000Z')).toBe('Aug 15')
    expect(newsTime('2025-12-30T12:00:00.000Z')).toBe('Dec 30, 2025')
  })

  it('returns empty string for an empty or unparseable date', () => {
    expect(newsTime('')).toBe('')
    expect(newsTime('not a date')).toBe('')
  })
})

describe('fDate — one date writer that follows the Date-format preference', () => {
  const thisYear = new Date().getFullYear()

  it('numeric style obeys dmy / mdy / iso and always keeps the year', () => {
    setDisplayFormats({ date_format: 'dmy' })
    expect(fDate('2026-10-06', 'numeric')).toBe('06/10/2026')
    setDisplayFormats({ date_format: 'mdy' })
    expect(fDate('2026-10-06', 'numeric')).toBe('10/06/2026')
    setDisplayFormats({ date_format: 'iso' })
    expect(fDate('2026-10-06', 'numeric')).toBe('2026-10-06')
  })

  it('an unset preference follows the browser locale', () => {
    expect(fDate('2020-07-20', 'long')).toBe('July 20, 2020')
  })

  it('reads a date-only value as the local calendar day, never shifted by UTC', () => {
    setDisplayFormats({ date_format: 'iso' })
    expect(fDate('2026-01-01', 'numeric')).toBe('2026-01-01')
  })

  it('drops the year in the current year and keeps it otherwise', () => {
    setDisplayFormats({ date_format: 'mdy' })
    expect(fDate(`${thisYear}-03-05`)).toBe('Mar 5')
    expect(fDate(`${thisYear - 1}-12-28`)).toBe(`Dec 28, ${thisYear - 1}`)
    expect(fDate(`${thisYear}-03-05`, 'short', 'always')).toBe(`Mar 5, ${thisYear}`)
    expect(fDate(`${thisYear - 1}-12-28`, 'short', 'never')).toBe('Dec 28')
  })

  it('iso only governs the numeric style — month names stay English', () => {
    setDisplayFormats({ date_format: 'iso' })
    expect(fDate('2020-10-06', 'short')).toMatch(/^6 Oct/)
    expect(fDate('2020-10-06', 'long')).toMatch(/^6 October/)
  })

  it('writes September as "Sep" under day-first too', () => {
    setDisplayFormats({ date_format: 'dmy' })
    expect(fDate('2020-09-06')).toBe('6 Sep 2020')
    expect(fMonth('2020-09')).toBe('Sep 2020')
  })

  it('returns an unreadable value untouched', () => {
    expect(fDate('not a date')).toBe('not a date')
  })
})

describe('fMonth / fDateTime', () => {
  it('writes a month key with the year only when it is not this year', () => {
    expect(fMonth('2020-10')).toBe('Oct 2020')
    expect(fMonth(`${new Date().getFullYear()}-10`, 'long')).toBe('October')
  })

  it('a date-only value has no clock time; a timestamp does', () => {
    setDisplayFormats({ date_format: 'mdy' })
    expect(fDateTime('2020-10-06')).toBe('Oct 6, 2020')
    expect(fDateTime(new Date(2020, 9, 6, 14, 5))).toMatch(/^Oct 6, 2020 • 2:05\s?PM$/)
  })
})

describe('parseNumericDate — a typed date in the chosen format', () => {
  it('reads day-first, month-first and ISO, and rejects the wrong order', () => {
    expect(parseNumericDate('24/05/1998', 'dmy')).toBe('1998-05-24')
    expect(parseNumericDate('05/24/1998', 'mdy')).toBe('1998-05-24')
    expect(parseNumericDate('1998-05-24', 'iso')).toBe('1998-05-24')
    expect(parseNumericDate('24/05/1998', 'mdy')).toBeNull()
  })

  it('takes one-digit parts and any of / . - as the separator', () => {
    expect(parseNumericDate('4.5.1998', 'dmy')).toBe('1998-05-04')
    expect(parseNumericDate('5-4-1998', 'mdy')).toBe('1998-05-04')
  })

  it('refuses a partial or impossible date', () => {
    expect(parseNumericDate('24/05/19', 'dmy')).toBeNull()
    expect(parseNumericDate('31/02/2026', 'dmy')).toBeNull()
    expect(parseNumericDate('', 'dmy')).toBeNull()
  })

  it('exposes the saved format, or undefined for the browser default', () => {
    setDisplayFormats({ date_format: 'mdy' })
    expect(getDateFormat()).toBe('mdy')
    setDisplayFormats({ date_format: null })
    expect(getDateFormat()).toBeUndefined()
  })
})

describe('priceDigits — 2 decimals for a share, the adaptive precision for a coin', () => {
  it('rounds a known non-crypto price or cost to 2 decimals', () => {
    expect(priceDigits('equity')).toBe(2)
    expect(fPrice(105.9846, priceDigits('equity'))).toBe('105.98')
    expect(fPrice(105.9846, priceDigits('bond'))).toBe('105.98')
  })
  it('keeps a coin, or a row of unknown class, at its own precision', () => {
    expect(fPrice(2.3456, priceDigits('crypto'))).toBe('2.3456')
    expect(fPrice(0.0000089, priceDigits('crypto'))).toBe('0.00000890')
    expect(priceDigits(undefined)).toBeUndefined()
    expect(priceDigits(null)).toBeUndefined()
  })
})
