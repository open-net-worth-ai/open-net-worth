import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import {
  Badge,
  Button,
  CheckboxGroup,
  Cluster,
  EmptyState,
  ErrorState,
  FormField,
  Loading,
  Modal,
  Panel,
  Select,
  Stack,
  Text,
  TextField,
} from '../../ui'
import { errorText, tradingMoney, tradingPercent, tradingTime } from './presentation'
import { ComparisonPerformance } from './ComparisonPerformance'
import { ComparisonScenario } from './ComparisonScenario'

export function TradingComparisons({
  comparisonId,
  onSelect,
  onRun,
}: {
  comparisonId: string
  onSelect: (id: string) => void
  onRun: (id: string, workspaceId: string) => void
}) {
  const comparisons = useQueryCache(`${tradingRoot}/comparisons`, tradingApi.comparisons)
  const runs = useQueryCache(`${tradingRoot}/runs`, tradingApi.allRuns)
  const selected = comparisonId || comparisons.data?.items[0]?.id || ''
  const comparison = useQueryCache(
    `${tradingRoot}/comparisons/${selected}`,
    () => tradingApi.comparison(selected),
    { enabled: !!selected },
  )
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [participants, setParticipants] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  useLayoutEffect(() => {
    const active = document.activeElement
    if (
      open &&
      (active === document.body ||
        (active instanceof HTMLButtonElement &&
          active.disabled &&
          formRef.current?.contains(active)))
    ) {
      formRef.current?.focus({ preventScroll: true })
    }
  }, [open, busy])
  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const result = await tradingApi.createComparison(name, participants)
      setOpen(false)
      onSelect(result.id)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  const results = comparison.data?.results ?? []
  return (
    <Stack gap="--space-6">
      <Cluster justify="between">
        <Text tone="muted">
          Compare runs with the same initial conditions and declared execution assumptions.
        </Text>
        <Button variant="primary" onClick={() => setOpen(true)}>
          New comparison
        </Button>
      </Cluster>
      {comparisons.initialLoading && <Loading label="Loading comparisons…" />}
      {!!comparisons.error && (
        <ErrorState
          message={errorText(comparisons.error)}
          onRetry={() => void comparisons.refresh()}
        />
      )}
      {!comparisons.initialLoading && !comparisons.error && !comparisons.data?.items.length && (
        <EmptyState
          title="No comparisons yet"
          body="Choose runs with shared starting conditions to compare their performance."
        />
      )}
      {!!comparisons.data?.items.length && (
        <Select
          aria-label="Selected comparison"
          value={selected}
          onChange={(e) => onSelect(e.target.value)}
          options={comparisons.data.items.map((item) => ({ value: item.id, label: item.name }))}
        />
      )}
      {!!comparison.error && (
        <ErrorState
          message={errorText(comparison.error)}
          onRetry={() => void comparison.refresh()}
        />
      )}
      {comparison.data && (
        <>
          <Panel title={comparison.data.name}>
            <Stack gap="--space-4">
              <Badge tone={comparison.data.comparable === false ? 'warn' : 'neutral'}>
                {comparison.data.comparable === false ? 'Not comparable' : 'Shared scenario'}
              </Badge>
              {comparison.data.reasons?.map((reason) => (
                <Text key={reason} tone="warn">
                  {reason}
                </Text>
              ))}
              <Text>
                {comparison.data.run_ids.length} participants · Model usage costs are reported
                separately from trading performance.
              </Text>
              <ComparisonScenario comparison={comparison.data} />
            </Stack>
          </Panel>
          <Panel title="Results">
            <div className="trading-table-wrap">
              <table className="trading-table">
                <thead>
                  <tr>
                    <th>Agent / run</th>
                    <th>Equity</th>
                    <th>Return</th>
                    <th>Maximum drawdown</th>
                    <th>Trading fees</th>
                    <th>Agent cost</th>
                    <th>Valuation</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((result) => (
                    <tr key={result.run_id}>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            const run = runs.data?.items.find((item) => item.id === result.run_id)
                            const workspaceId = result.workspace_id ?? run?.workspace_id
                            if (workspaceId) onRun(result.run_id, workspaceId)
                          }}
                        >
                          {result.agent_label || 'Agent'} · {result.run_id.slice(-12)}
                        </Button>
                        <Text as="div" size="xs" tone="muted" title={result.run_id}>
                          {result.mode ?? 'Unknown mode'} · {result.run_id}
                        </Text>
                        <Text as="div" size="xs" tone="muted">
                          {tradingTime(result.start_at)} – {tradingTime(result.end_at)}
                        </Text>
                        <Text as="div" size="xs" tone="muted">
                          Strategy {result.agent_version ?? 'unspecified'} · Observed{' '}
                          {tradingTime(result.current_time)}
                        </Text>
                      </td>
                      <td className="trading-number">
                        {tradingMoney(result.equity, result.base_currency)}
                      </td>
                      <td className="trading-number">{tradingPercent(result.return_pct)}</td>
                      <td className="trading-number">{tradingPercent(result.max_drawdown_pct)}</td>
                      <td className="trading-number">
                        {tradingMoney(result.fees, result.base_currency)}
                      </td>
                      <td className="trading-number">
                        {tradingMoney(result.model_cost_usd, 'USD')}
                      </td>
                      <td>
                        <Badge tone={result.valuation_complete ? 'neutral' : 'warn'}>
                          {result.valuation_complete ? 'Complete' : 'Awaiting marks'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!results.length && <EmptyState title="No results available" />}
          </Panel>
          <ComparisonPerformance results={results} />
        </>
      )}
      {open && (
        <Modal open onClose={() => setOpen(false)} title="New agent comparison" size="lg">
          <form ref={formRef} tabIndex={-1} onSubmit={save}>
            <Stack gap="--space-4">
              <FormField label="Comparison name">
                <TextField
                  aria-label="Comparison name"
                  required
                  maxLength={120}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </FormField>
              {runs.initialLoading ? (
                <Loading label="Loading participant runs…" />
              ) : (
                <CheckboxGroup
                  ariaLabel="Participant runs"
                  value={participants}
                  onChange={setParticipants}
                  options={(runs.data?.items ?? []).map((run) => ({
                    value: run.id,
                    label: `${run.workspace_snapshot.agent_label} · ${run.mode} · ${tradingTime(run.created_at)}`,
                    description: `${run.data_version} · ${run.agent_version}`,
                  }))}
                  emptyText="Create at least two runs to compare."
                />
              )}
              <Text tone="muted">
                The server checks period, capital, universe, data and execution assumptions.
                Incompatible runs cannot be ranked as equivalent.
              </Text>
              {error && <ErrorState message={error} />}
              <Button type="submit" disabled={busy || participants.length < 2}>
                {busy ? 'Creating…' : 'Create comparison'}
              </Button>
            </Stack>
          </form>
        </Modal>
      )}
    </Stack>
  )
}
