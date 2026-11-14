import { useNavigate } from '@tanstack/react-router'
import { Button } from '../../../ui'
import { useFlag } from '../../../../hooks/useFlag'
import { everyLabel, nextRunLine } from './bankSyncHelpers'
import { bankSyncSearch, useBankSync, type BankSyncStatus } from './bankSync'
import './bankSync.css'

// The bank sync on a connector's page (ADR 2026-11-13). Before it's set up: the connector's one
// "Next: bring in transactions" card, with Set up sync. After: one line (on or off, next sync)
// and Manage. Both open its page in Automations → Data Sync. Nothing for a connector without a
// sync, or with the flag off.

export function BankSyncSection({ provider }: { provider: string }) {
  return useFlag('bank_data_sync') === true ? <BankSyncSummary provider={provider} /> : null
}

function BankSyncSummary({ provider }: { provider: string }) {
  const navigate = useNavigate()
  const sync = useBankSync(provider)
  const status = sync.data
  if (!status) return null
  const open = () =>
    void navigate({ to: '/automations', search: bankSyncSearch(provider) as never } as never)
  // Nothing to sync until an account is linked; then one line and a way in, set up or not.
  const setUp = status.stage === 'done'
  if (!setUp && !(status.accounts ?? []).length) return null
  return (
    <section className="bs-summary" aria-label={`${status.label} sync`}>
      <div className="bs-summary-text">
        <div className="cn-sublabel">Sync</div>
        <p className="bs-note">
          {setUp
            ? summaryLine(status)
            : `Not set up · your history, then new transactions ${everyLabel(status.interval_hours).toLowerCase()}`}
        </p>
      </div>
      <Button size="sm" variant={setUp ? 'secondary' : 'primary'} onClick={open}>
        {setUp ? 'Manage' : 'Set up sync'}
      </Button>
    </section>
  )
}

function summaryLine(status: BankSyncStatus): string {
  const review = status.to_review ? ` · ${status.to_review} to review` : ''
  if (status.mode === 'data')
    return `On · ${status.running ? 'Syncing now…' : nextRunLine(status)}${review}`
  if (status.mode === 'lucius') return `Off · a Lucius Automation syncs it${review}`
  return 'Off'
}
