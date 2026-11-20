// @vitest-environment jsdom
//
// Owner bug report, 2026-07-31 — three rendering defects in "In your ledger, not on this
// statement", all in the one row block:
//
//   1. "why are we showing ai/agent here? that should show description, no?" — the secondary
//      line spent the row's only free line on `source||kind`, i.e. the DISPLAY-projected
//      provenance vocabulary (ai/agent/import/manual). Nothing the owner can act on, and the
//      description is already the primary line.
//      PARTLY SUPERSEDED the same day: the fix put the ACCOUNT there, then the owner asked for
//      "the description + any statement that these transactions already belong to" and the
//      server-resolved ORIGIN took the slot (SI-95, ImportModal.ledgerOnlyOrigin.test.tsx) —
//      the routed account is the same string on every row of a single-account import, so it
//      moved into the expanded body. What survives here unchanged is the NEGATIVE half, which
//      is what the owner actually reported: the provenance vocabulary is gone from the row.
//   2. "no color for categories!" — the chip hardcoded `imp3-cat none` and rendered no `<i>`
//      swatch at all, so a categorised entry showed permanently in the muted "Uncategorized"
//      styling. It now matches the pair/plain rows: real colour when categorised, `none`
//      only when genuinely uncategorised, transfer identity instead of "Uncategorized".
//   3. "why is this one showing too ugly?" — a native `title` on the truncated name rendered
//      the browser's own tooltip over the row beneath. The name ellipsizes cleanly instead;
//      the full text is still on the row's aria-label.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account, Category } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn().mockResolvedValue({ ok: false }) }))

afterEach(cleanup)
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

const card = {
  id: 'a1',
  name: 'Northgate Rewards Card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
} as unknown as Account
const savings = {
  id: 'a2',
  name: 'Meridian Multiplier',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
} as unknown as Account
const catList: Category[] = [
  { id: 'cat_soft', label: 'Software', color: '#3b86dd', kind: 'expense' },
]
const catMap = { cat_soft: catList[0]! }

async function openWith(preview: unknown) {
  const onPreview = vi.fn().mockResolvedValue(preview)
  render(
    <ImportModal
      open
      accounts={[card, savings]}
      defaultAccountId="a1"
      categories={catList}
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

/** One unremarkable statement row, plus whichever ledger-only entries the case needs. */
function previewWith(rows: Array<Record<string, unknown>>) {
  return {
    drafts: [
      {
        date: '2026-06-23',
        payee: 'Vercel',
        description: 'VERCEL INC',
        amount: -20.1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 0,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:vercel' }],
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
    ledgerCheck: { period: { start: '2026-06-01', end: '2026-06-30' }, rows, boundary: [] },
  }
}

/** The owner's screenshot, near enough: an AI-sourced entry with a long description. */
const LONG_NOTE = 'Northgate FlexiPay — Unbilled instalment 4 of 12'
const aiRow = {
  id: 'led-1',
  date: '2026-06-15',
  note: LONG_NOTE,
  amount: -18.44,
  currency: 'SGD',
  kind: 'expense',
  source: 'ai',
  category: 'cat_soft',
  account_id: 'a1',
  // Server-resolved: WHICH STATEMENT this row is on, from `external_refs`. It is what the
  // secondary line renders since SI-95; present on the fixture so the "no native tooltip"
  // assertion below still has that element to check.
  on_statements: {
    label: '1–30 Jun 2026 statement',
    reference: null,
    statements: [{ statement_id: 'stmt_jun', label: '1–30 Jun 2026 statement' }],
  },
  near_cutoff: null,
}

// Every fixture entry is ON a statement we hold (`on_statements`), and since 2026-10-26 such an
// entry is not "not on this statement": it sits in the quiet, collapsed "on another statement
// you imported" group beneath the panel, so the group is opened first. The row anatomy is the
// same one the panel itself renders.
const ledgerRow = async (note: string): Promise<HTMLElement> => {
  const group = await screen.findByRole('button', { name: /on another statement you imported/ })
  if (group.getAttribute('aria-expanded') === 'false') fireEvent.click(group)
  return (await screen.findByText(note)).closest('.imp3-arow') as HTMLElement
}

// ── Defect 1: the secondary line ─────────────────────────────────────────────────────

describe('ledger-only row — the secondary line is never the provenance vocabulary', () => {
  it('drops "ai"/"agent" from the row entirely', async () => {
    await openWith(
      previewWith([
        aiRow,
        { ...aiRow, id: 'led-2', note: 'Agent entry', source: 'agent', account_id: 'a2' },
      ]),
    )
    const row = await ledgerRow(LONG_NOTE)
    // The provenance vocabulary the owner objected to is gone from the row entirely.
    expect(within(row).queryByText('ai')).toBeNull()
    expect(within(row).queryByText('expense')).toBeNull()
    expect(within(await ledgerRow('Agent entry')).queryByText('agent')).toBeNull()
  })

  // The account moved into the expanded body's one-line facts run when the STATEMENT took the
  // closed line (SI-95). The NAMING rule the original criterion existed for is unchanged and is
  // asserted where the account now lives: resolve it, or omit it — never print a raw acc_ id.
  it('names the account in the body, and omits it rather than printing a raw id when unknown', async () => {
    await openWith(
      previewWith([
        aiRow,
        { ...aiRow, id: 'led-2', note: 'Ghost account', account_id: 'acc_gone' },
      ]),
    )
    const facts = (row: HTMLElement) => row.querySelector('.imp3-lc-facts')?.textContent ?? ''
    const open = (row: HTMLElement) => {
      fireEvent.click(row.querySelector('.imp3-arow-line') as HTMLElement)
      return row
    }
    expect(facts(open(await ledgerRow(LONG_NOTE)))).toContain('Northgate Rewards Card')
    const ghost = open(await ledgerRow('Ghost account'))
    expect(facts(ghost)).not.toContain('Northgate Rewards Card')
    expect(ghost.textContent).not.toContain('acc_gone')
    // Still not a bare row — date, status chip, category chip and amount all remain.
    expect(within(ghost).getByText('Ledger only')).toBeTruthy()
    expect(ghost.querySelector('.imp3-cat')).toBeTruthy()
  })
})

// ── Defect 2: the category chip's colour ─────────────────────────────────────────────

const swatch = (row: HTMLElement) =>
  row.querySelector('.imp3-arow-catcol .imp3-cat i') as HTMLElement | null
const chip = (row: HTMLElement) => row.querySelector('.imp3-arow-catcol .imp3-cat') as HTMLElement

describe('ledger-only row — the category chip carries its colour', () => {
  it('a categorised entry renders the category swatch and drops the "none" styling', async () => {
    await openWith(previewWith([aiRow]))
    const row = await ledgerRow(LONG_NOTE)
    expect(chip(row).className).not.toContain('none')
    expect(chip(row).textContent).toContain('Software')
    expect(swatch(row)?.style.background).toBe('rgb(59, 134, 221)')
  })

  it('an uncategorised entry keeps "none" styling and a faint swatch', async () => {
    await openWith(previewWith([{ ...aiRow, category: null }]))
    const row = await ledgerRow(LONG_NOTE)
    expect(chip(row).className).toContain('none')
    expect(chip(row).textContent).toContain('Uncategorized')
    expect(swatch(row)?.style.background).toBe('var(--faint)')
  })

  it('an unresolvable category id still colours nothing but is NOT muted as unsorted', async () => {
    await openWith(previewWith([{ ...aiRow, category: 'cat_deleted' }]))
    const row = await ledgerRow(LONG_NOTE)
    expect(chip(row).className).not.toContain('none')
    expect(chip(row).textContent).toContain('cat_deleted')
    expect(swatch(row)?.style.background).toBe('var(--faint)')
  })

  it('a transfer reads as a transfer — never "Uncategorized", never the none styling', async () => {
    await openWith(previewWith([{ ...aiRow, kind: 'transfer', category: null, account_id: 'a2' }]))
    const row = await ledgerRow(LONG_NOTE)
    expect(chip(row).className).not.toContain('none')
    expect(chip(row).textContent).toContain('Transfer')
    expect(chip(row).textContent).not.toContain('Uncategorized')
    expect(swatch(row)?.style.background).toBe('var(--muted)')
  })
})

// ── Defect 3: no native tooltip on the truncated name ────────────────────────────────

describe('ledger-only row — the name ellipsizes without a native tooltip', () => {
  it('carries no title attribute, and keeps the full description on the row aria-label', async () => {
    await openWith(previewWith([aiRow]))
    const row = await ledgerRow(LONG_NOTE)
    const name = row.querySelector('.imp3-arow-name') as HTMLElement
    expect(name.textContent).toBe(LONG_NOTE)
    expect(name.hasAttribute('title')).toBe(false)
    expect(row.querySelector('.imp3-arow-desc')?.hasAttribute('title')).toBe(false)
    expect(chip(row).hasAttribute('title')).toBe(false)
    // The full text is still announced — the disclosure button's own label.
    expect(row.querySelector('.imp3-arow-line')?.getAttribute('aria-label')).toBe(LONG_NOTE)
    // The ONLY surviving native tooltip on this row is the static explanation of the
    // pre-conversion figure (ImportModal.originalCharge.test.tsx owns that surface).
    const titled = Array.from(row.querySelectorAll('[title]'))
    expect(titled.every((n) => n.classList.contains('imp3-orig'))).toBe(true)
  })

  it("the category cell shares the pair row's 140px-floor / 260px-cap ellipsizing anatomy", async () => {
    await openWith(previewWith([aiRow]))
    const row = await ledgerRow(LONG_NOTE)
    // Same DOM shape as the pair/plain rows: catcol > .imp3-cat > i + span, where the label
    // span is the element `.imp3-cat > span` ellipsizes. Before the fix the `<i>` was missing
    // and the chip could not shrink the same way.
    const cell = row.querySelector('.imp3-arow-catcol') as HTMLElement
    expect(cell.children).toHaveLength(1)
    expect(chip(row).children[0]?.tagName).toBe('I')
    expect(chip(row).children[1]?.tagName).toBe('SPAN')
  })
})

// ── Regressions the fix must not introduce ───────────────────────────────────────────

describe('ledger-only row — what must NOT appear', () => {
  it("never renders the engine's diagnostic reason", async () => {
    await openWith(previewWith([{ ...aiRow, reason: 'no_statement_counterpart' }]))
    await ledgerRow(LONG_NOTE)
    expect(document.body.textContent).not.toContain('no_statement_counterpart')
  })

  it('still renders the pre-conversion original charge', async () => {
    await openWith(previewWith([{ ...aiRow, original_amount: 13.5, original_currency: 'USD' }]))
    const row = await ledgerRow(LONG_NOTE)
    expect(row.querySelector('.imp3-orig')?.textContent).toBe('$13.50')
  })
})
