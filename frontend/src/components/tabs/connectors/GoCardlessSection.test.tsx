// @vitest-environment jsdom
//
// GoCardless has no code to paste after consent: coming back from the bank, the section
// re-reads the requisition itself (Check link), and an ended consent says to link again.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { GoCardlessSection } from './GoCardlessSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const BASE = {
  configured: true,
  enabled: true,
  has_credentials: true,
  secret_id: 'SID',
  institution_id: 'ACME_BANK_GB',
  country: 'GB',
  has_requisition: true,
  requisition_status: 'CR',
  has_link: false,
  consent_expires_at: null,
  linked_accounts: 0,
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null,
}

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

function serve(status: typeof BASE, afterCheck: typeof BASE = status) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/gocardless/status') return okJson(status)
    if (url === '/api/gocardless/check-link' && init?.method === 'POST') return okJson(afterCheck)
    if (url.startsWith('/api/connectors/')) return okJson({ status: 'ok', accounts: [] })
    return okJson([])
  })
}

const checkCalls = () =>
  authFetch.mock.calls.filter((c) => c[0] === '/api/gocardless/check-link').length

describe('GoCardlessSection', () => {
  it('checks an unfinished link on open and shows it linked', async () => {
    serve(BASE, { ...BASE, configured: true, requisition_status: 'LN', has_link: true })
    render(<GoCardlessSection />)
    await waitFor(() => expect(screen.getByText(/Linked to ACME_BANK_GB/)).toBeTruthy())
    expect(checkCalls()).toBe(1)
  })

  it('does not auto-check an expired consent; it says to link again', async () => {
    serve({ ...BASE, requisition_status: 'EX' })
    render(<GoCardlessSection />)
    await waitFor(() => expect(screen.getByText(/access has expired/)).toBeTruthy())
    expect(checkCalls()).toBe(0)
    expect(screen.getByRole('button', { name: 'Link a different bank' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Check link' })).toBeNull()
  })

  it('shows the error Check link returns', async () => {
    serve({ ...BASE, requisition_status: 'LN', has_link: true, configured: true })
    render(<GoCardlessSection />)
    await screen.findByRole('button', { name: 'Check link' })
    authFetch.mockImplementation((url: string) =>
      url === '/api/gocardless/check-link'
        ? Promise.resolve({
            ok: false,
            json: () => Promise.resolve({ detail: 'GoCardless HTTP 503' }),
          })
        : okJson({ status: 'ok', accounts: [] }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Check link' }))
    await waitFor(() => expect(screen.getByText('GoCardless HTTP 503')).toBeTruthy())
  })
})
