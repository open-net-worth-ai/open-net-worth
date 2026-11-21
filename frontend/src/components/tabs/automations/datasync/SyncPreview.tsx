import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { Button, Stack, Text } from '../../../ui'
import { detailOf } from '../../imports/runs'
import type { BudgetAppInfo } from './budgetApps'

// A dry run of the next sync: the app is read and every transaction and month's Assigned decided
// by the run's own rules, and nothing is written on either side. Each line says what would change
// and where, with a few of the rows by date and payee.

export interface PreviewResult {
  transactions: { counts: Record<string, number>; samples: Record<string, string[]> }
  assigned?: { counts: Record<string, number> }
  categories_left_out?: boolean
}

function txnLine(kind: string, n: number, app: string): string {
  const t = `${n} transaction${n === 1 ? '' : 's'}`
  switch (kind) {
    case 'add_there':
      return `${t} new here would be added in ${app}`
    case 'change_there':
      return `${t} changed here would be changed in ${app}`
    case 'delete_there':
      return `${t} deleted here would be deleted in ${app}`
    case 'add_here':
      return `${t} new in ${app} would be added here`
    case 'change_here':
      return `${t} changed in ${app} would be changed here`
    case 'delete_here':
      return `${t} deleted in ${app} would be deleted here`
    case 'merge':
      return `${t} changed on both sides, in different fields, would keep both changes`
    case 'review':
      return `${t} would wait in Needs your review`
    case 'held':
      return `${t} would wait for the other direction: the sync runs one way`
    default:
      return `${t}: ${kind}`
  }
}

export function usePreview(app: BudgetAppInfo) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<PreviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  async function run() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await authFetch(`${app.apiPrefix}/sync/preview`, { method: 'POST' })
      if (!r.ok) setError(await detailOf(r, 'Could not preview the next sync.'))
      else setResult((await r.json()) as PreviewResult)
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }
  return { busy, result, error, run, close: () => (setResult(null), setError(null)) }
}

export function SyncPreviewPanel({
  app,
  preview,
}: {
  app: BudgetAppInfo
  preview: ReturnType<typeof usePreview>
}) {
  const { result, error } = preview
  if (!result && !error) return null
  const tx = Object.entries(result?.transactions.counts ?? {}).filter(([, n]) => n > 0)
  const assigned = result?.assigned?.counts ?? {}
  const nothing = result && tx.length === 0 && Object.keys(assigned).length === 0
  return (
    <section className="a3-block" aria-label="Preview of the next sync">
      <div className="a3-block-h">Preview of the next sync</div>
      <div className="a3-block-note">Nothing has been written. Sync now does this.</div>
      <Stack gap="--space-2">
        {error && (
          <Text size="sm" tone="bad">
            {error}
          </Text>
        )}
        {nothing && <Text size="sm">Nothing would change on either side.</Text>}
        {tx.map(([kind, n]) => (
          <Stack key={kind} gap="--space-1">
            <Text size="sm">{txnLine(kind, n, app.name)}</Text>
            {(result?.transactions.samples[kind] ?? []).map((s) => (
              <Text key={s} size="xs" tone="faint">
                {s}
              </Text>
            ))}
          </Stack>
        ))}
        {!!assigned.change_there && (
          <Text size="sm">{`${assigned.change_there} month's Assigned would be sent to ${app.name}`}</Text>
        )}
        {!!assigned.change_here && (
          <Text size="sm">{`${assigned.change_here} month's Assigned would come from ${app.name}`}</Text>
        )}
        {!!assigned.review && (
          <Text size="sm">{`${assigned.review} Assigned figure(s) would wait in Needs your review`}</Text>
        )}
        {result?.categories_left_out && (
          <Text size="xs" tone="faint">
            Category changes (new, renamed, hidden) aren&apos;t previewed: the run shows them in its
            report.
          </Text>
        )}
        <div>
          <Button size="sm" variant="ghost" onClick={preview.close}>
            Close preview
          </Button>
        </div>
      </Stack>
    </section>
  )
}
