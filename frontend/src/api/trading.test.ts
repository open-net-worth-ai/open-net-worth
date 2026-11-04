import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authFetch } from './client'
import { invalidateQueryCache } from '../state/queryCache'
import { tradingApi } from './trading'

vi.mock('./client', () => ({ authFetch: vi.fn() }))
vi.mock('../state/queryCache', () => ({ invalidateQueryCache: vi.fn() }))
beforeEach(() => vi.clearAllMocks())

describe('trading execution responses', () => {
  it('surfaces domain refusal text and does not pretend that the write succeeded', async () => {
    vi.mocked(authFetch).mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { code: 'conflict', message: 'Live account is not verified' } }),
        { status: 409 },
      ),
    )
    await expect(tradingApi.action('run-demo', 'start')).rejects.toThrow(
      'Live account is not verified',
    )
    expect(invalidateQueryCache).not.toHaveBeenCalled()
  })
  it('treats a failed script receipt as failure even when the HTTP request succeeded', async () => {
    vi.mocked(authFetch).mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: 'Isolation unavailable' })),
    )
    await expect(tradingApi.script('run-demo', 'print(1)')).rejects.toThrow('Isolation unavailable')
  })
  it('invalidates trading projections after a successful lifecycle change', async () => {
    vi.mocked(authFetch).mockResolvedValue(
      new Response(JSON.stringify({ id: 'run-demo', state: 'paused' })),
    )
    expect(await tradingApi.action('run-demo', 'pause')).toMatchObject({ state: 'paused' })
    expect(invalidateQueryCache).toHaveBeenCalledWith('/api/trading')
  })
})
