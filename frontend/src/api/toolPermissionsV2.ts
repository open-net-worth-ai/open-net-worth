import { authFetch } from './client'
import { fetchCachedQuery, invalidateQueryCache } from '../state/queryCache'

// permissions_v2 only (docs/FEATURE_FLAGS.md): the client for the one-value-per-tool API. With the
// flag off every caller uses `toolPermissions.ts`, which talks to the old response shapes.
//
// The ONE client seam for tool permissions — the confirm card's grant buttons, Settings ›
// Permissions, the Automation page's tool picker AND the Connectors tab's per-connector ladder
// all answer through this one table (backend ADR `2026-08-14-one-permission-store-for-every-tool`;
// the one-value model is ADR `2026-10-11-permissions-are-one-value-per-tool`).
//
// Mirrors backend/api/tool_permissions.py:
//   GET    /api/tool-permissions
//   PUT    /api/tool-permissions/{tool}
//   DELETE /api/tool-permissions/{tool}          (Reset to the seed value)
//
// EVERY tool has exactly one value: Always allow, Ask or Block (stored as `always_allow` /
// `needs_approval` / `blocked`). There is no "not set" state: a tool is seeded with a default —
// a read runs, a change or a send asks, an external MCP tool asks — and `default_level` names
// it, so a client shows Reset exactly when the value differs from it. A decision names a TOOL and
// nothing else — no amount ceiling, no account set, no argument matching, ever (§13.3b of the
// approval-model RFC). The mode (manual / auto / skip) only decides what Ask means.
//
// **Failure is REPORTED, never swallowed.** An earlier sibling of this flow
// (`handleCodeAllow`'s `catch {}` in ChangePreview) treats a failed grant as best-effort and
// runs the action anyway — defensible there because the nonce still gates that one script.
// Here it is not: silently proceeding after a failed "Always allow" leaves the user believing
// they granted a standing permission that does not exist, and they only discover it by being
// asked again with no explanation. So `setToolPermission` throws and the card renders the
// failure while still honouring the approval for THIS call.

/** The three real levels. Matches `backend/api/tool_permissions.py`'s `Level` exactly. */
export type PermissionLevel = 'always_allow' | 'needs_approval' | 'blocked'

/** What each level is called on screen. The stored names are unchanged. */
export const LEVEL_LABELS: Record<PermissionLevel, string> = {
  always_allow: 'Always allow',
  needs_approval: 'Ask',
  blocked: 'Block',
}

/** The levels in menu order. */
export const LEVEL_ORDER: PermissionLevel[] = ['always_allow', 'needs_approval', 'blocked']

/** The owner bucket for a tool that belongs to no connector — `add_transactions`,
 *  `email_send_message`, every unscoped write tool. */
export const CORE_CONNECTOR_ID = 'core'

/** One tool and its value — a Settings › Permissions row, an Automation picker entry. */
export interface ToolPermission {
  /** The CANONICAL tool name — the name the user saw on the confirm card, or the registry
   *  name a connector ladder addressed. */
  tool: string
  /** The tool's OWNER: a connector id, `mcp:<server_id>`, or `core` for an unscoped tool. */
  connector_id: string
  level: PermissionLevel | string
  /** The seed: what the tool starts at and what Reset restores. Empty for a row whose tool no
   *  longer exists (Reset deletes it). */
  default_level: PermissionLevel | ''
  /** The tool's own first sentence; empty for an external MCP tool. */
  description: string
  updated_at: string
  /** False when the stored tool name no longer resolves in the registry — a rename retired
   *  it, so the decision is already inert. Rendered rather than hidden: a permission the
   *  user set and can no longer see is the failure this surface exists to fix. */
  tool_exists: boolean
}

export interface ToolPermissionsResponse {
  permissions: ToolPermission[]
  total: number
}

export const TOOL_PERMISSIONS_URL = '/api/tool-permissions'
// The cache key differs from the URL on purpose: the old and the new shape are served from the
// same URL, and the query cache is mirrored to localStorage, so a flipped flag must never paint
// the other shape's entry. `invalidateQueryCache` matches by prefix, so a write still reaches it.
export const TOOL_PERMISSIONS_KEY = `${TOOL_PERMISSIONS_URL}#v2`

export async function listToolPermissions(): Promise<ToolPermissionsResponse> {
  return fetchCachedQuery(
    TOOL_PERMISSIONS_KEY,
    async () => {
      const res = await authFetch(TOOL_PERMISSIONS_URL)
      if (!res.ok) throw new Error(`Failed to load permissions (${res.status})`)
      return res.json()
    },
    { staleTime: 0 },
  )
}

export interface SetToolPermissionRequest {
  /** The CANONICAL tool name. A decision deliberately does NOT survive a tool rename
   *  (§15.1): a rename in this codebase is frequently a semantic change —
   *  `delete_account` → `delete_accounts` turned a single-target call into a batch — so
   *  the old key simply stops resolving and the tool is seeded afresh rather than a
   *  decision quietly widening. */
  tool: string
  level: PermissionLevel
  /** The tool's OWNER, when the caller already knows it (Settings › Permissions always
   *  does — it is re-setting a row it is displaying). Omitted, the server resolves it
   *  from the registry — the only option for a tool the caller has never seen a row for,
   *  e.g. the confirm card. */
  connector_id?: string
  /** Which conversation this decision was made in — NOT a scope. Sent for an
   *  Automation-owned conversation so Always allow / Block land on THAT Automation's own
   *  lists instead of becoming a global decision. */
  session_id?: string
}

/**
 * Set one tool's level. Throws on a non-OK response so a failed standing permission is
 * reported, never silently swallowed (see the module note).
 */
export async function setToolPermission(
  req: SetToolPermissionRequest,
): Promise<ToolPermissionsResponse> {
  const res = await authFetch(`/api/tool-permissions/${encodeURIComponent(req.tool)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      level: req.level,
      ...(req.connector_id ? { connector_id: req.connector_id } : {}),
      ...(req.session_id ? { session_id: req.session_id } : {}),
    }),
  })
  if (!res.ok) {
    const detail = res.status === 409 ? await refusalDetail(res) : null
    if (detail) throw new PermissionRefusedError(detail)
    throw new Error(`permission update failed (${res.status})`)
  }
  invalidateQueryCache(TOOL_PERMISSIONS_KEY)
  return res.json()
}

/** The server understood the request and refused it on purpose (HTTP 409), writing nothing —
 *  e.g. a card's Always allow whose Automation was deleted meanwhile. `message` is the server's
 *  own sentence, written to be shown to the owner as is. */
export class PermissionRefusedError extends Error {}

async function refusalDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    return typeof body.detail === 'string' && body.detail ? body.detail : null
  } catch {
    return null
  }
}

/**
 * Reset one tool to its seed value. `connectorId` is REQUIRED: the server cannot re-derive a
 * stale or MCP-owned row's owner from a registry that no longer knows the name, so the caller
 * hands back the one it was shown (`ToolPermission.connector_id`). `level` is the value the tool
 * is back at, or null when the row was deleted because its tool no longer exists.
 */
export async function resetToolPermission(
  tool: string,
  connectorId: string,
): Promise<{ reset: string; level: PermissionLevel | null }> {
  const res = await authFetch(
    `/api/tool-permissions/${encodeURIComponent(tool)}?connector_id=${encodeURIComponent(connectorId)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`Failed to reset ${tool} (${res.status})`)
  invalidateQueryCache(TOOL_PERMISSIONS_KEY)
  return res.json()
}

/** A tool's name in words ("delete_transactions_where" → "Delete transactions where"). The raw
 *  identifier is an implementation detail; it stays available as a tooltip, never as the label. */
export function toolTitle(tool: string): string {
  const words = tool.replace(/_/g, ' ')
  return words ? words[0]!.toUpperCase() + words.slice(1) : words
}

/** A tool's OWNER, in words — `connector_id` is an opaque id (`core`, `plaid`, `mcp:firecrawl`,
 *  `web_search:tavily`), and a bare `plaid_fetch_transactions` with no context is a puzzle. */
export function ownerLabel(connectorId: string): string {
  const cap = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : s)
  if (connectorId === CORE_CONNECTOR_ID) return 'Built-in'
  if (connectorId.startsWith('mcp:')) return `MCP • ${connectorId.slice(4)}`
  if (connectorId.startsWith('web_search:')) return `Web search • ${cap(connectorId.slice(11))}`
  return cap(connectorId.replace(/_/g, ' '))
}

// ── The confirm card's grant buttons ────────────────────────────────────────────────────
//
// The card only ever offers two choices, and only one of them writes anything:
//
//  - `once`   — this call only. Nothing is stored; the card's ✓ already IS that grant,
//               redeemed by the single-use confirm token.
//  - `always` — durable, until changed in Settings › Permissions. NO tool is excluded from
//               this scope (D8/D9 were proposed and withdrawn) — `restore_checkpoint`
//               included, which is safe only because the permission table is
//               checkpoint-EXCLUDED and so cannot be restored back into existence. Inside an
//               Automation run it lands on THAT Automation's own Always allowed list.
export type GrantScope = 'once' | 'always'

export interface GrantRequest {
  tool: string
  scope: GrantScope
  /** See `SetToolPermissionRequest.session_id`. */
  session_id?: string
}

/**
 * Record the card's choice. `once` returns immediately without a request — there is
 * nothing to store. `always` writes `always_allow`, omitting `connector_id`: the card
 * only ever knows the tool NAME it showed the user, never its owner, so the server
 * resolves it from the registry (`_resolve_owner` in `api/tool_permissions.py`).
 *
 * Throws on failure (see the module note) so the card can surface it while still
 * honouring the approval for THIS call.
 */
export async function grantTool(req: GrantRequest): Promise<void> {
  if (req.scope === 'once') return
  await setToolPermission({
    tool: req.tool,
    level: 'always_allow',
    ...(req.session_id ? { session_id: req.session_id } : {}),
  })
}

/**
 * The label each scope carries on the card. Kept beside the type so a new scope cannot ship
 * with a button that does not say what it does.
 */
export const GRANT_SCOPE_LABELS: Record<GrantScope, string> = {
  once: 'Allow once',
  always: 'Always allow',
}
