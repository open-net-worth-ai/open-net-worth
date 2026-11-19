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
let labels: Record<string, string>

function respond(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) })
}

beforeEach(() => {
  config = { ...FRESH }
  pending = []
  labels = {}
  authFetch.mockReset()
  authFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
    if (url === '/api/telegram/config' && init?.method === 'PUT') {
      const body = JSON.parse(init.body!) as { allowed_chat_ids?: string; bot_token?: string }
      if (body.bot_token || !config.configured) {
        config = { ...ONLINE, connection: 'connecting' }
      } else if (body.allowed_chat_ids !== undefined) {
        config = {
          ...config,
          allowed_chat_ids: body.allowed_chat_ids.split(/[\s,]+/).filter(Boolean),
        }
      }
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
    if (url.startsWith('/api/telegram/chats')) return respond({ labels })
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
    expect(screen.queryByLabelText('Add a chat ID')).toBeNull()
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
      expect(
        within(screen.getByRole('list', { name: 'Approved chats' })).getByText('4242'),
      ).toBeTruthy(),
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

function renderDetail(c: Connector, formFirst: boolean, onConfigClosed = vi.fn()) {
  return render(
    <ConnectorDetail
      onConfigClosed={onConfigClosed}
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

    fireEvent.change(screen.getByLabelText('Who can chat'), { target: { value: 'allowlist' } })

    expect(within(foot as HTMLElement).getByRole('button', { name: 'Save' })).toBeTruthy()
    expect(within(foot as HTMLElement).getByText('Save so your changes take effect')).toBeTruthy()
    expect(within(foot as HTMLElement).queryByRole('button', { name: 'Done' })).toBeNull()
  })

  it('Save and connect refreshes the page like the footer Save, so the badge follows the bot', async () => {
    const reload = vi.fn()
    renderDetail(saved, true, reload)
    fireEvent.change(await screen.findByLabelText(/^Bot token/), { target: { value: '123:ABC' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save and connect' }))
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })
})

describe('the bot token', () => {
  it('a typed token on a new connector puts Save right under it, and that Save connects', async () => {
    render(<TelegramSection />)
    const field = (await screen.findByLabelText(/Bot token/)) as HTMLInputElement
    expect(screen.queryByRole('button', { name: 'Save and connect' })).toBeNull()

    fireEvent.change(field, { target: { value: '123:ABC' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save and connect' }))

    await waitFor(() =>
      expect(
        authFetch.mock.calls.some(
          ([u, i]) => u === '/api/telegram/config' && (i as { method?: string })?.method === 'PUT',
        ),
      ).toBe(true),
    )
  })

  it('a new token on a set-up bot says approved people carry over', async () => {
    config = { ...ONLINE, allowed_chat_ids: ['111'] }
    render(<TelegramSection />)
    const field = (await screen.findByLabelText(/Bot token/)) as HTMLInputElement

    fireEvent.change(field, { target: { value: '456:DEF' } })

    expect(screen.getByText(/Everyone approved below stays approved/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save and switch bot' })).toBeTruthy()
  })
})

describe('approved chats', () => {
  it('are one list: the name with the ID under it, and the bare ID when this bot cannot name it', async () => {
    config = { ...ONLINE, allowed_chat_ids: ['111', '222'] }
    labels = { '111': 'Ada Lovelace (@ada)' }
    render(<TelegramSection />)

    const list = await screen.findByRole('list', { name: 'Approved chats' })
    expect(await within(list).findByText('Ada Lovelace (@ada)')).toBeTruthy()
    expect(within(list).getByText('111')).toBeTruthy()
    expect(within(list).getByText('222')).toBeTruthy()
    expect(within(list).getByText('Not found — check the number')).toBeTruthy()
    expect(screen.queryByDisplayValue(/111/)).toBeNull()
  })

  it('Remove asks first, then revokes at once with no second Save', async () => {
    config = { ...ONLINE, allowed_chat_ids: ['111', '222'] }
    labels = { '111': 'Ada Lovelace (@ada)' }
    render(<TelegramSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Ada Lovelace (@ada)' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Remove Ada Lovelace (@ada)?')).toBeTruthy()
    expect(authFetch.mock.calls.some(([, i]) => i?.method === 'PUT')).toBe(false)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      const put = authFetch.mock.calls.find(([, i]) => i?.method === 'PUT')
      expect(put).toBeTruthy()
      expect(JSON.parse(put![1].body)).toEqual({ allowed_chat_ids: '222' })
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const list = screen.getByRole('list', { name: 'Approved chats' })
    expect(within(list).queryByText('Ada Lovelace (@ada)')).toBeNull()
    expect(within(list).getByText('222')).toBeTruthy()
  })

  it('Add on a set-up bot takes effect at once — no Save', async () => {
    config = { ...ONLINE, allowed_chat_ids: ['111'] }
    render(<TelegramSection />)
    fireEvent.change(await screen.findByLabelText('Add a chat ID'), { target: { value: '333' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      const put = authFetch.mock.calls.find(([, i]) => i?.method === 'PUT')
      expect(put).toBeTruthy()
      expect(JSON.parse(put![1].body)).toEqual({ allowed_chat_ids: '111, 333' })
    })
    expect(
      await within(screen.getByRole('list', { name: 'Approved chats' })).findByText('333'),
    ).toBeTruthy()
  })

  it('looks up names for the IDs on screen', async () => {
    config = { ...ONLINE, allowed_chat_ids: ['111'] }
    render(<TelegramSection />)
    await waitFor(() =>
      expect(authFetch.mock.calls.some(([u]) => u === '/api/telegram/chats?ids=111')).toBe(true),
    )
  })

  it('Add refuses something that is not a chat ID', async () => {
    config = { ...ONLINE }
    render(<TelegramSection />)
    fireEvent.change(await screen.findByLabelText('Add a chat ID'), { target: { value: '@ada' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    expect(screen.getByText(/A chat ID is a number/)).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Approved chats' })).toBeNull()
  })
})

describe('switching to Allowlist', () => {
  it('says how to get a chat ID, once, with a link to the bot', async () => {
    config = { ...ONLINE }
    render(<TelegramSection />)
    fireEvent.change(await screen.findByLabelText('Who can chat'), {
      target: { value: 'allowlist' },
    })

    expect(screen.getByRole('region', { name: 'Add who can chat' })).toBeTruthy()
    expect(screen.getByText(/Have each person send/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /Open @acme_lucius_bot in Telegram/ })).toBeTruthy()
    expect(screen.queryByText(/Save to switch to Allowlist/)).toBeNull()
  })
})
