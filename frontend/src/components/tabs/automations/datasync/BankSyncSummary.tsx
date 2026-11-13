import { useNavigate } from '@tanstack/react-router'
import { Button } from '../../../ui'
import { useFlag } from '../../../../hooks/useFlag'
import { nextRunLine } from './bankSyncHelpers'
import { bankSyncSearch, useBankSync, type BankSyncStatus } from './bankSync'
import './bankSync.css'

// The bank sync on a connector's page (ADR 2026-11-13): one line saying who books this bank's
// transactions, and a button to its own page in Automations → Data sync, where it is set up and
// managed. Nothing for a connector without a sync, or with the flag off.

export function BankSyncSection({ provider }: { provider: string }) {
  return useFlag('bank_data_sync') === true ? <BankSyncSummary provider={provider} /> : null
}

function BankSyncSummary({ provider }: { provider: string }) {
  const navigate = useNavigate()
  const sync = useBankSync(provider)
  const status = sync.data
  if (!status) return null
  const setUp = status.stage === 'done'
  return (
    <section className="bs-summary" aria-label={`${status.label} sync`}>
      <div className="bs-summary-text">
        <div className="cn-sublabel">Sync</div>
        <p className="bs-note">{summaryLine(status)}</p>
      </div>
      <Button
        size="sm"
        variant={setUp ? 'secondary' : 'primary'}
        onClick={() =>
          void navigate({ to: '/automations', search: bankSyncSearch(provider) as never } as never)
        }
      >
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
