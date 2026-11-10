// The permission ladder (spec §4) — the SAME instrument for a channel, a bank feed, a
// web provider and a third-party MCP server.
//
// The prototype carries this markup twice, byte-identical: once inside `Detail` and once
// as `McpPermGrid` for the add flow's step 4. Both screen lanes need it, so it is
// promoted to one primitive here (CLAUDE.md: "a layout pattern used twice gets promoted")
// — which also guarantees the detail page and the add flow can never drift apart.
//
// CONTROLLED: this component owns no permission state. The caller supplies `perms` and
// persists the change — the detail page against the saved config, the MCP add flow
// against a local draft that is only written when the server is created.

import { useState, type JSX } from 'react'
import { Badge } from './primitives'
import { Icons } from './icons'
import { GroupLevelMenu, LevelSeg } from './PermissionControls'
import {
  LEVELS,
  deriveGroupLevel,
  toolPermission,
  type PermissionLevel,
  type PermissionMap,
  type ToolGroup,
} from './model'

export interface PermissionLadderProps {
  groups: ToolGroup[]
  perms: PermissionMap
  /** Override one tool. */
  onSetTool: (tool: string, level: PermissionLevel) => void
  /** Stamp every tool in the group — deliberately lossy (spec §4). Never called for
   *  `custom`, which is inert: it exists in the menu for legibility only. */
  onSetGroup: (tools: string[], level: PermissionLevel) => void
  /** How each level is named and drawn. */
  levels?: typeof LEVELS
  /** What a tool is called on its row, when that is not its full name. A REST API's tools are
   *  `rest__<api>__<tool>`, and on that API's own page the first two parts say nothing. The
   *  full name stays the key, and shows on hover. */
  toolLabel?: (tool: string) => string
  /** A second line under a tool's name, where one helps: what a REST tool calls. */
  toolSub?: (tool: string) => string | undefined
}

export function PermissionLadder({
  groups,
  perms,
  onSetTool,
  onSetGroup,
  levels = LEVELS,
  toolLabel,
  toolSub,
}: PermissionLadderProps): JSX.Element {
  // Every group starts EXPANDED (2026-08-18, owner request — spec §4's collapse-past-6-tools
  // default read as hiding real tool names behind an extra click). A later refresh that
  // lengthens a group still does not re-collapse it under the user, and their own toggles
  // always win — this only changes the seed, not the mechanism.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map((g) => {
        const level = deriveGroupLevel(g.tools, perms)
        return (
          <div key={g.name}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                padding: '4px 2px 8px',
              }}
            >
              <span
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 13.5,
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
                onClick={() => setCollapsed((p) => ({ ...p, [g.name]: !p[g.name] }))}
              >
                <span
                  style={{
                    display: 'inline-flex',
                    color: 'var(--faint)',
                    transform: collapsed[g.name] ? 'rotate(0)' : 'rotate(90deg)',
                    transition: 'transform .15s',
                  }}
                >
                  <Icons.chev />
                </span>
                {g.name}
                <Badge>{g.tools.length}</Badge>
              </span>
              <GroupLevelMenu
                value={level}
                onPick={(lv) => onSetGroup(g.tools, lv)}
                levels={levels}
              />
            </div>
            {!collapsed[g.name] && (
              <div className="cn-permlist">
                {g.tools.map((t) => (
                  <div key={t} className="cn-permrow2">
                    <span
                      className="cn-permtool"
                      style={{ fontSize: 13.5 }}
                      title={toolLabel ? t : undefined}
                    >
                      {toolLabel ? toolLabel(t) : t}
                      {toolSub?.(t) ? <span className="cn-permsub">{toolSub(t)}</span> : null}
                    </span>
                    <LevelSeg
                      label={toolLabel ? toolLabel(t) : t}
                      value={toolPermission(perms, t)}
                      onChange={(lv) => onSetTool(t, lv)}
                      levels={levels}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
