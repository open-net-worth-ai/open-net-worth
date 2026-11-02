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

import { useEffect, useRef, useState, type JSX } from 'react'
import { Badge, Btn } from './primitives'
import { icon, Icons } from './icons'
import {
  LEVELS,
  TOOL_LEVELS,
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
}

export function PermissionLadder({
  groups,
  perms,
  onSetTool,
  onSetGroup,
  levels = LEVELS,
}: PermissionLadderProps): JSX.Element {
  // Every group starts EXPANDED (2026-08-18, owner request — spec §4's collapse-past-6-tools
  // default read as hiding real tool names behind an extra click). A later refresh that
  // lengthens a group still does not re-collapse it under the user, and their own toggles
  // always win — this only changes the seed, not the mechanism.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [groupMenu, setGroupMenu] = useState<string | null>(null)

  // The group menu is a popover, so a click anywhere else dismisses it — matching `Kebab`.
  // The prototype left it open until something was picked.
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!groupMenu) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Element | null
      // A mousedown inside one of THIS ladder's group controls is that control's business.
      if (target && root.current?.contains(target) && target.closest('.rd-kebab')) return
      setGroupMenu(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [groupMenu])

  return (
    <div ref={root} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map((g) => {
        const level = deriveGroupLevel(g.tools, perms)
        const GroupIcon = icon(levels[level].icon)
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
              <span className="rd-kebab">
                <Btn
                  size="xs"
                  aria-expanded={groupMenu === g.name}
                  onClick={() => setGroupMenu(groupMenu === g.name ? null : g.name)}
                  style={{ fontSize: 12.5 }}
                >
                  <GroupIcon /> {levels[level].label}{' '}
                  <span style={{ display: 'inline-flex', width: 12, height: 12 }}>
                    <Icons.chev />
                  </span>
                </Btn>
                {groupMenu === g.name && (
                  <span className="rd-kebab-pop">
                    {(Object.keys(levels) as (keyof typeof levels)[]).map((lv) => {
                      const ItemIcon = icon(levels[lv].icon)
                      return (
                        <button
                          key={lv}
                          className="rd-kebab-item"
                          style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
                          onClick={() => {
                            if (lv !== 'custom') onSetGroup(g.tools, lv)
                            setGroupMenu(null)
                          }}
                        >
                          <ItemIcon /> {levels[lv].label}
                        </button>
                      )
                    })}
                  </span>
                )}
              </span>
            </div>
            {!collapsed[g.name] && (
              <div className="cn-permlist">
                {g.tools.map((t) => (
                  <div key={t} className="cn-permrow2">
                    <span className="cn-permtool" style={{ fontSize: 13.5 }}>
                      {t}
                    </span>
                    <span className="cn-permseg">
                      {TOOL_LEVELS.map((lv) => {
                        const LevelIcon = icon(levels[lv].icon)
                        const on = toolPermission(perms, t) === lv
                        return (
                          <button
                            key={lv}
                            className={'cn-permbtn' + (on ? ` on ${levels[lv].tone}` : '')}
                            title={levels[lv].label}
                            aria-label={`${t}: ${levels[lv].label}`}
                            aria-pressed={on}
                            onClick={() => onSetTool(t, lv)}
                          >
                            <LevelIcon />
                          </button>
                        )
                      })}
                    </span>
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
