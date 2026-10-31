// @vitest-environment jsdom
//
// The tab SHELL's wiring — the three things it owns that nothing else can prove:
// the directory is actually mounted (it shipped behind a "coming next" placeholder), the
// bank front door reaches the institution search, and a connector opened from the
// directory reaches a detail page even though it is deliberately absent from the list.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { ConnectorsTab } from './ConnectorsTab'
import { act } from '@testing-library/react'
import { _resetBusForTests, dispatchScreenCommand } from '../../../lucius/bus'
import { setupTargetFor } from './directoryRows'
import { clearQueryCache } from '../../../state/queryCache'
import type { Connector } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => undefined,
}))

const TELEGRAM: Connector = {
  id: 'telegram',
  name: 'Telegram',
  kind: 'telegram',
  enabled: true,
  config: {},
  secrets_public: { has_bot_token: true },
  status: 'active',
  pausable: true,
  short_description: 'Chat with Lucius',
  tools: [],
}

/** Present on the wire but NOT in the list — `isInstalled` drops it, so the only route to
 *  its setup is the directory → detail page. */
const PLAID_UNCONFIGURED: Connector = {
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
}

const PLAID_STATUS = {
  configured: false,
  enabled: false,
  environment: 'sandbox',
  has_secret: false,
  client_id: '',
  has_access_token: false,
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null,
}

beforeEach(() => {
  clearQueryCache()
  authFetch.mockReset()
  authFetch.mockImplementation((url: string) => {
    const body =
      url === '/api/connectors'
        ? { connectors: [TELEGRAM, PLAID_UNCONFIGURED] }
        : url === '/api/plaid/status'
          ? PLAID_STATUS
          : url === '/api/accounts'
            ? []
            : url.startsWith('/api/account-linking/institutions')
              ? { institutions: [], providers: [], manual_import: true }
              : {}
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
})

afterEach(cleanup)

async function renderTab() {
  const view = render(<ConnectorsTab />)
  await screen.findByText('Telegram')
  return view
}

describe('Browse & add is mounted, not a placeholder', () => {
  it('opens the real directory', async () => {
    await renderTab()
    fireEvent.click(screen.getByRole('button', { name: /Browse & add/ }))

    expect(await screen.findByLabelText('Search directory')).toBeTruthy()
    // the placeholder this replaced
    expect(screen.queryByText('The directory is coming next')).toBeNull()
  })

  it('opens it pre-filtered from a section add link', async () => {
    const { container } = await renderTab()
    const link = Array.from(container.querySelectorAll('.cn-addlink')).find(
      (l) => l.textContent?.trim() === 'Add a channel',
    )!
    fireEvent.click(link)
    const tabs = await screen.findAllByRole('tab', { name: /^Channels/ })
    expect(tabs.some((t) => t.getAttribute('aria-selected') === 'true')).toBe(true)
  })
})

describe('a connector that is not in the list is still reachable and configurable', () => {
  /** The directory's own control for one catalog entry — its `+`, or its gear once
   *  installed. Found by `aria-label` rather than an accessible-name role scan: the grid
   *  renders forty-odd cards and this block opens it four times over. */
  function directoryButton(label: string) {
    return waitFor(() => {
      // Scoped to the dialog: the list's own Suggested cards carry the same Add labels.
      const found = document.querySelector<HTMLElement>(`.rd-modal button[aria-label="${label}"]`)
      if (!found) throw new Error(`no directory button labelled "${label}"`)
      return found
    })
  }

  async function openDirectory(label: string) {
    fireEvent.click(screen.getByRole('button', { name: /Browse & add/ }))
    fireEvent.click(await directoryButton(label))
  }

  /** The modal title, which IS the back control on every view below the list. */
  function backControl(container: HTMLElement) {
    return within(container.querySelector('.rd-modal-title')!).getByRole('button')
  }

  it('lands the directory’s Add ON Plaid’s real form — Add already said "set this up"', async () => {
    const { container } = await renderTab()
    // Plaid is filtered out of the list's ROWS (it may still be a Suggested card) — this is the
    // whole point of the directory route.
    expect(container.querySelector('.cn-list')?.textContent).not.toContain('Plaid')

    await openDirectory('Add Plaid')

    // No Connect tap in between: the extra one was the defect. These are PlaidSection's own
    // fields, so it is the one real config surface and not a duplicate built for this entry.
    expect(await screen.findByLabelText(/^Client ID/)).toBeTruthy()
    expect(screen.getByLabelText(/^Secret/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull()
  })

  it('leaves the user on the detail page when they back out of that form', async () => {
    const { container } = await renderTab()
    await openDirectory('Add Plaid')
    await screen.findByLabelText(/^Client ID/)

    fireEvent.click(backControl(container)) // `‹ Plaid`

    expect(await screen.findByRole('button', { name: 'Connect' })).toBeTruthy()
    expect(screen.getByText('Tool permissions')).toBeTruthy()
    expect(screen.queryByLabelText(/^Client ID/)).toBeNull()
    // …and it is the detail page proper, whose own back control returns to the list.
    expect(backControl(container).textContent).toContain('Connectors')
  })

  it('opens the PLAIN detail page from an installed entry’s gear, never the form', async () => {
    await renderTab()
    await openDirectory('Configure Telegram')

    // Telegram already holds credentials, so its gear means "show me its settings page".
    expect(await screen.findByRole('button', { name: 'Pause' })).toBeTruthy()
    expect(screen.getByText('Tool permissions')).toBeTruthy()
    expect(screen.queryByLabelText(/^Bot token/)).toBeNull()
  })
})

describe('the bank front door', () => {
  it('rides with the Data sources section, so it is absent when that section is', async () => {
    const { container } = await renderTab()
    const labels = Array.from(container.querySelectorAll('.cn-addlink')).map((l) =>
      l.textContent?.trim(),
    )
    expect(labels).toEqual(['Add a channel'])
  })

  it('is offered once a data source exists, and searches institutions', async () => {
    authFetch.mockImplementation((url: string) => {
      const body =
        url === '/api/connectors'
          ? { connectors: [{ ...PLAID_UNCONFIGURED, status: 'configured' }] }
          : url.startsWith('/api/account-linking/institutions')
            ? { institutions: [], providers: [], manual_import: true }
            : PLAID_STATUS
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
    })
    const { container } = render(<ConnectorsTab />)
    await screen.findByText('Plaid')

    const link = Array.from(container.querySelectorAll('.cn-addlink')).find(
      (l) => l.textContent?.trim() === 'Find your bank',
    )!
    fireEvent.click(link)

    fireEvent.change(await screen.findByLabelText(/^Country/), { target: { value: 'DE' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() =>
      expect(
        authFetch.mock.calls.some((c: unknown[]) =>
          String(c[0]).startsWith('/api/account-linking/institutions?country=DE'),
        ),
      ).toBe(true),
    )
  })
})

describe('removing a connector for real (native, not just MCP)', () => {
  it('DELETEs the generic connector route, only after the confirmation is accepted', async () => {
    const { container } = await renderTab()
    fireEvent.click(screen.getByText('Telegram'))
    await screen.findByRole('button', { name: 'Pause' })

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const pop = container.querySelector('.rd-kebab-pop')!
    fireEvent.click(within(pop as HTMLElement).getByText('Remove connector'))

    // Not fired yet — the confirmation hasn't been accepted.
    expect(
      authFetch.mock.calls.some((c: unknown[]) => (c[1] as RequestInit)?.method === 'DELETE'),
    ).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() =>
      expect(
        authFetch.mock.calls.some(
          (c: unknown[]) =>
            c[0] === '/api/connectors/telegram' && (c[1] as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    )
  })
})

// Fix: `removable` used to read `!alwaysOn || Object.keys(secrets_public).length > 0` — so a
// connector that is not `pausable` but happens to carry ANY key in `secrets_public` (even a
// `false`-valued one) incorrectly read as removable, confirmation dialog and all. CoinGecko is
// exactly that case (`secrets_public: { has_api_key: true }`); Yahoo Finance never was,
// since its `secrets_public` is always `{}`. Both must behave identically: no confirmation step
// (there is nothing to confirm — the write can never succeed), Remove calls straight through, and
// the backend's honest, named refusal reaches the operator as a toast rather than the action
// being silently unreachable.
describe('a connector that is not pausable (Yahoo Finance, CoinGecko) can never be removed', () => {
  const COINGECKO: Connector = {
    id: 'coingecko',
    name: 'CoinGecko',
    kind: 'coingecko',
    enabled: true,
    config: {},
    // Holds a real credential — the exact shape that used to fool the old `removable` calc.
    secrets_public: { has_api_key: true },
    status: 'active',
    pausable: false,
    short_description: 'Crypto prices, search and facts',
    tools: [],
  }

  const YAHOO: Connector = {
    id: 'yahoo_finance',
    name: 'Yahoo Finance',
    kind: 'yahoo_finance',
    enabled: true,
    config: {},
    secrets_public: {},
    status: 'active',
    pausable: false,
    short_description: 'Stock, ETF and crypto prices',
    tools: [],
  }

  function mockConnectors(connector: Connector, deleteMessage: string) {
    authFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/connectors') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ connectors: [TELEGRAM, connector] }),
        })
      }
      if (url === `/api/connectors/${connector.id}` && init?.method === 'DELETE') {
        return Promise.resolve({
          ok: false,
          status: 422,
          json: () => Promise.resolve({ detail: deleteMessage }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it.each([
    ['CoinGecko', COINGECKO, "CoinGecko powers price lookups across the app and can't be removed."],
    [
      'Yahoo Finance',
      YAHOO,
      "Yahoo Finance powers price lookups across the app and can't be removed.",
    ],
  ])(
    '%s: Remove calls DELETE straight through and shows the refusal, not silence',
    async (_label, connector, message) => {
      mockConnectors(connector, message)
      const { container } = render(<ConnectorsTab />)
      await screen.findByText('Telegram')
      fireEvent.click(screen.getByText(connector.name))
      await screen.findByText('Tool permissions')

      // No Pause/Resume/Connect at all for a connector that is not pausable.
      expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
      expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()

      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      const pop = container.querySelector('.rd-kebab-pop')!
      fireEvent.click(within(pop as HTMLElement).getByText('Remove connector'))

      // Straight through — no confirmation dialog appears (a Remove/Cancel footer would).
      expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull()

      await waitFor(() =>
        expect(
          authFetch.mock.calls.some(
            (c: unknown[]) =>
              c[0] === `/api/connectors/${connector.id}` &&
              (c[1] as RequestInit)?.method === 'DELETE',
          ),
        ).toBe(true),
      )

      // The explanation actually reaches the operator — not a silently absent/no-op action.
      const toast = await screen.findByRole('status')
      expect(toast.textContent).toBe(message)
    },
  )
})

describe('the footer points inbound MCP at Settings → MCP', () => {
  it('no longer sends anyone to Settings → Developer', async () => {
    const { container } = await renderTab()
    const link = within(container).getByRole('link', { name: 'Settings → MCP' })
    expect(link.getAttribute('href')).toBe('/settings?sub=mcp')
    expect(container.textContent).not.toContain('Settings → Developer')
  })
})

describe('connectors.open_setup (Lucius) — opens a form, changes nothing', () => {
  afterEach(() => _resetBusForTests())

  it('opens a directory MCP entry on its add form with the preset pre-filled', async () => {
    await renderTab()
    act(() => dispatchScreenCommand({ name: 'connectors.open_setup', args: { id: 'mcp-ibkr' } }))

    // The add flow for a hosted OAuth server: its endpoint is pre-filled, nothing is saved.
    expect(await screen.findByDisplayValue('https://api.ibkr.com/v1/api/mcp-public')).toBeTruthy()
    expect(authFetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('opens a native connector on its configuration form', async () => {
    await renderTab()
    act(() => dispatchScreenCommand({ name: 'connectors.open_setup', args: { id: 'plaid' } }))
    expect(await screen.findByLabelText(/^Client ID/)).toBeTruthy()
  })

  it('fails cleanly on an id the directory does not have', async () => {
    await renderTab()
    act(() => dispatchScreenCommand({ name: 'connectors.open_setup', args: { id: 'nope' } }))
    expect(await screen.findByText(/isn't in the directory/)).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('resolves the target from live state', () => {
    const telegram = { ...TELEGRAM }
    expect(setupTargetFor([telegram], 'telegram')).toEqual({ kind: 'settings', id: 'telegram' })
    expect(setupTargetFor([PLAID_UNCONFIGURED], 'plaid')).toEqual({ kind: 'form', id: 'plaid' })
    expect(setupTargetFor([], 'mcp-ibkr')).toEqual({ kind: 'add', id: 'mcp-ibkr' })
    expect(setupTargetFor([], 'mcp-custom')).toEqual({ kind: 'unknown' })
    // 'custom' is the Add custom server form — any MCP server the directory doesn't list.
    expect(setupTargetFor([], 'custom')).toEqual({ kind: 'add', id: 'mcp-custom' })
    expect(setupTargetFor([], 'zzz')).toEqual({ kind: 'unknown' })
  })
})
