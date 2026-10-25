// @vitest-environment jsdom
//
// SI-68 — the Lucius draft-operations pass: upload instructions that describe what a row
// really IS (a transfer / a category / "skip these") get applied directly to the drafts,
// server-side, hard-validated — never proposed for a separate confirm step. The review UI
// surfaces this as an applied strip (counts) + a per-row ✦ provenance chip (with the
// model's own reason) + an undo affordance that reverts one row (or all) via
// DELETE /api/statements/{id}/lucius-ops and re-previews against the cached parse.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'
import type { ImportPreviewResult } from './importHelpers'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only ever
// for overriding, never for discovering"), so open the row before reaching into it.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

const authFetch = vi.fn(async (_url: string, _init?: RequestInit) => ({
  ok: true,
  json: async () => ({ undone: true, removed: 1, remaining: 0 }),
}))
vi.mock('../../../api/client', () => ({
  authFetch: (url: string, init?: RequestInit) => authFetch(url, init),
}))

afterEach(() => {
  cleanup()
  authFetch.mockClear()
})

const accounts = [
  { id: 'rvb', name: 'Riverside Bank', kind: 'asset', type: 'cash', currency: 'VND' },
  { id: 'pocket', name: 'PocketPay Wallet', kind: 'asset', type: 'wallet', currency: 'VND' },
] as unknown as Account[]

const CSV =
  'Date,Description,Amount\n2026-07-10,Ecom.EW.SD.240710.POCKET.0387654321.CashOut.a1,-500000\n'

const basePreview = () => ({
  statementId: 'st-pocket',
  drafts: [
    {
      date: '2026-07-10',
      payee: 'PocketPay',
      description: 'Ecom.EW.SD.240710.POCKET.0387654321.CashOut.a1',
      amount: -500000,
      currency: 'VND',
      kind: 'expense',
      category: null,
      dup: false,
      rowIndex: 0,
      external_refs: [{ source: 'csv', ref: 'x1' }],
      flags: [],
      destId: 'rvb',
      destName: 'Riverside Bank',
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
  luciusPass: null as ImportPreviewResult['luciusPass'],
})

const routedPreview = () => ({
  ...basePreview(),
  drafts: [
    {
      ...basePreview().drafts[0]!,
      kind: 'transfer',
      routed: {
        rule_id: 'lucius_pass',
        from_account_id: 'rvb',
        from_account_name: 'Riverside Bank',
        to_account_id: 'pocket',
        to_account_name: 'PocketPay Wallet',
      },
      lucius: {
        op: 'route_transfer' as const,
        reason: 'CashOut is a top-up from Riverside Bank to PocketPay',
        account_id: 'pocket',
      },
    },
  ],
  luciusPass: {
    status: 'ran' as const,
    proposed: 1,
    validated: 1,
    applied: 1,
    routes: 1,
    categories: 0,
    skips: 0,
    fresh: true,
    llm_calls: 1,
    cost_usd: 0,
  },
})

const categorizedPreview = () => ({
  ...basePreview(),
  drafts: [
    {
      ...basePreview().drafts[0]!,
      lucius: {
        op: 'set_category' as const,
        reason: 'This is a ride-hailing charge',
        category: 'cat_transport',
        category_label: 'Transport',
      },
    },
  ],
  luciusPass: {
    status: 'ran' as const,
    proposed: 1,
    validated: 1,
    applied: 1,
    routes: 0,
    categories: 1,
    skips: 0,
    fresh: true,
    llm_calls: 1,
    cost_usd: 0,
  },
})

const skipPreview = () => ({
  ...basePreview(),
  drafts: [
    {
      ...basePreview().drafts[0]!,
      lucius: { op: 'skip' as const, reason: 'Notes say hold POCKET rows this month' },
    },
  ],
  luciusPass: {
    status: 'ran' as const,
    proposed: 1,
    validated: 1,
    applied: 1,
    routes: 0,
    categories: 0,
    skips: 1,
    fresh: true,
    llm_calls: 1,
    cost_usd: 0,
  },
})

async function openPreview(payload: ReturnType<typeof basePreview>) {
  const onPreview = vi.fn().mockResolvedValue(payload)
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
  render(
    <ImportModal
      open
      accounts={accounts}
      defaultAccountId="rvb"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit as never}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'riverside.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  fireEvent.change(fileInput, { target: { files: [file] } })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
  // Matches the row's own description text only — distinct from the lucius `reason` text
  // (also rendered visibly since the reason-visibility fix), which never contains ".a1".
  await screen.findByText(/CashOut\.a1/)
  return { onPreview, onDecisionCommit }
}

// REMOVED 2026-07-27 (owner directive, "match 100% with the design"): the choose-step honesty
// caption this described no longer renders — see PARITY_AND_GAPS.md deviation #10. The BEHAVIOR
// it warned about (a note silently reshaping a row) is unchanged; only the standing caption is
// gone, replaced by this guard against it silently coming back.
describe('ImportModal — choose-step honesty caption (SI-68)', () => {
  it('the caption stays removed — no "get applied automatically" text on the choose step', async () => {
    const onPreview = vi.fn()
    render(
      <ImportModal
        open
        accounts={accounts}
        defaultAccountId="rvb"
        onClose={() => {}}
        onPreview={onPreview as never}
        onDecisionCommit={vi.fn() as never}
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([CSV], 'riverside.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => CSV })
    fireEvent.change(fileInput, { target: { files: [file] } })
    await screen.findByLabelText('Tell Lucius')
    expect(screen.queryByText(/get applied automatically/)).toBeNull()
  })
})

describe('ImportModal — Lucius pass applied strip (SI-68)', () => {
  // REMOVED 2026-07-27 (owner: "this is not necessary to show" / "not show this part
  // completely"): the aggregate "Your instructions changed N rows... Undo all" summary is
  // gone entirely, not just merged — it never rendered in the handoff's own interactive
  // prototype (`imp3-lustrip` is dead CSS there), and per-row undo (the ✦ provenance chip's
  // own undo control) already covers reverting an instruction-driven change.
  it('never renders the aggregate "instructions changed" note, even when ops applied', async () => {
    const mixed = {
      ...routedPreview(),
      luciusPass: {
        status: 'ran' as const,
        proposed: 3,
        validated: 3,
        applied: 3,
        routes: 1,
        categories: 1,
        skips: 1,
        fresh: true,
        llm_calls: 1,
        cost_usd: 0,
      },
    }
    await openPreview(mixed)
    expect(document.querySelector('.imp3-lunote-strip')).toBeNull()
    expect(screen.queryByText(/Your instructions changed/)).toBeNull()
    expect(screen.queryByText(/Undo all/)).toBeNull()
  })

  it('does not render when nothing applied', async () => {
    await openPreview(basePreview())
    expect(screen.queryByText(/Your instructions changed/)).toBeNull()
  })
})

describe('ImportModal — no AI-provenance chrome on a Lucius-touched row (SI-68)', () => {
  // REMOVED 2026-07-28 (owner: "i don't think we should show lucius reason at all, and
  // shouldn't show undo ... what we show to users is the final decision for them ... we are
  // just complicating the flow if we show more 'detailed implementation' info"). The per-row
  // ✦ provenance chip (reason tooltip + "undo" link) is gone entirely for every op — a
  // route_transfer row gets a plain-language "change kind" control instead (below), and
  // set_category/skip land in the row's already-existing category pill / excluded state with
  // nothing extra to show.
  it('a route_transfer op shows no ✦ chip, no reason text, no "undo" — just the transfer line and a plain-language way to change kind', async () => {
    await openPreview(routedPreview())
    await screen.findByText('Transfer → PocketPay Wallet')
    expect(document.querySelector('.imp3-arow-prov')).toBeNull()
    expect(document.querySelector('.imp3-routed')).toBeNull()
    expect(screen.queryByText(/top-up from Riverside Bank to PocketPay/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'undo' })).toBeNull()
    // The change-kind control sits inside the same expanded detail as "Books as transfer" —
    // one row, not a standalone block.
    const row = openRow(screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement)
    expect(
      within(row).getByText(/Books as transfer: Riverside Bank → PocketPay Wallet/),
    ).toBeTruthy()
    expect(
      within(row).getByRole('button', { name: 'Not a transfer — book as an expense instead' }),
    ).toBeTruthy()
  })

  it('a set_category op shows no ✦ chip and no reason text', async () => {
    await openPreview(categorizedPreview())
    await screen.findByText(/CashOut\.a1/)
    expect(document.querySelector('.imp3-arow-prov')).toBeNull()
    expect(document.querySelector('.imp3-routed')).toBeNull()
    expect(screen.queryByText(/ride-hailing charge/)).toBeNull()
  })

  it('a skip op shows no ✦ chip and no "undo" — the row is just excluded, with an Import override like any other left-out row', async () => {
    await openPreview(skipPreview())
    await screen.findByText(/CashOut\.a1/)
    expect(document.querySelector('.imp3-arow-prov')).toBeNull()
    expect(document.querySelector('.imp3-routed')).toBeNull()
    expect(screen.queryByRole('button', { name: 'undo' })).toBeNull()
    // A Lucius-skipped row is excluded, so its row-level control offers the single Import override
    // — the SAME plain override every other excluded row gets, nothing AI-specific. It lives in
    // the row's open part, like everything else there.
    const row = openRow(screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement)
    const importBtn = screen.getByRole('button', { name: 'Import' })
    expect(importBtn.className).toContain('imp3-actbtn')
    fireEvent.click(importBtn)
    // Included: the row now offers the two-way radiogroup instead.
    openRow(row)
    expect(within(row).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    expect(within(row).getByRole('radio', { name: 'Leave out' }).getAttribute('aria-checked')).toBe(
      'false',
    )
  })

  it('provenance Include clears an earlier row-level Leave out before commit', async () => {
    const { onDecisionCommit } = await openPreview(skipPreview())
    const row = screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement
    // Include → leave out → include again, all through the row's one decision control.
    openRow(row)
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    fireEvent.click(within(openRow(row)).getByRole('radio', { name: 'Leave out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    expect(decisions).toContainEqual({ row: 0, action: 'create' })
    expect(row.className).not.toContain('skip')
  })
})

describe('ImportModal — undo (SI-68)', () => {
  it('per-row undo DELETEs this row only, then re-previews against the cached parse', async () => {
    const { onPreview } = await openPreview(routedPreview())
    const row = openRow(screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement)
    fireEvent.click(
      within(row).getByRole('button', { name: 'Not a transfer — book as an expense instead' }),
    )
    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    // Not necessarily the FIRST call any more (B20): the initial preview's own processing
    // step polls GET .../preview-progress/{token} in the background, so find the DELETE
    // specifically rather than assume it's call index 0.
    const deleteCall = authFetch.mock.calls.find(
      ([, init]) => (init as { method?: string } | undefined)?.method === 'DELETE',
    ) as unknown as [string, { method: string }]
    expect(deleteCall).toBeTruthy()
    const [url, init] = deleteCall
    expect(url).toBe('/api/statements/st-pocket/lucius-ops?row=0')
    expect(init.method).toBe('DELETE')
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2))
  })

  // SKIPPED 2026-07-27 (owner: "this is not necessary to show" — the aggregate
  // "instructions changed... Undo all" strip is gone entirely, not just its summary text).
  // `undoLucius()`'s no-arg bulk-DELETE branch is kept (per-row undo still calls it with a
  // row index), but no control in the UI triggers the no-arg form anymore.
  it.skip('"Undo all" DELETEs every op on the statement, then re-previews', async () => {
    const mixed = {
      ...routedPreview(),
      luciusPass: {
        status: 'ran' as const,
        proposed: 2,
        validated: 2,
        applied: 2,
        routes: 1,
        categories: 1,
        skips: 0,
        fresh: true,
        llm_calls: 1,
        cost_usd: 0,
      },
    }
    const { onPreview } = await openPreview(mixed)
    fireEvent.click(await screen.findByRole('button', { name: 'Undo all' }))
    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    const deleteCall = authFetch.mock.calls.find(
      ([, init]) => (init as { method?: string } | undefined)?.method === 'DELETE',
    ) as unknown as [string, { method: string }]
    const [url, init] = deleteCall
    expect(url).toBe('/api/statements/st-pocket/lucius-ops')
    expect(init.method).toBe('DELETE')
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2))
  })
})

// A rejected match must leave the row LINKABLE (owner repro, 2026-08-09): the blanket
// `!d.lucius` gate hid the affordance, leaving Import / Leave out as the only choices.
const rejectedMatchPreview = () => ({
  ...basePreview(),
  drafts: [
    {
      ...basePreview().drafts[0]!,
      lucius: {
        op: 'reject_match' as const,
        reason: 'The row names Compass Asia-Pacific, but the proposed match names Nimbus.',
      },
    },
  ],
  ledgerCheck: {
    status: 'ok',
    period: { start: '2026-07-01', end: '2026-07-31' },
    rows: [
      {
        id: 'txn_compass',
        date: '2026-07-10',
        note: 'InstantPay to Compass Singapore',
        amount: -500000,
        currency: 'VND',
        kind: 'transfer',
        account_id: 'rvb',
        to_account_id: 'pocket',
        category: null,
        has_category: false,
        cleared: true,
        source: 'agent',
      },
    ],
    boundary: [],
  },
  luciusPass: {
    status: 'ran' as const,
    proposed: 1,
    validated: 1,
    applied: 1,
    routes: 0,
    categories: 0,
    skips: 0,
    fresh: true,
    llm_calls: 1,
    cost_usd: 0,
  },
})

describe('ImportModal — a rejected match stays linkable', () => {
  it('offers Link to existing on a reject_match row', async () => {
    await openPreview(rejectedMatchPreview() as never)
    const row = openRow(screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement)

    expect(within(row).getByRole('button', { name: /Link to existing/ })).toBeTruthy()
  })

  it('still hides it on a row Lucius genuinely settled (route_transfer)', async () => {
    await openPreview(routedPreview() as never)
    const row = openRow(screen.getByText(/CashOut\.a1/).closest('.imp3-arow') as HTMLElement)

    expect(within(row).queryByRole('button', { name: /Link to existing/ })).toBeNull()
  })
})
