import { authFetch } from './client'
import { fetchCachedQuery, invalidateQueryCache } from '../state/queryCache'

// The ONE client seam for tool permissions — the confirm card's grant buttons, Settings ›
// Permissions, AND the Connectors tab's per-connector ladder all answer through this one
// table now (backend ADR `2026-08-14-one-permission-store-for-every-tool`). Before that
// merge there were two client modules (`api/grants.ts` for the confirm card + Settings,
// `components/tabs/connectors/permissions.ts` for the connector-scoped routes) because
// there were two backend stores that could disagree. The connector-scoped module still
// exists — its routes are unchanged — but this file is now the only one that can answer
// "what has the user decided about this tool, anywhere".
//
// Mirrors backend/api/tool_permissions.py:
//   GET    /api/tool-permissions
//   PUT    /api/tool-permissions/{tool}
//   DELETE /api/tool-permissions/{tool}
//
// Three levels, renamed from the old binary-grant vocabulary and the old connector-ladder
// vocabulary at once: `always_allow` (was `allow`/a stored grant), `needs_approval` (was
// `ask`/no grant), `blocked` (was `block` — a level the old grant table had no way to
// express at all). An ABSENT row reads as `needs_approval`.
//
// The model: every READ tool runs free, every WRITE tool (plus `send_email`, which has an
// effect outside this install) confirms EVERY time until set to `always_allow`. A decision
// names a TOOL and nothing else — no amount ceiling, no account set, no argument matching,
// ever (§13.3b of the approval-model RFC). The only knob on the confirm card is SCOPE.
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

/** The owner bucket for a tool that belongs to no connector — `add_transactions`,
 *  `send_email`, every unscoped write tool. */
export const CORE_CONNECTOR_ID = 'core'

/** The reserved tool name for yolo. A real tool can never collide: names are identifiers. */
export const YOLO_TOOL = '*'

/** One stored decision, app-wide — a Settings › Permissions row. */
export interface DecisionRow {
  /** The CANONICAL tool name at decision time — the name the user saw on the confirm card,
   *  or the registry name a connector ladder addressed. */
  tool: string
  /** The tool's OWNER: a connector id, `mcp:<server_id>`, or `core` for an unscoped tool. */
  connector_id: string
  level: PermissionLevel | string
  updated_at: string
  /** False when the stored tool name no longer resolves in the registry — a rename retired
   *  it, so the decision is already inert. Rendered rather than hidden: a permission the
   *  user set and can no longer see is the failure this surface exists to fix. Always true
   *  for the reserved `*` yolo row and for an MCP-owned tool, neither of which the registry
   *  can speak for. */
  tool_exists: boolean
}

export interface DecisionsResponse {
  permissions: DecisionRow[]
  total: number
  /** Whether the blanket "stop asking me" row is armed. Read off the STORED row rather
   *  than inferred, so this can never disagree with the gate it describes (a real, shipped
   *  failure the old grant table fixed once: an env-var implementation reported OFF while
   *  a stored row skipped confirmations). */
  yolo: boolean
}

export async function listToolPermissions(): Promise<DecisionsResponse> {
  return fetchCachedQuery(
    '/api/tool-permissions',
    async () => {
      const res = await authFetch('/api/tool-permissions')
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
   *  the old key simply stops resolving and the user is asked again rather than a
   *  decision quietly widening. */
  tool: string
  level: PermissionLevel
  /** The tool's OWNER, when the caller already knows it (Settings › Permissions always
   *  does — it is re-setting a row it is displaying). Omitted, the server resolves it
   *  from the registry — the only option for a tool the caller has never seen a row for,
   *  e.g. the confirm card. */
  connector_id?: string
  /** Which conversation this decision was made in — NOT a scope. Sent for an
   *  Automation-owned conversation so `always_allow` is scoped to that Automation instead
   *  of becoming a global decision. */
  session_id?: string
  /** The card the decision was tapped on. In an Automation run, *Always* on a `fetch_page`
   *  card allows that card's website only (`fetch_hosts`), never the whole tool. */
  tool_use_id?: string
}

/**
 * Set one tool's level. Throws on a non-OK response so a failed standing permission is
 * reported, never silently swallowed (see the module note).
 */
export async function setToolPermission(req: SetToolPermissionRequest): Promise<DecisionsResponse> {
  const res = await authFetch(`/api/tool-permissions/${encodeURIComponent(req.tool)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      level: req.level,
      ...(req.connector_id ? { connector_id: req.connector_id } : {}),
      ...(req.session_id ? { session_id: req.session_id } : {}),
      ...(req.tool_use_id ? { tool_use_id: req.tool_use_id } : {}),
    }),
  })
  if (!res.ok) throw new Error(`permission update failed (${res.status})`)
  invalidateQueryCache('/api/tool-permissions')
  return res.json()
}

/**
 * Forget one tool's decision — it returns to its default (`needs_approval` for a connector
 * tool). `connectorId` is REQUIRED: the server cannot re-derive a stale or MCP-owned row's
 * owner from a registry that no longer knows the name, so the caller hands back the one it
 * was shown (`DecisionRow.connector_id`).
 */
export async function clearToolPermission(
  tool: string,
  connectorId: string,
): Promise<{ cleared: string }> {
  const res = await authFetch(
    `/api/tool-permissions/${encodeURIComponent(tool)}?connector_id=${encodeURIComponent(connectorId)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`Failed to clear ${tool} (${res.status})`)
  return res.json()
}

// ── The confirm card's grant buttons ────────────────────────────────────────────────────
//
// The card only ever offers two choices, and only one of them writes anything:
//
//  - `once`   — this call only. Nothing is stored; the card's ✓ already IS that grant,
//               redeemed by the single-use confirm token.
//  - `always` — durable, until reset in Settings › Permissions. NO tool is excluded from
//               this scope (D8/D9 were proposed and withdrawn) — `restore_checkpoint`
//               included, which is safe only because the permission table is
//               checkpoint-EXCLUDED and so cannot be restored back into existence.
//
// There was a third, `session` (until the conversation ends). Removed 2026-08-02: a card
// offers Allow once and Always allow, nothing else.
export type GrantScope = 'once' | 'always'

export interface GrantRequest {
  tool: string
  scope: GrantScope
  /** See `SetToolPermissionRequest.session_id`. */
  session_id?: string
  /** See `SetToolPermissionRequest.tool_use_id`. */
  tool_use_id?: string
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
    ...(req.tool_use_id ? { tool_use_id: req.tool_use_id } : {}),
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

/**
 * The suffix appended to a resolved "✓ Approved" chip so it names the grant that was given.
 * `once` adds nothing — it is the default and the common case, and "✓ Approved" already reads
 * as a one-off. A dot-separated tail keeps the standing scope visible without shouting.
 */
export const GRANT_RESOLVED_SUFFIX: Record<GrantScope, string> = {
  once: '',
  always: ' • always',
}
