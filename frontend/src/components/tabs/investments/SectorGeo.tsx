// SectorGeo — sector & geography exposure panel. Verbatim port of prototype SectorGeo().
// Toggle: Sector | Region. Shows ranked bars with concentration flag.
//
// Region is the server's look-through (`portfolio_xray`, services/xray.py): a fund with saved
// constituents is spread across them, anything else is placed by services/classification.py,
// and what neither can place is shown as "Unmapped". The browser used to guess a fund's region
// from its listing currency, so a EUR-listed global fund read "Europe 100%" (issue #210).

import { useMemo, useState } from 'react'
import type { PortfolioXray } from '../../../api/investments'
import { querySource } from '../../../hooks/serverState/dataQuery'
import { useFlag } from '../../../hooks/useFlag'
import { useQueryCache } from '../../../state/queryCache'
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
  'Communication Services': '#22b8cf',
  'Consumer Discretionary': '#e0a032',
  'Consumer Staples': '#c8921a',
  'Health Care': '#9a7fd0',
  Cash: '#6b8fa3',
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
  'North America': '#3b86dd',
  Canada: '#2ba39a',
  'Greater China': '#bd7a1a',
  'Emerging Markets': '#c8921a',
  Pacific: '#d4537e',
  Australia: '#2ba39a',
  Unmapped: '#6b7280',
}

const UNMAPPED = 'Unmapped'
// A look-through region is whatever its constituents say ("Emerging markets", "Pacific"), so a
// name the map doesn't know gets a stable colour of its own — never Unmapped's grey.
const SPARE_COLORS = ['#3b86dd', '#9a7fd0', '#e0a032', '#22b8cf', '#1d9e75', '#e2554f', '#d4537e']

function colorFor(map: Record<string, string>, k: string): string {
  const known =
    map[k] ?? Object.entries(map).find(([n]) => n.toLowerCase() === k.toLowerCase())?.[1]
  if (known) return known
  let h = 0
  for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return SPARE_COLORS[h % SPARE_COLORS.length] ?? '#3b86dd'
}
const XRAY_URL = '/api/portfolio/xray'

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

/** One X-ray axis as this panel's rows, its unplaced share last. */
function xrayBreakdown(
  xray: PortfolioXray | null | undefined,
  axis: 'sector' | 'region',
  lookthrough: boolean,
) {
  if (!xray) return { rows: [] as { k: string; v: number; pct: number }[] }
  const buckets = axis === 'sector' ? xray.by_sector : xray.by_region
  // A sliver (a fund's 0.4% cash) would draw as "0%"; it stays in the server's figures. Only
  // with the flag on, where looked-through funds make slivers: off, every bucket draws as before.
  const rows = buckets
    .filter((b) => !lookthrough || b.pct >= 0.5)
    .map((b) => ({ k: b.name, v: b.value_base, pct: b.pct }))
  const unmappedPct = axis === 'sector' ? xray.sector_unmapped_pct : xray.region_unmapped_pct
  const unmappedBase = axis === 'sector' ? xray.sector_unmapped_base : xray.region_unmapped_base
  if (unmappedPct >= 0.5) rows.push({ k: UNMAPPED, v: unmappedBase, pct: unmappedPct })
  return { rows }
}

/** "62% looked through funds · 35% by each holding's own sector · 3% unmapped". */
function coverageLine(xray: PortfolioXray | null | undefined): string | null {
  const c = xray?.sector_coverage
  if (!c) return null
  return (
    `${Math.round(c.looked_through)}% looked through funds · ` +
    `${Math.round(c.classified)}% by each holding’s own sector · ` +
    `${Math.round(c.unmapped)}% unmapped`
  )
}

function useRegionXray() {
  return useQueryCache<PortfolioXray>(
    XRAY_URL,
    () => querySource<PortfolioXray>('portfolio_xray', {}, XRAY_URL),
    { staleTime: 0 },
  )
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

  const xray = useRegionXray()
  // Flag `fund_lookthrough` — this panel's one switch point. On: the Sector view is the X-ray's,
  // funds looked through, its verdict the server's. Off (or not loaded yet): as before.
  const lookthrough = useFlag('fund_lookthrough') === true
  const sectors = useMemo(
    () =>
      lookthrough ? xrayBreakdown(xray.data, 'sector', true) : exposureBreakdown(d.all, rowSector),
    [lookthrough, xray.data, d.all],
  )
  const regions = useMemo(
    () => xrayBreakdown(xray.data, 'region', lookthrough),
    [xray.data, lookthrough],
  )
  const bd = view === 'sector' ? sectors : regions
  const regionPending = (view === 'geography' || lookthrough) && !xray.data

  const colorMap = view === 'sector' ? SECTOR_COLOR : REGION_COLOR
  const max = Math.max(1, ...bd.rows.map((r) => r.pct))

  const tech = bd.rows.find((r) => r.k === 'Technology')
  const techHi = view === 'sector' && !lookthrough && tech && tech.pct >= 30
  const sectorFlag = view === 'sector' && lookthrough ? xray.data?.sector_flag : null
  const top = bd.rows.find((r) => r.k !== UNMAPPED)
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
      {regionPending && (
        <div className="mk-lots-note">
          {xray.error ? 'Couldn’t load the breakdown.' : 'Loading…'}
        </div>
      )}
      <div className="sec-list">
        {bd.rows.map((s) => (
          <div className="sec-row" key={s.k}>
            <div className="sec-top">
              <span className="sec-k">
                <span className="sec-dot" style={{ background: colorFor(colorMap, s.k) }} />
                {s.k}
              </span>
              <span className="sec-v mask">{s.pct.toFixed(0)}%</span>
            </div>
            <div className="sec-track">
              <span
                className="sec-fill"
                style={{ width: (s.pct / max) * 100 + '%', background: colorFor(colorMap, s.k) }}
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
      {sectorFlag && (
        <div className="mk-flag">
          <span className="mk-stress-mark">▲</span>
          <span>
            {sectorFlag.name} is <b className="money">{Math.round(sectorFlag.pct)}%</b> of your
            book, counting what your funds hold — a deeper concentration than any single ticker
            shows.
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
        {view === 'sector' && lookthrough
          ? coverageLine(xray.data)
          : view === 'sector'
            ? 'Grouped by each holding’s own sector. ETFs counted at fund level.'
            : 'A fund with a saved look-through is spread across its regions; anything else is placed by its listing. Unmapped is what can’t be placed.'}
      </div>
    </Card>
  )
}
