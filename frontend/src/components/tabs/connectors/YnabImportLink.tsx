import { useNavigate } from '@tanstack/react-router'
import { Badge, Button, Cluster, Text } from '../../ui'
import { fDate } from '../../../utils/format'
import { useRuns } from '../imports/runs'

// The YNAB card's way into Imports & sync: a one-line status and the button that opens it. The
// import itself, its history and its reports live on that page.
export function YnabImportLink() {
  const navigate = useNavigate()
  const runs = useRuns('ynab', false)
  const running = runs.data?.find((r) => r.status === 'running')
  const imported = runs.data?.find((r) => r.kind === 'import' && r.status === 'done')

  return (
    <Cluster gap="--space-3" justify="between">
      <Cluster gap="--space-2">
        {running ? (
          <Badge tone="neutral">{running.phase}</Badge>
        ) : (
          <Text size="sm" tone="faint">
            {imported
              ? `Imported ${fDate(imported.started_at, 'short', 'always')}`
              : 'Not imported yet'}
          </Text>
        )}
      </Cluster>
      <Button
        variant="secondary"
        onClick={() => void navigate({ to: '/imports', search: { provider: 'ynab' } } as never)}
      >
        Import &amp; sync…
      </Button>
    </Cluster>
  )
}
