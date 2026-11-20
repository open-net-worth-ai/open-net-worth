// @vitest-environment jsdom
//
// Owner, 2026-07-31, on the "In your ledger, not on this statement" list:
//   "for rows in not on statement, please help to show the description + any statement that
//    these transactions already belong to"
//   "why the fuck are we showing already on for what? show description, account, and
//    externalrefs (only if it's a statement) on one row together!"
//   "why the fuck are we using import batch id? why don't we use external_refs?"
//   "please handle these on the backend, not client so the ai tool can get the same
//    information"
//
// That last line is the design constraint, and it is what this file mostly pins. Every fact
// arrives ALREADY RESOLVED on the wire — `note`, `on_statements.label`, `near_cutoff.label` —
// so the component's only job is to render them. The negative assertions matter more than the
// positive ones: a client that re-derives any of these is how the app grows two answers to one
// question (the Accounts-tab net-worth recompute and the retired twin matcher were both that).
//
// The row's secondary line has been wrong twice, in instructive ways: first it printed
// `r.source` ("ai"/"agent" — unactionable), then it printed a BATCH-derived label, which said
// "Already on: Anthropic USD invoices…" for a row whose statement was eStatement_Jun2026 —
// a truthful answer to "where did it come from", rendered as an answer to "what is it on".

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account, Category } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn().mockResolvedValue({ ok: false }) }))

afterEach(cleanup)
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

const acct = {
  id: 'a1',
  name: 'Northgate Rewards Card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
} as unknown as Account
const cats: Category[] = [{ id: 'cat_soft', label: 'Software', color: '#3b86dd', kind: 'expense' }]
const catMap = { cat_soft: cats[0]! }

// Long enough that the 190px name rail certainly ellipsized it — the one case where restating
// the description in the body earns its space.
const LONG_DESC = 'SP DELUGS Singapore - Refund — Refund posted 11 May, trans date 8 May'
const SHORT_DESC = 'Anthropic'

const JUN_STATEMENT = {
  label: '5 May – 4 Jun 2026 statement',
  reference: null,
  statements: [
    {
      statement_id: 'stmt_jun',
      label: '5 May – 4 Jun 2026 statement',
      filename: 'eStatement_Jun2026 2.pdf',
      reference: null,
      period_start: '2026-05-05',
      period_end: '2026-06-04',
    },
  ],
}

function ledgerOnlyPreview(rows?: unknown[]) {
  return {
    drafts: [
      {
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        preRecon: false,
        autoCat: false,
        date: '2026-06-14',
        payee: 'Notion',
        description: 'NOTION LABS',
        amount: -12,
        rowIndex: 0,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:notion' }],
        flags: [],
      },
    ],
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
    ledgerCheck: {
      period: { start: '2026-05-31', end: '2026-07-01' },
      rows: rows ?? [
        {
          id: 'led-1',
          date: '2026-06-15',
          note: LONG_DESC,
          amount: -18.44,
          currency: 'SGD',
          kind: 'expense',
          source: 'ai',
          account_id: 'a1',
          category: 'cat_soft',
          on_statements: JUN_STATEMENT,
          near_cutoff: null,
        },
      ],
      boundary: [],
    },
  }
}

async function openWith(preview: unknown) {
  const onPreview = vi.fn().mockResolvedValue(preview)
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      categories={cats}
      catMap={catMap}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={vi
        .fn()
        .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
}

const ledgerRow = async (): Promise<HTMLElement> => {
  // An entry a statement we hold already backs (`on_statements`) sits in the quiet, collapsed
  // "on another statement you imported" group since 2026-10-26 — open it first. A row with
  // `on_statements: null` is in the panel itself and has no such group.
  await waitFor(() =>
    expect(
      document.querySelector('.imp3-dchip.ledger') ||
        screen.queryByRole('button', { name: /on another statement you imported/ }),
    ).toBeTruthy(),
  )
  const group = screen.queryByRole('button', { name: /on another statement you imported/ })
  if (group?.getAttribute('aria-expanded') === 'false') fireEvent.click(group)
  await waitFor(() => expect(document.querySelector('.imp3-arow .imp3-dchip.ledger')).toBeTruthy())
  return document.querySelector('.imp3-dchip.ledger')!.closest('.imp3-arow') as HTMLElement
}
const expand = (row: HTMLElement) => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement
  if (line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}
/** The ONE body line: description? · account · statement · ref. */
const factsLine = (row: HTMLElement) => row.querySelector('.imp3-lc-facts')?.textContent ?? null

describe('ledger-only row — which statement it is ON comes from the server', () => {
  it('the secondary line is the statement label, NOT the source vocabulary', async () => {
    await openWith(ledgerOnlyPreview())
    const row = await ledgerRow()
    expect((row.querySelector('.imp3-arow-desc') as HTMLElement).textContent).toBe(
      '5 May – 4 Jun 2026 statement',
    )
    // The two shapes this line has been wrong as before.
    expect(row.querySelector('.imp3-arow-line')!.textContent).not.toContain('ai')
    expect(row.textContent).not.toContain('stmt_jun')
  })

  it('renders NOTHING rather than substituting where the row came FROM', async () => {
    // The owner's exact bug: this row's `batch_id` names an invoice-import batch. The
    // server resolves statements from `external_refs`, finds none, and sends null — so there
    // is nothing here for the client to dress up as "already on".
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-15',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'agent',
        account_id: 'a1',
        category: null,
        on_statements: null,
        near_cutoff: null,
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    expect(row.querySelector('.imp3-arow-desc')).toBeNull()
    expect(row.textContent).not.toContain('Already on')
    expect(row.textContent).not.toContain('invoices')
    expect(row.querySelector('.imp3-arow-line')!.textContent).not.toContain('agent')
    // The line still carries what it does know.
    expect(factsLine(row)).toBe('Northgate Rewards Card')
  })

  it('names every statement when a row is on more than one — the count comes from the server', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-03',
        note: SHORT_DESC,
        amount: -14.58,
        currency: 'SGD',
        kind: 'expense',
        source: 'email',
        account_id: 'a1',
        category: null,
        near_cutoff: null,
        on_statements: {
          label: '5 May – 4 Jun 2026 statement +1 more',
          reference: null,
          statements: [
            { statement_id: 'stmt_jun', label: '5 May – 4 Jun 2026 statement' },
            { statement_id: 'stmt_jul', label: '5 Jun – 4 Jul 2026 statement' },
          ],
        },
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    // Rendered verbatim: the client never counts `statements.length` or writes "+N more".
    expect((row.querySelector('.imp3-arow-desc') as HTMLElement).textContent).toBe(
      '5 May – 4 Jun 2026 statement +1 more',
    )
    expect(factsLine(row)).toContain('5 May – 4 Jun 2026 statement +1 more')
  })
})

describe('ledger-only row — one line: description • account • statement • ref', () => {
  it('is a single line, not a stack of labelled rows', async () => {
    await openWith(ledgerOnlyPreview())
    const row = expand(await ledgerRow())
    expect(row.querySelectorAll('.imp3-lc-facts').length).toBe(1)
    // The label/value grid it replaced is gone — no <dt>/<dd>, and no "Already on" wording.
    expect(row.querySelectorAll('dt').length).toBe(0)
    expect(row.textContent).not.toContain('Already on')
    expect(factsLine(row)).toBe(
      `${LONG_DESC} • Northgate Rewards Card • 5 May – 4 Jun 2026 statement`,
    )
  })

  it('carries the bank reference when the statement had one', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-15',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'a1',
        category: null,
        near_cutoff: null,
        on_statements: {
          label: '1–30 Jun 2026 statement',
          reference: '100000000001',
          statements: [
            { statement_id: 'stmt_1', label: '1–30 Jun 2026 statement', reference: '100000000001' },
          ],
        },
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    expect(factsLine(row)).toBe(
      'Northgate Rewards Card • 1–30 Jun 2026 statement • ref 100000000001',
    )
  })

  it('omits the description when the name rail above already showed it in full', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-15',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'a1',
        category: null,
        on_statements: JUN_STATEMENT,
        near_cutoff: null,
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    // "Description: Anthropic" under a row whose name IS "Anthropic" was the owner's complaint.
    expect((row.querySelector('.imp3-arow-name') as HTMLElement).textContent).toBe(SHORT_DESC)
    expect(factsLine(row)).toBe('Northgate Rewards Card • 5 May – 4 Jun 2026 statement')
  })

  it('restates the description when the rail clipped it', async () => {
    await openWith(ledgerOnlyPreview())
    const row = expand(await ledgerRow())
    expect(factsLine(row)!.startsWith(LONG_DESC)).toBe(true)
    // The native tooltip is gone (it covered the row beneath), so this line is the only place
    // the full descriptor is readable. The aria-label still announces it.
    expect(row.querySelector('.imp3-arow-name')!.getAttribute('title')).toBeNull()
    expect(row.querySelector('.imp3-arow-line')!.getAttribute('aria-label')).toBe(LONG_DESC)
  })

  it('an unresolvable account is OMITTED, never printed as a raw acc_ id', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-15',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'acc_ghost',
        category: null,
        on_statements: JUN_STATEMENT,
        near_cutoff: null,
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    expect(factsLine(row)).toBe('5 May – 4 Jun 2026 statement')
    expect(row.textContent).not.toContain('acc_ghost')
  })

  it('renders no line at all when there is nothing to put on it', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-15',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'acc_ghost',
        category: null,
        on_statements: null,
        near_cutoff: null,
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    expect(row.querySelector('.imp3-lc-facts')).toBeNull()
    // Still a usable row: the hint and the action vocabulary are untouched.
    expect(row.querySelector('.imp3-lc-hint')).toBeTruthy()
    expect(row.querySelector('.imp3-lc-acts')).toBeTruthy()
  })

  it('keeps the category chip its real colour swatch instead of always looking Uncategorized', async () => {
    await openWith(ledgerOnlyPreview())
    const row = await ledgerRow()
    const chip = row.querySelector('.imp3-arow-catcol .imp3-cat') as HTMLElement
    expect(chip.className).not.toContain('none')
    expect(chip.textContent).toContain('Software')
    expect((chip.querySelector('i') as HTMLElement).style.background).toBeTruthy()
  })
})

describe('ledger-only row — the near-cut-off chip', () => {
  const withCutoff = (near: unknown) =>
    ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-30',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'a1',
        category: null,
        on_statements: null,
        near_cutoff: near,
      },
    ])

  it('shows the server label as a chip beside the band chip', async () => {
    await openWith(withCutoff({ edge: 'end', days: 1, label: '1 day before closing' }))
    const row = await ledgerRow()
    expect((row.querySelector('.imp3-dchip.cutoff') as HTMLElement).textContent).toBe(
      '1 day before closing',
    )
  })

  it('renders no chip when the server says the entry is clear of both edges', async () => {
    await openWith(withCutoff(null))
    const row = expand(await ledgerRow())
    expect(row.querySelector('.imp3-dchip.cutoff')).toBeNull()
    // …even though `period` (2026-05-31 → 2026-07-01) and the row's own date are both right
    // there on the wire. Deriving "1 day before the end" from them is exactly the second
    // definition this field exists to prevent.
    expect(row.textContent).not.toContain('closing')
  })

  it('never renders the engine-side diagnostic `reason`', async () => {
    const p = ledgerOnlyPreview([
      {
        id: 'led-1',
        date: '2026-06-30',
        note: SHORT_DESC,
        amount: -18.44,
        currency: 'SGD',
        kind: 'expense',
        source: 'ai',
        account_id: 'a1',
        category: null,
        on_statements: JUN_STATEMENT,
        near_cutoff: { edge: 'end', days: 1, label: '1 day before closing' },
        reason: 'INTERNAL: score 0.81 vs fence 0.72',
      },
    ])
    await openWith(p)
    const row = expand(await ledgerRow())
    expect(row.textContent).not.toContain('INTERNAL')
    expect(row.textContent).not.toContain('fence')
  })
})
