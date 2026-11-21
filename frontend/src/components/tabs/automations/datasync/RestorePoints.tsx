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

async function download(point: RestorePoint): Promise<string | null> {
  try {
    // Actual only: YNAB keeps no copy of a plan, so there is no such route for it.
    const r = await authFetch(`/api/actual-budget/restore-points/${encodeURIComponent(point.id)}`)
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

export function RestorePoints({
  app,
  points,
  unavailable = false,
}: {
  app: BudgetAppInfo
  points: RestorePoint[]
  unavailable?: boolean
}) {
  const [error, setError] = useState<string | null>(null)
  if (unavailable)
    return (
      <section className="a3-block" aria-label="Restore points">
        <div className="a3-block-h">No undo kept for this budget</div>
        <div className="a3-block-note">
          {`Your ${app.name} budget is encrypted, so this app keeps no copy of it from before a sync: its copy here is decrypted. Before a sync writes to ${app.name}, export the budget there (Settings, then Export data).`}
        </div>
      </section>
    )
  if (points.length === 0) return null
  return (
    <section className="a3-block" aria-label="Restore points">
      <div className="a3-block-h">Before each sync that wrote</div>
      <div className="a3-block-note">
        {`${app.name}'s budget as it was just before a sync changed it; the last five are kept. To undo a sync, download one and, in ${app.name}, import it (Import file, then Actual). It comes back as a separate budget, so nothing is overwritten.`}
      </div>
      <Stack gap="--space-1">
        {points.map((p) => (
          <div key={p.id}>
            <Button size="sm" variant="ghost" onClick={() => void download(p).then(setError)}>
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
