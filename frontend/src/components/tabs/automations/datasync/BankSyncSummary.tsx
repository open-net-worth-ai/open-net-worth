import { useNavigate } from '@tanstack/react-router'
import { Button, Text } from '../../../ui'
import { useFlag } from '../../../../hooks/useFlag'
import { everyLabel, nextRunLine } from './bankSyncHelpers'
import { bankSyncSearch, useBankSync, type BankSyncStatus } from './bankSync'
import './bankSync.css'

// The bank sync on a connector's page (ADR 2026-11-13). Before it's set up: the connector's one
// "Next: bring in transactions" card, with Set up sync. After: one line (on or off, next sync)
// and Manage. Both open its page in Automations → Data sync. Nothing for a connector without a
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
  if (status.stage !== 'done') {
    // Before it's set up this IS the connector's next step, in the same card the Lucius
    // suggestion used: one way in, not two. Nothing until an account is linked.
    if (!(status.accounts ?? []).length) return null
    return (
      <section className="conn-next" aria-label={`${status.label} sync`}>
        <Text size="sm" tone="ink" weight={600}>
          Next: bring in transactions
        </Text>
        <Text size="xs" tone="muted">
          {`Linking doesn’t import anything yet. Sync brings in your history, then new transactions ${everyLabel(status.interval_hours).toLowerCase()}.`}
        </Text>
        <div className="conn-next-actions">
          <Button size="sm" variant="primary" onClick={open}>
            Set up sync
          </Button>
        </div>
      </section>
    )
  }
  return (
    <section className="bs-summary" aria-label={`${status.label} sync`}>
      <div className="bs-summary-text">
        <div className="cn-sublabel">Sync</div>
        <p className="bs-note">{summaryLine(status)}</p>
      </div>
      <Button size="sm" variant="secondary" onClick={open}>
        Manage
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
