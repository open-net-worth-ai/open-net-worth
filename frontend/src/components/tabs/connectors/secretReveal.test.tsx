// @vitest-environment jsdom
//
// The eye that reveals a SAVED secret — `ui/useSecretReveal` + `connectors/secrets`, driven
// through a real connector form (`TelegramSection`) rather than in isolation, because the
// contract that matters spans both: the field's eye, the fetch it makes, and what the form
// then SENDS on save.
//
// The two failures worth breaking a build over:
//   1. the eye unmasks only what you typed — the saved value stays unreachable, which is the
//      bug this feature exists to fix (a key you pasted in was write-only forever);
//   2. revealing a value and NOT editing it rewrites the credential on save. `bot_token` is
//      keep-on-blank, so a revealed-then-untouched field must go back to blank; if the
//      revealed string were sent instead, every look at a secret would be a write.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { useConnectorSaveHost } from './formSave'
import { TelegramSection } from './TelegramSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const CONFIG = {
  configured: true,
  enabled: true,
  allowed_chat_ids: ['123'],
  access_policy: 'allowlist',
  has_bot_token: true,
}

/** Every request the section makes, as `${method} ${url}`. */
const calls = () =>
  authFetch.mock.calls.map(
    (c: unknown[]) => `${(c[1] as RequestInit | undefined)?.method ?? 'GET'} ${String(c[0])}`,
  )

const bodyOf = (index: number) =>
  JSON.parse(String((authFetch.mock.calls[index]?.[1] as RequestInit).body))

function mockApi(overrides: Record<string, unknown> = {}, config = CONFIG) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`
    if (key in overrides) return Promise.resolve(overrides[key])
    if (url === '/api/telegram/config' && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(config) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

const REVEALS = {
  'GET /api/connectors/telegram/secret/bot_token': {
    ok: true,
    json: async () => ({ connector_id: 'telegram', field: 'bot_token', value: '999:REAL-TOKEN' }),
  },
}

beforeEach(() => {
  authFetch.mockReset()
  mockApi(REVEALS)
})
afterEach(cleanup)

/** The modal shell, reduced to the part under test: a form publishes its save, and ONE
 *  primary action in the footer runs it. Same wiring as `ConnectorDetail`'s config view —
 *  so this exercises the real seam rather than reaching into it. */
function Harness({ children }: { children: ReactNode }) {
  const host = useConnectorSaveHost()
  return (
    <>
      <host.Provider>{children}</host.Provider>
      <button
        type="button"
        disabled={!host.state?.dirty || host.state?.saving}
        onClick={() => void host.run()}
      >
        {host.state?.saving ? 'Saving…' : 'Save'}
      </button>
    </>
  )
}

async function renderForm() {
  render(
    <Harness>
      <TelegramSection />
    </Harness>,
  )
  return (await screen.findByLabelText(/^Bot token/)) as HTMLInputElement
}

const footerSave = () =>
  screen.getByRole('button', { name: /^Sav(e|ing\W*)$/ }) as HTMLButtonElement

describe('the eye reveals the SAVED value, not just what you typed', () => {
  it('starts masked with nothing in the box, and fetches nothing until asked', async () => {
    const field = await renderForm()

    expect(field.value).toBe('')
    expect(field.type).toBe('password')
    expect(field.placeholder).toBe('••••••••')
    expect(calls()).not.toContain('GET /api/connectors/telegram/secret/bot_token')
    // The list/detail read carried no value — only a `has_bot_token` flag built this screen.
    expect(document.body.innerHTML).not.toContain('999:REAL-TOKEN')
  })

  it('puts the stored token in the box, unmasked, on one tap', async () => {
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))

    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))
    expect(field.type).toBe('text')
    expect(calls()).toContain('GET /api/connectors/telegram/secret/bot_token')
  })

  it('is the ONLY reveal control — no second button beside the field', async () => {
    await renderForm()

    expect(screen.queryByText(/Reveal saved/)).toBeNull()
    expect(screen.queryByLabelText(/read only/i)).toBeNull()
  })

  it('does not arm the footer Save — looking at a key is not editing it', async () => {
    const field = await renderForm()
    expect(footerSave().disabled).toBe(true)

    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))

    // The box differs from blank, but the CONNECTOR does not differ from what is stored.
    expect(footerSave().disabled).toBe(true)

    // Editing the revealed value is a real change, and arms it.
    fireEvent.change(field, { target: { value: '999:ROTATED' } })
    await waitFor(() => expect(footerSave().disabled).toBe(false))
  })

  it('says it could not read the value rather than showing an empty box', async () => {
    mockApi({ 'GET /api/connectors/telegram/secret/bot_token': { ok: false, status: 404 } })
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))

    await waitFor(() => expect(screen.getByText(/Could not read the saved value/)).toBeTruthy())
    expect(field.value).toBe('')
  })
})

describe('a revealed value that is not edited must not rewrite the secret', () => {
  it('returns the box to blank when the reveal is hidden again', async () => {
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))

    fireEvent.click(screen.getByLabelText('Hide secret'))
    await waitFor(() => expect(field.value).toBe(''))
    expect(field.type).toBe('password')
  })

  it('sends NO bot_token when a revealed field is saved untouched', async () => {
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))

    // Save with the revealed value still on screen, exactly as a user would after looking.
    // `bot_token` is keep-on-blank at the store, and the form's own rule is "only send it if
    // the user typed one" — a revealed value is not a typed one.
    fireEvent.change(screen.getByLabelText('Who can chat'), { target: { value: 'pairing' } })
    fireEvent.click(screen.getByLabelText('Hide secret'))
    await waitFor(() => expect(field.value).toBe(''))

    await saveAndAssert((body) => {
      expect(body.bot_token).toBeUndefined()
      expect(body.access_policy).toBe('pairing')
    })
  })

  it('sends the new value when the user EDITS a revealed one', async () => {
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))

    fireEvent.change(field, { target: { value: '999:ROTATED' } })
    await saveAndAssert((body) => expect(body.bot_token).toBe('999:ROTATED'))
  })

  it('keeps an edited value when the eye is closed over it', async () => {
    const field = await renderForm()
    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(field.value).toBe('999:REAL-TOKEN'))

    fireEvent.change(field, { target: { value: '999:ROTATED' } })
    fireEvent.click(screen.getByLabelText('Hide secret'))

    // Masked again, but the edit survives — hiding is not discarding.
    await waitFor(() => expect(field.type).toBe('password'))
    expect(field.value).toBe('999:ROTATED')
  })
})

/** Drive the footer's one primary action, then assert what reached the wire. */
async function saveAndAssert(check: (body: Record<string, unknown>) => void) {
  await waitFor(() => expect(footerSave().disabled).toBe(false))
  fireEvent.click(footerSave())

  await waitFor(() => expect(calls()).toContain('PUT /api/telegram/config'))
  const putIndex = calls().findIndex((c) => c === 'PUT /api/telegram/config')
  check(bodyOf(putIndex))
}
