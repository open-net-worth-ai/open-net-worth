import { useState } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { Container, PageTabs, Stack } from '../../ui'
import { RunHistory } from './RunHistory'
import { RunReport } from './RunReport'
import { SyncPanel } from './SyncPanel'
import { YnabImport } from './YnabImport'
import { useRuns } from './runs'

// Imports & sync: bring your history in from another budgeting app. A provider is a tab; its
// panels, the run history and a run's report are the same shell for any of them (today only
// YNAB). `?provider=ynab&run=<id>` opens one run as a report.

const PROVIDERS = [{ key: 'ynab', label: 'YNAB' }] as const
type Provider = (typeof PROVIDERS)[number]['key']

export function ImportsTab() {
  const navigate = useNavigate()
  const search = useRouterState({
    select: (st) => st.location.search as { provider?: unknown; run?: unknown },
  })
  const provider: Provider = PROVIDERS.some((p) => p.key === search.provider)
    ? (search.provider as Provider)
    : PROVIDERS[0].key
  const runId = typeof search.run === 'string' && search.run ? search.run : null

  const [showArchived, setShowArchived] = useState(false)
  const runs = useRuns(provider, showArchived, !runId)

  const open = (run: string | undefined) =>
    void navigate({ to: '/imports', search: { provider, run } } as never)

  return (
    <Container size="wide">
      <Stack gap="--space-6">
        {runId ? (
          <RunReport runId={runId} onBack={() => open(undefined)} />
        ) : (
          <>
            <PageTabs
              tabs={[...PROVIDERS]}
              value={provider}
              onChange={(key) =>
                void navigate({ to: '/imports', search: { provider: key } } as never)
              }
              ariaLabel="Providers"
            />
            <YnabImport runs={runs.data} onStarted={() => void runs.refresh()} onOpen={open} />
            <SyncPanel onOpen={open} />
            <RunHistory
              runs={runs.data}
              error={runs.error}
              showArchived={showArchived}
              onShowArchived={setShowArchived}
              onOpen={open}
              onChanged={() => void runs.refresh()}
            />
          </>
        )}
      </Stack>
    </Container>
  )
}
