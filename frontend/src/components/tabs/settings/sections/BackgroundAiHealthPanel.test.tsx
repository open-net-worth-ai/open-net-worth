// @vitest-environment jsdom
//
// Settings → AI usage → Background AI tasks (BackgroundAiHealthPanel.tsx): a failing task says
// so in plain words — on 2026-11-19 the analyst was refused for six days with nothing on screen.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

import { BackgroundAiHealthPanel } from './BackgroundAiHealthPanel'
import type { BackgroundAiTask } from './BackgroundAiHealthPanel'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function reply(body: unknown, status = 200) {
  mockAuthFetch.mockImplementation(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
}

function task(overrides: Partial<BackgroundAiTask>): BackgroundAiTask {
  return {
    source: 'analysis',
    label: 'Intel analysis',
    status: 'working',
    calls_24h: 12,
    failures_24h: 0,
    last_success_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    last_failure_at: null,
    problem: null,
    ...overrides,
  }
}

const SINCE = new Date(Date.now() - 2 * 3600_000).toISOString()

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
})
afterEach(() => cleanup())

describe('BackgroundAiHealthPanel', () => {
  it('shows a failing task with its plain problem and counts', async () => {
    reply({
      since: SINCE,
      tasks: [
        task({
          status: 'failing',
          failures_24h: 40,
          calls_24h: 40,
          last_success_at: null,
          last_failure_at: new Date().toISOString(),
          problem: 'The AI provider declined the request (reasoning_extraction)',
        }),
      ],
    })
    render(<BackgroundAiHealthPanel />)
    const row = await screen.findByRole('row', { name: /Intel analysis/ })
    expect(within(row).getByText('Failing')).toBeTruthy()
    expect(within(row).getByText('40 of 40 failed')).toBeTruthy()
    expect(within(row).getByText(/declined the request \(reasoning_extraction\)/)).toBeTruthy()
    expect(within(row).getByText('Never')).toBeTruthy()
  })

  it('shows a working task without a problem', async () => {
    reply({ since: SINCE, tasks: [task({})] })
    render(<BackgroundAiHealthPanel />)
    const row = await screen.findByRole('row', { name: /Intel analysis/ })
    expect(within(row).getByText('Working')).toBeTruthy()
    expect(within(row).getByText('0 of 12 failed')).toBeTruthy()
    expect(within(row).getByText('5m ago')).toBeTruthy()
  })

  it('says when nothing has run yet, and since when it counts', async () => {
    reply({ since: SINCE, tasks: [] })
    render(<BackgroundAiHealthPanel />)
    expect(await screen.findByText('No background AI tasks have run yet')).toBeTruthy()
    expect(screen.getByText(/since the server started/i)).toBeTruthy()
  })

  it('treats an unexpected reply as empty rather than crashing', async () => {
    reply({})
    render(<BackgroundAiHealthPanel />)
    expect(await screen.findByText('No background AI tasks have run yet')).toBeTruthy()
  })

  it('offers a retry when the server cannot be reached', async () => {
    reply({ detail: 'boom' }, 500)
    render(<BackgroundAiHealthPanel />)
    await waitFor(() => expect(screen.getByText('Could not load background AI tasks')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})
