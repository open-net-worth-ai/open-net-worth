// @vitest-environment jsdom
//
// CDS-UI-4..8 (docs/qa/acceptance/connectors-data-sources.md): the per-provider "Accounts"
// section — every status branch's copy, link (append-only PUT), unlink (removes exactly one
// entry), and create-account-then-link.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ConnectorAccountsList } from './ConnectorAccountsList'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  mockAuthFetch.mockReset()
})

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const appAccounts = [
  {
    id: 'acc_1',
    name: 'Existing Checking',
    kind: 'asset',
    type: 'cash',
    institution: 'Chase',
    currency: 'USD',
    balance: 0,
    balance_base: 0,
    cleared_balance: 0,
    cleared_balance_base: 0,
    external_ids: [],
  },
]

function mockRoutes({
  accountsResponse,
  onPut,
  onPost,
}: {
  accountsResponse: unknown
  onPut?: (url: string, body: unknown) => void
  onPost?: (body: unknown) => void
}) {
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/connectors/plaid/accounts') return Promise.resolve(okJson(accountsResponse))
    if (url === '/api/accounts' && !init) return Promise.resolve(okJson(appAccounts))
    if (init?.method === 'PUT') {
      onPut?.(url, JSON.parse(init.body as string))
      return Promise.resolve(okJson({}))
    }
    if (init?.method === 'POST' && url === '/api/accounts') {
      onPost?.(JSON.parse(init.body as string))
      return Promise.resolve(
        okJson({
          id: 'acc_new',
          name: 'New Savings',
          kind: 'asset',
          type: 'savings',
          currency: 'USD',
          balance: 0,
          balance_base: 0,
          cleared_balance: 0,
          cleared_balance_base: 0,
          external_ids: [],
        }),
      )
    }
    return Promise.resolve(okJson({}))
  })
}

describe('ConnectorAccountsList — status branches (CDS-UI-8)', () => {
  it('CDS-UI-4: status ok with 2 accounts renders 2 rows, each with a link control', async () => {
    mockRoutes({
      accountsResponse: {
        status: 'ok',
        accounts: [
          {
            provider_account_id: 'p1',
            name: 'Checking',
            mask: '1234',
            currency: 'USD',
            balance: null,
            linked_account_id: null,
          },
          {
            provider_account_id: 'p2',
            name: 'Savings',
            mask: '5678',
            currency: 'USD',
            balance: null,
            linked_account_id: 'acc_1',
          },
        ],
      },
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)

    expect(await screen.findByRole('button', { name: 'Add' })).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Link Checking to an existing account' }),
    ).toBeTruthy()
    expect(screen.getByText('Linked to “Existing Checking”')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Unlink' })).toBeTruthy()
  })

  it('ok + empty accounts: "No accounts yet"', async () => {
    mockRoutes({ accountsResponse: { status: 'ok', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText('No accounts yet')).toBeTruthy()
  })

  it('not_configured: no Retry button', async () => {
    mockRoutes({ accountsResponse: { status: 'not_configured', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText('Not set up yet')).toBeTruthy()
    expect(screen.getByText(/Add your Plaid credentials above/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('disabled: no Retry button', async () => {
    mockRoutes({ accountsResponse: { status: 'disabled', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText('Paused')).toBeTruthy()
    expect(screen.getByText(/Plaid is paused/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('auth_error: error copy + Retry', async () => {
    mockRoutes({ accountsResponse: { status: 'auth_error', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText(/rejected the saved credentials/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('rate_limited: error copy + Retry', async () => {
    mockRoutes({ accountsResponse: { status: 'rate_limited', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText(/rate-limiting requests/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('connect_error: error copy + Retry', async () => {
    mockRoutes({ accountsResponse: { status: 'connect_error', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText("Couldn't reach Plaid.")).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('connect_error with a previously-linked account: error copy AND the linked row both show — a live-fetch failure must not hide a link the user already made', async () => {
    mockRoutes({
      accountsResponse: {
        status: 'connect_error',
        accounts: [
          {
            provider_account_id: 'p1',
            name: 'Checking',
            mask: '1234',
            currency: null,
            balance: null,
            linked_account_id: 'acc_1',
          },
        ],
      },
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText("Couldn't reach Plaid.")).toBeTruthy()
    expect(screen.getByText('Linked to “Existing Checking”')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Unlink' })).toBeTruthy()
  })

  it('not_configured with a previously-linked account: empty-state copy AND the linked row both show', async () => {
    mockRoutes({
      accountsResponse: {
        status: 'not_configured',
        accounts: [
          {
            provider_account_id: 'p1',
            name: 'Checking',
            mask: '1234',
            currency: null,
            balance: null,
            linked_account_id: 'acc_1',
          },
        ],
      },
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText('Not set up yet')).toBeTruthy()
    expect(screen.getByText('Linked to “Existing Checking”')).toBeTruthy()
  })

  it('error (or unmapped): error copy + Retry, not a raw status string', async () => {
    mockRoutes({ accountsResponse: { status: 'something_unmapped', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    expect(await screen.findByText("Couldn't load accounts for Plaid.")).toBeTruthy()
    expect(screen.queryByText('something_unmapped')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('Retry re-fires the GET', async () => {
    mockRoutes({ accountsResponse: { status: 'connect_error', accounts: [] } })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    await screen.findByRole('button', { name: 'Retry' })
    const before = mockAuthFetch.mock.calls.filter(
      (c) => c[0] === '/api/connectors/plaid/accounts',
    ).length
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() =>
      expect(
        mockAuthFetch.mock.calls.filter((c) => c[0] === '/api/connectors/plaid/accounts').length,
      ).toBe(before + 1),
    )
  })
})

describe('ConnectorAccountsList — link (CDS-UI-5)', () => {
  it('choosing an existing account PUTs external_ids appended, not replaced', async () => {
    let putUrl = '',
      putBody: unknown
    mockRoutes({
      accountsResponse: {
        status: 'ok',
        accounts: [
          {
            provider_account_id: 'p1',
            name: 'Checking',
            mask: '1234',
            currency: 'USD',
            balance: null,
            linked_account_id: null,
          },
        ],
      },
      onPut: (url, body) => {
        putUrl = url
        putBody = body
      },
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Link Checking to an existing account' }),
    )
    // The ledger's own searchable account picker opens straight away.
    const picker = await screen.findByRole('group', { name: 'Link Checking to an account' })
    expect(within(picker).getByPlaceholderText('Search accounts…')).toBeTruthy()
    fireEvent.mouseDown(within(picker).getByRole('button', { name: 'Existing Checking' }))

    await waitFor(() => expect(putUrl).toBe('/api/accounts/acc_1'))
    expect(putBody).toEqual({
      external_ids: [{ source: 'plaid', ref: 'p1', name: 'Checking', mask: '1234' }],
    })
  })
})

describe('ConnectorAccountsList — unlink (CDS-UI-7)', () => {
  it('Unlink PUTs external_ids with that one {source, ref} removed, no others', async () => {
    let putUrl = '',
      putBody: unknown
    const linkedAppAccount = {
      ...appAccounts[0],
      external_ids: [
        { source: 'plaid', ref: 'p1', name: 'Checking', mask: '1234', linked_at: 't' },
        { source: 'ynab', ref: 'y1', name: 'Other', mask: '9999', linked_at: 't' },
      ],
    }
    mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/connectors/plaid/accounts') {
        return Promise.resolve(
          okJson({
            status: 'ok',
            accounts: [
              {
                provider_account_id: 'p1',
                name: 'Checking',
                mask: '1234',
                currency: 'USD',
                balance: null,
                linked_account_id: 'acc_1',
              },
            ],
          }),
        )
      }
      if (url === '/api/accounts' && !init) return Promise.resolve(okJson([linkedAppAccount]))
      if (init?.method === 'PUT') {
        putUrl = url
        putBody = JSON.parse(init.body as string)
        return Promise.resolve(okJson({}))
      }
      return Promise.resolve(okJson({}))
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink' }))

    await waitFor(() => expect(putUrl).toBe('/api/accounts/acc_1'))
    expect(putBody).toEqual({
      external_ids: [{ source: 'ynab', ref: 'y1', name: 'Other', mask: '9999', linked_at: 't' }],
    })
  })
})

describe('ConnectorAccountsList — create account (CDS-UI-6)', () => {
  it('"Add" opens AccountModal prefilled, then POSTs and links', async () => {
    let postBody: unknown,
      putUrl = '',
      putBody: unknown
    mockRoutes({
      accountsResponse: {
        status: 'ok',
        accounts: [
          {
            provider_account_id: 'p1',
            name: 'New Savings',
            mask: '4242',
            currency: 'USD',
            balance: null,
            linked_account_id: null,
          },
        ],
      },
      onPost: (body) => {
        postBody = body
      },
      onPut: (url, body) => {
        putUrl = url
        putBody = body
      },
    })
    render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }))

    expect(await screen.findByRole('dialog')).toBeTruthy()
    expect(screen.getByDisplayValue('New Savings')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    await waitFor(() => expect(putUrl).toBe('/api/accounts/acc_new'))
    expect(postBody).toMatchObject({
      name: 'New Savings',
      currency: 'USD',
      institution: 'Plaid',
      last_four: '4242',
    })
    expect(putBody).toEqual({
      external_ids: [{ source: 'plaid', ref: 'p1', name: 'New Savings', mask: '4242' }],
    })
  })
})
