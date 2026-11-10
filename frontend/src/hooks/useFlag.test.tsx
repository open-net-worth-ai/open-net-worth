// @vitest-environment jsdom
// useFlag — reads the resolved flag list through the query cache; unknown names throw; the
// vitest override seam beats the server.
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { Component } from 'react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../state/queryCache'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../api/client'
import { overrideFlagsForTests, useFlag } from './useFlag'
import type { FeatureFlag } from './useFlag'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function flagRow(over: Partial<FeatureFlag> = {}): FeatureFlag {
  return {
    name: 'example_flag',
    description: 'Shows the new thing.',
    kind: 'release',
    default: false,
    enabled: false,
    source: 'default',
    stored: null,
    env_var: 'ONW_FLAG_EXAMPLE_FLAG',
    owner: 'tests',
    added: '2026-10-25',
    remove_by: '2099-12-31',
    ...over,
  }
}

function serve(flags: FeatureFlag[]) {
  mockAuthFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ flags, locked: false }),
  })
}

function Probe({ name }: { name: string }) {
  const on = useFlag(name)
  return <span data-testid="v">{on === undefined ? 'loading' : on ? 'new' : 'old'}</span>
}

beforeEach(() => {
  clearQueryCache()
  mockAuthFetch.mockReset()
})
afterEach(cleanup)

it('is undefined until loaded, then the resolved value', async () => {
  serve([flagRow({ enabled: true, source: 'stored', stored: true })])
  render(<Probe name="example_flag" />)
  expect(screen.getByTestId('v').textContent).toBe('loading')
  await waitFor(() => expect(screen.getByTestId('v').textContent).toBe('new'))
})

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

it('throws for a name the server does not declare', async () => {
  serve([flagRow()])
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
  render(
    <Boundary>
      <Probe name="no_such_flag" />
    </Boundary>,
  )
  await waitFor(() => expect(screen.getByTestId('err').textContent).toContain('no_such_flag'))
  errors.mockRestore()
})

describe.each([false, true])('overrideFlagsForTests (example_flag=%s)', (value) => {
  let restore: () => void
  beforeEach(() => {
    serve([flagRow({ enabled: !value })])
    restore = overrideFlagsForTests({ example_flag: value })
  })
  afterEach(() => restore())

  it('beats the server, from the first render, without fetching', () => {
    render(<Probe name="example_flag" />)
    expect(screen.getByTestId('v').textContent).toBe(value ? 'new' : 'old')
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })
})
