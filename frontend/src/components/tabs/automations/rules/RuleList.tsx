// The Rules, in the order they run. A row reorders by dragging its handle, or from the keyboard:
// focus the handle and press the up or down arrow (Move up / Move down in the menu does the same).
import { useState } from 'react'
import { GripVertical } from 'lucide-react'
import type { Automation } from '../../../../api/automations'
import { Badge, Switch } from '../../../ui'
import { timeAgo } from '../../../../utils/format'
import { AutomationMenu } from '../AutomationMenu'

function matchSummary(rule: Automation): string {
  const count = Number(rule.stats.match_count ?? 0)
  const last = typeof rule.stats.last_matched_at === 'string' ? rule.stats.last_matched_at : null
  if (count === 0) return 'Not matched yet'
  const when = last ? timeAgo(last) : ''
  return `${count.toLocaleString()} ${count === 1 ? 'match' : 'matches'}${when ? ` · last ${when}` : ''}`
}

export function RuleList({
  rules,
  reorderable,
  busyId,
  onMove,
  onToggle,
  onEdit,
  onDelete,
}: {
  rules: Automation[]
  /** False while a filter is on: a partial list cannot be reordered. */
  reorderable: boolean
  busyId: string | null
  /** Moves the Rule with this id to `index` of the FULL order. */
  onMove: (id: string, toIndex: number) => void
  onToggle: (rule: Automation) => void
  onEdit: (rule: Automation) => void
  onDelete: (rule: Automation) => void
}) {
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const finishDrag = () => {
    setDragId(null)
    setOverId(null)
  }
  return (
    <ol className="rl-list" aria-label="Rules, in the order they run">
      {rules.map((rule, index) => {
        const paused = rule.status === 'paused'
        const stop = rule.execution.stop === true
        return (
          <li
            key={rule.id}
            className={`rl-item${paused ? ' off' : ''}${dragId === rule.id ? ' dragging' : ''}${overId === rule.id && dragId !== rule.id ? ' over' : ''}`}
            data-od-id={`rule-row-${rule.id}`}
            onDragOver={(event) => {
              if (!dragId) return
              event.preventDefault()
              setOverId(rule.id)
            }}
            onDrop={(event) => {
              event.preventDefault()
              if (dragId && dragId !== rule.id) onMove(dragId, index)
              finishDrag()
            }}
          >
            <button
              type="button"
              className="rl-grip"
              draggable={reorderable}
              disabled={!reorderable}
              aria-label={`Reorder ${rule.name}. Use the up and down arrow keys.`}
              title={
                reorderable
                  ? 'Drag to change the order rules run in'
                  : 'Clear the search to change the order'
              }
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'move'
                event.dataTransfer.setData('text/plain', rule.id)
                setDragId(rule.id)
              }}
              onDragEnd={finishDrag}
              onKeyDown={(event) => {
                if (event.key === 'ArrowUp' && index > 0) {
                  event.preventDefault()
                  onMove(rule.id, index - 1)
                } else if (event.key === 'ArrowDown' && index < rules.length - 1) {
                  event.preventDefault()
                  onMove(rule.id, index + 1)
                }
              }}
            >
              <GripVertical size={15} aria-hidden="true" />
            </button>
            <div className="rl-body">
              <div className="rl-name">
                <span className="rl-name-t">{rule.name}</span>
                {paused && <Badge tone="warn">Paused</Badge>}
                {stop && <Badge>Stops here</Badge>}
              </div>
              <p className="rl-sentence">{rule.instructions}</p>
              <span className="a3-muted rl-stats">{matchSummary(rule)}</span>
            </div>
            <Switch
              dense
              aria-label={`${rule.name} is on`}
              checked={!paused}
              disabled={busyId === rule.id}
              onChange={() => onToggle(rule)}
            />
            <AutomationMenu
              label={`More actions for ${rule.name}`}
              items={[
                { label: 'Edit', onSelect: () => onEdit(rule) },
                ...(reorderable
                  ? [
                      {
                        label: 'Move up',
                        disabled: index === 0,
                        onSelect: () => onMove(rule.id, index - 1),
                      },
                      {
                        label: 'Move down',
                        disabled: index === rules.length - 1,
                        onSelect: () => onMove(rule.id, index + 1),
                      },
                    ]
                  : []),
                { label: 'Delete', danger: true, onSelect: () => onDelete(rule) },
              ]}
            />
          </li>
        )
      })}
    </ol>
  )
}
