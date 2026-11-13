import { useState } from 'react'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fScheduleWhen } from '../../../../utils/format'
import { Button, Checkbox, Modal } from '../../../ui'
import { Clock3 } from 'lucide-react'
import { ConnectorAccountsList } from '../../connectors/ConnectorAccountsList'
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

/** Each linked account with its Sync box. */
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
  return (
    <div className="bs-accounts" role="group" aria-label="Accounts to sync">
      {(status.accounts ?? []).map((a) => (
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
  if (a.reads_left > 0) return `${a.reads_left} of ${daily} reads left today`
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
          subtitle="Add each bank account as a new account, or link it to one you already have."
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
