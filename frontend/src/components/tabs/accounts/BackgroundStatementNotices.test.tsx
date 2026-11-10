// @vitest-environment jsdom
//
// Owner, 2026-10-06: a statement left reading in the background must say when it is done — "at
// least in app as a popup". The popup is app-wide (mounted beside the drawer surfaces), driven by
// the backgroundStatements store, and its "Review" hands the statement to the Accounts tab.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { BackgroundStatementNotices } from './BackgroundStatementNotices'
import {
  backgroundStatements,
  takeStatementReview,
  trackBackgroundStatement,
} from './backgroundStatements'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

function status(parse_status: string) {
  authFetch.mockImplementation(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ parse_status, progress: null, error: null }),
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
  takeStatementReview()
})
afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('BackgroundStatementNotices', () => {
  it('pops up when a background read is ready, and Review hands it to the Accounts tab', async () => {
    trackBackgroundStatement({ id: 's1', accountId: 'acc1', filename: 'oct.pdf' })
    status('ready')
    const onOpenAccounts = vi.fn()
    render(<BackgroundStatementNotices onOpenAccounts={onOpenAccounts} />)

    expect(await screen.findByText('Ready to review')).toBeTruthy()
    expect(screen.getByText('oct.pdf')).toBeTruthy()
    expect(backgroundStatements()).toEqual([]) // reported once, no longer watched

    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    expect(onOpenAccounts).toHaveBeenCalledTimes(1)
    expect(takeStatementReview()).toEqual({ id: 's1', accountId: 'acc1', filename: 'oct.pdf' })
    await waitFor(() => expect(screen.queryByText('Ready to review')).toBeNull())
  })

  it('a failed read says so, with no Review', async () => {
    trackBackgroundStatement({ id: 's2', accountId: 'acc1', filename: 'bad.csv' })
    status('failed')
    render(<BackgroundStatementNotices />)

    expect(await screen.findByText('Couldn’t read')).toBeTruthy()
    expect(screen.getByText('bad.csv')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByText('Couldn’t read')).toBeNull())
  })

  it('a read just sent to the background shows as reading, then turns into Ready in the same pill', async () => {
    // Owner, 2026-10-25: "Continue in background" put up a register toast AND, when the read
    // finished quickly, this popup beside it — two pills, two centres. Now one pill changes state.
    trackBackgroundStatement({
      id: 's4',
      accountId: 'acc1',
      filename: 'fast.xlsx',
      startedAt: Date.now(),
    })
    status('parsing')
    render(<BackgroundStatementNotices />)

    expect(await screen.findByText('Reading in the background')).toBeTruthy()
    expect(screen.getByText('fast.xlsx')).toBeTruthy()

    status('ready')
    expect(await screen.findByText('Ready to review', {}, { timeout: 6000 })).toBeTruthy()
    expect(screen.queryByText('Reading in the background')).toBeNull()
    expect(screen.getAllByText('fast.xlsx')).toHaveLength(1)
  }, 10000)

  it('the reading pill steps aside after a few seconds while the read carries on', async () => {
    trackBackgroundStatement({
      id: 's5',
      accountId: null,
      filename: 'slow.xlsx',
      startedAt: Date.now() - 4700,
    })
    status('parsing')
    render(<BackgroundStatementNotices />)

    expect(screen.getByText('Reading in the background')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText('Reading in the background')).toBeNull())
    expect(backgroundStatements().map((s) => s.id)).toEqual(['s5'])
  })

  it('stays quiet and keeps watching while the read is still going', async () => {
    trackBackgroundStatement({ id: 's3', accountId: null, filename: 'slow.pdf' })
    status('parsing')
    render(<BackgroundStatementNotices />)

    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
    expect(backgroundStatements().map((s) => s.id)).toEqual(['s3'])
  })
})
