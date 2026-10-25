// StatementsSection — the account's imported-statement history (RFC statement-import-unified D8).
// Collapsed by default; expanding fetches GET /api/accounts/{id}/statements. Each row: file,
// period, amount due, the reconcile badge, and a download link to the original bytes.
import { useCallback, useEffect, useState } from 'react'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { useQueryCache } from '../../../state/queryCache'
import { ZoomImage } from '../../ui/ZoomImage'
import { DocumentReader } from '../../vault/DocumentReader'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { authFetch } from '../../../api/client'
import { fetchVaultItem, type VaultItem } from '../../../api/vault'
import { DocumentPreview } from '../../vault/DocumentPreview'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { LuciusCta } from '../../intro/LuciusCta'
import { ActionMenu, Badge, Cluster, EmptyState, ErrorState, Loading, Money, Stack } from '../../ui'
import { fDate, fDateTime } from '../../../utils/format'
import { Download, FileText, ChevronDown, ChevronRight } from '../../../ui/icons'
import { fCur } from './data'
import { formatElapsed, progressPhaseLabel } from './importHelpers'
import { useDocumentVisible, useStatementParseStatus } from '../../../hooks/useImportProgress'

interface StatementRow {
  id: string
  filename?: string
  created_at?: string
  period_start?: string | null
  period_end?: string | null
  statement_date?: string | null
  payment_due_date?: string | null
  amount_due?: number | null
  minimum_payment?: number | null
  closing_balance?: number | null
  currency?: string | null
  reconcile?: { status: string; delta?: number } | null
  batch_ids?: string[]
  /** Committed at least once — a batch id, or `filed_at` for a filing that wrote nothing. */
  filed?: boolean
  account_ids?: string[]
  llm_cost_usd?: number | null
  /** B20 async lifecycle: parsing | ready | failed | interrupted | null/undefined
   *  (legacy/pre-B20 — reads as an ordinary committed row). */
  parse_status?: string | null
  parse_error?: string | null
  /** Rows the file holds — every state, so a statement waiting for review says its size. */
  rows_read?: number | null
  /** When the first booking from it happened; null until something was booked. */
  imported_at?: string | null
  /** Transactions it added that are still in the ledger / existing entries it linked to. */
  added?: number | null
  linked?: number | null
}

// The facts every row shows, in the same order whatever its state (owner, 2026-10-25: a
// statement waiting for review showed no date, period or size). Each returns null when the
// statement has nothing to say for it.
function periodFact(s: StatementRow): string | null {
  if (s.period_start) {
    return `${fDate(s.period_start)} → ${s.period_end ? fDate(s.period_end, 'short', 'always') : '…'}`
  }
  return s.statement_date ? `Statement ${fDate(s.statement_date)}` : null
}
function rowsFact(s: StatementRow): string | null {
  if (!s.rows_read) return null
  const rows = `${s.rows_read} row${s.rows_read === 1 ? '' : 's'}`
  if (s.added == null) return rows
  return s.linked ? `${rows} · ${s.added} new · ${s.linked} linked` : `${rows} · ${s.added} new`
}
function uploadedFact(s: StatementRow, withTime = false): string | null {
  if (!s.created_at) return null
  return `Uploaded ${withTime ? fDateTime(s.created_at) : fDate(s.created_at)}`
}
function aiCostFact(s: StatementRow): string | null {
  if (typeof s.llm_cost_usd !== 'number') return null
  return s.llm_cost_usd > 0
    ? `AI read ≈ $${Math.max(s.llm_cost_usd, 0.01).toFixed(2)}`
    : 'No AI cost'
}
function Facts({ facts }: { facts: (string | null)[] }) {
  return (
    <>
      {facts.filter(Boolean).map((f) => (
        <span key={f} className="imp-metafact">
          {f}
        </span>
      ))}
    </>
  )
}
/** The balance check in words, or nothing when the file printed no balances to check. */
function BalanceBadge({ s }: { s: StatementRow }) {
  const r = s.reconcile
  if (!r) return null
  const hint = 'Opening + money in − money out, against the closing balance the statement prints'
  if (r.status === 'ok')
    return (
      <span title={hint}>
        <Badge tone="pos">✓ Balance matches</Badge>
      </span>
    )
  if (r.status === 'off')
    return (
      <span title={hint}>
        <Badge tone="warn">
          {typeof r.delta === 'number'
            ? `Balance off by ${fCur(Math.abs(r.delta), s.currency || 'SGD', { cents: true })}`
            : 'Balance off'}
        </Badge>
      </span>
    )
  if (r.status === 'ok_if_flipped')
    return (
      <span title={hint}>
        <Badge tone="warn">Balance matches with signs flipped</Badge>
      </span>
    )
  return null
}

/** A row still mid-lifecycle (not yet a plain, committed statement) — these sort ABOVE
 *  ordinary rows and get their own action instead of Download/Delete. `ready` only counts
 *  while nothing has been booked from it yet (`batch_ids` empty) — a `ready` statement that
 *  was already committed is just a normal row that happens to carry a status stamp. */
function isLifecycleRow(s: StatementRow): boolean {
  if (
    s.parse_status === 'parsing' ||
    s.parse_status === 'failed' ||
    s.parse_status === 'interrupted'
  ) {
    return true
  }
  return s.parse_status === 'ready' && !(s.filed || (s.batch_ids && s.batch_ids.length > 0))
}

interface DiffRow {
  id: string
  date?: string
  note?: string
  amount?: number | null
  currency?: string | null
  kind?: string
  source?: string
}

const _TEXT_EXTS = ['csv', 'txt', 'qif', 'ofx', 'sta', 'mt940', 'xml', 'json', 'camt']
const _TEXT_RENDER_CAP = 300_000 // chars — a statement export is small; anything bigger downloads

/** The fetch-then-render state machine shared by every in-app blob viewer (statements,
 *  receipts): PDFs and images render via a force-typed blob URL (never served inline from
 *  the app origin as a scriptable document — the Blob's type is set by US, not trusted from
 *  the response's own Content-Type); text formats preview as text; everything else offers
 *  the download. `isImage` is caller-supplied (a receipt knows its media_type; a statement
 *  never is one, so always passes false) rather than re-derived from `ext`, since a photo's
 *  extension is a much weaker signal than the stored media_type. */
export type FileViewerState =
  | { kind: 'loading' }
  | { kind: 'pdf'; url: string }
  | { kind: 'image'; url: string }
  | { kind: 'text'; text: string }
  | { kind: 'other' }
  | { kind: 'error' }

export function useFileViewerState(
  fileUrl: string,
  ext: string,
  isImage: boolean,
): FileViewerState {
  const [state, setState] = useState<FileViewerState>({ kind: 'loading' })
  useEffect(() => {
    let alive = true
    let url = ''
    setState({ kind: 'loading' })
    authFetch(fileUrl)
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        if (ext === 'pdf') {
          const blob = new Blob([await r.arrayBuffer()], { type: 'application/pdf' })
          url = URL.createObjectURL(blob)
          if (alive) setState({ kind: 'pdf', url })
        } else if (isImage) {
          // Pinned to a fixed-safe type, never the response's own Content-Type header — the
          // server already refuses to serve a non-allowlisted media_type as anything but
          // application/octet-stream (backend/api/receipts.py), but an <img> consumer of this
          // blob URL should never be one line away from trusting a caller-declared type either.
          const blob = new Blob([await r.arrayBuffer()], { type: 'image/png' })
          url = URL.createObjectURL(blob)
          if (alive) setState({ kind: 'image', url })
        } else if (_TEXT_EXTS.includes(ext)) {
          const text = await r.text()
          if (alive)
            setState(text.length <= _TEXT_RENDER_CAP ? { kind: 'text', text } : { kind: 'other' })
        } else if (alive) setState({ kind: 'other' })
      })
      .catch(() => {
        if (alive) setState({ kind: 'error' })
      })
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [fileUrl, ext, isImage])
  return state
}

/** Renders one of `FileViewerState`'s branches — the body every in-app blob viewer (this
 *  file's StatementViewer, accounts/ReceiptViewer) shares so the states never fork into
 *  divergent copies. `errorMessage` overrides the default download-fallback hint for a
 *  viewer whose copy differs (none currently do — both use the same line). */
export function FileViewerBody({
  state,
  filename,
  errorMessage,
  pageItemId,
}: {
  state: FileViewerState
  filename?: string
  errorMessage?: string
  /** The file's Documents id (`statement-…`/`receipt-…`). On a phone a PDF is then shown as
   *  server-drawn pages, stacked and zoomable (components/vault/DocumentReader) — the phone's
   *  own PDF view kept the page at its full width, so it scrolled sideways (owner report), and
   *  the app has the browser's pinch-zoom off, so the pages zoom themselves ("can't zoom in"). */
  pageItemId?: string
}) {
  const isMobile = useIsMobile()
  if (state.kind === 'loading') return <div className="acct-stmtview-hint">Loading…</div>
  if (state.kind === 'error')
    return (
      <div className="acct-stmtview-hint">
        {errorMessage || "Couldn't load the file — try the download instead."}
      </div>
    )
  if (state.kind === 'other')
    return (
      <div className="acct-stmtview-hint">
        No in-app preview for this file type — use the download.
      </div>
    )
  if (state.kind === 'pdf' && isMobile && pageItemId)
    return <DocumentReader itemId={pageItemId} title={filename || 'file'} />
  if (state.kind === 'pdf')
    return <iframe className="acct-stmtview-frame" src={state.url} title={filename || 'file'} />
  if (state.kind === 'image')
    return (
      <div className="acct-receiptview-zoom">
        <ZoomImage className="acct-receiptview-img" src={state.url} alt={filename || 'receipt'} />
      </div>
    )
  return <pre className="acct-stmtview-text">{state.text}</pre>
}

/** The statement's own file, through the ONE previewer the Documents tab uses — PDF pages, a
 *  CSV/Excel spreadsheet grid, images, text (owner, 2026-10-26: "even the statement viewer is
 *  not supporting full files like we have in that documents tab"). */
function StatementFile({ statementId }: { statementId: string }) {
  const [loaded, setLoaded] = useState<{ id: string; item: VaultItem | null } | null>(null)
  useEffect(() => {
    let alive = true
    fetchVaultItem(`statement-${statementId}`).then(
      (item) => alive && setLoaded({ id: statementId, item }),
      () => alive && setLoaded({ id: statementId, item: null }),
    )
    return () => {
      alive = false
    }
  }, [statementId])
  if (loaded?.id !== statementId) return <div className="acct-stmtview-hint">Loading…</div>
  if (!loaded.item)
    return (
      <div className="acct-stmtview-hint">Couldn't load the file — try the download instead.</div>
    )
  return <DocumentPreview item={loaded.item} />
}

/** In-app statement viewer: the file (see StatementFile), its facts and the ledger check.
 *  Callers may pass just {id} (the due banner) — the viewer hydrates its own facts. */
export function StatementViewer({
  stmt: stmtProp,
  onClose,
}: {
  stmt: StatementRow
  onClose: () => void
}) {
  const [hydrated, setHydrated] = useState<StatementRow | null>(null)
  const stmt = hydrated ?? stmtProp
  useEffect(() => {
    if (stmtProp.filename) return // caller already has the row
    let alive = true
    authFetch(`/api/statements/${stmtProp.id}`)
      .then(async (r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (alive && body) setHydrated({ ...stmtProp, ...body })
      })
      .catch(() => {
        /* facts are a nicety — the file fetch below carries the modal */
      })
    return () => {
      alive = false
    }
  }, [stmtProp])

  const facts = [
    stmt.period_start ? `${stmt.period_start} → ${stmt.period_end || '…'}` : null,
    stmt.statement_date ? `Issued ${stmt.statement_date}` : null,
    stmt.amount_due != null
      ? `Due ${stmt.payment_due_date || '—'} • ${fCur(stmt.amount_due, stmt.currency || 'SGD', { cents: true })}`
      : null,
    stmt.minimum_payment != null && stmt.minimum_payment > 0
      ? `min ${fCur(stmt.minimum_payment, stmt.currency || 'SGD', { cents: true })}`
      : null,
  ]
    .filter(Boolean)
    .join('  •  ')

  // Ledger check (owner ask): ledger transactions in this statement's window that the
  // statement does NOT contain — slipped duplicates to remove, or things to look into.
  const [diff, setDiff] = useState<DiffRow[] | null>(null)
  const [diffOpen, setDiffOpen] = useState(false)
  const loadDiff = useCallback(() => {
    authFetch(`/api/statements/${stmt.id}/ledger-diff`)
      .then(async (r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (body?.status === 'ok') setDiff(body.rows || [])
      })
      .catch(() => {
        /* advisory panel — the viewer works without it */
      })
  }, [stmt.id])
  useEffect(() => {
    loadDiff()
  }, [loadDiff])
  const removeTxn = async (id: string) => {
    if (
      !window.confirm(
        'Remove this transaction from your ledger? (It looks like a duplicate of a statement row.)',
      )
    )
      return
    const r = await authFetch(`/api/transactions/${id}`, { method: 'DELETE' })
    if (r.ok) loadDiff()
  }

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title={stmt.filename || 'Statement'}
      subtitle={facts || undefined}
      footer={
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            const a = document.createElement('a')
            a.href = `/api/statements/${stmt.id}/file`
            a.download = stmt.filename || 'statement'
            a.click()
          }}
        >
          <Download size={14} aria-hidden="true" /> Download original
        </Button>
      }
    >
      {diff !== null && diff.length > 0 && (
        <div className="acct-stmtview-check">
          <button
            type="button"
            className="acct-stmtview-check-head"
            onClick={() => setDiffOpen((o) => !o)}
            aria-expanded={diffOpen}
          >
            <Badge tone="warn">⚠ ledger check</Badge>
            <span className="acct-stmtview-check-t">
              {diff.length} transaction{diff.length === 1 ? '' : 's'} in your ledger during this
              period {diff.length === 1 ? 'is' : 'are'} not on this statement — duplicates to
              remove, or worth a closer look.
            </span>
            <span className="acct-stmtview-check-caret">
              {diffOpen ? (
                <ChevronDown size="1em" aria-hidden="true" />
              ) : (
                <ChevronRight size="1em" aria-hidden="true" />
              )}
            </span>
          </button>
          {diffOpen &&
            diff.map((d) => (
              <div key={d.id} className="acct-stmtview-check-row">
                <span className="nums">{d.date}</span>
                <span className="acct-stmtview-check-note">{d.note || '—'}</span>
                <Badge tone="muted">{d.source || d.kind || 'row'}</Badge>
                <Money className="nums">
                  {d.amount != null ? fCur(d.amount, d.currency || 'SGD', { cents: true }) : ''}
                </Money>
                <Button size="sm" variant="danger" onClick={() => removeTxn(d.id)}>
                  Remove
                </Button>
              </div>
            ))}
        </div>
      )}
      <StatementFile statementId={stmt.id} />
    </Modal>
  )
}

/** One lifecycle row (B20 async): `parsing` polls its own live line every 4s (paused while
 *  the tab is hidden); `ready`-but-uncommitted offers "Review →"; `interrupted`/`failed`
 *  offer "Re-run". Every one but a row still being read also has the ⋯ menu a committed row
 *  has (owner, 2026-10-26: "why can't i delete the ready to review ones?"). */
function LifecycleStatementRow({
  s,
  onReview,
  onRerun,
  onDownload,
  onDelete,
}: {
  s: StatementRow
  onReview?: (statementId: string, filename?: string, uploadedAt?: string) => void
  onRerun: (s: StatementRow) => void
  onDownload: (s: StatementRow) => void
  onDelete: (s: StatementRow) => void
}) {
  const visible = useDocumentVisible()
  const isParsing = s.parse_status === 'parsing'
  const [errorOpen, setErrorOpen] = useState(false)
  const { snapshot } = useStatementParseStatus(isParsing ? s.id : null, isParsing && visible, 4000)
  const stage = snapshot?.progress?.stages[snapshot.progress.stages.length - 1]
  // The badge already says "Reading…" — the line says which stage, and for how long.
  const liveLine = snapshot?.progress
    ? `${stage ? progressPhaseLabel(stage.stage, stage.page, stage.pages, stage.label) : 'Starting'} · ${formatElapsed(snapshot.progress.elapsed_s)}`
    : 'Starting'

  return (
    <div className="acct-stmt-row">
      <div className="acct-stmt-main">
        <Stack gap="--space-1">
          <Cluster gap="--space-3">
            <span className="acct-stmt-icon">
              <FileText size={14} strokeWidth={1.5} />
            </span>
            {s.parse_status === 'ready' || isParsing ? (
              <button
                type="button"
                className="acct-stmt-name"
                onClick={() => onReview?.(s.id, s.filename, s.created_at)}
              >
                {s.filename || 'statement'}
              </button>
            ) : (
              <span className="acct-stmt-name-plain">{s.filename || 'statement'}</span>
            )}
            {/* One status badge per row, the same vocabulary as a filed row's "imported"
                (owner, 2026-10-06: "show clearly the status — parsed, parsing, imported"). */}
            {isParsing && <Badge tone="neutral">Reading…</Badge>}
            {s.parse_status === 'ready' && <Badge tone="warn">Ready to review</Badge>}
            {s.parse_status === 'interrupted' && <Badge tone="warn">Reading stopped</Badge>}
            {s.parse_status === 'failed' && (
              <span title={s.parse_error || 'Could not read this file'}>
                <Badge tone="neg">Couldn’t read</Badge>
              </span>
            )}
          </Cluster>
          <div className="imp-metaline">
            {isParsing && <span className="acct-stmt-live">{liveLine}</span>}
            {s.parse_status === 'ready' && (
              <Facts facts={[periodFact(s), rowsFact(s), uploadedFact(s), aiCostFact(s)]} />
            )}
            {s.parse_status === 'interrupted' && (
              <span className="imp-metafact">Reading was interrupted</span>
            )}
            {s.parse_status === 'failed' &&
              (s.parse_error ? (
                <button
                  type="button"
                  className="acct-stmt-err-toggle"
                  onClick={() => setErrorOpen((o) => !o)}
                  aria-expanded={errorOpen}
                >
                  <span className="imp-metafact">Couldn’t read this file</span>
                  <span className="acct-stmt-err-caret">
                    {errorOpen ? (
                      <ChevronDown size="1em" aria-hidden="true" />
                    ) : (
                      <ChevronRight size="1em" aria-hidden="true" />
                    )}
                  </span>
                </button>
              ) : (
                <span className="imp-metafact">Couldn’t read this file</span>
              ))}
            {s.parse_status === 'failed' && errorOpen && s.parse_error && (
              <span className="acct-stmt-err-detail">{s.parse_error}</span>
            )}
            {s.parse_status !== 'ready' && <Facts facts={[uploadedFact(s, isParsing)]} />}
          </div>
        </Stack>
      </div>
      <Cluster gap="--space-2">
        {(s.parse_status === 'ready' || isParsing) && (
          <Button
            className="acct-stmt-action"
            variant="secondary"
            size="sm"
            onClick={() => onReview?.(s.id, s.filename, s.created_at)}
          >
            {isParsing ? 'Open' : 'Review'}
          </Button>
        )}
        {(s.parse_status === 'interrupted' || s.parse_status === 'failed') && (
          <Button
            className="acct-stmt-action"
            variant="secondary"
            size="sm"
            onClick={() => onRerun(s)}
          >
            Re-run
          </Button>
        )}
        {!isParsing && (
          <ActionMenu
            label={`More actions for ${s.filename || 'statement'}`}
            items={[
              {
                label: 'Download',
                icon: <Download size={14} strokeWidth={1.5} aria-hidden />,
                onSelect: () => onDownload(s),
              },
              { label: 'Delete', tone: 'danger', onSelect: () => onDelete(s) },
            ]}
          />
        )}
      </Cluster>
    </div>
  )
}

/** The account's statement history, opened from the account ⋯ menu (owner: a permanent
 *  inline row crowded the main UI). Fetches on open; rows click through to the viewer. */
export function StatementsModal({
  accountId,
  accountName,
  onClose,
  onImport,
  onAskLucius,
  onReviewStatement,
}: {
  accountId: string
  accountName?: string
  onClose: () => void
  onImport?: () => void
  /** Hands a message to Lucius; absent in a context with no chat. */
  onAskLucius?: (text: string) => void
  /** "Review →" on a `ready`-but-uncommitted row — opens the import modal straight at the
   *  review step for this statement's already-cached parse (no re-upload). */
  onReviewStatement?: (statementId: string, filename?: string, uploadedAt?: string) => void
}) {
  const [viewing, setViewing] = useState<StatementRow | null>(null)

  // Read through the query cache: reopening shows the last list at once and refreshes it
  // behind (owner, 2026-10-26: "why loading statement takes so long?"). Any write — Lucius's
  // diff/verify, an upload, a commit — bumps the version and refetches.
  const anyWriteV = useDataVersion('*')
  const listUrl = `/api/accounts/${accountId}/statements`
  const fetchList = useCallback(async () => {
    const r = await authFetch(listUrl)
    if (!r.ok) throw new Error(await r.text())
    const body = await r.json()
    return (body.statements || []) as StatementRow[]
  }, [listUrl])
  const list = useQueryCache(listUrl, fetchList, { version: anyWriteV })
  const rows = list.data ?? null
  const error = list.error != null && rows === null ? 'Could not load statements.' : ''
  const reload = () => void list.refresh().catch(() => {})

  // Lifecycle rows (parsing/failed/interrupted/ready-not-yet-reviewed) sort ABOVE ordinary
  // committed statements — a stable partition, each group keeping the server's own order.
  const sortedRows = rows
    ? [...rows.filter(isLifecycleRow), ...rows.filter((r) => !isLifecycleRow(r))]
    : null

  const download = (s: StatementRow) => {
    const a = document.createElement('a')
    a.href = `/api/statements/${s.id}/file`
    a.download = s.filename || 'statement'
    a.click()
  }
  const removeStatement = async (s: StatementRow) => {
    if (
      !window.confirm(`Delete "${s.filename || 'this statement'}"?

The file and its record are removed. Transactions it imported STAY in your ledger (ask Lucius to roll back the import batch to undo those). Re-uploading the same file later will re-read it with AI.`)
    )
      return
    const r = await authFetch(`/api/statements/${s.id}`, { method: 'DELETE' })
    if (r.ok) reload()
  }
  // "Re-run" (interrupted/failed rows): async so the modal doesn't have to sit and wait —
  // the row itself flips to `parsing` and starts polling as soon as the list reloads.
  const rerunStatement = async (s: StatementRow) => {
    const fd = new FormData()
    fd.append('force_reparse', 'true')
    fd.append('mode', 'async')
    const r = await authFetch(`/api/statements/${s.id}/preview`, { method: 'POST', body: fd })
    if (r.ok) reload()
  }

  return (
    <Modal open onClose={onClose} size="xl" title="Statements" subtitle={accountName}>
      {error && <ErrorState title="Couldn't load statements" message={error} onRetry={reload} />}
      {!error && rows === null && <Loading label="Loading statements…" />}
      {!error && rows !== null && rows.length === 0 && (
        <EmptyState
          icon={<FileText size={28} strokeWidth={1.5} aria-hidden="true" />}
          title="No statements yet"
          body="Import a bank or card statement and it files here — with its period, due date, amount, and the balance check against the statement's own figures."
          action={
            <Cluster gap="--space-3" justify="center">
              {onImport && (
                <Button size="sm" variant="primary" onClick={onImport}>
                  Import a statement
                </Button>
              )}
              <LuciusCta
                label="Add with Lucius"
                message={`Help me add statements for my ${accountName ?? 'account'}. I can send a PDF or screenshot instead of importing it myself.`}
                onAskLucius={onAskLucius}
              />
            </Cluster>
          }
        />
      )}
      {!error && sortedRows !== null && sortedRows.length > 0 && (
        <div className="acct-stmts-list">
          {sortedRows.map((s) =>
            isLifecycleRow(s) ? (
              <LifecycleStatementRow
                key={s.id}
                s={s}
                onReview={onReviewStatement}
                onRerun={rerunStatement}
                onDownload={download}
                onDelete={removeStatement}
              />
            ) : (
              <div key={s.id} className="acct-stmt-row">
                <div className="acct-stmt-main">
                  <Stack gap="--space-1">
                    <Cluster gap="--space-3">
                      <span className="acct-stmt-icon">
                        <FileText size={14} strokeWidth={1.5} />
                      </span>
                      <button
                        type="button"
                        className="acct-stmt-name"
                        title="View the statement"
                        onClick={() => setViewing(s)}
                      >
                        {s.filename || 'statement'}
                      </button>
                      <Badge tone="pos">
                        {s.imported_at ? `Imported ${fDate(s.imported_at)}` : 'Imported'}
                      </Badge>
                      <BalanceBadge s={s} />
                      {(s.account_ids?.length ?? 0) > 1 && (
                        <Badge tone="neutral">{s.account_ids!.length} accounts</Badge>
                      )}
                    </Cluster>
                    <div className="imp-metaline">
                      <Facts facts={[periodFact(s), rowsFact(s), uploadedFact(s), aiCostFact(s)]} />
                    </div>
                  </Stack>
                </div>
                {/* A card statement's amount due — the only right-hand figure; a bank
                    statement owes nothing, so it shows none instead of a dash. */}
                {s.amount_due != null && (
                  <div className="acct-stmt-figure">
                    <Stack gap="--space-1">
                      <Money className="acct-stmt-amt">
                        {fCur(s.amount_due, s.currency || 'SGD', { cents: true })}
                      </Money>
                      {s.payment_due_date && (
                        <span className="acct-stmt-due">Due {fDate(s.payment_due_date)}</span>
                      )}
                    </Stack>
                  </div>
                )}
                <ActionMenu
                  label={`More actions for ${s.filename || 'statement'}`}
                  items={[
                    {
                      label: 'Download',
                      icon: <Download size={14} strokeWidth={1.5} aria-hidden />,
                      onSelect: () => download(s),
                    },
                    {
                      label: 'Delete',
                      tone: 'danger',
                      onSelect: () => removeStatement(s),
                    },
                  ]}
                />
              </div>
            ),
          )}
        </div>
      )}
      {viewing && <StatementViewer stmt={viewing} onClose={() => setViewing(null)} />}
    </Modal>
  )
}
