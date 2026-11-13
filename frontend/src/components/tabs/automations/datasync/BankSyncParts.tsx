import { useState } from 'react'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fDate, fScheduleWhen, timeAgo } from '../../../../utils/format'
import { Button, Checkbox, Modal } from '../../../ui'
import { Clock3 } from 'lucide-react'
import { ConnectorAccountsList } from '../../connectors/ConnectorAccountsList'
import { ConnectorPanel } from '../../connectors/ConnectorPanel'
import { useConnectors } from '../../connectors/useConnectors'
import { connectorLogo } from '../../connectors/logos'
import { SettingMenu } from './SyncBlocks'
import { bankSyncSend, type BankSyncAccount, type BankSyncStatus } from './bankSync'
import { everyLabel, type Act } from './bankSyncHelpers'

// Pieces the bank sync's setup steps and its status panel share (ADR 2026-11-13).

export function ActionError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div className="a3-action-error" role="alert">
      {error}
    </div>
  )
}

/** Each linked account with its Sync box, under the bank it's at: one connector can hold
 *  several banks (Enable Banking, GoCardless), and the account's own name doesn't say which. */
export function AccountBoxes({
  status,
  busy,
  act,
}: {
  status: BankSyncStatus
  busy: string | null
  act: Act
}) {
  const p = status.provider
  const banks = new Map<string, BankSyncAccount[]>()
  for (const a of status.accounts ?? []) {
    const bank = a.bank || 'Other'
    banks.set(bank, [...(banks.get(bank) ?? []), a])
  }
  return (
    <div className="bs-banks">
      {[...banks].map(([bank, accounts]) => (
        <div
          key={bank}
          className="bs-accounts"
          role="group"
          aria-label={`${bank} accounts to sync`}
        >
          <div className="bs-bank-h">{bank}</div>
          <BankStatusLine status={status} accounts={accounts} />
          {accounts.map((a) => (
            <Checkbox
              key={a.account_id}
              id={`bs-${p}-${a.account_id}`}
              label={a.name}
              description={readsLine(a, status.daily_reads)}
              checked={a.sync}
              disabled={busy !== null}
              onChange={() =>
                void act('account', () =>
                  bankSyncSend(`/${p}/accounts/${a.account_id}`, 'PUT', { sync: !a.sync }),
                )
              }
            />
          ))}
        </div>
      ))}
    </div>
  )
}

function readsLine(a: BankSyncAccount, daily: number | null): string {
  // The label is the account in your book; this line says which bank account feeds it.
  const source = [a.bank_name, a.mask ? `··${a.mask}` : ''].filter(Boolean).join(' ')
  return [source && `From ${source}`, syncLine(a, daily)].filter(Boolean).join(' · ')
}

function syncLine(a: BankSyncAccount, daily: number | null): string {
  if (!a.sync) return 'Not synced'
  if (daily === null || a.reads_left === null) return ''
  // The count is noise until it runs out; then it says why Sync now is waiting.
  if (a.reads_left > 0) return ''
  return a.next_read_at
    ? `No reads left · next ${fScheduleWhen(a.next_read_at)}`
    : 'No reads left today'
}

/** Link the bank's accounts without leaving the sync's page: the connector page's own account
 *  list (Add as a new account, or link one you have) in a dialog. Closing it re-reads the sync,
 *  so a newly linked account appears with its Sync box ticked. */
export function LinkAccountsButton({
  status,
  primary = false,
}: {
  status: BankSyncStatus
  primary?: boolean
}) {
  const [open, setOpen] = useState(false)
  const close = () => {
    setOpen(false)
    invalidateQueryCache((key) => key.startsWith('/api/bank-sync'))
  }
  return (
    <>
      {primary ? (
        <div>
          <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
            Link accounts
          </Button>
        </div>
      ) : (
        <button type="button" className="a3-linkbtn" onClick={() => setOpen(true)}>
          Link more accounts
        </button>
      )}
      {open && (
        <Modal
          open
          size="lg"
          title={`Link ${status.label} accounts`}
          subtitle="Add as a new account, or link to one you have."
          onClose={close}
          footer={
            <Button size="sm" variant="primary" onClick={close}>
              Done
            </Button>
          }
        >
          <ConnectorAccountsList provider={status.provider} providerLabel={status.label} embedded />
        </Modal>
      )}
    </>
  )
}

/** How often the sync runs, as the budget apps' Schedule reads: the value, opening the choices
 *  this bank allows. */
export function ScheduleMenu({
  status,
  busy,
  act,
}: {
  status: BankSyncStatus
  busy: string | null
  act: Act
}) {
  return (
    <SettingMenu
      label="How often to sync"
      icon={<Clock3 size={14} aria-hidden="true" />}
      value={String(status.interval_hours)}
      options={(status.intervals ?? [status.interval_hours]).map((h) => ({
        value: String(h),
        label: everyLabel(h),
      }))}
      disabled={busy !== null}
      onPick={(h) =>
        void act('schedule', () =>
          bankSyncSend(`/${status.provider}/schedule`, 'PUT', { interval_hours: Number(h) }),
        )
      }
    />
  )
}

/** The provider's mark on the white plate every brand logo gets (the budget apps' `a3-mark`),
 *  or nothing when it has none or the file fails. Decorative: its name is always beside it.
 *  `size`: `lg` for the page's title, `xs` inline beside a card's name. */
const MARK_CLASS = { lg: 'a3-mark bs-mark-lg', md: 'a3-mark', xs: 'a3-mark bs-mark-xs' }

export function BankLogo({ provider, size }: { provider: string; size?: 'lg' | 'xs' }) {
  const src = connectorLogo(provider)
  const [broken, setBroken] = useState(false)
  if (!src || broken) return null
  return (
    <span className={MARK_CLASS[size ?? 'md']} aria-hidden="true">
      <img className="a3-mark-logo" src={src} alt="" onError={() => setBroken(true)} />
    </span>
  )
}

/** The page's title: the provider's mark, its name, and what the page does. */
export function BankSyncTitle({ provider, label }: { provider: string; label: string }) {
  return (
    <div className="a3-detail-title bs-title">
      <BankLogo provider={provider} size="lg" />
      <div>
        <h1>{label}</h1>
        <div className="a3-detail-sub">Syncs your bank transactions automatically.</div>
      </div>
    </div>
  )
}

/** The connector's own setup panel, opened in place as the budget apps' page does ("Connect
 *  YNAB…"): connecting a bank, renewing its access, reconnecting it all live there. Closing it
 *  re-reads the sync. `link`: a text button inside a line, not a primary button. */
export function ConnectorSetupButton({
  status,
  label,
  link = false,
}: {
  status: BankSyncStatus
  label: string
  link?: boolean
}) {
  useConnectors() // warm the connector list, so the panel opens at once
  const [open, setOpen] = useState(false)
  return (
    <>
      {link ? (
        <button type="button" className="a3-linkbtn" onClick={() => setOpen(true)}>
          {label}
        </button>
      ) : (
        <div>
          <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
            {label}
          </Button>
        </div>
      )}
      {open && (
        <ConnectorPanel
          connectorId={status.provider}
          configureOnOpen
          onClose={() => {
            setOpen(false)
            invalidateQueryCache((key) => key.startsWith('/api/bank-sync'))
          }}
        />
      )}
    </>
  )
}

/** One line under a bank's name: when it last synced and until when the bank lets this app
 *  read it — or, when something needs you, that thing in plain words with the one button that
 *  fixes it. */
function BankStatusLine({
  status,
  accounts,
}: {
  status: BankSyncStatus
  accounts: BankSyncAccount[]
}) {
  const ended = accounts.some((a) => a.access_ended)
  const ends = accounts
    .map((a) => a.access_ends_at)
    .filter((d): d is string => !!d)
    .sort()[0]
  const soon = accounts.some((a) => a.access_renew_soon)
  const signIn = status.last_status === 'auth_error' || status.last_status === 'reconnect_needed'
  const problem = ended
    ? ['Access ended. Nothing syncs until you renew it.', 'Renew']
    : signIn
      ? ['The bank needs you to sign in again.', 'Reconnect']
      : soon
        ? [`Access ends ${fDate(ends!)}.`, 'Renew']
        : null
  if (problem)
    return (
      <p className="bs-bank-line warn">
        {problem[0]} <ConnectorSetupButton status={status} label={problem[1]!} link />
      </p>
    )
  const run = status.last_run
  const synced = status.running
    ? 'Syncing now…'
    : run
      ? `Synced ${timeAgo(run.finished_at || run.started_at)}`
      : 'Not synced yet'
  return (
    <p className="bs-bank-line">
      {[synced, ends && `access until ${fDate(ends)}`].filter(Boolean).join(' · ')}
    </p>
  )
}
