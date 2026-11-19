// @vitest-environment jsdom
//
// Transfer-aware import review (owner complaint 2026-07-17: "the table is showing
// everything like just income/expense — there is transfer too!"): the pair table's three
// surfaces — the STATEMENT category cell, the YOUR LEDGER NOW line, and the AFTER IMPORT
// projection — render a transfer's identity (direction-aware counter-account, or both
// legs "Transfer · <from> → <to>") instead of pushing every row through the
// expense/income template with an "Uncategorized" category. ONE shared helper
// (importHelpers.transferLine) feeds all three; non-transfer rows stay byte-identical.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { transferLine, existingTxnLine, afterImportProjection } from './importHelpers'
import type { ImportPreviewDraft } from './importHelpers'
import type { Account, Category } from './types'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

const authFetch = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }))
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...(a as [])) }))

afterEach(() => {
  cleanup()
  authFetch.mockClear()
})

const accounts = [
  { id: 'rvb', name: 'Riverside Bank', kind: 'asset', type: 'cash', currency: 'SGD' },
  { id: 'pocket', name: 'PocketPay Wallet', kind: 'asset', type: 'wallet', currency: 'SGD' },
  { id: 'compass', name: 'Compass SGD', kind: 'asset', type: 'cash', currency: 'SGD' },
] as unknown as Account[]
const categories = [
  { id: 'cat_food', label: 'Food', color: '#f80', kind: 'expense' },
] as unknown as Category[]
const catMap = { cat_food: categories[0]! }

const base = () => ({
  statementId: 'st-1',
  importable: 1,
  duplicates: 0,
  skipped: [],
  total: 1,
  anomaly: {
    near_duplicate: 0,
    transfer_pair: 0,
    intra_batch_duplicate: 0,
    amount_anomaly: 0,
    flagged_rows: 0,
  },
})

const routedDraft = (over: Record<string, unknown> = {}) => ({
  date: '2026-06-01',
  payee: null,
  description: 'Ecom.EW.SD.POCKET.CashIn',
  amount: -99.51,
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  rowIndex: 0,
  external_refs: [{ source: 'csv', ref: 'x1' }],
  destId: 'rvb',
  destName: 'Riverside Bank',
  flags: [],
  routed: {
    rule_id: 'rule_1',
    from_account_id: 'rvb',
    from_account_name: 'Riverside Bank',
    to_account_id: 'pocket',
    to_account_name: 'PocketPay Wallet',
  },
  ...over,
})

async function openPreview(payload: unknown) {
  const onPreview = vi.fn().mockResolvedValue(payload)
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
  render(
    <ImportModal
      open
      accounts={accounts}
      defaultAccountId="rvb"
      categories={categories}
      catMap={catMap}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit as never}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
}

// ── Surface 1: the STATEMENT category cell of a routed-transfer draft ───────────────

describe('ImportModal — routed draft category cell', () => {
  it('an OUT routed row reads "Transfer → <counter>" and never "Uncategorized"', async () => {
    await openPreview({ ...base(), drafts: [routedDraft()] })
    await screen.findByText('Transfer → PocketPay Wallet')
    expect(screen.queryByText('Uncategorized')).toBeNull()
    // The identity cell and the disclosure's route note coexist: the closed row says WHERE it
    // books, the body says WHY. (A Lucius-pass row also gets the violet provenance strip —
    // covered in ImportModal.luciusPass.test.tsx; a saved routing RULE has no such strip.)
    const row = openRow(
      screen.getByText('Transfer → PocketPay Wallet').closest('.imp3-arow') as HTMLElement,
    )
    expect(
      within(row).getByText(/Books as transfer: Riverside Bank → PocketPay Wallet/),
    ).toBeTruthy()
  })

  it('an IN routed row points the other way: "Transfer ← <counter>"', async () => {
    await openPreview({
      ...base(),
      drafts: [
        routedDraft({
          amount: 200,
          kind: 'income',
          routed: {
            rule_id: 'rule_1',
            from_account_id: 'pocket',
            from_account_name: 'PocketPay Wallet',
            to_account_id: 'rvb',
            to_account_name: 'Riverside Bank',
          },
        }),
      ],
    })
    await screen.findByText('Transfer ← PocketPay Wallet')
    expect(screen.queryByText('Uncategorized')).toBeNull()
  })

  it('a transfer WITH a category (legal, owner 2026-07-03) shows both', async () => {
    await openPreview({ ...base(), drafts: [routedDraft({ category: 'cat_food' })] })
    await screen.findByText('Transfer → PocketPay Wallet • Food')
    expect(screen.queryByText('Uncategorized')).toBeNull()
  })

  it('a plain expense row still reads "Uncategorized" — the non-transfer template is untouched', async () => {
    await openPreview({ ...base(), drafts: [routedDraft({ routed: null })] })
    await screen.findByText('Uncategorized')
    expect(screen.queryByText(/Transfer →/)).toBeNull()
  })
})

// ── Surfaces 2+3: YOUR LEDGER NOW + AFTER IMPORT for an existing transfer ───────────

const echoFlag = (existing: Record<string, unknown>) => [
  {
    type: 'transfer_leg_echo',
    reason: 'already booked as a transfer',
    existing_id: 'tx-1',
    band: 'strong',
    existing,
  },
]

describe('ImportModal — existing-transfer pair row', () => {
  it('ledger-now + after render both legs "Transfer • <from> → <to>" (to = the statement side), no Uncategorized', async () => {
    await openPreview({
      ...base(),
      drafts: [
        {
          date: '2026-06-01',
          payee: null,
          description: 'Sunridge Bank transfer in - Nguyen Van A',
          amount: 99.51,
          currency: 'SGD',
          kind: 'income',
          category: null,
          dup: false,
          rowIndex: 0,
          external_refs: [{ source: 'csv', ref: 'x2' }],
          destId: 'rvb',
          destName: 'Riverside Bank',
          flags: echoFlag({
            note: 'Compass → Riverside top-up',
            kind: 'transfer',
            account_id: 'compass',
            amount: 99.51,
            currency: 'SGD',
            date: '2026-06-01',
            category: null,
            source: 'manual',
          }),
        },
      ],
    })
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    // Surface 3 — the whole ledger-now line, legs named from the wire's from-leg + the
    // statement side's own account. Date + amount dropped (owner, 2026-07-27) — the
    // .imp3-cmp-facts footer right below already carries both.
    within(row).getByText('— Transfer • Compass SGD → Riverside Bank • added by you')
    // Surface 2 — the AFTER card keeps the transfer identity + the linked ✓ status line.
    const after = row.querySelector('.imp3-after') as HTMLElement
    within(after).getByText('Transfer • Compass SGD → Riverside Bank')
    within(after).getByText('linked ✓')
    const ledgerNow = row.querySelectorAll('.imp-pair-side')[1] as HTMLElement
    expect(ledgerNow.textContent).not.toContain('Uncategorized')
    expect(after.textContent).not.toContain('Uncategorized')
  })

  it('a routed echo (existing from-leg IS the statement side) names the to-leg from the route — nothing "Uncategorized" in the row', async () => {
    await openPreview({
      ...base(),
      drafts: [
        routedDraft({
          flags: echoFlag({
            note: 'Riverside → PocketPay top-up',
            kind: 'transfer',
            account_id: 'rvb',
            amount: 99.51,
            currency: 'SGD',
            date: '2026-06-01',
            category: null,
            source: 'ai',
          }),
        }),
      ],
    })
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    // Date + amount dropped from this sentence (owner, 2026-07-27: "why do we show duplicated
    // information" — the .imp3-cmp-facts footer right below already shows both).
    within(row).getByText('— Transfer • Riverside Bank → PocketPay Wallet • via Lucius')
    expect(within(row).getAllByText('Transfer → PocketPay Wallet').length).toBeGreaterThan(0) // scan row + statement detail
    expect(row.textContent).not.toContain('Uncategorized')
  })

  it('an expense pair row is byte-identical to before — regression', async () => {
    await openPreview({
      ...base(),
      drafts: [
        {
          date: '2026-06-09',
          payee: 'Gopay',
          description: 'Gopay-Gojek',
          amount: -11.0,
          currency: 'SGD',
          kind: 'expense',
          category: null,
          dup: false,
          rowIndex: 0,
          external_refs: [{ source: 'csv', ref: 'x3' }],
          destId: 'rvb',
          flags: [
            {
              type: 'near_duplicate',
              reason: 'possible match',
              existing_id: 'tx-2',
              band: 'strong',
              existing: {
                note: 'Gopay-Gojek',
                amount: -11.0,
                currency: 'SGD',
                date: '2026-06-09',
                kind: 'expense',
                category: null,
                account_id: 'rvb',
                source: 'agent',
              },
            },
          ],
        },
      ],
    })
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    // Date + amount dropped from this sentence (owner, 2026-07-27) — same reasoning as above.
    within(row).getByText('— Riverside Bank • Uncategorized • via agent')
  })

  it('a row matched to an existing transfer (no d.routed of its own) names the counterpart from the matched entry', async () => {
    // A transfer_leg_echo/transfer_pair MATCH carries no `routed` on the draft itself (only
    // the matched existing entry knows it's a transfer), so the category chip used to fall back
    // to the bare word. It now names the counterpart from the existing entry's own account
    // fields, same as a Lucius-routed row.
    await openPreview({
      ...base(),
      drafts: [
        {
          date: '2026-06-13',
          payee: 'FX',
          description: 'FX conversion leg',
          amount: -200,
          currency: 'SGD',
          kind: 'expense',
          category: null,
          dup: false,
          rowIndex: 0,
          external_refs: [{ source: 'csv', ref: 'fx-echo' }],
          flags: [
            {
              type: 'transfer_leg_echo',
              reason: 'already booked as a transfer',
              existing_id: 'existing-transfer',
              band: 'strong',
              existing: {
                note: 'Compass→Riverside Bank',
                amount: -200,
                currency: 'SGD',
                date: '2026-06-13',
                kind: 'transfer',
                account_id: 'compass',
              },
            },
          ],
        },
      ],
    })
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = document.querySelector('.imp-pair-row') as HTMLElement
    within(row).getByText('Transfer ← Compass SGD')
  })
})

// ── The shared helper (pure) ────────────────────────────────────────────────────────

describe('transferLine', () => {
  it('compact draft form: direction-aware counter-account', () => {
    const routed = {
      rule_id: 'r',
      from_account_id: 'rvb',
      from_account_name: 'Riverside Bank',
      to_account_id: 'pocket',
      to_account_name: 'PocketPay Wallet',
    }
    expect(
      transferLine({ routed, amount: -5, category: null }, accounts, {}, { compact: true }),
    ).toBe('Transfer → PocketPay Wallet')
    expect(
      transferLine({ routed, amount: 5, category: null }, accounts, {}, { compact: true }),
    ).toBe('Transfer ← Riverside Bank')
  })

  it('legs draft form names both ends; category appends', () => {
    const routed = {
      rule_id: 'r',
      from_account_id: 'rvb',
      from_account_name: null,
      to_account_id: 'pocket',
      to_account_name: null,
    }
    expect(transferLine({ routed, amount: -5, category: null }, accounts, {}, {})).toBe(
      'Transfer • Riverside Bank → PocketPay Wallet',
    )
    expect(transferLine({ routed, amount: -5, category: 'cat_food' }, accounts, catMap, {})).toBe(
      'Transfer • Riverside Bank → PocketPay Wallet • Food',
    )
  })

  it('an unknown account id falls back to the raw id, never blank', () => {
    expect(
      transferLine(
        {
          routed: { rule_id: 'r', from_account_id: 'rvb', to_account_id: 'acct_gone' },
          amount: -5,
          category: null,
        },
        accounts,
        {},
        { compact: true },
      ),
    ).toBe('Transfer → acct_gone')
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'acct_gone', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb' },
      ),
    ).toBe('Transfer • acct_gone → Riverside Bank')
  })

  it('existing transfer: anchor names the to-leg; from == anchor degrades honestly (hint fills it when routed)', () => {
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'compass', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb' },
      ),
    ).toBe('Transfer • Compass SGD → Riverside Bank')
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'rvb', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb' },
      ),
    ).toBe('Transfer')
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'rvb', category: 'cat_food' },
        accounts,
        catMap,
        { anchorAccountId: 'rvb' },
      ),
    ).toBe('Transfer • Food')
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'rvb', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb', counterHint: 'pocket' },
      ),
    ).toBe('Transfer • Riverside Bank → PocketPay Wallet')
  })

  it('compact existing-transfer form: the from-leg names the counter when anchor is the to-leg, honestly degrades when anchor is the from-leg', () => {
    // A row LINKED to a matched existing transfer has no `d.routed` of its own, so the
    // compact category chip must name the counterpart from the MATCHED entry's own account
    // fields, the same way the legs form already does.
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'compass', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb', compact: true },
      ),
    ).toBe('Transfer ← Compass SGD')
    // Anchor IS the from-leg: the to-leg isn't nameable from the wire alone — degrades to the
    // bare word, never a fabricated arrow (same honesty as the legs form).
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'rvb', category: null },
        accounts,
        {},
        { anchorAccountId: 'rvb', compact: true },
      ),
    ).toBe('Transfer')
    // …unless a routed hint fills the to-leg (still honors category appends).
    expect(
      transferLine(
        { kind: 'transfer', account_id: 'rvb', category: 'cat_food' },
        accounts,
        catMap,
        { anchorAccountId: 'rvb', counterHint: 'pocket', compact: true },
      ),
    ).toBe('Transfer → PocketPay Wallet • Food')
  })

  it('a non-transfer subject returns null — callers keep their own template', () => {
    expect(
      transferLine({ kind: 'expense', account_id: 'rvb', category: null }, accounts, {}, {}),
    ).toBeNull()
    expect(transferLine({ amount: -5, category: 'cat_food' }, accounts, catMap, {})).toBeNull()
  })
})

// ── existingTxnLine + afterImportProjection transfer branches (pure) ────────────────

describe('existingTxnLine — transfer treatment', () => {
  const ex = {
    date: '2026-06-01',
    kind: 'transfer',
    account_id: 'compass',
    category: null,
    source: 'manual',
    amount: 99.51,
    currency: 'SGD',
  }

  it('replaces account+category tokens with the legs line', () => {
    expect(existingTxnLine(ex, accounts, {}, 'SGD', { anchorAccountId: 'rvb' })).toBe(
      '— 06/01 • Transfer • Compass SGD → Riverside Bank • added by you • S$99.51',
    )
  })

  it('degraded (counter-leg unknown): keeps the account token so the from side stays visible', () => {
    expect(
      existingTxnLine({ ...ex, account_id: 'rvb' }, accounts, {}, 'SGD', {
        anchorAccountId: 'rvb',
      }),
    ).toBe('— 06/01 • Riverside Bank • Transfer • added by you • S$99.51')
  })

  it('non-transfer output is byte-identical, with or without the new context arg', () => {
    const plain = {
      date: '2026-06-09',
      account_id: 'rvb',
      category: null,
      source: 'agent',
      amount: -11,
      currency: 'SGD',
    }
    const line = existingTxnLine(plain, accounts, {}, 'SGD')
    expect(line).toBe('— 06/09 • Riverside Bank • Uncategorized • via agent • -S$11.00')
    expect(existingTxnLine(plain, accounts, {}, 'SGD', { anchorAccountId: 'rvb' })).toBe(line)
  })
})

describe('afterImportProjection — transfer treatment', () => {
  const draft = {
    date: '2026-06-01',
    payee: null,
    description: 'Sunridge Bank transfer in',
    amount: 99.51,
    currency: 'SGD',
    category: null,
    kind: 'income',
  } as unknown as ImportPreviewDraft

  it('merge into a transfer target: the kept record carries the transfer identity, not "Uncategorized"', () => {
    const existing = {
      note: 'Top-up',
      date: '2026-06-01',
      amount: 99.51,
      currency: 'SGD',
      kind: 'transfer',
      account_id: 'compass',
      category: null,
    }
    const p = afterImportProjection({
      action: 'merge',
      draft,
      existing,
      flagType: 'transfer_leg_echo',
      catMap: {},
      accounts,
      anchorAccountId: 'rvb',
    })
    expect(p.records[0]!.category).toBe('Transfer • Compass SGD → Riverside Bank')
    expect(p.records[0]!.badges).toEqual(['linked ✓'])
  })

  it('uses the to-anchor amount and currency for a cross-currency transfer', () => {
    const existing = {
      note: 'USD→SGD',
      date: '2026-06-01',
      amount: 120,
      currency: 'USD',
      kind: 'transfer',
      account_id: 'compass',
      to_account_id: 'rvb',
      from_amount: 120,
      from_currency: 'USD',
      to_amount: 18.4,
      to_currency: 'SGD',
      category: null,
    }
    const toDraft = { ...draft, amount: 18.4, currency: 'SGD' } as ImportPreviewDraft
    const p = afterImportProjection({
      action: 'merge',
      draft: toDraft,
      existing,
      flagType: 'transfer_leg_echo',
      catMap: {},
      accounts,
      anchorAccountId: 'rvb',
    })
    expect(p.records[0]!.amount).toEqual({ value: 'S$18.40' })
  })

  it('uses the from-anchor amount and currency for a cross-currency transfer', () => {
    const existing = {
      note: 'USD→SGD',
      date: '2026-06-01',
      amount: 120,
      currency: 'USD',
      kind: 'transfer',
      account_id: 'compass',
      to_account_id: 'rvb',
      from_amount: 120,
      from_currency: 'USD',
      to_amount: 18.4,
      to_currency: 'SGD',
      category: null,
    }
    const fromDraft = { ...draft, amount: -120, currency: 'USD' } as ImportPreviewDraft
    const p = afterImportProjection({
      action: 'merge',
      draft: fromDraft,
      existing,
      flagType: 'transfer_leg_echo',
      catMap: {},
      accounts,
      anchorAccountId: 'compass',
    })
    expect(p.records[0]!.amount).toEqual({ value: '-$120.00' })
  })

  it('a NEW record for a routed draft books as a transfer — identity from the route, category appended when set', () => {
    const routed = routedDraft({ category: 'cat_food' }) as unknown as ImportPreviewDraft
    const existing = {
      note: 'Other row',
      date: '2026-06-02',
      amount: -5,
      currency: 'SGD',
      kind: 'expense',
      category: null,
    }
    const p = afterImportProjection({
      action: 'new',
      draft: routed,
      existing,
      catMap,
      accounts,
      anchorAccountId: 'rvb',
    })
    expect(p.records[1]!.category).toBe('Transfer • Riverside Bank → PocketPay Wallet • Food')
  })

  it('non-transfer records keep the "Uncategorized"/label fallback — regression', () => {
    const existing = {
      note: 'Gopay',
      date: '2026-06-09',
      amount: -11,
      currency: 'SGD',
      kind: 'expense',
      category: null,
    }
    const p = afterImportProjection({
      action: 'new',
      draft,
      existing,
      catMap: {},
      accounts,
      anchorAccountId: 'rvb',
    })
    expect(p.records[0]!.category).toBe('Uncategorized')
    expect(p.records[1]!.category).toBe('Uncategorized')
  })
})

describe('ImportModal — cross-currency transfer evidence', () => {
  it('shows the statement-account leg without a false drift in the row and After import', async () => {
    await openPreview({
      ...base(),
      drafts: [
        {
          ...routedDraft({
            payee: 'Cross-currency transfer',
            description: 'Cross-currency transfer',
            amount: 18.4,
            currency: 'SGD',
            kind: 'income',
            routed: undefined,
            flags: [
              {
                type: 'transfer_leg_echo',
                reason: 'same transfer',
                existing_id: 'fx-transfer',
                existing: {
                  note: 'USD→SGD',
                  date: '2026-06-01',
                  amount: 120,
                  currency: 'USD',
                  kind: 'transfer',
                  account_id: 'compass',
                  to_account_id: 'rvb',
                  from_amount: 120,
                  from_currency: 'USD',
                  to_amount: 18.4,
                  to_currency: 'SGD',
                  category: null,
                },
                band: 'strong',
              },
            ],
          }),
        },
      ],
    })
    const row = screen
      .getByRole('button', { name: /^Cross-currency transfer, Link/ })
      .closest('.imp-pair-row') as HTMLElement
    const evidence = row.querySelector('.imp3-arow-match-amt') as HTMLElement
    expect(evidence.textContent).toBe('S$18.40')
    expect(evidence.className).not.toContain('drift')

    fireEvent.click(
      within(row).getByRole('button', { name: /Cross-currency transfer.*Expand details/ }),
    )
    const after = row.querySelector('.imp3-after') as HTMLElement
    expect(within(after).getByText('S$18.40')).toBeTruthy()
    expect(within(after).queryByText('$120.00')).toBeNull()
  })
})
