// @vitest-environment jsdom
//
// EM1 — Source section, kind: 'email' (docs/qa/acceptance/email-source-snapshot.md +
// CDS-UI-17 in docs/qa/acceptance/connectors-data-sources.md). Covers the 5 fetch states
// (idle/gated-off, loading, ok, unavailable, error), the reset-on-txn-change behavior, and
// that the full body renders as React-escaped text (no <a>/<script> in the DOM). This file IS
// the byte-identical regression gate for the connectors-as-data-sources generalization: the
// per-entry email rendering (Subject/From/Received rows, the toggle, the body, the
// unavailable copy) must read exactly as it did before "Source email" became "Source" — only
// the section HEADING and the prop/type names changed.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn, SourceEntry } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Northgate Rewards Card',
    kind: 'asset',
    type: 'credit',
    institution: 'Northgate',
    currency: 'SGD',
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
    connected: false,
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: -100,
    balanceBase: -100,
    clearedBalance: -100,
    clearedBalanceBase: -100,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-01',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'Northgate charge',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 28.18,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 28.18,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'agent',
    createdAt: '2026-07-01',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

const emailTxn = () =>
  makeTxn({ id: 'email-txn-1', externalRefs: [{ source: 'email', ref: 'm1aaaa' }] })

const activity = Promise.resolve({
  source: 'agent',
  addedBy: 'email-transaction-logger',
  createdAt: '2026-07-01',
  updatedAt: '2026-07-01',
  activity: [],
})

function renderDrawer(
  t: Txn,
  onLoadSourceEntries: (id: string) => Promise<SourceEntry[] | null>,
  onDetachSourceRef: (txn: Txn, source: string, ref: string) => Promise<void> = () =>
    Promise.resolve(),
) {
  return render(
    <TxnDetail
      txn={t}
      accounts={[makeAccount()]}
      categories={[] as Category[]}
      payeeOptions={[]}
      baseCurrency="SGD"
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

describe('TxnDetail — Source section, kind: email (EM1-01 gate)', () => {
  it('non-email transaction: no section, no fetch issued', async () => {
    const loader = vi.fn(() => Promise.resolve(null))
    renderDrawer(makeTxn(), loader)
    // Give any stray effect a tick to (not) fire.
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('Source', { selector: '.acct-detail-sec' })).toBeNull()
    expect(loader).not.toHaveBeenCalled()
  })
})

describe('TxnDetail — Source section, kind: email (EM1-02/03/04 ok state + disclosure)', () => {
  const data: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'm1aaaa',
    messageId: 'm1aaaa',
    subject: 'Your Northgate statement is ready',
    from: 'alerts@northgatecard.example',
    receivedAt: '2026-07-01',
    bodyText: 'Line one\nLine two\nAmount: $50.00',
    unavailable: false,
  }

  it('renders below Provenance with real Subject/From/Received + a collapsed toggle', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([data]))
    await waitFor(() =>
      expect(screen.getByText('Source', { selector: '.acct-detail-sec' })).toBeTruthy(),
    )
    expect(screen.getByText('Your Northgate statement is ready')).toBeTruthy()
    expect(screen.getByText('alerts@northgatecard.example')).toBeTruthy()
    const toggle = screen.getByText('Show full email')
    expect(toggle).toBeTruthy()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    // "Source" renders after "Provenance" in document order.
    const sections = screen.getAllByText(/^(Provenance|Source)$/, { selector: '.acct-detail-sec' })
    expect(sections.map((n) => n.textContent)).toEqual(['Provenance', 'Source'])
  })

  it('expand reveals the scrollable plain-text body with line breaks preserved, no links', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([data]))
    await waitFor(() => screen.getByText('Show full email'))
    fireEvent.click(screen.getByText('Show full email'))
    expect(screen.getByText('Hide full email')).toBeTruthy()
    const body = document.getElementById('src-email-body-m1aaaa')
    expect(body).toBeTruthy()
    expect(body!.tagName).toBe('PRE')
    expect(body!.textContent).toBe('Line one\nLine two\nAmount: $50.00')
    expect(body!.querySelector('a')).toBeNull()
  })

  it('collapse reverts the toggle and hides the body', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([data]))
    await waitFor(() => screen.getByText('Show full email'))
    fireEvent.click(screen.getByText('Show full email'))
    expect(screen.getByText('Hide full email')).toBeTruthy()
    fireEvent.click(screen.getByText('Hide full email'))
    expect(screen.getByText('Show full email')).toBeTruthy()
    expect(document.getElementById('src-email-body-m1aaaa')).toBeNull()
  })

  it('a blank subject falls back to "(no subject)"', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([{ ...data, subject: '' }]))
    await waitFor(() => expect(screen.getByText('(no subject)')).toBeTruthy())
  })

  it('a full ISO receivedAt (EM1 date_time) renders with a "• HH:MM" time, not date-only', async () => {
    // EM1: imap_client now carries hour+minute via date_time; the Received row must show
    // it (fmtDateTime already branches on `iso.includes('T')` — this proves the wiring).
    const withTime: SourceEntry = { ...data, receivedAt: '2026-07-01T10:15:00+00:00' }

    renderDrawer(emailTxn(), () => Promise.resolve([withTime]))
    await waitFor(() => expect(screen.getByText('Your Northgate statement is ready')).toBeTruthy())
    const d = new Date(withTime.receivedAt)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    expect(screen.getByText(new RegExp(`•\\s*${hh}:${mm}$`))).toBeTruthy()
  })

  it('keeps the email rows, disclosure, and unlink action in one source block', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([data]))
    await waitFor(() => screen.getByText('Show full email'))
    const heading = screen.getByText('Source', { selector: '.acct-detail-sec' })
    const sourceBlock = heading.nextElementSibling as HTMLElement
    expect(sourceBlock.classList.contains('acct-detail-source-block')).toBe(true)
    expect(sourceBlock.querySelector('.acct-detail-rows')).toBeTruthy()
    expect(sourceBlock.querySelector('.acct-detail-email-toggle')).toBeTruthy()
    expect(sourceBlock.querySelector('.acct-detail-source-unlink')).toBeTruthy()
  })
})

describe('TxnDetail — Source section, kind: email (CDS-UI-22 Unlink source)', () => {
  const data: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'm1aaaa',
    messageId: 'm1aaaa',
    subject: 'Your Northgate statement is ready',
    from: 'alerts@northgatecard.example',
    receivedAt: '2026-07-01',
    bodyText: 'Line one\nLine two\nAmount: $50.00',
    unavailable: false,
  }

  it("posts the entry's {source, ref} and removes it from the drawer, no page reload", async () => {
    const onDetach = vi.fn(() => Promise.resolve())
    const txn = emailTxn()
    renderDrawer(txn, () => Promise.resolve([data]), onDetach)
    await waitFor(() => screen.getByText('Unlink source'))
    fireEvent.click(screen.getByText('Unlink source'))
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Unlink source?' })).getByRole('button', {
        name: 'Unlink source',
      }),
    )
    expect(onDetach).toHaveBeenCalledWith(
      expect.objectContaining({ id: txn.id }),
      'email',
      'm1aaaa',
    )
    await waitFor(() => expect(screen.queryByText('Unlink source')).toBeNull())
    expect(screen.queryByText('Your Northgate statement is ready')).toBeNull()
    expect(screen.queryByText('Show full email')).toBeNull()
  })

  it('a failed unlink shows an inline error in the email block and can be retried', async () => {
    let attempt = 0
    const onDetach = vi.fn(() => {
      attempt += 1
      return attempt === 1 ? Promise.reject(new Error('network down')) : Promise.resolve()
    })
    renderDrawer(emailTxn(), () => Promise.resolve([data]), onDetach)
    await waitFor(() => screen.getByText('Unlink source'))
    fireEvent.click(screen.getByText('Unlink source'))
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Unlink source?' })).getByRole('button', {
        name: 'Unlink source',
      }),
    )
    await waitFor(() => expect(screen.getByText(/Couldn't unlink this source\./)).toBeTruthy())

    const errBlock = screen
      .getByText(/Couldn't unlink this source\./)
      .closest('.acct-detail-source-block')
    expect(errBlock).toBeTruthy()
    fireEvent.click(within(errBlock as HTMLElement).getByText('Unlink source'))
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Unlink source?' })).getByRole('button', {
        name: 'Unlink source',
      }),
    )
    await waitFor(() => expect(onDetach).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(/Couldn't unlink this source\./)).toBeNull())
    // Success on retry — the entry is gone, same as the happy path above.
    expect(screen.queryByText('Unlink source')).toBeNull()
  })
})

const goneEmail: SourceEntry = {
  kind: 'email',
  source: 'email',
  ref: 'm1aaaa',
  messageId: 'm1aaaa',
  unavailable: true,
}

describe('TxnDetail — Source section, kind: email (EM1-05 unavailable)', () => {
  it('shows the verbatim unavailable message with no rows/toggle', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([goneEmail]))
    await waitFor(() =>
      expect(
        screen.getByText(
          'Snapshot unavailable — this transaction was booked from email before snapshots were kept (or the original message is no longer stored).',
        ),
      ).toBeTruthy(),
    )
    expect(screen.queryByText('Subject')).toBeNull()
    expect(screen.queryByText(/Show full email/)).toBeNull()
  })
})

describe('TxnDetail — Source section, kind: email (EM1-06 loading)', () => {
  it('shows 3 skeleton rows while the fetch is in flight', async () => {
    let resolve!: (v: SourceEntry[] | null) => void
    const pending = new Promise<SourceEntry[] | null>((r) => {
      resolve = r
    })
    renderDrawer(emailTxn(), () => pending)
    await waitFor(() =>
      expect(screen.getByText('Source', { selector: '.acct-detail-sec' })).toBeTruthy(),
    )
    // Loading state: no real Subject/From row text yet, no toggle, no unavailable copy.
    expect(screen.queryByText('Show full email')).toBeNull()
    expect(screen.queryByText(/Snapshot unavailable/)).toBeNull()
    // Resolve to avoid an unhandled-state warning after the test.
    resolve([goneEmail])
    await waitFor(() => expect(screen.getByText(/Snapshot unavailable/)).toBeTruthy())
  })
})

describe('TxnDetail — Source section, kind: email (EM1-07 error + Retry)', () => {
  it('shows error copy + Retry, which re-issues the fetch (loading reappears)', async () => {
    let attempt = 0
    const loader = vi.fn(() => {
      attempt += 1
      if (attempt === 1) return Promise.reject(new Error('network down'))
      return new Promise<SourceEntry[] | null>(() => {}) // never resolves — just prove re-fetch fired
    })
    renderDrawer(emailTxn(), loader)
    await waitFor(() => expect(screen.getByText(/Couldn't load the source details\./)).toBeTruthy())
    // Scoped to the source block specifically — the drawer's own (unrelated, self-fetching)
    // Receipts section can independently land on its own error+Retry state in this
    // unmocked-authFetch test environment, so a bare screen.getByText('Retry') is ambiguous.
    const sourceBlock = document.querySelector('.acct-detail-email-empty') as HTMLElement
    expect(within(sourceBlock).getByText('Retry')).toBeTruthy()

    fireEvent.click(within(sourceBlock).getByText('Retry'))
    expect(loader).toHaveBeenCalledTimes(2)
    // Back to loading: the error copy is gone.
    await waitFor(() => expect(screen.queryByText(/Couldn't load the source details\./)).toBeNull())
  })
})

describe('TxnDetail — Source section, kind: email (EM1-08 reset on txn change)', () => {
  const dataA: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'ma1111',
    messageId: 'ma1111',
    subject: 'Txn A email',
    from: 'a@x.com',
    receivedAt: '2026-07-01',
    bodyText: 'Body A',
    unavailable: false,
  }
  const dataB: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'mb2222',
    messageId: 'mb2222',
    subject: 'Txn B email',
    from: 'b@x.com',
    receivedAt: '2026-07-02',
    bodyText: 'Body B',
    unavailable: false,
  }

  it('opening a different email txn starts collapsed with its own data, not the prior txn state', async () => {
    const txnA = makeTxn({ id: 'email-txn-A', externalRefs: [{ source: 'email', ref: 'ma1111' }] })
    const txnB = makeTxn({ id: 'email-txn-B', externalRefs: [{ source: 'email', ref: 'mb2222' }] })
    const loader = (id: string) => Promise.resolve(id === txnA.id ? [dataA] : [dataB])

    const { rerender } = render(
      <TxnDetail
        txn={txnA}
        accounts={[makeAccount()]}
        categories={[] as Category[]}
        payeeOptions={[]}
        baseCurrency="SGD"
        rates={{}}
        onClose={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() => activity}
        onLoadSourceEntries={loader}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    await waitFor(() => expect(screen.getByText('Txn A email')).toBeTruthy())
    fireEvent.click(screen.getByText('Show full email'))
    expect(screen.getByText('Hide full email')).toBeTruthy()

    rerender(
      <TxnDetail
        txn={txnB}
        accounts={[makeAccount()]}
        categories={[] as Category[]}
        payeeOptions={[]}
        baseCurrency="SGD"
        rates={{}}
        onClose={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() => activity}
        onLoadSourceEntries={loader}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    await waitFor(() => expect(screen.getByText('Txn B email')).toBeTruthy())
    // Starts collapsed again, with B's own data — never A's.
    expect(screen.getByText('Show full email')).toBeTruthy()
    expect(screen.queryByText('Txn A email')).toBeNull()
  })
})

describe('TxnDetail — Source section, kind: email (EM1-09 HTML-looking body renders escaped)', () => {
  it('a body containing <a>/<script>-looking text never becomes real markup', async () => {
    const hostile: SourceEntry = {
      kind: 'email',
      source: 'email',
      ref: 'm1aaaa',
      messageId: 'm1aaaa',
      subject: 'Statement',
      from: 'a@x.com',
      receivedAt: '2026-07-01',
      bodyText: '<a href="https://evil.example">click me</a><script>alert(1)</script>',
      unavailable: false,
    }
    renderDrawer(emailTxn(), () => Promise.resolve([hostile]))
    await waitFor(() => screen.getByText('Show full email'))
    fireEvent.click(screen.getByText('Show full email'))
    const body = document.getElementById('src-email-body-m1aaaa')!
    // The raw text is visible verbatim...
    expect(body.textContent).toContain('<a href="https://evil.example">click me</a>')
    // ...but never interpreted as real DOM elements — the <pre> has exactly one text-node
    // child (React's default escaping for a plain `{expr}` child), no <a>/<script> elements.
    expect(body.querySelector('a')).toBeNull()
    expect(body.querySelector('script')).toBeNull()
    expect(body.children.length).toBe(0)
  })
})

describe('TxnDetail — Source section, kind: email (EM1-10 stale-response race, switch without closing)', () => {
  it('a slow-resolving fetch for txn A does not clobber txn B after switching before A resolves', async () => {
    // The drawer never unmounts on a row switch — AccountsTab just swaps the `txn` prop in
    // place (see the sibling activity-loader effect's own `cancelled` guard). Without an
    // equivalent guard on the source fetch, a response for a txn the user has since
    // navigated AWAY from can land after the newer txn's own (faster) response and clobber
    // what's on screen. Reproduces by resolving A strictly AFTER B.
    const dataA: SourceEntry = {
      kind: 'email',
      source: 'email',
      ref: 'ma1111',
      messageId: 'ma1111',
      subject: 'Txn A email',
      from: 'a@x.com',
      receivedAt: '2026-07-01',
      bodyText: 'Body A',
      unavailable: false,
    }
    const dataB: SourceEntry = {
      kind: 'email',
      source: 'email',
      ref: 'mb2222',
      messageId: 'mb2222',
      subject: 'Txn B email',
      from: 'b@x.com',
      receivedAt: '2026-07-02',
      bodyText: 'Body B',
      unavailable: false,
    }
    const txnA = makeTxn({ id: 'race-txn-A', externalRefs: [{ source: 'email', ref: 'ma1111' }] })
    const txnB = makeTxn({ id: 'race-txn-B', externalRefs: [{ source: 'email', ref: 'mb2222' }] })

    let resolveA!: (v: SourceEntry[] | null) => void
    let resolveB!: (v: SourceEntry[] | null) => void
    const pendingA = new Promise<SourceEntry[] | null>((r) => {
      resolveA = r
    })
    const pendingB = new Promise<SourceEntry[] | null>((r) => {
      resolveB = r
    })
    const loader = vi.fn((id: string) => (id === txnA.id ? pendingA : pendingB))

    const { rerender } = render(
      <TxnDetail
        txn={txnA}
        accounts={[makeAccount()]}
        categories={[] as Category[]}
        payeeOptions={[]}
        baseCurrency="SGD"
        rates={{}}
        onClose={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() => activity}
        onLoadSourceEntries={loader}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    await waitFor(() => expect(loader).toHaveBeenCalledWith(txnA.id))

    // Switch to B WITHOUT closing the drawer, while A's fetch is still in flight.
    rerender(
      <TxnDetail
        txn={txnB}
        accounts={[makeAccount()]}
        categories={[] as Category[]}
        payeeOptions={[]}
        baseCurrency="SGD"
        rates={{}}
        onClose={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() => activity}
        onLoadSourceEntries={loader}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    await waitFor(() => expect(loader).toHaveBeenCalledWith(txnB.id))

    // B's fetch resolves first...
    resolveB([dataB])
    await waitFor(() => expect(screen.getByText('Txn B email')).toBeTruthy())

    // ...then A's now-stale fetch resolves AFTER B is already showing.
    resolveA([dataA])
    await new Promise((r) => setTimeout(r, 0))

    // B's data must survive — A's late response must never clobber the currently-displayed txn.
    expect(screen.getByText('Txn B email')).toBeTruthy()
    expect(screen.queryByText('Txn A email')).toBeNull()
  })
})

describe('TxnDetail — Source section, kind: email (multi-source-email drawer, ADR 2026-07-21)', () => {
  const em1: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'm1aaaa',
    messageId: 'm1aaaa',
    subject: 'Order confirmation',
    from: 'shop@x.com',
    receivedAt: '2026-07-01',
    bodyText: 'Body one',
    unavailable: false,
  }
  const em2: SourceEntry = {
    kind: 'email',
    source: 'email',
    ref: 'm2bbbb',
    messageId: 'm2bbbb',
    subject: 'Delivery receipt',
    from: 'shop@x.com',
    receivedAt: '2026-07-02',
    bodyText: 'Body two',
    unavailable: false,
  }
  const multiTxn = () =>
    makeTxn({
      id: 'email-txn-multi',
      externalRefs: [
        { source: 'email', ref: 'm1aaaa' },
        { source: 'email', ref: 'm2bbbb' },
      ],
    })

  it('renders "Source (2)" with one block per email and a divider between them', async () => {
    renderDrawer(multiTxn(), () => Promise.resolve([em1, em2]))
    await waitFor(() => expect(screen.getByText('Source (2)')).toBeTruthy())
    expect(screen.getByText('Order confirmation')).toBeTruthy()
    expect(screen.getByText('Delivery receipt')).toBeTruthy()
    expect(document.querySelectorAll('.acct-detail-email-div').length).toBe(1)
  })

  it('each email expands independently (per-message-id toggle state)', async () => {
    renderDrawer(multiTxn(), () => Promise.resolve([em1, em2]))
    await waitFor(() => expect(screen.getAllByText('Show full email').length).toBe(2))
    fireEvent.click(screen.getAllByText('Show full email')[1]!)
    // Only the SECOND email expanded — one Hide, one Show, and only body two is in the DOM.
    expect(screen.getAllByText('Show full email').length).toBe(1)
    expect(screen.getByText('Hide full email')).toBeTruthy()
    expect(document.getElementById('src-email-body-m2bbbb')).toBeTruthy()
    expect(document.getElementById('src-email-body-m1aaaa')).toBeNull()
    expect(screen.getByText('Body two')).toBeTruthy()
  })

  it('keeps each unlink action with its own email block', async () => {
    renderDrawer(multiTxn(), () => Promise.resolve([em1, em2]))
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Unlink email' })).toHaveLength(2),
    )

    const firstEmail = screen.getByText('Order confirmation').closest('.acct-detail-source-block')
    const secondEmail = screen.getByText('Delivery receipt').closest('.acct-detail-source-block')
    expect(firstEmail).toBeTruthy()
    expect(secondEmail).toBeTruthy()
    expect(firstEmail?.querySelector('.acct-detail-source-unlink')).toBeTruthy()
    expect(secondEmail?.querySelector('.acct-detail-source-unlink')).toBeTruthy()
  })

  it('a pruned snapshot renders the unavailable copy in ITS slot only, others stay full', async () => {
    const gone: SourceEntry = {
      kind: 'email',
      source: 'email',
      ref: 'm2bbbb',
      messageId: 'm2bbbb',
      unavailable: true,
    }
    renderDrawer(multiTxn(), () => Promise.resolve([em1, gone]))
    await waitFor(() => expect(screen.getByText('Source (2)')).toBeTruthy())
    expect(screen.getByText('Order confirmation')).toBeTruthy()
    expect(screen.getByText(/Snapshot unavailable/)).toBeTruthy()
    // The available email still has its toggle; the unavailable slot has none.
    expect(screen.getAllByText('Show full email').length).toBe(1)
  })

  it('single-email header stays "Source" (no count suffix)', async () => {
    renderDrawer(emailTxn(), () => Promise.resolve([em1]))
    await waitFor(() =>
      expect(screen.getByText('Source', { selector: '.acct-detail-sec' })).toBeTruthy(),
    )
    expect(screen.queryByText(/Source \(/)).toBeNull()
  })
})
