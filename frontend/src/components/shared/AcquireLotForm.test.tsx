// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AcquireLotForm } from './AcquireLotForm'

const mockAuthFetch = vi.fn()
const mockNotifyPortfolioChanged = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))
vi.mock('../../hooks/serverState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/serverState')>()
  return {
    ...actual,
    notifyPortfolioChanged: () => mockNotifyPortfolioChanged(),
  }
})

const ACCOUNTS = [
  { id: 'acc-1', name: 'IBKR', kind: 'asset', type: 'investment' },
  { id: 'acc-2', name: 'Vanguard', kind: 'asset', type: 'investment' },
]

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockNotifyPortfolioChanged.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
      return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
  })
})
afterEach(cleanup)

describe('AcquireLotForm', () => {
  it('shows symbol and account on add, with a new-account option', async () => {
    const { findByLabelText, findByText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    expect(getByRole('dialog').textContent).toContain('Add lot')
    const symbol = (await findByLabelText(/Symbol/)) as HTMLInputElement
    expect(symbol.value).toBe('AAPL')
    expect(symbol.readOnly).toBe(true)
    expect(await findByText('Account')).toBeTruthy()
    expect(await findByText('+ New account…')).toBeTruthy()
  })

  it('reveals an inline name field when creating a new account', async () => {
    const { findByLabelText, findByPlaceholderText } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    fireEvent.change(account, { target: { value: '__new__' } })
    expect(await findByPlaceholderText('Account name (e.g. IBKR)')).toBeTruthy()
  })

  it('keeps symbol editable when used as Add holding', async () => {
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm onDone={() => {}} onClose={() => {}} />,
    )
    expect(getByRole('dialog').textContent).toContain('Add holding')
    const symbol = (await findByLabelText(/Symbol/)) as HTMLInputElement
    expect(symbol.readOnly).toBe(false)
    fireEvent.change(symbol, { target: { value: 'msft' } })
    expect(symbol.value).toBe('MSFT')
  })

  it('does not show the book base on Unit cost until the ticker quote currency is known', async () => {
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm onDone={() => {}} onClose={() => {}} />,
    )
    const text = getByRole('dialog').textContent || ''
    expect(text).not.toMatch(/Unit cost\s*\(SGD\)/)
    expect(text).not.toMatch(/Total spent\s*\(SGD\)/)
    expect(text).not.toMatch(/Fees\s*\(SGD\)/)
    expect(await findByLabelText(/^Unit cost$/)).toBeTruthy()
    expect(await findByLabelText(/^Total spent$/)).toBeTruthy()
    expect(await findByLabelText(/^Fees$/)).toBeTruthy()
  })

  it('labels Unit cost with the ticker quote currency after resolve, and POSTs that (not SGD)', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/resolve' && init?.method === 'POST')
        return Promise.resolve({
          ok: true,
          json: async () => ({
            resolved: true,
            instrument: { id: 'inst-msft', symbol: 'MSFT', quote_currency: 'USD' },
          }),
        })
      if (url === '/api/instruments/inst-msft/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm onDone={() => {}} onClose={() => {}} />,
    )
    const symbol = (await findByLabelText(/Symbol/)) as HTMLInputElement
    fireEvent.change(symbol, { target: { value: 'MSFT' } })
    fireEvent.blur(symbol)
    await waitFor(() => expect(findByLabelText(/^Unit cost \(USD\)$/)).resolves.toBeTruthy())
    expect(await findByLabelText(/^Total spent \(USD\)$/)).toBeTruthy()
    expect(await findByLabelText(/^Fees \(USD\)$/)).toBeTruthy()

    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '2' } })
    fireEvent.change(await findByLabelText(/Unit cost/), { target: { value: '100' } })
    fireEvent.click(getByRole('button', { name: 'Add holding' }))
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-msft/lots' && c[1]?.method === 'POST',
      )
      expect(call).toBeTruthy()
      const body = JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>
      expect(body.cost_currency).toBe('USD')
      expect(body.cost_currency).not.toBe('SGD')
    })
  })

  it('does not auto-pick an account when several exist', async () => {
    const { findByLabelText } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    await waitFor(async () => {
      const account = (await findByLabelText(/Account/)) as HTMLSelectElement
      expect(account.value).toBe('')
    })
  })

  it('posts the lot to the instrument with the chosen account', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'acc-new' }) })
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/inst-1/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const onDone = vi.fn()
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={onDone}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '10' } })
    fireEvent.click(getByRole('button', { name: 'Add lot' }))
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-1/lots' && c[1]?.method === 'POST',
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        account_id: 'acc-1',
        quantity: 10,
      })
    })
    expect(onDone).toHaveBeenCalled()
    expect(mockNotifyPortfolioChanged).toHaveBeenCalled()
  })

  it('creates a new account then posts the lot against it', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'acc-new' }) })
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/inst-1/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    fireEvent.change(account, { target: { value: '__new__' } })
    fireEvent.change(await findByLabelText(/New account name/), { target: { value: 'Schwab' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '4' } })
    fireEvent.click(getByRole('button', { name: 'Add lot' }))
    await waitFor(() => {
      const create = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/accounts' && c[1]?.method === 'POST',
      )
      expect(create).toBeTruthy()
      expect(JSON.parse(String(create?.[1]?.body))).toMatchObject({
        name: 'Schwab',
        type: 'investment',
        initial_balance: 0,
      })
      const lot = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-1/lots' && c[1]?.method === 'POST',
      )
      expect(JSON.parse(String(lot?.[1]?.body))).toMatchObject({
        account_id: 'acc-new',
        quantity: 4,
      })
    })
  })

  it('derives unit_cost from Quantity + Total spent (gross incl. fees)', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/inst-1/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '10' } })
    fireEvent.change(await findByLabelText(/Fees/), { target: { value: '5' } })
    fireEvent.change(await findByLabelText(/Total spent/), { target: { value: '105' } })
    const unit = (await findByLabelText(/Unit cost/)) as HTMLInputElement
    expect(unit.value).toBe('10')
    fireEvent.click(getByRole('button', { name: 'Add lot' }))
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-1/lots' && c[1]?.method === 'POST',
      )
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        quantity: 10,
        unit_cost: 10,
        fees: 5,
      })
    })
  })

  it('derives quantity from Unit cost + Total spent', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/inst-1/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Unit cost/), { target: { value: '50' } })
    fireEvent.change(await findByLabelText(/Total spent/), { target: { value: '500' } })
    const qty = (await findByLabelText(/Quantity/)) as HTMLInputElement
    expect(qty.value).toBe('10')
    fireEvent.click(getByRole('button', { name: 'Add lot' }))
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-1/lots' && c[1]?.method === 'POST',
      )
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        quantity: 10,
        unit_cost: 50,
      })
    })
  })

  it('posts null unit_cost when both Unit cost and Total spent are blank', async () => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/instruments/inst-1/lots' && init?.method === 'POST')
        return Promise.resolve({ ok: true, json: async () => ({ id: 'lot-new' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '3' } })
    fireEvent.click(getByRole('button', { name: 'Add lot' }))
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/instruments/inst-1/lots' && c[1]?.method === 'POST',
      )
      const body = JSON.parse(String(call?.[1]?.body))
      expect(body.quantity).toBe(3)
      expect(body.unit_cost).toBeNull()
    })
  })
})

describe('AcquireLotForm — a lot whose purchase date is unknown', () => {
  const undated = {
    id: 'lot-open',
    position_id: 'pos-1',
    instrument_id: 'inst-1',
    account_id: 'acc-1',
    acquired_at: '',
    original_qty: 27,
    remaining_qty: 27,
    unit_cost: 131.98,
    cost_currency: 'USD',
    fees: 0,
    kind: 'opening',
    note: '',
    effective_unit_cost: 131.98,
    remaining_basis: 3563.46,
    term_to_date: 'unknown' as const,
  }

  function patchBody(): Record<string, unknown> | undefined {
    const call = mockAuthFetch.mock.calls.find(
      ([url, init]) => url === '/api/lots/lot-open' && init?.method === 'PATCH',
    )
    return call ? JSON.parse(call[1].body as string) : undefined
  }

  beforeEach(() => {
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({ ok: true, json: async () => ACCOUNTS })
      if (url === '/api/lots/lot-open' && init?.method === 'PATCH')
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
  })

  it('edits as an empty date — never prefilled with today', async () => {
    const { findByLabelText, findByText } = render(
      <AcquireLotForm
        symbol="PLTR"
        costCcy="USD"
        lot={undated}
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const date = (await findByLabelText(/Acquired date/)) as HTMLInputElement
    expect(date.value).toBe('')
    expect(await findByText(/Unknown — set it if you know/)).toBeTruthy()
  })

  it('saving a note leaves the date unknown (no acquired_at sent)', async () => {
    const { findByLabelText, getByRole } = render(
      <AcquireLotForm
        symbol="PLTR"
        costCcy="USD"
        lot={undated}
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    fireEvent.change(await findByLabelText(/Note/), { target: { value: 'from a statement' } })
    fireEvent.click(getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patchBody()).toBeTruthy())
    expect(patchBody()).not.toHaveProperty('acquired_at')
    expect(patchBody()?.note).toBe('from a statement')
  })
})

describe('AcquireLotForm — a lot with a purchase transaction (UAT DS-LOT-NODEDUCT)', () => {
  const paid = {
    id: 'lot-paid',
    position_id: 'pos-1',
    instrument_id: 'inst-1',
    account_id: 'acc-1',
    acquired_at: '2026-02-06',
    original_qty: 2,
    remaining_qty: 2,
    unit_cost: 50,
    cost_currency: 'SGD',
    fees: 0,
    kind: 'buy',
    note: '',
    txn_id: 'txn-paid',
    effective_unit_cost: 50,
    remaining_basis: 100,
    term_to_date: 'short' as const,
  }

  beforeEach(() => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({
          ok: true,
          json: async () => [
            ...ACCOUNTS,
            { id: 'cash-1', name: 'Everyday Cash', kind: 'asset', type: 'cash' },
          ],
        })
      if (url === '/api/transactions/txn-paid')
        return Promise.resolve({ ok: true, json: async () => ({ from_account_id: 'cash-1' }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
  })

  it('warns that choosing no account removes the purchase and what it paid', async () => {
    const { findByLabelText, queryByText, findByText } = render(
      <AcquireLotForm
        symbol="ACME"
        costCcy="SGD"
        lot={paid}
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const funding = (await findByLabelText(/Deduct from/)) as HTMLSelectElement
    await waitFor(() => expect(funding.value).toBe('cash-1'))
    expect(queryByText(/removes this lot's purchase transaction/)).toBeNull()

    fireEvent.change(funding, { target: { value: '' } })

    expect(await findByText(/removes this lot's purchase transaction/)).toBeTruthy()
    expect(queryByText(/won't change when you add this lot/)).toBeNull()
  })

  it('says removing the lot removes its purchase and frees what it paid (UAT DS-LOT-REMOVE)', async () => {
    const { findByRole, findByText } = render(
      <AcquireLotForm
        symbol="ACME"
        costCcy="SGD"
        lot={paid}
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    fireEvent.click(await findByRole('button', { name: 'Remove lot' }))
    expect(await findByText(/Its purchase transaction is removed too/)).toBeTruthy()
  })
})

describe('AcquireLotForm — dates (found driving Add holding, 2026-10-12)', () => {
  // The dialog renders in a portal, outside the render container.
  const setDate = (iso: string) =>
    fireEvent.change(document.querySelector('input[type="date"]') as HTMLInputElement, {
      target: { value: iso },
    })
  const renderAdd = () =>
    render(
      <AcquireLotForm
        symbol="AAPL"
        instrumentId="inst-1"
        costCcy="USD"
        lockSymbol
        onDone={() => {}}
        onClose={() => {}}
      />,
    )

  it('refuses a purchase dated in the future', async () => {
    const { findByLabelText, getByRole, findByText } = renderAdd()
    const account = (await findByLabelText(/Account/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(account.options).some((o) => o.value === 'acc-1')).toBe(true),
    )
    fireEvent.change(account, { target: { value: 'acc-1' } })
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '1' } })
    setDate('2999-01-01')

    expect(await findByText("That date hasn't happened yet.")).toBeTruthy()
    expect((getByRole('button', { name: 'Add lot' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it("doesn't promise a deduction dated before the account's starting balance", async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({
          ok: true,
          json: async () => [
            ...ACCOUNTS,
            { id: 'acc-cash', name: 'Cash USD', kind: 'asset', initial_balance_date: '2026-06-05' },
          ],
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByLabelText, findByText } = renderAdd()
    fireEvent.change(await findByLabelText(/Quantity/), { target: { value: '1' } })
    fireEvent.change(await findByLabelText(/Unit cost/), { target: { value: '200' } })
    const deduct = (await findByLabelText(/Deduct from/)) as HTMLSelectElement
    await waitFor(() =>
      expect(Array.from(deduct.options).some((o) => o.value === 'acc-cash')).toBe(true),
    )
    fireEvent.change(deduct, { target: { value: 'acc-cash' } })

    setDate('2024-08-01')
    expect(await findByText(/before its starting balance/)).toBeTruthy()

    setDate('2026-07-01')
    expect(await findByText(/will be deducted from Cash USD/)).toBeTruthy()
  })
})
