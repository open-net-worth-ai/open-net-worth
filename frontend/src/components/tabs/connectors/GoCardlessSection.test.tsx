// @vitest-environment jsdom
//
// One GoCardless connector holds several banks. There is no code to paste after consent:
// coming back from the bank, the section re-reads every requisition itself (Check links); an
// ended consent says to link that bank again while the other banks stay listed.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { GoCardlessSection } from './GoCardlessSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const bank = (over: Record<string, unknown> = {}) => ({
  key: 'k1',
  institution_id: 'ACME_BANK_GB',
  institution_name: 'ACME Bank',
  country: 'GB',
  status: 'LN',
  consent_expires_at: null,
  linked_at: '2026-11-01T00:00:00+00:00',
  accounts: 2,
  renewing: false,
  ...over,
})

const status = (banks: ReturnType<typeof bank>[]) => ({
  configured: true,
  enabled: true,
  has_credentials: true,
  secret_id: 'SID',
  has_link: banks.some((b) => b.status === 'LN'),
  banks,
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null,
  last_caution: null,
})

type Status = ReturnType<typeof status>

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

function serve(current: Status, afterCheck: Status = current) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/gocardless/status') return okJson(current)
    if (url === '/api/gocardless/check-link' && init?.method === 'POST') return okJson(afterCheck)
    if (url.startsWith('/api/gocardless/banks/')) return okJson({ removed: true, banks_left: 1 })
    if (url.startsWith('/api/connectors/')) return okJson({ status: 'ok', accounts: [] })
    return okJson([])
  })
}

const calls = (url: string) => authFetch.mock.calls.filter((c) => c[0] === url).length

describe('GoCardlessSection', () => {
  it('lists every linked bank under its own section, apart from adding one', async () => {
    serve(status([bank(), bank({ key: 'k2', institution_name: 'Other Bank', accounts: 1 })]))
    render(<GoCardlessSection />)
    await screen.findByText('Other Bank')
    expect(screen.getByText('ACME Bank')).toBeTruthy()
    expect(screen.getByText(/Linked · 1 account$/)).toBeTruthy()
    expect(screen.getByText('Your banks')).toBeTruthy()
    expect(screen.getByText('Add a bank')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'List banks' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Check links' })).toBeNull()
    expect(calls('/api/gocardless/check-link')).toBe(0)
  })

  it('checks an unfinished link on open and shows it linked', async () => {
    serve(status([bank({ status: 'CR', accounts: 0 })]), status([bank()]))
    render(<GoCardlessSection />)
    await waitFor(() => expect(screen.getByText(/Linked · 2 accounts/)).toBeTruthy())
    expect(calls('/api/gocardless/check-link')).toBe(1)
  })

  it('says an expired bank needs linking again, beside a bank that still works', async () => {
    serve(status([bank(), bank({ key: 'k2', institution_name: 'Other Bank', status: 'EX' })]))
    render(<GoCardlessSection />)
    await screen.findByText(/Access expired — not syncing until you link it again/)
    expect(calls('/api/gocardless/check-link')).toBe(0)
    expect(screen.getByRole('button', { name: 'Link again Other Bank' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Renew access to ACME Bank' })).toBeTruthy()
  })

  it('removes one bank after confirming', async () => {
    serve(status([bank(), bank({ key: 'k2', institution_name: 'Other Bank' })]))
    render(<GoCardlessSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Other Bank' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(calls('/api/gocardless/banks/k2/remove')).toBe(1))
    expect(calls('/api/gocardless/banks/k1/remove')).toBe(0)
  })

  it('shows the error Check links returns', async () => {
    serve(status([bank(), bank({ key: 'k2', status: 'GA', renewing: true })]))
    render(<GoCardlessSection />)
    await screen.findByRole('button', { name: 'Check links' })
    authFetch.mockImplementation((url: string) =>
      url === '/api/gocardless/check-link'
        ? Promise.resolve({
            ok: false,
            json: () => Promise.resolve({ detail: 'GoCardless HTTP 503' }),
          })
        : okJson({ status: 'ok', accounts: [] }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Check links' }))
    await waitFor(() => expect(screen.getByText('GoCardless HTTP 503')).toBeTruthy())
  })
})

describe('GoCardlessSection fetch caution', () => {
  it('drops the part a bank row already shows and keeps the rest', async () => {
    serve({
      ...status([bank(), bank({ key: 'k2', institution_name: 'Other Bank', status: 'EX' })]),
      last_caution:
        'Other Bank: bank access expired — link it again; ACME Savings (ACME Bank): GoCardless HTTP 429',
    } as unknown as Status)
    render(<GoCardlessSection />)
    await screen.findByText(
      /Last fetch couldn.t read everything — ACME Savings \(ACME Bank\): GoCardless HTTP 429$/,
    )
    expect(screen.queryByText(/Other Bank: bank access expired/)).toBeNull()
  })
})
