// @vitest-environment jsdom
//
// Regression: the per-payee "Apply category to existing transactions" and
// "Apply rules to existing transactions" buttons only set a local success
// toast — they never called the backend (POST /api/payees/{id}/apply-category
// and /apply-rules), so clicking them silently did nothing while claiming
// success. Both buttons must invoke their onApplyCategory/onApplyPayeeRules
// callback with the selected payee's id.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'

const authFetch = vi.hoisted(() =>
  vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    Promise.reject(new Error('offline')),
  ),
)
vi.mock('../../../api/client', () => ({ authFetch }))

import { PayeesModal } from './PayeesModal'
import type { Category, Payee } from './types'

afterEach(cleanup)

const categories: Category[] = [
  {
    id: 'drinks_nightlife',
    label: 'Drinks & Nightlife',
    color: '#e0a032',
    kind: 'expense',
    group: 'Wants',
  },
]

function makePayee(over: Partial<Payee> = {}): Payee {
  return {
    id: 'pay_1',
    name: 'Bar Example',
    aliases: ['bar example hanoi'],
    defaultCategories: { expense: 'drinks_nightlife' },
    autocategorize: true,
    hidden: false,
    txnCount: 20,
    lastUsed: null,
    rules: [{ match_type: 'contains', value: 'example' }],
    ...over,
  }
}

function noop() {}

function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

describe('PayeesModal — mobile list/detail navigation', () => {
  it('shows one full-width state at a time and returns to the preserved list', () => {
    const restore = mockMobile(true)
    try {
      const first = makePayee()
      const second = makePayee({ id: 'pay_2', name: 'Grab', txnCount: 177 })
      render(
        <PayeesModal
          open
          payees={[first, second]}
          categories={categories}
          onClose={noop}
          onChange={noop}
          onMerge={noop}
          onView={noop}
          onAdd={() => ''}
          onDelete={noop}
        />,
      )

      const body = document.querySelector('.pm-body')!
      expect(body.classList.contains('pm-mobile--list')).toBe(true)
      expect(document.querySelector('.pm-main')?.getAttribute('aria-hidden')).toBe('true')
      expect(screen.getByText('Uncategorized')).toBeTruthy()

      fireEvent.click(screen.getByText('Grab', { selector: '.pm-item-nm' }))
      expect(body.classList.contains('pm-mobile--detail')).toBe(true)
      expect(document.querySelector('.pm-listcol')?.getAttribute('aria-hidden')).toBe('true')
      expect(screen.getByLabelText('Back to payees')).toBeTruthy()

      fireEvent.click(screen.getByLabelText('Back to payees'))
      expect(body.classList.contains('pm-mobile--list')).toBe(true)
      expect(document.querySelector('.pm-main')?.getAttribute('aria-hidden')).toBe('true')
    } finally {
      restore()
    }
  })

  it('leaves the desktop two-pane manager unchanged', () => {
    const restore = mockMobile(false)
    try {
      render(
        <PayeesModal
          open
          payees={[makePayee()]}
          categories={categories}
          onClose={noop}
          onChange={noop}
          onMerge={noop}
          onView={noop}
          onAdd={() => ''}
          onDelete={noop}
        />,
      )
      const body = document.querySelector('.pm-body')!
      expect(body.classList.contains('pm-mobile')).toBe(false)
      expect(document.querySelector('.pm-listcol')?.hasAttribute('aria-hidden')).toBe(false)
      expect(document.querySelector('.pm-main')?.hasAttribute('aria-hidden')).toBe(false)
    } finally {
      restore()
    }
  })
})

describe('PayeesModal — apply-to-existing wiring', () => {
  it('calls onApplyCategory with the payee id when the category button is confirmed', () => {
    const onApplyCategory = vi.fn()
    const payee = makePayee()
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
        onApplyCategory={onApplyCategory}
      />,
    )

    fireEvent.click(screen.getByText('Apply defaults to existing transactions'))
    fireEvent.click(screen.getByText('Apply defaults'))

    expect(onApplyCategory).toHaveBeenCalledWith('pay_1')
  })

  it('renders a default-category picker per transaction kind', () => {
    const payee = makePayee()
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )
    expect(screen.getByText('Default category by kind')).toBeTruthy()
    for (const label of ['Expense', 'Income', 'Transfer', 'Investment']) {
      expect(screen.getByText(label, { selector: '.pm-kindlabel' })).toBeTruthy()
    }
  })

  it('calls onApplyPayeeRules with the payee id when the rules button is confirmed', () => {
    const onApplyPayeeRules = vi.fn()
    const payee = makePayee()
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
        onApplyPayeeRules={onApplyPayeeRules}
      />,
    )

    fireEvent.click(screen.getByText('Apply rules to existing transactions'))
    fireEvent.click(screen.getByText('Apply rules'))

    expect(onApplyPayeeRules).toHaveBeenCalledWith('pay_1')
  })
})

describe('PayeesModal — last-used display', () => {
  // Regression: the editor header hardcoded fmtDate(undefined)||'Never used', so a payee
  // with a real lastUsed date (and a nonzero txnCount) still showed "Never used".
  it('shows the last-used date instead of "Never used" when the payee has transactions', async () => {
    const payee = makePayee({ txnCount: 146, lastUsed: '2026-07-20' })
    serveUsage(payee)
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )
    expect(await screen.findByText(/Last used/)).toBeTruthy()
    expect(screen.queryByText('Never used')).toBeNull()
  })

  it('shows "Never used" for a payee with no transactions', async () => {
    const payee = makePayee({ txnCount: 0, lastUsed: null })
    serveUsage(payee)
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )
    expect(await screen.findByText('Never used')).toBeTruthy()
  })

  // Regression (owner report 2026-10-26): on a busy machine the usage read took ~20s, and
  // every payee read "0 · Never used" meanwhile — with the no-reassign delete on offer.
  it('shows counting — never zero or "Never used" — and holds delete until usage lands', () => {
    authFetch.mockReturnValueOnce(new Promise(() => {}))
    render(
      <PayeesModal
        open
        payees={[makePayee({ txnCount: 0, lastUsed: null })]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )
    expect(screen.queryByText('Never used')).toBeNull()
    expect(screen.getAllByText('Counting transactions…').length).toBeGreaterThan(0)
    const del = screen
      .getByText('Delete payee', { selector: 'button, button *' })
      .closest('button')!
    expect(del.disabled).toBe(true)
  })
})

function serveUsage(p: Payee) {
  authFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({ payees: [{ id: p.id, txn_count: p.txnCount, last_used: p.lastUsed }] }),
  } as unknown as Response)
}

describe('PayeesModal — auto-matched descriptions', () => {
  // Regression: aliasesFor() used to synthesize chips from the payee NAME instead
  // of rendering the real stored aliases, so a merge that folded in extra aliases
  // (e.g. "golden dragon") never showed them. All stored aliases must render.
  it('renders every real stored alias, not name-derived tokens', () => {
    const payee = makePayee({
      name: 'Sunshine Grill',
      aliases: ['sunshine grill', 'qpay sgrl vcc3', 'golden dragon'],
    })
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )
    for (const alias of ['sunshine grill', 'qpay sgrl vcc3', 'golden dragon']) {
      expect(screen.getByText(alias, { selector: '.pm-chip' })).toBeTruthy()
    }
  })

  it('calls onRemoveAlias with the alias when its × is clicked', () => {
    const onRemoveAlias = vi.fn()
    const payee = makePayee({
      name: 'Sunshine Grill',
      aliases: ['sunshine grill', 'golden dragon'],
    })
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
        onRemoveAlias={onRemoveAlias}
      />,
    )
    fireEvent.click(screen.getByLabelText('Remove auto-match golden dragon'))
    expect(onRemoveAlias).toHaveBeenCalledWith('pay_1', 'golden dragon')
  })

  it('shows the name fallback with NO delete control when a payee has no stored aliases', () => {
    const payee = makePayee({ name: 'Sunshine Grill', aliases: [] })
    render(
      <PayeesModal
        open
        payees={[payee]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
        onRemoveAlias={noop}
      />,
    )
    expect(screen.getByText('sunshine grill', { selector: '.pm-chip' })).toBeTruthy()
    expect(screen.queryByLabelText('Remove auto-match sunshine grill')).toBeNull()
  })
})

describe('PayeesModal — merge confirmation copy', () => {
  it('tells the user renaming rules (not just aliases) move to the target payee', () => {
    const payee = makePayee()
    const target = makePayee({ id: 'pay_2', name: 'Target Payee', txnCount: 5, rules: [] })
    render(
      <PayeesModal
        open
        payees={[payee, target]}
        categories={categories}
        onClose={noop}
        onChange={noop}
        onMerge={noop}
        onView={noop}
        onAdd={() => ''}
        onDelete={noop}
      />,
    )

    // Static section hint.
    expect(
      screen.getByText(/Folds all aliases and renaming rules from this payee into the target/),
    ).toBeTruthy()

    // Pick the merge target, then confirm — the dialog body should say the same.
    fireEvent.click(screen.getByText('Pick target payee…'))
    fireEvent.mouseDown(screen.getByText('Target Payee (5 txns)'))
    fireEvent.click(screen.getByText('Merge'))

    expect(
      screen.getByText(/aliases, renaming rules, and 20 transactions will move to Target Payee/),
    ).toBeTruthy()
  })
})
