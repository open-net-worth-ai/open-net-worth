// ActivityPanel — the Lucius landing screen's Activity panel (below the greeting, inside
// ChatPanel's `isHomeEmpty` block — see ChatPanel.tsx, which renders it as a sibling right
// after the greeting, ahead of the composer/footer). Needs attention (transactions to review,
// automations whose latest run failed — shown on the phone too), then Active (runs
// running/waiting or just finished, across every automation) and Scheduled (automations with
// an upcoming next_run_at). Renders NOTHING when all are empty — this is a landing-screen
// accent, not a dashboard with its own empty state.

import { useEffect, useState } from 'react'
import { Badge, Button, Text } from '../../ui'
import { authFetch } from '../../../api/client'
import type {
  ActiveActivityItem,
  FailedActivityItem,
  ScheduledActivityItem,
} from '../../../api/automations'
import { useAutomationActivity } from '../../../hooks/useAutomationActivity'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { fScheduleWhen, timeAgo } from '../../../utils/format'
import { AlertCircle, Ban, Bell, Check, ChevronRight, Clock } from '../../../ui/icons'
import './activity-panel.css'

/** How many rows show before "Show more" — mirrors Inbox.tsx's SECTION_CAP convention. The
 *  server already caps the fetch at 20; this only caps what's shown before expanding. */
const SECTION_CAP = 5

interface ActivityPanelProps {
  /** Navigate to the Automations tab and open this automation's run detail (or, for a
   *  Scheduled row with no run yet, just its detail). Threaded down from ChatPanel, which
   *  already owns the identical navigation for a run's header (`openRun`). `sessionId` (Active
   *  rows only — a Scheduled automation has no run yet) also switches the Lucius panel to that
   *  run's own conversation, instead of leaving whatever empty draft was showing. */
  onOpenAutomation: (automationId: string, runId?: string, sessionId?: string) => void
  /** Opens the shared transaction Review mode without starting a Lucius turn. */
  onOpenTransactionReview?: () => void
  /** Mobile keeps automation activity hidden, but still shows Needs attention — ledger work
   *  to review and failed automations. */
  showAutomationSections?: boolean
}

export function ActivityPanel({
  onOpenAutomation,
  onOpenTransactionReview,
  showAutomationSections = true,
}: ActivityPanelProps) {
  const { data } = useAutomationActivity()
  const failed = data?.failed ?? []
  // A failed run is listed once, under Needs attention — not again under Active.
  const failedRuns = new Set(failed.map((item) => item.run_id))
  const active = showAutomationSections
    ? (data?.active ?? []).filter((item) => !failedRuns.has(item.run_id))
    : []
  const scheduled = showAutomationSections ? (data?.scheduled ?? []) : []
  const [reviewTotal, setReviewTotal] = useState(0)
  const ledgerVersion = useDataVersion('ledger')
  useEffect(() => {
    let activeRequest = true
    void authFetch('/api/transactions/recently-added?needs_review=true&limit=1')
      .then(async (response) => {
        if (!response.ok) return
        const body = (await response.json()) as { total?: number }
        if (activeRequest) setReviewTotal(body.total ?? 0)
      })
      .catch(() => undefined)
    return () => {
      activeRequest = false
    }
  }, [ledgerVersion])
  const review = reviewTotal > 0 && onOpenTransactionReview ? reviewTotal : 0
  if (review === 0 && failed.length === 0 && active.length === 0 && scheduled.length === 0) {
    return null
  }

  return (
    <div className="lx-automation-activity">
      <NeedsAttentionSection
        reviewTotal={review}
        onOpenReview={onOpenTransactionReview}
        failed={failed}
        onOpenRun={onOpenAutomation}
      />
      <ActiveSection items={active} onOpen={onOpenAutomation} />
      <ScheduledSection items={scheduled} onOpen={onOpenAutomation} />
    </div>
  )
}

function NeedsAttentionSection({
  reviewTotal,
  onOpenReview,
  failed,
  onOpenRun,
}: {
  reviewTotal: number
  onOpenReview?: () => void
  failed: FailedActivityItem[]
  onOpenRun: (automationId: string, runId?: string, sessionId?: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const count = (reviewTotal > 0 ? 1 : 0) + failed.length
  if (count === 0) return null
  const room = SECTION_CAP - (reviewTotal > 0 ? 1 : 0)
  const visible = expanded ? failed : failed.slice(0, room)
  const overflow = failed.length - room
  return (
    <section className="lx-activity-section">
      <div className="lx-activity-section-head">
        <Text as="span" size="xs" weight={600} tone="muted" className="lx-activity-section-title">
          Needs attention
        </Text>
        <Badge tone="neutral">{count}</Badge>
      </div>
      <div className="lx-activity-list">
        {reviewTotal > 0 && onOpenReview && <ReviewRow total={reviewTotal} onOpen={onOpenReview} />}
        {visible.map((item) => (
          <FailedRow key={item.run_id} item={item} onOpen={onOpenRun} />
        ))}
      </div>
      {overflow > 0 && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((e) => !e)}
          className="lx-activity-show-more"
        >
          {expanded ? 'Show less' : `Show ${overflow} more`}
        </Button>
      )}
    </section>
  )
}

/** An automation whose latest run failed. Opens the run's own conversation, where the error
 *  and "Try again" are; opening it marks it read, which clears it here. It also leaves once a
 *  later run or that retry succeeds, or after 24 hours (the server's window). */
function FailedRow({
  item,
  onOpen,
}: {
  item: FailedActivityItem
  onOpen: (automationId: string, runId?: string, sessionId?: string) => void
}) {
  const open = () => onOpen(item.automation_id, item.run_id, item.session_id ?? undefined)
  const why = item.error || "The run didn't finish."
  return (
    <div className="lx-activity-row lx-activity-row--action-only">
      <span
        className="lx-automation-activity-dot lx-automation-activity-dot--failed"
        aria-hidden="true"
      >
        <AlertCircle size={12} strokeWidth={1.8} />
      </span>
      <span className="lx-activity-row-body">
        <Text as="span" size="sm" weight={600} truncate title={item.automation_name}>
          {`${item.automation_name} failed`}
        </Text>
        <Text as="span" size="xs" tone="muted">
          {`${why} • ${timeAgo(item.finished_at ?? item.started_at)}`}
        </Text>
      </span>
      <span className="lx-activity-row-action">
        <Button
          variant="secondary"
          size="sm"
          aria-label={`Open the failed run of ${item.automation_name}`}
          onClick={open}
        >
          Open
        </Button>
      </span>
    </div>
  )
}

function ReviewRow({ total, onOpen }: { total: number; onOpen: () => void }) {
  const label = `${total} transaction${total === 1 ? '' : 's'} to review`
  return (
    <div className="lx-activity-row lx-activity-row--action-only">
      <span
        className="lx-automation-activity-dot lx-automation-activity-dot--waiting"
        aria-hidden="true"
      >
        <AlertCircle size={14} strokeWidth={1.8} />
      </span>
      <span className="lx-activity-row-body">
        <Text as="span" size="sm" weight={600}>
          {label}
        </Text>
        <Text as="span" size="xs" tone="muted">
          Check what was added while you were away
        </Text>
      </span>
      <span className="lx-activity-row-action">
        <Button variant="secondary" size="sm" aria-label="Review transactions" onClick={onOpen}>
          Review
        </Button>
      </span>
    </div>
  )
}

function ActiveSection({
  items,
  onOpen,
}: {
  items: ActiveActivityItem[]
  onOpen: (automationId: string, runId?: string, sessionId?: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return null
  const visible = expanded ? items : items.slice(0, SECTION_CAP)
  const overflow = items.length - SECTION_CAP

  return (
    <section className="lx-activity-section">
      <div className="lx-activity-section-head">
        <Text as="span" size="xs" weight={600} tone="muted" className="lx-activity-section-title">
          Active
        </Text>
        <Badge tone="neutral">{items.length}</Badge>
      </div>
      <div className="lx-activity-list">
        {visible.map((item) => (
          <ActiveRow key={item.run_id} item={item} onOpen={onOpen} />
        ))}
      </div>
      {overflow > 0 && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((e) => !e)}
          className="lx-activity-show-more"
        >
          {expanded ? 'Show less' : `Show ${overflow} more`}
        </Button>
      )}
    </section>
  )
}

/** Label + relative-time subtitle, and the dot/icon, per Active status. `completed`/`failed`
 *  use `finished_at` for the relative time (that's the moment the status this label names
 *  actually happened) — `started_at` for the still-in-flight statuses, which have no
 *  `finished_at` yet. */
const ACTIVE_STATUS_META: Record<
  ActiveActivityItem['status'],
  { label: string; dotClass: string }
> = {
  waiting: { label: 'Permissions needed', dotClass: 'waiting' },
  running: { label: 'Running', dotClass: 'running' },
  completed: { label: 'Completed', dotClass: 'completed' },
  failed: { label: 'Failed', dotClass: 'failed' },
  // "Stopped", not "Cancelled" — matches AutomationHistory.tsx's own established label for
  // this exact status (owner report: showing "Cancelled" here and "Stopped" there for the
  // same run read as two different things happening). Covers both a deliberate user Stop and
  // a run cut short by a server restart/shutdown; the backend doesn't distinguish the two at
  // the status level (see ActiveActivityItem's own comment, api/automations.ts), so neither
  // does this label — same reasoning as AutomationHistory's.
  cancelled: { label: 'Stopped', dotClass: 'cancelled' },
}

function ActiveRow({
  item,
  onOpen,
}: {
  item: ActiveActivityItem
  onOpen: (automationId: string, runId?: string, sessionId?: string) => void
}) {
  const waiting = item.status === 'waiting'
  const meta = ACTIVE_STATUS_META[item.status]
  const when = item.finished_at ?? item.started_at
  const subtitle = `${meta.label} • ${timeAgo(when)}`
  const open = () => onOpen(item.automation_id, item.run_id, item.session_id ?? undefined)
  return (
    <div
      className="lx-activity-row"
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter') open()
      }}
    >
      <span
        className={`lx-automation-activity-dot lx-automation-activity-dot--${meta.dotClass}`}
        aria-hidden="true"
      >
        {item.status === 'waiting' && <Bell size={12} strokeWidth={1.8} />}
        {item.status === 'running' && <span className="lx-automation-activity-dot-pulse" />}
        {item.status === 'completed' && <Check size={12} strokeWidth={2} />}
        {item.status === 'failed' && <AlertCircle size={12} strokeWidth={1.8} />}
        {item.status === 'cancelled' && <Ban size={12} strokeWidth={1.8} />}
      </span>
      <span className="lx-activity-row-body">
        <Text as="span" size="sm" weight={600} truncate title={item.automation_name}>
          {item.automation_name}
        </Text>
        <Text as="span" size="xs" tone="muted">
          {subtitle}
        </Text>
      </span>
      {waiting ? (
        <span className="lx-activity-row-action" onClick={(e) => e.stopPropagation()}>
          <Button variant="secondary" size="sm" onClick={open}>
            Review
          </Button>
        </span>
      ) : (
        <ChevronRight
          size={16}
          strokeWidth={1.8}
          className="lx-activity-row-chevron"
          aria-hidden="true"
        />
      )}
    </div>
  )
}

function ScheduledSection({
  items,
  onOpen,
}: {
  items: ScheduledActivityItem[]
  onOpen: (automationId: string, runId?: string, sessionId?: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return null
  const visible = expanded ? items : items.slice(0, SECTION_CAP)
  const overflow = items.length - SECTION_CAP

  return (
    <section className="lx-activity-section">
      <div className="lx-activity-section-head">
        <Text as="span" size="xs" weight={600} tone="muted" className="lx-activity-section-title">
          Scheduled
        </Text>
        <Badge tone="neutral">{items.length}</Badge>
      </div>
      <div className="lx-activity-list">
        {visible.map((item) => (
          <div
            key={item.automation_id}
            className="lx-activity-row"
            role="button"
            tabIndex={0}
            onClick={() => onOpen(item.automation_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onOpen(item.automation_id)
            }}
          >
            <span
              className="lx-automation-activity-dot lx-automation-activity-dot--scheduled"
              aria-hidden="true"
            >
              <Clock size={12} strokeWidth={1.8} />
            </span>
            <span className="lx-activity-row-body">
              <Text as="span" size="sm" weight={600} truncate title={item.automation_name}>
                {item.automation_name}
              </Text>
              <Text as="span" size="xs" tone="muted">
                {fScheduleWhen(item.next_run_at)}
              </Text>
            </span>
            <ChevronRight
              size={16}
              strokeWidth={1.8}
              className="lx-activity-row-chevron"
              aria-hidden="true"
            />
          </div>
        ))}
      </div>
      {overflow > 0 && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((e) => !e)}
          className="lx-activity-show-more"
        >
          {expanded ? 'Show less' : `Show ${overflow} more`}
        </Button>
      )}
    </section>
  )
}
