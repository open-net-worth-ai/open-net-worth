import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Badge,
  Button,
  Cluster,
  ErrorState,
  Meter,
  Panel,
  Skeleton,
  Stack,
  SummaryBar,
  Text,
} from '../../ui'
import { fTime } from '../../../utils/format'
import { ProblemList, ResultsTable, VerificationBody } from './ReportTables'
import { SyncSections } from './SyncReport'
import {
  KIND_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  formatDuration,
  formatWhen,
  runTitle,
  setArchived,
  useRun,
  type Preview,
  type RunRecord,
} from './runs'

const BOOK_STATE_LABEL = {
  fresh: 'First import',
  rerun: 'Re-import of this plan',
  foreign: 'Import alongside existing accounts',
}

// One run, opened as a page. A running run polls and fills in as it goes; a failed or
// interrupted one shows everything it captured before it stopped.
export function RunReport({ runId, onBack }: { runId: string; onBack: () => void }) {
  const run = useRun(runId)
  const [actionError, setActionError] = useState<string | null>(null)
  const r = run.data

  if (run.error) {
    return (
      <ErrorState
        message={run.error instanceof Error ? run.error.message : 'Could not load that run.'}
        onRetry={() => void run.refresh()}
      />
    )
  }
  if (!r) return <Skeleton height={160} />

  async function toggleArchive(rec: RunRecord) {
    setActionError(await setArchived(rec.id, !rec.archived_at))
    void run.refresh()
  }

  return (
    <Stack gap="--space-6">
      <Cluster justify="between" gap="--space-3">
        <Cluster gap="--space-3">
          <Button size="sm" variant="ghost" onClick={onBack}>
            ‹ Runs
          </Button>
          <Text size="lg" weight={600}>
            {runTitle(r)}
          </Text>
          <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>
        </Cluster>
        <Button
          size="sm"
          variant="secondary"
          disabled={r.status === 'running'}
          onClick={() => void toggleArchive(r)}
        >
          {r.archived_at ? 'Unarchive' : 'Archive'}
        </Button>
      </Cluster>
      {actionError && (
        <Text size="sm" tone="bad">
          {actionError}
        </Text>
      )}
      <SummaryBar
        cards={[
          { label: 'Kind', value: KIND_LABEL[r.kind] },
          { label: 'Trigger', value: r.trigger === 'scheduled' ? 'Scheduled' : 'Manual' },
          { label: 'Started', value: formatWhen(r.started_at) },
          { label: 'Duration', value: formatDuration(r) },
          { label: 'Plan', value: r.plan_name || r.plan_id },
          ...(r.kind === 'sync' && r.requests_used != null
            ? [{ label: 'YNAB requests', value: String(r.requests_used) }]
            : []),
        ]}
      />
      {r.outcome && <Text size="sm">{r.outcome}</Text>}
      {r.status === 'failed' && <ErrorState title="This run failed" message={r.error} />}
      {r.status === 'interrupted' && (
        <Text size="sm" tone="warn">
          {r.error}
        </Text>
      )}
      {r.status === 'running' && <Progress run={r} />}
      <Sections run={r} />
      <Log run={r} />
    </Stack>
  )
}

function Progress({ run }: { run: RunRecord }) {
  const { done, total } = run.progress
  return (
    <Panel title="Progress">
      <Stack gap="--space-2">
        <Text size="sm" weight={600}>
          {run.phase}
        </Text>
        {total > 0 && (
          <Meter
            value={(done / total) * 100}
            ariaLabel="Run progress"
            valueText={`${done} of ${total}`}
          />
        )}
      </Stack>
    </Panel>
  )
}

function Sections({ run }: { run: RunRecord }) {
  if (run.kind === 'sync') return <SyncSections run={run} />
  const problems = [...run.problems, ...run.warnings]
  return (
    <>
      {run.kind === 'import' && <Setup run={run} />}
      {run.kind === 'preview' && run.preview && <WouldCreate preview={run.preview} />}
      {run.kind === 'import' && (
        <Panel title="Results">
          <ResultsTable counts={run.counts} />
        </Panel>
      )}
      {problems.length > 0 && (
        <Panel title="Needs attention" badge={problems.length}>
          <ProblemList problems={problems} />
        </Panel>
      )}
      {run.verification && (
        <Panel
          title="Verification"
          subtitle={`${run.verification.checks_total} checks, ${run.verification.checks_matching} match`}
        >
          <VerificationBody verification={run.verification} />
        </Panel>
      )}
    </>
  )
}

function Setup({ run }: { run: RunRecord }) {
  const navigate = useNavigate()
  return (
    <Panel title="Setup">
      <Stack gap="--space-2">
        <Text size="sm">{run.book_state ? BOOK_STATE_LABEL[run.book_state] : '—'}</Text>
        {run.backup_name && (
          <Cluster gap="--space-2">
            <Text size="sm">Backup taken first: {run.backup_name}.</Text>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void navigate({ to: '/settings', search: { sub: 'data' } } as never)}
            >
              Settings › Data
            </Button>
          </Cluster>
        )}
      </Stack>
    </Panel>
  )
}

function WouldCreate({ preview: p }: { preview: Preview }) {
  return (
    <Panel title="Would create" subtitle="Nothing was written.">
      <SummaryBar
        cards={[
          { label: 'Accounts', value: String(p.accounts.length) },
          { label: 'Transactions', value: String(p.transactions) },
          { label: 'Categories', value: String(p.categories), caption: `${p.groups} groups` },
          { label: 'Budget months', value: String(p.budget_months) },
        ]}
      />
    </Panel>
  )
}

function Log({ run }: { run: RunRecord }) {
  if (run.log.length === 0) return null
  const live = run.status === 'running'
  return (
    <Panel title="Log" collapsible defaultCollapsed={!live} key={live ? 'live' : 'done'}>
      <Stack gap="--space-1">
        {run.log.map((e, i) => (
          <Text key={i} size="xs" tone="faint">
            {fTime(new Date(e.at))} · {e.line}
          </Text>
        ))}
      </Stack>
    </Panel>
  )
}
