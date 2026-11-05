import { Clock3, Zap } from 'lucide-react'
import type { Automation } from '../../../api/automations'
import { AutomationMenu } from './AutomationMenu'
import {
  automationCustomRule,
  automationTriggerLabel,
  type AutomationOptions,
} from './AutomationTriggerSummary'

export function AutomationCard({
  automation,
  options,
  busy,
  onOpen,
  onRunNow,
  onToggle,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  automation: Automation
  options?: AutomationOptions
  busy?: boolean
  onOpen: () => void
  onRunNow: () => void
  onToggle: () => void
  onEdit: () => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const paused = automation.status === 'paused'
  const finished = automation.status === 'completed'
  const description =
    automation.description.trim() || firstSentence(automation.instructions) || 'No description yet.'
  const mailStarter = paused && automation.provenance.system_key === 'mail_ledger'
  const TriggerIcon = automation.trigger.kind === 'event' ? Zap : Clock3
  // Lucius entity wiring: `automation` is a registered mention type, but this card never
  // emitted the attributes, so an automation could be @-mentioned and nothing else — not
  // dragged into the dock, not right-clicked "Ask Lucius". DragRightClick binds globally on
  // [data-entity-id][draggable], so the attributes below ARE the wiring.
  return (
    <article
      className={`a3-row${paused || finished ? ' off' : ''}`}
      aria-label={automation.name}
      data-od-id={`automation-row-${automation.id}`}
      data-entity-id={`automation:${automation.id}`}
      data-entity-label={automation.name}
      draggable
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <span className="a3-row-body">
        <span className="a3-row-title">
          <span className="t">{automation.name}</span>
          {paused && <span className="a3-tag warn">Paused</span>}
          {finished && <span className="a3-tag">Finished</span>}
        </span>
        <span className="a3-row-blurb">
          {mailStarter
            ? 'Starter only. Connect Email and review its settings before enabling.'
            : description}
        </span>
        <span
          className={`a3-when${paused || finished ? ' off' : ''}${automation.trigger.kind === 'event' ? ' evt' : ''}`}
          title={automationCustomRule(automation.trigger) ?? undefined}
        >
          <TriggerIcon size={13} strokeWidth={1.7} aria-hidden="true" />
          {automationTriggerLabel(automation.trigger, options)}
        </span>
      </span>
      <span className="a3-row-right" onClick={(event) => event.stopPropagation()}>
        <AutomationMenu
          items={[
            { label: 'Run now', disabled: busy, onSelect: onRunNow },
            ...(finished
              ? []
              : [{ label: paused ? 'Resume' : 'Pause', disabled: busy, onSelect: onToggle }]),
            { label: finished ? 'Edit to run again' : 'Edit', onSelect: onEdit },
            { label: 'Duplicate', onSelect: onDuplicate },
            { label: 'Delete', danger: true, onSelect: onDelete },
          ]}
        />
      </span>
    </article>
  )
}

function firstSentence(value: string) {
  return (value.trim().split(/(?<=[.!?])\s|\n/)[0] ?? '').trim()
}
