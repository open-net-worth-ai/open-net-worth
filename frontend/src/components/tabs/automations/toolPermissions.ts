// Pure edits of an Automation's two tool lists — "Always allowed" (`always_allowed`, entries of
// `{src, desc, capability?, tools?}`) and "Blocked" (`blocked`, plain tool names). The Tool
// permissions section and a run card's Always allow both go through these, so a tool can
// never end up on both lists (the server refuses that too).
import type { AutomationPermission } from '../../../api/automations'

const MCP_ROW_PREFIX = 'mcp:'
const MCP_NAME_PREFIX = 'mcp__'

/** The name an Automation's lists use for a permission row: the registry name, or the namespaced
 *  `mcp__<server>__<tool>` an external MCP tool is called by (the row's own `tool` is the bare
 *  name the server advertised, owned by `mcp:<server>`). */
export function listedName(row: { tool: string; connector_id: string }): string {
  return row.connector_id.startsWith(MCP_ROW_PREFIX)
    ? `${MCP_NAME_PREFIX}${row.connector_id.slice(MCP_ROW_PREFIX.length)}__${row.tool}`
    : row.tool
}

/** A listed name in words: "Delete transactions", or "Transfer · MCP drive" for an external
 *  tool. */
export function listedTitle(name: string): string {
  const words = (value: string) => {
    const spaced = value.replace(/_/g, ' ')
    return spaced ? spaced[0]!.toUpperCase() + spaced.slice(1) : spaced
  }
  if (!name.startsWith(MCP_NAME_PREFIX)) return words(name)
  const [server, ...tool] = name.slice(MCP_NAME_PREFIX.length).split('__')
  return tool.length ? `${words(tool.join('__'))} · MCP ${server}` : words(name)
}

export type AutomationToolValue = 'always_allow' | 'blocked'

export interface ToolLists {
  always_allowed: AutomationPermission[]
  blocked: string[]
}

/** One line of the section: a tool and what this Automation does with it. `tool` is null for a
 *  legacy always-allowed entry that names no tool (only a description) — it can be removed but
 *  not re-valued. `entryIndex` is that entry's position, for removing it. */
export interface AutomationToolRow {
  tool: string | null
  label: string
  value: AutomationToolValue
  entryIndex?: number
}

/** An always-allowed entry's tool names: its `tools` plus its `capability`, which the runner
 *  also treats as a tool name. */
function namesOf(permission: AutomationPermission): string[] {
  return [
    ...new Set([
      ...(permission.tools ?? []),
      ...(permission.capability ? [permission.capability] : []),
    ]),
  ]
}

export function toolRows(lists: ToolLists): AutomationToolRow[] {
  const allowed = lists.always_allowed.flatMap((permission, entryIndex): AutomationToolRow[] => {
    const names = namesOf(permission)
    if (!names.length) {
      return [{ tool: null, label: permission.desc, value: 'always_allow', entryIndex }]
    }
    return names.map((tool) => ({ tool, label: tool, value: 'always_allow', entryIndex }))
  })
  const blocked = lists.blocked.map(
    (tool): AutomationToolRow => ({ tool, label: tool, value: 'blocked' }),
  )
  return [...allowed, ...blocked]
}

/** The always-allowed entries with `tool` taken out; an entry that granted nothing else goes. */
function withoutAllowed(allowed: AutomationPermission[], tool: string): AutomationPermission[] {
  return allowed.flatMap((permission) => {
    if (!namesOf(permission).includes(tool)) return [permission]
    const next: AutomationPermission = { src: permission.src, desc: permission.desc }
    const rest = (permission.tools ?? []).filter((name) => name !== tool)
    if (rest.length) next.tools = rest
    if (permission.capability && permission.capability !== tool) {
      next.capability = permission.capability
    }
    return next.tools || next.capability ? [next] : []
  })
}

/** Remove `tool` from both lists. */
export function removeTool(lists: ToolLists, tool: string): ToolLists {
  return {
    always_allowed: withoutAllowed(lists.always_allowed, tool),
    blocked: lists.blocked.filter((name) => name !== tool),
  }
}

/** Remove one legacy always-allowed entry by position. */
export function removeEntry(lists: ToolLists, entryIndex: number): ToolLists {
  return {
    ...lists,
    always_allowed: lists.always_allowed.filter((_, index) => index !== entryIndex),
  }
}

/** Put `tool` on exactly one list. Always allow appends a new entry (the same shape the server
 *  writes when a run card's *Always allow* is tapped); Block appends to `blocked`. */
export function setTool(lists: ToolLists, tool: string, value: AutomationToolValue): ToolLists {
  const rest = removeTool(lists, tool)
  if (value === 'blocked') return { ...rest, blocked: [...rest.blocked, tool] }
  return {
    ...rest,
    always_allowed: [
      ...rest.always_allowed,
      { src: 'Open Net Worth', desc: `Allow ${tool.replace(/_/g, ' ')}`, tools: [tool] },
    ],
  }
}
