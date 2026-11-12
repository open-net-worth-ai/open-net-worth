// @vitest-environment jsdom
//
// The Enable Banking form is two numbered steps: ① register the app (Redirect URL to copy,
// Application ID, private key) and ② link a bank (Country → Bank → Link bank). The Redirect URL
// is a value to copy, never an input; the paste-a-code fallback stays folded away until the
// bank didn't bring the user back.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EnableBankingSection } from './EnableBankingSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  vi.restoreAllMocks()
})

function bankRow(aspsp: string, country: string, key: string, validUntil = '2099-02-10') {
  return { key, aspsp, country, valid_until: validUntil, linked_at: '2026-11-01', accounts: 2 }
}

const status = (over: Record<string, unknown> = {}) => ({
  configured: false,
  enabled: true,
  has_credentials: false,
  application_id: '',
  aspsp: '',
  country: '',
  has_session: false,
  banks: [] as ReturnType<typeof bankRow>[],
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null as string | null,
  ...over,
})

const linked = status({
  configured: true,
  has_credentials: true,
  application_id: '96bb32ba-0000-4000-8000-000000000000',
  aspsp: 'ACME Bank',
  country: 'FI',
  has_session: true,
  banks: [bankRow('ACME Bank', 'FI', 'k1')],
})

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

function serve(current: ReturnType<typeof status>, link?: Record<string, unknown>) {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/enable-banking/banks/')) return okJson({ removed: true })
    if (url === '/api/enable-banking/status') return okJson(current)
    if (url.startsWith('/api/enable-banking/institutions'))
      return okJson({ institutions: [{ id: 'ACME Bank', name: 'ACME Bank', country: 'FI' }] })
    if (url === '/api/enable-banking/link') return okJson(link ?? {})
    if (url.startsWith('/api/connectors/')) return okJson({ status: 'ok', accounts: [] })
    return okJson([])
  })
}

describe('EnableBankingSection', () => {
  it('starts on step 1 with the Redirect URL to copy, and step 2 waiting for the keys', async () => {
    serve(status())
    render(<EnableBankingSection />)
    expect(await screen.findByText('Register your app with Enable Banking')).toBeTruthy()
    const url = screen.getByText(`${window.location.origin}/connectors`)
    expect(url.tagName).toBe('CODE') // a value to copy, never an editable field
    expect(screen.getByRole('button', { name: 'Copy the redirect URL' })).toBeTruthy()
    expect(screen.getByText('Save your application ID and private key first.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Link bank' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('folds step 1 once the keys are saved and offers Renew for the linked bank', async () => {
    serve(linked)
    render(<EnableBankingSection />)
    expect(await screen.findByText(/Application ID 96bb32ba… · private key saved/)).toBeTruthy()
    expect(screen.queryByText(`${window.location.origin}/connectors`)).toBeNull()
    expect(screen.getByRole('list', { name: 'Linked banks' }).textContent).toContain('ACME Bank')
    expect(screen.getByText(/Finland · 2 accounts · access until/)).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Renew link' })).toBeTruthy()
    // The paste-a-code fallback is folded away until asked for.
    expect(screen.queryByLabelText('Code from the address bar')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Bank didn't bring you back/ }))
    expect(screen.getByLabelText('Code from the address bar')).toBeTruthy()
  })

  it('opens the paste row and step 1 when the link fell back to the old redirect', async () => {
    serve(linked, { url: 'https://bank.example/consent', returns_to_app: false })
    vi.spyOn(window, 'open').mockReturnValue(null)
    render(<EnableBankingSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Renew link' }))
    await waitFor(() => expect(screen.getByLabelText('Code from the address bar')).toBeTruthy())
    expect(screen.getByText(/won't load/)).toBeTruthy()
    expect(screen.getByText(`${window.location.origin}/connectors`)).toBeTruthy()
  })

  it('says the consent ran out when a fetch was refused after a link', async () => {
    serve({ ...linked, last_error: 'Enable Banking HTTP 401' })
    render(<EnableBankingSection />)
    expect(await screen.findByText(/Access to a bank has expired/)).toBeTruthy()
  })

  it('lists every linked bank and adds another with the picker', async () => {
    serve({
      ...linked,
      banks: [bankRow('ACME Bank', 'FI', 'k1'), bankRow('Other Bank', 'SE', 'k2', '2020-01-01')],
    })
    render(<EnableBankingSection />)
    expect(await screen.findByText('Other Bank')).toBeTruthy()
    expect(screen.getByText(/Sweden · access ended/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Renew access to ACME Bank (Finland)' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove Other Bank (Sweden)' })).toBeTruthy()
  })

  it('Renew on a listed bank starts a consent for that bank, not the picked one', async () => {
    serve(
      { ...linked, banks: [bankRow('ACME Bank', 'FI', 'k1'), bankRow('Other Bank', 'SE', 'k2')] },
      { url: 'https://bank.example/consent', returns_to_app: true },
    )
    vi.spyOn(window, 'open').mockReturnValue(null)
    render(<EnableBankingSection />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Renew access to Other Bank (Sweden)' }),
    )
    await waitFor(() => {
      const call = authFetch.mock.calls.find((c) => c[0] === '/api/enable-banking/link')
      expect(JSON.parse(String(call![1]?.body))).toMatchObject({
        aspsp: 'Other Bank',
        country: 'SE',
      })
    })
  })

  it('Remove asks first, then unlinks only that bank', async () => {
    serve({
      ...linked,
      banks: [bankRow('ACME Bank', 'FI', 'k1'), bankRow('Other Bank', 'SE', 'k2')],
    })
    render(<EnableBankingSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Other Bank (Sweden)' }))
    expect(screen.getByText('Remove Other Bank (Sweden)?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() =>
      expect(
        authFetch.mock.calls.some(
          (c) => c[0] === '/api/enable-banking/banks/k2/remove' && c[1]?.method === 'POST',
        ),
      ).toBe(true),
    )
  })
})
