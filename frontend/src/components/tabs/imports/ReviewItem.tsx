import { Badge, Button, Cluster, Grid, Stack, Text } from '../../ui'
import type { ReviewItem as Item, Version } from './sync'

// One thing the sync would not apply on its own: both versions side by side, and the choice of
// which one wins on BOTH sides.

const money = (v: number | null | undefined) =>
  v == null
    ? ''
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const FIELDS: { key: keyof Version; label: string }[] = [
  { key: 'amount', label: 'Amount' },
  { key: 'date', label: 'Date' },
  { key: 'account', label: 'Account' },
  { key: 'payee', label: 'Payee' },
  { key: 'category', label: 'Category' },
  { key: 'memo', label: 'Memo' },
  { key: 'cleared', label: 'Cleared' },
]

function shown(key: keyof Version, v: Version | null): string {
  if (v === null) return 'Deleted'
  const value = v[key]
  if (value == null || value === '') return '—'
  if (key === 'amount') return money(value as number)
  if (key === 'cleared') return value ? 'Cleared' : 'Uncleared'
  return String(value)
}

/** The fields the two sides disagree on, in reading order; a side that is gone differs on all. */
function differences(onw: Version | null, ynab: Version | null) {
  if (onw === null || ynab === null) return [{ key: 'amount' as const, label: 'Transaction' }]
  return FIELDS.filter(
    ({ key }) => onw[key] !== ynab[key] && (onw[key] ?? '') !== (ynab[key] ?? ''),
  )
}

function context(v: Version | null): string {
  if (!v) return ''
  return [v.date, v.account, v.payee, v.amount != null ? money(v.amount) : null]
    .filter(Boolean)
    .join(' · ')
}

function Differences({ onw, ynab }: { onw: Version | null; ynab: Version | null }) {
  const rows = differences(onw, ynab)
  if (rows.length === 0) return null
  const gone = onw === null || ynab === null
  return (
    <Stack gap="--space-2">
      {rows.map(({ key, label }) => (
        <div key={label}>
          <Text size="xs" weight={600}>
            {label}
          </Text>
          <Grid min={200} gap="--space-3">
            <Text size="sm">
              <Text size="xs" tone="faint">
                Open Net Worth:{' '}
              </Text>
              {gone ? (onw === null ? 'Deleted' : context(onw)) : shown(key, onw)}
            </Text>
            <Text size="sm">
              <Text size="xs" tone="faint">
                YNAB:{' '}
              </Text>
              {gone ? (ynab === null ? 'Deleted' : context(ynab)) : shown(key, ynab)}
            </Text>
          </Grid>
        </div>
      ))}
    </Stack>
  )
}

const KIND_LABEL: Record<Item['kind'], string> = {
  conflict: 'Changed in both',
  unsupported: "YNAB can't take it",
  failed: 'Refused',
}

export function ReviewCard(props: {
  item: Item
  busy: boolean
  onKeep: (keep: 'onw' | 'ynab') => void
}) {
  const { item } = props
  return (
    <Stack gap="--space-2">
      <Cluster gap="--space-2" align="center">
        <Text size="sm" weight={600}>
          {item.title}
        </Text>
        <Badge tone={item.kind === 'conflict' ? 'warn' : 'neg'}>{KIND_LABEL[item.kind]}</Badge>
      </Cluster>
      <Text
        size="sm"
        weight={item.kind === 'conflict' ? undefined : 600}
        tone={item.kind === 'conflict' ? 'faint' : item.kind === 'failed' ? 'bad' : 'warn'}
      >
        {item.reason}
      </Text>
      <Differences onw={item.onw} ynab={item.ynab} />
      <Text size="xs" tone="faint">
        {context(item.onw ?? item.ynab)}
      </Text>
      <Cluster gap="--space-2">
        {(item.kind !== 'unsupported' || item.ynab === null) && (
          <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('onw')}>
            Keep Open Net Worth&apos;s
          </Button>
        )}
        <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('ynab')}>
          Keep YNAB&apos;s
        </Button>
      </Cluster>
    </Stack>
  )
}
