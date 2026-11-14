import { useNavigate } from '@tanstack/react-router'
import { RefreshCw } from 'lucide-react'
import { Button, Text } from '../../../ui'
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
    // Headed like the Accounts list above it, with the sync's own icon: it read as small print
    // (owner, 2026-11-14), without a box of its own.
    <section className="bs-summary" aria-label={`${status.label} sync`}>
      <Text size="sm" tone="ink" weight={600}>
        Data import and sync
      </Text>
      <div className="bs-summary-row">
        <RefreshCw size={15} className="bs-summary-icon" aria-hidden="true" />
        <p className="bs-summary-line">
          {setUp
            ? summaryLine(status)
            : `Bring in your history, then new transactions ${everyLabel(status.interval_hours).toLowerCase()}.`}
        </p>
        <Button size="sm" variant={setUp ? 'secondary' : 'primary'} onClick={open}>
          {setUp ? 'Manage' : 'Set up sync'}
        </Button>
      </div>
    </section>
  )
}

function summaryLine(status: BankSyncStatus): string {
  const review = status.to_review ? ` · ${status.to_review} to review` : ''
  if (status.mode === 'data')
    return `On · ${status.running ? 'Syncing now…' : nextRunLine(status)}${review}`
  return 'Off'
}
