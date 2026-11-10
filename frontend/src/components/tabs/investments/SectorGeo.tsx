// SectorGeo — sector & geography exposure panel. Verbatim port of prototype SectorGeo().
// Toggle: Sector | Region. Shows ranked bars with concentration flag.

import { useMemo, useState } from 'react'
import type { BookRow, DerivedBook } from './useInvestmentsBook'

const SECTOR_MAP: Record<string, string> = {
  NVDA: 'Technology',
  AAPL: 'Technology',
  MSFT: 'Technology',
  AMD: 'Technology',
  AVGO: 'Technology',
  ADBE: 'Technology',
  CRM: 'Technology',
  GOOGL: 'Communication',
  META: 'Communication',
  NFLX: 'Communication',
  AMZN: 'Consumer',
  TSLA: 'Consumer',
  WMT: 'Consumer',
  PG: 'Consumer',
  COST: 'Consumer',
  HD: 'Consumer',
  JPM: 'Financials',
  V: 'Financials',
  MA: 'Financials',
  'BRK.B': 'Financials',
  UNH: 'Healthcare',
  JNJ: 'Healthcare',
  LLY: 'Healthcare',
  ABBV: 'Healthcare',
  XOM: 'Energy',
}

// The instrument's own sector (the price provider's names) folded into this panel's buckets.
const SECTOR_ALIASES: Record<string, string> = {
  Technology: 'Technology',
  'Information Technology': 'Technology',
  'Communication Services': 'Communication',
  'Consumer Cyclical': 'Consumer',
  'Consumer Defensive': 'Consumer',
  'Consumer Discretionary': 'Consumer',
  'Consumer Staples': 'Consumer',
  'Financial Services': 'Financials',
  Financials: 'Financials',
  Healthcare: 'Healthcare',
  'Health Care': 'Healthcare',
  Energy: 'Energy',
  Industrials: 'Industrials',
  'Basic Materials': 'Materials',
  Materials: 'Materials',
  'Real Estate': 'Real Estate',
  Utilities: 'Utilities',
}

const SECTOR_COLOR: Record<string, string> = {
  Technology: '#3b86dd',
  Communication: '#22b8cf',
  Consumer: '#e0a032',
  Financials: '#1d9e75',
  Healthcare: '#9a7fd0',
  Energy: '#e2554f',
  Industrials: '#6b7280',
  Materials: '#bd7a1a',
  'Real Estate': '#d4537e',
  Utilities: '#2ba39a',
  'Broad market': '#5b6472',
  'Fixed income': '#2ba39a',
  'Digital assets': '#9a7fd0',
  Commodities: '#c8921a',
  Other: '#6b7280',
}

const REGION_COLOR: Record<string, string> = {
  'United States': '#3b86dd',
  Europe: '#9a7fd0',
  Singapore: '#e0a032',
  'Global ex-US': '#22b8cf',
  Global: '#1d9e75',
  'United Kingdom': '#e2554f',
  'Hong Kong': '#bd7a1a',
  Japan: '#d4537e',
}

const REGION_BY_CCY: Record<string, string> = {
  USD: 'United States',
  EUR: 'Europe',
  SGD: 'Singapore',
  GBP: 'United Kingdom',
  HKD: 'Hong Kong',
  JPY: 'Japan',
}

function rowSector(r: BookRow): string {
  if (r.cls === 'crypto') return 'Digital assets'
  if (r.cls === 'commodity') return 'Commodities'
  if (r.cls === 'bond') return 'Fixed income'
  if (r.sub === 'etf' || r.sub === 'etc') {
    return /QQQ|VGT|XLK|SMH|tech/i.test(r.sym + ' ' + r.name) ? 'Technology' : 'Broad market'
  }
  // The instrument's recorded sector first. The prototype this was ported from hashed any
  // ticker outside a short list into a sector, so a bank (DBS) read "Communication"
  // (persona QA 2026-10-13); an unknown is "Other", never a guess.
  const own = (r._raw.sector ?? '').trim()
  if (own) return SECTOR_ALIASES[own] ?? own
  return SECTOR_MAP[r.sym] ?? 'Other'
}

function rowRegion(r: BookRow): string {
  if (r.cls === 'crypto' || r.cls === 'commodity') return 'Global'
  const label = r.sym + ' ' + r.name
  if (/ex-us|intl|emerging|developed|VXUS|BNDX/i.test(label)) return 'Global ex-US'
  // An all-world fund holds the US too (VWRA is ~60% US) — never "ex-US".
  if (/world|acwi|VWRA/i.test(label)) return 'Global'
  return REGION_BY_CCY[r.ccy] ?? 'United States'
}

function exposureBreakdown(all: BookRow[], keyFn: (r: BookRow) => string) {
  const m = new Map<string, number>()
  let tot = 0
  all.forEach((r) => {
    const k = keyFn(r)
    m.set(k, (m.get(k) ?? 0) + r.value)
    tot += r.value
  })
  const rows = [...m.entries()]
    .map(([k, v]) => ({ k, v, pct: tot ? (v / tot) * 100 : 0 }))
    .sort((a, b) => b.v - a.v)
  return { rows, tot }
}

interface Props {
  d: DerivedBook
}

function Card({
  title,
  meta,
  children,
}: {
  title: string
  meta?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="mk-card">
      <div className="mk-card-head">
        <span className="mk-card-title">{title}</span>
        {meta && <span className="mk-card-meta">{meta}</span>}
      </div>
      <div className="mk-card-body">{children}</div>
    </div>
  )
}

export function SectorGeo({ d }: Props) {
  const [view, setView] = useState<'sector' | 'geography'>('sector')

  const bd = useMemo(
    () => exposureBreakdown(d.all, view === 'sector' ? rowSector : rowRegion),
    [d.all, view],
  )

  const colorMap = view === 'sector' ? SECTOR_COLOR : REGION_COLOR
  const max = Math.max(1, ...bd.rows.map((r) => r.pct))

  const tech = bd.rows.find((r) => r.k === 'Technology')
  const techHi = view === 'sector' && tech && tech.pct >= 30
  const top = bd.rows[0]
  const regionHi = view === 'geography' && top && top.pct >= 70

  return (
    <Card
      title="Sector & geography"
      meta={
        <div className="mk-seg">
          <button className={view === 'sector' ? 'on' : ''} onClick={() => setView('sector')}>
            Sector
          </button>
          <button className={view === 'geography' ? 'on' : ''} onClick={() => setView('geography')}>
            Region
          </button>
        </div>
      }
    >
      <div className="sec-list">
        {bd.rows.map((s) => (
          <div className="sec-row" key={s.k}>
            <div className="sec-top">
              <span className="sec-k">
                <span className="sec-dot" style={{ background: colorMap[s.k] ?? '#6b7280' }} />
                {s.k}
              </span>
              <span className="sec-v mask">{s.pct.toFixed(0)}%</span>
            </div>
            <div className="sec-track">
              <span
                className="sec-fill"
                style={{ width: (s.pct / max) * 100 + '%', background: colorMap[s.k] ?? '#6b7280' }}
              />
            </div>
          </div>
        ))}
      </div>
      {techHi && (
        <div className="mk-flag">
          <span className="mk-stress-mark">▲</span>
          <span>
            Technology is <b className="money">{Math.round(tech!.pct)}%</b> of your book across
            multiple holdings — a deeper concentration than any single ticker shows. A tech selloff
            would hit harder than the asset-class mix suggests.
          </span>
        </div>
      )}
      {regionHi && (
        <div className="mk-flag">
          <span className="mk-stress-mark">▲</span>
          <span>
            <b>{top!.k}</b> is <b className="money">{Math.round(top!.pct)}%</b> of your book —
            limited geographic diversification. International exposure would cushion a domestic
            drawdown.
          </span>
        </div>
      )}
      <div className="mk-lots-note">
        Look-through by where revenue and listing sit, not just asset class. ETFs counted at fund
        level.
      </div>
    </Card>
  )
}
