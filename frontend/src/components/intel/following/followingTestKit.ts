import { vi } from 'vitest'
import type {
  DirectoryEntry,
  Follow,
  FollowPerformance,
  PerfWindow,
  PreviewPage,
  PreviewSummary,
} from '../../../api/followedPortfolios'

/** A fund with two quarters on record and one dated trade after the newest (synthetic). */
export function fund(over: Partial<Follow> = {}): Follow {
  return {
    id: 'fol_fund',
    kind: 'fund_13f',
    source_ref: '0000000001',
    name: 'Acme Capital',
    subtitle: 'Ada Acme',
    status: 'ok',
    error: null,
    last_checked_at: '2026-11-09T08:00:00Z',
    model: {
      as_of: '2026-06-30',
      prev_as_of: '2026-03-31',
      filed_at: '2026-08-14',
      age_days: 88,
      estimated: false,
      total_value: 1_200_000_000,
      prev_total_value: 1_100_000_000,
      positions: 4,
      prev_positions: 4,
      holdings: [
        {
          symbol: 'ACME',
          name: 'Acme Corp',
          weight: 0.5,
          prev_weight: 0.6,
          change: 'reduced',
          shares: 1_000_000,
          prev_shares: 1_500_000,
          shares_change: -500_000,
          shares_change_pct: -1 / 3,
          price: 30,
          change_value: -15_000_000,
        },
        {
          symbol: 'BOLT',
          name: 'Bolt Inc',
          weight: 0.3,
          prev_weight: 0.2,
          change: 'increased',
          shares: 400,
          prev_shares: 200,
          shares_change: 200,
          shares_change_pct: 1,
          price: 10_000,
          change_value: 2_000_000,
        },
        {
          symbol: 'CRUX',
          name: 'Crux Holdings',
          weight: 0.12,
          prev_weight: null,
          change: 'new',
          shares: 100,
          prev_shares: 0,
          shares_change: 100,
          price: 1_000,
          change_value: 100_000,
        },
        {
          symbol: 'DUNE',
          name: 'Dune Ltd',
          weight: 0.04,
          prev_weight: 0.04,
          change: 'unchanged',
          shares: 50,
          prev_shares: 50,
          shares_change: 0,
          price: 100,
        },
      ],
      exited: [
        { symbol: 'OXYZ', name: 'Oxyz Energy', prev_shares: 3_200_000, prev_value: 96_000_000 },
      ],
      unmatched: [{ key: 'X00000001', name: 'Mystery Trust', weight: 0.04 }],
      unread_reports: 0,
    },
    history: [
      {
        as_of: '2026-06-30',
        filed_at: '2026-08-14',
        qty: { ACME: 1_000_000, BOLT: 400, CRUX: 100, DUNE: 50 },
        value: { ACME: 30_000_000, BOLT: 4_000_000, CRUX: 100_000, DUNE: 5_000 },
      },
      {
        as_of: '2026-03-31',
        filed_at: '2026-05-15',
        qty: { ACME: 1_500_000, BOLT: 200, DUNE: 50, OXYZ: 3_200_000 },
        value: { ACME: 45_000_000, BOLT: 2_000_000, DUNE: 5_000, OXYZ: 96_000_000 },
      },
    ],
    activity: [
      {
        date: '2026-10-02',
        filed_at: '2026-10-05',
        symbol: 'BOLT',
        name: 'Bolt Inc',
        side: 'buy',
        amount_low: 0,
        amount_high: 0,
        asset_type: 'stock',
        counted: true,
        shares: 10,
        price: 10_000,
        value: 100_000,
        source: 'form4',
      },
    ],
    ...over,
  }
}

export function member(over: Partial<Follow> = {}): Follow {
  return {
    id: 'fol_member',
    kind: 'house_member',
    source_ref: 'M000001',
    name: 'Pat Placeholder',
    subtitle: 'Representative, ZZ01',
    status: 'ok',
    error: null,
    last_checked_at: '2026-11-09T08:00:00Z',
    model: {
      as_of: '2026-10-02',
      filed_at: '2026-10-09',
      age_days: 32,
      estimated: true,
      holdings: [
        { symbol: 'NOVA', name: 'Nova Systems', weight: 1, prev_weight: null, change: 'unchanged' },
      ],
      exited: [],
      unmatched: [],
      unread_reports: 0,
    },
    activity: [
      {
        date: '2026-10-02',
        filed_at: '2026-10-09',
        symbol: 'NOVA',
        name: 'Nova Systems',
        side: 'buy',
        amount_low: 250_000,
        amount_high: 500_000,
        asset_type: 'stock',
        counted: true,
      },
      {
        date: '2026-08-03',
        filed_at: '2026-08-10',
        symbol: 'OLDR',
        name: 'Older Co',
        side: 'sell',
        amount_low: 15_000,
        amount_high: 50_000,
        asset_type: 'stock',
        counted: true,
      },
    ],
    ...over,
  }
}

export const SECTORS: Record<string, string> = {
  ACME: 'Technology',
  BOLT: 'Industrials',
  CRUX: 'Technology',
  DUNE: 'Energy',
}

export const DIRECTORY: DirectoryEntry[] = [
  {
    kind: 'fund_13f',
    source_ref: '0000000001',
    name: 'Acme Capital',
    subtitle: 'Ada Acme',
    featured: true,
    followed: true,
  },
  {
    kind: 'house_member',
    source_ref: 'M000001',
    name: 'Pat Placeholder',
    subtitle: 'Representative, ZZ01',
    featured: true,
    followed: false,
  },
  {
    kind: 'fund_13f',
    source_ref: '0000000002',
    name: 'Borealis Partners',
    subtitle: 'Bo Borealis',
    featured: true,
    followed: false,
  },
]

const window_ = (ret: number, bench: number, holdings: Record<string, number>): PerfWindow => ({
  status: 'ok',
  start: '2025-11-10',
  end: '2026-11-10',
  return: ret,
  benchmark_return: bench,
  covered_weight: 1,
  series: [
    { date: '2025-11-10', portfolio: 0, benchmark: 0 },
    { date: '2026-05-10', portfolio: ret / 2, benchmark: bench / 2 },
    { date: '2026-11-10', portfolio: ret, benchmark: bench },
  ],
  holdings,
  missing: [],
})

export function performance(over: Partial<FollowPerformance> = {}): FollowPerformance {
  return {
    benchmark: 'SPY',
    periods: {
      '12M': window_(0.163, 0.108, { ACME: 0.294, BOLT: -0.107, CRUX: 0.05, DUNE: 0 }),
      '6M': window_(0.081, 0.06, { ACME: 0.1, BOLT: 0.02 }),
      '1M': window_(0.021, 0.034, { ACME: 0.01, BOLT: 0.02 }),
      FILING: window_(-0.012, -0.035, { ACME: -0.02, BOLT: 0.004 }),
      ALL: window_(0.4, 0.3, {}),
    },
    trade_returns: {},
    ...over,
  }
}

export function summary(f: Follow): PreviewSummary {
  return {
    kind: f.kind,
    source_ref: f.source_ref,
    name: f.name,
    subtitle: f.subtitle,
    follow_id: f.id,
    estimated: f.kind === 'house_member',
    style: null,
    holdings_count: f.model?.holdings.length ?? 0,
    weights: (f.model?.holdings ?? []).map((h) => ({ symbol: h.symbol, weight: h.weight })),
    as_of: f.model?.as_of ?? null,
    latest_at: f.activity[0]?.filed_at ?? null,
    filed_at: f.model?.filed_at ?? null,
    benchmark: 'SPY',
    return_12m: 0.163,
    benchmark_12m: 0.108,
    spark: [],
    moves: [],
  }
}

export interface Server {
  /** Every portfolio the directory knows, by `kind:ref`. */
  portfolios: Follow[]
  /** The ones the owner watches (their ids). */
  watched: Set<string>
  directory: DirectoryEntry[]
  failList?: boolean
  /** Your own book's return series (the Investments Performance tab's), when it has one. */
  ownReturns?: { date: string; portfolio: number; benchmark: number | null }[]
}

export function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

/** An in-memory stand-in for the routes Famous Portfolios reads and writes. */
export function installServer(authFetch: ReturnType<typeof vi.fn>, server: Server) {
  const byRef = (kind: string | null, ref: string | null) =>
    server.portfolios.find((p) => p.kind === kind && p.source_ref === ref)
  authFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const u = new URL(url, 'http://x')
    if (u.pathname === '/api/followed-portfolios' && method === 'GET')
      return server.failList
        ? json({ detail: 'down' }, 500)
        : json({ follows: server.portfolios.filter((p) => server.watched.has(p.id)) })
    if (u.pathname === '/api/returns')
      return server.ownReturns
        ? json({ series: server.ownReturns })
        : json({ detail: 'no history' }, 404)
    if (u.pathname === '/api/followed-portfolios/directory') {
      const q = (u.searchParams.get('q') ?? '').toLowerCase()
      const results =
        q.length < 2
          ? server.directory
          : server.directory.filter((d) => d.name.toLowerCase().includes(q))
      return json({ results })
    }
    if (u.pathname === '/api/followed-portfolios/preview') {
      const p = byRef(u.searchParams.get('kind'), u.searchParams.get('ref'))
      if (!p) return json({ detail: 'not found' }, 404)
      if (u.searchParams.get('full') !== 'true') return json(summary(p))
      const page: PreviewPage = {
        follow: p,
        follow_id: server.watched.has(p.id) ? p.id : null,
        performance: performance(),
        names: {},
        sectors: SECTORS,
      }
      return json(page)
    }
    if (u.pathname === '/api/followed-portfolios/price-history') {
      const symbol = u.searchParams.get('symbol') ?? ''
      return json({
        symbol,
        points: [
          { date: '2026-03-31', price: 90 },
          { date: '2026-08-14', price: 100 },
          { date: '2026-11-10', price: 110 },
        ],
      })
    }
    if (u.pathname === '/api/followed-portfolios' && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as { kind: string; source_ref: string }
      const p = byRef(body.kind, body.source_ref)!
      server.watched.add(p.id)
      return json({ follow: p, new_filing: false })
    }
    const id = decodeURIComponent(u.pathname.split('/')[3] ?? '')
    if (method === 'DELETE') {
      server.watched.delete(id)
      return json({ follow: server.portfolios.find((p) => p.id === id), new_filing: false })
    }
    return json({ detail: `unhandled ${method} ${url}` }, 404)
  })
}
