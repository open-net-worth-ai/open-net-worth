// @vitest-environment jsdom
//
// B20 async — the statements list's lifecycle rows: parsing / ready-to-review / interrupted /
// failed. Each state shows exactly one action (never the ordinary row's Download/Delete
// pair), and lifecycle rows sort ABOVE plain committed statements.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { StatementsModal } from './StatementsSection'

const authFetch = vi.hoisted(() => vi.fn<(url: string, init?: RequestInit) => Promise<unknown>>())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: async () => body, text: async () => JSON.stringify(body) })
}

const PARSING_ROW = {
  id: 'stmt_parsing',
  filename: 'jan-parsing.pdf',
  created_at: '2026-01-01T00:00:00Z',
  parse_status: 'parsing',
}
const READY_ROW = {
  id: 'stmt_ready',
  filename: 'feb-ready.csv',
  created_at: '2026-02-01T00:00:00Z',
  parse_status: 'ready',
  batch_ids: [],
}
const INTERRUPTED_ROW = {
  id: 'stmt_interrupted',
  filename: 'mar-interrupted.csv',
  created_at: '2026-03-01T00:00:00Z',
  parse_status: 'interrupted',
}
const FAILED_ROW = {
  id: 'stmt_failed',
  filename: 'apr-failed.csv',
  created_at: '2026-04-01T00:00:00Z',
  parse_status: 'failed',
  parse_error: 'This does not look like a CSV file.',
}
const NORMAL_ROW = {
  id: 'stmt_normal',
  filename: 'may-normal.csv',
  created_at: '2026-05-01T00:00:00Z',
  parse_status: 'ready',
  batch_ids: ['batch_1'],
  reconcile: { status: 'ok' },
}

function mockList(rows: unknown[]) {
  authFetch.mockImplementation((url: string) => {
    if (url.includes('/statements') && !url.includes('parse-status') && !url.includes('/preview')) {
      return jsonResponse({ statements: rows })
    }
    if (url.includes('/parse-status')) {
      return jsonResponse({ parse_status: 'parsing', progress: null, error: null })
    }
    return jsonResponse({})
  })
}

function open() {
  return render(<StatementsModal accountId="acct1" accountName="Checking" onClose={() => {}} />)
}

describe('StatementsModal — B20 lifecycle rows', () => {
  it('a parsing row shows a live line and no action', async () => {
    mockList([PARSING_ROW])
    open()
    await screen.findByText('jan-parsing.pdf')
    const row = screen.getByText('jan-parsing.pdf').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).queryByRole('button', { name: /Review|Re-run|Download|Delete/ })).toBeNull()
    expect(within(row).getByText(/Reading…/)).toBeTruthy()
    // Plain text, not a link — nothing to view yet.
    expect(within(row).queryByRole('button', { name: 'jan-parsing.pdf' })).toBeNull()
  })

  it('a ready-but-uncommitted row offers Review → and calls back with its id', async () => {
    mockList([READY_ROW])
    const onReviewStatement = vi.fn()
    render(
      <StatementsModal
        accountId="acct1"
        onClose={() => {}}
        onReviewStatement={onReviewStatement}
      />,
    )
    await screen.findByText('feb-ready.csv')
    const row = screen.getByText('feb-ready.csv').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).getByText('Ready to review')).toBeTruthy()
    fireEvent.click(within(row).getByRole('button', { name: /Review/ }))
    expect(onReviewStatement).toHaveBeenCalledWith('stmt_ready', 'feb-ready.csv')
    expect(within(row).queryByRole('button', { name: 'Download' })).toBeNull()
  })

  it('an interrupted row shows a warn badge and Re-run, which re-triggers async parsing', async () => {
    mockList([INTERRUPTED_ROW])
    open()
    await screen.findByText('mar-interrupted.csv')
    const row = screen.getByText('mar-interrupted.csv').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).getByText('interrupted')).toBeTruthy()
    expect(within(row).getByText('Reading was interrupted')).toBeTruthy()

    fireEvent.click(within(row).getByRole('button', { name: 'Re-run' }))

    await waitFor(() => {
      const call = authFetch.mock.calls.find(([url]) =>
        url.includes('/statements/stmt_interrupted/preview'),
      )
      if (!call) throw new Error('no preview request was made')
      const init = call[1] as unknown as { method: string; body: FormData }
      expect(init.method).toBe('POST')
      expect(init.body.get('force_reparse')).toBe('true')
      expect(init.body.get('mode')).toBe('async')
    })
  })

  it('a failed row shows the error on title= and offers Re-run', async () => {
    mockList([FAILED_ROW])
    open()
    await screen.findByText('apr-failed.csv')
    const row = screen.getByText('apr-failed.csv').closest('.acct-stmt-row') as HTMLElement
    const badge = within(row).getByText('failed')
    expect(badge.closest('[title]')?.getAttribute('title')).toBe(FAILED_ROW.parse_error)
    expect(within(row).getByRole('button', { name: 'Re-run' })).toBeTruthy()
  })

  it('a failed row reveals its parse_error on tap, via a disclosure — not only title=', async () => {
    mockList([FAILED_ROW])
    open()
    await screen.findByText('apr-failed.csv')
    const row = screen.getByText('apr-failed.csv').closest('.acct-stmt-row') as HTMLElement

    // Not reachable on touch until expanded.
    expect(within(row).queryByText(FAILED_ROW.parse_error)).toBeNull()
    const toggle = within(row).getByRole('button', { name: /Couldn.t read this file/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(toggle)

    expect(within(row).getByText(FAILED_ROW.parse_error)).toBeTruthy()
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    // Still exactly one ACTION on the row — the disclosure is not a second action.
    expect(within(row).getAllByRole('button', { name: 'Re-run' })).toHaveLength(1)
  })

  it('a failed row with no parse_error message falls back to plain (non-toggling) text', async () => {
    mockList([{ ...FAILED_ROW, id: 'stmt_failed_noerr', parse_error: undefined }])
    open()
    await screen.findByText('apr-failed.csv')
    const row = screen.getByText('apr-failed.csv').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).queryByRole('button', { name: /Couldn.t read this file/ })).toBeNull()
    expect(within(row).getByText('Couldn’t read this file')).toBeTruthy()
  })

  it('a normal committed row is untouched — Download/Delete, no lifecycle chrome', async () => {
    mockList([NORMAL_ROW])
    open()
    await screen.findByText('may-normal.csv')
    const row = screen.getByText('may-normal.csv').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).getByRole('button', { name: 'Download' })).toBeTruthy()
    expect(within(row).getByRole('button', { name: /Delete/ })).toBeTruthy()
    expect(within(row).queryByText('Ready to review')).toBeNull()
  })

  it('lifecycle rows sort above ordinary committed rows', async () => {
    mockList([NORMAL_ROW, PARSING_ROW, READY_ROW])
    open()
    await screen.findByText('may-normal.csv')
    const names = screen
      .getAllByText(/\.pdf$|\.csv$/)
      .map((el) => el.textContent)
      .filter((t): t is string => !!t)
    // Both lifecycle rows (parsing, ready) precede the one ordinary committed row.
    expect(names.indexOf('jan-parsing.pdf')).toBeLessThan(names.indexOf('may-normal.csv'))
    expect(names.indexOf('feb-ready.csv')).toBeLessThan(names.indexOf('may-normal.csv'))
  })

  it('pauses the parsing row poll while the tab is hidden', async () => {
    mockList([PARSING_ROW])
    open()
    await screen.findByText('jan-parsing.pdf')
    await waitFor(() =>
      expect(authFetch.mock.calls.some(([u]) => u.includes('parse-status'))).toBe(true),
    )
    const callsBeforeHidden = authFetch.mock.calls.filter(([u]) =>
      u.includes('parse-status'),
    ).length

    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((r) => setTimeout(r, 50))
    const callsWhileHidden = authFetch.mock.calls.filter(([u]) => u.includes('parse-status')).length
    expect(callsWhileHidden).toBe(callsBeforeHidden) // no new poll fired once hidden

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
  })

  it('a legacy row with no parse_status at all reads as an ordinary row', async () => {
    mockList([{ id: 'stmt_legacy', filename: 'legacy.csv', created_at: '2025-01-01T00:00:00Z' }])
    open()
    await screen.findByText('legacy.csv')
    const row = screen.getByText('legacy.csv').closest('.acct-stmt-row') as HTMLElement
    expect(within(row).getByRole('button', { name: 'Download' })).toBeTruthy()
  })
})

describe('StatementsModal — one clear status per row (owner, 2026-10-06)', () => {
  const badgeIn = (filename: string, text: string) => {
    const row = screen.getByText(filename).closest('.acct-stmt-row') as HTMLElement
    return within(row).queryByText(text)
  }

  it('labels every row: parsing…, parsed, imported, failed, interrupted', async () => {
    mockList([PARSING_ROW, READY_ROW, INTERRUPTED_ROW, FAILED_ROW, NORMAL_ROW])
    open()
    await screen.findByText('may-normal.csv')
    expect(badgeIn('jan-parsing.pdf', 'parsing…')).toBeTruthy()
    expect(badgeIn('feb-ready.csv', 'parsed')).toBeTruthy()
    expect(badgeIn('mar-interrupted.csv', 'interrupted')).toBeTruthy()
    expect(badgeIn('apr-failed.csv', 'failed')).toBeTruthy()
    expect(badgeIn('may-normal.csv', 'imported')).toBeTruthy()
    // a parsed-but-unfiled statement is never called imported
    expect(badgeIn('feb-ready.csv', 'imported')).toBeNull()
  })

  it('a statement filed with nothing to write (no batch) reads imported, not parsed', async () => {
    mockList([{ ...READY_ROW, filename: 'jun-filed.csv', filed: true }])
    open()
    await screen.findByText('jun-filed.csv')
    expect(badgeIn('jun-filed.csv', 'imported')).toBeTruthy()
    expect(badgeIn('jun-filed.csv', 'parsed')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
  })
})
