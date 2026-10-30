// @vitest-environment jsdom

/**
 * Browse & add — the behaviours spec §6/§7 call out by name, each of which has already been
 * a real defect in this surface:
 *
 *   - an installed entry STAYS LISTED and swaps `+` for a gear (hiding it made a filter read
 *     "No matches" purely because the one real channel was already set up);
 *   - the results area holds its 480px floor, so filtering to two results doesn't shrink the
 *     dialog out from under the pinned header and footer;
 *   - the generic "Custom MCP server" card skips the preview entirely — it has nothing to show;
 *   - an UNREACHED server is never persisted.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { DirectoryModal } from './DirectoryModal'
import type { Connector } from './types'

const mockAuthFetch = vi.fn()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

afterEach(cleanup)
beforeEach(() => mockAuthFetch.mockReset())

/** A label matched from its start — a required field's asterisk is appended inside the label. */
function labelStartingWith(label: string): RegExp {
  return new RegExp('^' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
}

function connector(overrides: Partial<Connector> & { id: string }): Connector {
  return {
    name: overrides.id,
    kind: 'telegram',
    enabled: true,
    config: {},
    secrets_public: {},
    status: 'active',
    pausable: true,
    ...overrides,
  }
}

function open(props: Partial<Parameters<typeof DirectoryModal>[0]> = {}) {
  return render(
    <DirectoryModal
      open
      onClose={props.onClose ?? (() => {})}
      connectors={props.connectors ?? []}
      onFocusConnector={props.onFocusConnector ?? (() => {})}
      onAdded={props.onAdded}
      toast={props.toast}
      family={props.family}
      previewId={props.previewId}
    />,
  )
}

/** Find a directory card by its visible name. */
function card(container: HTMLElement, name: string): HTMLElement {
  const found = [...container.querySelectorAll('.cn-dircard')].find((el) =>
    el.querySelector('.cn-dircard-name')?.textContent?.startsWith(name),
  )
  if (!found) throw new Error(`no card for ${name}`)
  return found as HTMLElement
}

describe('installed entries stay listed and swap the + for a gear', () => {
  it('renders an Add button for a connector that is not installed', () => {
    const { container } = open()
    const telegram = card(container, 'Telegram')
    expect(telegram.querySelector('button[aria-label="Add Telegram"]')).toBeTruthy()
    expect(telegram.querySelector('button[aria-label="Configure Telegram"]')).toBeFalsy()
  })

  it('KEEPS the card once installed and swaps in the settings gear', () => {
    const { container } = open({
      connectors: [connector({ id: 'telegram', name: 'Telegram', status: 'active' })],
    })
    const telegram = card(container, 'Telegram')
    expect(telegram.querySelector('button[aria-label="Configure Telegram"]')).toBeTruthy()
    expect(telegram.querySelector('button[aria-label="Add Telegram"]')).toBeFalsy()
  })

  it('does not leave the Channels filter empty just because its one channel is installed', () => {
    const { container, getByText } = open({
      connectors: [connector({ id: 'telegram', name: 'Telegram', status: 'active' })],
    })
    fireEvent.click(getByText('Channels'))
    expect(container.querySelectorAll('.cn-dircard').length).toBe(1)
    expect(container.textContent).not.toContain('No matches')
  })

  it('opens the LANDING page when an installed card is clicked, not the detail page', () => {
    const onFocusConnector = vi.fn()
    const { container } = open({
      connectors: [connector({ id: 'telegram', name: 'Telegram', status: 'active' })],
      onFocusConnector,
    })
    fireEvent.click(card(container, 'Telegram'))
    // Every card body opens its landing page — installed or not — so an installed connector
    // stays readable before you act on it. Only the action ON that page differs. The gear is
    // the explicit shortcut for skipping straight to settings, covered by the test below.
    expect(onFocusConnector).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Back')
  })

  it('sends the GEAR as a plain focus — an installed entry has nothing to set up', () => {
    const onFocusConnector = vi.fn()
    const { container } = open({
      connectors: [connector({ id: 'telegram', name: 'Telegram', status: 'active' })],
      onFocusConnector,
    })
    fireEvent.click(
      card(container, 'Telegram').querySelector('button[aria-label="Configure Telegram"]')!,
    )
    // No second argument: the gear means "show me its settings page", NOT "reopen the
    // credential form" — asserted exactly, since an extra `{ configure: true }` here would
    // reopen a form over a connector that is already set up.
    expect(onFocusConnector).toHaveBeenCalledWith('telegram')
  })

  it('lists an installed MCP server that no catalog could know about', () => {
    const { container } = open({
      connectors: [
        connector({
          id: 'mcp:iras-tax-tables',
          name: 'IRAS Tax Tables',
          kind: 'mcp',
        }),
      ],
    })
    const row = card(container, 'IRAS Tax Tables')
    expect(row.querySelector('button[aria-label="Configure IRAS Tax Tables"]')).toBeTruthy()
  })
})

/**
 * Clicking Add already declares intent to set the connector up, so it lands ON the form
 * rather than one tap short of it (D24). The MCP path is untouched: an MCP server is
 * configured by the add flow, not by a credential form.
 */
describe('Add on a NATIVE connector declares the intent to set it up', () => {
  it('routes to the detail page WITH the configure intent, from the card', () => {
    const onFocusConnector = vi.fn()
    const onClose = vi.fn()
    const { container } = open({ onFocusConnector, onClose })
    fireEvent.click(card(container, 'Telegram').querySelector('button[aria-label="Add Telegram"]')!)

    expect(onFocusConnector).toHaveBeenCalledWith('telegram', { configure: true })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does the same from the preview page’s Add', () => {
    const onFocusConnector = vi.fn()
    const { container, getByText } = open({ onFocusConnector })
    fireEvent.click(card(container, 'Telegram'))
    fireEvent.click(getByText('Add connector'))
    expect(onFocusConnector).toHaveBeenCalledWith('telegram', { configure: true })
  })

  it('leaves the MCP path alone — Add there starts the add flow and focuses nothing', () => {
    const onFocusConnector = vi.fn()
    const { container, getByText } = open({ onFocusConnector })
    fireEvent.click(getByText('MCP'))
    fireEvent.click(card(container, 'Playwright MCP').querySelector('button[aria-label^="Add"]')!)

    expect(getByText('Connect')).toBeTruthy()
    expect(onFocusConnector).not.toHaveBeenCalled()
  })
})

describe('the verified marker is a text badge, not a check', () => {
  it('renders "Verified" as text', () => {
    const { container } = open()
    const tavily = card(container, 'Tavily')
    expect(tavily.querySelector('.cn-verified-tag')?.textContent).toBe('Verified')
  })
})

describe('the 480px results floor', () => {
  it('holds when a filter narrows the grid to two results', () => {
    const { container, getByLabelText } = open()
    const grid = () => container.querySelector('.cn-dirgrid') as HTMLElement
    const results = () => grid().parentElement as HTMLElement

    expect(results().style.minHeight).toBe('480px')

    // Any term matching exactly two cards will do — the COUNT is what this pins, not the word.
    // ("aggregation" used to match Plaid + Yodlee; "bank and card feeds" matched Plaid + Yodlee
    // too, until Yodlee's catalog entry was removed 2026-08-14 — see
    // docs/adr/2026-08-14-hide-yodlee-enterprise-only-pricing.md.)
    fireEvent.change(getByLabelText('Search directory'), {
      target: { value: 'read-only' },
    })
    expect(grid().querySelectorAll('.cn-dircard').length).toBe(2)
    expect(results().style.minHeight).toBe('480px')
  })

  it('holds even when nothing matches at all', () => {
    const { container, getByLabelText } = open()
    fireEvent.change(getByLabelText('Search directory'), { target: { value: 'zzzz' } })
    expect(container.textContent).toContain('No matches')
    const results = container.querySelector('.cn-dirgrid')?.parentElement as HTMLElement
    expect(results.style.minHeight).toBe('480px')
  })
})

describe('Suggested (the Connectors list’s block, not a separate Popular row)', () => {
  const suggested = (container: HTMLElement) =>
    container.querySelector('[aria-label="Suggested connectors"]')

  it('shows on the unfiltered, unsearched view only', () => {
    const { container, getByLabelText } = open()
    expect(suggested(container)).toBeTruthy()

    fireEvent.change(getByLabelText('Search directory'), { target: { value: 'tav' } })
    expect(suggested(container)).toBeFalsy()
  })

  it('hides once a family chip is picked', () => {
    const { container, getByRole } = open()
    // A role query, not text: "Web" is also a category chip on the cards now.
    fireEvent.click(getByRole('button', { name: 'Web' }))
    expect(suggested(container)).toBeFalsy()
  })

  it('offers at most three, never one already added, as the dashed suggested cards', () => {
    const { container } = open({
      connectors: [
        {
          id: 'telegram',
          kind: 'telegram',
          name: 'Telegram',
          enabled: true,
          status: 'active',
          pausable: true,
          config: {},
          secrets_public: {},
          tools: [],
        },
      ],
    })
    const cards = [...suggested(container)!.querySelectorAll('.cn-dircard.suggested')]
    expect(cards.length).toBeGreaterThan(0)
    expect(cards.length).toBeLessThanOrEqual(3)
    expect(cards.some((c) => c.textContent?.includes('Telegram'))).toBe(false)
  })
})

describe('opening a card resets .rd-modal-body scroll — it must never inherit the grid’s offset', () => {
  it('scrolls back to the top when a preview page opens, and again when it closes', () => {
    const { container, getByText } = open()
    fireEvent.click(getByText('MCP'))
    const body = container.querySelector('.rd-modal-body') as HTMLElement
    body.scrollTop = 400

    fireEvent.click(card(container, 'Playwright MCP'))
    expect(body.scrollTop).toBe(0)

    body.scrollTop = 250
    fireEvent.click(container.querySelector('.rd-modal-foot button') as HTMLElement)
    expect(body.scrollTop).toBe(0)
  })
})

describe('the generic MCP card skips the preview', () => {
  it('goes straight to the add form when the card body is clicked', () => {
    const { container, getByText, queryByText } = open()
    fireEvent.click(getByText('MCP'))
    fireEvent.click(card(container, 'Custom MCP server'))

    // Step 1 of the add flow, not a preview page.
    expect(getByText('Connect')).toBeTruthy()
    expect(getByText('Paste from a config snippet')).toBeTruthy()
    expect(queryByText('Categories')).toBeFalsy()
  })

  it('previews a catalog MCP entry that HAS something to show', () => {
    const { container, getByText } = open()
    fireEvent.click(getByText('MCP'))
    fireEvent.click(card(container, 'Playwright MCP'))

    expect(getByText('Categories')).toBeTruthy()
    // D23: attribution + a Details footer, never the boxed panel.
    // Shape: `DirectoryPreview.test.tsx`.
    expect(getByText(/Developed by/)).toBeTruthy()
    expect(getByText('Details')).toBeTruthy()
  })
})

describe('step 1 → step 2: an unreached server is never saved', () => {
  function openAddForm(props: Parameters<typeof open>[0] = {}) {
    const view = open(props)
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))
    return view
  }

  it('keeps Test connect disabled until there is something to connect to', () => {
    const { getByText, getByLabelText } = openAddForm()
    expect((getByText('Connect') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    expect((getByText('Connect') as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows the REAL connect error and persists NOTHING on failure', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        server_info: null,
        tools: [],
        suggested_name: '',
        suggested_id: '',
        error: 'Connection refused by mcp.example.com',
      }),
    })

    const { getByText, getAllByText, getByLabelText } = openAddForm()
    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    fireEvent.click(getByText('Connect'))

    await waitFor(() => expect(getByText(/Connection refused/)).toBeTruthy())

    // Back + Retry, per spec §7 — and no create call whatsoever. Two "Back"s now (2026-08-22):
    // the header also reads "Back" outside the form step, alongside the footer's own
    // dedicated Back/Retry pair for this step — redundant, not a conflict, since both do
    // the identical `flow.back()`.
    expect(getByText('Retry')).toBeTruthy()
    expect(getAllByText('Back').length).toBe(2)
    const paths = mockAuthFetch.mock.calls.map((c) => c[0])
    expect(paths).toEqual(['/api/mcp-clients/probe'])
    expect(paths).not.toContain('/api/mcp-clients')
  })

  it('offers the fix when a 401 says the server wants a sign-in, and switches in one click', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        server_info: null,
        tools: [],
        suggested_name: '',
        suggested_id: '',
        error: "server 'mcp-example-com' returned HTTP 401: authentication required",
        needs_auth: 'oauth',
      }),
    })

    const { getByText, getByLabelText, getByRole } = openAddForm()
    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    fireEvent.click(getByText('Connect'))

    await waitFor(() => expect(getByText(/needs you to sign in/)).toBeTruthy())
    fireEvent.click(getByRole('button', { name: 'Switch to OAuth' }))

    // Back on the form, with OAuth picked and its own one-line hint showing.
    const auth = within(getByRole('group', { name: 'Authentication' }))
    expect(auth.getByRole('button', { name: 'OAuth' }).getAttribute('aria-pressed')).toBe('true')
    expect(getByText(/Sign in on the vendor/)).toBeTruthy()
  })

  it('persists nothing when the probe ENDPOINT itself fails, not just the server', async () => {
    // A 503 from our own API is a different failure from "the MCP server refused", and it must
    // land in the same place: an inline error with Back/Retry, and nothing written.
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'probe unavailable' }),
    })

    const { getByText, getByLabelText } = openAddForm()
    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    fireEvent.click(getByText('Connect'))

    await waitFor(() => expect(getByText(/probe unavailable/)).toBeTruthy())
    expect(getByText('Retry')).toBeTruthy()
    expect(mockAuthFetch.mock.calls.map((c) => c[0])).toEqual(['/api/mcp-clients/probe'])
  })

  it('names, ids and permissions what it found on success — then saves once', async () => {
    mockAuthFetch.mockImplementation((path: string) => {
      if (path === '/api/mcp-clients/probe') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            server_info: { name: 'Firecrawl', version: '1.0.0' },
            suggested_name: 'Firecrawl',
            suggested_id: 'firecrawl',
            tools: [{ name: 'firecrawl_scrape' }, { name: 'firecrawl_crawl' }],
            error: null,
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({ id: 'firecrawl' }) })
    })

    const toast = vi.fn()
    const onAdded = vi.fn()
    const { getByText, getByLabelText, container } = openAddForm({ toast, onAdded })
    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    fireEvent.click(getByText('Connect'))

    await waitFor(() => expect(getByText(/Reachable — 2 tools found/)).toBeTruthy())

    // Step 3: Name pre-filled from serverInfo.name; Server ID collapsed behind `customize`.
    expect((container.querySelector('input.rd-input') as HTMLInputElement).value).toBe('Firecrawl')
    expect(getByText('firecrawl')).toBeTruthy()
    expect(getByText('customize')).toBeTruthy()

    // Step 4: every discovered tool sits at Needs approval.
    const asked = container.querySelectorAll('.cn-permbtn.on.warn')
    expect(asked.length).toBe(2)

    // Only now does anything persist.
    expect(mockAuthFetch.mock.calls.map((c) => c[0])).toEqual(['/api/mcp-clients/probe'])

    fireEvent.click(getByText('Add server'))
    // Wait on the toast, not on the POST: it fires after `createMcpServer` has fully settled,
    // so the flow is quiescent when this test ends rather than leaving writes in flight.
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Firecrawl added — 2 tools ready for Lucius'),
    )
    expect(onAdded).toHaveBeenCalled()
    expect(mockAuthFetch.mock.calls.map((c) => c[0])).toEqual([
      '/api/mcp-clients/probe',
      '/api/mcp-clients',
    ])
  })

  it('the header "Back" control from review steps back to the form, keeping what was typed', async () => {
    // 2026-08-22, owner-reported: the header control used to always fully exit the flow
    // (`leaveMcpFlow`), so backing out of review threw away a hand-typed URL/command and
    // dropped you at the empty search grid — "i don't want it to return to the search grid,
    // it must go back to the custom server one!". Now it steps back through the wizard: the
    // form step reappears with the SAME URL still in the field, not blank and not the grid.
    mockAuthFetch.mockImplementation((path: string) => {
      if (path === '/api/mcp-clients/probe') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            server_info: { name: 'Firecrawl', version: '1.0.0' },
            suggested_name: 'Firecrawl',
            suggested_id: 'firecrawl',
            tools: [{ name: 'firecrawl_scrape' }, { name: 'firecrawl_crawl' }],
            error: null,
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({ id: 'firecrawl' }) })
    })

    const { getByText, getByLabelText } = openAddForm()
    fireEvent.change(getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.example.com' },
    })
    fireEvent.click(getByText('Connect'))

    await waitFor(() => expect(getByText(/Reachable — 2 tools found/)).toBeTruthy())

    // Reserved for the form step, where stepping back really does mean leaving — not shown
    // while reachable within the wizard.
    expect(() => getByText('Back to directory')).toThrow()

    fireEvent.click(getByText('Back'))

    // Back on the form step, URL preserved — not blank, not the search grid.
    expect((getByLabelText(labelStartingWith('Server URL')) as HTMLInputElement).value).toBe(
      'https://mcp.example.com',
    )
    expect(getByText('Back to directory')).toBeTruthy() // the label reverts on the form step
  })
})

// ── OAuth sign-in — popup + poll (RFC `2026-08-14-mcp-oauth` §4.2/§4.3/§4.6) ──────────────────

describe('OAuth sign-in — the popup + poll lifecycle', () => {
  function openAddForm(props: Parameters<typeof open>[0] = {}) {
    const view = open(props)
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))
    return view
  }

  function selectOAuth(view: ReturnType<typeof open>) {
    fireEvent.change(view.getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.mercury.com/mcp' },
    })
    fireEvent.click(
      within(view.getByRole('group', { name: 'Authentication' })).getByRole('button', {
        name: 'OAuth',
      }),
    )
  }

  let popup: { location: { href: string }; closed: boolean; close: ReturnType<typeof vi.fn> }
  let windowOpen: ReturnType<typeof vi.fn>

  beforeEach(() => {
    popup = { location: { href: '' }, closed: false, close: vi.fn() }
    popup.close.mockImplementation(() => {
      popup.closed = true
    })
    windowOpen = vi.fn(() => popup)
    vi.stubGlobal('open', windowOpen)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('opens a popup synchronously and shows a waiting state', async () => {
    mockAuthFetch.mockImplementation((path: string) => {
      if (path === '/api/mcp-clients/oauth/begin') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({ status: 'pending', error: null }) })
    })
    const view = openAddForm()
    selectOAuth(view)
    fireEvent.click(view.getByText('Connect'))

    // A blank popup opens BEFORE the begin() round trip resolves — the popup-blocker-safe
    // sequence `oauthSignIn.ts` documents.
    expect(windowOpen).toHaveBeenCalledWith('about:blank', 'onw-mcp-oauth', expect.any(String))
    expect(view.getByText('Waiting for sign-in…')).toBeTruthy()
    await waitFor(() => expect(popup.location.href).toBe('https://as.example/authorize'))

    // The mocked poll answers `pending` forever by design (this test only cares about the
    // popup-open moment) — Cancel tears down its interval so it does not keep firing, against
    // whatever the NEXT test in this file reconfigures the mock to, after this one ends.
    fireEvent.click(view.getByText('Cancel sign-in'))
  })

  it('chains sign-in straight into the probe — one click reaches review, the modal never unmounts', async () => {
    const oauthSummary = {
      connected: true,
      issuer_host: 'mcp.mercury.com',
      scope: 'mcp:tools',
      expires_at: null,
      needs_reauth: false,
    }
    mockAuthFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/mcp-clients/oauth/begin') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        })
      }
      if (path === '/api/mcp-clients/oauth/f1') {
        return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', error: null }) })
      }
      if (path === '/api/mcp-clients') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: [
              {
                id: 'mercury',
                url: 'https://mcp.mercury.com/mcp',
                auth_type: 'oauth',
                oauth: oauthSummary,
              },
            ],
          }),
        })
      }
      if (path === '/api/mcp-clients/mercury/test' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 'mercury',
            name: 'Mercury',
            url: 'https://mcp.mercury.com/mcp',
            auth_type: 'oauth',
            last_status: 'ok',
            last_error: null,
            discovered_tools: [{ name: 'list_accounts' }],
            publisher: 'Mercury',
            verification: 'verified',
            homepage_url: null,
            repo_url: null,
            endpoint: 'https://mcp.mercury.com/mcp',
            oauth: oauthSummary,
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
    const view = openAddForm()
    selectOAuth(view)
    const modalRoot = view.container.querySelector('.rd-modal')
    expect((view.getByText('Connect') as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(view.getByText('Connect'))
    await waitFor(() => expect(view.getByText(/Reachable — 1 tool found/)).toBeTruthy())

    expect(popup.close).toHaveBeenCalled()
    // Same DOM node before and after the whole sequence — a remount would hand back a NEW
    // modal element, which is exactly what RFC §4.3 rules out ("destroy the half-completed
    // add flow").
    expect(view.container.querySelector('.rd-modal')).toBe(modalRoot)
  })

  it('renders the RFC §4.6 message for each distinct begin() failure reason, not one generic fallback', async () => {
    const cases: Array<[string, string]> = [
      [
        'not_oauth',
        "This server didn't ask for a sign-in. Try None — it may not need credentials.",
      ],
      [
        'no_metadata',
        "This server doesn't publish OAuth sign-in details, so we can't sign you in automatically. Use an API key if the vendor issues one.",
      ],
      [
        'insecure_redirect',
        'Sign-in needs a secure address. Reach this instance over HTTPS, or from the machine it runs on.',
      ],
    ]
    for (const [reason, error] of cases) {
      cleanup()
      mockAuthFetch.mockReset()
      mockAuthFetch.mockResolvedValue({
        ok: true,
        json: async () => ({ ok: false, reason, error }),
      })
      const view = openAddForm()
      selectOAuth(view)
      fireEvent.click(view.getByText('Connect'))
      await waitFor(() => expect(view.getByText(error)).toBeTruthy())
    }
  })

  it('shows the manual Client ID field only for no_registration, with its own affordance', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        reason: 'no_registration',
        error: "This server needs an app registration we can't create automatically.",
        needs_client_id: true,
      }),
    })
    const view = openAddForm()
    selectOAuth(view)
    fireEvent.click(view.getByText('Connect'))

    await waitFor(() => expect(view.getByLabelText('Client ID')).toBeTruthy())
  })

  it('shows the redirect URIs to register by hand only for dcr_rejected', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        reason: 'dcr_rejected',
        error: 'invalid_redirect_uri',
        redirect_uris: ['https://onw.example.test/api/mcp-oauth/callback'],
      }),
    })
    const view = openAddForm()
    selectOAuth(view)
    fireEvent.click(view.getByText('Connect'))

    await waitFor(() =>
      expect(view.getByText('https://onw.example.test/api/mcp-oauth/callback')).toBeTruthy(),
    )
    expect(view.queryByLabelText('Client ID')).toBeNull()
  })

  it('Cancel sign-in stops the wait, closes the popup, and leaves the rest of the form untouched', async () => {
    mockAuthFetch.mockImplementation((path: string) => {
      if (path === '/api/mcp-clients/oauth/begin') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({ status: 'pending', error: null }) })
    })
    const view = openAddForm()
    selectOAuth(view)
    fireEvent.click(view.getByText('Connect'))
    await waitFor(() => expect(view.getByText('Waiting for sign-in…')).toBeTruthy())

    fireEvent.click(view.getByText('Cancel sign-in'))

    // Back to a plain, clickable Connect — ready to retry, not stuck waiting forever — and
    // the popup is gone.
    expect((view.getByText('Connect') as HTMLButtonElement).disabled).toBe(false)
    expect(popup.close).toHaveBeenCalled()
    // Nothing else about the half-filled form was lost — same values, same modal.
    expect((view.getByLabelText(labelStartingWith('Server URL')) as HTMLInputElement).value).toBe(
      'https://mcp.mercury.com/mcp',
    )
    expect(
      within(view.getByRole('group', { name: 'Authentication' }))
        .getByRole('button', { name: 'OAuth' })
        .getAttribute('aria-pressed'),
    ).toBe('true')
  })
})

// ── Regression: abandoning the add flow after a completed sign-in (final review gate) ────────
//
// `oauth_flow.complete` (backend) persists a brand-new, dormant `McpServerConfig` row — with a
// REAL access/refresh token already in it — the instant the vendor's callback lands, entirely
// before "Add server" is ever clicked. Every other transport in this flow persists nothing until
// then (`probeMcpServer`'s own docstring: "persists NOTHING"). Before this fix, Discard/Cancel/
// Back-to-directory/closing the modal just hid the wizard — the live-credentialed row was left
// behind forever, and repeating the sign-in-then-abandon cycle piled up ghost servers
// (`mercury`, `mercury-2`, ...) visible in the Connectors list. Each test below drives a real
// completed sign-in (popup + poll, exactly as `oauth_flow.complete` would answer for a fresh
// add), then leaves the flow a different way and asserts the row's DELETE fired.
describe('abandoning the add flow after a completed sign-in deletes the dormant server row', () => {
  function openAddForm(props: Parameters<typeof open>[0] = {}) {
    const view = open(props)
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))
    return view
  }

  function selectOAuth(view: ReturnType<typeof open>) {
    fireEvent.change(view.getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.mercury.com/mcp' },
    })
    fireEvent.click(
      within(view.getByRole('group', { name: 'Authentication' })).getByRole('button', {
        name: 'OAuth',
      }),
    )
  }

  let popup: { location: { href: string }; closed: boolean; close: ReturnType<typeof vi.fn> }
  beforeEach(() => {
    popup = { location: { href: '' }, closed: false, close: vi.fn() }
    popup.close.mockImplementation(() => {
      popup.closed = true
    })
    vi.stubGlobal(
      'open',
      vi.fn(() => popup),
    )
  })
  afterEach(() => vi.unstubAllGlobals())

  const MERCURY_OAUTH_SUMMARY = {
    connected: true,
    issuer_host: 'mcp.mercury.com',
    scope: 'mcp:tools',
    expires_at: null,
    needs_reauth: false,
  }

  /** The callback's dormant row — exactly what `GET /api/mcp-clients` answers once
   *  `oauth_flow.complete` has minted it server-side for this url. */
  function mockSignedInMercury(extra: Record<string, unknown> = {}) {
    mockAuthFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/mcp-clients/oauth/begin') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        })
      }
      if (path === '/api/mcp-clients/oauth/f1') {
        return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', error: null }) })
      }
      if (path === '/api/mcp-clients') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: [
              {
                id: 'mercury',
                url: 'https://mcp.mercury.com/mcp',
                auth_type: 'oauth',
                oauth: MERCURY_OAUTH_SUMMARY,
              },
            ],
          }),
        })
      }
      if (path === '/api/mcp-clients/mercury/test' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 'mercury',
            name: 'Mercury',
            url: 'https://mcp.mercury.com/mcp',
            auth_type: 'oauth',
            last_status: 'ok',
            last_error: null,
            discovered_tools: [{ name: 'list_accounts' }],
            publisher: 'Mercury',
            verification: 'verified',
            homepage_url: null,
            repo_url: null,
            endpoint: 'https://mcp.mercury.com/mcp',
            oauth: MERCURY_OAUTH_SUMMARY,
          }),
        })
      }
      if (path in extra) return Promise.resolve(extra[path])
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
  }

  /** Every DELETE this test's mocked `authFetch` actually saw. */
  function deleteCalls(): string[] {
    return mockAuthFetch.mock.calls
      .filter((c: unknown[]) => (c[1] as RequestInit | undefined)?.method === 'DELETE')
      .map((c: unknown[]) => String(c[0]))
  }

  /** Connect now chains sign-in straight into the probe, so this always lands on review. */
  async function signIn(view: ReturnType<typeof open>) {
    selectOAuth(view)
    fireEvent.click(view.getByText('Connect'))
    await waitFor(() => expect(view.getByText(/Reachable — 1 tool found/)).toBeTruthy())
  }

  it('Discard (after Connect lands on review) deletes the row', async () => {
    mockSignedInMercury({
      '/api/mcp-clients/mercury': { ok: true, json: async () => ({ deleted: 'mercury' }) },
    })
    const view = openAddForm()
    await signIn(view)

    fireEvent.click(view.getByText('Discard'))

    await waitFor(() => expect(deleteCalls()).toEqual(['/api/mcp-clients/mercury']))
  })

  it('the header "Back" control (stepping back to the form, not exiting) deletes the row too', async () => {
    // 2026-08-22: the header control now steps back to the form step (preserving what was
    // typed) rather than always exiting the flow — but a dormant OAuth row from THIS
    // attempt must still be discarded, or retrying sign-in after tweaking the URL would
    // orphan the first row's live credential forever. Label is "Back" here, not "Back to
    // directory" — that wording is reserved for the form step, where stepping back really
    // does mean leaving.
    mockSignedInMercury({
      '/api/mcp-clients/mercury': { ok: true, json: async () => ({ deleted: 'mercury' }) },
    })
    const view = openAddForm()
    await signIn(view)

    fireEvent.click(view.getByText('Back'))

    await waitFor(() => expect(deleteCalls()).toEqual(['/api/mcp-clients/mercury']))
  })

  it('closing the whole modal (the header ✕) deletes the row too', async () => {
    mockSignedInMercury({
      '/api/mcp-clients/mercury': { ok: true, json: async () => ({ deleted: 'mercury' }) },
    })
    const view = openAddForm()
    await signIn(view)

    fireEvent.click(view.getByLabelText('Close'))

    await waitFor(() => expect(deleteCalls()).toEqual(['/api/mcp-clients/mercury']))
  })

  it('does NOT delete a row that "Add server" actually claimed', async () => {
    mockSignedInMercury()
    const view = openAddForm()
    await signIn(view)

    fireEvent.click(view.getByText('Add server'))
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/mcp-clients/mercury', expect.anything()),
    )

    expect(deleteCalls()).toEqual([])
  })
})

describe("the review step renders the PROBE's own provenance (spec §5)", () => {
  function probeReturning(trust: unknown, tools: { name: string }[] = [{ name: 'do_thing' }]) {
    mockAuthFetch.mockImplementation((path: string) =>
      Promise.resolve(
        path === '/api/mcp-clients/probe'
          ? {
              ok: true,
              json: async () => ({
                ok: true,
                server_info: { name: 'Srv', version: '1' },
                suggested_name: 'Srv',
                suggested_id: 'srv',
                tools,
                trust,
                error: null,
              }),
            }
          : { ok: true, json: async () => ({}) },
      ),
    )
  }

  async function reviewCustomServer() {
    const view = open()
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))
    fireEvent.change(view.getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://mcp.x.test' },
    })
    fireEvent.click(view.getByText('Connect'))
    await waitFor(() => expect(view.getByText(/Reachable — 1 tool found/)).toBeTruthy())
    return view
  }

  it('shows the server’s self-reported publisher, and the endpoint the backend rendered', async () => {
    probeReturning({
      publisher: 'Acme Corp',
      verification: 'unknown',
      homepage_url: null,
      repo_url: null,
      endpoint: 'https://mcp.x.test/v1',
    })
    const { getByText } = await reviewCustomServer()

    expect(getByText('Publisher')).toBeTruthy()
    expect(getByText('Acme Corp')).toBeTruthy()
    // The endpoint comes from the probe, not from re-deriving the form — the two can differ
    // (a normalised path here), and what you are trusting is what we actually connected to.
    expect(getByText('https://mcp.x.test/v1')).toBeTruthy()
  })

  it('claims NO author when the server volunteers none — never an "Unknown publisher" name', async () => {
    probeReturning({
      publisher: null,
      verification: 'unknown',
      homepage_url: null,
      repo_url: null,
      endpoint: 'https://mcp.x.test',
    })
    const { getByText, container } = await reviewCustomServer()

    expect(getByText('Unknown publisher')).toBeTruthy() // the BADGE says it
    expect(getByText(/can’t vouch for its code/)).toBeTruthy()
    // ...and the name slot stays empty. The prototype passed the literal string "Unknown
    // publisher" as the publisher NAME, which made the panel print the known-publisher note.
    expect(container.textContent).not.toContain('third-party MCP server')
  })

  it('does not let a probe DOWNGRADE a curated catalog entry to unknown', async () => {
    // The probe's `verification` is structurally always `unknown` (it describes an endpoint the
    // user typed). Adopting it here would make Playwright MCP read "Verified" on its card and
    // "Unknown publisher" one screen later — the split-brain the endpoint join exists to stop.
    probeReturning({
      publisher: null,
      verification: 'unknown',
      homepage_url: null,
      repo_url: null,
      endpoint: 'npx @playwright/mcp',
    })
    const view = open()
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Playwright MCP'))
    fireEvent.click(view.getByText('Add'))
    fireEvent.click(view.getByText('Connect'))
    await waitFor(() => expect(view.getByText(/Reachable — 1 tool found/)).toBeTruthy())

    expect(view.getByText('Verified publisher')).toBeTruthy()
    expect(view.queryByText('Unknown publisher')).toBeNull()
  })

  it('still renders a panel when a backend sends no trust block at all', async () => {
    probeReturning(undefined)
    const { getByText } = await reviewCustomServer()
    expect(getByText('Publisher')).toBeTruthy()
    expect(getByText('Unknown publisher')).toBeTruthy()
  })
})

describe('paste is not a separate mode', () => {
  it('fills the very same fields typing would, and shares the same Test connect', () => {
    const view = open()
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))

    fireEvent.click(view.getByText('Paste from a config snippet'))
    fireEvent.change(view.getByLabelText('Config snippet'), {
      target: {
        value: JSON.stringify({
          mcpServers: { fc: { command: 'npx', args: ['-y', 'firecrawl-mcp'] } },
        }),
      },
    })

    expect(view.getByText('Parsed — fields below are filled in.')).toBeTruthy()
    // Transport switched to local, and the command/args fields — the TYPED ones — are filled.
    expect((view.getByLabelText(labelStartingWith('Command')) as HTMLInputElement).value).toBe(
      'npx',
    )
    expect((view.getByLabelText('Arguments') as HTMLInputElement).value).toBe('-y firecrawl-mcp')
    expect((view.getByText('Connect') as HTMLButtonElement).disabled).toBe(false)
  })

  it('says so plainly when a snippet will not parse, without clearing the box', () => {
    const view = open()
    fireEvent.click(view.getByText('MCP'))
    fireEvent.click(card(view.container, 'Custom MCP server'))
    fireEvent.click(view.getByText('Paste from a config snippet'))
    fireEvent.change(view.getByLabelText('Config snippet'), { target: { value: '{ oops' } })

    expect(view.getByText(/Couldn't parse that yet/)).toBeTruthy()
    expect((view.getByLabelText('Config snippet') as HTMLTextAreaElement).value).toBe('{ oops')
  })
})

describe('an installed server merged into its catalog card', () => {
  const IBKR_SERVER: Connector = {
    id: 'mcp:api-ibkr-com',
    kind: 'mcp',
    name: 'Interactive Brokers',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { transport: 'http', url: 'https://api.ibkr.com/v1/api/mcp-public' },
    secrets_public: {},
    tools: [],
  }

  it("opens ON that card from the server's View details, not on the plain browse", () => {
    // The server has no directory row of its own any more — it is the `mcp-ibkr` card — so a
    // lookup by its id alone fell through to the grid, and View details reopened Browse & add.
    const { container } = open({ connectors: [IBKR_SERVER], previewId: 'mcp:api-ibkr-com' })
    expect(container.querySelector('.cn-dirgrid')).toBeNull()
    expect(container.textContent).toContain('Interactive Brokers')
  })
})

describe('on a phone the preview’s action lives in the footer, not the header', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows ONE full-width Add in the footer, no header action and no footer Back', () => {
    const onFocusConnector = vi.fn()
    const { container, getAllByText } = open({ onFocusConnector })
    fireEvent.click(card(container, 'Telegram'))

    expect(container.querySelector('.cn-idhead-actions')).toBeNull()
    const foot = container.querySelector('.rd-modal-foot') as HTMLElement
    expect(within(foot).queryByText('Back')).toBeNull()
    const add = within(foot).getByText('Add connector')
    expect(add.className).toContain('cn-foot-action')
    expect(getAllByText('Add connector')).toHaveLength(1)

    fireEvent.click(add)
    expect(onFocusConnector).toHaveBeenCalledWith('telegram', { configure: true })
  })

  it('shows Open settings there for an installed connector', () => {
    const { container } = open({
      connectors: [connector({ id: 'telegram', name: 'Telegram', status: 'active' })],
    })
    fireEvent.click(card(container, 'Telegram'))
    const foot = container.querySelector('.rd-modal-foot') as HTMLElement
    expect(within(foot).getByText('Open settings')).toBeTruthy()
  })
})
