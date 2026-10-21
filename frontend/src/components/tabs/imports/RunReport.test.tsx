// @vitest-environment jsdom
//
// A run opened as a report: mismatches before matches, the live state of a running run, what a
// failed or interrupted one still shows.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { RunReport } from './RunReport'
import { clearQueryCache } from '../../../state/queryCache'
import { importRun, run } from './importsFixtures'
import type { RunRecord } from './runs'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

let current: RunRecord
let posts: string[]

beforeEach(() => {
  posts = []
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(url)
    return Promise.resolve({ ok: true, json: () => Promise.resolve(current) })
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

const open = () => render(<RunReport runId={current.id} onBack={() => {}} />)

describe('RunReport', () => {
  it('lists the mismatches first and folds the checks that match', async () => {
    current = importRun()
    open()
    expect(await screen.findByText('3 checks, 2 match')).toBeTruthy()
    expect(screen.getByText('1 to look at')).toBeTruthy()
    expect(screen.getByText('Checking: transactions')).toBeTruthy()
    expect(screen.getByText('YNAB has 4')).toBeTruthy()
    // a transaction count is a whole number, never "4.00"
    expect(screen.getByText('-1')).toBeTruthy()
    expect(screen.getByText('4')).toBeTruthy()
    expect(screen.queryByText('4.00')).toBeNull()
    expect(screen.queryByText('Savings: balance')).toBeNull()

    fireEvent.click(screen.getByText('Show all 2 that match'))
    expect(screen.getByText('Savings: balance')).toBeTruthy()
    expect(screen.getAllByText('90.00').length).toBeGreaterThan(0) // money keeps two decimals
  })

  it('shows the results per item, what needs attention and the backup', async () => {
    current = importRun()
    open()
    const results = (await screen.findByText('Transactions')).closest('tr') as HTMLElement
    expect(
      within(results)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Transactions', '3', '0', '1', '0'])
    expect(screen.getByText('Needs attention')).toBeTruthy()
    expect(screen.getByText(/You deleted this transaction here earlier/)).toBeTruthy()
    expect(screen.getByText(/Backup taken first: backup-2026-10-12/)).toBeTruthy()
    expect(screen.getByText('First import')).toBeTruthy()
  })

  it('a verify run shows its verification and nothing about setup', async () => {
    current = run({ kind: 'verify', verification: importRun().verification })
    open()
    expect(await screen.findByText('Verification')).toBeTruthy()
    expect(screen.queryByText('Setup')).toBeNull()
    expect(screen.queryByText('Results')).toBeNull()
  })

  it('a preview says nothing was written', async () => {
    current = run()
    open()
    expect(await screen.findByText('Would create')).toBeTruthy()
    expect(screen.getByText('Nothing was written.')).toBeTruthy()
  })

  it('a running run shows its phase, progress and streaming log, and cannot be archived', async () => {
    current = importRun({
      status: 'running',
      finished_at: null,
      outcome: '',
      phase: 'booking transactions',
      progress: { done: 2, total: 4 },
      verification: null,
      problems: [],
    })
    open()
    expect(await screen.findAllByText('booking transactions')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Run progress' })).toBeTruthy()
    expect(screen.getByText(/Accounts: 1 created/)).toBeTruthy()
    expect((screen.getByText('Archive').closest('button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a failed run shows why, above what it captured before it stopped', async () => {
    current = importRun({ status: 'failed', error: 'YNAB HTTP 429', verification: null })
    open()
    expect(await screen.findByText('YNAB HTTP 429')).toBeTruthy()
    expect(screen.getByText('This run failed')).toBeTruthy()
    expect(screen.getByText('Results')).toBeTruthy()
  })

  it('a run left running by a restart reads as Interrupted', async () => {
    current = importRun({
      status: 'interrupted',
      error: 'The server stopped while this run was in progress.',
      verification: null,
    })
    open()
    expect((await screen.findAllByText('Interrupted')).length).toBeGreaterThan(0)
    expect(screen.getByText(/server stopped/)).toBeTruthy()
  })

  it('archives and unarchives through the server', async () => {
    current = importRun()
    open()
    fireEvent.click(await screen.findByText('Archive'))
    expect(posts).toEqual([`/api/imports/runs/${current.id}/archive`])
  })
})
