// @vitest-environment jsdom
//
// An import must not be lost to a MISCLICK (owner, 2026-08-09: "let's not allow user to click
// outside to turn this off ... a lot of times i miscliecked"). Two separate protections:
//
//   1. The backdrop no longer dismisses at all (`closeOnBackdrop={false}`). Clicking the page
//      behind the modal is the accident, and there is no reading of it that means "abandon
//      this import" — so it does nothing, at every step.
//   2. A deliberate exit (✕ / Escape) still CONFIRMS whenever something would be lost — the
//      unsaved row decisions on `preview`. `processing` no longer confirms at all (B20): the
//      read is a server-side thread that outlives the connection regardless, so closing
//      during it is "Continue in background", not "abandon a paid read" — see
//      ImportModal.liveProgress.test.tsx for that step's own coverage.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

// The live progress channel reports the statement's id once the server has minted it —
// `liveStatementId` is what the next poll of /preview-progress answers (null = not yet).
const progress = vi.hoisted(() => ({ liveStatementId: 'stmt_live' as string | null }))
vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('preview-progress')
        ? { stages: [], elapsed_s: 1, done: false, statement_id: progress.liveStatementId }
        : {},
  })),
}))

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  progress.liveStatementId = 'stmt_live'
})

const acct = {
  id: 'a1',
  name: 'Mega Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
} as unknown as Account
const CSV = 'Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n'

const PREVIEW = {
  statementId: 'st-1',
  drafts: [
    {
      date: '2026-07-01',
      payee: null,
      description: 'COFFEE',
      amount: -12.5,
      currency: 'SGD',
      kind: 'expense',
      category: null,
      dup: false,
      rowIndex: 0,
      external_refs: [{ source: 'csv', ref: 'c1' }],
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
  instructions: null,
}

/** Renders and advances to the choose step with a file picked. `onPreview` decides whether
 *  the next click parks on `processing` (a pending promise) or lands on `preview`. */
async function mount(onPreview: ReturnType<typeof vi.fn>, onClose = vi.fn()) {
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={onClose as never}
      onPreview={onPreview as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'mega.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  fireEvent.change(fileInput, { target: { files: [file] } })
  await screen.findByRole('button', { name: /Read the statement/ })
  return onClose
}

function escape() {
  fireEvent.keyDown(document.activeElement || document.body, { key: 'Escape', code: 'Escape' })
}

describe('ImportModal — an accidental exit cannot end the import', () => {
  it('Escape during the READ closes outright — no confirm, the read keeps going server-side', async () => {
    // A promise that never settles keeps the modal on `processing`, exactly like a real
    // multi-second LLM read.
    const onPreview = vi.fn(() => new Promise(() => {}))
    const onClose = await mount(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText(/Lucius is reading/)

    escape()

    // No "Stop reading?" prompt at all any more (B20) — closes immediately.
    expect(screen.queryByText('Stop reading this statement?')).toBeNull()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('"Continue in background" closes the same way, and reports the in-flight statement id', async () => {
    const onPreview = vi.fn(() => new Promise(() => {}))
    const onContinueInBackground = vi.fn()
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        onClose={vi.fn() as never}
        onPreview={onPreview as never}
        onContinueInBackground={onContinueInBackground}
        onDecisionCommit={
          vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
        }
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([CSV], 'mega.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => CSV })
    fireEvent.change(fileInput, { target: { files: [file] } })
    await screen.findByRole('button', { name: /Read the statement/ })
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText(/Lucius is reading/)

    fireEvent.click(screen.getByRole('button', { name: 'Continue in background' }))

    await waitFor(() =>
      expect(onContinueInBackground).toHaveBeenCalledWith('stmt_live', 'mega.csv'),
    )
  })

  // QA 2026-10-25: tapped in the first second of an upload, before the server had minted the
  // statement's id, the read went untracked — no "Ready to review" could ever come for it.
  it('tapped before the id exists, it waits for the id and hands THAT off', async () => {
    progress.liveStatementId = null
    const onPreview = vi.fn(() => new Promise(() => {}))
    const onContinueInBackground = vi.fn()
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        onClose={vi.fn() as never}
        onPreview={onPreview as never}
        onContinueInBackground={onContinueInBackground}
        onDecisionCommit={vi.fn() as never}
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([CSV], 'mega.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => CSV })
    fireEvent.change(fileInput, { target: { files: [file] } })
    fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
    await screen.findByText(/Lucius is reading/)

    fireEvent.click(screen.getByRole('button', { name: 'Continue in background' }))
    expect(await screen.findByRole('button', { name: 'Continuing…' })).toBeTruthy()
    expect(onContinueInBackground).not.toHaveBeenCalled()

    progress.liveStatementId = 'stmt_minted'
    await waitFor(
      () => expect(onContinueInBackground).toHaveBeenCalledWith('stmt_minted', 'mega.csv'),
      { timeout: 3000 },
    )
  })

  it('Escape on the REVIEW still asks, with the row-decisions wording', async () => {
    const onPreview = vi.fn().mockResolvedValue(PREVIEW)
    const onClose = await mount(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText('COFFEE')

    escape()

    await screen.findByText('Close and lose this review?')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('dismissing the prompt keeps the import exactly where it was', async () => {
    const onPreview = vi.fn().mockResolvedValue(PREVIEW)
    const onClose = await mount(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText('COFFEE')
    escape()
    await screen.findByText('Close and lose this review?')

    fireEvent.click(screen.getByRole('button', { name: /Cancel|Keep|Stay/ }))

    await waitFor(() => expect(screen.queryByText('Close and lose this review?')).toBeNull())
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText('COFFEE')).toBeTruthy()
  })

  it('the dialog opts OUT of backdrop dismissal', async () => {
    const onPreview = vi.fn().mockResolvedValue(PREVIEW)
    await mount(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText('COFFEE')

    // Radix marks a content node that ignores outside interaction; the modal must carry it.
    // Asserting the wiring rather than simulating a pointer sequence jsdom cannot dispatch
    // faithfully — the real click path is covered by the qa harness.
    const dialog = screen.getByRole('dialog')
    expect(dialog.getAttribute('data-onw-dismissable')).toBe('false')
  })
})
