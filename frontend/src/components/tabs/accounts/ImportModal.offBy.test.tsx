// @vitest-environment jsdom
//
// Off-by drift surfaces (owner motivation 2026-07-17, verbatim: "if there is mismatch, it
// will cause users hard time — i quit ynab because of a mismatch that took me entire 5
// hours to figure out"): the verdict's "⚠ Off by X — N kept at your original figures"
// must make every drifted link findable in seconds. Three surfaces, ONE predicate
// (importHelpers.linkedAmountDrift, consumed by amountsVerdict itself): a per-row amber
// marker next to the action segment, a transient Off-by filter pill in the chip row, and
// the verdict line itself as a click-through into that filter. The counting cross-walk
// extends: pill count == verdict kept == rendered markers, live across action flips.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
})

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account
const domDraftOrder = () =>
  Array.from(document.querySelectorAll('[data-draft-index]')).map((el) =>
    el.getAttribute('data-draft-index'),
  )
// a row handed back for interaction is OPENED first: its override controls live in the
// disclosure (handoff: tapping a row is only ever for overriding)
const draftEl = (i: number) =>
  openRow(document.querySelector(`[data-draft-index="${i}"]`) as HTMLElement)
const pillEl = () => document.querySelector('.imp3-filter button.offby') as HTMLElement | null
const verdictKeptLine = () =>
  screen
    .getByText(/off the statement|kept at your original figure/)
    .closest('.imp-verdict-line') as HTMLElement
// The verdict panel collapses to one summary row by default; expand it once per test so the
// per-question detail lines are queryable.
const openVerdict = () => {
  const btn = document.querySelector('.imp3-vsummary') as HTMLElement | null
  if (btn && btn.getAttribute('aria-expanded') === 'false') fireEvent.click(btn)
}

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
  const utils = render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  return utils
}

const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

// Two drifted review matches (default skip → kept at yours), one clean strong match, one
// plain new row — >3 drafts so the chip row (and its transient pill) renders.
// Deltas: (−150.00 − −148.30) + (−25.00 − −20.00) = −6.70 → "Off by S$6.70 — 2 kept".
function driftBookPreview() {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-05',
        payee: 'Netflix',
        description: 'NETFLIX',
        amount: -148.3,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:d1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'possible',
            existing_id: 'e-d1',
            existing: {
              note: 'Netflix est',
              amount: -150.0,
              currency: 'SGD',
              date: '2026-06-05',
              kind: 'expense',
            },
            band: 'review',
          },
        ],
      },
      {
        ...base,
        date: '2026-06-06',
        payee: 'Gym',
        description: 'GYM',
        amount: -20,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:d2' }],
        rowIndex: 1,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'possible',
            existing_id: 'e-d2',
            existing: {
              note: 'Gym est',
              amount: -25.0,
              currency: 'SGD',
              date: '2026-06-06',
              kind: 'expense',
            },
            band: 'review',
          },
        ],
      },
      {
        ...base,
        date: '2026-06-07',
        payee: 'Grab',
        description: 'GRAB',
        amount: -8.2,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:d3' }],
        rowIndex: 2,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'sure',
            existing_id: 'e-d3',
            existing: {
              note: 'Grab ride',
              amount: -8.2,
              currency: 'SGD',
              date: '2026-06-07',
              kind: 'expense',
            },
            band: 'strong',
          },
        ],
      },
      {
        ...base,
        date: '2026-06-08',
        payee: 'Lazada',
        description: 'LAZADA',
        amount: -6.89,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:d4' }],
        rowIndex: 3,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 0,
    skipped: [],
    total: 4,
    anomaly: {
      near_duplicate: 3,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 3,
    },
  }
}
const openDriftBook = async () => {
  // Netflix and Gym (review band, confident matches) are auto-decided the moment the preview
  // loads (owner, 2026-07-27) — with this fixture's skip default that's immediately "Left
  // out — kept at your original figures", no interaction needed to get there anymore (the
  // 1/2/3 keyboard shortcut queue this used to drive is gone — owner, 2026-07-27).
  const view = await openWith(driftBookPreview(), { importReviewDefault: 'skip' })
  await screen.findByText('LAZADA')
  openVerdict()
  return view
}

// Zero drift: a strong match adopting the identical settled figure + three plain rows.
function cleanBookPreview() {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-05',
        payee: 'Grab',
        description: 'GRAB',
        amount: -8.2,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:c1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'sure',
            existing_id: 'e-c1',
            existing: {
              note: 'Grab ride',
              amount: -8.2,
              currency: 'SGD',
              date: '2026-06-05',
              kind: 'expense',
            },
            band: 'strong',
          },
        ],
      },
      {
        ...base,
        date: '2026-06-06',
        payee: 'A',
        description: 'AA',
        amount: -1,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:c2' }],
        rowIndex: 1,
        flags: [],
      },
      {
        ...base,
        date: '2026-06-07',
        payee: 'B',
        description: 'BB',
        amount: -2,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:c3' }],
        rowIndex: 2,
        flags: [],
      },
      {
        ...base,
        date: '2026-06-08',
        payee: 'C',
        description: 'CC',
        amount: -3,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:c4' }],
        rowIndex: 3,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 0,
    skipped: [],
    total: 4,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

describe('ImportModal — per-row Off-by markers', () => {
  it('each drifted row carries its own amber marker + honest skip tooltip; clean rows carry none', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    const markers = Array.from(document.querySelectorAll('.imp-offby'))
    expect(markers.map((m) => m.textContent)).toEqual(['Off by S$1.70', 'Off by S$5.00'])
    // The SKIPPED-row honesty tooltip: yours stays; the statement figure is not adopted.
    expect(markers[0]!.getAttribute('title')).toBe(
      'Skipped — yours stays -S$150.00; the statement’s -S$148.30 is not adopted.',
    )
    // The adopting strong match and the plain new row carry no marker.
    expect(draftEl(2).querySelector('.imp-offby')).toBeNull()
    expect(draftEl(3).querySelector('.imp-offby')).toBeNull()
  })

  it('a matched FX-pair leg (plain-row rendering) shows the marker on its match sub-line too', async () => {
    const p = {
      drafts: [
        {
          ...base,
          date: '2026-06-10',
          payee: 'Compass',
          description: 'COMPASS OUT',
          amount: -300,
          external_refs: [{ source: 'pdf', ref: 'ref:a1:fx1' }],
          rowIndex: 0,
          pair: { role: 'out', partner_index: 99 },
          flags: [
            {
              type: 'near_duplicate',
              reason: 'possible',
              existing_id: 'e-fx',
              existing: {
                note: 'Compass est',
                amount: -305,
                currency: 'SGD',
                date: '2026-06-10',
                kind: 'expense',
              },
              band: 'review',
            },
          ],
        },
        {
          ...base,
          date: '2026-06-11',
          payee: 'Plain',
          description: 'PLAIN',
          amount: -1,
          external_refs: [{ source: 'pdf', ref: 'ref:a1:fx2' }],
          rowIndex: 1,
          flags: [],
        },
      ],
      importable: 2,
      duplicates: 0,
      skipped: [],
      total: 2,
      anomaly: {
        near_duplicate: 1,
        transfer_pair: 0,
        intra_batch_duplicate: 0,
        amount_anomaly: 0,
        flagged_rows: 1,
      },
    }
    await openWith(p, { importReviewDefault: 'skip' })
    await screen.findByText('PLAIN')
    const tr = draftEl(0)
    expect(tr.tagName).toBe('ARTICLE') // handoff disclosure row, not a matched comparison card
    // draftEl opens the row — the collapsed match line (and its marker) is superseded by the
    // expanded detail's own copy once open, not absent.
    expect((tr.querySelector('.imp-offby') as HTMLElement).textContent).toBe('Off by S$5.00')
  })
})

describe('ImportModal — explicit FX-pair decisions', () => {
  const alreadyBookedPairPreview = () => ({
    statementId: 'st-fx-booked',
    multi: true,
    drafts: [
      {
        ...base,
        date: '2026-06-10',
        payee: 'FX debit',
        description: 'FX OUT',
        amount: -100,
        external_refs: [{ source: 'pdf', ref: 'fx-booked-out' }],
        rowIndex: 0,
        pair: { role: 'out', partner_index: 1 },
        flags: [
          {
            type: 'transfer_leg_echo',
            reason: 'already booked as a transfer',
            existing_id: 'tx-fx',
            band: 'strong',
            existing: {
              note: 'Booked FX transfer',
              date: '2026-06-10',
              amount: 100,
              currency: 'SGD',
              kind: 'transfer',
              account_id: 'a1',
              from_account_id: 'a1',
              to_account_id: 'a2',
              from_amount: 100,
              to_amount: 102,
              from_currency: 'SGD',
              to_currency: 'SGD',
            },
          },
        ],
      },
      {
        ...base,
        date: '2026-06-10',
        payee: 'FX credit',
        description: 'FX IN',
        amount: 102,
        kind: 'income',
        external_refs: [{ source: 'pdf', ref: 'fx-booked-in' }],
        rowIndex: 1,
        pair: { role: 'in', partner_index: 0 },
        flags: [],
      },
    ],
    importable: 2,
    duplicates: 0,
    skipped: [],
    total: 2,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 1,
      transfer_leg_echo: 1,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  })

  it('an ordinary OUT match cannot hide its IN partner as folded into a transfer', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(
      {
        statementId: 'st-fx-ordinary-out-match',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [
              {
                type: 'near_duplicate',
                reason: 'ordinary expense match',
                existing_id: 'expense-1',
                existing: {
                  note: 'Ordinary expense',
                  amount: -100,
                  currency: 'SGD',
                  date: '2026-06-10',
                  kind: 'expense',
                },
                band: 'strong',
              },
            ],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 1,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 1,
        },
      },
      { onDecisionCommit },
    )

    await screen.findAllByText('FX debit')
    const incoming = draftEl(1)
    expect(
      within(incoming).queryByText('Folded into transfer', { selector: '.imp3-dchip' }),
    ).toBeNull()
    expect(
      within(incoming).getByText('Import separately', { selector: '.imp3-dchip' }),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'expense-1' },
      { row: 1, action: 'create' },
    ])
  })

  it('a low-confidence IN leg split by an ordinary OUT match honors Leave out at commit', async () => {
    // The split IN leg (needsEyes, no match) now defaults to Import at load (owner,
    // 2026-07-28) — explicitly choosing Leave out must still commit skip correctly.
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 1 })
    await openWith(
      {
        statementId: 'st-fx-ordinary-out-low-in',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-low-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [
              {
                type: 'near_duplicate',
                reason: 'ordinary expense match',
                existing_id: 'expense-low-1',
                existing: {
                  note: 'Ordinary expense',
                  amount: -100,
                  currency: 'SGD',
                  date: '2026-06-10',
                  kind: 'expense',
                },
                band: 'strong',
              },
            ],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            needsEyes: true,
            confidence: 0.35,
            notes: ['Low read confidence'],
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-low-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 1,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 2,
        },
      },
      { onDecisionCommit },
    )

    await screen.findAllByText('FX debit')
    fireEvent.click(within(draftEl(1)).getByRole('radio', { name: 'Leave out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'expense-low-1' },
      { row: 1, action: 'skip' },
    ])
  })

  it.each([
    ['Lucius skip', { lucius: { op: 'skip', reason: 'Skip per saved instruction' } }],
    ['pre-reconciliation', { preRecon: true }],
  ])('a split IN leg keeps the %s fail-closed default', async (_label, inState) => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 1 })
    await openWith(
      {
        statementId: 'st-fx-ordinary-out-fail-closed-in',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-fail-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [
              {
                type: 'near_duplicate',
                reason: 'ordinary expense match',
                existing_id: 'expense-fail-1',
                existing: {
                  note: 'Ordinary expense',
                  amount: -100,
                  currency: 'SGD',
                  date: '2026-06-10',
                  kind: 'expense',
                },
                band: 'strong',
              },
            ],
          },
          {
            ...base,
            ...inState,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-fail-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 1,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 1,
        },
      },
      { onDecisionCommit },
    )

    await screen.findAllByText('FX debit')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'expense-fail-1' },
      { row: 1, action: 'skip' },
    ])
  })

  it('an ordinary IN match remains an actionable decision instead of folding behind OUT creation', async () => {
    // The IN leg's review-band match already defaults to Link at load (owner,
    // 2026-07-27) — no queue interaction needed to reach this state anymore.
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(
      {
        statementId: 'st-fx-ordinary-in-match',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-out-new' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-ordinary-in-match' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [
              {
                type: 'near_duplicate',
                reason: 'ordinary income match',
                existing_id: 'income-1',
                existing: {
                  note: 'Ordinary income',
                  amount: 102,
                  currency: 'SGD',
                  date: '2026-06-10',
                  kind: 'income',
                },
                band: 'review',
              },
            ],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 1,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 1,
        },
      },
      { onDecisionCommit },
    )

    await screen.findAllByText('FX debit')
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(within(draftEl(1)).queryByText('Folded into transfer')).toBeNull()
    expect(draftEl(1).className).toContain('imp-pair-row')

    fireEvent.click(document.querySelector('.imp3-commit') as HTMLElement)
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toContainEqual({
      row: 1,
      action: 'merge',
      existing_id: 'income-1',
    })
  })

  it.each([
    ['Link', 'merge'],
    ['Import', 'create'],
    ['Leave out', 'skip'],
  ] as const)('an already-booked pair OUT exposes %s and commits %s', async (control, action) => {
    const onDecisionCommit = vi.fn().mockResolvedValue({
      created: action === 'create' ? 1 : 0,
      merged: action === 'merge' ? 1 : 0,
      duplicates: 0,
      skipped: action === 'skip' ? 1 : 0,
    })
    await openWith(alreadyBookedPairPreview(), { onDecisionCommit })
    await screen.findAllByText('FX debit')
    const row = draftEl(0)
    expect(row.className).toContain('imp-pair-row')
    expect(within(row).getByRole('radio', { name: 'Link' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Import' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    fireEvent.click(within(row).getByRole('radio', { name: control }))
    fireEvent.click(document.querySelector('.imp3-commit') as HTMLElement)
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toContainEqual({
      row: 0,
      action,
      ...(action === 'merge' ? { existing_id: 'tx-fx' } : {}),
    })
  })

  it('an active pair OUT has no inline editors at all (handoff: read-only Cmp, decision only)', async () => {
    // 2026-07-27 (owner directive, match the handoff exactly): the inline category/account/
    // amount-flip editors this used to pin ("hides amount/account, keeps category") are GONE
    // everywhere — the prototype's Cmp is read-only; fixing a row is a post-commit edit.
    const other = { ...acct, id: 'a2', name: 'Meridian' } as unknown as Account
    await openWith(alreadyBookedPairPreview(), { accounts: [acct, other] })
    await screen.findAllByText('FX debit')
    const row = draftEl(0)
    expect(within(row).queryByRole('combobox', { name: 'Category for FX debit' })).toBeNull()
    expect(within(row).queryByRole('combobox', { name: 'Account for FX debit' })).toBeNull()
    expect(within(row).queryByRole('button', { name: /Flip FX debit/ })).toBeNull()
  })

  it('a review-band pair OUT defaults to Link while its IN partner stays pair-owned', async () => {
    // REWORDED 2026-07-28: the OUT leg's review band auto-decides at load (owner,
    // 2026-07-27), so there's no Needs-you queue card to open — what survives is that its
    // decision is independent (Link) while the IN leg stays folded into the transfer, never
    // queued on its own.
    const preview = alreadyBookedPairPreview()
    preview.drafts[0]!.flags[0]!.band = 'review'
    await openWith(preview, { importReviewDefault: 'merge' })

    await screen.findAllByText('FX debit')
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    const out = draftEl(0)
    expect(within(out).getByRole('radio', { name: 'Link' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    const incoming = draftEl(1)
    expect(
      within(incoming).getByText('Folded into transfer', { selector: '.imp3-dchip' }),
    ).toBeTruthy()
  })

  it('an unmatched active pair OUT also has no inline editors', async () => {
    const other = { ...acct, id: 'a2', name: 'Meridian' } as unknown as Account
    await openWith(
      {
        statementId: 'st-fx-unmatched-out',
        multi: true,
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-unmatched-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-unmatched-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 0,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 0,
        },
      },
      { accounts: [acct, other] },
    )

    const row = openRow((await screen.findByText('FX debit')).closest('.imp3-arow') as HTMLElement)
    expect(within(row).queryByRole('combobox', { name: 'Category for FX debit' })).toBeNull()
    expect(within(row).queryByRole('combobox', { name: 'Account for FX debit' })).toBeNull()
    expect(within(row).queryByRole('button', { name: /Flip FX debit/ })).toBeNull()
  })

  it('leaving out an unmatched pair OUT marks both legs uncovered instead of folded', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 2 })
    await openWith(
      {
        statementId: 'st-fx-left-out',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-left-out-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-left-out-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 0,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 0,
        },
      },
      { onDecisionCommit },
    )

    const out = openRow((await screen.findByText('FX debit')).closest('.imp3-arow') as HTMLElement)
    openVerdict()
    fireEvent.click(within(out).getByRole('radio', { name: 'Leave out' }))

    const incoming = draftEl(1)
    expect(within(incoming).getByText('Left out', { selector: '.imp3-dchip.skip' })).toBeTruthy()
    expect(
      within(incoming).queryByText('Folded into transfer', { selector: '.imp3-dchip' }),
    ).toBeNull()
    expect(screen.getByText(/2 statement transactions won’t be anywhere after import/)).toBeTruthy()
    expect(
      within(document.querySelector('.imp3-foot-note') as HTMLElement).getByText('leave out 2'),
    ).toBeTruthy()

    // Nothing links and nothing imports, so the CTA says exactly that (it still FILES the
    // statement — a real all-left-out outcome the design has no fixture for).
    fireEvent.click(document.querySelector('.imp3-commit') as HTMLElement)
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([{ row: 0, action: 'skip' }])
  })

  it('a low-confidence active pair OUT offers no ignored amount fix', async () => {
    // REWORDED 2026-07-28: this needsEyes pair-OUT leg auto-decides at load (owner,
    // 2026-07-27/28) — it renders as a regular plain row, not a queue card, but it keeps the
    // same exclusion the old queue card had: its amount is cross-checked against its IN
    // partner, so it's never independently "fixable" here.
    await openWith({
      statementId: 'st-fx-low-confidence',
      drafts: [
        {
          ...base,
          date: '2026-06-10',
          payee: 'FX debit',
          description: 'FX OUT',
          amount: -100,
          needsEyes: true,
          confidence: 0.35,
          notes: ['Low read confidence — verify the row'],
          external_refs: [{ source: 'pdf', ref: 'fx-low-out' }],
          rowIndex: 0,
          pair: { role: 'out', partner_index: 1 },
          flags: [],
        },
        {
          ...base,
          date: '2026-06-10',
          payee: 'FX credit',
          description: 'FX IN',
          amount: 102,
          kind: 'income',
          external_refs: [{ source: 'pdf', ref: 'fx-low-in' }],
          rowIndex: 1,
          pair: { role: 'in', partner_index: 0 },
          flags: [],
        },
      ],
      importable: 2,
      duplicates: 0,
      skipped: [],
      total: 2,
      anomaly: {
        near_duplicate: 0,
        transfer_pair: 1,
        intra_batch_duplicate: 0,
        amount_anomaly: 0,
        flagged_rows: 1,
      },
    })

    await screen.findAllByText('FX debit')
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    const row = draftEl(0)
    expect(within(row).queryByRole('button', { name: /Fix the amount/ })).toBeNull()
    // No more visual key badges (owner, 2026-07-27) — just the two choices for a pair-out row,
    // already decided to its default (Import — a needsEyes row with no match defaults to
    // Import as of 2026-07-28).
    expect(within(row).getByRole('radio', { name: 'Import', checked: true })).toBeTruthy()
  })

  it('leaving out a matched pair OUT keeps its IN leg covered by the existing transfer', async () => {
    await openWith(alreadyBookedPairPreview())
    await screen.findAllByText('FX debit')
    openVerdict()
    const out = draftEl(0)
    fireEvent.click(within(out).getByRole('radio', { name: 'Leave out' }))

    const incoming = draftEl(1)
    expect(
      within(incoming).getByText('Folded into transfer', { selector: '.imp3-dchip' }),
    ).toBeTruthy()
    expect(screen.queryByText(/won’t be anywhere after import/)).toBeNull()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
  })

  it('a folded FX pair reports one import and omits the absorbed leg from the payload', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
    await openWith(
      {
        statementId: 'st-fx-folded',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-folded-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            needsEyes: true,
            confidence: 0.3,
            external_refs: [{ source: 'pdf', ref: 'fx-folded-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 0,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 0,
        },
      },
      { onDecisionCommit },
    )

    await screen.findByText('FX debit')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).getByRole('button', { name: 'All' })).toBeTruthy()
    expect(within(nav).getByRole('button', { name: /Will link\s*1/ })).toBeTruthy()
    expect(within(nav).getByRole('button', { name: /Import\s*1/ })).toBeTruthy()
    expect(within(nav).queryByRole('button', { name: /Left out/ })).toBeNull()
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(screen.queryByText(/still need your call/)).toBeNull()
    const footer = document.querySelector('.imp3-foot-note') as HTMLElement
    expect(within(footer).getByText('import 1')).toBeTruthy()
    expect(footer.textContent).not.toContain('leave out')
    const absorbed = draftEl(1)
    expect(absorbed.querySelector('.imp3-dchip')?.textContent).toBe('Folded into transfer')
    expect(absorbed.querySelector('.imp3-cat.none')).toBeNull()
    expect(within(absorbed).queryByRole('button', { name: 'Import' })).toBeNull()
    expect(within(absorbed).queryByRole('button', { name: 'Leave out' })).toBeNull()
    openRow(absorbed)
    expect(within(absorbed).queryByRole('combobox')).toBeNull()
    expect(within(absorbed).queryByRole('button', { name: /Flip FX credit/ })).toBeNull()
    expect(within(absorbed).getByRole('button', { name: 'Book separately' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([])
  })

  it('Book separately emits an explicit IN-leg create and Fold in reverses it', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
    await openWith(
      {
        statementId: 'st-fx-pair',
        drafts: [
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX debit',
            description: 'FX OUT',
            amount: -100,
            external_refs: [{ source: 'pdf', ref: 'fx-out' }],
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            flags: [],
          },
          {
            ...base,
            date: '2026-06-10',
            payee: 'FX credit',
            description: 'FX IN',
            amount: 102,
            kind: 'income',
            external_refs: [{ source: 'pdf', ref: 'fx-in' }],
            rowIndex: 1,
            pair: { role: 'in', partner_index: 0 },
            flags: [],
          },
        ],
        importable: 2,
        duplicates: 0,
        skipped: [],
        total: 2,
        anomaly: {
          near_duplicate: 0,
          transfer_pair: 1,
          intra_batch_duplicate: 0,
          amount_anomaly: 0,
          flagged_rows: 0,
        },
      },
      { onDecisionCommit },
    )

    const credit = openRow(
      (await screen.findByText('FX credit')).closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(credit).getByRole('button', { name: 'Book separately' }))
    expect(within(credit).getByText('Import separately', { selector: '.imp3-dchip' })).toBeTruthy()
    expect(within(credit).queryByRole('button', { name: 'Import' })).toBeNull()
    expect(within(credit).queryByRole('button', { name: 'Leave out' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    fireEvent.click(within(credit).getByRole('button', { name: 'Fold in' }))
    expect(
      within(credit).getByText('Folded into transfer', { selector: '.imp3-dchip' }),
    ).toBeTruthy()
    fireEvent.click(within(credit).getByRole('button', { name: 'Book separately' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    expect(decisions).toContainEqual({ row: 1, action: 'create' })
    expect(decisions).not.toContainEqual(expect.objectContaining({ row: 1, action: 'skip' }))
  })
})

describe('ImportModal — the verdict line click-through + the transient pill', () => {
  it('the ⚠ Off-by verdict line is a button: clicking filters the list to exactly the drifted rows', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    const verdictBtn = screen.getByRole('button', {
      name: /off the statement|kept at your original figure/,
    })
    expect(verdictBtn.textContent).toContain('S$6.70 off the statement')
    fireEvent.click(verdictBtn)
    expect(domDraftOrder()).toEqual(['0', '1'])
    expect(screen.queryByText('LAZADA')).toBeNull()
    const pill = pillEl()!
    expect(pill.getAttribute('aria-pressed')).toBe('true')
    expect(pill.querySelector('.x svg')).not.toBeNull()
  })

  it('the pill toggles the filter; its ✕ click and the All chip both clear back to everything', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(pillEl()!.querySelector('.x svg')).toBeNull() // inactive: present (drift exists), no ✕
    fireEvent.click(pillEl()!)
    expect(domDraftOrder()).toEqual(['0', '1'])
    fireEvent.click(pillEl()!) // the active pill (its ✕) clears
    expect(domDraftOrder()).toEqual(['0', '1', '2', '3'])
    fireEvent.click(pillEl()!)
    fireEvent.click(within(nav).getByRole('button', { name: 'All' })) // All clears too
    expect(domDraftOrder()).toEqual(['0', '1', '2', '3'])
    expect(pillEl()!.getAttribute('aria-pressed')).toBe('false')
  })

  it('counting cross-walk (extended): pill count == verdict kept == rendered markers', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    const pillN = Number((pillEl()!.querySelector('i') as HTMLElement).textContent)
    const kept = Number(
      /(\d+) rows? keeps? your older figure/.exec(verdictKeptLine().textContent || '')?.[1],
    )
    const markers = document.querySelectorAll('.imp-offby').length
    expect([pillN, kept, markers]).toEqual([2, 2, 2])
  })
})

describe('ImportModal — drift surfaces update together, live', () => {
  it('flipping a drifted row to Merge updates marker, pill, and verdict line in one render', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    fireEvent.click(within(draftEl(0)).getByRole('radio', { name: 'Link' }))
    // Marker gone from the fixed row, kept on the other.
    expect(draftEl(0).querySelector('.imp-offby')).toBeNull()
    expect((draftEl(1).querySelector('.imp-offby') as HTMLElement).textContent).toBe(
      'Off by S$5.00',
    )
    // Pill amount + count dropped with it.
    expect(pillEl()!.textContent).toContain('Off by S$5.00')
    expect((pillEl()!.querySelector('i') as HTMLElement).textContent).toBe('1')
    // Verdict line agrees.
    expect(verdictKeptLine().textContent).toContain('S$5.00 off the statement')
    expect(verdictKeptLine().textContent).toContain('1 row')
    // Fixing the second row clears every surface — the verdict flips to ✓.
    fireEvent.click(within(draftEl(1)).getByRole('radio', { name: 'Link' }))
    expect(document.querySelectorAll('.imp-offby').length).toBe(0)
    expect(pillEl()).toBeNull()
    expect(screen.queryByText(/off the statement|kept at your original figure/)).toBeNull()
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()
  })

  it('under the active filter, a fixed row leaves the list; fixing the last one falls back to All', async () => {
    await openDriftBook()
    await screen.findByText('LAZADA')
    fireEvent.click(pillEl()!)
    expect(domDraftOrder()).toEqual(['0', '1'])
    fireEvent.click(within(draftEl(0)).getByRole('radio', { name: 'Link' }))
    expect(domDraftOrder()).toEqual(['1'])
    fireEvent.click(within(draftEl(1)).getByRole('radio', { name: 'Link' }))
    expect(domDraftOrder()).toEqual(['0', '1', '2', '3']) // empty population → All fallback
  })

  it('a zero-drift book: no marker, no pill, and the ✓ amounts line stays a plain div', async () => {
    await openWith(cleanBookPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    openVerdict()
    expect(document.querySelectorAll('.imp-offby').length).toBe(0)
    expect(pillEl()).toBeNull()
    const ok = screen
      .getByText(/Amounts match the statement to the cent/)
      .closest('.imp-verdict-line') as HTMLElement
    expect(ok.className).toContain('imp3-vline ok')
    expect(ok.tagName).toBe('DIV')
    expect(document.querySelector('.imp-verdict-jump')).toBeNull()
  })
})
