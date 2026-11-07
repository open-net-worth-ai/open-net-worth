// @vitest-environment jsdom
//
// The review cards and the transaction drawer report through what is on screen, never a toast
// bar (owner, 2026-09-28: "I don't think we need to show that toast bar — remove it"). The bar
// sat under both surfaces anyway (z-index 80), so its messages were rarely seen. A success says
// nothing extra — the drawer and the cards already show the result. A failure the bar used to
// carry is said IN the drawer, as a readable sentence (the server's `detail`, not its JSON
// envelope): a refused approve or delete, a category that could not be created, a row that
// could not be loaded. A failed delete leaves the drawer open, since nothing was deleted.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { TransactionSurfacesProvider } from './TransactionSurfaces'
import { useTransactionSurfaces } from './transactionSurfacesContext'
import { fromApiTxn } from './data'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount, ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body, text: async () => JSON.stringify(body) } as Response
}
function refused(status: number, detail: string): Response {
  const body = JSON.stringify({ detail })
  return {
    ok: false,
    status,
    json: async () => JSON.parse(body),
    text: async () => body,
  } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 1000,
  balance_base: 1000,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
}

const TXN: ApiTxn = {
  id: 'txn-1',
  date: '2026-09-03',
  kind: 'expense',
  category: null,
  description: 'Corner Grocer',
  memo: '',
  payee_id: null,
  base_amount: 63.8,
  base_currency: 'USD',
  cleared: false,
  needs_review: true,
  from_account_id: 'acc-1',
  from_amount: 63.8,
  from_currency: 'USD',
}

type Route = (url: string, opts?: RequestInit) => Response | undefined

function setupMocks(route: Route = () => undefined) {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    const hit = route(url, opts)
    if (hit) return Promise.resolve(hit)
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.endsWith('/activity')) {
      return Promise.resolve(
        okJson({ addedBy: 'You', createdAt: TXN.date, updatedAt: TXN.date, activity: [] }),
      )
    }
    if (url.endsWith('/receipts')) return Promise.resolve(okJson({ receipts: [] }))
    if (url === `/api/transactions/${TXN.id}` && opts?.method === 'PUT') {
      return Promise.resolve(okJson({ ...TXN, ...JSON.parse(String(opts.body)) }))
    }
    if (url === `/api/transactions/${TXN.id}` && opts?.method === 'DELETE') {
      return Promise.resolve(okJson({ ok: true }))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Open({ byId }: { byId?: string }) {
  const { openTransaction, openTransactionById } = useTransactionSurfaces()
  useEffect(() => {
    if (byId) openTransactionById(byId)
    else openTransaction(fromApiTxn(TXN))
  }, [byId, openTransaction, openTransactionById])
  return null
}

async function openDrawer(byId?: string) {
  render(
    <TransactionSurfacesProvider onManagePayees={() => {}}>
      <Open byId={byId} />
    </TransactionSurfacesProvider>,
  )
  await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).not.toBeNull())
}

const drawer = () => document.querySelector('.acct-detail-wrap') as HTMLElement | null
const toast = () => document.querySelector('.acct-toast')
const sent = (method: string) =>
  mockAuthFetch.mock.calls.some(
    ([u, o]) =>
      u === `/api/transactions/${TXN.id}` && (o as RequestInit | undefined)?.method === method,
  )

describe('transaction surfaces — no toast bar', () => {
  it('approving from the drawer says nothing extra: the review block just goes', async () => {
    setupMocks()
    await openDrawer()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.queryByText('Needs your review')).toBeNull())
    expect(toast()).toBeNull()
  })

  it('a delete that lands closes the drawer, with no toast after it', async () => {
    setupMocks()
    await openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete transaction' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(drawer()).toBeNull())
    expect(sent('DELETE')).toBe(true)
    expect(toast()).toBeNull()
  })

  it('the confirm dialog says what deleting a linked row will change', async () => {
    setupMocks((url) =>
      url === '/api/transactions/delete-preview'
        ? okJson({
            links: [{ label: '10 shares bought 2026-01-05', effect: 'The lot keeps its shares.' }],
          })
        : undefined,
    )
    await openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete transaction' })
    await within(confirm).findByText(/10 shares bought 2026-01-05: The lot keeps its shares\./)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE')).toBe(true))
  })

  it('a failed preview still lets you delete, with the plain dialog', async () => {
    setupMocks((url) =>
      url === '/api/transactions/delete-preview' ? refused(500, 'nope') : undefined,
    )
    await openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete transaction' })
    expect(within(confirm).getByText(/will be removed\.$/)).toBeTruthy()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(sent('DELETE')).toBe(true))
  })
})

describe('transaction surfaces — a failure is said in the drawer', () => {
  it('a refused approve, as the server’s sentence', async () => {
    setupMocks((url, opts) =>
      url === `/api/transactions/${TXN.id}` && opts?.method === 'PUT'
        ? refused(409, 'This transaction is on a reconciled statement.')
        : undefined,
    )
    await openDrawer()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    const alert = await within(drawer()!).findByRole('alert')
    expect(alert.textContent).toBe('This transaction is on a reconciled statement.')
    expect(toast()).toBeNull()
  })

  it('a refused delete leaves the drawer open and says why', async () => {
    setupMocks((url, opts) =>
      url === `/api/transactions/${TXN.id}` && opts?.method === 'DELETE'
        ? refused(409, 'A starting balance cannot be deleted.')
        : undefined,
    )
    await openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete transaction' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    const alert = await within(drawer()!).findByRole('alert')
    expect(alert.textContent).toBe('A starting balance cannot be deleted.')
    expect(toast()).toBeNull()
  })

  it('a category that could not be created, in the edit it was made for', async () => {
    setupMocks((url, opts) =>
      url === '/api/categories' && opts?.method === 'POST'
        ? refused(409, "A category named 'Groceries' already exists")
        : undefined,
    )
    await openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Edit transaction' }))
    fireEvent.click(screen.getByRole('button', { name: /Category…/ }))
    fireEvent.change(screen.getByPlaceholderText('Search categories…'), {
      target: { value: 'Groceries' },
    })
    fireEvent.mouseDown(screen.getByRole('button', { name: /New category/ }))
    const modal = await screen.findByRole('dialog', { name: 'Add category' })
    fireEvent.click(within(modal).getByRole('button', { name: 'Save' }))
    const alert = await within(drawer()!).findByRole('alert')
    expect(alert.textContent).toBe("A category named 'Groceries' already exists")
    expect(document.querySelector('.acct-detail-kicker')?.textContent).toBe('Edit transaction')
    expect(toast()).toBeNull()
  })

  it('a row that could not be loaded keeps the drawer open and says so', async () => {
    setupMocks((url) =>
      url === '/api/transactions/txn-gone' ? refused(404, 'Not found') : undefined,
    )
    await openDrawer('txn-gone')
    const alert = await within(drawer()!).findByRole('alert')
    expect(alert.textContent).toBe('This transaction could not be loaded.')
    expect(toast()).toBeNull()
  })
})
