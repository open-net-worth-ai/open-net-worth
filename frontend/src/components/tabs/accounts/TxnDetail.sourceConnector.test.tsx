// @vitest-environment jsdom
//
// CDS-UI-16/18 (docs/qa/acceptance/connectors-data-sources.md): the drawer's "Source" section
// generalized to a connector-booked entry — Provider/Provider account/Fetched rows, a "Show
// raw fields ▾" toggle, and the generic unavailable copy. Never displays the raw
// provider_account_id as a label (UI spec §4.4).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn, SourceEntry } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'acc_checking',
    name: 'Chase Checking',
    kind: 'asset',
    type: 'cash',
    institution: 'Chase',
    currency: 'USD',
    initialBalance: 0,
    initialBalanceDate: null,
    creditLimit: null,
    lastFour: '1234',
    note: '',
    statementCloseDay: null,
    paymentDueDay: null,
    reminderDays: null,
    lastReconciled: null,
    archived: false,
    connected: true,
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: -42.1,
    balanceBase: -42.1,
    clearedBalance: -42.1,
    clearedBalanceBase: -42.1,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 'connector-txn-1',
    date: '2026-09-03',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'WHOLE FOODS MARKET',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'acc_checking',
    fromAmount: 42.1,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 42.1,
    baseCurrency: 'USD',
    cleared: true,
    source: 'connector',
    createdAt: '2026-09-03',
    deleted: false,
    needsReview: false,
    externalRefs: [{ source: 'plaid', ref: 'plaid_txn_1' }],
    ...over,
  }
}

const activity = Promise.resolve({
  source: 'connector',
  addedBy: 'plaid',
  createdAt: '2026-09-03',
  updatedAt: '2026-09-03',
  activity: [],
})

function renderDrawer(
  t: Txn,
  entries: SourceEntry[] | null,
  onDetachSourceRef: (txn: Txn, source: string, ref: string) => Promise<void> = () =>
    Promise.resolve(),
  onLoadSourceEntries: (id: string) => Promise<SourceEntry[] | null> = () =>
    Promise.resolve(entries),
) {
  return render(
    <TxnDetail
      txn={t}
      accounts={[makeAccount()]}
      categories={[] as Category[]}
      payeeOptions={[]}
      baseCurrency="USD"
      rates={{}}
      onClose={() => {}}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() => activity}
      onLoadSourceEntries={onLoadSourceEntries}
      onDetachSourceRef={onDetachSourceRef}
    />,
  )
}

describe('TxnDetail — Source section, kind: connector (CDS-UI-16)', () => {
  const entry: SourceEntry = {
    kind: 'connector',
    source: 'plaid',
    ref: 'plaid_txn_1',
    provider: 'plaid',
    providerLabel: 'Plaid',
    providerAccountId: 'plaid-account-abc123',
    fetchedAt: '2026-09-03T06:00:00Z',
    raw: {
      amount: -42.1,
      currency: 'USD',
      description: 'WHOLE FOODS MARKET',
      pending: false,
    },
    unavailable: false,
  }

  it('renders Provider / Provider account / Fetched rows and a raw-fields toggle, not "Source email"', async () => {
    renderDrawer(makeTxn(), [entry])

    await waitFor(() =>
      expect(screen.getByText('Source', { selector: '.acct-detail-sec' })).toBeTruthy(),
    )
    expect(screen.queryByText('Source email')).toBeNull()
    expect(screen.getByText('Provider')).toBeTruthy()
    expect(screen.getByText('Plaid')).toBeTruthy()
    expect(screen.getByText('Provider account')).toBeTruthy()
    // Resolved to the LINKED APP ACCOUNT'S name, never the raw provider_account_id.
    expect(screen.getAllByText('Chase Checking').length).toBeGreaterThan(0)
    expect(screen.queryByText('plaid-account-abc123')).toBeNull()
    expect(screen.getByText('Fetched')).toBeTruthy()
    expect(screen.getByRole('button', { name: /show raw fields/i })).toBeTruthy()
  })

  it('expanding "Show raw fields" reveals one row per raw[] key, Title-Cased', async () => {
    renderDrawer(makeTxn(), [entry])
    await waitFor(() => screen.getByRole('button', { name: /show raw fields/i }))
    fireEvent.click(screen.getByRole('button', { name: /show raw fields/i }))

    expect(screen.getByRole('button', { name: /hide raw fields/i })).toBeTruthy()
    const rawBody = document.querySelector('.acct-detail-raw-body') as HTMLElement
    expect(rawBody).toBeTruthy()
    expect(within(rawBody).getByText('Amount')).toBeTruthy()
    expect(within(rawBody).getByText('-42.1')).toBeTruthy()
    expect(within(rawBody).getByText('Currency')).toBeTruthy()
    expect(within(rawBody).getByText('Description')).toBeTruthy()
    expect(within(rawBody).getByText('WHOLE FOODS MARKET')).toBeTruthy()
    expect(within(rawBody).getByText('Pending')).toBeTruthy()
    expect(within(rawBody).getByText('false')).toBeTruthy()
  })

  it('falls back to "<Provider> account" when the txn resolves to no app account', async () => {
    renderDrawer(makeTxn({ fromAccountId: null, toAccountId: null }), [entry])
    await waitFor(() => expect(screen.getByText('Provider account')).toBeTruthy())
    expect(screen.getByText('Plaid account')).toBeTruthy()
    expect(screen.queryByText('plaid-account-abc123')).toBeNull()
  })
})

describe('TxnDetail — Source section, kind: connector (CDS-UI-22 Unlink source)', () => {
  const entry: SourceEntry = {
    kind: 'connector',
    source: 'plaid',
    ref: 'plaid_txn_1',
    provider: 'plaid',
    providerLabel: 'Plaid',
    providerAccountId: 'plaid-account-abc123',
    fetchedAt: '2026-09-03T06:00:00Z',
    raw: { amount: -42.1 },
    unavailable: false,
  }

  it("posts the entry's {source, ref} (the connector's own pair, not messageId-shaped) and removes it", async () => {
    const onDetach = vi.fn(() => Promise.resolve())
    const txn = makeTxn()
    renderDrawer(txn, [entry], onDetach)
    await waitFor(() => screen.getByText('Unlink source'))
    fireEvent.click(screen.getByText('Unlink source'))
    expect(screen.getByText('Unlink source?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Unlink source' }))
    expect(onDetach).toHaveBeenCalledWith(
      expect.objectContaining({ id: txn.id }),
      'plaid',
      'plaid_txn_1',
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Unlink source' })).toBeNull())
    expect(screen.queryByText('Provider')).toBeNull()
    expect(screen.queryByRole('button', { name: /show raw fields/i })).toBeNull()
  })

  it('a failed confirmed unlink keeps the source and shows a scoped error', async () => {
    const onDetach = vi.fn(() => Promise.reject(new Error('network down')))
    renderDrawer(makeTxn(), [entry], onDetach)
    await waitFor(() => screen.getByText('Unlink source'))
    fireEvent.click(screen.getByText('Unlink source'))
    fireEvent.click(screen.getByRole('button', { name: 'Unlink source' }))
    await waitFor(() => expect(screen.getByText(/Couldn't unlink this source\./)).toBeTruthy())
    // The connector's own rows survive the failed unlink — nothing was actually removed.
    expect(screen.getByText('Plaid')).toBeTruthy()
  })
})

describe('TxnDetail — Source section, kind: connector unavailable (CDS-UI-18)', () => {
  it('shows the generic "Snapshot unavailable" copy for a pruned/never-stashed connector entry', async () => {
    const gone: SourceEntry = {
      kind: 'connector',
      source: 'plaid',
      ref: 'plaid_txn_1',
      provider: 'plaid',
      providerLabel: 'Plaid',
      unavailable: true,
    }
    renderDrawer(makeTxn(), [gone])

    await waitFor(() =>
      expect(
        screen.getByText(
          'Snapshot unavailable — this transaction was booked before evidence was kept (or the original record is no longer stored).',
        ),
      ).toBeTruthy(),
    )
    expect(screen.queryByText('Provider account')).toBeNull()
    expect(screen.queryByRole('button', { name: /show raw fields/i })).toBeNull()
  })
})

describe('TxnDetail — Source section, nothing to show (sources call resolves null)', () => {
  it('an iOS-captured row shows no Source section — its refs are keys, not evidence', async () => {
    const load = vi.fn(() => Promise.resolve(null))
    renderDrawer(
      makeTxn({
        externalRefs: [
          { source: 'ios-wallet', ref: 'wallet-tap:2026-09-01T00:36:19+07:00' },
          { source: 'ios-notification', ref: 'notif:2026-09-01T00:36:22+07:00@Bank' },
        ],
      }),
      null,
      () => Promise.resolve(),
      load,
    )

    await waitFor(() => expect(load).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText(/^Source/, { selector: '.acct-detail-sec' })).toBeNull()
    expect(screen.queryByText(/Snapshot unavailable/)).toBeNull()
  })
})

describe('TxnDetail — Source section, mixed email + connector entries', () => {
  it('renders "Source (2)" with one email block and one connector block', async () => {
    const emailEntry: SourceEntry = {
      kind: 'email',
      source: 'email',
      ref: 'm1',
      messageId: 'm1',
      subject: 'Receipt',
      from: 'receipts@wholefoods.example',
      receivedAt: '2026-09-03',
      bodyText: 'Thanks for shopping',
      unavailable: false,
    }
    const connectorEntry: SourceEntry = {
      kind: 'connector',
      source: 'plaid',
      ref: 'plaid_txn_1',
      provider: 'plaid',
      providerLabel: 'Plaid',
      providerAccountId: 'plaid-account-abc123',
      fetchedAt: '2026-09-03T06:00:00Z',
      raw: { amount: -42.1 },
      unavailable: false,
    }
    renderDrawer(
      makeTxn({
        externalRefs: [
          { source: 'email', ref: 'm1' },
          { source: 'plaid', ref: 'plaid_txn_1' },
        ],
      }),
      [emailEntry, connectorEntry],
    )

    await waitFor(() => expect(screen.getByText('Source (2)')).toBeTruthy())
    expect(screen.getByText('Show full email ▾')).toBeTruthy()
    expect(screen.getByRole('button', { name: /show raw fields/i })).toBeTruthy()
    expect(document.querySelectorAll('.acct-detail-email-div').length).toBe(1)
  })
})

describe("TxnDetail — Source section never lists a statement file's row fingerprints", () => {
  const statementRefs = [
    { source: 'statement', ref: 'a1b2c3d4e5f6a1b2c3d4e5f6' },
    { source: 'statement', ref: 'ref:acc_checking:900000102:2026-09-10:-3.72' },
  ]

  it('a statement-linked row with no other provenance shows no Source section and fetches nothing', async () => {
    const load = vi.fn(() => Promise.resolve(null))
    renderDrawer(makeTxn({ externalRefs: statementRefs }), null, () => Promise.resolve(), load)

    await new Promise((r) => setTimeout(r, 20))
    expect(load).not.toHaveBeenCalled()
    expect(screen.queryByText('Source', { selector: '.acct-detail-sec' })).toBeNull()
    expect(screen.queryByText(/Snapshot unavailable/)).toBeNull()
  })

  it('a connector row that also carries statement refs still shows its one real source', async () => {
    const entry: SourceEntry = {
      kind: 'connector',
      source: 'plaid',
      ref: 'plaid_txn_1',
      provider: 'plaid',
      providerLabel: 'Plaid',
      providerAccountId: '',
      fetchedAt: '2026-09-03T06:00:00Z',
      raw: { amount: -42.1 },
      unavailable: false,
    }
    const load = vi.fn(() => Promise.resolve([entry]))
    renderDrawer(
      makeTxn({ externalRefs: [{ source: 'plaid', ref: 'plaid_txn_1' }, ...statementRefs] }),
      [entry],
      () => Promise.resolve(),
      load,
    )

    await waitFor(() =>
      expect(screen.getByText('Source', { selector: '.acct-detail-sec' })).toBeTruthy(),
    )
    expect(load).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/Snapshot unavailable/)).toBeNull()
  })
})
