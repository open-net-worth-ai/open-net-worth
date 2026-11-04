import { useState } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import type { EvidenceSelection, TradingFile } from '../../../types/trading'
import { Button, Cluster, EmptyState, ErrorState, Loading, Select, Stack, Text } from '../../ui'
import { CodeBlock } from '../../chat/CodeBlock'
import { errorText } from './presentation'

export function TradingFiles({
  runId,
  onSelect,
}: {
  runId: string
  onSelect: (selection: EvidenceSelection) => void
}) {
  const [selected, setSelected] = useState<TradingFile | null>(null)
  const [latestVersion, setLatestVersion] = useState(1)
  const files = useQueryCache(`${tradingRoot}/runs/${runId}/files`, () => tradingApi.files(runId))
  const content = useQueryCache(
    `${tradingRoot}/runs/${runId}/files?name=${selected?.name}&version=${selected?.version}`,
    () => tradingApi.file(runId, selected!.name, selected!.version),
    { enabled: !!selected },
  )
  return (
    <Stack gap="--space-4">
      <Text weight={600}>Workspace file versions</Text>
      {files.initialLoading && <Loading label="Loading workspace files…" />}
      {!!files.error && (
        <ErrorState message={errorText(files.error)} onRetry={() => void files.refresh()} />
      )}
      {!files.initialLoading && !files.error && !files.data?.items.length && (
        <EmptyState
          title="No workspace files recorded"
          body="Files appear when this executor writes them through its workspace tools. Other artifacts can appear in execution records below."
        />
      )}
      {files.data?.items.map((file) => (
        <Cluster key={`${file.name}:${file.version}`} justify="between">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSelected(file)
              setLatestVersion(file.version)
            }}
          >
            {file.name} · v{file.version}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onSelect({ kind: 'event', id: file.event_id })}
          >
            Linked work
          </Button>
        </Cluster>
      ))}
      {selected && (
        <>
          <Select
            aria-label="File version"
            value={String(selected.version)}
            onChange={(e) => setSelected({ ...selected, version: Number(e.target.value) })}
            options={Array.from({ length: latestVersion }, (_, index) => ({
              value: String(index + 1),
              label: `Version ${index + 1}`,
            }))}
          />
          <Text size="sm">
            {selected.name} · Version {selected.version}
            {(content.data ?? selected).redacted ? ' · Redacted' : ''}
          </Text>
          <Text size="xs" tone="muted">
            SHA-256 {content.data?.sha256 ?? 'Loading version metadata…'}
          </Text>
          {content.initialLoading && <Loading label="Loading file version…" />}
          {!!content.error && (
            <ErrorState message={errorText(content.error)} onRetry={() => void content.refresh()} />
          )}
          {content.data && (
            <CodeBlock className="trading-code">
              {content.data.content ?? 'Content not recorded'}
            </CodeBlock>
          )}
        </>
      )}
    </Stack>
  )
}
