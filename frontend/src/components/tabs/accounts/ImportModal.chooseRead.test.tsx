// @vitest-environment jsdom
//
// Statement Import v3 — the Choose and Read steps, ported VERBATIM from the handoff
// prototype (`design_handoffs/statement_import_handoff/prototype/import_v3/flow3.jsx`,
// functions `DropStep` and `ReadStep`). These pin the design's own DOM/class structure and
// copy, plus the production fact the prototype's static fixture has no state for: the extra
// chips routing unrouted rows through the per-row seam.
//
// Account choice used to default to a server-side "✦ Detect from the statement" chip; that was
// reverted 2026-07-27 (`docs/adr/2026-07-27-statement-import-drop-account-auto-detect.md`) so an
// account is always explicit, matching the prototype's own `disabled={acctIds.length === 0}`.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

afterEach(cleanup)

const mkAcct = (id: string, name: string, currency: string, color: string) =>
  ({
    id,
    name,
    currency,
    color,
    kind: 'asset',
    type: 'cash',
    archived: false,
  }) as unknown as Account

const ACCOUNTS = [
  mkAcct('rvb', 'Riverside Bank', 'VND', '#3b86dd'),
  mkAcct('pocket', 'PocketPay Wallet', 'VND', '#dd6fa8'),
  mkAcct('compass', 'Compass USD', 'USD', '#2ba39a'),
]

const CSV = 'Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n'

function preview(overrides: Record<string, unknown> = {}) {
  return {
    statementId: 'st-1',
    drafts: [
      {
        date: '2026-07-01',
        payee: null,
        description: 'COFFEE',
        amount: -12.5,
        currency: 'VND',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 0,
        external_refs: [],
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
    ...overrides,
  }
}

function open(props: Record<string, unknown> = {}) {
  const onPreview =
    (props.onPreview as ReturnType<typeof vi.fn>) || vi.fn().mockResolvedValue(preview())
  const r = render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
      {...(props as object)}
    />,
  )
  return { ...r, onPreview }
}

function pick(_container: HTMLElement, name = 'sao-ke.csv') {
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], name, { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  Object.defineProperty(file, 'size', { value: 41 * 1024 })
  fireEvent.change(input, { target: { files: [file] } })
  return file
}

describe('Choose step — the unpicked dropzone (prototype DropStep, !picked)', () => {
  it('renders the design dropzone, its one-line format list, and the URL link as its own control', () => {
    open()
    const drop = document.body.querySelector('.imp3-drop') as HTMLElement
    expect(drop).toBeTruthy()
    expect(drop.querySelector('.imp3-drop-t')!.textContent).toBe('Drop a statement here, or browse')
    expect(drop.querySelector('.imp3-drop-d')!.textContent).toBe(
      'PDF, CSV, XLSX, OFX, QIF, MT940, CAMT — scanned PDFs work too',
    )
    // The URL source is a quiet control BELOW the dropzone, not a peer tab: the pre-v3
    // "Upload a file / From a URL" tab strip is gone.
    expect(screen.getByRole('button', { name: 'or paste a URL / Google Sheet link' })).toBeTruthy()
    expect(document.body.querySelector('.imp-srctabs')).toBeNull()
  })

  it('a URL source keeps the Re-read control, and never sends a file alongside it', async () => {
    // The peek fingerprints FILE BYTES, so a hosted source can't be probed at all. That is
    // `unavailable`, not `unknown`: "Re-read from scratch" is the only control that sets
    // `force_relearn`, and a URL source gets stuck on a bad saved format profile exactly like
    // an uploaded one. It must stay reachable — without asserting "I know this source".
    const onPreview = vi.fn().mockResolvedValue(preview())
    open({ onPreview })
    fireEvent.click(screen.getByRole('button', { name: 'or paste a URL / Google Sheet link' }))
    fireEvent.change(screen.getByLabelText('Statement URL'), {
      target: { value: 'https://docs.google.com/x/pub?output=csv' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    await screen.findByRole('button', { name: /Read the statement/ })
    expect(screen.queryByText(/I know this source/)).toBeNull()
    // The picked-source row names the URL, and the format badge reads the Sheet's real format.
    expect(document.body.querySelector('.imp3-file-nm')!.textContent).toContain('docs.google.com')
    expect(document.body.querySelector('.imp3-file-badge')!.textContent).toBe('CSV')
    fireEvent.click(screen.getByRole('checkbox', { name: /Re-read from scratch/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // (file, url, …) — the server refuses a request carrying both (422).
    expect(onPreview.mock.calls[0]![0]).toBeNull()
    expect(onPreview.mock.calls[0]![1]).toContain('docs.google.com')
    expect(onPreview.mock.calls[0]![6]).toBe(true)
    expect(onPreview.mock.calls[0]![7]).toBe(true)
  })

  it('switching source resets the PAID re-read flag instead of firing it invisibly', async () => {
    // The hazard: check Re-read on a file, then switch to a URL. Before this fix `fresh`
    // survived the switch while its checkbox did not, so `force_relearn` — a billed LLM
    // re-detection — fired with nothing on screen to show or cancel it.
    const onPreview = vi.fn().mockResolvedValue(preview())
    const onSourcePeek = vi
      .fn()
      .mockResolvedValue({ known: true, instructions: '', formatLabel: null })
    const { container } = open({ onPreview, onSourcePeek })
    pick(container)
    await screen.findByText(/I know this source/)
    fireEvent.click(screen.getByRole('checkbox', { name: /Re-read from scratch/ }))
    expect(
      (screen.getByRole('checkbox', { name: /Re-read from scratch/ }) as HTMLInputElement).checked,
    ).toBe(true)
    // → back to the dropzone, then load a URL instead
    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    fireEvent.click(screen.getByRole('button', { name: 'or paste a URL / Google Sheet link' }))
    fireEvent.change(screen.getByLabelText('Statement URL'), {
      target: { value: 'https://example.com/stmt.csv' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    await screen.findByRole('button', { name: /Read the statement/ })
    // The control is on screen for the URL source, and it is OFF — the flag did not survive.
    expect(
      (screen.getByRole('checkbox', { name: /Re-read from scratch/ }) as HTMLInputElement).checked,
    ).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![6]).toBeFalsy()
    expect(onPreview.mock.calls[0]![7]).toBeFalsy()
  })

  it('shows a loading indicator while the source-known probe is in flight, then swaps to the resolved banner', async () => {
    // Before this fix the box rendered nothing at all while onSourcePeek was in flight, so
    // the "I know this source" banner appeared to pop in from nowhere — confusing (owner:
    // "we should show some loading indicator... otherwise users will be confusing").
    let resolvePeek!: (v: { known: boolean; instructions: string; formatLabel: null }) => void
    const onSourcePeek = vi.fn(
      () =>
        new Promise((resolve) => {
          resolvePeek = resolve
        }),
    )
    const { container } = open({ onSourcePeek })
    pick(container)
    await screen.findByText(/Checking if this source is remembered/)
    expect(screen.queryByText(/I know this source/)).toBeNull()
    await act(async () => {
      resolvePeek({ known: true, instructions: '', formatLabel: null })
    })
    await screen.findByText(/I know this source/)
    expect(screen.queryByText(/Checking if this source is remembered/)).toBeNull()
  })

  it('the URL link reveals the URL field, and Cancel puts it away', () => {
    open()
    fireEvent.click(screen.getByRole('button', { name: 'or paste a URL / Google Sheet link' }))
    expect(screen.getByLabelText('Statement URL')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText('Statement URL')).toBeNull()
    expect(document.body.querySelector('.imp3-drop')).toBeTruthy()
  })
})

describe('Choose step — the picked state (prototype DropStep, picked)', () => {
  it('renders the format badge, name, size and Change; Change returns to the dropzone', async () => {
    const { container } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    expect(document.body.querySelector('.imp3-file-badge')!.textContent).toBe('CSV')
    expect(document.body.querySelector('.imp3-file-nm')!.textContent).toBe('sao-ke.csv')
    expect(document.body.querySelector('.imp3-file-sub')!.textContent).toBe('41 KB')
    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    expect(document.body.querySelector('.imp3-drop')).toBeTruthy()
  })

  it("carries the design's own CTA row: the review promise and the Read CTA", async () => {
    const { container } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    expect(document.body.querySelector('.imp3-drop-cost')!.textContent).toBe(
      'You review everything before it books',
    )
  })

  it("the Read step shows the prototype's rotating phase line, and holds on the last one", async () => {
    vi.useFakeTimers()
    try {
      const { container } = open({ onPreview: vi.fn().mockReturnValue(new Promise(() => {})) })
      pick(container)
      await act(async () => {
        await Promise.resolve()
      })
      fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
      fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
      fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
      expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
      expect(document.body.querySelector('.imp3-orb')).toBeTruthy()
      expect(document.body.querySelector('.imp3-phase')!.textContent).toBe('Reading the statement')
      act(() => {
        vi.advanceTimersByTime(520 * 2)
      })
      expect(document.body.querySelector('.imp3-phase')!.textContent).toBe(
        'Checking the statement against its own balances',
      )
      act(() => {
        vi.advanceTimersByTime(520 * 20)
      })
      expect(document.body.querySelector('.imp3-phase')!.textContent).toBe(
        'Checking every row against your ledger',
      )
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('Choose step — "Into accounts" chips', () => {
  it('offers no chip by default and disables the CTA until an account is chosen', async () => {
    const { container, onPreview } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    // No stand-in chip — the add-chip reads "Select account" (prototype wording) and the CTA
    // is DISABLED, matching the prototype's own `disabled={acctIds.length === 0}`.
    expect(document.body.querySelector('.imp3-achip:not(.add)')).toBeNull()
    expect(screen.getByRole('button', { name: 'Select account' })).toBeTruthy()
    const cta = screen.getByRole('button', { name: /Read the statement/ }) as HTMLButtonElement
    expect(cta.disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    expect(cta.disabled).toBe(false)
    fireEvent.click(cta)
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![2]).toBe('rvb')
  })

  it('picking accounts builds chips, sends the FIRST as the anchor, and ✕ removes them', async () => {
    const { container, onPreview } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    // A real chip renders, and the add-chip switches wording.
    expect(document.body.querySelector('.imp3-achip:not(.add)')!.textContent).toContain(
      'Riverside Bank',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('option', { name: /PocketPay Wallet/ }))
    expect(document.body.querySelectorAll('.imp3-achip:not(.add)').length).toBe(2)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![2]).toBe('rvb')
  })

  it('removing the anchor promotes the next chip; removing the last leaves none and disables the CTA', async () => {
    const { container } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('option', { name: /PocketPay Wallet/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove Riverside Bank' }))
    const chips = Array.from(document.body.querySelectorAll('.imp3-achip:not(.add)'))
    expect(chips.length).toBe(1)
    expect(chips[0]!.textContent).toContain('PocketPay Wallet')
    fireEvent.click(screen.getByRole('button', { name: 'Remove PocketPay Wallet' }))
    expect(document.body.querySelector('.imp3-achip:not(.add)')).toBeNull()
    expect(
      (screen.getByRole('button', { name: /Read the statement/ }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  // SKIPPED 2026-07-27 (owner directive): the "Prefer to map the columns yourself?" link this
  // test navigates through was removed — the configure step has no UI entry point left. See
  // ImportModal.manualRoute.test.tsx's file header for the full reasoning.
  it.skip("the manual-mapping step's own account select can never duplicate a chip", async () => {
    // Regression: `accountId` has a SECOND writer — the `configure` step's select, which
    // predates the chips. Picking there an account that is already an extra chip used to
    // render the id twice (duplicate React keys) and made its ✕ un-removable, because both
    // copies resolved to removeAccount's anchor branch.
    const { container } = open()
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('option', { name: /PocketPay Wallet/ }))
    // → configure, re-point the anchor at the account already held as an extra chip, → back
    fireEvent.click(screen.getByRole('button', { name: /map the columns yourself/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Import into account' }))
    fireEvent.click(screen.getByRole('option', { name: /PocketPay Wallet/ }))
    // the configure step offers Back twice (its source line and its footer) — either returns
    fireEvent.click(screen.getAllByRole('button', { name: 'Back' })[0]!)
    const chips = Array.from(document.body.querySelectorAll('.imp3-achip:not(.add)'))
    expect(chips.length).toBe(1)
    expect(chips[0]!.textContent).toContain('PocketPay Wallet')
    // and it is still removable, leaving no chip and disabling the CTA again
    fireEvent.click(screen.getByRole('button', { name: 'Remove PocketPay Wallet' }))
    expect(document.body.querySelector('.imp3-achip:not(.add)')).toBeNull()
    expect(
      (screen.getByRole('button', { name: /Read the statement/ }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  // SKIPPED 2026-07-27 (owner directive): same removal as above — no UI path reaches Configure
  // any more, so this gate can't be exercised through the real flow. The `disabled={!accountId}`
  // fix on ImportModal.tsx's two Configure-step shortcuts is UNTOUCHED, just unreachable.
  it.skip("the Configure step's own Lucius shortcuts are gated on having an account chosen too", async () => {
    // Regression: `map the columns yourself` reaches Configure without ever going through the
    // Choose step's own account gate, and its "Let Lucius do it instead" / "Import with Lucius
    // instead" shortcuts call the SAME server preview `runLucius()` does — a second path to an
    // account-less preview that the Choose step's CTA disabling alone would have missed.
    open()
    pick(document.body)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: /map the columns yourself/ }))
    await screen.findByRole('button', { name: 'Import into account' })
    const shortcut = await screen.findByRole('button', { name: /Lucius.*instead/ })
    expect((shortcut as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Import into account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    expect(
      (screen.getByRole('button', { name: /Lucius.*instead/ }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('an extra chip routes a row the server left UNROUTED, matching on currency only', async () => {
    // The server takes ONE `account_id` anchor and fans it out by currency; a wallet it can't
    // reach (no shared platform, no remembered map) leaves its rows with no destination. The
    // extra chip fills exactly those, and only when the currency matches — the commit
    // hard-fails a cross-currency pick.
    const onPreview = vi.fn().mockResolvedValue(
      preview({
        drafts: [
          {
            date: '2026-07-01',
            payee: null,
            description: 'ROUTED',
            amount: -12.5,
            currency: 'VND',
            dup: false,
            rowIndex: 0,
            destId: 'rvb',
            destName: 'Riverside Bank',
            external_refs: [],
            flags: [],
          },
          {
            date: '2026-07-02',
            payee: null,
            description: 'UNROUTED USD',
            amount: -20,
            currency: 'USD',
            dup: false,
            rowIndex: 1,
            external_refs: [],
            flags: [],
          },
          {
            date: '2026-07-03',
            payee: null,
            description: 'UNROUTED EUR',
            amount: -30,
            currency: 'EUR',
            dup: false,
            rowIndex: 2,
            external_refs: [],
            flags: [],
          },
        ],
        importable: 3,
        total: 3,
      }),
    )
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 3, merged: 0, duplicates: 0, skipped: 0 })
    const { container } = open({ onPreview, onDecisionCommit })
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('option', { name: /Compass USD/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText('UNROUTED USD')
    // The fill rides the EXISTING per-row destination seam, so it reaches the server as a
    // decision `edits.account_id` — the USD row picks up the USD chip; the EUR row matches
    // no chip and is left exactly as the server returned it.
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<
      Record<string, Record<string, unknown>>
    >
    const editFor = (row: number) =>
      decisions.find((d) => (d.row as unknown as number) === row)?.edits
    expect(editFor(1)?.account_id).toBe('compass')
    expect(editFor(2)?.account_id).toBeUndefined()
    expect(editFor(0)?.account_id).toBeUndefined()
    expect(container).toBeTruthy()
  })

  it('an FX-pair leg is NEVER routed by a chip — it keeps its safe skip', async () => {
    // Regression. A leg of an intra-statement FX pair is off-limits to the chip fill for two
    // independent reasons: the commit books a pair as ONE two-leg transfer through
    // `decisions._transfer_draft`, which reads both accounts off the routing context and never
    // looks at `edits.account_id`; and filling it flips the row's FATE, because `isUnrouted`
    // goes false the moment `touched.account && destId` holds — so a leg that must commit as
    // an explicit `skip` would instead emit `action: "create"`.
    const onPreview = vi.fn().mockResolvedValue(
      preview({
        drafts: [
          {
            date: '2026-07-01',
            payee: null,
            description: 'FX OUT LEG',
            amount: -500,
            currency: 'USD',
            dup: false,
            rowIndex: 0,
            pair: { role: 'out', partner_index: 1 },
            external_refs: [],
            flags: [{ type: 'no_account_for_currency' }],
          },
          {
            date: '2026-07-01',
            payee: null,
            description: 'FX IN LEG',
            amount: 12000000,
            currency: 'VND',
            dup: false,
            rowIndex: 1,
            destId: 'rvb',
            destName: 'Riverside Bank',
            pair: { role: 'in', partner_index: 0 },
            external_refs: [],
            flags: [],
          },
        ],
        importable: 2,
        total: 2,
      }),
    )
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 1 })
    const { container } = open({ onPreview, onDecisionCommit })
    pick(container)
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(screen.getByRole('option', { name: /Riverside Bank/ }))
    // A chip in the OUT leg's own currency — the exact shape that would tempt the fill.
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('option', { name: /Compass USD/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText('FX OUT LEG')
    // Both legs default to Leave out here (unrouted currency), so the commit CTA reads its
    // own "File statement — nothing new" label, not "Confirm" — it still submits the same
    // all-skip decisions to file the statement.
    fireEvent.click(await screen.findByRole('button', { name: /Confirm|File statement/ }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    const out = decisions.find((d) => d.row === 0)
    // The leg still commits as an explicit skip, carrying no account edit — never `create`.
    expect(out?.action).toBe('skip')
    expect(out?.edits).toBeUndefined()
    // …while a NON-pair row in that same currency would have been filled — the guard is the
    // pair, not the chip failing to match.
    expect(decisions.find((d) => d.row === 1)).toBeUndefined() // the IN leg is absorbed
  })
})
