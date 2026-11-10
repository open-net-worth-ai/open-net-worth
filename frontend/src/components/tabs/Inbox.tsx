import { useState } from 'react'
import {
  Badge,
  Button,
  Cluster,
  Container,
  EmptyState,
  ErrorState,
  PageHeader,
  Panel,
  SegmentedControl,
  Stack,
  Text,
  TabSkeleton,
} from '../ui'
import type { BadgeTone } from '../ui'
import { useInbox, type InboxItem } from '../../hooks/useInbox'
import { DeduplicationSection } from './dedup/DeduplicationSection'
import { useRegisterScreenFacts } from '../../lucius/useScreenContract'
import { entityRefProps } from '../../utils/mentionable'
import { buildCtxFields } from '../../lucius/ctxFields'
import { filterByDescriptor } from '../../lucius/entityRegistry'
import { X } from '../../ui/icons'

// ── Display helpers ───────────────────────────────────────────────────────────

function relative(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`
  return `${Math.round(mins / 1440)}d ago`
}

type ExtendedKind = InboxItem['kind'] | 'dedup_review'

// Per-kind visual identity: section title, badge tone, and glyph shown in the header.
const KIND_META: Record<
  ExtendedKind,
  { label: string; sectionTitle: string; tone: BadgeTone; icon: string }
> = {
  dedup_review: {
    label: 'Duplicate review',
    sectionTitle: 'Duplicate review',
    tone: 'warn',
    icon: '⟺',
  },
  intel: { label: 'Intel', sectionTitle: 'Intel', tone: 'warn', icon: '◎' },
  signal: { label: 'Signal', sectionTitle: 'Signals', tone: 'neutral', icon: '⬆' },
}

/** Human label for the destination tab an item opens. */
const TAB_LABEL: Record<string, string> = {
  intel: 'Intel',
}

/** Kinds rendered in display order — actionable kinds first. */
const KIND_ORDER: ExtendedKind[] = ['dedup_review', 'intel', 'signal']

/** How many rows to show before "Show more" collapses the rest. */
const SECTION_CAP = 5

/** An item is actionable if it sits in the API's top band. */
function isActionable(item: InboxItem): boolean {
  if (item.kind === 'intel' && item.status === 'action_needed') return true
  return false
}

type Filter = 'unread' | 'all' | 'pending_approvals'

const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'unread', label: 'Unread' },
  { value: 'all', label: 'All' },
  { value: 'pending_approvals', label: 'Pending Approvals' },
]

/** An item needs approval if it has a proposed_change waiting for a human to apply.
 *  STUB: hard-coded false, so the `pending_approvals` filter matches nothing today. Left as-is
 *  rather than implemented from the docstring — which items qualify is a product call. */
function needsApproval(_item: InboxItem): boolean {
  return false
}

// ── Per-kind section ─────────────────────────────────────────────────────────

/** One grouped section (e.g. "Findings [3]") rendered as a Panel with a count
 * badge and an optional "Show more / Show less" toggle. Items beyond SECTION_CAP
 * are hidden until expanded. Empty sections render nothing (not even a header). */
function KindSection({
  kind,
  items,
  onNavigate,
  onDismiss,
}: {
  kind: InboxItem['kind']
  items: InboxItem[]
  onNavigate: (tab: string) => void
  onDismiss: (item: InboxItem) => void
}) {
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return null

  const meta = KIND_META[kind]
  const visible = expanded ? items : items.slice(0, SECTION_CAP)
  const overflow = items.length - SECTION_CAP
  const hasMore = overflow > 0

  const sectionBadge = <Badge tone={meta.tone}>{items.length}</Badge>

  return (
    <Panel
      title={
        <span className="inbox-section__title">
          <span className="inbox-section__icon" aria-hidden="true">
            {meta.icon}
          </span>
          {meta.sectionTitle}
        </span>
      }
      badge={sectionBadge}
      className="inbox-section"
    >
      <Stack gap="--space-3">
        {visible.map((item) => (
          <InboxRow key={item.id} item={item} onNavigate={onNavigate} onDismiss={onDismiss} />
        ))}
        {hasMore && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded((e) => !e)}
            className="inbox-section__show-more"
          >
            {expanded ? 'Show less' : `Show ${overflow} more`}
          </Button>
        )}
      </Stack>
    </Panel>
  )
}

// ── Tab ───────────────────────────────────────────────────────────────────────

export function Inbox({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [filter, setFilter] = useState<Filter>('unread')
  const { items, loading, error, reload, dismiss, dismissAll, markAllRead } = useInbox(
    filter === 'all',
  )

  useRegisterScreenFacts(() => [
    ['Inbox items', String(allItems.length)],
    ['Unread', String(unreadCount)],
  ])

  if (loading)
    return (
      <Container size="wide">
        <TabSkeleton variant="inbox" label="Loading inbox…" />
      </Container>
    )
  if (error)
    return (
      <Container size="wide">
        <ErrorState message={error} onRetry={reload} />
      </Container>
    )

  const allItems = items ?? []
  const list = filter === 'pending_approvals' ? allItems.filter(needsApproval) : allItems
  const dismissableCount = list.length
  const unreadCount = allItems.filter((it) => !it.read).length

  // Group items by kind, preserving server order within each group.
  const byKind: Record<ExtendedKind, InboxItem[]> = {
    dedup_review: [],
    intel: [],
    signal: [],
  }
  for (const item of list) {
    byKind[item.kind].push(item)
  }

  // dedup_review is always rendered (manages its own data); count it when not suppressed
  const dedupVisible = filter !== 'pending_approvals'
  const totalSections =
    (dedupVisible ? 1 : 0) +
    KIND_ORDER.filter((k) => k !== 'dedup_review' && byKind[k].length > 0).length

  return (
    <Container size="wide" data-ctx-region="content" data-ctx-title="Inbox">
      <Stack gap="--space-6">
        <PageHeader
          title="Inbox"
          subtitle="Everything that needs your attention, in one place"
          actions={
            <Cluster gap="--space-3" align="center">
              {unreadCount > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void markAllRead()}
                  title="Mark all unread items as read"
                >
                  Mark all read
                </Button>
              )}
              {dismissableCount > 0 && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    if (
                      confirm(
                        `Dismiss all ${dismissableCount} item${dismissableCount === 1 ? '' : 's'}? This removes intel alerts and notifications — not your transactions or portfolio data.`,
                      )
                    )
                      void dismissAll()
                  }}
                >
                  Dismiss all ({dismissableCount})
                </Button>
              )}
              <SegmentedControl
                options={FILTER_OPTIONS}
                value={filter}
                onChange={setFilter}
                ariaLabel="Filter inbox items"
              />
            </Cluster>
          }
        />

        {/* totalSections === 0 implies !dedupVisible (the dedup section counts as 1 when
            visible), so this empty state only ever renders under the pending-approvals
            filter — the unread/all filters always render the dedup section instead. */}
        {totalSections === 0 && !dedupVisible ? (
          <EmptyState
            title="No pending approvals"
            body="Nothing is waiting for your approval right now."
          />
        ) : (
          <Stack gap="--space-5">
            {KIND_ORDER.map((kind) => {
              if (kind === 'dedup_review') {
                return <DeduplicationSection key="dedup_review" filter={filter} />
              }
              return (
                <KindSection
                  key={kind}
                  kind={kind as InboxItem['kind']}
                  items={byKind[kind]}
                  onNavigate={onNavigate}
                  onDismiss={dismiss}
                />
              )
            })}
          </Stack>
        )}
      </Stack>
    </Container>
  )
}

// ── Item row ────────────────────────────────────────────────────────────────

// What the × does, per kind — shown as the button's tooltip so the action is honest.
const DISMISS_TITLE: Record<InboxItem['kind'], string> = {
  signal: 'Dismiss notification',
  intel: 'Remove this alert from the inbox',
}

function InboxRow({
  item,
  onNavigate,
  onDismiss,
}: {
  item: InboxItem
  onNavigate: (tab: string) => void
  onDismiss: (item: InboxItem) => void
}) {
  const meta = KIND_META[item.kind]
  const actionable = isActionable(item)
  const destTab = item.ref.tab
  const destLabel = TAB_LABEL[destTab] ?? destTab

  // Every row is clearable from the inbox (signal → read, intel → delete;
  // see useInbox.dismissRequest). A row with a source tab also gets an
  // "Open in …" link to act there; the × just clears it from this list.
  const hasDest = Boolean(destTab)
  const when = relative(item.created_at)

  // Only `signal` rows are a registered mention type: they come from NotificationLogStore
  // (GET /api/inbox folds several sources; the dismiss route for this kind is
  // /api/inbox/signals/{id}) which is exactly what the `notification` descriptor reads
  // (descriptors/new_types.py, order=17). `intel` rows have no registered type, so they
  // stay unwired rather than emitting an unresolvable ref.
  const notificationProps =
    item.kind === 'signal'
      ? {
          ...entityRefProps(
            'notification',
            item.id,
            item.title,
            `${item.title} ${item.summary ?? ''}`,
            meta.label,
          ),
          'data-ctx': '',
          'data-ctx-type': 'notification',
          'data-ctx-fields': buildCtxFields(
            filterByDescriptor('notification', {
              title: item.title,
              summary: item.summary,
              created_at: item.created_at,
            }),
          ),
          draggable: true,
        }
      : {}

  return (
    <Panel
      className={`inbox-item${actionable ? ' inbox-item--actionable' : ''}`}
      {...notificationProps}
    >
      <Stack gap="--space-3">
        <Cluster justify="between" align="start" gap="--space-3">
          <Stack gap="--space-2">
            <Cluster gap="--space-2" align="center">
              <Badge tone={meta.tone}>{meta.label}</Badge>
              {actionable && <Badge tone="neg">Needs attention</Badge>}
            </Cluster>
            <Text as="h3" size="md" weight={600} className="inbox-item__title">
              {item.title}
            </Text>
          </Stack>
          <Cluster gap="--space-2" align="center">
            {hasDest && (
              <Button variant="secondary" size="sm" onClick={() => onNavigate(destTab)}>
                Open in {destLabel}
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onDismiss(item)}
              aria-label={DISMISS_TITLE[item.kind]}
              title={DISMISS_TITLE[item.kind]}
            >
              <X size={14} aria-hidden="true" />
            </Button>
          </Cluster>
        </Cluster>

        {item.summary && (
          <Text tone="muted" size="sm" className="inbox-item__summary">
            {item.summary}
          </Text>
        )}

        <Cluster gap="--space-2" align="center">
          {item.source && (
            <Text tone="faint" size="xs">
              {item.source}
            </Text>
          )}
          {item.source && when && (
            <Text tone="faint" size="xs">
              •
            </Text>
          )}
          {when && (
            <Text tone="faint" size="xs">
              {when}
            </Text>
          )}
        </Cluster>
      </Stack>
    </Panel>
  )
}
