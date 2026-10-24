// @vitest-environment jsdom
//
// CC1 regression: the credit-card due banner used to render `fCur(account.balance, …)` —
// the LIVE running balance — which (a) shows a literal minus sign (a liability's balance is
// negative) and (b) never disappears once a statement is fully paid, because a new post-payment
// charge in the still-open cycle keeps the running balance nonzero. The fix computes the banner
// from GET /api/accounts/{id}/statement (backend/services/card_statements.py, already tested)
// instead of the raw balance: a fully-paid statement must hide the banner even though the
// account's live balance is still nonzero from new spending.
//
// Covers both halves of the fix:
//  - AccountHeader rendering: positive amount / partial phrasing / overdue phrasing, given a
//    contrived `dueInfo` prop (no fetch involved).
//  - AccountsTab wiring: the fetched statement summary correctly turns into `dueInfo` (or null),
//    reproducing the exact reported scenario end-to-end.
//
// Recency ladder (owner 2026-07-16): when the backend flags statement_missing_for_cycle —
// a newer cycle closed but its statement isn't imported — the banner is the new cycle's
// ESTIMATE plus an import nudge ("Your <date> statement isn't imported yet — import it to confirm"), and it
// must NEVER congratulate: congrats is reserved for a paid statement that is still the
// card's current obligation.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountHeader } from './AccountHeader'
import type { DueInfo } from './AccountHeader'
import { AccountsTab } from './AccountsTab'
import { fromApiAccount } from './data'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount } from './types'

// ── AccountHeader — direct render, no fetch ─────────────────────────────────

const API_ACCOUNT: ApiAccount = {
  id: 'acc-cc',
  name: 'Amex',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
  statement_close_day: 20,
  payment_due_day: 5,
  balance: -45.2,
  balance_base: -45.2,
  cleared_balance: -45.2,
  cleared_balance_base: -45.2,
}
const ACCOUNT = fromApiAccount(API_ACCOUNT)
const noop = () => {}

function renderHeader(dueInfo: DueInfo | null) {
  return render(
    <AccountHeader
      account={ACCOUNT}
      dueInfo={dueInfo}
      stats={null}
      menuOpen={false}
      setMenuOpen={noop}
      collapsed={false}
      onReopen={noop}
      onAddTxn={noop}
      onEdit={noop}
      onArchive={noop}
      onDelete={noop}
      onManagePayees={noop}
      onManageCategories={noop}
      onImport={noop}
      onExport={noop}
      onReconcile={noop}
      onReview={noop}
      onStartReview={noop}
    />,
  )
}

describe('AccountHeader credit-card due banner (CC1)', () => {
  afterEach(cleanup)

  it('hides the banner entirely when dueInfo is null (e.g. a fully-paid statement)', () => {
    renderHeader(null)
    expect(screen.queryByText(/due in|overdue/)).toBeNull()
  })

  it('shows a POSITIVE amount for an unpaid statement — no leading minus sign', () => {
    renderHeader({ amountDue: 165.75, currency: 'SGD', days: 1, closeDays: 12, partial: false })
    expect(screen.getByText(/S\$165\.75/)).toBeTruthy() // "~" prefix allowed (estimate tier)
    expect(screen.queryByText(/-S\$165\.75/)).toBeNull()
    expect(screen.getByText(/due in/)).toBeTruthy()
    expect(screen.getByText(/statement closes in/)).toBeTruthy()
  })

  it('phrases a partial payment as a remaining balance, not a fresh due amount', () => {
    renderHeader({
      amountDue: 50,
      currency: 'SGD',
      days: 3,
      closeDays: 12,
      partial: true,
      paidAmount: 120,
      totalDue: 170,
    })
    expect(screen.getByText(/paid/i)).toBeTruthy()
    expect(screen.getByText('S$120')).toBeTruthy() // how much WAS paid
    expect(screen.getByText('S$50')).toBeTruthy() // the remainder
    expect(screen.getByText(/still/)).toBeTruthy()
  })

  it('congratulates a fully-paid statement instead of warning', () => {
    renderHeader({
      amountDue: 0,
      currency: 'SGD',
      days: 0,
      closeDays: null,
      partial: false,
      settled: true,
      totalDue: 4000.0,
      source: 'statement',
    })
    expect(screen.getByText(/congrats/i)).toBeTruthy()
    expect(screen.getByText(/paid in full/i)).toBeTruthy()
    expect(screen.queryByText(/overdue|due in/)).toBeNull()
    expect(screen.queryByText(/remind me/i)).toBeNull() // nothing left to remind
  })

  it('estimate-tier congrats leads with the REAL payment, never "paid in full" of a guess', () => {
    renderHeader({
      amountDue: 0,
      currency: 'SGD',
      days: 0,
      closeDays: null,
      partial: false,
      settled: true,
      totalDue: 1166.98,
      paidAmount: 1518.96,
      source: 'config',
    })
    expect(screen.getByText(/congrats/i)).toBeTruthy()
    expect(screen.getByText('S$1,518.96')).toBeTruthy() // what was actually paid
    expect(screen.getByText(/~S\$1,166\.98/)).toBeTruthy() // the estimate, marked as such
    expect(screen.queryByText(/paid in full/i)).toBeNull()
    expect(screen.getByText(/import statement/i)).toBeTruthy() // confirm with the real figures
  })

  it('phrases an overdue due date as "overdue by N days", not "due in -2 days"', () => {
    renderHeader({ amountDue: 100, currency: 'SGD', days: -2, closeDays: null, partial: false })
    expect(screen.getByText(/overdue/i)).toBeTruthy()
    expect(screen.queryByText(/due in -2/)).toBeNull()
  })

  it('nudges to import when a newer cycle closed without its statement (estimate + nudge tier)', () => {
    renderHeader({
      amountDue: 4969.57,
      currency: 'SGD',
      days: 14,
      closeDays: null,
      partial: false,
      source: 'config',
      missingForCycle: true,
      missingCloseDate: '2026-07-05',
    })
    expect(screen.getByText(/~S\$4,969\.57/)).toBeTruthy() // '~' estimate marker
    expect(screen.getByText(/due in/)).toBeTruthy()
    expect(screen.getByText(/statement isn't imported yet/i)).toBeTruthy()
    expect(screen.getByText('07/05/2026')).toBeTruthy()
    expect(screen.getByText(/import it to confirm/i)).toBeTruthy()
    expect(screen.getByText(/import statement/i)).toBeTruthy() // the CTA
    expect(screen.queryByText(/congrats/i)).toBeNull()
  })

  it('renders a nudge-only banner (no congrats, no zero amount) when the missing-cycle estimate folds to zero', () => {
    renderHeader({
      amountDue: 0,
      currency: 'SGD',
      days: 14,
      closeDays: null,
      partial: false,
      source: 'config',
      missingForCycle: true,
      missingCloseDate: '2026-07-05',
    })
    expect(screen.getByText(/statement isn't imported yet/i)).toBeTruthy()
    expect(screen.getByText(/import it to confirm/i)).toBeTruthy()
    const upload = screen.getByRole('button', { name: 'Import statement' })
    expect(upload.classList.contains('acct-import-btn')).toBe(true)
    expect(upload.querySelector('svg[aria-hidden="true"]')).toBeTruthy()
    expect(upload.querySelector('.acct-import-label')?.textContent).toBe('Import statement')
    expect(screen.queryByText(/congrats/i)).toBeNull()
    // No zero-amount payment phrase — the banner is nudge-only (the S$0.00 elsewhere on
    // the page is the balance card's own uncleared line, not the banner).
    expect(screen.queryByText(/payment of/i)).toBeNull()
    expect(document.querySelector('.acct-alert')?.textContent).not.toMatch(/S\$0/)
    expect(screen.queryByText(/remind me/i)).toBeNull() // nothing measurable to remind
  })
})

// ── AccountsTab — full wiring through the fetched statement summary ────────

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

// Mirrors the backend's clamped remaining_amount (MATH-45, card_statements.py) so these
// mocked /statement payloads stay honest about the field AccountsTab now reads directly
// instead of re-deriving.
function remainingAmount(balance: number, payment: number | null): number {
  return Math.max(0, Math.round((balance - (payment ?? 0)) * 100) / 100)
}

// A credit card whose LIVE balance is nonzero purely from new spending after the last
// statement was paid off in full — the exact reported scenario.
const CC_ACCOUNT: ApiAccount = {
  id: 'acc-cc',
  name: 'Amex',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
  statement_close_day: 20,
  payment_due_day: 5,
  balance: -45.2,
  balance_base: -45.2,
  cleared_balance: -45.2,
  cleared_balance_base: -45.2,
}

function setupMocks(statementResponse: Response) {
  mockAuthFetch.mockImplementation((url: string) => {
    if (url === `/api/accounts/${CC_ACCOUNT.id}/statement`)
      return Promise.resolve(statementResponse)
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([CC_ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  // The query cache is module-level: without this, a URL fetched by an earlier
  // test is served from cache here and never reaches the authFetch mock.
  clearQueryCache()
  vi.clearAllMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function renderSelectedCard() {
  render(<AccountsTab selectedAccountId={CC_ACCOUNT.id} />)
  await waitFor(() =>
    expect(
      mockAuthFetch.mock.calls.some(([u]) => u === `/api/accounts/${CC_ACCOUNT.id}/statement`),
    ).toBe(true),
  )
  // "Amex" also appears in the sidebar row — wait on the header title specifically.
  await waitFor(() => expect(document.querySelector('.acct-head-title')).toBeTruthy())
}

describe('AccountsTab wires the fetched statement summary into the due banner (CC1)', () => {
  it('opens the account navigator as a bottom sheet on mobile', async () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 })
    vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
      matches: query === '(max-width: 639.98px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 1,
        last_statement_balance: 200,
        paid: true,
        partial: false,
        payment_amount: 200,
        remaining_amount: remainingAmount(200, 200),
      }),
    )

    await renderSelectedCard()
    fireEvent.click(screen.getByRole('button', { name: 'Show accounts' }))

    const dialog = await screen.findByRole('dialog', { name: 'Accounts' })
    expect(document.querySelector('.acct-side-sheet .acct-side')).toBeTruthy()
    fireEvent.click(dialog.querySelector('[data-entity-id="account:acc-cc"]') as HTMLElement)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Accounts' })).toBeNull())
  })

  it('hides the banner once fully paid, even though the account balance is still nonzero from new spending', async () => {
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 1,
        last_statement_balance: 200,
        paid: true,
        partial: false,
        payment_amount: 200,
        remaining_amount: remainingAmount(200, 200),
      }),
    )
    await renderSelectedCard()
    expect(screen.queryByText(/due in|overdue/)).toBeNull()
  })

  it('shows the POSITIVE last-statement amount when unpaid', async () => {
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 1,
        last_statement_balance: 165.75,
        paid: false,
        partial: false,
        payment_amount: null,
        remaining_amount: remainingAmount(165.75, null),
      }),
    )
    await renderSelectedCard()
    await screen.findByText(/S\$165\.75/)
    expect(screen.queryByText(/-S\$165\.75/)).toBeNull()
    expect(screen.getByText(/due in/)).toBeTruthy()
  })

  it('shows the unpaid REMAINDER (not the full statement) and flags it as a partial payment', async () => {
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 3,
        last_statement_balance: 200,
        paid: true,
        partial: true,
        payment_amount: 120,
        remaining_amount: remainingAmount(200, 120),
      }),
    )
    await renderSelectedCard()
    await screen.findByText('S$80')
    expect(screen.getByText(/paid/i)).toBeTruthy()
    expect(screen.queryByText('S$200')).toBeNull()
  })

  it('renders remaining_amount verbatim from the backend rather than re-deriving it (MATH-45)', async () => {
    // last_statement_balance - payment_amount would compute 80 here — remaining_amount is
    // deliberately set to a DIFFERENT value so this only passes if the banner reads the
    // field itself instead of recomputing the raw formula client-side.
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 3,
        last_statement_balance: 200,
        paid: true,
        partial: true,
        payment_amount: 120,
        remaining_amount: 37,
      }),
    )
    await renderSelectedCard()
    await screen.findByText('S$37')
    expect(screen.queryByText('S$80')).toBeNull()
  })

  it('clamps an overpaid statement to zero remaining instead of a negative amount (MATH-45)', async () => {
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 3,
        last_statement_balance: 200,
        paid: true,
        partial: false,
        payment_amount: 250,
        remaining_amount: remainingAmount(200, 250),
      }),
    )
    await renderSelectedCard()
    // Fully covered (and then some): the banner reads as settled, never a negative due amount.
    expect(screen.queryByText(/due in|overdue/)).toBeNull()
    expect(document.querySelector('.acct-alert')?.textContent).not.toMatch(/-S\$/)
  })

  it("does not attribute card A's stale statement to card B while B's own fetch is still pending", async () => {
    const CARD_B: ApiAccount = {
      id: 'acc-cc-b',
      name: 'Visa',
      kind: 'liability',
      type: 'credit',
      currency: 'SGD',
      statement_close_day: 15,
      payment_due_day: 1,
      balance: -9.99,
      balance_base: -9.99,
      cleared_balance: -9.99,
      cleared_balance_base: -9.99,
    }
    let resolveB!: (r: Response) => void
    const bPending = new Promise<Response>((resolve) => {
      resolveB = resolve
    })

    mockAuthFetch.mockImplementation((url: string) => {
      if (url === `/api/accounts/${CC_ACCOUNT.id}/statement`) {
        return Promise.resolve(
          okJson({
            currency: 'SGD',
            days_until_due: 1,
            last_statement_balance: 165.75,
            paid: false,
            partial: false,
            payment_amount: null,
            remaining_amount: remainingAmount(165.75, null),
          }),
        )
      }
      if (url === `/api/accounts/${CARD_B.id}/statement`) return bPending
      if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([CC_ACCOUNT, CARD_B]))
      if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
      if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
      if (url.startsWith('/api/transactions'))
        return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
      return Promise.resolve(okJson({}))
    })

    const { rerender } = render(<AccountsTab selectedAccountId={CC_ACCOUNT.id} />)
    await screen.findByText(/S\$165\.75/) // card A's due amount is showing

    rerender(<AccountsTab selectedAccountId={CARD_B.id} />)
    await waitFor(() =>
      expect(
        mockAuthFetch.mock.calls.some(([u]) => u === `/api/accounts/${CARD_B.id}/statement`),
      ).toBe(true),
    )
    // B's own fetch is still pending — the banner must show NOTHING, never A's stale amount.
    expect(screen.queryByText(/S\$165\.75/)).toBeNull()
    expect(screen.queryByText(/due in|overdue/)).toBeNull()

    // Once B's own (different) statement resolves, its own amount appears correctly.
    resolveB(
      okJson({
        currency: 'SGD',
        days_until_due: 5,
        last_statement_balance: 42,
        paid: false,
        partial: false,
        payment_amount: null,
        remaining_amount: remainingAmount(42, null),
      }),
    )
    await screen.findByText(/S\$42/)
  })

  it('prefetches every card so opening one paints its banner in the same render — no late push', async () => {
    const CARD_B: ApiAccount = {
      ...CC_ACCOUNT,
      id: 'acc-cc-b',
      name: 'Visa',
      balance: -9.99,
      balance_base: -9.99,
    }
    const due = (amount: number) =>
      okJson({
        currency: 'SGD',
        days_until_due: 5,
        last_statement_balance: amount,
        paid: false,
        partial: false,
        payment_amount: null,
        remaining_amount: remainingAmount(amount, null),
      })
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === `/api/accounts/${CC_ACCOUNT.id}/statement`) return Promise.resolve(due(165.75))
      if (url === `/api/accounts/${CARD_B.id}/statement`) return Promise.resolve(due(42))
      if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([CC_ACCOUNT, CARD_B]))
      if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
      if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
      if (url.startsWith('/api/transactions'))
        return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
      return Promise.resolve(okJson({}))
    })
    const statementCalls = (id: string) =>
      mockAuthFetch.mock.calls.filter(([u]) => u === `/api/accounts/${id}/statement`).length

    const { rerender } = render(<AccountsTab selectedAccountId={CC_ACCOUNT.id} />)
    await screen.findByText(/S\$165\.75/)
    // B was never opened, yet its summary is already loaded.
    await waitFor(() => expect(statementCalls(CARD_B.id)).toBe(1))

    rerender(<AccountsTab selectedAccountId={CARD_B.id} />)
    // Synchronous: the banner is there in the render that opened the card.
    expect(screen.getByText(/S\$42/)).toBeTruthy()
    // …and opening it still revalidates, so a payment made since is picked up.
    await waitFor(() => expect(statementCalls(CARD_B.id)).toBe(2))
  })

  it('shows the new-cycle estimate + import nudge when the linked statement is older and PAID (never a stale congrats)', async () => {
    // Owner repro 2026-07-16: June statement paid in full, July cycle closed 07-05 without
    // its statement — backend falls through to the ledger estimate for the new cycle.
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 14,
        next_due_date: '2026-07-30',
        last_statement_balance: 4969.57,
        paid: false,
        partial: false,
        payment_amount: null,
        remaining_amount: remainingAmount(4969.57, null),
        due_source: 'config',
        statement_missing_for_cycle: true,
        last_statement_close: '2026-07-05',
      }),
    )
    await renderSelectedCard()
    await screen.findByText(/S\$4,969\.57/)
    expect(screen.getByText(/due in/)).toBeTruthy()
    expect(screen.getByText(/statement isn't imported yet/i)).toBeTruthy()
    expect(screen.getByText('07/05/2026')).toBeTruthy()
    expect(screen.getByText(/import it to confirm/i)).toBeTruthy()
    expect(screen.queryByText(/congrats/i)).toBeNull()
    expect(screen.queryByText(/paid in full/i)).toBeNull()
  })

  it('never congratulates off a missing-statement cycle even when the estimate reads fully paid', async () => {
    setupMocks(
      okJson({
        currency: 'SGD',
        days_until_due: 14,
        last_statement_balance: 200,
        paid: true,
        partial: false,
        payment_amount: 200,
        remaining_amount: remainingAmount(200, 200),
        due_source: 'config',
        statement_missing_for_cycle: true,
        last_statement_close: '2026-07-05',
      }),
    )
    await renderSelectedCard()
    expect(await screen.findByText(/statement isn't imported yet/i)).toBeTruthy()
    expect(screen.getByText(/import it to confirm/i)).toBeTruthy()
    expect(screen.queryByText(/congrats/i)).toBeNull()
    expect(screen.queryByText(/paid in full/i)).toBeNull()
    expect(screen.queryByText(/remind me/i)).toBeNull()
  })
})
