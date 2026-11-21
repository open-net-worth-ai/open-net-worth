import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { timeAgo } from '../../../../utils/format'
import { Button, Cluster, Stack, Text } from '../../../ui'
import { detailOf } from '../../imports/runs'
import type { BudgetAppInfo } from './budgetApps'

// Undo the last sync here: what it changed in this book's transactions is put back, and the sync
// is switched off. Asked twice, the second time saying what happens on each side.

export function UndoLastSync({
  app,
  at,
  onDone,
}: {
  app: BudgetAppInfo
  at?: string | null
  onDone: () => void
}) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  if (!at && !result) return null

  async function undo() {
    setBusy(true)
    try {
      const r = await authFetch('/api/actual-budget/sync/undo-last', { method: 'POST' })
      if (!r.ok) {
        setResult(await detailOf(r, 'Could not undo the last sync.'))
        return
      }
      const body = (await r.json()) as { put_back: number; skipped: string[] }
      const left = body.skipped.length
        ? ` ${body.skipped.length} changed here since stayed as they are: ${body.skipped.slice(0, 3).join(', ')}.`
        : ''
      setResult(
        `Put back ${body.put_back} transaction${body.put_back === 1 ? '' : 's'}, and switched the sync off.${left}`,
      )
      onDone()
    } catch {
      setResult('Could not reach the server.')
    } finally {
      setBusy(false)
      setAsking(false)
    }
  }

  return (
    <section className="a3-block" aria-label="Undo the last sync here">
      <div className="a3-block-h">Undo the last sync here</div>
      <Stack gap="--space-2">
        {result ? (
          <span role="status">
            <Text size="sm">{result}</Text>
          </span>
        ) : asking ? (
          <>
            <Text size="sm">
              {`Put back what the sync ${timeAgo(at!)} changed in this book's transactions, and switch the sync off. ${app.name} isn't changed now. When you turn the sync on again, what was put back here is sent to ${app.name} too; to undo ${app.name}'s side instead, use a restore point.`}
            </Text>
            <Cluster gap="--space-2">
              <Button size="sm" disabled={busy} loading={busy} onClick={() => void undo()}>
                Yes, undo it
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>
                Cancel
              </Button>
            </Cluster>
          </>
        ) : (
          <div>
            <Button size="sm" variant="secondary" onClick={() => setAsking(true)}>
              {`Undo the sync from ${timeAgo(at!)}…`}
            </Button>
          </div>
        )}
      </Stack>
    </section>
  )
}
