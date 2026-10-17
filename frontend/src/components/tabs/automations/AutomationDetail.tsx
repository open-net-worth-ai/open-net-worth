import { useEffect, useRef, useState } from 'react'
import {
  Check,
  Clock3,
  FastForward,
  Globe,
  Hand,
  Pencil,
  Play,
  Square,
  Trash2,
  TriangleAlert,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import type { Automation, AutomationRun } from '../../../api/automations'
import { Button, Modal, PageTabs, Switch, type PermissionMode } from '../../ui'
import { fDateTime } from '../../../utils/format'
import { AutomationCostPanel } from './AutomationCostPanel'
import { AutomationHistory } from './AutomationHistory'
import { AutomationMenu } from './AutomationMenu'
import { AutomationRunCard } from './AutomationRunCard'
import { automationTriggerLabel, type AutomationOptions } from './AutomationTriggerSummary'

export function AutomationDetail({
  automation,
  runs,
  options,
  selectedRunId,
  busy = false,
  onSelectRun,
  onCloseRun,
  onEdit,
  onDelete,
  onRunNow,
  onStopRun,
  onStatusChange,
  onRevoke,
  onBack,
}: {
  automation: Automation
  runs: AutomationRun[]
  options?: AutomationOptions
  selectedRunId: string | null
  busy?: boolean
  onSelectRun: (run: AutomationRun) => void
  onCloseRun: () => void
  onEdit: () => void
  onDelete: () => void
  onRunNow: () => void
  onStopRun: (run: AutomationRun) => void
  onStatusChange: (status: 'active' | 'paused') => void
  onRevoke: (index: number) => void
  onBack?: () => void
}) {
  const layoutRef = useRef<HTMLDivElement>(null)
  const [compact, setCompact] = useState(false)
  const [tab, setTab] = useState<'setup' | 'history'>('setup')
  const [showAll, setShowAll] = useState(false)
  const selectedRun = selectedRunId ? (runs.find((run) => run.id === selectedRunId) ?? null) : null
  const runningRun = runs.find((run) => run.status === 'running') ?? null

  useEffect(() => {
    const element = layoutRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setCompact(entry.contentRect.width < 720)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (compact && selectedRunId) setTab('history')
  }, [compact, selectedRunId])

  const paused = automation.status === 'paused'
  const finished = automation.status === 'completed'
  const longInstructions = automation.instructions.length > 260
  const TriggerIcon = automation.trigger.kind === 'event' ? Zap : Clock3
  return (
    <div className="a3-wrap" data-od-id="automation-detail">
      {onBack && (
        <nav className="a3-crumb" aria-label="Breadcrumb">
          <button type="button" onClick={onBack}>
            Automations
          </button>
          <span aria-hidden="true">/</span>
          <span className="cur" aria-current="page">
            {automation.name}
          </span>
        </nav>
      )}
      <div className={`a3-detail-head${compact ? ' compact' : ''}`}>
        <div className="a3-detail-title">
          <h1>{automation.name}</h1>
          <div className="a3-detail-sub">
            {automation.description || firstSentence(automation.instructions)}
          </div>
        </div>
        <div className="a3-detail-bar">
          <div className="a3-detail-state">
            {finished ? (
              <span className="a3-tag">Finished</span>
            ) : (
              <Switch
                id={`automation-status-${automation.id}`}
                checked={!paused}
                disabled={busy}
                onChange={() => onStatusChange(paused ? 'active' : 'paused')}
                aria-label={`${paused ? 'Resume' : 'Pause'} ${automation.name}`}
              />
            )}
            <span>
              {finished
                ? 'Its schedule has run out. Edit the trigger to run it again.'
                : paused
                  ? 'Resume it to put it back on watch.'
                  : automation.next_run_at
                    ? `Next run: ${fDateTime(automation.next_run_at)}`
                    : automation.trigger.kind === 'event'
                      ? 'Runs whenever this happens'
                      : 'Runs only when you start it.'}
            </span>
          </div>
          <div className="a3-detail-actions">
            {runningRun ? (
              <Button
                size="sm"
                variant="secondary"
                iconLeft={<Square size={12} aria-hidden="true" />}
                onClick={() => onStopRun(runningRun)}
              >
                Stop
              </Button>
            ) : (
              <Button
                size="sm"
                variant="primary"
                loading={busy}
                iconLeft={<Play size={12} aria-hidden="true" />}
                onClick={onRunNow}
              >
                Run now
              </Button>
            )}
            <AutomationMenu
              label="More actions"
              items={[
                {
                  label: 'Edit automation',
                  icon: (
                    <span className="a3-menu-ic">
                      <Pencil size={14} aria-hidden="true" />
                    </span>
                  ),
                  onSelect: onEdit,
                },
                {
                  label: 'Delete automation',
                  danger: true,
                  icon: (
                    <span className="a3-menu-ic">
                      <Trash2 size={14} aria-hidden="true" />
                    </span>
                  ),
                  onSelect: onDelete,
                },
              ]}
            />
          </div>
        </div>
      </div>
      <div
        ref={layoutRef}
        className={`a3-cols${compact ? ' stacked' : ''}`}
        data-tab={compact ? tab : 'both'}
      >
        {compact && (
          <div className="a3-subtabs">
            <PageTabs
              ariaLabel="Automation sections"
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'setup', label: 'Setup' },
                { key: 'history', label: 'History', badge: runs.length },
              ]}
            />
          </div>
        )}
        <div className="a3-col-setup">
          <DetailSection title="Instructions" odId="instructions">
            <div
              className={`a3-instr${longInstructions ? ' clamped clickable' : ''}`}
              role={longInstructions ? 'button' : undefined}
              tabIndex={longInstructions ? 0 : undefined}
              onClick={() => longInstructions && setShowAll(true)}
              onKeyDown={(event) => {
                if (longInstructions && event.key === 'Enter') setShowAll(true)
              }}
            >
              {automation.instructions}
            </div>
            {longInstructions && (
              <button type="button" className="a3-linkbtn" onClick={() => setShowAll(true)}>
                Show all
              </button>
            )}
          </DetailSection>
          <DetailSection title="Trigger" odId="trigger">
            <div className="a3-trigger-detail">
              <TriggerIcon size={14} aria-hidden="true" />
              {automationTriggerLabel(automation.trigger, options)}
            </div>
            <div className="a3-block-note">
              {finished
                ? 'Finished — no runs left on this schedule.'
                : paused
                  ? 'Paused — nothing will fire it.'
                  : automation.trigger.kind === 'event' && automation.trigger.limit !== 'each'
                    ? 'A burst inside the window collapses into one run.'
                    : null}
            </div>
          </DetailSection>
          <DetailSection title="Model" odId="model">
            <div className="a3-model-detail">
              <Zap size={14} aria-hidden="true" />
              <b>{automation.model || 'Default'}</b>
              <span>•</span>
              <span>{effortLabel(automation.effort)} effort</span>
            </div>
          </DetailSection>
          <DetailSection title="Permission Mode" odId="permission-mode">
            {(() => {
              const mode = permissionModeInfo(automation.permission_mode ?? 'auto')
              const ModeIcon = mode.Icon
              return (
                <div className="a3-model-detail">
                  <ModeIcon size={14} aria-hidden="true" />
                  <b>{mode.short}</b>
                  <span>•</span>
                  <span>{mode.description}</span>
                </div>
              )
            })()}
          </DetailSection>
          {(automation.fetch_hosts ?? []).length > 0 && (
            <DetailSection title="Websites it may open" odId="fetch-hosts">
              <div className="a3-model-detail">
                <Globe size={14} aria-hidden="true" />
                <span>{(automation.fetch_hosts ?? []).join(', ')}</span>
              </div>
            </DetailSection>
          )}
          <DetailSection title="Cost & usage" odId="cost-and-usage">
            <AutomationCostPanel automation={automation} runs={runs} />
          </DetailSection>
          <DetailSection
            title={
              <>
                Always allowed <Check size={13} aria-hidden="true" />
              </>
            }
            odId="always-allowed"
          >
            {(automation.always_allowed ?? []).length > 0 && (
              <div className="a3-allow">
                {/* ONE ROW PER TOOL. A grant entry can carry many tools, and collapsing them
                    onto its ``desc`` ("Tools available to the '…' Automation") told the owner
                    nothing about what was actually authorised — this list is the only place
                    those names are visible. Revoke still targets the whole entry, so the row
                    says which entry it belongs to. */}
                {(automation.always_allowed ?? []).flatMap((permission, index) =>
                  (permission.tools?.length ? permission.tools : [null]).map((tool) => (
                    <div
                      className={`a3-allow-row${permission.src === 'Open Net Worth' ? ' internal' : ''}`}
                      key={`${permission.src}-${index}-${tool ?? permission.desc}`}
                    >
                      <span className="mark" style={permissionMarkStyle(permission.src)}>
                        {permission.src === 'Open Net Worth' ? 'ONW' : initials(permission.src)}
                      </span>
                      <b>{permission.src}</b>
                      <span className="d">{tool ?? permission.desc}</span>
                      <button
                        type="button"
                        className="a3-iconbtn danger revoke"
                        aria-label={`Revoke ${permission.src}: ${tool ?? permission.desc}`}
                        title="Revoke"
                        disabled={busy}
                        onClick={() => onRevoke(index)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  )),
                )}
              </div>
            )}
            <div className="a3-block-note">Approvals you grant during a run appear here.</div>
          </DetailSection>
        </div>
        <div className="a3-block a3-col-history" data-od-id="run-history">
          <DetailSection
            title="History"
            meta={`${runs.length} ${runs.length === 1 ? 'run' : 'runs'}`}
            bare
          >
            <AutomationHistory
              runs={runs}
              selectedRunId={selectedRunId}
              onSelectRun={onSelectRun}
              emptyMessage={emptyHistoryMessage(automation)}
            />
          </DetailSection>
          {selectedRun && <AutomationRunCard run={selectedRun} onClose={onCloseRun} />}
          {runs.length > 0 && (
            <div className="a3-history-note">
              Open any run to read what it did — and keep talking to it.
            </div>
          )}
        </div>
      </div>
      <Modal open={showAll} size="xl" title={automation.name} onClose={() => setShowAll(false)}>
        <div className="a3-modal-kicker">Instructions</div>
        <div className="a3-modal-instructions">{automation.instructions}</div>
      </Modal>
    </div>
  )
}

function DetailSection({
  title,
  meta,
  children,
  odId,
  bare = false,
}: {
  title: React.ReactNode
  meta?: string
  children: React.ReactNode
  odId?: string
  bare?: boolean
}) {
  return (
    <section className={bare ? '' : 'a3-block'} data-od-id={odId}>
      <div className="a3-block-h">
        {title}
        {meta && <span className="count">{meta}</span>}
      </div>
      {children}
    </section>
  )
}

function effortLabel(value: string) {
  return value ? value[0]!.toUpperCase() + value.slice(1) : 'Medium'
}

// Mirrors PermissionModeControl's own icon-per-mode mapping (ui/) — same three-way choice,
// read-only here since this screen doesn't own editing.
const PERMISSION_MODE_INFO: Record<
  PermissionMode,
  { short: string; description: string; Icon: LucideIcon }
> = {
  manual: {
    short: 'Manual',
    description: 'Lucius pauses so you can approve each action.',
    Icon: Hand,
  },
  auto: {
    short: 'Auto',
    description: 'Lucius runs on its own and pauses to ask if anything looks unsafe.',
    Icon: FastForward,
  },
  skip: {
    short: 'Skip',
    description: 'Lucius never pauses, even for unsafe actions.',
    Icon: TriangleAlert,
  },
}
function permissionModeInfo(mode: PermissionMode) {
  return PERMISSION_MODE_INFO[mode]
}
function initials(value: string) {
  return value
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}
function firstSentence(value: string) {
  return (value.trim().split(/(?<=[.!?])\s|\n/)[0] ?? '').trim()
}
function emptyHistoryMessage(automation: Automation) {
  if (automation.status === 'completed')
    return 'No runs kept. Its schedule has run out — edit the trigger to run it again.'
  if (automation.trigger.kind === 'event')
    return 'Hasn’t fired yet — it runs when the trigger happens, or hit Run now.'
  if (automation.trigger.kind === 'schedule')
    return `Hasn’t run yet. It first runs ${automation.next_run_at ? fDateTime(automation.next_run_at) : automationTriggerLabel(automation.trigger)} — or hit Run now.`
  return 'Hasn’t run yet. Hit Run now to start it.'
}
function permissionMarkStyle(source: string): React.CSSProperties {
  const color =
    source === 'Open Net Worth'
      ? 'var(--accent)'
      : source.toLowerCase().includes('telegram')
        ? '#2aabee'
        : source.toLowerCase().includes('gmail')
          ? '#ea4335'
          : 'var(--lucius)'
  return {
    color,
    background: `color-mix(in srgb, ${color} 16%, transparent)`,
    borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
  }
}
