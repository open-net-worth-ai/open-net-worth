import { Badge, Button, Cluster, Grid, Stack, Text } from '../../ui'
import { YNAB_APP, type BudgetAppInfo } from '../automations/datasync/budgetApps'
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

function Differences({
  name,
  onw,
  ynab,
}: {
  name: string
  onw: Version | null
  ynab: Version | null
}) {
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
                {`${name}:`}{' '}
              </Text>
              {gone ? (ynab === null ? 'Deleted' : context(ynab)) : shown(key, ynab)}
            </Text>
          </Grid>
        </div>
      ))}
    </Stack>
  )
}

const badgeOf = (item: Item, name: string) =>
  item.kind === 'conflict' && item.fresh
    ? 'Differs'
    : item.kind === 'unsupported'
      ? `${name} can't take it`
      : KIND_LABEL[item.kind]

const KIND_LABEL: Record<Exclude<Item['kind'], 'unsupported'>, string> = {
  conflict: 'Changed in both',
  failed: 'Refused',
  before_start: 'Before the Starting Balance',
  choose: 'Needs your choice',
}

type Keep = (keep: 'onw' | 'ynab', choice?: string) => void

// A category or a group: what differs is a few words, and a category YNAB has that could go
// with several things here asks which. Each button says what it does.
function NameCard(props: { name: string; item: Item; busy: boolean; onKeep: Keep }) {
  const { name, item, busy, onKeep } = props
  const asking = item.kind === 'choose' && item.entity === 'new_category'
  const ours = item.category !== undefined // a category here YNAB would not take
  return (
    <Stack gap="--space-2">
      <Cluster gap="--space-2" align="center">
        <Text size="sm" weight={600}>
          {item.title}
        </Text>
        <Badge tone={item.kind === 'failed' ? 'neg' : 'warn'}>{badgeOf(item, name)}</Badge>
      </Cluster>
      <Text size="sm" tone={item.kind === 'failed' ? 'bad' : 'faint'}>
        {item.reason}
      </Text>
      {(item.fields ?? []).map((f) => (
        <Grid key={f.label} min={200} gap="--space-3">
          <Text size="sm">
            <Text size="xs" tone="faint">
              Open Net Worth {f.label.toLowerCase()}:{' '}
            </Text>
            {f.onw}
          </Text>
          <Text size="sm">
            <Text size="xs" tone="faint">
              {`${name} `}
              {f.label.toLowerCase()}:{' '}
            </Text>
            {f.ynab}
          </Text>
        </Grid>
      ))}
      <Cluster gap="--space-2">
        {asking ? (
          <>
            {(item.options ?? []).map((o) => (
              <Button key={o.ref} size="sm" disabled={busy} onClick={() => onKeep('onw', o.ref)}>
                {o.ref.startsWith('env:') ? `Add it to ${o.name}` : `Use ${o.name}`}
              </Button>
            ))}
            <Button size="sm" disabled={busy} onClick={() => onKeep('ynab')}>
              Add as a new category
            </Button>
          </>
        ) : item.entity === 'budget' ? (
          <>
            <Button size="sm" disabled={busy} onClick={() => onKeep('ynab')}>
              Give it an envelope
            </Button>
            <Button size="sm" disabled={busy} onClick={() => onKeep('onw')}>
              Leave it without one
            </Button>
          </>
        ) : item.entity === 'new_category' ? (
          <>
            <Button size="sm" disabled={busy} onClick={() => onKeep(ours ? 'onw' : 'ynab')}>
              Try again
            </Button>
            <Button size="sm" disabled={busy} onClick={() => onKeep(ours ? 'ynab' : 'onw')}>
              {ours ? `Leave it out of ${name}` : 'Leave it out'}
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy} onClick={() => onKeep('onw')}>
              Keep Open Net Worth&apos;s
            </Button>
            <Button size="sm" disabled={busy} onClick={() => onKeep('ynab')}>
              {`Keep ${name}'s`}
            </Button>
          </>
        )}
      </Cluster>
    </Stack>
  )
}

// Moving a Starting Balance is never automatic: the card states both outcomes and the button
// names the move it makes.
function StartCard(props: { item: Item; busy: boolean; onKeep: Keep }) {
  const { item } = props
  const cur = item.currency ? ` ${item.currency}` : ''
  return (
    <Stack gap="--space-2">
      <Cluster gap="--space-2" align="center">
        <Text size="sm" weight={600}>
          {item.title}
        </Text>
        <Badge tone="warn">{KIND_LABEL.before_start}</Badge>
      </Cluster>
      <Text size="sm" tone="faint">
        {item.reason}
      </Text>
      <Grid min={200} gap="--space-3">
        <Text size="sm">
          <Text size="xs" tone="faint">
            Starting Balance date:{' '}
          </Text>
          {item.start} → {item.earliest}
        </Text>
        <Text size="sm">
          <Text size="xs" tone="faint">
            Balance:{' '}
          </Text>
          {money(item.balance_now)}
          {cur} → {money(item.balance_counted)}
          {cur}
        </Text>
      </Grid>
      <Cluster gap="--space-2">
        <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('ynab')}>
          Move the Starting Balance to {item.earliest}
        </Button>
        <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('onw')}>
          Keep as history
        </Button>
      </Cluster>
    </Stack>
  )
}

export function ReviewCard({
  app = YNAB_APP,
  ...props
}: {
  app?: BudgetAppInfo
  item: Item
  busy: boolean
  onKeep: Keep
}) {
  const { item } = props
  const name = app.name
  if (item.entity === 'start') return <StartCard {...props} />
  if (['category', 'group', 'new_category', 'budget'].includes(item.entity ?? ''))
    return <NameCard name={name} {...props} />
  return (
    <Stack gap="--space-2">
      <Cluster gap="--space-2" align="center">
        <Text size="sm" weight={600}>
          {item.title}
        </Text>
        <Badge tone={item.kind === 'conflict' ? 'warn' : 'neg'}>{badgeOf(item, name)}</Badge>
      </Cluster>
      <Text
        size="sm"
        weight={item.kind === 'conflict' ? undefined : 600}
        tone={item.kind === 'conflict' ? 'faint' : item.kind === 'failed' ? 'bad' : 'warn'}
      >
        {item.reason}
      </Text>
      <Differences name={name} onw={item.onw} ynab={item.ynab} />
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
          {`Keep ${name}'s`}
        </Button>
      </Cluster>
    </Stack>
  )
}
