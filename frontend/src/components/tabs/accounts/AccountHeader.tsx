/**
 * AccountHeader.tsx — Single-account header: name/badges + action bar +
 * Cleared+Uncleared=Working balance card + credit-card due alert +
 * inline ReconcilePopover.
 *
 * Presentational. Port of prototype/accounts/main.jsx AccountHeader +
 * app.jsx ReconcilePopover.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useChatComposerOptional } from '../../../state/chatComposer'
import { StatementViewer, StatementsModal } from './StatementsSection'
import { Button, FormField, InfoPopover, TextField } from '../../ui'
import { Spark } from '../../../lucius/identity'
import {
  Archive,
  ChevronRight,
  CircleCheck,
  ClockAlert,
  Download,
  FileText,
  MoreHorizontal,
  Pencil,
  Tags,
  Trash2,
  Upload,
  User,
} from '../../../ui/icons'
import type { Account } from './types'
import { fCur } from './data'
import { ReviewStrip } from './ReviewStrip'
import { typeLabel } from './categories'
import { fDate } from '../../../utils/format'
import { daysSinceReconcile, isReconcileOverdue } from './reconcileCadence'

type MenuIconName =
  | 'payees'
  | 'categories'
  | 'import'
  | 'edit'
  | 'statements'
  | 'export'
  | 'archive'
  | 'delete'

function MenuIcon({ name }: { name: MenuIconName }) {
  const common = { size: 16, strokeWidth: 1.75, 'aria-hidden': true }
  switch (name) {
    case 'payees':
      return <User {...common} />
    case 'categories':
      return <Tags {...common} />
    case 'import':
      return <Upload {...common} />
    case 'edit':
      return <Pencil {...common} />
    case 'statements':
      return <FileText {...common} />
    case 'export':
      return <Download {...common} />
    case 'archive':
      return <Archive {...common} />
    case 'delete':
      return <Trash2 {...common} />
  }
}

function AccountMenuItem({
  icon,
  children,
  danger = false,
  className,
  onClick,
}: {
  icon: MenuIconName
  children: ReactNode
  danger?: boolean
  className?: string
  onClick: () => void
}) {
  return (
    <button
      className={[className, danger ? 'danger' : ''].filter(Boolean).join(' ')}
      onClick={onClick}
    >
      <span className="acct-menu-icon">
        <MenuIcon name={icon} />
      </span>
      <span>{children}</span>
    </button>
  )
}

function ImportStatementButton({ title, onClick }: { title: string; onClick: () => void }) {
  return (
    <Button
      size="sm"
      variant="primary"
      className="acct-import-btn"
      aria-label="Import statement"
      title={title}
      iconLeft={<Upload size={16} strokeWidth={1.75} aria-hidden="true" />}
      onClick={onClick}
    >
      <span className="acct-import-label">Import statement</span>
    </Button>
  )
}

// ── Due info (credit cards) ───────────────────────────────────────────────────

export interface DueInfo {
  /** Remaining unpaid amount from the last CLOSED statement — always >= 0 (never the raw,
   *  possibly-negative live account balance, and never includes new post-payment spending). */
  amountDue: number
  currency: string
  days: number
  closeDays: number | null
  /** True when a payment was received but it didn't cover the full statement balance. */
  partial: boolean
  /** "statement" when the due date/amount are the PRINTED figures off an imported statement
   *  (overriding the day-of-month estimate); "config" when estimated. */
  source?: 'statement' | 'config'
  /** Printed minimum payment, when the statement carries one. */
  minPayment?: number | null
  /** The statement record behind the printed figures — links to the original file. */
  statementId?: string | null
  /** Total payments received since the statement closed (partial-payment display). */
  paidAmount?: number | null
  /** The full statement balance (amountDue is the REMAINING part after payments). */
  totalDue?: number | null
  /** ISO due date — feeds the "Remind me" prefill. */
  dueDate?: string | null
  /** Fully paid — the banner turns into a congratulation instead of a warning. */
  settled?: boolean
  /** Printed statement period — labels the statement chip ("statement 06-03 → 07-01"). */
  periodStart?: string | null
  periodEnd?: string | null
  /** A newer cycle closed but its statement isn't imported yet — the figures are that
   *  cycle's ledger ESTIMATE; the banner nudges to import it (never a congrats). */
  missingForCycle?: boolean
  /** ISO close date of the cycle whose statement is missing. */
  missingCloseDate?: string | null
}

// ── Account stats footer ──────────────────────────────────────────────────────

export interface AccountStats {
  count: number
  review: number
}

// ── Reconcile Popover ─────────────────────────────────────────────────────────

interface ReconcilePopoverProps {
  account: Account
  onReconcile: (account: Account, realBalance: number) => void
}

/** Lives on the "Reconciled …" line under the balance — the action beside the status it
 *  changes, at every width (owner, 2026-10-26), instead of a header button + ⋯ menu entry. */
function ReconcilePopover({ account, onReconcile }: ReconcilePopoverProps) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'ask' | 'input'>('ask')
  const [val, setVal] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  })

  function close() {
    setOpen(false)
    setMode('ask')
    setVal('')
  }

  const real = parseFloat(val)
  const has = !Number.isNaN(real)
  // Reconcile diffs against the CLEARED balance (the backend default) — uncleared charges are
  // not yet on the statement, so they are neither asked about nor adjusted away.
  const diff = has ? Math.round((real - account.clearedBalance) * 100) / 100 : 0

  return (
    <div className="acct-pop-wrap acct-pop-wrap--line" ref={ref}>
      <button
        type="button"
        className="acct-recon-action as-btn"
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        Reconcile
      </button>

      {open && (
        <div className="acct-pop" role="dialog">
          {mode === 'ask' ? (
            <>
              <div className="acct-pop-q">
                Is the real balance{' '}
                <b className={account.clearedBalance < 0 ? 'neg' : ''}>
                  {fCur(account.clearedBalance, account.currency, { cents: true })}{' '}
                  {account.currency}
                </b>
                ?
              </div>
              <div className="acct-pop-actions">
                <Button size="sm" variant="ghost" onClick={() => setMode('input')}>
                  No
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    onReconcile(account, account.clearedBalance)
                    close()
                  }}
                >
                  Yes, it matches
                </Button>
              </div>
            </>
          ) : (
            <>
              <FormField label={`Real balance (${account.currency})`}>
                <TextField
                  autoFocus
                  value={val}
                  placeholder="actual"
                  onChange={(e) => setVal(e.target.value)}
                />
              </FormField>
              {has && (
                <div className="acct-pop-diff">
                  <span>Adjustment</span>
                  <b className={diff < 0 ? 'neg' : diff > 0 ? 'pos' : ''}>
                    {diff === 0
                      ? 'In sync'
                      : (diff > 0 ? '+' : '') + fCur(diff, account.currency, { cents: true })}
                  </b>
                </div>
              )}
              <div className="acct-pop-actions">
                <Button size="sm" variant="ghost" onClick={() => setMode('ask')}>
                  Back
                </Button>
                <Button
                  size="sm"
                  disabled={!has}
                  onClick={() => {
                    onReconcile(account, real)
                    close()
                  }}
                >
                  {diff === 0 ? 'Confirm' : 'Add adjustment'}
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Prop types ────────────────────────────────────────────────────────────────

export interface AccountHeaderProps {
  account: Account
  dueInfo: DueInfo | null
  stats: AccountStats | null
  menuOpen: boolean
  setMenuOpen: (open: boolean | ((prev: boolean) => boolean)) => void
  collapsed: boolean
  onReopen: () => void
  /** Hover on the reopen button floats the accounts rail (AccountsTab's peek). */
  onReopenHover?: () => void
  onReopenLeave?: () => void
  onAddTxn: () => void
  onEdit: () => void
  onArchive: () => void
  onDelete: () => void
  onManagePayees: () => void
  onManageCategories: () => void
  onImport: () => void
  /** Hands a message to Lucius in a fresh conversation; absent in a context with no chat. */
  onAskLucius?: (text: string) => void
  /** "Review →" on a ready-but-uncommitted statement (B20 async) — opens the import modal
   *  straight at the review step, no re-upload. */
  onReviewStatement?: (statementId: string, filename?: string, uploadedAt?: string) => void
  onExport: () => void
  /** Called with (account, realBalance) — orchestrator wires to hook's reconcile */
  onReconcile: (account: Account, realBalance: number) => void
  onReview: () => void
  onStartReview: () => void
}

// ── AccountHeader ─────────────────────────────────────────────────────────────

export function AccountHeader({
  account,
  dueInfo,
  stats,
  menuOpen,
  setMenuOpen,
  collapsed,
  onReopen,
  onReopenHover,
  onReopenLeave,
  onEdit,
  onArchive,
  onDelete,
  onManagePayees,
  onManageCategories,
  onImport,
  onAskLucius,
  onReviewStatement,
  onExport,
  onReconcile,
  onReview,
  onStartReview,
}: AccountHeaderProps) {
  const chatComposer = useChatComposerOptional()
  const [viewStatement, setViewStatement] = useState(false)
  const [showStatements, setShowStatements] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const h = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey)
    }
  })

  const pending = account.balance - account.clearedBalance

  return (
    <>
      {/* ── header row ─────────────────────────────────────────────────────── */}
      <div className="acct-head">
        <div className="acct-head-l">
          {collapsed && (
            <button
              className="acct-reopen"
              onClick={onReopen}
              onMouseEnter={onReopenHover}
              onMouseLeave={onReopenLeave}
              title="Show accounts"
            >
              <ChevronRight size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          )}
          <div>
            <div className="acct-head-title" data-ctx-title={account.name}>
              {account.name}
            </div>
            <div className="acct-head-sub">
              <span>{typeLabel(account.type)}</span>
              <span className="sep">•</span>
              <span>{account.currency}</span>
              {account.institution && (
                <>
                  <span className="sep">•</span>
                  <span>{account.institution}</span>
                </>
              )}
              {account.lastFour && (
                <>
                  <span className="sep">•</span>
                  <span>•••{account.lastFour}</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="acct-head-actions">
          <Button
            className="acct-head-collapsible"
            size="sm"
            variant="ghost"
            onClick={onManagePayees}
          >
            Manage payees
          </Button>
          <Button
            className="acct-head-collapsible"
            size="sm"
            variant="ghost"
            onClick={onManageCategories}
          >
            Manage categories
          </Button>
          <Button className="acct-head-collapsible" size="sm" variant="ghost" onClick={onImport}>
            Import
          </Button>
          <div className="acct-menu-wrap" ref={menuRef}>
            <button
              className="btn-ghost-icon"
              onClick={() => setMenuOpen((o) => !o)}
              aria-label="More actions"
            >
              <MoreHorizontal size={18} strokeWidth={1.75} aria-hidden="true" />
            </button>
            {menuOpen && (
              <div className="acct-menu">
                {/* Folded in from the header row on narrow widths (see accounts.css) */}
                <AccountMenuItem
                  icon="payees"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onManagePayees()
                  }}
                >
                  Manage payees
                </AccountMenuItem>
                <AccountMenuItem
                  icon="categories"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onManageCategories()
                  }}
                >
                  Manage categories
                </AccountMenuItem>
                <AccountMenuItem
                  icon="import"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onImport()
                  }}
                >
                  Import
                </AccountMenuItem>
                <div className="acct-menu-collapsible sep" />
                <AccountMenuItem icon="edit" onClick={onEdit}>
                  Edit account
                </AccountMenuItem>
                <AccountMenuItem
                  icon="statements"
                  onClick={() => {
                    setMenuOpen(false)
                    setShowStatements(true)
                  }}
                >
                  Statements
                </AccountMenuItem>
                <AccountMenuItem icon="export" onClick={onExport}>
                  Export transactions
                </AccountMenuItem>
                <div className="sep" />
                <AccountMenuItem icon="archive" danger onClick={onArchive}>
                  Archive account
                </AccountMenuItem>
                <AccountMenuItem icon="delete" danger onClick={onDelete}>
                  Delete account
                </AccountMenuItem>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── credit-card due alert ───────────────────────────────────────────── */}
      {/* Honesty tiers: fully PAID congratulates; an ESTIMATE says so (~ + tag) with an
          Import-statement action; statement-backed shows remaining / paid-so-far / minimum.
          Actions (Import statement · Remind me) sit together on the right. */}
      {dueInfo && dueInfo.settled && (
        <div className="acct-alert paid">
          <span className="mark">✓</span>
          <span className="grow">
            {dueInfo.source === 'statement' ? (
              <>
                Congrats — this statement is <b>paid in full</b>
                {dueInfo.totalDue != null && <> ({fCur(dueInfo.totalDue, dueInfo.currency)})</>}.
                {dueInfo.statementId && (
                  <button
                    type="button"
                    className="acct-alert-src"
                    title="View the statement"
                    onClick={() => setViewStatement(true)}
                  >
                    {dueInfo.periodStart
                      ? `Statement: ${fDate(dueInfo.periodStart, 'numeric')} → ${dueInfo.periodEnd ? fDate(dueInfo.periodEnd, 'numeric') : '…'}`
                      : 'View statement'}
                  </button>
                )}
              </>
            ) : (
              // Estimate tier: lead with the REAL fact (the payment received); the folded
              // total is only what it covers — never claim an estimated figure was "paid
              // in full" (the actual payment usually differs).
              <>
                Congrats — <b>{fCur(dueInfo.paidAmount ?? 0, dueInfo.currency)}</b> paid this cycle,
                covering your estimated balance
                {dueInfo.totalDue != null && <> of ~{fCur(dueInfo.totalDue, dueInfo.currency)}</>}.
              </>
            )}
          </span>
          {dueInfo.source !== 'statement' && (
            <span className="acct-alert-actions">
              <ImportStatementButton
                title="Import the statement to confirm the exact figures"
                onClick={onImport}
              />
            </span>
          )}
        </div>
      )}
      {dueInfo && !dueInfo.settled && (
        <div className="acct-alert">
          <span className="mark">●</span>
          <span className="grow">
            {dueInfo.missingForCycle && dueInfo.amountDue <= 0 ? (
              // A newer cycle closed without its statement and the ledger estimate folds
              // to zero — never celebrate an unconfirmed estimate, just ask for the file.
              <>
                Your
                {dueInfo.missingCloseDate ? (
                  <>
                    {' '}
                    <b>{fDate(dueInfo.missingCloseDate, 'numeric')}</b>
                  </>
                ) : (
                  ' latest'
                )}{' '}
                statement isn't imported yet — import it to confirm the exact figures.
              </>
            ) : (
              <>
                {dueInfo.source !== 'statement' && <b className="acct-alert-est">Estimated</b>}
                {dueInfo.partial ? (
                  <>
                    {' '}
                    Paid <b>{fCur(dueInfo.paidAmount ?? 0, dueInfo.currency)}</b>
                    {dueInfo.totalDue != null && (
                      <> of {fCur(dueInfo.totalDue, dueInfo.currency)}</>
                    )}{' '}
                    — <b>{fCur(dueInfo.amountDue, dueInfo.currency)}</b> still{' '}
                  </>
                ) : (
                  <>
                    {' '}
                    Payment of{' '}
                    <b>
                      {dueInfo.source !== 'statement' ? '~' : ''}
                      {fCur(dueInfo.amountDue, dueInfo.currency)}
                    </b>{' '}
                  </>
                )}
                {dueInfo.days < 0 ? (
                  <>
                    overdue by <b>{Math.abs(dueInfo.days)} days</b>
                  </>
                ) : (
                  <>
                    due in <b>{dueInfo.days} days</b>
                  </>
                )}
                {/* the next cycle's close is noise when THIS banner is about a closed-but-unimported cycle */}
                {dueInfo.closeDays != null &&
                  dueInfo.source !== 'statement' &&
                  !dueInfo.missingForCycle && (
                    <>
                      {' '}
                      • statement closes in <b>{dueInfo.closeDays} days</b>
                    </>
                  )}
                {dueInfo.minPayment != null &&
                  dueInfo.minPayment > 0 &&
                  ((dueInfo.paidAmount ?? 0) < dueInfo.minPayment ? (
                    <>
                      {' '}
                      • minimum <b>{fCur(dueInfo.minPayment, dueInfo.currency)}</b>
                      {(dueInfo.paidAmount ?? 0) > 0 && ' not yet met'}
                    </>
                  ) : (
                    <> • minimum met</>
                  ))}
                .
                {dueInfo.missingForCycle && (
                  <>
                    {' '}
                    Your
                    {dueInfo.missingCloseDate ? (
                      <>
                        {' '}
                        <b>{fDate(dueInfo.missingCloseDate, 'numeric')}</b>
                      </>
                    ) : (
                      ' latest'
                    )}{' '}
                    statement isn't imported yet — import it to confirm.
                  </>
                )}
                {dueInfo.source === 'statement' && dueInfo.statementId && (
                  <button
                    type="button"
                    className="acct-alert-src"
                    title="Printed on your imported statement — view it"
                    onClick={() => setViewStatement(true)}
                  >
                    {dueInfo.periodStart
                      ? `Statement: ${fDate(dueInfo.periodStart, 'numeric')} → ${dueInfo.periodEnd ? fDate(dueInfo.periodEnd, 'numeric') : '…'}`
                      : 'View statement'}
                  </button>
                )}
              </>
            )}
          </span>
          <span className="acct-alert-actions">
            {dueInfo.source !== 'statement' && (
              <ImportStatementButton
                title="These figures are estimated from your ledger and cycle settings — the statement carries the exact due date, amount and minimum"
                onClick={onImport}
              />
            )}
            {dueInfo.amountDue > 0 && (
              <Button
                size="sm"
                variant="primary"
                className="fi-lucius"
                iconLeft={<Spark size={15} ariaHidden />}
                title="Ask Lucius to set a payment reminder"
                onClick={() => {
                  const due = dueInfo.dueDate
                    ? ` by ${dueInfo.dueDate}`
                    : dueInfo.days >= 0
                      ? ` in ${dueInfo.days} days`
                      : ' (overdue)'
                  const min =
                    dueInfo.minPayment && (dueInfo.paidAmount ?? 0) < dueInfo.minPayment
                      ? `, minimum ${fCur(dueInfo.minPayment, dueInfo.currency)}`
                      : ''
                  const estimate =
                    dueInfo.source !== 'statement'
                      ? ' (estimated — exact figures are on the statement)'
                      : ''
                  chatComposer?.setPendingText(
                    `Create a reminder to pay my ${account.name} bill — ${fCur(dueInfo.amountDue, dueInfo.currency)} due${due}${min}${estimate}. Remind me a few days before it's due.`,
                  )
                  window.dispatchEvent(new Event('lx-dock-open'))
                }}
              >
                Remind me
              </Button>
            )}
          </span>
        </div>
      )}

      {viewStatement && dueInfo?.statementId && (
        <StatementViewer
          stmt={{ id: dueInfo.statementId }}
          onClose={() => setViewStatement(false)}
        />
      )}
      {showStatements && (
        <StatementsModal
          accountId={account.id}
          accountName={account.name}
          onClose={() => setShowStatements(false)}
          onImport={() => {
            setShowStatements(false)
            onImport()
          }}
          onAskLucius={
            onAskLucius &&
            ((text) => {
              setShowStatements(false)
              onAskLucius(text)
            })
          }
          onReviewStatement={
            onReviewStatement &&
            ((statementId, filename, uploadedAt) => {
              setShowStatements(false)
              onReviewStatement(statementId, filename, uploadedAt)
            })
          }
        />
      )}

      {/* ── balance card ────────────────────────────────────────────────────── */}
      <div className="acct-bal">
        <div className="acct-bal-card">
          <div className="acct-bal-k">
            {account.kind === 'liability' ? 'Balance owed' : 'Balance'}
          </div>
          <div className="acct-bal-breakdown">
            <div className="acct-bal-part">
              <div className={'acct-bal-fig mask' + (account.clearedBalance > 0 ? ' pos' : '')}>
                {fCur(account.clearedBalance, account.currency, { cents: true })}
              </div>
              <div className="acct-bal-lbl">
                Cleared <InfoPopover text="Posted — finalized; the amount won't change." />
              </div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">+</span>
              <div className="acct-bal-part">
                <div className={'acct-bal-fig mask' + (pending > 0 ? ' pos' : '')}>
                  {fCur(pending, account.currency, { cents: true })}
                </div>
                <div className="acct-bal-lbl">
                  Uncleared{' '}
                  <InfoPopover text="Pending — a temporary hold; the amount can still change." />
                </div>
              </div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">=</span>
              <div className="acct-bal-part">
                <div className={'acct-bal-fig work mask' + (account.balance > 0 ? ' pos' : '')}>
                  {fCur(account.balance, account.currency, { cents: true })}
                </div>
                <div className="acct-bal-lbl">
                  Working balance <InfoPopover text="Cleared + uncleared, for this account." />
                </div>
              </div>
            </div>
          </div>

          {/* reconcile line + stats */}
          <div className="acct-recon-line">
            <span className="acct-recon-group">
              <span className="acct-recon-stat">
                {account.lastReconciled && isReconcileOverdue(account.lastReconciled) ? (
                  <span
                    className="acct-recon-stat"
                    title="Reconcile at least once a month, so a gap is still easy to trace."
                  >
                    <ClockAlert size={14} className="warn" aria-hidden="true" />
                    Reconciled {fDate(account.lastReconciled, 'numeric', 'auto')}{' '}
                    <span className="warn">
                      ({daysSinceReconcile(account.lastReconciled)} days ago)
                    </span>
                  </span>
                ) : account.lastReconciled ? (
                  <>
                    <CircleCheck size={14} className="ok" aria-hidden="true" />
                    Reconciled {fDate(account.lastReconciled, 'numeric', 'auto')}
                  </>
                ) : (
                  <>
                    <ClockAlert size={14} className="warn" aria-hidden="true" />
                    Never reconciled
                  </>
                )}
              </span>
              <ReconcilePopover account={account} onReconcile={onReconcile} />
            </span>
            {/* Gated on `stats` existing, never on count, so the line doesn't disappear at 0 txns. */}
            {stats && (
              <>
                <span className="acct-recon-grow" />
                <span className="acct-recon-group acct-recon-group--review">
                  <ReviewStrip
                    count={stats.review}
                    onReview={onReview}
                    onStartReview={onStartReview}
                  />
                  <span className="acct-recon-stat muted">{stats.count} txns</span>
                </span>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
