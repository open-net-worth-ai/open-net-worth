import { useNavigate } from '@tanstack/react-router'
import { useFlag } from '../../../../hooks/useFlag'
import { bankSyncSearch, useBankSync } from './bankSync'

// "Automate" on a bank connector (owner, 2026-11-14): two ways to put it to work, said plainly —
// the sync, which books by fixed rules with no AI, and a Lucius Automation, which fetches, books
// and categorises. They are independent (ADR 2026-11-13, Amendment 6). Null for a connector
// without a bank sync, which keeps its single Automate button.

// The connectors that have a bank sync (backend ``settings.capable_providers``).
const BANK_SYNC_KINDS = new Set(['gocardless', 'enable_banking', 'plaid'])

/** Whether `provider` has a bank sync to offer (the flag on, and a bank that has one). */
export function useHasBankSync(provider: string): boolean {
  return useFlag('bank_data_sync') === true && BANK_SYNC_KINDS.has(provider)
}

export interface AutomateChoice {
  label: string
  onSelect: () => void
}

export function useBankAutomateChoices(
  provider: string,
  openLuciusTemplate: (() => void) | null,
): AutomateChoice[] | null {
  const navigate = useNavigate()
  const on = useHasBankSync(provider)
  const status = useBankSync(provider, on).data
  if (!on || !status) return null
  const choices: AutomateChoice[] = [
    {
      label: status.mode === 'data' ? 'Sync transactions · On' : 'Sync transactions (no AI)',
      onSelect: () =>
        void navigate({ to: '/automations', search: bankSyncSearch(provider) as never } as never),
    },
  ]
  if (openLuciusTemplate) choices.push({ label: 'Lucius Automation', onSelect: openLuciusTemplate })
  return choices
}
