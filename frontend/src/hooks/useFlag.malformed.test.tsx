// @vitest-environment jsdom
// PR #93 FLAG-11: `useFlag('investment_value_source')` on a malformed flag list. A response that
// is not a flag list reads as "not loaded" (undefined → the old UI), never a crash; only a
// well-formed list that lacks the flag throws (the declared-flag guard). Synthetic data only.
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { Component } from 'react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../state/queryCache'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../api/client'
import { useFlag } from './useFlag'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const NAME = 'investment_value_source'

function Probe() {
  const on = useFlag(NAME)
  return <span data-testid="v">{on === undefined ? 'old' : on ? 'on' : 'off'}</span>
}

class Boundary extends Component<{ children: ReactNode }, { message: string | null }> {
  state = { message: null as string | null }
  static getDerivedStateFromError(e: Error) {
    return { message: e.message }
  }
  render() {
    return this.state.message ? (
      <span data-testid="err">{this.state.message}</span>
    ) : (
      this.props.children
    )
  }
}

function serve(body: unknown, ok = true, status = 200) {
  mockAuthFetch.mockResolvedValue({
    ok,
    status,
    json: () => (body instanceof Error ? Promise.reject(body) : Promise.resolve(body)),
  })
}

function flag(enabled: boolean) {
  return {
    name: NAME,
    description: 'Value an investment account by its brokerage total or live holdings.',
    kind: 'release',
    default: false,
    enabled,
    source: 'default',
    stored: null,
    env_var: 'ONW_FLAG_INVESTMENT_VALUE_SOURCE',
    owner: 'tests',
    added: '2026-11-10',
    remove_by: '2027-03-01',
  }
}

beforeEach(() => {
  clearQueryCache()
  mockAuthFetch.mockReset()
})
afterEach(cleanup)

describe('FLAG-11: a response that is not a flag list reads as the old UI', () => {
  it.each([
    ['{}', {}],
    ['null', null],
    ['a string for flags', { flags: 'x' }],
    ['an object for flags', { flags: {} }],
    ['a bare array', []],
  ])('%s → old, no crash', async (_n, body) => {
    serve(body)
    render(
      <Boundary>
        <Probe />
      </Boundary>,
    )
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByTestId('err')).toBeNull()
    expect(screen.getByTestId('v').textContent).toBe('old')
  })

  it('an HTML error body (json() rejects) stays on the old UI', async () => {
    serve(new SyntaxError('Unexpected token <'))
    render(
      <Boundary>
        <Probe />
      </Boundary>,
    )
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByTestId('err')).toBeNull()
    expect(screen.getByTestId('v').textContent).toBe('old')
  })

  it('a 500 stays on the old UI', async () => {
    serve({ detail: 'boom' }, false, 500)
    render(
      <Boundary>
        <Probe />
      </Boundary>,
    )
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByTestId('err')).toBeNull()
    expect(screen.getByTestId('v').textContent).toBe('old')
  })
})

describe('FLAG-11: a well-formed list', () => {
  it('reads the flag on', async () => {
    serve({ flags: [flag(true)], locked: false })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('v').textContent).toBe('on'))
  })

  it('reads the flag off', async () => {
    serve({ flags: [flag(false)], locked: false })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('v').textContent).toBe('off'))
  })

  it('an empty list lacking the flag throws the declared-flag guard', async () => {
    serve({ flags: [], locked: false })
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <Boundary>
        <Probe />
      </Boundary>,
    )
    await waitFor(() => expect(screen.getByTestId('err').textContent).toContain(NAME))
    expect(screen.getByTestId('err').textContent).toContain('core/feature_flags.py')
    errors.mockRestore()
  })
})
