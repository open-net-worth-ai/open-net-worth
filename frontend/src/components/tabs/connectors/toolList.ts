// Which tool list the Browse & add preview shows, and how it labels it. Three sources, best
// first: the tools the connected server ACTUALLY advertised; the full list we recorded from
// the vendor's own source or docs (`recordedTools.ts`); and, for an MCP server with neither,
// the catalog's hand-picked sample — labelled "Example tools", because a bare "Tools 3" read
// as the whole set for a server with 37 (owner, 2026-10-31).

import type { DirectoryRow } from './catalog'
import { RECORDED_TOOLS } from './recordedTools'

export interface ToolList {
  label: string
  names: string[]
  note?: string
}

export function resolveToolList(d: DirectoryRow, liveTools?: string[]): ToolList {
  if (d.installed && liveTools && liveTools.length > 0) {
    return { label: 'Tools', names: liveTools, note: 'from your connection' }
  }
  const recorded = d.mcp ? RECORDED_TOOLS[d.id] : undefined
  if (recorded && recorded.tools.length > 0) {
    if (recorded.complete === false) {
      return { label: 'Example tools', names: recorded.tools, note: "from the vendor's docs" }
    }
    const at = recorded.version ? `v${recorded.version}` : recorded.asOf
    return { label: 'Tools', names: recorded.tools, note: `as of ${at}` }
  }
  // A native connector's catalog list IS its whole toolset (we wrote the tools); only a
  // third-party server's list is a sample.
  return { label: d.mcp ? 'Example tools' : 'Tools', names: d.tools }
}
