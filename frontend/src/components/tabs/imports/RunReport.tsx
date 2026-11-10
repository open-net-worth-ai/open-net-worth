import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Badge,
  Button,
  Cluster,
  ErrorState,
  Meter,
  Modal,
  Panel,
  Skeleton,
  Stack,
  SummaryBar,
  Text,
} from '../../ui'
import { fTime } from '../../../utils/format'
import { appForProvider, type BudgetAppInfo } from '../automations/datasync/budgetApps'
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

const BOOK_STATE_LABEL = (app: BudgetAppInfo) => ({
  fresh: 'First import',
  rerun: `Re-import of this ${app.planWord}`,
  foreign: 'Import alongside existing accounts',
})

const titled = (word: string) => `${word[0]!.toUpperCase()}${word.slice(1)}`

// One run, opened as a page. A running run polls and fills in as it goes; a failed or
// interrupted one shows everything it captured before it stopped.
/** Everything under a run's header: its facts, outcome, problems, sections and log. */
function RunReportBody({ r }: { r: RunRecord }) {
  const app = appForProvider(r.provider)
  return (
    <>
      <SummaryBar
        cards={[
          { label: 'Kind', value: KIND_LABEL[r.kind] },
          { label: 'Trigger', value: r.trigger === 'scheduled' ? 'Scheduled' : 'Manual' },
          { label: 'Started', value: formatWhen(r.started_at) },
          { label: 'Duration', value: formatDuration(r) },
          { label: titled(app.planWord), value: r.plan_name || r.plan_id },
          ...(app.showsRequests && r.kind === 'sync' && r.requests_used != null
            ? [{ label: `${app.name} requests`, value: String(r.requests_used) }]
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
      <Sections app={app} run={r} />
      <Log run={r} />
    </>
  )
}

/** One run's report, as a modal over the page it was opened from (the YNAB data sync), so
 *  closing it returns to the step the user was on. The caller keeps it in the URL. */
export function RunReportModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const run = useRun(runId)
  const [actionError, setActionError] = useState<string | null>(null)
  const r = run.data

  async function toggleArchive(rec: RunRecord) {
    setActionError(await setArchived(rec.id, !rec.archived_at))
    void run.refresh()
  }

  return (
    <Modal
      open
      size="xl"
      onClose={onClose}
      title={
        r ? (
          <Cluster gap="--space-3">
            {runTitle(r)}
            <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>
          </Cluster>
        ) : (
          'Run report'
        )
      }
      headerActions={
        r ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={r.status === 'running'}
            onClick={() => void toggleArchive(r)}
          >
            {r.archived_at ? 'Unarchive' : 'Archive'}
          </Button>
        ) : undefined
      }
    >
      {run.error ? (
        <ErrorState
          message={run.error instanceof Error ? run.error.message : 'Could not load that run.'}
          onRetry={() => void run.refresh()}
        />
      ) : !r ? (
        <Skeleton height={160} />
      ) : (
        <Stack gap="--space-6">
          {actionError && (
            <Text size="sm" tone="bad">
              {actionError}
            </Text>
          )}
          <RunReportBody r={r} />
        </Stack>
      )}
    </Modal>
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

function Sections({ app, run }: { app: BudgetAppInfo; run: RunRecord }) {
  if (run.kind === 'sync') return <SyncSections app={app} run={run} />
  const problems = [...run.problems, ...run.warnings]
  return (
    <>
      {run.kind === 'import' && <Setup app={app} run={run} />}
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
          <VerificationBody app={app} verification={run.verification} />
        </Panel>
      )}
    </>
  )
}

function Setup({ app, run }: { app: BudgetAppInfo; run: RunRecord }) {
  const navigate = useNavigate()
  return (
    <Panel title="Setup">
      <Stack gap="--space-2">
        <Text size="sm">{run.book_state ? BOOK_STATE_LABEL(app)[run.book_state] : '—'}</Text>
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
