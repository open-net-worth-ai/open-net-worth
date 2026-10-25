// @vitest-environment jsdom
//
// B20 async — reopening a statement by id (`resumeStatementId`): `ready` goes straight to
// review via POST /api/statements/{id}/preview (no re-upload); `parsing` shows the same
// processing UI, polled by id, and auto-transitions to review once the poll reports `ready`.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const ACCOUNTS = [
  {
    id: 'rvb',
    name: 'Riverside Bank',
    currency: 'USD',
    color: '#3b86dd',
    kind: 'asset',
    type: 'cash',
    archived: false,
  },
] as unknown as Account[]

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: async () => body })
}

const PREVIEW_RESULT = {
  statementId: 'stmt_resume_1',
  drafts: [
    {
      date: '2026-07-01',
      payee: null,
      description: 'COFFEE',
      amount: -12.5,
      currency: 'USD',
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
}

function open(resumeStatementId: string, onReopenPreview: ReturnType<typeof vi.fn>) {
  return render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      resumeStatementId={resumeStatementId}
      resumeFilename="jan.pdf"
      onReopenPreview={onReopenPreview as never}
      onClose={() => {}}
      onPreview={vi.fn() as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
    />,
  )
}

describe('ImportModal — resume by id (B20 async)', () => {
  it('opens straight into processing, then jumps to review once ready', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/parse-status')
        ? jsonResponse({ parse_status: 'ready', progress: null, error: null })
        : jsonResponse({}),
    )
    const onReopenPreview = vi.fn().mockResolvedValue(PREVIEW_RESULT)
    open('stmt_resume_1', onReopenPreview)

    expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
    await waitFor(() => expect(onReopenPreview).toHaveBeenCalledWith('stmt_resume_1', undefined))
    await screen.findByText('COFFEE')
  })

  // QA 2026-10-25: reopening a FINISHED statement flashed "Lucius is reading" (and a leftover
  // elapsed clock) while it fetched the kept review — it isn't reading anything.
  it('a ready statement says it is opening, never "Lucius is reading"', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    let release!: (v: unknown) => void
    const onReopenPreview = vi.fn(() => new Promise((r) => (release = r)))
    open('stmt_resume_ready', onReopenPreview)

    expect(await screen.findByText('Opening your review')).toBeTruthy()
    expect(screen.queryByText(/Lucius is reading/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Continue in background' })).toBeNull()

    await waitFor(() => expect(onReopenPreview).toHaveBeenCalled())
    release({ ...PREVIEW_RESULT, statementId: 'stmt_resume_ready' })
    await screen.findByText('COFFEE')
  })

  it('a statement the server still reports as parsing shows the read, with Continue in background', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({
        parse_status: 'parsing',
        progress: {
          stages: [],
          started_at: new Date().toISOString(),
          elapsed_s: 5,
          done: false,
          error: null,
          statement_id: 'stmt_resume_parsing',
        },
        error: null,
      }),
    )
    open('stmt_resume_parsing', vi.fn())

    expect(await screen.findByText(/Lucius is reading/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Continue in background' })).toBeTruthy()
  })

  // QA 2026-10-25: Review → Book → "Import another statement" → a new file fired the new read
  // AND a reopen of the statement just booked — two statements racing in one dialog.
  it('"Import another statement" after a reopen reads only the new file', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    const onReopenPreview = vi
      .fn()
      .mockResolvedValue({ ...PREVIEW_RESULT, statementId: 'stmt_booked' })
    const onPreview = vi.fn(() => new Promise(() => {}))
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        resumeStatementId="stmt_booked"
        resumeFilename="jan.pdf"
        onReopenPreview={onReopenPreview as never}
        onClose={() => {}}
        onPreview={onPreview as never}
        onDecisionCommit={
          vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
        }
      />,
    )
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Import another' }))

    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const csv = 'date,description,amount\n2026-07-02,TEA,-3.00\n'
    const file = new File([csv], 'feb.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => csv })
    fireEvent.change(fileInput, { target: { files: [file] } })
    fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Lucius is reading/)).toBeTruthy()
    expect(screen.getByText('feb.csv')).toBeTruthy()
    expect(onReopenPreview).toHaveBeenCalledTimes(1)
  })

  it('sending that new read to the background names the NEW file, not the reopened one', async () => {
    authFetch.mockImplementation((url: string) =>
      jsonResponse(
        url.includes('preview-progress')
          ? { stages: [], elapsed_s: 1, done: false, statement_id: 'stmt_new' }
          : { parse_status: 'ready', progress: null, error: null },
      ),
    )
    const onContinueInBackground = vi.fn()
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        resumeStatementId="stmt_booked"
        resumeFilename="jan.pdf"
        onReopenPreview={
          vi.fn().mockResolvedValue({ ...PREVIEW_RESULT, statementId: 'stmt_booked' }) as never
        }
        onContinueInBackground={onContinueInBackground}
        onClose={() => {}}
        onPreview={vi.fn(() => new Promise(() => {})) as never}
        onDecisionCommit={
          vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
        }
      />,
    )
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Import another' }))
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const csv = 'date,description,amount\n2026-07-02,TEA,-3.00\n'
    const file = new File([csv], 'feb.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => csv })
    fireEvent.change(fileInput, { target: { files: [file] } })
    fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
    await screen.findByText(/Lucius is reading/)

    fireEvent.click(screen.getByRole('button', { name: 'Continue in background' }))
    await waitFor(
      () => expect(onContinueInBackground).toHaveBeenCalledWith('stmt_new', 'feb.csv'),
      { timeout: 3000 },
    )
  })

  // Owner, 2026-10-25: a failed reopen dropped to the Choose step with no file — a nameless
  // "CSV" chip and "Checking if this source is remembered…" spinning forever.
  it('a reopen that needs an account asks for one on the reopen itself, then opens the review', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    const onReopenPreview = vi
      .fn()
      .mockResolvedValueOnce({ ...PREVIEW_RESULT, needsAccount: true })
      .mockResolvedValueOnce({ ...PREVIEW_RESULT, resolvedAccountId: 'rvb' })
    open('stmt_no_acct', onReopenPreview)

    expect(await screen.findByText(/Which account is this statement for/)).toBeTruthy()
    expect(screen.getByText('jan.pdf')).toBeTruthy()
    expect(screen.queryByText(/Checking if this source is remembered/)).toBeNull()
    const openBtn = screen.getByRole('button', { name: 'Open review' }) as HTMLButtonElement
    expect(openBtn.disabled).toBe(true)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'rvb' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open review' }))

    await waitFor(() => expect(onReopenPreview).toHaveBeenLastCalledWith('stmt_no_acct', 'rvb'))
    await screen.findByText('COFFEE')
  })

  it('a reopen that errors offers Try again on the reopen, never the empty Choose step', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    const onReopenPreview = vi
      .fn()
      .mockRejectedValueOnce(new Error('500'))
      .mockResolvedValueOnce({ ...PREVIEW_RESULT })
    open('stmt_flaky', onReopenPreview)

    expect(await screen.findByText(/The review didn’t load/)).toBeTruthy()
    expect(screen.queryByText(/Checking if this source is remembered/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByText('COFFEE')
    expect(onReopenPreview).toHaveBeenCalledTimes(2)
  })

  it('a statement whose read failed says so, with no empty Choose step', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'failed', progress: null, error: 'boom' }),
    )
    const onReopenPreview = vi.fn()
    open('stmt_failed', onReopenPreview)

    expect(await screen.findByText(/Its read didn’t finish/)).toBeTruthy()
    expect(
      screen.getAllByRole('button', { name: 'Close' }).some((b) => b.textContent === 'Close'),
    ).toBe(true)
    expect(screen.queryByText(/Checking if this source is remembered/)).toBeNull()
    expect(onReopenPreview).not.toHaveBeenCalled()
  })

  // Owner, 2026-10-25: Back from a reopened statement's review landed on an empty file picker —
  // a nameless "CSV" chip and "Checking if this source is remembered…" forever.
  it('Back from a reopened review shows that statement, and Back to review restores it', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    const onReopenPreview = vi.fn().mockResolvedValue({ ...PREVIEW_RESULT, total: 210 })
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        resumeStatementId="stmt_back"
        resumeFilename="june.xlsx"
        resumeUploadedAt="2026-06-02T17:55:51Z"
        onReopenPreview={onReopenPreview as never}
        onClose={() => {}}
        onPreview={vi.fn() as never}
        onDecisionCommit={vi.fn() as never}
      />,
    )
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(await screen.findByText('june.xlsx')).toBeTruthy()
    expect(screen.getByText(/^Read .* · 210 rows$/)).toBeTruthy()
    expect(screen.queryByText(/Checking if this source is remembered/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Change' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Read the statement/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Back to review' }))
    await screen.findByText('COFFEE')
    expect(onReopenPreview).toHaveBeenCalledTimes(1) // restored from memory, nothing refetched
  })

  it('Back from a reopened review shows the instructions it was read with', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'ready', progress: null, error: null }),
    )
    const onReopenPreview = vi.fn().mockResolvedValue({
      ...PREVIEW_RESULT,
      resolvedAccountId: 'rvb',
      instructions: {
        text: 'Top-ups are transfers',
        lines: 1,
        used_in_parse: false,
        remembered: false,
      },
    })
    open('stmt_instr', onReopenPreview)
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    const box = (await screen.findByLabelText('Tell Lucius')) as HTMLTextAreaElement
    expect(box.value).toBe('Top-ups are transfers')
    expect(
      (screen.getByRole('checkbox', { name: /Remember for this source/ }) as HTMLInputElement)
        .checked,
    ).toBe(false)
  })

  it('Read again re-reads the saved file in the background, then opens the new review', async () => {
    let status = 'ready'
    authFetch.mockImplementation(() =>
      jsonResponse({
        parse_status: status,
        progress: status === 'parsing' ? { stages: [], elapsed_s: 1, done: false } : null,
        error: null,
      }),
    )
    const onReopenPreview = vi.fn(
      async (_id: string, _acct?: string, opts?: { background?: boolean }) => {
        if (opts?.background) {
          status = 'parsing'
          return { ...PREVIEW_RESULT, attached: false, drafts: [] }
        }
        return { ...PREVIEW_RESULT, total: 1 }
      },
    )
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        resumeStatementId="stmt_again"
        resumeFilename="june.xlsx"
        onReopenPreview={onReopenPreview as never}
        onClose={() => {}}
        onPreview={vi.fn() as never}
        onDecisionCommit={vi.fn() as never}
      />,
    )
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.change(await screen.findByLabelText('Tell Lucius'), {
      target: { value: 'Dates are DD/MM' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Read again' }))

    expect(await screen.findByText(/Lucius is reading/)).toBeTruthy()
    expect(onReopenPreview).toHaveBeenLastCalledWith(
      'stmt_again',
      'rvb',
      expect.objectContaining({
        forceReparse: true,
        background: true,
        instructions: 'Dates are DD/MM',
      }),
    )
    status = 'ready'
    await screen.findByText('COFFEE', {}, { timeout: 5000 })
    // the review fetched once ready is the one built for the instructions it was re-read with
    const last = onReopenPreview.mock.lastCall?.[2] as Record<string, unknown> | undefined
    expect(last).toEqual(expect.objectContaining({ instructions: 'Dates are DD/MM' }))
    expect(last?.background).toBeUndefined()
  }, 12000)

  // QA 2026-10-25: Read again switched to the reading screen before the server had the new
  // read; the first poll saw the OLD "ready" and reopened the review being replaced.
  it('Read again never reopens before the server has accepted the re-read', async () => {
    let status = 'ready'
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: status, progress: null, error: null }),
    )
    let accept!: () => void
    const order: string[] = []
    const onReopenPreview = vi.fn(
      (_id: string, _acct?: string, opts?: { background?: boolean }) => {
        if (opts?.background) {
          order.push('read-again')
          return new Promise(
            (r) =>
              (accept = () => {
                status = 'parsing'
                r({ ...PREVIEW_RESULT, drafts: [] })
              }),
          )
        }
        order.push('reopen')
        return Promise.resolve({ ...PREVIEW_RESULT, resolvedAccountId: 'rvb' })
      },
    )
    open('stmt_race', onReopenPreview)
    await screen.findByText('COFFEE')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Read again' }))
    expect(await screen.findByRole('button', { name: 'Starting…' })).toBeTruthy()
    await new Promise((r) => setTimeout(r, 1200)) // a poll would have fired by now
    expect(order).toEqual(['reopen', 'read-again'])
    accept()
    expect(await screen.findByText(/Lucius is reading/)).toBeTruthy()
  })

  it('a reopen that attaches to a still-running read keeps watching it until it ends', async () => {
    const statuses = ['ready', 'parsing', 'ready']
    let poll = 0
    authFetch.mockImplementation(() =>
      jsonResponse({
        parse_status: statuses[Math.min(poll++, statuses.length - 1)],
        progress: null,
        error: null,
      }),
    )
    const onReopenPreview = vi
      .fn()
      .mockResolvedValueOnce({ ...PREVIEW_RESULT, drafts: [], attached: true })
      .mockResolvedValue({ ...PREVIEW_RESULT })
    open('stmt_attached', onReopenPreview)
    await screen.findByText('COFFEE', {}, { timeout: 6000 })
    expect(onReopenPreview).toHaveBeenCalledTimes(2)
  }, 10000)

  it('a still-parsing id polls by statement id and auto-transitions once ready', async () => {
    let calls = 0
    authFetch.mockImplementation((url: string) => {
      if (!url.includes('/parse-status')) return jsonResponse({})
      calls += 1
      if (calls === 1) {
        return jsonResponse({
          parse_status: 'parsing',
          progress: {
            stages: [
              {
                stage: 'read',
                label: 'Reading page 1 of 3',
                started_at: new Date().toISOString(),
                done_at: null,
                page: 1,
                pages: 3,
                retry: null,
              },
            ],
            started_at: new Date().toISOString(),
            elapsed_s: 5,
            done: false,
            error: null,
            statement_id: 'stmt_resume_2',
          },
          error: null,
        })
      }
      return jsonResponse({ parse_status: 'ready', progress: null, error: null })
    })
    const onReopenPreview = vi
      .fn()
      .mockResolvedValue({ ...PREVIEW_RESULT, statementId: 'stmt_resume_2' })
    open('stmt_resume_2', onReopenPreview)

    // A still-parsing id opens straight into the processing step...
    expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
    // ...and auto-transitions to review the moment a poll (by statement id) reports ready —
    // the two mocked polls resolve near-instantly here, so the intermediate `parsing` phase
    // is real but not reliably observable mid-test; the auto-transition itself is the thing
    // this test pins.
    await waitFor(() => expect(onReopenPreview).toHaveBeenCalledWith('stmt_resume_2', undefined), {
      timeout: 3000,
    })
    await screen.findByText('COFFEE')
    expect(calls).toBeGreaterThanOrEqual(2) // both the one-shot check and at least one poll ran
  })

  it('fires ONE reopen for a ready statement even while the polled status also reports ready', async () => {
    // The one-shot status check and the 1s snapshot poll both see `ready`. The reopen is a heavy
    // server call, so a slow one used to be POSTed twice, each slowing the other.
    authFetch.mockImplementation((url: string) =>
      url.includes('/parse-status')
        ? jsonResponse({ parse_status: 'ready', progress: null, error: null })
        : jsonResponse({}),
    )
    let finish: (v: typeof PREVIEW_RESULT) => void = () => {}
    const onReopenPreview = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve as typeof finish
        }),
    )
    open('stmt_resume_slow', onReopenPreview)

    await waitFor(() => expect(onReopenPreview).toHaveBeenCalledTimes(1))
    await new Promise((r) => setTimeout(r, 2500)) // past two poll ticks, reopen still in flight
    expect(onReopenPreview).toHaveBeenCalledTimes(1)
    finish(PREVIEW_RESULT)
    await screen.findByText('COFFEE')
    expect(onReopenPreview).toHaveBeenCalledTimes(1)
  })

  it('a resumed statement never shows the old "Stop reading?" confirm on close', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'parsing', progress: null, error: null }),
    )
    open('stmt_resume_3', vi.fn())
    await waitFor(() => expect(document.body.querySelector('.imp3-reading')).toBeTruthy())
    expect(screen.queryByText('Stop reading this statement?')).toBeNull()
  })
})

describe('ImportModal — a sync upload attaches to an already-parsing async job (code review round B, M4)', () => {
  it('switches into the resume-by-id flow on a 202 attach response, never renders it as an empty preview', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/parse-status')
        ? jsonResponse({ parse_status: 'ready', progress: null, error: null })
        : jsonResponse({}),
    )
    // The plain sync preview call comes back ATTACHED — someone else's upload of the exact
    // same bytes is already `parsing` server-side — never a real (empty) preview shape.
    const onPreview = vi.fn().mockResolvedValue({ attached: true, statementId: 'stmt_attach_1' })
    const onReopenPreview = vi.fn().mockResolvedValue(PREVIEW_RESULT)
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        onClose={() => {}}
        onPreview={onPreview as never}
        onReopenPreview={onReopenPreview as never}
        onDecisionCommit={
          vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
        }
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['%PDF-1.4 fake'], 'statement.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    const luBtn = await screen.findByText(/Read the statement/i)
    fireEvent.click(luBtn)

    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // Never a "0 to import" empty preview render — it moved straight to the resume flow.
    expect(screen.queryByText(/to import/i)).toBeNull()
    await waitFor(() =>
      expect(onReopenPreview).toHaveBeenCalledWith('stmt_attach_1', 'rvb' as unknown),
    )
    await screen.findByText('COFFEE')
  })
})
