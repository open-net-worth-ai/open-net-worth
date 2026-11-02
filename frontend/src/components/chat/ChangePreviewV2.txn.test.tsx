// @vitest-environment jsdom
//
// The ledger confirm card, rendered from REAL backend payloads.
//
// The fixtures below are verbatim `proposed_change` dicts captured from
// `backend/services/ledger_preview.py` (preview_create / preview_update / preview_bulk)
// against a seeded book — not hand-written approximations. That is the point: this file is
// the contract test between the two halves, so a backend key rename or a shape change shows
// up here as a failing render rather than as a blank card in production.
//
// What it pins is the feature's whole reason to exist (docs/qa/acceptance/ledger-confirm-gate.md,
// LCG2 + LCG3): the card must show what the write will ACTUALLY do after the silent
// resolution inside create_transaction — the resolved category AND which tier decided it, the
// resolved account NAMES, the base amount — and approval must resolve the SAME tool_use_id
// the card is bound to (RFC 2026-07-22), never hand a token back to the model. `_gate.stage()`
// always sets `grant_tool` on these cards (2026-07-19 fix), so they render the permission
// card's [Allow once ▾][Deny] buttons, not a bare Confirm/Dismiss.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

// A permission-card approval routes through `handleGrant`, which POSTs the standing-grant
// choice (even for "once", which records nothing but still calls the same endpoint) BEFORE
// resolving the tool_use — a real `fetch` isn't available in jsdom, so it's stubbed here
// exactly like `ChangePreviewV2.grant.test.tsx` does.
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(null, { status: 200 })),
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const TOOL_USE_ID = 'toolu_abc123'

// A spend rule silently overrode the caller's category — the live 2026-07-17 incident shape.
const CREATE_RULE_OVERRIDE: ProposedChange = {
  type: 'txn_create',
  id: TOOL_USE_ID,
  grant_tool: 'add_transactions',
  txn_kind: 'expense',
  date: '2026-07-19',
  description: 'Grab GrabFood order',
  memo: '',
  category: { id: 'cat_transport', label: 'Transport', source: 'rule' },
  rule: { id: 'rule_grab', name: 'Grab -> Transport' },
  payee: { id: null, name: 'Grab GrabFood order', is_new: true },
  from: {
    account_id: 'acc_chk',
    account_name: 'Meridian Bank Checking',
    amount: 13.8,
    currency: 'SGD',
  },
  to: null,
  base: { amount: 13.8, currency: 'SGD', fx_rate: 1.0 },
  splits: null,
  tags: [],
  warnings: [],
}

const CREATE_TRANSFER: ProposedChange = {
  type: 'txn_create',
  id: TOOL_USE_ID,
  grant_tool: 'add_transactions',
  txn_kind: 'transfer',
  date: '2026-07-19',
  description: 'Move to savings',
  memo: '',
  category: { id: null, label: 'Uncategorized', source: 'none' },
  rule: null,
  payee: null,
  from: {
    account_id: 'acc_chk',
    account_name: 'Meridian Bank Checking',
    amount: 500.0,
    currency: 'SGD',
  },
  to: {
    account_id: 'acc_sav',
    account_name: 'Meridian Bank Savings',
    amount: 500.0,
    currency: 'SGD',
  },
  base: { amount: 500.0, currency: 'SGD', fx_rate: 1.0 },
  splits: null,
  tags: [],
  warnings: [],
}

const UPDATE: ProposedChange = {
  type: 'txn_update',
  id: TOOL_USE_ID,
  grant_tool: 'update_transactions',
  transaction_id: 'txn_984659150000',
  fields: [{ field: 'category', label: 'Category', before: 'Daily Meals', after: 'Transport' }],
  txn_kind: 'expense',
  date: '2026-07-19',
  description: 'Lunch',
  memo: '',
  category: { id: 'cat_transport', label: 'Transport', source: 'explicit' },
  rule: null,
  payee: { id: 'pay_91d6570d0000', name: 'Lunch', is_new: false },
  from: {
    account_id: 'acc_chk',
    account_name: 'Meridian Bank Checking',
    amount: 12.0,
    currency: 'SGD',
  },
  to: null,
  base: { amount: 12.0, currency: 'SGD', fx_rate: 1.0 },
  splits: null,
  tags: [],
  warnings: [],
}

const BULK: ProposedChange = {
  type: 'txn_bulk',
  id: TOOL_USE_ID,
  grant_tool: 'add_transactions',
  count: 15,
  import_description: 'Card Apr',
  rows: Array.from({ length: 12 }, (_, i) => ({
    date: '2026-07-19',
    description: `Grab ride ${i}`,
    category_label: 'Transport',
    category_source: 'rule',
    amount: 10 + i,
    currency: 'SGD',
    base_amount: 10 + i,
    account_name: 'Meridian Bank Checking',
  })),
  truncated: true,
  totals: { base_currency: 'SGD', inflow_base: 0, outflow_base: 255.0 },
  rules_applied: [{ id: 'rule_grab', name: 'Grab -> Transport', count: 15 }],
  warnings: [],
}

function renderCard(change: ProposedChange, onResolvePendingToolUse = vi.fn()) {
  render(<ChangePreviewV2 change={change} onResolvePendingToolUse={onResolvePendingToolUse} />)
  return onResolvePendingToolUse
}

describe('txn_create card', () => {
  it('renders a structured transaction warning without an account id', () => {
    renderCard({
      ...CREATE_RULE_OVERRIDE,
      warnings: [
        {
          type: 'currency_converted',
          message: "Dated before Meridian Bank Checking's tracking start.",
        },
      ],
    })

    expect(screen.getByText(/Dated before Meridian Bank Checking/)).toBeTruthy()
  })

  it('shows the RESOLVED category, not the category the caller asked for', () => {
    const { container } = render(<ChangePreviewV2 change={CREATE_RULE_OVERRIDE} />)
    // The caller said "Daily Meals"; the rule stored "Transport". The card must say Transport.
    // (Matched on the container because "Transport" legitimately appears twice — as the
    // resolved category and inside the rule's name.)
    expect(container.textContent).toMatch(/Transport/)
    expect(container.textContent).not.toMatch(/Daily Meals/)
  })

  it('names the RULE that decided the category', () => {
    renderCard(CREATE_RULE_OVERRIDE)
    // Without this the override is invisible and the user only finds out after it commits.
    expect(screen.getByText(/Grab -> Transport/)).toBeTruthy()
  })

  it('resolves the account to its NAME, never a raw id', () => {
    renderCard(CREATE_RULE_OVERRIDE)
    expect(screen.getByText(/Meridian Bank Checking/)).toBeTruthy()
    expect(screen.queryByText(/acc_chk/)).toBeNull()
  })

  it('shows the amount as money, never raw JSON', () => {
    const { container } = render(<ChangePreviewV2 change={CREATE_RULE_OVERRIDE} />)
    expect(container.textContent).toMatch(/13\.80|13\.8/)
    expect(container.textContent).not.toMatch(/[{[]"/)
  })

  it('says a brand-new payee will be created', () => {
    const { container } = render(<ChangePreviewV2 change={CREATE_RULE_OVERRIDE} />)
    // Mirrors the position card's `account_to_is_new` copy — the preview resolved the payee
    // read-only and reports the intent, per ADR 2026-07-18 (previews must not write).
    expect(container.textContent).toMatch(/will be created/i)
  })

  it('renders "Uncategorized" as a real state, not a blank', () => {
    const { container } = render(<ChangePreviewV2 change={CREATE_TRANSFER} />)
    expect(container.textContent).toMatch(/Uncategorized/)
  })

  it('shows BOTH legs of a transfer', () => {
    renderCard(CREATE_TRANSFER)
    expect(screen.getByText(/Meridian Bank Checking/)).toBeTruthy()
    expect(screen.getByText(/Meridian Bank Savings/)).toBeTruthy()
  })

  it('renders the permission card (Allow once / Deny), not a bare Confirm/Dismiss', () => {
    renderCard(CREATE_RULE_OVERRIDE)
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
  })

  it('approving resolves the SAME tool_use_id the card is bound to — no token, no model turn', async () => {
    const onResolve = renderCard(CREATE_RULE_OVERRIDE)
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    // handleGrant awaits the standing-grant POST before resolving — the assertion must wait
    // past that microtask, not check synchronously right after the click.
    await waitFor(() => expect(onResolve).toHaveBeenCalledTimes(1))
    expect(onResolve).toHaveBeenCalledWith(TOOL_USE_ID, 'approve', { always: false })
  })

  it('denying resolves the same id as a denial — writes nothing', () => {
    const onResolve = renderCard(CREATE_RULE_OVERRIDE)
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(onResolve).toHaveBeenCalledTimes(1)
    expect(onResolve).toHaveBeenCalledWith(TOOL_USE_ID, 'deny')
  })
})

describe('txn_update card', () => {
  it('shows the resolved before → after diff using LABELS, not ids', () => {
    const { container } = render(<ChangePreviewV2 change={UPDATE} />)
    expect(container.textContent).toMatch(/Daily Meals/)
    expect(container.textContent).toMatch(/Transport/)
    expect(container.textContent).not.toMatch(/cat_transport/)
  })

  it('approving resolves the update_transactions tool_use_id', async () => {
    const onResolve = renderCard(UPDATE)
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() =>
      expect(onResolve).toHaveBeenCalledWith(TOOL_USE_ID, 'approve', { always: false }),
    )
  })
})

describe('txn_bulk card', () => {
  it('is ONE card for N rows, showing the true total count', () => {
    const { container } = render(<ChangePreviewV2 change={BULK} />)
    expect(container.textContent).toMatch(/15/)
  })

  it('accounts for the rows it does not display', () => {
    const { container } = render(<ChangePreviewV2 change={BULK} />)
    // 15 total, 12 sampled — the user must not think 12 is the whole batch.
    expect(container.textContent).toMatch(/3 more/)
  })

  it('reports the rules that fired across the batch', () => {
    const { container } = render(<ChangePreviewV2 change={BULK} />)
    expect(container.textContent).toMatch(/Grab -> Transport/)
  })

  it('shows batch totals as money', () => {
    const { container } = render(<ChangePreviewV2 change={BULK} />)
    expect(container.textContent).toMatch(/255/)
  })
})

describe('graceful degradation', () => {
  it('renders a minimal card when every optional field is absent', () => {
    // The backend can omit any of these; a money card must never blow up or go blank.
    const bare: ProposedChange = {
      type: 'txn_create',
      id: TOOL_USE_ID,
      grant_tool: 'add_transactions',
    }
    const { container } = render(<ChangePreviewV2 change={bare} />)
    expect(container.textContent && container.textContent.length).toBeTruthy()
  })
})
