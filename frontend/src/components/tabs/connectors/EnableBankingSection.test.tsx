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
    const url = screen.getByText(`${window.location.origin}/connectors/callback/enable_banking`)
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
    expect(
      screen.queryByText(`${window.location.origin}/connectors/callback/enable_banking`),
    ).toBeNull()
    expect(screen.getByRole('list', { name: 'Linked banks' }).textContent).toContain('ACME Bank')
    expect(screen.getByText(/Finland · 2 accounts · access until/)).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Renew link' })).toBeTruthy()
    // The paste-a-code fallback is folded away until asked for.
    expect(screen.queryByLabelText('Code from the address bar')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Bank didn't bring you back/ }))
    expect(screen.getByLabelText('Code from the address bar')).toBeTruthy()
  })

  it('stops before the bank when Enable Banking refuses the address, and names it', async () => {
    const callback = `${window.location.origin}/connectors/callback/enable_banking`
    serve(linked)
    const fallback = authFetch.getMockImplementation()!
    const linkBodies: { redirect: string }[] = []
    authFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/enable-banking/link') {
        const body = JSON.parse(String(init?.body))
        linkBodies.push(body)
        if (body.redirect)
          return Promise.resolve({
            ok: false,
            json: () =>
              Promise.resolve({
                detail: {
                  code: 'redirect_not_registered',
                  message: 'add it',
                  redirect_url: body.redirect,
                },
              }),
          })
        return okJson({ url: 'https://bank.example/consent', returns_to_app: false })
      }
      return fallback(url, init)
    })
    const tab = { opener: {}, closed: false, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    render(<EnableBankingSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Renew link' }))
    // Step 1 reopens on its Redirect URL, saying what's wrong; step 2 points up to it.
    expect(await screen.findByText(/doesn.t know this address yet/)).toBeTruthy()
    expect(screen.getByText(callback).tagName).toBe('CODE')
    expect(screen.getByRole('button', { name: 'Copy the redirect URL' })).toBeTruthy()
    expect(screen.getByText(/Add the Redirect URL from step 1/)).toBeTruthy()
    // Only the address: the saved keys stay folded away.
    expect(screen.queryByLabelText(/Application ID/)).toBeNull()
    // One way round, not two: the "enter its code" link waits until this is resolved.
    expect(screen.queryByRole('button', { name: /Bank didn't bring you back/ })).toBeNull()
    // Nobody went to the bank: the blank tab opened on the click is closed again.
    expect(tab.close).toHaveBeenCalled()
    expect(tab.location.href).toBe('')
    expect(screen.queryByLabelText('Code from the address bar')).toBeNull()
    expect(linkBodies.map((b) => b.redirect)).toEqual([callback])

    // The user can't add it: link with a code — no address of ours is sent.
    fireEvent.click(screen.getByRole('button', { name: /Link with a code instead/ }))
    await waitFor(() => expect(screen.getByLabelText('Code from the address bar')).toBeTruthy())
    expect(linkBodies.map((b) => b.redirect)).toEqual([callback, ''])
    expect(screen.getByText(/shows an error page/)).toBeTruthy()
    expect(screen.queryByText(/Add the Redirect URL from step 1/)).toBeNull()
  })

  it("keeps a failed bank return's reason on a linked connector while its banks load", async () => {
    sessionStorage.setItem('onw:enable-banking:return-error', "This bank link doesn't match")
    serve(linked)
    render(<EnableBankingSection />)
    await screen.findByRole('button', { name: 'Renew link' }) // banks listed
    expect(screen.getByText("This bank link doesn't match")).toBeTruthy()
    expect(sessionStorage.getItem('onw:enable-banking:return-error')).toBeNull()
  })

  it('says the consent ran out when a fetch was refused after a link', async () => {
    serve({ ...linked, last_error: 'Enable Banking HTTP 401' })
    render(<EnableBankingSection />)
    expect(await screen.findByText(/Access to a bank has ended/)).toBeTruthy()
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

describe('EnableBankingSection bank page', () => {
  // Found 2026-11-13: with the bank's tab blocked (a pop-up blocker, an embedded browser, the
  // iPhone home-screen app) the form said "Approve access in the tab that just opened" and left
  // nothing to click. GoCardless's form already kept the link on screen.
  const renew = () =>
    fireEvent.click(screen.getByRole('button', { name: 'Renew access to ACME Bank (Finland)' }))

  it('with the tab blocked, the link to the bank is the instruction', async () => {
    serve(linked, { url: 'https://bank.example/consent', returns_to_app: true })
    vi.spyOn(window, 'open').mockReturnValue(null)
    render(<EnableBankingSection />)
    await screen.findByRole('button', { name: 'Renew access to ACME Bank (Finland)' })
    renew()
    const link = await screen.findByRole('link', { name: /open your bank.s page/i })
    expect(link.getAttribute('href')).toBe('https://bank.example/consent')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(screen.queryByText(/tab that just opened/)).toBeNull()
  })

  it('opens the tab on the click, points it at the bank, and keeps the link as a fallback', async () => {
    serve(linked, { url: 'https://bank.example/consent', returns_to_app: true })
    const tab = { opener: {}, closed: false, location: { href: '' }, close: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    render(<EnableBankingSection />)
    await screen.findByRole('button', { name: 'Renew access to ACME Bank (Finland)' })
    renew()
    expect(open).toHaveBeenCalledTimes(1) // on the click, before the server answered
    await waitFor(() => expect(tab.location.href).toBe('https://bank.example/consent'))
    expect(await screen.findByText(/tab that just opened/)).toBeTruthy()
    const link = screen.getByRole('link', { name: /open your bank.s page/i })
    expect(link.getAttribute('href')).toBe('https://bank.example/consent')
    expect(tab.close).not.toHaveBeenCalled()
  })

  it('keeps the link when the bank returns a code to paste, too', async () => {
    serve(linked, { url: 'https://bank.example/consent', returns_to_app: false })
    vi.spyOn(window, 'open').mockReturnValue(null)
    render(<EnableBankingSection />)
    await screen.findByRole('button', { name: 'Renew access to ACME Bank (Finland)' })
    renew()
    const link = await screen.findByRole('link', { name: /open your bank.s page/i })
    expect(link.getAttribute('href')).toBe('https://bank.example/consent')
    expect(screen.getByText(/paste it below/)).toBeTruthy()
  })
})

describe('EnableBankingSection after switching apps', () => {
  // A bank's consent belongs to the app that created it; after another app is saved, every
  // bank answered nothing while the form still said Connected (2026-11-13).
  it('says each bank was linked under the previous app and offers Renew on it', async () => {
    serve({
      ...linked,
      banks: [
        { ...bankRow('ACME Bank', 'FI', 'k1'), access_ended: 'app_changed' },
        { ...bankRow('Other Bank', 'SE', 'k2'), access_ended: 'app_changed' },
      ],
    } as unknown as ReturnType<typeof status>)
    render(<EnableBankingSection />)
    expect(
      await screen.findByText(/Your banks were linked under your previous Enable Banking app/),
    ).toBeTruthy()
    expect(
      screen.getByText('Finland · linked under your previous app — renew to keep syncing'),
    ).toBeTruthy()
    expect(screen.queryByText(/access until/)).toBeNull()
  })

  it('says only the bank from the previous app needs renewing when another is fine', async () => {
    serve({
      ...linked,
      banks: [
        bankRow('ACME Bank', 'FI', 'k1'),
        { ...bankRow('Other Bank', 'SE', 'k2'), access_ended: 'app_changed' },
      ],
    } as unknown as ReturnType<typeof status>)
    render(<EnableBankingSection />)
    expect(
      await screen.findByText(
        'A bank was linked under your previous Enable Banking app. Click Renew next to it to keep it syncing.',
      ),
    ).toBeTruthy()
  })

  it('says a bank whose consent ended needs renewing', async () => {
    serve({
      ...linked,
      banks: [{ ...bankRow('ACME Bank', 'FI', 'k1'), access_ended: 'ended' }],
    } as unknown as ReturnType<typeof status>)
    render(<EnableBankingSection />)
    expect(await screen.findByText(/Access to a bank has ended/)).toBeTruthy()
    expect(screen.getByText('Finland · access ended — renew to keep syncing')).toBeTruthy()
  })

  it('warns before saving another app while banks are linked', async () => {
    serve(linked)
    render(<EnableBankingSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    expect(screen.queryByText(/Saving another app ends their/)).toBeNull()
    fireEvent.change(screen.getByLabelText(/Application ID/), {
      target: { value: '424286e7-0000-4000-8000-000000000000' },
    })
    expect(screen.getByText(/Saving another app ends their\s+access/)).toBeTruthy()
    expect(screen.getByText("Paste the new app's private key.")).toBeTruthy()
  })

  it("shows what the last fetch couldn't read, less what a bank row already says", async () => {
    serve({
      ...linked,
      banks: [
        bankRow('ACME Bank', 'FI', 'k1'),
        { ...bankRow('Other Bank', 'SE', 'k2'), access_ended: 'ended' },
      ],
      last_caution:
        "Other Bank: access ended — renew it in Connectors to keep it syncing; ACME Bank: couldn't be read (Enable Banking HTTP 503)",
    } as unknown as ReturnType<typeof status>)
    render(<EnableBankingSection />)
    expect(
      await screen.findByText(
        /Last fetch couldn.t read everything — ACME Bank: couldn't be read \(Enable Banking HTTP 503\)$/,
      ),
    ).toBeTruthy()
  })
})
