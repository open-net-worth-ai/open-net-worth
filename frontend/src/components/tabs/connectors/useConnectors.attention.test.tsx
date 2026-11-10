// @vitest-environment jsdom
//
// The nav's dot reads the same `attention` the list's Attention filter does.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../state/queryCache'
import { useConnectorAttentionCount, useConnectorAttentionIsSignInOnly } from './useConnectors'
import type { Connector } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

function connector(overrides: Partial<Connector> & Pick<Connector, 'id'>): Connector {
  return {
    name: overrides.id,
    kind: 'mcp',
    enabled: true,
    status: 'active',
    pausable: true,
    config: {},
    secrets_public: {},
    tools: [],
    ...overrides,
  }
}

function serve(connectors: Connector[]) {
  authFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ connectors }) })
}

beforeEach(() => {
  clearQueryCache()
  authFetch.mockReset()
})
afterEach(cleanup)

describe('useConnectorAttentionCount', () => {
  it('counts every connector in an error state', async () => {
    serve([
      connector({ id: 'mcp:down', config: { last_status: 'connect_error' } }),
      connector({ id: 'plaid', status: 'error', error: 'Bad key' }),
      connector({ id: 'mcp:fine', config: { last_status: 'ok' } }),
    ])
    const { result } = renderHook(() => useConnectorAttentionCount())
    await waitFor(() => expect(result.current).toBe(2))
  })

  it('never counts a connector that is merely switched off', async () => {
    serve([connector({ id: 'mcp:off', enabled: false, status: 'configured' })])
    const { result } = renderHook(() => useConnectorAttentionCount())
    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    expect(result.current).toBe(0)
  })
})

describe('useConnectorAttentionIsSignInOnly', () => {
  const signedOut = connector({ id: 'mcp:acme', config: { last_status: 'auth_error' } })

  it('is amber while every problem is a sign-in', async () => {
    serve([signedOut, connector({ id: 'mcp:fine', config: { last_status: 'ok' } })])
    const { result } = renderHook(() => useConnectorAttentionIsSignInOnly())
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('goes back to red as soon as something is actually failing', async () => {
    serve([signedOut, connector({ id: 'mcp:down', config: { last_status: 'connect_error' } })])
    const { result } = renderHook(() => ({
      count: useConnectorAttentionCount(),
      signInOnly: useConnectorAttentionIsSignInOnly(),
    }))
    await waitFor(() => expect(result.current.count).toBe(2))
    expect(result.current.signInOnly).toBe(false)
  })
})
