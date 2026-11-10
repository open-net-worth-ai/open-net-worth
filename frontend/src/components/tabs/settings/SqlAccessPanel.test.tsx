// @vitest-environment jsdom
//
// Settings → Developer → SQL access.
//
// Coverage:
//  - the kill switch renders from GET and PUTs on flip
//  - the query log renders SQL, row count, duration, origin and an ok/refused badge
//  - a REFUSED query shows its reason (the owner needs to see attempts, not just successes)
//  - loading / empty / error states — the empty state's copy changes with the switch
//  - relativeTime's boundaries
//  - on Postgres, an unavailable SQL login shows its reason and the one command that fixes it

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { SqlAccessPanel, relativeTime } from './SqlAccessPanel'
import { clearQueryCache } from '../../../state/queryCache'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const OK_ROW = {
  id: 'sqlq_1',
  ts: new Date().toISOString(),
  sql: 'SELECT payee_id, COUNT(*) FROM transactions GROUP BY payee_id',
  ok: true,
  error: null,
  row_count: 42,
  truncated: false,
  elapsed_ms: 12.7,
  origin: 'web',
}

const REFUSED_ROW = {
  id: 'sqlq_2',
  ts: new Date().toISOString(),
  sql: 'SELECT * FROM secret_config',
  ok: false,
  error: 'Refused: secret_config is not readable through this tool.',
  row_count: 0,
  truncated: false,
  elapsed_ms: 0.4,
  origin: 'web',
}

function jsonOnce(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) })
}

/** Route each path to its fixture, so ordering between the two hooks never matters. */
function routeFetch(config: Record<string, unknown>, queries: unknown[]) {
  mockAuthFetch.mockImplementation((path: string, init?: RequestInit) => {
    if (path.startsWith('/api/config/sql-query')) {
      if (init?.method === 'PUT') {
        return jsonOnce({ enabled: JSON.parse(String(init.body)).enabled })
      }
      return jsonOnce(config)
    }
    if (path.startsWith('/api/sql-queries')) return jsonOnce({ queries })
    return jsonOnce({})
  })
}

const toast = vi.fn()

beforeEach(() => {
  clearQueryCache()
  mockAuthFetch.mockReset()
  toast.mockReset()
})
afterEach(cleanup)

describe('SqlAccessPanel', () => {
  it('renders the kill switch from the server state', async () => {
    routeFetch({ enabled: true }, [])
    render(<SqlAccessPanel toast={toast} />)
    const toggle = await screen.findByRole('switch', { name: 'Direct SQL reads' })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
  })

  it('renders as off when the server says off', async () => {
    routeFetch({ enabled: false }, [])
    render(<SqlAccessPanel toast={toast} />)
    const toggle = await screen.findByRole('switch', { name: 'Direct SQL reads' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
  })

  it('says why SQL is not working and offers the one command that fixes it', async () => {
    routeFetch(
      {
        enabled: true,
        available: false,
        reason: "the app's database login (onw_app) isn't allowed to create the restricted login.",
        fix: 'ALTER ROLE "onw_app" CREATEROLE;',
      },
      [],
    )
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('Not working on this database')).toBeTruthy()
    expect(screen.getByText(/isn't allowed to create the restricted login/)).toBeTruthy()
    expect(screen.getByText('ALTER ROLE "onw_app" CREATEROLE;')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })

  it('shows no warning when SQL is available', async () => {
    routeFetch({ enabled: true, available: true, reason: '', fix: '' }, [])
    render(<SqlAccessPanel toast={toast} />)
    await screen.findByRole('switch', { name: 'Direct SQL reads' })
    expect(screen.queryByText('Not working on this database')).toBeNull()
  })

  it('PUTs the new value when flipped off', async () => {
    routeFetch({ enabled: true }, [])
    render(<SqlAccessPanel toast={toast} />)
    const toggle = await screen.findByRole('switch', { name: 'Direct SQL reads' })
    fireEvent.click(toggle)
    await waitFor(() => {
      const put = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/config/sql-query' && c[1]?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(JSON.parse(String(put![1].body))).toEqual({ enabled: false })
    })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('SQL access blocked'))
  })

  it('surfaces a save failure instead of silently reverting', async () => {
    mockAuthFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path.startsWith('/api/config/sql-query')) {
        if (init?.method === 'PUT') return jsonOnce({}, false, 500)
        return jsonOnce({ enabled: true })
      }
      return jsonOnce({ queries: [] })
    })
    render(<SqlAccessPanel toast={toast} />)
    fireEvent.click(await screen.findByRole('switch', { name: 'Direct SQL reads' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not save (HTTP 500)'))
  })

  it('lists a successful query with its SQL, rows and duration', async () => {
    routeFetch({ enabled: true }, [OK_ROW])
    const { container } = render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText(OK_ROW.sql)).toBeTruthy()
    expect(screen.getByText('42 rows')).toBeTruthy()
    expect(screen.getByText('13 ms')).toBeTruthy()
    // Status is a dot, not a pill: at one row per query the badge was the loudest thing
    // on the line, and "ok" is the boring case that should recede.
    expect(container.querySelector('.sqla-dot.ok')).toBeTruthy()
  })

  it("hides the origin when it is just the owner's own browser", async () => {
    // "web" on every row is noise. Anything else is the signal.
    routeFetch({ enabled: true }, [OK_ROW])
    render(<SqlAccessPanel toast={toast} />)
    await screen.findByText(OK_ROW.sql)
    expect(screen.queryByText('web')).toBeNull()
  })

  it('shows the origin when a query did NOT come from the browser', async () => {
    routeFetch({ enabled: true }, [{ ...OK_ROW, origin: 'scheduled' }])
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('scheduled')).toBeTruthy()
  })

  it('shows a REFUSED query, why, and marks it red', async () => {
    routeFetch({ enabled: true }, [REFUSED_ROW])
    const { container } = render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('refused')).toBeTruthy()
    expect(screen.getByText(REFUSED_ROW.error)).toBeTruthy()
    expect(container.querySelector('.sqla-dot.bad')).toBeTruthy()
  })

  it('a query is collapsed by default and expands on click', async () => {
    // Collapsed truncates with CSS ellipsis rather than scrolling horizontally — a
    // scrollbar per row is noise. Expanded wraps, so nothing needs dragging.
    routeFetch({ enabled: true }, [OK_ROW])
    const { container } = render(<SqlAccessPanel toast={toast} />)
    await screen.findByText(OK_ROW.sql)
    const btn = container.querySelector('.sqla-q') as HTMLButtonElement
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(container.querySelector('.sqla-sql.open')).toBeNull()
    fireEvent.click(btn)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    expect(container.querySelector('.sqla-sql.open')).toBeTruthy()
  })

  it('marks a capped result as capped', async () => {
    routeFetch({ enabled: true }, [{ ...OK_ROW, row_count: 200, truncated: true }])
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('200 rows (capped)')).toBeTruthy()
  })

  it('singularises a one-row result', async () => {
    routeFetch({ enabled: true }, [{ ...OK_ROW, row_count: 1 }])
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('1 row')).toBeTruthy()
  })

  it('empty state invites a question when SQL access is on', async () => {
    routeFetch({ enabled: true }, [])
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('No queries yet')).toBeTruthy()
    expect(screen.getByText(/Ask Lucius something/)).toBeTruthy()
  })

  it('empty state explains itself when SQL access is off', async () => {
    routeFetch({ enabled: false }, [])
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('SQL access is off, so nothing can run.')).toBeTruthy()
  })

  it('error state offers a retry', async () => {
    mockAuthFetch.mockImplementation((path: string) => {
      if (path.startsWith('/api/config/sql-query')) return jsonOnce({ enabled: true })
      return jsonOnce({}, false, 500)
    })
    render(<SqlAccessPanel toast={toast} />)
    expect(await screen.findByText('Could not load the query log')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('renders every row as a DIRECT child of the panel body', async () => {
    // The layout regression this file previously missed, twice. `.rd-panel-body > *` is what
    // gives a row its padding and divider, so ANY wrapper around the list breaks it — and
    // `display: contents` on that wrapper does not save it (the box the padding applied to is
    // dropped, and every row ends up flush against the panel edge). Text-and-class assertions
    // cannot see this; parentage can.
    routeFetch({ enabled: true }, [OK_ROW, REFUSED_ROW])
    const { container } = render(<SqlAccessPanel toast={toast} />)
    await screen.findByText(OK_ROW.sql)
    const body = container.querySelector('.rd-panel-body')
    expect(body).toBeTruthy()
    const rows = Array.from(container.querySelectorAll('.sqla-q'))
    expect(rows.length).toBe(2)
    for (const r of rows) expect(r.parentElement).toBe(body)
    // The section label is a sibling of the rows, not a wrapper around them.
    expect(container.querySelector('.rd-bars-lbl')?.parentElement).toBe(body)
  })

  it('keeps the query list out of the Lucius screen snapshot', async () => {
    // A WHERE clause can embed real amounts and account names.
    routeFetch({ enabled: true }, [OK_ROW])
    const { container } = render(<SqlAccessPanel toast={toast} />)
    await screen.findByText(OK_ROW.sql)
    expect(container.querySelector('[data-ctx-secret]')).toBeTruthy()
  })
})

describe('relativeTime', () => {
  const now = Date.parse('2026-08-23T12:00:00Z')
  it('reads under a minute as "just now"', () => {
    expect(relativeTime('2026-08-23T11:59:30Z', now)).toBe('just now')
  })
  it('reads minutes', () => {
    expect(relativeTime('2026-08-23T11:57:00Z', now)).toBe('3m ago')
  })
  it('reads hours', () => {
    expect(relativeTime('2026-08-23T09:00:00Z', now)).toBe('3h ago')
  })
  it('falls back to a date past a day', () => {
    expect(relativeTime('2026-08-20T12:00:00Z', now)).toMatch(/\d/)
  })
  it('survives an unparseable timestamp', () => {
    expect(relativeTime('not-a-date', now)).toBe('')
  })
})
