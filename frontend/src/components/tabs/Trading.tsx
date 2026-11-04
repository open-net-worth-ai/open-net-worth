import { useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTradingWorkspaces, useTradingRuns } from '../../hooks/useTrading'
import { useQueryCache } from '../../state/queryCache'
import { tradingApi, tradingRoot } from '../../api/trading'
import type { TradingWorkspace } from '../../types/trading'
import {
  Badge,
  Button,
  Container,
  EmptyState,
  ErrorState,
  Grid,
  Loading,
  PageHeader,
  PageTabs,
  Panel,
  Stack,
  Text,
  TextField,
} from '../ui'
import { WorkspaceForm } from './trading/WorkspaceForm'
import { WorkspaceDetail, type TradingSection } from './trading/WorkspaceDetail'
import { TradingComparisons } from './trading/TradingComparisons'
import {
  errorText,
  readable,
  statusTone,
  tradingTime,
  tradingMoney,
  tradingPercent,
} from './trading/presentation'

export function Trading() {
  const search = useSearch({ from: '/trading' })
  const navigate = useNavigate()
  const workspaces = useTradingWorkspaces()
  const [form, setForm] = useState<TradingWorkspace | 'new' | null>(null)
  const [filter, setFilter] = useState('')
  const selected = workspaces.data?.items.find((workspace) => workspace.id === search.workspace)
  const view = search.view ?? 'workspaces'
  const section = (search.section ?? 'overview') as TradingSection
  function route(patch: Partial<typeof search>) {
    void navigate({ to: '/trading', search: { ...search, ...patch } })
  }
  const items = (workspaces.data?.items ?? []).filter((item) =>
    `${item.name} ${item.agent_label}`.toLowerCase().includes(filter.toLowerCase()),
  )
  return (
    <Container size="wide" className="trading-page">
      <Stack gap="--space-6">
        <PageHeader
          title="Trading"
          subtitle="Autonomous trading workspaces, from backtest to live"
          actions={
            <Button variant="primary" onClick={() => setForm('new')}>
              New workspace
            </Button>
          }
        />
        <PageTabs
          ariaLabel="Trading views"
          value={view}
          onChange={(value) => route({ view: value })}
          tabs={[
            { key: 'workspaces', label: 'Workspaces' },
            { key: 'compare', label: 'Compare' },
          ]}
        />
        <div role="tabpanel" aria-labelledby={`ui-page-tab-${view}`}>
          {view === 'compare' ? (
            <TradingComparisons
              comparisonId={search.comparison ?? ''}
              onSelect={(id) => route({ comparison: id })}
              onRun={(run, workspace) => route({ view: 'workspaces', workspace, run })}
            />
          ) : (
            <>
              {workspaces.initialLoading && <Loading label="Loading trading workspaces…" />}
              {!!workspaces.error && (
                <ErrorState
                  message={errorText(workspaces.error)}
                  onRetry={() => void workspaces.refresh()}
                />
              )}
              {selected ? (
                <WorkspaceDetail
                  key={selected.id}
                  workspace={selected}
                  runId={search.run ?? ''}
                  section={section}
                  onRun={(id) => route({ run: id })}
                  onSection={(value) => route({ section: value })}
                  onBack={() => route({ workspace: undefined, run: undefined, section: undefined })}
                  onEdit={() => setForm(selected)}
                />
              ) : search.workspace && !workspaces.initialLoading && !workspaces.error ? (
                <ErrorState
                  message="This workspace was not found."
                  onRetry={() => route({ workspace: undefined, run: undefined })}
                />
              ) : (
                <Stack gap="--space-6">
                  {!workspaces.initialLoading &&
                  !workspaces.error &&
                  !workspaces.data?.items.length ? (
                    <EmptyState
                      title="Create your first trading workspace"
                      body="Give an agent a defined allocation and period, then follow its actions and results."
                      action={
                        <Button variant="primary" onClick={() => setForm('new')}>
                          New workspace
                        </Button>
                      }
                    />
                  ) : (
                    <>
                      <TextField
                        aria-label="Search workspaces"
                        placeholder="Search workspaces"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      />
                      <Grid>
                        {items.map((workspace) => (
                          <WorkspaceCard
                            key={workspace.id}
                            workspace={workspace}
                            onOpen={() => route({ workspace: workspace.id, run: undefined })}
                          />
                        ))}
                      </Grid>
                      {!!workspaces.data?.items.length && !items.length && (
                        <EmptyState title="No matching workspaces" />
                      )}
                    </>
                  )}
                </Stack>
              )}
            </>
          )}
        </div>
        {form && (
          <WorkspaceForm
            workspace={form === 'new' ? undefined : form}
            onClose={() => setForm(null)}
            onSaved={(workspace) => {
              setForm(null)
              route({ view: 'workspaces', workspace: workspace.id })
            }}
          />
        )}
      </Stack>
    </Container>
  )
}

function WorkspaceCard({ workspace, onOpen }: { workspace: TradingWorkspace; onOpen: () => void }) {
  const runs = useTradingRuns(workspace.id)
  const latest = runs.data?.items.at(-1)
  const performance = useQueryCache(
    `${tradingRoot}/runs/${latest?.id ?? ''}/performance`,
    () => tradingApi.performance(latest!.id),
    { enabled: !!latest },
  )
  return (
    <Panel title={workspace.name} className="trading-workspace-card">
      <Stack gap="--space-4">
        <Text>
          {workspace.agent_label} · {workspace.mandate.allowed_asset_classes.join(', ')}
        </Text>
        {latest ? (
          <>
            <Badge tone={statusTone(latest.state)}>{readable(latest.state)}</Badge>
            <Text size="sm" tone="muted">
              {latest.mode} · {tradingTime(latest.start_at)} – {tradingTime(latest.end_at)}
            </Text>
          </>
        ) : (
          <Text tone="muted">
            {runs.initialLoading
              ? 'Loading latest run…'
              : runs.error
                ? 'Run status unavailable'
                : 'No runs yet'}
          </Text>
        )}
        <Text size="sm" tone="muted">
          {workspace.instructions || 'Create a run to define its capital, period and environment.'}
        </Text>
        {performance.data && (
          <Text size="sm">
            Equity {tradingMoney(performance.data.equity, performance.data.base_currency)} · Return{' '}
            {tradingPercent(performance.data.return_pct)} · Drawdown{' '}
            {tradingPercent(performance.data.max_drawdown_pct)}
          </Text>
        )}
        <Button onClick={onOpen}>Open workspace</Button>
      </Stack>
    </Panel>
  )
}
