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

const status = (over: Record<string, unknown> = {}) => ({
  configured: false,
  enabled: true,
  has_credentials: false,
  application_id: '',
  aspsp: '',
  country: '',
  has_session: false,
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
})

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

function serve(current: ReturnType<typeof status>, link?: Record<string, unknown>) {
  authFetch.mockImplementation((url: string) => {
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
    expect(screen.getByText('Linked: ACME Bank · Finland')).toBeTruthy()
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
    expect(await screen.findByText(/Your bank access has expired/)).toBeTruthy()
  })
})
