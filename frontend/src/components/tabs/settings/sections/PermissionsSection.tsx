// Settings → Permissions: every BUILT-IN tool, one value each — Always allow, Ask or Block (ADR
// `2026-10-11-permissions-are-one-value-per-tool`). Ported from
// design_handoffs/settings_handoff/prototype/redesign/settings-permissions.jsx, then reshaped:
// the list used to show only the decisions the owner had made; now every tool has a row (it is
// seeded with a sensible default — a read runs, a change or a send asks), so the list is the
// whole built-in catalogue, searchable. A connector's or an MCP server's tools are set on that
// connector's own page in the Connectors tab, not here.
//
// The mode (manual / auto / skip) only decides what Ask means. Reset puts a tool back to its
// default and is offered only when the value differs from it.
import { useState } from 'react'
import {
  LEVEL_LABELS,
  LEVEL_ORDER,
  TOOL_PERMISSIONS_KEY,
  listToolPermissions,
  resetToolPermission,
  setToolPermission,
  toolTitle,
} from '../../../../api/toolPermissions'
import type {
  PermissionLevel,
  ToolPermission,
  ToolPermissionsResponse,
} from '../../../../api/toolPermissions'
import { useCachedFetchState } from '../../../../state/queryCache'
import { Badge, Btn, Empty, Panel, Select, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'

function isLevel(level: string): level is PermissionLevel {
  return level in LEVEL_LABELS
}

/** True when the owner has moved this tool off its default — or its tool is gone, which Reset
 *  also clears. */
function isChanged(row: ToolPermission): boolean {
  return !row.tool_exists || row.level !== row.default_level
}

function matches(row: ToolPermission, needle: string): boolean {
  if (!needle) return true
  return [toolTitle(row.tool), row.tool, row.description].join(' ').toLowerCase().includes(needle)
}

function PermissionRow({
  row,
  onReset,
  onChange,
}: {
  row: ToolPermission
  onReset: (row: ToolPermission) => void
  onChange: (row: ToolPermission, level: PermissionLevel) => void
}) {
  const title = toolTitle(row.tool)
  const editable = row.tool_exists && isLevel(row.level)
  return (
    <div className="rd-permrow" role="row">
      <div className="rd-permrow-tool" title={row.tool}>
        {title}
        {row.description && row.tool_exists ? (
          <div className="rd-permrow-desc">{row.description}</div>
        ) : null}
        {/* A rename retired this tool — the decision is inert but still real, so it is
            RENDERED rather than filtered out: a permission the user set and can no longer
            see is the failure this list exists to fix. */}
        {!row.tool_exists && (
          <div className="rd-permrow-desc">No longer available — renamed or removed</div>
        )}
      </div>
      <span>
        {editable ? (
          <Select
            aria-label={`Permission for ${title}`}
            value={row.level}
            onChange={(e) => onChange(row, e.target.value as PermissionLevel)}
          >
            {LEVEL_ORDER.map((level) => (
              <option key={level} value={level}>
                {LEVEL_LABELS[level]}
              </option>
            ))}
          </Select>
        ) : (
          <Badge>{isLevel(row.level) ? LEVEL_LABELS[row.level] : row.level}</Badge>
        )}
      </span>
      <span>
        {isChanged(row) ? (
          <Btn
            size="xs"
            variant="ghost"
            onClick={() => onReset(row)}
            aria-label={`Reset ${title} to the default`}
          >
            Reset
          </Btn>
        ) : (
          <span className="rd-permrow-default">Default</span>
        )}
      </span>
    </div>
  )
}

export function PermissionsSection({ toast }: SectionProps) {
  const { data, initialLoading, refresh } = useCachedFetchState<ToolPermissionsResponse>(
    TOOL_PERMISSIONS_KEY,
    listToolPermissions,
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const [query, setQuery] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)

  // Built-in tools only — a connector's and an MCP server's tools live on their Connectors page.
  const all = (data?.permissions ?? []).filter((row) => row.connector_id === 'core')
  const changedCount = all.filter(isChanged).length
  const needle = query.trim().toLowerCase()
  const shown = all.filter((row) => matches(row, needle) && (!changedOnly || isChanged(row)))

  const reset = async (row: ToolPermission) => {
    try {
      const { level } = await resetToolPermission(row.tool, row.connector_id)
      void refresh()
      toast(
        level && isLevel(level)
          ? `${toolTitle(row.tool)}: back to ${LEVEL_LABELS[level]} — takes effect on the next call`
          : `${toolTitle(row.tool)}: removed`,
      )
    } catch {
      toast('Could not reset — try again')
    }
  }

  const change = async (row: ToolPermission, level: PermissionLevel) => {
    try {
      await setToolPermission({ tool: row.tool, level, connector_id: row.connector_id })
      void refresh()
      toast(`${toolTitle(row.tool)}: ${LEVEL_LABELS[level]} — takes effect on the next call`)
    } catch {
      toast('Could not change that — try again')
    }
  }

  return (
    <Panel
      title={<span>Tool permissions {all.length ? <Badge>{all.length}</Badge> : null}</span>}
      sub="Lucius's built-in tools, and whether each runs, asks you first, or is blocked. In Auto mode a tool set to Ask is judged by the Auto judge; in Skip mode it runs. A connector's tools are set on its page in Connectors."
      actions={
        <Btn
          size="xs"
          variant="ghost"
          onClick={() => {
            void refresh()
            toast('Refreshed')
          }}
        >
          Refresh
        </Btn>
      }
    >
      <div className="rd-permtoolbar">
        <input
          className="rd-input"
          type="search"
          value={query}
          placeholder="Search tools…"
          aria-label="Search tools"
          onChange={(e) => setQuery(e.target.value)}
        />
        <Btn
          size="xs"
          variant={changedOnly ? 'primary' : 'ghost'}
          aria-pressed={changedOnly}
          onClick={() => setChangedOnly((on) => !on)}
        >
          {changedOnly ? 'Showing changed' : `Changed (${changedCount})`}
        </Btn>
      </div>
      {shown.length ? (
        <>
          <div className="rd-permrow head" role="row">
            <span>Tool</span>
            <span>Value</span>
            <span></span>
          </div>
          {shown.map((r) => (
            <PermissionRow
              key={r.tool}
              row={r}
              onReset={(row) => void reset(row)}
              onChange={(row, level) => void change(row, level)}
            />
          ))}
        </>
      ) : (
        <Empty
          title={initialLoading ? 'Loading tools…' : 'No tools match'}
          body={
            initialLoading
              ? undefined
              : changedOnly && !query
                ? 'Nothing has been changed from its default.'
                : 'Try another word — you can search by name or what it does.'
          }
        />
      )}
    </Panel>
  )
}
