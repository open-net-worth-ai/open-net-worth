// @vitest-environment jsdom
//
// The regression this file exists for: the redesigned tab shipped routed and live with the
// detail page's primary action wired to nothing but `PATCH {enabled}`, so there was no path
// to ANY credential form — no way to enter a Plaid key, a Telegram bot token, IMAP
// credentials. Every provider below is asserted end to end: Connect opens THAT provider's
// real form, its real fields are on screen, and Save reaches the same per-provider endpoint
// it reached from the outgoing tab.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { ConnectorDetail } from './ConnectorDetail'
import { ConnectorConfig, hasConfigForm } from './ConnectorConfig'
import { toEntry } from './adapter'
import type { Connector } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(cleanup)

// ── wire fixtures ───────────────────────────────────────────────────────────

function connector(overrides: Partial<Connector> = {}): Connector {
  return {
    id: 'plaid',
    name: 'Plaid',
    kind: 'plaid',
    enabled: false,
    config: {},
    secrets_public: {},
    status: 'not_configured',
    pausable: true,
    short_description: 'Bank + card aggregation',
    tools: [],
    ...overrides,
  }
}

// Yodlee is untouched by the connectors-as-data-sources RFC (disabled=True, excluded from
// PROVIDER_KEYS) and still carries its own stats shape — kept for that one entry only.
const SYNC_STATS = { created: 0, skipped: 0, accounts_linked: 0 }
const SYNC_BASE = {
  configured: false,
  enabled: false,
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null,
}

// The per-provider self-fetch each ConnectorAccountsList makes — the real backend
// (connector_fetch.provider_accounts) always includes `accounts`, so the component doesn't
// guard against it being absent; every provider under test needs an entry here.
const emptyAccounts = (label: string) => ({
  status: 'not_configured',
  provider_label: label,
  accounts: [],
})

/** GET route → the status payload that provider's form loads itself with. */
const STATUS: Record<string, unknown> = {
  // Every bank/broker section's new Accounts list (ConnectorAccountsList) reads the shared
  // GET /api/accounts cache — an empty array is the honest "no app accounts yet" answer.
  '/api/accounts': [],
  '/api/connectors/plaid/accounts': emptyAccounts('Plaid'),
  '/api/connectors/lunch_money/accounts': emptyAccounts('Lunch Money'),
  '/api/connectors/ynab/accounts': emptyAccounts('YNAB'),
  '/api/connectors/enable_banking/accounts': emptyAccounts('Enable Banking'),
  '/api/connectors/alpaca/accounts': emptyAccounts('Alpaca'),
  '/api/connectors/ibkr/accounts': emptyAccounts('Interactive Brokers'),
  '/api/plaid/status': {
    ...SYNC_BASE,
    environment: 'sandbox',
    has_secret: false,
    client_id: '',
    has_access_token: false,
  },
  '/api/lunch-money/status': {
    ...SYNC_BASE,
    has_access_token: false,
  },
  '/api/ynab/sync': {
    token_saved: false,
    enabled: false,
    interval: '1h',
    intervals: ['15m', '30m', '1h', '6h', 'daily', 'manual'],
    plan_id: null,
    plan_name: null,
    running: false,
    last_run: null,
    next_run_at: null,
    conflicts: [],
  },
  '/api/ynab/status': {
    ...SYNC_BASE,
    has_access_token: false,
    budget_id: null,
    budget_name: null,
    budget_currency: null,
  },
  '/api/enable-banking/status': {
    ...SYNC_BASE,
    has_credentials: false,
    application_id: '',
    aspsp: '',
    country: '',
    has_session: false,
  },
  '/api/yodlee/status': {
    ...SYNC_BASE,
    api_endpoint: '',
    has_secret: false,
    client_id: '',
    has_user_credentials: false,
    username: '',
    stats: SYNC_STATS,
  },
  '/api/alpaca/status': {
    ...SYNC_BASE,
    environment: 'paper',
    has_keys: false,
    cash: null,
    buying_power: null,
  },
  '/api/ibkr/status': {
    ...SYNC_BASE,
    has_token: false,
    flex_query_id: '',
  },
  '/api/telegram/config': {
    configured: false,
    enabled: false,
    allowed_chat_ids: [],
    access_policy: 'allowlist',
    has_bot_token: false,
  },
  '/api/search/config': {
    default_provider: 'duckduckgo',
    providers: [
      {
        id: 'tavily',
        label: 'Tavily',
        hint: 'Agent-oriented search',
        docs_url: 'https://tavily.com',
        needs_api_key: true,
        native_fetch: true,
        extra_fields: [],
        enabled: false,
        has_key: false,
        is_active: false,
      },
    ],
  },
  '/api/channels': {
    channels: [
      {
        name: 'webhook',
        enabled: false,
        secret_header_name: 'X-Webhook-Secret',
        has_url: false,
        url_display: '',
        has_secret: false,
        configured: false,
      },
    ],
  },
  '/api/email/config': {
    configured: false,
    enabled: false,
    has_password: false,
    provider: null,
    address: '',
    imap_host: '',
    imap_port: 993,
    imap_folder: 'INBOX',
    lookback_days: 30,
    smtp_host: '',
    smtp_port: 465,
    use_tls: true,
    last_status: 'never_run',
    last_error: null,
  },
  '/api/telegram/pairings': { pairings: [] },
}

beforeEach(() => {
  authFetch.mockReset()
  authFetch.mockImplementation((url: string) => {
    const body = STATUS[url.split('?')[0]!] ?? {}
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
})

// ── the harness ─────────────────────────────────────────────────────────────

/** The detail page as `ConnectorsTab` composes it: the entry through the adapter, the
 *  config form through `ConnectorConfig`. Nothing here is a stand-in for the real wiring. */
function renderDetail(c: Connector) {
  const form = hasConfigForm(c) ? <ConnectorConfig connector={c} /> : undefined
  const onSetEnabled = vi.fn()
  const onConfigClosed = vi.fn()
  const view = render(
    <ConnectorDetail
      entry={toEntry(c)}
      onClose={vi.fn()}
      onSetEnabled={onSetEnabled}
      onReconnect={vi.fn()}
      onViewDetails={vi.fn()}
      onEditConnection={vi.fn()}
      onRefreshTools={vi.fn()}
      onRemove={vi.fn()}
      onUsePrompt={vi.fn()}
      onSetToolPermission={vi.fn()}
      onSetGroupPermission={vi.fn()}
      configForm={form}
      onConfigClosed={onConfigClosed}
    />,
  )
  return { ...view, onSetEnabled, onConfigClosed }
}

function clickConnect() {
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
}

/** The same primary action, once credentials already exist — it reads Resume there, not
 *  Connect (spec: Pause/Resume/Remove rework). */
function clickResume() {
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
}

/** A label matched from its start — `PasswordField` appends a reveal affordance to some,
 *  and several labels carry parentheses that would otherwise read as regex groups. */
function labelStartingWith(label: string): RegExp {
  return new RegExp('^' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
}

/** The PUT/POST the form fired, if any — the GETs each form makes on mount are ignored. */
function writeCalls(): [string, { method?: string }][] {
  return authFetch.mock.calls.filter(
    (c: unknown[]) => (c[1] as { method?: string } | undefined)?.method,
  ) as [string, { method?: string }][]
}

// ── every provider, one case each ───────────────────────────────────────────

interface ProviderCase {
  /** What the case is called in the run output. */
  name: string
  connector: Connector
  /** A field only THIS provider's form renders — proof the right form opened. */
  fields: string[]
  saveLabel: string
  /** The per-provider write endpoint, unchanged from the outgoing tab. */
  saveUrl: string
  /** Field(s) to type into before Save — a form whose Save now lives in the modal footer
   *  is disabled until it is actually dirty, unlike the old always-on inline button. */
  fillBeforeSave?: { label: string; value: string }[]
}

const CASES: ProviderCase[] = [
  {
    name: 'Plaid',
    connector: connector(),
    fields: ['Client ID', 'Secret', 'Environment'],
    saveLabel: 'Save',
    saveUrl: '/api/plaid/config',
    fillBeforeSave: [
      { label: 'Client ID', value: 'test-client-id' },
      { label: 'Secret', value: 'test-secret' },
    ],
  },
  {
    name: 'Lunch Money',
    connector: connector({ id: 'lunch_money', name: 'Lunch Money', kind: 'lunch_money' }),
    fields: ['Access token'],
    saveLabel: 'Save',
    saveUrl: '/api/lunch-money/config',
    fillBeforeSave: [{ label: 'Access token', value: 'test-lm-token' }],
  },
  {
    name: 'YNAB',
    connector: connector({ id: 'ynab', name: 'YNAB', kind: 'ynab' }),
    fields: ['Access token'],
    saveLabel: 'Save',
    saveUrl: '/api/ynab/config',
    fillBeforeSave: [{ label: 'Access token', value: 'test-ynab-token' }],
  },
  {
    name: 'Enable Banking',
    connector: connector({
      id: 'enable_banking',
      name: 'Enable Banking',
      kind: 'enable_banking',
    }),
    fields: ['Application ID', 'Private key (PEM)'],
    saveLabel: 'Save',
    saveUrl: '/api/enable-banking/config',
    fillBeforeSave: [
      { label: 'Application ID', value: 'test-app-id' },
      {
        // Multi-line fixture, not a real key — no PEM header/footer, so it stays out of
        // gitleaks' private-key rule (it does content-shape matching, not context-aware
        // "is this a fixture" detection). The field itself doesn't validate PEM-ness; this
        // only proves the multi-line value reaches Save.
        label: 'Private key (PEM)',
        value: 'not-a-real-key-line-one\nnot-a-real-key-line-two',
      },
    ],
  },
  {
    name: 'Yodlee',
    connector: connector({ id: 'yodlee', name: 'Yodlee', kind: 'yodlee' }),
    fields: ['Client ID', 'Client secret (cobrand password)', 'Username (loginName)'],
    saveLabel: 'Save',
    saveUrl: '/api/yodlee/config',
    fillBeforeSave: [
      { label: 'Client ID', value: 'test-client-id' },
      { label: 'Client secret (cobrand password)', value: 'test-cobrand-secret' },
      { label: 'Username (loginName)', value: 'test-login-name' },
      { label: 'Password', value: 'test-user-password' },
    ],
  },
  {
    name: 'Alpaca',
    connector: connector({ id: 'alpaca', name: 'Alpaca', kind: 'alpaca' }),
    fields: ['API key id', 'Secret key'],
    saveLabel: 'Save',
    saveUrl: '/api/alpaca/config',
    fillBeforeSave: [
      { label: 'API key id', value: 'test-key-id' },
      { label: 'Secret key', value: 'test-secret' },
    ],
  },
  {
    name: 'IBKR Flex',
    connector: connector({ id: 'ibkr', name: 'IBKR Flex', kind: 'ibkr' }),
    fields: ['Flex token', 'Flex query ID'],
    saveLabel: 'Save',
    saveUrl: '/api/ibkr/config',
    fillBeforeSave: [
      { label: 'Flex token', value: 'test-flex-token' },
      { label: 'Flex query ID', value: '123456' },
    ],
  },
  {
    name: 'Telegram',
    connector: connector({
      id: 'telegram',
      name: 'Telegram',
      kind: 'telegram',
      config: {},
    }),
    fields: ['Bot token', 'Allowed chat IDs'],
    saveLabel: 'Save',
    saveUrl: '/api/telegram/config',
    fillBeforeSave: [
      { label: 'Bot token', value: 'test-bot-token' },
      { label: 'Allowed chat IDs', value: '123456789' },
    ],
  },
  {
    name: 'Web search (Tavily)',
    connector: connector({
      id: 'web_search:tavily',
      name: 'Web search • Tavily',
      kind: 'web_search',
      config: {},
    }),
    fields: ['API key'],
    saveLabel: 'Save',
    saveUrl: '/api/search/config/tavily',
    fillBeforeSave: [{ label: 'API key', value: 'test-api-key' }],
  },
  {
    name: 'Webhook',
    connector: connector({
      id: 'webhook',
      name: 'Webhook',
      kind: 'webhook',
      config: {},
    }),
    fields: ['URL', 'Secret header name'],
    saveLabel: 'Save',
    saveUrl: '/api/channels/webhook',
    fillBeforeSave: [{ label: 'URL', value: 'https://hooks.example.com/abc' }],
  },
]

describe('Connect opens the provider’s own configuration form', () => {
  for (const c of CASES) {
    it(`${c.name}: shows its real fields and saves to ${c.saveUrl}`, async () => {
      renderDetail(c.connector)
      clickConnect()

      for (const label of c.fields) {
        expect(await screen.findByLabelText(labelStartingWith(label))).toBeTruthy()
      }

      for (const fill of c.fillBeforeSave ?? []) {
        fireEvent.change(screen.getByLabelText(labelStartingWith(fill.label)), {
          target: { value: fill.value },
        })
      }

      fireEvent.click(screen.getByRole('button', { name: c.saveLabel }))
      await waitFor(() => expect(writeCalls().length).toBeGreaterThan(0))
      expect(writeCalls()[0]![0]).toBe(c.saveUrl)
    })
  }

  it('Email opens ONE unified form (Account/Receive/Send) and Save writes ONE request', async () => {
    const email = connector({
      id: 'email',
      name: 'Email',
      kind: 'email',
      config: {},
    })
    renderDetail(email)
    clickConnect()

    // The shared Account fields, entered once.
    expect(await screen.findByLabelText(labelStartingWith('Email / username'))).toBeTruthy()
    expect(screen.getByLabelText(labelStartingWith('App password'))).toBeTruthy()
    // Never set up, the form opens on Gmail (owner, 2026-10-09): its hosts are known, so the
    // host/port fields stay hidden until Custom asks for a self-hosted server.
    expect(screen.getByRole('button', { name: 'Gmail' }).className).toContain('chip--active')
    expect(screen.queryByLabelText(/^IMAP host/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
    expect(screen.getByLabelText(/^IMAP host/)).toBeTruthy()
    expect(screen.getByLabelText(/^SMTP host/)).toBeTruthy()
    // The one remaining receive-only field.
    expect(screen.getByLabelText(labelStartingWith('Mail folder'))).toBeTruthy()
    // From/To/TLS/lookback are gone entirely (owner decision, 2026-08-18) — From/To always
    // equal the account address, TLS is always on, lookback has no live consumer at all
    // (the on-demand search_messages/get_messages tools take an explicit date window per
    // call; there is no stored poll cursor for it to seed).
    expect(screen.queryByLabelText(labelStartingWith('From address'))).toBeNull()
    expect(screen.queryByLabelText(labelStartingWith('To address'))).toBeNull()
    expect(screen.queryByLabelText(/Use TLS/)).toBeNull()
    expect(screen.queryByLabelText(labelStartingWith('Initial lookback'))).toBeNull()
    // No per-form Enable checkbox anywhere on this screen — Pause/Resume owns that now.
    expect(screen.queryByLabelText(/^Enable$/)).toBeNull()
    expect(screen.queryByLabelText(/Sending enabled/)).toBeNull()

    fireEvent.change(screen.getByLabelText(/^SMTP host/), {
      target: { value: 'smtp.example.com' },
    })
    // A password is required to save — no saved credential exists yet on this fresh connector.
    fireEvent.change(screen.getByLabelText(labelStartingWith('App password')), {
      target: { value: 'app-specific-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(writeCalls().length).toBe(1))
    expect(writeCalls().map((c) => c[0])).toEqual(['/api/email/config'])
  })

  it('Email’s provider chip fills BOTH IMAP and SMTP host/port from one click and saves them together', async () => {
    const email = connector({
      id: 'email',
      name: 'Email',
      kind: 'email',
      config: {},
    })
    renderDetail(email)
    clickConnect()
    await screen.findByLabelText(labelStartingWith('Email / username'))

    fireEvent.click(screen.getByRole('button', { name: 'Gmail' }))
    // A matched preset hides the host/port fields (nothing left to re-enter) and derives TLS,
    // so the only remaining trace on screen is the chip's own active state.
    expect(screen.queryByLabelText(/^IMAP host/)).toBeNull()
    expect(screen.queryByLabelText(/^SMTP host/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Gmail' }).className).toContain('chip--active')

    fireEvent.change(screen.getByLabelText(labelStartingWith('App password')), {
      target: { value: 'app-specific-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(writeCalls().length).toBe(1))
    const call = writeCalls().find((c) => c[0] === '/api/email/config')!
    const body = JSON.parse((call[1] as { body: string }).body) as Record<string, unknown>
    expect(body).toMatchObject({
      imap_host: 'imap.gmail.com',
      imap_port: 993,
      smtp_host: 'smtp.gmail.com',
      smtp_port: 587,
      use_tls: true,
      password: 'app-specific-secret',
    })
  })
})

describe('Connect is credentials-first, not a flag flip', () => {
  it('opens the form when the connector holds none', async () => {
    const { onSetEnabled } = renderDetail(connector())
    clickConnect()
    expect(await screen.findByLabelText(/^Client ID/)).toBeTruthy()
    // the regression: this used to be the ONLY thing Connect did
    expect(onSetEnabled).not.toHaveBeenCalled()
  })

  it('re-arms directly once it does — a saved connector does not re-ask for its key', () => {
    const { onSetEnabled } = renderDetail(connector({ status: 'configured' }))
    clickResume()
    expect(onSetEnabled).toHaveBeenCalledWith(true)
    expect(screen.queryByLabelText(/^Client ID/)).toBeNull()
  })

  it('falls back to the flag flip for a connector with no form at all (MCP)', () => {
    const { onSetEnabled } = renderDetail(
      connector({
        id: 'mcp:tax',
        name: 'IRAS Tax Tables',
        kind: 'mcp',
        config: { url: 'https://mcp.example' },
      }),
    )
    clickResume()
    expect(onSetEnabled).toHaveBeenCalledWith(true)
  })
})

describe('the form is a view of the detail page, not a dialog stacked on it', () => {
  it('keeps exactly one modal, retitled to a back control, and reports its own close', async () => {
    const { container, onConfigClosed } = renderDetail(connector())
    clickConnect()
    await screen.findByLabelText(/^Client ID/)

    expect(container.querySelectorAll('.rd-modal-scrim')).toHaveLength(1)
    const title = container.querySelector('.rd-modal-title')!
    expect(title.textContent).toContain('Plaid')

    // Plaid now publishes save state (the eye-reveal wiring), so the footer reads "Save"
    // rather than "Done" — leaving without saving is the back-title control instead, exactly
    // as it always was for a form with nothing to publish (see ./formSave's docstring).
    fireEvent.click(title.querySelector('button')!)
    expect(onConfigClosed).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Tool permissions')).toBeTruthy()
  })

  it('offers Edit configuration so a CONNECTED connector can still rotate its key', () => {
    const { container } = renderDetail(connector({ status: 'active', enabled: true }))
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const items = Array.from(container.querySelectorAll('.rd-kebab-item')).map((i) => i.textContent)
    expect(items).toContain('Edit configuration')
  })
})

describe('a connector with no form of its own', () => {
  it('is offered to a connector that is not pausable — Yahoo Finance and CoinGecko still have settings', () => {
    expect(hasConfigForm(connector({ id: 'coingecko', kind: 'coingecko', pausable: false }))).toBe(
      true,
    )
    expect(hasConfigForm(connector())).toBe(true)
  })

  it('is refused to a kind that has no form', () => {
    expect(hasConfigForm(connector({ id: 'something', kind: 'something' }))).toBe(false)
  })

  it('is refused to MCP servers, which the add flow configures instead', () => {
    expect(hasConfigForm(connector({ id: 'mcp:x', kind: 'mcp' }))).toBe(false)
  })
})
