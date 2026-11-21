import { useState } from 'react'
import { Button, Cluster, Text } from '../../ui'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import type { ReviewItem } from './sync'

// One answer for many transactions: after a budget move or a long one-way stretch the review list
// can hold dozens of rows that all want the same side kept. Only plain transaction conflicts qualify
// (a refused write needs its own look):
// a category or group asks its own question, and an account's Starting Balance its own. The
// answer is asked twice, the second time saying how many rows change and where.

export function bulkItems(items: ReviewItem[]) {
  return items.filter((i) => (i.entity ?? 'txn') === 'txn' && i.kind === 'conflict')
}

type Keep = 'onw' | 'ynab'

export function BulkReview(props: {
  app: BudgetAppInfo
  items: ReviewItem[]
  busy: boolean
  onKeepAll: (keep: Keep, ids: string[]) => Promise<void>
}) {
  const { app, busy } = props
  const items = bulkItems(props.items)
  const [asking, setAsking] = useState<Keep | null>(null)
  if (items.length < 2) return null
  const n = items.length
  // Rows the kept side no longer has: keeping it deletes them on the other side.
  const deletes = asking
    ? items.filter((i) => (asking === 'onw' ? i.onw : i.ynab) === null).length
    : 0
  const where = asking === 'onw' ? app.name : 'this book'
  return (
    <Cluster gap="--space-2" align="center">
      {asking ? (
        <>
          <Text size="sm">
            {`Keep ${asking === 'onw' ? 'Open Net Worth' : app.name}'s version of all ${n} transactions? Each one is changed in ${where} to match${deletes ? `, and ${deletes} ${deletes === 1 ? 'is' : 'are'} deleted there, as the kept side no longer has ${deletes === 1 ? 'it' : 'them'}` : ''}.`}
          </Text>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              const keep = asking
              setAsking(null)
              void props.onKeepAll(
                keep,
                items.map((i) => i.id),
              )
            }}
          >
            {`Yes, all ${n}`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>
            Cancel
          </Button>
        </>
      ) : (
        <>
          <Text size="sm" tone="faint">{`${n} transactions:`}</Text>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setAsking('onw')}>
            Keep all Open Net Worth&apos;s
          </Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setAsking('ynab')}>
            {`Keep all ${app.name}'s`}
          </Button>
        </>
      )}
    </Cluster>
  )
}
