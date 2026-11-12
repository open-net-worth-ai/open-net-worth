// @vitest-environment jsdom
//
// The Actual connector's form: an address and one way to sign in, then a budget. Every secret is
// write-only: nothing reads one back, and a blank field keeps what is saved.

import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache, invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { ActualSection } from './ActualSection'
import { hasConfigForm } from './ConnectorConfig'
import type { Connector } from './types'
import { useConnectorSaveHost, type ConnectorSaveHost } from './formSave'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const BLANK = {
  configured: false,
  enabled: false,
  available: true,
  has_password: false,
  has_token: false,
  has_encryption_password: false,
  has_server_url: false,
  has_proxy_headers: false,
  proxy_header_names: [] as string[],
  server_host: null as string | null,
  server_scheme: null as string | null,
  server_plain_http: false,
  budget_id: null as string | null,
  budget_name: null as string | null,
  budget_currency: null as string | null,
  last_synced_at: null as string | null,
  last_status: 'never_run',
  last_error: null as string | null,
}
const SAVED = {
  ...BLANK,
  configured: true,
  enabled: true,
  has_password: true,
  has_server_url: true,
  server_host: 'actual.acme.test',
  server_scheme: 'https',
}
const HOUSEHOLD = { id: 'b1', name: 'ACME Household', currency: null, encrypted: false }
const VAULT = { id: 'b2', name: 'ACME Vault', currency: null, encrypted: true }

let status: typeof BLANK
let budgets: unknown
let saveAnswer: { status: number; body: unknown } | null
let requests: { url: string; method: string; body: Record<string, unknown> | null }[]

const reply = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

beforeEach(() => {
  status = BLANK
  budgets = { budgets: [HOUSEHOLD, VAULT] }
  saveAnswer = null
  requests = []
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : null
    requests.push({ url, method, body })
    if (url === '/api/actual-budget/status') return reply(status)
    if (url === '/api/actual-budget/budgets') {
      const b = budgets as { status?: number; detail?: string }
      return b.status ? reply({ detail: b.detail }, b.status) : reply(budgets)
    }
    if (url === '/api/actual-budget/config') {
      if (saveAnswer) return reply(saveAnswer.body, saveAnswer.status)
      status = { ...SAVED }
      return reply(status)
    }
    return reply({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
})

let footer: ConnectorSaveHost | null = null
function Harness() {
  const host = useConnectorSaveHost()
  useEffect(() => {
    footer = host
  })
  return (
    <host.Provider>
      <ActualSection />
    </host.Provider>
  )
}
function ConnectorsReader() {
  useQueryCache('/api/connectors', async () => (await authFetch('/api/connectors')).json())
  return null
}
const form = () => render(<Harness />)
const canSave = () => !!footer?.state?.dirty
const save = () => act(async () => void (await footer?.run()))
const type = (label: RegExp | string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
const lastSave = () => requests.filter((r) => r.method === 'PUT').at(-1)?.body

describe('ActualSection', () => {
  it('is the form the connector opens', () => {
    expect(hasConfigForm({ kind: 'actual_budget' } as Connector)).toBe(true)
  })

  it('says why it is unavailable behind the gateway, and offers nothing to save', async () => {
    status = { ...BLANK, available: false }
    form()
    expect(await screen.findByText(/isn't available on a hosted instance/)).toBeTruthy()
    expect(screen.queryByLabelText(/Server address/)).toBeNull()
    expect(footer?.state).toBeNull()
    expect(requests.map((r) => r.url)).toEqual(['/api/actual-budget/status'])
  })

  it('asks for an address and a password, then a budget once they are saved', async () => {
    form()
    await screen.findByLabelText(/Server address/)
    expect(screen.getByRole('tablist', { name: 'Sign in with' })).toBeTruthy()
    expect(screen.getByText(/Your budgets appear here once you save/)).toBeTruthy()
    expect(requests.map((r) => r.url)).not.toContain('/api/actual-budget/budgets')
    expect(canSave()).toBe(false)
    type(/Server address/, 'https://actual.acme.test')
    expect(canSave()).toBe(false) // no sign-in yet
    type(/^Password/, 'pw-1')
    expect(canSave()).toBe(true)
    await save()
    expect(lastSave()).toEqual({ server_url: 'https://actual.acme.test', password: 'pw-1' })
    expect(await screen.findByLabelText(/^Budget/)).toBeTruthy()
    expect((screen.getByLabelText(/^Password/) as HTMLInputElement).value).toBe('')
    expect(screen.getByText('A password is saved. Leave blank to keep it.')).toBeTruthy()
  })

  it('sends only the sign-in on show, and starts on a token when that is what is saved', async () => {
    form()
    await screen.findByLabelText(/Server address/)
    type(/Server address/, 'https://actual.acme.test')
    type(/^Password/, 'pw-1')
    fireEvent.click(screen.getByRole('tab', { name: 'Token' }))
    expect(canSave()).toBe(false) // the password stays typed, but isn't what is showing
    type(/^Token/, 'tok-1')
    await save()
    expect(lastSave()).toEqual({ server_url: 'https://actual.acme.test', token: 'tok-1' })
    cleanup()
    clearQueryCache()
    status = { ...SAVED, has_password: false, has_token: true }
    form()
    expect(await screen.findByLabelText(/^Token/)).toBeTruthy()
  })

  it('never reads a secret back: only placeholders, and no secret request', async () => {
    status = { ...SAVED, has_encryption_password: true }
    form()
    const password = (await screen.findByLabelText(/^Password/)) as HTMLInputElement
    expect(password.value).toBe('')
    expect(password.placeholder).toBe('••••••••')
    expect(password.autocomplete).toBe('new-password')
    const encryption = screen.getByLabelText(/Encryption password/) as HTMLInputElement
    expect(encryption.placeholder).toBe('••••••••')
    expect(encryption.autocomplete).toBe('new-password')
    const address = screen.getByLabelText(/Server address/) as HTMLInputElement
    expect(address.value).toBe('')
    expect(address.placeholder).toBe('actual.acme.test')
    await waitFor(() => expect(requests.some((r) => r.url.includes('/budgets'))).toBe(true))
    expect(requests.some((r) => r.url.includes('/secret/'))).toBe(false)
  })

  it('warns about plain http to another computer, typed or saved', async () => {
    form()
    await screen.findByLabelText(/Server address/)
    const warning = /uses plain http/
    expect(screen.queryByText(warning)).toBeNull()
    type(/Server address/, 'http://192.168.1.20:5006')
    expect(screen.getByText(warning)).toBeTruthy()
    type(/Server address/, 'http://localhost:5006')
    expect(screen.queryByText(warning)).toBeNull()
    type(/Server address/, 'http://127.0.0.1:5006')
    expect(screen.queryByText(warning)).toBeNull()
    type(/Server address/, 'http://[::1]:5006')
    expect(screen.queryByText(warning)).toBeNull()
    type(/Server address/, 'https://192.168.1.20:5006')
    expect(screen.queryByText(warning)).toBeNull()
    cleanup()
    clearQueryCache()
    status = { ...SAVED, server_plain_http: true, server_host: '192.168.1.20:5006' }
    form()
    expect(await screen.findByText(warning)).toBeTruthy()
    expect(screen.getByText(/your password, token or proxy headers/)).toBeTruthy()
    expect(screen.getByText(/change what this app reads and writes/)).toBeTruthy()
  })

  it('a new address needs its sign-in again, and the same server written another way doesn’t', async () => {
    status = SAVED
    form()
    await screen.findByLabelText(/Server address/)
    type(/Server address/, 'https://other.acme.test')
    expect(canSave()).toBe(false)
    expect(screen.getByText(/A new address needs its password again/)).toBeTruthy()
    expect((screen.getByLabelText(/^Password/) as HTMLInputElement).placeholder).toBe('')
    type(/Server address/, 'https://actual.acme.test:443/sync')
    expect(canSave()).toBe(true)
    expect(screen.getByText('A password is saved. Leave blank to keep it.')).toBeTruthy()
    type(/Server address/, 'https://other.acme.test')
    type(/^Password/, 'pw-2')
    expect(canSave()).toBe(true)
    await save()
    expect(lastSave()).toEqual({ server_url: 'https://other.acme.test', password: 'pw-2' })
  })

  it('refuses a malformed address once the field is left', async () => {
    form()
    const address = await screen.findByLabelText(/Server address/)
    type(/^Password/, 'pw-1')
    type(/Server address/, 'actual.acme.test')
    expect(canSave()).toBe(false)
    expect(screen.queryByText(/must start with http/)).toBeNull()
    fireEvent.blur(address)
    expect(screen.getByRole('alert').textContent).toMatch(/must start with http/)
  })

  it('picks a budget through the footer, and sends only what changed', async () => {
    status = SAVED
    form()
    const picker = (await screen.findByLabelText(/^Budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(3))
    expect(screen.getByText('Connected to actual.acme.test.')).toBeTruthy()
    expect(canSave()).toBe(false)
    fireEvent.change(picker, { target: { value: 'b1' } })
    expect(canSave()).toBe(true)
    await save()
    expect(lastSave()).toEqual({ budget_id: 'b1' })
  })

  it('an encrypted budget needs its encryption password before saving', async () => {
    status = SAVED
    form()
    const picker = (await screen.findByLabelText(/^Budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(3))
    expect(picker.options[2]?.textContent).toBe('ACME Vault (encrypted)')
    fireEvent.change(picker, { target: { value: 'b2' } })
    expect(
      screen.getByText('ACME Vault is encrypted, so it needs its encryption password.'),
    ).toBeTruthy()
    expect(canSave()).toBe(false)
    type(/Encryption password/, 'enc-1')
    expect(canSave()).toBe(true)
    await save()
    expect(lastSave()).toEqual({ budget_id: 'b2', encryption_password: 'enc-1' })
  })

  it('pre-selects the only budget, and says when the saved one is gone', async () => {
    budgets = { budgets: [HOUSEHOLD] }
    status = SAVED
    form()
    const picker = (await screen.findByLabelText(/^Budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('b1'))
    expect(canSave()).toBe(true)
    cleanup()
    clearQueryCache()
    budgets = { budgets: [VAULT] }
    status = { ...SAVED, budget_id: 'b1', budget_name: 'ACME Household' }
    form()
    expect(
      await screen.findByText('ACME Household is no longer on this server. Choose another.'),
    ).toBeTruthy()
    expect(canSave()).toBe(false)
  })

  it('shows the server’s message when the sign-in fails, with a retry', async () => {
    budgets = { status: 502, detail: 'The Actual server refused the password.' }
    status = SAVED
    form()
    expect(await screen.findByText('The Actual server refused the password.')).toBeTruthy()
    budgets = { budgets: [HOUSEHOLD, VAULT] }
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByLabelText(/^Budget/)).toBeTruthy()
  })

  it('says when a sign-in sees no budgets', async () => {
    budgets = { budgets: [] }
    status = SAVED
    form()
    expect(await screen.findByText("This sign-in can't see any Actual budgets.")).toBeTruthy()
  })

  it('a refused save stays on screen, and the status is read again', async () => {
    status = SAVED
    saveAnswer = { status: 422, body: { detail: 'That budget was not found.' } }
    form()
    const picker = (await screen.findByLabelText(/^Budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(3))
    fireEvent.change(picker, { target: { value: 'b1' } })
    const before = requests.filter((r) => r.url.endsWith('/status')).length
    await save()
    expect(screen.getByText('That budget was not found.')).toBeTruthy()
    await waitFor(() =>
      expect(requests.filter((r) => r.url.endsWith('/status')).length).toBeGreaterThan(before),
    )
  })

  it('names no last-fetched line until something has been fetched', async () => {
    status = SAVED
    form()
    await screen.findByLabelText(/Server address/)
    expect(screen.queryByText(/Last fetched/)).toBeNull()
  })

  it('labels the Budget slot before it has anything to pick, and through an error', async () => {
    form()
    const before = await screen.findByText(/Your budgets appear here/)
    expect(before.closest('.ui-field')?.querySelector('.form-label')?.textContent).toBe('Budget')
    cleanup()
    clearQueryCache()
    budgets = { status: 502, detail: 'Actual refused the sign-in. Check its password or token.' }
    status = SAVED
    form()
    const error = await screen.findByText(/refused the sign-in/)
    const field = error.closest('.ui-field')
    expect(field?.querySelector('.form-label')?.textContent).toBe('Budget')
    expect(field?.querySelector('button')?.textContent).toBe('Retry')
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('follows a budget changed elsewhere, unless one has been picked here', async () => {
    status = { ...SAVED, budget_id: 'b1', budget_name: 'ACME Household' }
    form()
    const picker = (await screen.findByLabelText(/^Budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('b1'))
    status = { ...SAVED, budget_id: 'b2', budget_name: 'ACME Vault' }
    invalidateQueryCache('/api/actual-budget/status')
    await waitFor(() => expect(picker.value).toBe('b2'))
    expect(canSave()).toBe(false)
    fireEvent.change(picker, { target: { value: 'b1' } })
    expect(picker.value).toBe('b1')
    expect(canSave()).toBe(true)
  })

  it('a save doesn’t put back a budget it didn’t change', async () => {
    status = { ...SAVED, budget_id: 'b1', budget_name: 'ACME Household' }
    form()
    await screen.findByLabelText(/^Budget/)
    status = { ...SAVED, budget_id: 'b2', budget_name: 'ACME Vault', has_encryption_password: true }
    invalidateQueryCache('/api/actual-budget/status')
    await waitFor(() =>
      expect((screen.getByLabelText(/^Budget/) as HTMLSelectElement).value).toBe('b2'),
    )
    type(/^Password/, 'pw-2')
    await save()
    expect(lastSave()).toEqual({ password: 'pw-2' })
  })

  it('leaving the password blank doesn’t mark an empty Token tab as missing', async () => {
    form()
    const password = await screen.findByLabelText(/^Password/)
    fireEvent.blur(password)
    expect(screen.getByText('A password is required.')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Token' }))
    expect(screen.queryByText('A token is required.')).toBeNull()
  })

  it('tells the connector list when a read is refused', async () => {
    budgets = { status: 502, detail: 'Actual refused the sign-in. Check its password or token.' }
    status = SAVED
    render(
      <>
        <ConnectorsReader />
        <Harness />
      </>,
    )
    await screen.findByText(/refused the sign-in/)
    await waitFor(() =>
      expect(requests.filter((r) => r.url === '/api/connectors').length).toBeGreaterThan(1),
    )
  })

  it('says it is paused, as the other connectors do', async () => {
    status = { ...SAVED, enabled: false }
    form()
    expect(await screen.findByText(/Currently paused/)).toBeTruthy()
  })

  it('reads no budgets while paused, and says how to choose one', async () => {
    status = { ...SAVED, enabled: false, budget_id: 'b1', budget_name: 'ACME Household' }
    form()
    expect(await screen.findByText(/nothing talks to your Actual server/)).toBeTruthy()
    expect(screen.getByText(/ACME Household\. Resume the connector/)).toBeTruthy()
    expect(requests.map((r) => r.url)).not.toContain('/api/actual-budget/budgets')
  })

  it('a save while paused sends the sign-in and no budget, so it isn’t refused', async () => {
    status = { ...SAVED, enabled: false }
    budgets = { budgets: [HOUSEHOLD] }
    form()
    await screen.findByText(/Resume the connector to choose a budget/)
    type(/^Password/, 'pw-2')
    await save()
    expect(lastSave()).toEqual({ password: 'pw-2' })
  })

  it('saves proxy headers, shows only their names, and can remove them', async () => {
    status = SAVED
    form()
    await screen.findByLabelText(/Proxy headers/)
    type(/Proxy headers/, 'X-Acme-Proxy token')
    expect(screen.getByText('Write each header on its own line, as Name: value.')).toBeTruthy()
    expect(canSave()).toBe(false)
    type(/Proxy headers/, 'X-Acme-Proxy: PROXY-1')
    expect(canSave()).toBe(true)
    await save()
    expect(lastSave()).toEqual({ proxy_headers: 'X-Acme-Proxy: PROXY-1' })
    cleanup()

    status = { ...SAVED, has_proxy_headers: true, proxy_header_names: ['X-Acme-Proxy'] }
    form()
    expect(await screen.findByText(/Saved: X-Acme-Proxy\./)).toBeTruthy()
    expect(screen.queryByText(/PROXY-1/)).toBeNull()
    expect(canSave()).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(screen.getByText(/Removed when you save/)).toBeTruthy()
    await save()
    expect(lastSave()).toEqual({ clear_proxy_headers: true })
  })

  it('says saved headers stay behind when the address moves', async () => {
    status = { ...SAVED, has_proxy_headers: true, proxy_header_names: ['X-Acme-Proxy'] }
    form()
    await screen.findByLabelText(/Server address/)
    type(/Server address/, 'https://other.acme.test')
    expect(screen.getByText(/the saved ones aren't sent to another server/)).toBeTruthy()
  })
})
