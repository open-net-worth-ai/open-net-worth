import { beforeEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.fn()
vi.mock('../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { querySource } from './dataQuery'

function ndjson(lines: object[], status = 200): Response {
  return new Response(lines.map((l) => JSON.stringify(l)).join('\n') + '\n', {
    status,
    headers: { 'content-type': 'application/x-ndjson' },
  })
}

function sentQueries(call = 0): { refId: string; source: string; params: object }[] {
  const url = new URL(String(authFetch.mock.calls[call]![0]), 'http://x')
  return JSON.parse(url.searchParams.get('q') ?? '{}').queries
}

describe('querySource', () => {
  beforeEach(() => {
    // Braces matter: a function returned from beforeEach is run as its teardown.
    authFetch.mockReset()
  })

  it('sends everything asked in one tick as ONE request and resolves each by refId', async () => {
    authFetch.mockImplementation(async (url: string) => {
      const { queries } = JSON.parse(new URL(url, 'http://x').searchParams.get('q') ?? '{}')
      return ndjson(
        queries.map((q: { refId: string; source: string }) => ({
          refId: q.refId,
          status: 200,
          data: { from: q.source },
        })),
      )
    })
    const [a, b] = await Promise.all([
      querySource('net_worth', {}, '/api/net-worth'),
      querySource('planning_summary', { ym: '2026-11' }, '/api/planning/summary?ym=2026-11'),
    ])
    expect(authFetch).toHaveBeenCalledTimes(1)
    expect(sentQueries().map((q) => [q.source, q.params])).toEqual([
      ['net_worth', {}],
      ['planning_summary', { ym: '2026-11' }],
    ])
    expect(a).toEqual({ from: 'net_worth' })
    expect(b).toEqual({ from: 'planning_summary' })
  })

  it('drops undefined parameters rather than sending them', async () => {
    authFetch.mockResolvedValue(ndjson([]))
    authFetch.mockResolvedValueOnce(ndjson([]))
    await querySource('commitments_coverage', { months: 12, floor: undefined }, '/x').catch(
      () => {},
    )
    expect(sentQueries()[0]!.params).toEqual({ months: 12 })
  })

  it('rejects a query whose answer is an error, the way fetchJson would', async () => {
    authFetch.mockImplementation(async () => {
      const { refId } = sentQueries()[0]!
      return ndjson([{ refId, status: 409, error: 'nope' }])
    })
    await expect(querySource('net_worth', {}, '/api/net-worth')).rejects.toThrow(
      '/api/net-worth → HTTP 409',
    )
  })

  it('falls back to each GET when the batch request fails', async () => {
    authFetch.mockImplementation(async (url: string) =>
      url.startsWith('/api/query')
        ? new Response('', { status: 404 })
        : new Response(JSON.stringify({ via: url }), { status: 200 }),
    )
    await expect(querySource('net_worth', {}, '/api/net-worth')).resolves.toEqual({
      via: '/api/net-worth',
    })
  })

  it('falls back to its GET for a query the stream never answered', async () => {
    authFetch.mockImplementation(async (url: string) => {
      if (!url.startsWith('/api/query')) return new Response('{"via":"get"}', { status: 200 })
      const [first] = sentQueries()
      return ndjson([{ refId: first!.refId, status: 200, data: 1 }])
    })
    const [a, b] = await Promise.all([
      querySource('net_worth', {}, '/api/net-worth'),
      querySource('net_worth_stats', {}, '/api/net-worth/stats'),
    ])
    expect(a).toBe(1)
    expect(b).toEqual({ via: 'get' })
  })
})
