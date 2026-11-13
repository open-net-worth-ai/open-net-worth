import { useState } from 'react'
import { invalidateQueryCache } from '../../../../state/queryCache'
import { fDate, fScheduleWhen, timeAgo } from '../../../../utils/format'
import { Button, Checkbox, Modal } from '../../../ui'
import { useNavigate } from '@tanstack/react-router'
import { Clock3, Wallet } from 'lucide-react'
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

/** One card per bank — one connector can hold several (Enable Banking, GoCardless), and an
 *  account's own name doesn't say which: the bank and how it stands on top, then two labelled
 *  columns, the bank's account (with its Sync box) and the account in your book it books into.
 *  Both are always shown: one added from the connector has the same name, and a row naming it
 *  once left no telling which side was which. */
export function AccountBoxes({
  status,
  busy,
  act,
}: {
  status: BankSyncStatus
  busy: string | null
  act: Act
}) {
  const navigate = useNavigate()
  const p = status.provider
  const banks = new Map<string, BankSyncAccount[]>()
  for (const a of status.accounts ?? []) {
    const bank = a.bank || 'Other'
    banks.set(bank, [...(banks.get(bank) ?? []), a])
  }
  return (
    <div className="bs-banks">
      {[...banks].map(([bank, accounts]) => (
        <section
          key={bank}
          className="bs-bank"
          role="group"
          aria-label={`${bank} accounts to sync`}
        >
          <header className="bs-bank-head">
            <span className="bs-bank-name">{bank}</span>
            <BankStatus status={status} accounts={accounts} />
          </header>
          <div className="bs-acct bs-acct-cols" aria-hidden="true">
            <span>At the bank</span>
            <span>In your book</span>
          </div>
          <ul className="bs-bank-accts">
            {accounts.map((a) => (
              <li key={a.account_id} className="bs-acct">
                <Checkbox
                  id={`bs-${p}-${a.account_id}`}
                  label={
                    <>
                      {a.bank_name || a.name}
                      {a.mask && <span className="bs-acct-mask">{`•••${a.mask}`}</span>}
                    </>
                  }
                  description={syncLine(a, status.daily_reads) || undefined}
                  checked={a.sync}
                  disabled={busy !== null}
                  onChange={() =>
                    void act('account', () =>
                      bankSyncSend(`/${p}/accounts/${a.account_id}`, 'PUT', { sync: !a.sync }),
                    )
                  }
                />
                <button
                  type="button"
                  className="bs-acct-book"
                  title="Open it in Accounts"
                  onClick={() =>
                    void navigate({
                      to: '/accounts',
                      search: { account: a.account_id } as never,
                    } as never)
                  }
                >
                  <Wallet size={13} aria-hidden="true" />
                  {a.name}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
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

const PILL = {
  ok: 'bs-pill',
  warn: 'bs-pill bs-pill-warn',
  bad: 'bs-pill bs-pill-bad',
} as const

/** How a bank stands, top right of its card: a coloured dot and a few words — and, when
 *  something needs you, the one button that fixes it. */
function BankStatus({ status, accounts }: { status: BankSyncStatus; accounts: BankSyncAccount[] }) {
  const ended = accounts.some((a) => a.access_ended)
  const ends = accounts
    .map((a) => a.access_ends_at)
    .filter((d): d is string => !!d)
    .sort()[0]
  const soon = accounts.some((a) => a.access_renew_soon)
  const signIn = status.last_status === 'auth_error' || status.last_status === 'reconnect_needed'
  const problem: [keyof typeof PILL, string, string] | null = ended
    ? ['bad', 'Access ended', 'Renew']
    : signIn
      ? ['bad', 'Sign-in needed', 'Reconnect']
      : soon
        ? ['warn', `Access ends ${fDate(ends!)}`, 'Renew']
        : null
  const until = ends ? `Access until ${fDate(ends)}` : undefined
  if (problem)
    return (
      <span className="bs-bank-status">
        <span className={PILL[problem[0]]} title={until}>
          <span className="bs-dot" aria-hidden="true" />
          {problem[1]}
        </span>
        <ConnectorSetupButton status={status} label={problem[2]} link />
      </span>
    )
  // Nothing to say before the first sync: a grey "not synced yet" only raised a question.
  const run = status.last_run
  if (!status.running && !run) return null
  const text = status.running
    ? 'Syncing now…'
    : `Synced ${timeAgo(run!.finished_at || run!.started_at)}`
  return (
    <span className="bs-bank-status">
      <span className={PILL.ok} title={until}>
        <span className="bs-dot" aria-hidden="true" />
        {text}
      </span>
    </span>
  )
}
