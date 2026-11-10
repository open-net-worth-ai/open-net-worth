import { vi } from 'vitest'
import type { DirectoryEntry, Follow } from '../../../api/followedPortfolios'

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
      filed_at: '2026-08-14',
      age_days: 88,
      estimated: false,
      holdings: [
        { symbol: 'ACME', name: 'Acme Corp', weight: 0.224, prev_weight: 0.241, change: 'reduced' },
        {
          symbol: 'BOLT',
          name: 'Bolt Inc',
          weight: 0.109,
          prev_weight: 0.103,
          change: 'increased',
        },
        { symbol: 'CRUX', name: 'Crux Holdings', weight: 0.032, prev_weight: null, change: 'new' },
        { symbol: 'DUNE', name: 'Dune Ltd', weight: 0.05, prev_weight: 0.05, change: 'unchanged' },
      ],
      exited: [{ symbol: 'OXYZ', name: 'Oxyz Energy' }],
      unmatched: [{ name: 'Mystery Trust', weight: 0.041 }],
      unread_reports: 0,
    },
    activity: [],
    ...over,
  }
}

export function member(over: Partial<Follow> = {}): Follow {
  return {
    id: 'fol_member',
    kind: 'house_member',
    source_ref: 'M000001',
    name: 'Pat Placeholder',
    subtitle: 'House of Representatives',
    status: 'ok',
    error: null,
    last_checked_at: '2026-11-09T08:00:00Z',
    model: {
      as_of: '2026-10-09',
      filed_at: '2026-10-09',
      age_days: 32,
      estimated: true,
      holdings: [
        { symbol: 'NOVA', name: 'Nova Systems', weight: 1, prev_weight: null, change: 'unchanged' },
      ],
      exited: [],
      unmatched: [],
      unread_reports: 2,
    },
    activity: [
      {
        date: '2026-09-01',
        filed_at: '2026-09-08',
        symbol: 'OLDR',
        name: 'Older Co',
        side: 'sell',
        amount_low: 15000,
        amount_high: 50000,
        asset_type: 'stock',
        counted: true,
      },
      {
        date: '2026-10-02',
        filed_at: '2026-10-09',
        symbol: 'NOVA',
        name: 'Nova Systems',
        side: 'buy',
        amount_low: 250000,
        amount_high: 500000,
        asset_type: 'stock',
        counted: true,
      },
      {
        date: '2026-10-03',
        filed_at: '2026-10-09',
        symbol: null,
        name: 'Muni Bond 2030',
        side: 'buy',
        amount_low: 1000,
        amount_high: 15000,
        asset_type: 'bond',
        counted: false,
      },
    ],
    ...over,
  }
}

export const DIRECTORY: DirectoryEntry[] = [
  {
    kind: 'fund_13f',
    source_ref: '0000000001',
    name: 'Acme Capital',
    subtitle: 'Ada Acme',
    featured: true,
    followed: false,
  },
  {
    kind: 'house_member',
    source_ref: 'M000001',
    name: 'Pat Placeholder',
    subtitle: 'House of Representatives',
    featured: true,
    followed: true,
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

export interface Server {
  follows: Follow[]
  directory: DirectoryEntry[]
  followResult?: { status: number; body: unknown }
  failList?: boolean
}

export function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

/** An in-memory stand-in for the backend routes the Following screen reads and writes. */
export function installServer(authFetch: ReturnType<typeof vi.fn>, server: Server) {
  authFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/followed-portfolios' && method === 'GET')
      return server.failList ? json({ detail: 'down' }, 500) : json({ follows: server.follows })
    if (url.startsWith('/api/followed-portfolios/directory')) {
      const q = new URL(url, 'http://x').searchParams.get('q') ?? ''
      const results =
        q.length < 2
          ? server.directory
          : server.directory.filter((d) => d.name.toLowerCase().includes(q.toLowerCase()))
      return json({ results })
    }
    if (url === '/api/followed-portfolios' && method === 'POST') {
      const res = server.followResult ?? {
        status: 200,
        body: { follow: fund({ id: 'fol_new', name: 'Borealis Partners' }), new_filing: false },
      }
      if (res.status === 200) server.follows.push((res.body as { follow: Follow }).follow)
      return json(res.body, res.status)
    }
    const id = decodeURIComponent(url.split('/')[3] ?? '')
    const at = server.follows.findIndex((f) => f.id === id)
    if (method === 'DELETE') {
      const [gone] = server.follows.splice(at, 1)
      return json({ follow: gone, new_filing: false })
    }
    if (url.endsWith('/refresh')) {
      const f = server.follows[at]!
      f.last_checked_at = '2026-11-10T09:30:00Z'
      return json({ follow: f, new_filing: false })
    }
    return json({ detail: `unhandled ${method} ${url}` }, 404)
  })
}
