import { useState, type JSX } from 'react'
import { ConfirmDialog, SwipeActions, useSwipeRows } from '../../ui'
import { Pencil, Trash2 } from '../../../ui/icons'
import { useFlag } from '../../../hooks/useFlag'
import { Icons } from './icons'
import { Badge, Btn } from './primitives'
import { GroupLevelMenu, LevelSeg } from './PermissionControls'
import { usePermissionLevels } from './usePermissionLevels'
import { EndpointForm } from './RestEndpointForm'
import { READ_GROUP, WRITE_GROUP, type GroupPermissionLevel, type PermissionLevel } from './model'
import { editedEndpoint, isReadEndpoint, shownLevel } from './restApi'
import type { RestEndpoint } from './restApi'

// The endpoint list of one REST API. Each endpoint becomes a tool of its own for Lucius, so the
// list is laid out as an MCP server's tools are: the ones that only read, then the ones that
// write or delete, each with its permission on its own row and a "set them all" on the group.
// Filled by reading an OpenAPI document, typed by hand, or both — every row is editable either
// way. A saved endpoint carries its `tool` id through an edit untouched, so the tool keeps its
// name.

function newEndpoint(): RestEndpoint {
  return { method: 'GET', path: '', description: '', params: [] }
}

function summaryOf(endpoint: RestEndpoint): string {
  const count = endpoint.params.length
  const params = count ? `${count} parameter${count === 1 ? '' : 's'}` : ''
  return [endpoint.description, params].filter(Boolean).join(' · ')
}

function groupLevel(levels: PermissionLevel[]): GroupPermissionLevel {
  return levels.every((l) => l === levels[0]) ? levels[0]! : 'custom'
}

interface RowProps {
  endpoint: RestEndpoint
  level: PermissionLevel
  onLevel: (level: PermissionLevel) => void
  onEdit: () => void
  onRemove: () => void
}

/** One endpoint, drawn as an MCP server's tool is: what it is, and its permission. Edit and
 *  Remove stay out of sight until asked for — a pencil, a bin and three switch buttons on
 *  every row read as five things to press (owner, 2026-11-11). With a mouse they surface on
 *  hover; on a touch screen the row swipes left onto them, as Memory's rows do. Either way
 *  the endpoint itself opens its editor. */
function EndpointRow({ endpoint, level, onLevel, onEdit, onRemove }: RowProps): JSX.Element {
  const levels = usePermissionLevels()
  const swipes = useSwipeRows()
  const what = `${endpoint.method} ${endpoint.path}`
  return (
    <SwipeActions
      actions={[
        {
          label: 'Edit',
          ariaLabel: `Edit ${what}`,
          onClick: onEdit,
          icon: <Pencil size={20} strokeWidth={1.8} aria-hidden="true" />,
        },
        {
          label: 'Remove',
          ariaLabel: `Remove ${what}`,
          onClick: onRemove,
          tone: 'danger',
          icon: <Trash2 size={20} strokeWidth={1.8} aria-hidden="true" />,
        },
      ]}
    >
      <div role="listitem" className="conn-acct-row rest-ep-row">
        <button
          type="button"
          className="rest-ep-rowbtn"
          title="Edit"
          aria-label={`Open ${what}`}
          onClick={onEdit}
        >
          <span className={`rest-method rest-method--${endpoint.method.toLowerCase()}`}>
            {endpoint.method}
          </span>
          <span className="rest-ep-text">
            <span className="conn-acct-name rest-path" title={endpoint.path}>
              {endpoint.path || 'New endpoint'}
            </span>
            {summaryOf(endpoint) && <span className="conn-acct-sub">{summaryOf(endpoint)}</span>}
          </span>
        </button>
        {!swipes && (
          <span className="rest-ep-acts">
            <button
              type="button"
              className="rest-ep-icon"
              title="Edit"
              aria-label={`Edit ${what}`}
              onClick={onEdit}
            >
              <Pencil size={15} aria-hidden />
            </button>
            <button
              type="button"
              className="rest-ep-icon rest-ep-icon--remove"
              title="Remove"
              aria-label={`Remove ${what}`}
              onClick={onRemove}
            >
              <Trash2 size={15} aria-hidden />
            </button>
          </span>
        )}
        {levels && <LevelSeg label={what} value={level} onChange={onLevel} levels={levels} />}
      </div>
    </SwipeActions>
  )
}

export interface RestEndpointsEditorProps {
  endpoints: RestEndpoint[]
  onChange: (next: RestEndpoint[]) => void
}

export function RestEndpointsEditor({ endpoints, onChange }: RestEndpointsEditorProps) {
  const [open, setOpen] = useState<number | null>(null)
  // The endpoint a Remove is waiting on a yes for, by its place in the list.
  const [removing, setRemoving] = useState<number | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const v2 = useFlag('permissions_v2') === true
  const levels = usePermissionLevels()

  const replace = (i: number, next: RestEndpoint) =>
    onChange(endpoints.map((e, j) => (j === i ? editedEndpoint(e, next, v2) : e)))
  const setLevel = (indexes: number[], level: PermissionLevel) =>
    onChange(endpoints.map((e, j) => (indexes.includes(j) ? { ...e, permission: level } : e)))
  const remove = (i: number) => {
    onChange(endpoints.filter((_, j) => j !== i))
    setOpen(null)
    setRemoving(null)
  }
  // A row with no path yet is a slip of the Add button, not something to be asked about.
  const askRemove = (i: number) => (endpoints[i]!.path.trim() ? setRemoving(i) : remove(i))
  const leaving = removing === null ? undefined : endpoints[removing]
  const add = () => {
    onChange([...endpoints, newEndpoint()])
    setOpen(endpoints.length)
  }

  // Indexes, not copies: a row edits the one list, wherever its group puts it on screen.
  const all = endpoints.map((_, i) => i)
  const groups = [
    { name: READ_GROUP, rows: all.filter((i) => isReadEndpoint(endpoints[i]!)) },
    { name: WRITE_GROUP, rows: all.filter((i) => !isReadEndpoint(endpoints[i]!)) },
  ].filter((g) => g.rows.length > 0)

  return (
    <div className="rest-ep-list">
      {groups.map((group) => (
        <div key={group.name} className="rest-ep-group">
          <div className="rest-ep-grouphead">
            {/* Headed and folded as the permission ladder's groups are. */}
            <button
              type="button"
              className="rest-ep-groupname"
              aria-expanded={!collapsed[group.name]}
              onClick={() => setCollapsed((c) => ({ ...c, [group.name]: !c[group.name] }))}
            >
              <span
                className={'rest-ep-chev' + (collapsed[group.name] ? '' : ' rest-ep-chev--open')}
                aria-hidden
              >
                <Icons.chev />
              </span>
              {group.name} <Badge>{group.rows.length}</Badge>
            </button>
            {levels && (
              <GroupLevelMenu
                value={groupLevel(group.rows.map((i) => shownLevel(endpoints[i]!, v2)))}
                onPick={(level) => setLevel(group.rows, level)}
                levels={levels}
              />
            )}
          </div>
          <div
            className="conn-acct-group"
            role="list"
            aria-label={group.name}
            hidden={collapsed[group.name]}
          >
            {group.rows.map((i) =>
              open === i ? (
                <div key={i} role="listitem" className="rest-ep-open">
                  <EndpointForm
                    id={`rest-ep-${i}`}
                    endpoint={endpoints[i]!}
                    onChange={(next) => replace(i, next)}
                    onRemove={() => askRemove(i)}
                    onDone={() => setOpen(null)}
                  />
                </div>
              ) : (
                <EndpointRow
                  key={i}
                  endpoint={endpoints[i]!}
                  level={shownLevel(endpoints[i]!, v2)}
                  onLevel={(level) => setLevel([i], level)}
                  onEdit={() => setOpen(i)}
                  onRemove={() => askRemove(i)}
                />
              ),
            )}
          </div>
        </div>
      ))}
      <div>
        <Btn onClick={add}>
          <Icons.plus /> Add an endpoint
        </Btn>
      </div>
      <ConfirmDialog
        data={
          leaving
            ? {
                title: 'Remove this endpoint?',
                body: (
                  <>
                    <code className="rest-path">
                      {leaving.method} {leaving.path}
                    </code>{' '}
                    will stop being a tool Lucius can call once you save. An Automation that uses it
                    will report an unknown tool.
                  </>
                ),
                confirmLabel: 'Remove',
                danger: true,
              }
            : null
        }
        onConfirm={() => removing !== null && remove(removing)}
        onCancel={() => setRemoving(null)}
      />
    </div>
  )
}
