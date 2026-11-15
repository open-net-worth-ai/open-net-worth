// @vitest-environment jsdom
// Telegram setup in one pass (2026-11-15): token → Save → message the bot → Approve, with the
// form saying what comes next at every point. The owner and a friend both got lost when Save
// was the last thing on screen and the approval box stayed invisible.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { TelegramSection } from './TelegramSection'
import { ConnectorDetail } from './ConnectorDetail'
import { ConnectorConfig } from './ConnectorConfig'
import { toEntry } from './adapter'
import type { Connector } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

interface Cfg {
  configured: boolean
  enabled: boolean
  allowed_chat_ids: string[]
  access_policy: string
  has_bot_token: boolean
  bot_username: string
  connection: string
}

const FRESH: Cfg = {
  configured: false,
  enabled: false,
  allowed_chat_ids: [],
  access_policy: 'allowlist', // the store's default — the form must still start on Pairing
  has_bot_token: false,
  bot_username: '',
  connection: 'off',
}
const ONLINE: Cfg = {
  ...FRESH,
  configured: true,
  enabled: true,
  access_policy: 'pairing',
  has_bot_token: true,
  bot_username: 'acme_lucius_bot',
  connection: 'connected',
}

let config: Cfg
let pending: { sender_id: string; code: string; sender_label: string }[]

function respond(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) })
}

beforeEach(() => {
  config = { ...FRESH }
  pending = []
  authFetch.mockReset()
  authFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
    if (url === '/api/telegram/config' && init?.method === 'PUT') {
      config = { ...ONLINE, connection: 'connecting' }
      return respond(config)
    }
    if (url === '/api/telegram/config') return respond(config)
    if (url.startsWith('/api/chat-pairing/approve')) {
      const { sender_id } = JSON.parse(init!.body!) as { sender_id: string }
      pending = pending.filter((p) => p.sender_id !== sender_id)
      config = { ...config, allowed_chat_ids: [...config.allowed_chat_ids, sender_id] }
      return respond({ ok: true, welcomed: true })
    }
    if (url.startsWith('/api/chat-pairing')) return respond({ requests: pending })
    return respond({})
  })
})
afterEach(() => {
  cleanup()
})

describe('a new Telegram connector', () => {
  it('starts on Pairing and says Save brings the bot online, with no chat-ID box yet', async () => {
    render(<TelegramSection />)

    const policy = (await screen.findByLabelText('Who can chat')) as HTMLSelectElement
    expect(policy.value).toBe('pairing')
    expect(screen.getByText(/Click Save to bring your bot online/)).toBeTruthy()
    expect(screen.queryByLabelText(/chat IDs/)).toBeNull()
    expect(screen.getByRole('region', { name: 'Create your bot' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Say hi to your bot' })).toBeTruthy()
  })
})

describe('once the bot is saved', () => {
  it('shows it online, links to it, and waits for the first message', async () => {
    config = { ...ONLINE }
    render(<TelegramSection />)

    expect(await screen.findByText('Online as @acme_lucius_bot')).toBeTruthy()
    const open = screen.getByRole('link', { name: /Open @acme_lucius_bot in Telegram/ })
    expect(open.getAttribute('href')).toBe('https://t.me/acme_lucius_bot')
    expect(screen.getByText(/Waiting for your first message/)).toBeTruthy()
  })

  it('keeps checking while the bot connects, then says it is online', async () => {
    config = { ...ONLINE, connection: 'connecting' }
    render(<TelegramSection />)
    expect(await screen.findByText('Connecting to Telegram…')).toBeTruthy()

    config = { ...ONLINE }

    expect(
      await screen.findByText('Online as @acme_lucius_bot', {}, { timeout: 5000 }),
    ).toBeTruthy()
  })

  it('approving a request needs no second save and keeps what you were typing', async () => {
    config = { ...ONLINE }
    pending = [{ sender_id: '4242', code: 'K7Q2', sender_label: 'Ada Lovelace (@ada)' }]
    render(<TelegramSection />)
    const row = await screen.findByText(/Ada Lovelace \(@ada\)/)
    fireEvent.change(screen.getByLabelText(/^Bot token/), { target: { value: '9:half-typed' } })

    fireEvent.click(
      within(row.closest('div')!.parentElement!).getByRole('button', { name: 'Approve' }),
    )

    expect(
      await screen.findByText(
        /Ada Lovelace \(@ada\) can chat with Lucius now — the bot just told them/,
      ),
    ).toBeTruthy()
    await waitFor(() =>
      expect((screen.getByLabelText('Approved chat IDs') as HTMLInputElement).value).toBe('4242'),
    )
    expect((screen.getByLabelText(/^Bot token/) as HTMLInputElement).value).toBe('9:half-typed')
    expect(authFetch.mock.calls.some(([, i]) => i?.method === 'PUT')).toBe(false)
  })

  it('surfaces a token Telegram refused instead of saving it', async () => {
    authFetch.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === '/api/telegram/config' && init?.method === 'PUT')
        return respond({ detail: "Telegram didn't accept this bot token." }, false)
      if (url === '/api/telegram/config') return respond(config)
      return respond({ requests: [] })
    })
    const c: Connector = {
      id: 'telegram',
      name: 'Telegram',
      kind: 'telegram',
      enabled: false,
      config: {},
      secrets_public: {},
      status: 'not_configured',
      pausable: true,
      short_description: '',
      tools: [],
    }
    renderDetail(c, false)
    fireEvent.change(await screen.findByLabelText(/^Bot token/), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText("Telegram didn't accept this bot token.")).toBeTruthy()
  })
})

function renderDetail(c: Connector, formFirst: boolean) {
  return render(
    <ConnectorDetail
      entry={toEntry(c)}
      onClose={vi.fn()}
      onSetEnabled={vi.fn()}
      onReconnect={vi.fn()}
      onViewDetails={vi.fn()}
      onEditConnection={vi.fn()}
      onRefreshTools={vi.fn()}
      onRemove={vi.fn()}
      onUsePrompt={vi.fn()}
      onSetToolPermission={vi.fn()}
      onSetGroupPermission={vi.fn()}
      configForm={<ConnectorConfig connector={c} />}
      configureOnOpen
      formFirst={formFirst}
    />,
  )
}

describe('the form footer', () => {
  const saved: Connector = {
    id: 'telegram',
    name: 'Telegram',
    kind: 'telegram',
    enabled: true,
    config: {},
    secrets_public: {},
    status: 'active',
    pausable: true,
    short_description: '',
    tools: [],
  }

  it('offers Done — not a greyed-out Save — when a saved connector has no edits', async () => {
    config = { ...ONLINE }
    const { container } = renderDetail(saved, true)
    await screen.findByText('Online as @acme_lucius_bot')
    const foot = container.querySelector('.rd-modal-foot') ?? container
    expect(within(foot as HTMLElement).getByRole('button', { name: 'Done' })).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Approved chat IDs'), { target: { value: '1, 2' } })

    expect(within(foot as HTMLElement).getByRole('button', { name: 'Save' })).toBeTruthy()
    expect(within(foot as HTMLElement).queryByRole('button', { name: 'Done' })).toBeNull()
  })
})
