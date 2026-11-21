import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { timeAgo } from '../../../../utils/format'
import { Button, Stack, Text } from '../../../ui'
import type { BudgetAppInfo } from './budgetApps'

// Actual's budget as it was before a sync wrote to it: the last few, kept only for runs that
// wrote. Downloaded as the zip Actual's own Import takes, so a bad sync can be undone there.

export interface RestorePoint {
  id: string
  created_at: string
  budget: string
  bytes: number
}

async function download(app: BudgetAppInfo, point: RestorePoint): Promise<string | null> {
  try {
    const r = await authFetch(`${app.apiPrefix}/restore-points/${encodeURIComponent(point.id)}`)
    if (!r.ok) return 'That restore point couldn’t be downloaded.'
    const url = URL.createObjectURL(await r.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `actual-${point.id}.zip`
    link.click()
    URL.revokeObjectURL(url)
    return null
  } catch {
    return 'Could not reach the server.'
  }
}

export function RestorePoints({ app, points }: { app: BudgetAppInfo; points: RestorePoint[] }) {
  const [error, setError] = useState<string | null>(null)
  if (points.length === 0) return null
  return (
    <section className="a3-block" aria-label="Restore points">
      <div className="a3-block-h">Before each sync that wrote</div>
      <div className="a3-block-note">
        {`${app.name}'s budget as it was just before a sync changed it. To undo a sync, download one and, in ${app.name}, import it (Import file, then Actual). It comes back as a separate budget, so nothing is overwritten.`}
      </div>
      <Stack gap="--space-1">
        {points.map((p) => (
          <div key={p.id}>
            <Button size="sm" variant="ghost" onClick={() => void download(app, p).then(setError)}>
              {`Before the sync ${timeAgo(p.created_at)} · ${Math.max(1, Math.round(p.bytes / 1024))} KB`}
            </Button>
          </div>
        ))}
        {error && (
          <Text size="xs" tone="bad">
            {error}
          </Text>
        )}
      </Stack>
    </section>
  )
}
