import { useState, type ReactNode } from 'react'
import { Ic, Spark, I_CHECK, I_WARN } from './importIcons'

export interface BookedResult {
  created: number
  merged?: number
  duplicates: number
  skipped: number
  accounts: number
  transfers?: number
  batchId?: string | null
  deleted?: number
  /** Rows the user (or a default) left out in the review — the "leave out N" it showed. */
  leftOut?: number
  /** Rows the reader skipped from the file; never a decision of the user's. */
  unreadable?: number
}

export interface UndoOutcome {
  deleted: number
  unmerged: number
  /** Ledger entries the import had deleted, brought back. */
  restored?: number
}

export interface BookedScreenProps {
  result: BookedResult
  filename: string
  /** "Account · period · N rows" under the title — already joined by the caller. */
  context: string
  deleteErrors: Array<{ id: string; error: string }>
  refusedDuplicates: Array<{ date?: string | null; description?: string | null }>
  doneDiff: { core: number; boundary: number } | null
  taught: boolean
  reviewMarkState: 'idle' | 'pending' | 'done'
  reviewMarkError: string
  onMarkAllReviewed?: () => void
  onViewInRegister?: () => void
  onUndo?: (batchId: string) => Promise<UndoOutcome>
  onReviewAgain: () => void
  onImportAnother: () => void
  onDone: () => void
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

type UndoState = 'idle' | 'confirm' | 'pending' | 'error'

export function BookedScreen(p: BookedScreenProps) {
  const [undo, setUndo] = useState<UndoState>('idle')
  const [undoError, setUndoError] = useState('')
  const [undone, setUndone] = useState<UndoOutcome | null>(null)
  const r = p.result
  const linked = r.merged ?? 0
  const transfers = r.transfers ?? 0
  const batchId = r.batchId

  async function runUndo() {
    if (!batchId || !p.onUndo) return
    setUndo('pending')
    setUndoError('')
    try {
      setUndone(await p.onUndo(batchId))
    } catch (e) {
      setUndo('error')
      setUndoError(e instanceof Error && e.message ? e.message : 'Could not undo — try again.')
    }
  }

  if (undone) return <UndoneScreen {...p} undone={undone} />

  return (
    <div className="imp3-booked">
      <div className="imp3-booked-head">
        <span className="imp3-booked-ic">
          <Ic d={I_CHECK} size={16} sw={2.6} />
        </span>
        <div className="imp3-booked-titles">
          <div className="imp3-booked-t">Imported {p.filename}</div>
          {p.context && <div className="imp3-booked-sub">{p.context}</div>}
        </div>
      </div>

      <div className="imp3-booked-stats">
        <Stat n={r.created} label="added" sub={transfers > 0 ? `${transfers} as transfers` : ''} />
        <Stat n={linked} label="linked" sub="to entries you had" />
        <Stat n={r.leftOut ?? r.skipped} label="left out" sub="by you" />
      </div>

      <div className="imp3-booked-lines">
        {p.doneDiff !== null && p.doneDiff.core === 0 && (
          <div className="imp3-booked-line good">
            <span aria-hidden="true">✓</span>
            <span>
              Statement and ledger agree for this period
              {p.doneDiff.boundary > 0 && (
                <span className="imp3-booked-aside">
                  {' '}
                  · {plural(p.doneDiff.boundary, 'entry', 'entries')} near the cut-off belong
                  {p.doneDiff.boundary === 1 ? 's' : ''} to the statement before or after
                </span>
              )}
            </span>
          </div>
        )}
        {p.doneDiff !== null && p.doneDiff.core > 0 && (
          <WarnLine>
            {plural(p.doneDiff.core, 'entry', 'entries')} in your ledger for this period{' '}
            {p.doneDiff.core === 1 ? 'is' : 'are'} not on this statement — check them under
            Statements.
          </WarnLine>
        )}
        {(r.unreadable ?? 0) > 0 && (
          <div className="imp3-booked-line">
            <span aria-hidden="true">·</span>
            <span>
              {plural(r.unreadable ?? 0, 'row')} in the file {r.unreadable === 1 ? 'was' : 'were'}{' '}
              skipped when it was read
            </span>
          </div>
        )}
        {r.duplicates > 0 && (
          <div className="imp3-booked-line">
            <span aria-hidden="true">·</span>
            <span>{plural(r.duplicates, 'row')} already imported earlier — skipped</span>
          </div>
        )}
        {(r.deleted ?? 0) > 0 && (
          <div className="imp3-booked-line">
            <span aria-hidden="true">·</span>
            <span>{plural(r.deleted ?? 0, 'row')} deleted from your ledger, as you marked</span>
          </div>
        )}
        {p.refusedDuplicates.length > 0 && (
          <WarnLine>
            {plural(p.refusedDuplicates.length, 'row')} not imported — already in your ledger:{' '}
            {p.refusedDuplicates
              .map((d) => `${d.date ?? ''} ${d.description ?? ''}`.trim() || '(unnamed row)')
              .join('; ')}
          </WarnLine>
        )}
        {p.deleteErrors.length > 0 && (
          <WarnLine>
            {plural(p.deleteErrors.length, 'marked transaction')} could not be deleted —{' '}
            {p.deleteErrors.map((e) => e.error).join('; ')}
          </WarnLine>
        )}
        {r.created > 0 && batchId && p.onMarkAllReviewed && (
          <div className="imp3-booked-line review">
            {p.reviewMarkState === 'done' ? (
              <span className="imp3-booked-good">
                ✓ All {plural(r.created, 'new row')} marked reviewed
              </span>
            ) : (
              <>
                <span>
                  {plural(r.created, 'new row')} {r.created === 1 ? 'is' : 'are'} marked to review
                  in the register
                </span>
                <button
                  type="button"
                  className="imp3-done-review-btn"
                  disabled={p.reviewMarkState === 'pending'}
                  onClick={p.onMarkAllReviewed}
                >
                  {p.reviewMarkState === 'pending' ? 'Marking…' : 'Mark all reviewed'}
                </button>
              </>
            )}
          </div>
        )}
        {p.taught && (
          <div className="imp3-booked-line lucius">
            <Spark size={14} />
            <span>Lucius saved your instruction — the next import applies it automatically.</span>
          </div>
        )}
        {p.reviewMarkError && <div className="imp3-done-err">{p.reviewMarkError}</div>}
      </div>

      {(undo === 'confirm' || undo === 'pending' || undo === 'error') && (
        <div className="imp3-undo" role="alertdialog" aria-label="Undo this import?">
          <div className="imp3-undo-t">Undo this import?</div>
          <div className="imp3-undo-d">
            The {plural(r.created, 'added row')} {r.created === 1 ? 'is' : 'are'} removed
            {linked > 0 &&
              ` and the ${plural(linked, 'linked entry', 'linked entries')} go${linked === 1 ? 'es' : ''} back to how ${linked === 1 ? 'it was' : 'they were'}`}
            . The statement returns to Ready to review.
            {(r.deleted ?? 0) > 0 &&
              ` The ${plural(r.deleted ?? 0, 'entry', 'entries')} you deleted ${r.deleted === 1 ? 'comes' : 'come'} back.`}
          </div>
          {undo === 'error' && <div className="imp3-done-err">{undoError}</div>}
          <div className="imp3-undo-acts">
            <button
              type="button"
              className="imp3-btn2"
              disabled={undo === 'pending'}
              onClick={() => setUndo('idle')}
            >
              Keep it
            </button>
            <button
              type="button"
              className="imp3-undo-go"
              disabled={undo === 'pending'}
              onClick={runUndo}
            >
              {undo === 'pending' ? 'Undoing…' : 'Undo import'}
            </button>
          </div>
        </div>
      )}

      <div className="imp3-booked-foot">
        <div className="imp3-booked-foot-l">
          {batchId && p.onUndo && undo === 'idle' && (
            <button type="button" className="imp3-undo-btn" onClick={() => setUndo('confirm')}>
              Undo import
            </button>
          )}
          <button type="button" className="imp3-done-another" onClick={p.onImportAnother}>
            Import another
          </button>
        </div>
        <div className="imp3-booked-foot-r">
          {batchId && p.onViewInRegister && (
            <button type="button" className="imp3-btn2" onClick={p.onViewInRegister}>
              View in register
            </button>
          )}
          <button type="button" className="imp3-lubtn" onClick={p.onDone}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

function Stat({ n, label, sub }: { n: number; label: string; sub: string }) {
  return (
    <div className="imp3-booked-stat">
      <div className="imp3-booked-n">{n}</div>
      <div className="imp3-booked-l">{label}</div>
      <div className="imp3-booked-s">{sub || ' '}</div>
    </div>
  )
}

function WarnLine({ children }: { children: ReactNode }) {
  return (
    <div className="imp3-booked-line warn">
      <Ic d={I_WARN} size={14} />
      <span>{children}</span>
    </div>
  )
}

function UndoneScreen(p: BookedScreenProps & { undone: UndoOutcome }) {
  const { deleted, unmerged, restored = 0 } = p.undone
  const parts = [`${plural(deleted, 'row')} removed`]
  if (unmerged > 0) parts.push(`${plural(unmerged, 'entry', 'entries')} restored`)
  if (restored > 0) parts.push(`${plural(restored, 'deleted entry', 'deleted entries')} back`)
  return (
    <div className="imp3-booked">
      <div className="imp3-booked-head">
        <span className="imp3-booked-ic undone">
          <Ic d={I_CHECK} size={16} sw={2.6} />
        </span>
        <div className="imp3-booked-titles">
          <div className="imp3-booked-t">Import undone</div>
          <div className="imp3-booked-sub">
            {parts.join(' · ')}. {p.filename} is ready to review again.
          </div>
        </div>
      </div>
      <div className="imp3-booked-foot">
        <div className="imp3-booked-foot-l" />
        <div className="imp3-booked-foot-r">
          <button type="button" className="imp3-btn2" onClick={p.onDone}>
            Done
          </button>
          <button type="button" className="imp3-lubtn" onClick={p.onReviewAgain}>
            Review again
          </button>
        </div>
      </div>
    </div>
  )
}
