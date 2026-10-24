// The permission-persistence seam — ONE file the ladder writes through.
//
// The three levels are REAL and family-agnostic: `tool_permissions` holds one row per
// (owner, tool) with `level` ∈ always_allow/needs_approval/blocked, an ABSENT row reads as the
// tool's default (a read runs, a change or a send asks; an external MCP tool asks), and — since the 2026-08-14 merge that folded the old binary `tool_grants`
// table into this one and made enforcement universal — that config is the single authority on
// whether ANY tool call fires, native connectors included, not MCP alone (ADR
// `2026-08-14-one-permission-store-for-every-tool`).
//
// TWO SCARS, both from the same shipped defect — this file wrote to
// `PATCH /api/mcp-clients/{id}/tool-permissions` for the whole life of the redesign branch,
// which commit 8d4afa9d had DELETED (verified live: 405). Every level change failed, the grid
// showed the level anyway, and a tool the operator had "blocked" was still being offered:
//
//   1. THE CONNECTOR ID IS SENT WHOLE. `entry.id` is already `mcp:<server_id>` — the exact key
//      the permission rows use — so nothing here strips the prefix and re-adds it. The old
//      call sliced it off to build an MCP-only URL; there IS no MCP-only permissions route,
//      deliberately, because the ladder is one ladder for every connector family.
//   2. THE LEVEL IS SENT VERBATIM. The old code mapped the three levels onto a retired
//      two-value `allow`/`deny` wire vocabulary, which turned "Needs approval" into `allow` —
//      an upgrade, in the unsafe direction, of the one level that means "stop and ask me".

import { authFetch } from '../../../api/client'
import type { ConnectorDetailEntry, PermissionLevel } from './model'

export type SaveResult = { ok: true } | { ok: false; error: string }

async function failure(res: Response): Promise<SaveResult> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  const detail = typeof body?.detail === 'string' ? body.detail : ''
  return { ok: false, error: detail || `Couldn't save that permission level (${res.status})` }
}

/**
 * Persist one level across one or more tools of a connector.
 *
 * A single tool takes the per-tool route; several take the GROUP route in ONE request, which
 * is what makes a group stamp land as one decision rather than N racing ones — matching the
 * control's contract that picking a group level overwrites every tool in it.
 */
export async function savePermissions(
  entry: ConnectorDetailEntry,
  tools: string[],
  level: PermissionLevel,
): Promise<SaveResult> {
  if (tools.length === 0) return { ok: true } // an empty group is a no-op, not an error
  const base = `/api/connectors/${encodeURIComponent(entry.id)}/permissions`
  const [single] = tools
  const url = tools.length === 1 ? `${base}/${encodeURIComponent(single!)}` : base
  const body = tools.length === 1 ? { level } : { tools, level }
  try {
    const res = await authFetch(url, { method: 'PUT', body: JSON.stringify(body) })
    return res.ok ? { ok: true } : await failure(res)
  } catch {
    return { ok: false, error: "Couldn't save that permission level — the request failed." }
  }
}
