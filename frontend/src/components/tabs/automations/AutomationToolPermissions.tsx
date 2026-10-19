import { useEffect, useRef, useState } from 'react'
import { Plus } from 'lucide-react'
import type { Automation } from '../../../api/automations'
import {
  LEVEL_LABELS,
  TOOL_PERMISSIONS_KEY,
  listToolPermissions,
  ownerLabel,
} from '../../../api/toolPermissions'
import type { PermissionLevel, ToolPermissionsResponse } from '../../../api/toolPermissions'
import { useCachedFetchState } from '../../../state/queryCache'
import { Select } from '../../ui'
import {
  listedName,
  listedTitle,
  removeEntry,
  removeTool,
  setTool,
  toolRows,
} from './toolPermissions'
import type { AutomationToolValue, ToolLists } from './toolPermissions'

const VALUE_OPTIONS = [
  { value: 'always_allow', label: LEVEL_LABELS.always_allow },
  { value: 'blocked', label: LEVEL_LABELS.blocked },
]

/** The most picker rows drawn at once — the catalogue is a few hundred tools, so narrow it by
 *  typing rather than render them all. */
const PICKER_LIMIT = 80

/** One Automation's own say over individual tools, on top of the global setting: **Always
 *  allow** a tool here (it never asks in this Automation) or **Block** it here (it is refused,
 *  and the run carries on without it). Everything not listed follows the tool's global value;
 *  a tool set to Ask is handled by this Automation's mode. The tool list is the same one
 *  Settings → Permissions shows. */
export function AutomationToolPermissions({
  automation,
  busy,
  modeLabel,
  onSave,
}: {
  automation: Automation
  busy: boolean
  modeLabel: string
  onSave: (lists: ToolLists) => void
}) {
  const { data } = useCachedFetchState<ToolPermissionsResponse>(
    TOOL_PERMISSIONS_KEY,
    listToolPermissions,
    { staleTime: 0 },
  )
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const pickerRef = useRef<HTMLDivElement>(null)
  const lists: ToolLists = {
    always_allowed: automation.always_allowed ?? [],
    blocked: automation.blocked ?? [],
  }
  const rows = toolRows(lists)
  const listed = new Set(rows.flatMap((row) => (row.tool ? [row.tool] : [])))

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const needle = query.trim().toLowerCase()
  const candidates = (data?.permissions ?? []).filter(
    (row) =>
      row.tool_exists &&
      !listed.has(listedName(row)) &&
      (!needle ||
        [listedTitle(listedName(row)), row.tool, row.description, ownerLabel(row.connector_id)]
          .join(' ')
          .toLowerCase()
          .includes(needle)),
  )
  const globalLabel = (tool: string) => {
    const level = data?.permissions.find((row) => listedName(row) === tool)?.level
    return level && level in LEVEL_LABELS ? LEVEL_LABELS[level as PermissionLevel] : null
  }

  const choose = (tool: string, value: AutomationToolValue) => {
    onSave(setTool(lists, tool, value))
    setOpen(false)
    setQuery('')
  }

  return (
    <>
      {rows.length > 0 && (
        <div className="a3-perm" role="table" aria-label="Tool permissions in this Automation">
          <div className="a3-perm-row head" role="row">
            <span>Tool</span>
            <span>In this Automation</span>
            <span />
          </div>
          {rows.map((row) => {
            const title = row.tool ? listedTitle(row.tool) : row.label
            return (
              <div
                className="a3-perm-row"
                role="row"
                key={`${row.value}:${row.label}:${row.entryIndex ?? ''}`}
              >
                <span className="a3-perm-name" title={row.tool ?? undefined}>
                  {title}
                </span>
                {row.tool ? (
                  <Select
                    dense
                    aria-label={`Permission for ${title} in this Automation`}
                    value={row.value}
                    disabled={busy}
                    options={VALUE_OPTIONS}
                    onChange={(event) =>
                      onSave(setTool(lists, row.tool!, event.target.value as AutomationToolValue))
                    }
                  />
                ) : (
                  <span className="a3-perm-fixed">{LEVEL_LABELS.always_allow}</span>
                )}
                <button
                  type="button"
                  className="a3-linkbtn danger"
                  aria-label={`Remove ${title}`}
                  disabled={busy}
                  onClick={() =>
                    onSave(
                      row.tool
                        ? removeTool(lists, row.tool)
                        : removeEntry(lists, row.entryIndex ?? 0),
                    )
                  }
                >
                  Remove
                </button>
              </div>
            )
          })}
        </div>
      )}
      <div className="a3-perm-add" ref={pickerRef}>
        <button
          type="button"
          className="a3-linkbtn"
          aria-expanded={open}
          disabled={busy}
          onClick={() => setOpen((on) => !on)}
        >
          <Plus size={12} aria-hidden="true" /> Add tool
        </button>
        {open && (
          <div className="a3-perm-picker" role="dialog" aria-label="Add a tool">
            <input
              type="search"
              autoFocus
              value={query}
              placeholder="Search every tool…"
              aria-label="Search tools"
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="a3-perm-list">
              {candidates.slice(0, PICKER_LIMIT).map((row) => (
                <div className="a3-perm-cand" key={`${row.connector_id}:${row.tool}`}>
                  <span className="a3-perm-cand-main">
                    <b title={listedName(row)}>{listedTitle(listedName(row))}</b>
                    <span className="a3-perm-cand-sub">
                      {ownerLabel(row.connector_id)}
                      {globalLabel(listedName(row))
                        ? ` · Globally: ${globalLabel(listedName(row))}`
                        : ''}
                      {row.description ? ` — ${row.description}` : ''}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="a3-perm-pick"
                    aria-label={`Always allow ${listedTitle(listedName(row))} in this Automation`}
                    onClick={() => choose(listedName(row), 'always_allow')}
                  >
                    {LEVEL_LABELS.always_allow}
                  </button>
                  <button
                    type="button"
                    className="a3-perm-pick danger"
                    aria-label={`Block ${listedTitle(listedName(row))} in this Automation`}
                    onClick={() => choose(listedName(row), 'blocked')}
                  >
                    {LEVEL_LABELS.blocked}
                  </button>
                </div>
              ))}
              {candidates.length === 0 && (
                <div className="a3-block-note">{data ? 'No tool matches.' : 'Loading tools…'}</div>
              )}
              {candidates.length > PICKER_LIMIT && (
                <div className="a3-block-note">
                  {candidates.length - PICKER_LIMIT} more — keep typing to narrow the list.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="a3-block-note">Everything else: global setting · {modeLabel} mode</div>
    </>
  )
}
