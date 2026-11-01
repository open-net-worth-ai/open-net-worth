import { useState } from 'react'
import {
  ActionMenu,
  Badge,
  Button,
  Checkbox,
  Cluster,
  EmptyState,
  ErrorState,
  Panel,
  SegmentedControl,
  Skeleton,
  Stack,
  Tag,
  Text,
} from '../../ui'
import {
  KIND_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  formatDuration,
  formatWhen,
  setArchived,
  type RunKind,
  type RunSummary,
} from './runs'

type KindFilter = 'all' | RunKind

const FILTERS: { value: KindFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'import', label: KIND_LABEL.import },
  { value: 'verify', label: KIND_LABEL.verify },
  { value: 'preview', label: KIND_LABEL.preview },
  { value: 'sync', label: KIND_LABEL.sync },
]

// Run history: every import, verify and preview of the provider, newest first. Archiving only
// hides a finished run from the default list; nothing is deleted.
export function RunHistory(props: {
  runs: RunSummary[] | undefined
  error: unknown
  showArchived: boolean
  onShowArchived: (v: boolean) => void
  onOpen: (id: string) => void
  onChanged: () => void
}) {
  const { runs, error } = props
  const [kind, setKind] = useState<KindFilter>('all')
  const [actionError, setActionError] = useState<string | null>(null)
  const shown = (runs ?? []).filter((r) => kind === 'all' || r.kind === kind)

  async function toggleArchive(run: RunSummary) {
    setActionError(await setArchived(run.id, !run.archived_at))
    props.onChanged()
  }

  return (
    <Panel title="Run history">
      <Stack gap="--space-3">
        <Cluster gap="--space-3">
          <SegmentedControl
            ariaLabel="Filter runs by kind"
            options={FILTERS}
            value={kind}
            onChange={setKind}
          />
          <Checkbox
            checked={props.showArchived}
            onChange={(e) => props.onShowArchived(e.target.checked)}
            label="Show archived"
          />
        </Cluster>
        {actionError && (
          <Text size="sm" tone="bad">
            {actionError}
          </Text>
        )}
        {error ? (
          <ErrorState
            message={error instanceof Error ? error.message : 'Could not load the run history.'}
            onRetry={props.onChanged}
          />
        ) : runs === undefined ? (
          <Skeleton height={96} />
        ) : shown.length === 0 ? (
          <EmptyState title="No runs yet" body="Preview a plan to see what would change." />
        ) : (
          <table className="imp-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Status</th>
                <th>Outcome</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((run) => (
                <tr
                  key={run.id}
                  className={run.archived_at ? 'imp-table__row--archived' : undefined}
                >
                  <td data-label="Kind">
                    <Cluster gap="--space-2">
                      <Badge tone="muted">{KIND_LABEL[run.kind]}</Badge>
                      {run.trigger === 'scheduled' && <Tag tone="muted">Scheduled</Tag>}
                      {run.archived_at && <Tag tone="muted">Archived</Tag>}
                    </Cluster>
                  </td>
                  <td data-label="Started">{formatWhen(run.started_at)}</td>
                  <td data-label="Duration">{formatDuration(run)}</td>
                  <td data-label="Status">
                    <Badge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</Badge>
                  </td>
                  <td data-label="Outcome" className="imp-table__wrap">
                    {run.status === 'running' ? run.phase : run.outcome || '—'}
                  </td>
                  <td className="imp-table__actions">
                    <Cluster gap="--space-2" justify="end">
                      <Button size="sm" variant="secondary" onClick={() => props.onOpen(run.id)}>
                        Open
                      </Button>
                      <ActionMenu
                        label={`More actions for the ${KIND_LABEL[run.kind]} run`}
                        items={[
                          {
                            label: run.archived_at ? 'Unarchive' : 'Archive',
                            disabled: run.status === 'running',
                            onSelect: () => void toggleArchive(run),
                          },
                        ]}
                      />
                    </Cluster>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Stack>
    </Panel>
  )
}
