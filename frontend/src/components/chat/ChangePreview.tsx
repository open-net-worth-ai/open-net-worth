import { useEffect, useState } from 'react'
import { grantTool, PermissionRefusedError, type GrantScope } from '../../api/toolPermissions'
import type { ProposedChange } from '../../types/chat'
import {
  mcpConnectorId,
  setToolPermission as setMcpToolPermission,
} from '../tabs/connectors/mcpApi'
import { CardBody } from './approval/CardBody'
import { GrantActions } from './approval/GrantActions'
import { CardHeader } from './approval/CardHeader'
import { previewFromDisplay, previewHasNoChanges } from './approval/cardTypes'

interface Props {
  change: ProposedChange
  // Resolve a paused (awaiting-approval) tool_use — the ONLY way a card is ever answered:
  // server-side, against the SAME tool_use_id (`change.id`) the card is bound to, with no
  // model turn at all.
  //
  // There is deliberately no `onSendMessage` here any more. Answering a card by sending
  // Lucius a sentence — "Confirmed — remove Gold (spot) from the portfolio" — is what this
  // component used to do for most change types, and it is the whole defect behind ADR
  // 2026-08-25-one-confirm-mechanism-for-every-tool: the model had to reconstruct the write
  // from prose, and on the position/lot cards it could not, so a ✓ drew a second card and
  // wrote nothing. Removing the prop is what stops that being re-addable one branch at a
  // time.
  onResolvePendingToolUse?: (
    toolUseId: string,
    decision: 'approve' | 'deny',
    options?: { always?: boolean },
  ) => void
  // Persist the user's response so the card stays resolved across re-renders /
  // reloads. Without it, ChangePreview only tracks an optimistic local response.
  onRespond?: (status: 'confirmed' | 'dismissed') => void
}

/**
 * ProposedChangeDiff — read-only rendering of a proposed change's visual diff.
 *
 * Renders the same header + body as ChangePreview but WITHOUT the Confirm/Dismiss
 * action buttons. Use this in the Inbox and Agents findings panel where the change
 * is being *reviewed*, not actioned from this surface. The raw JSON fallback is
 * intentionally omitted — GenericChangePreview handles any unrecognised type.
 */
export function ProposedChangeDiff({ change }: { change: ProposedChange }) {
  return (
    <div className="change-preview change-preview--readonly">
      <CardHeader change={change} />
      <CardBody change={change} />
    </div>
  )
}

// `change.refused` ⇒ the server could not redeem this card's tool_use_id — a LATER message in
// the conversation replaced it, or it was already answered — so nothing ran and nothing ever
// can via this card again (the backend consumes/replaces a pending tool_use once; there is no
// second redemption). Every button-bearing layout below (the grant_tool/external_mcp_call
// split button) must render this SAME terminal row instead of its live buttons, or a re-armed
// "Allow once" sits over the error that just explained why the first tap did nothing — the
// mobile incident this fixes (2026-09-06). Reuses `.change-confirmed`, the row the card already
// shows for "✓ Approved"/"✗ Dismissed", so no new className is needed.
function RefusedRow() {
  return (
    <div className="change-confirmed" role="status" style={{ color: 'var(--muted)' }}>
      Not run — can't be reused.
    </div>
  )
}

export function ChangePreview({ change, onRespond, onResolvePendingToolUse }: Props) {
  // Optimistic local response for instant feedback; the persisted `change.status`
  // (written via onRespond) is what survives a reload / conversation switch. Either
  // being set resolves the card and hides the Confirm/Dismiss buttons.
  const [localResponse, setLocalResponse] = useState<'confirmed' | 'dismissed' | null>(null)
  const responded = localResponse ?? change.status ?? null

  // `change.refused` ⇒ the server could not redeem this approval, so NOTHING ran. Drop the
  // optimistic tick as well as the persisted status, or the card goes on rendering "✓ Approved"
  // directly above the red error — the UI asserting a write that never happened (the 2026-07-30
  // screenshot behind ADR `approvals-never-expire`). Keyed on the flag rather than on
  // `status === undefined` because the tap's own two updates batch into one render, so a
  // status-transition watcher would never see the change.
  useEffect(() => {
    if (change.refused) setLocalResponse(null)
  }, [change.refused])

  const isPermissionCard = Boolean(change.grant_tool)
  // external_mcp_call renders its OWN three-way card (see the early return below) rather
  // than falling into isPermissionCard's grant_tool branch — it deliberately never carries
  // grant_tool (see the docstring on backend mcp/client_manager.py's _build_proposed_change).
  const isExternalMcpCard = change.type === 'external_mcp_call'

  // Permission-card state. `grantBusy` disables the row while the grant POST is in flight so
  // a double-tap cannot record two grants or fire two approvals; `grantError` records a
  // FAILED standing grant — the call still went ahead (the user approved it), but the
  // standing permission did not stick and saying so is the whole point (see api/toolPermissions.ts).
  const [grantBusy, setGrantBusy] = useState<GrantScope | null>(null)
  // The scope the user actually chose, so the card answers "✓ Approved" or "✓ Always allowed"
  // at once; once a reload has run, the persisted `change.resolution` says the same.
  const [grantedScope, setGrantedScope] = useState<GrantScope | null>(null)

  const preview = previewFromDisplay(change.display)
  const isPreviewNoChanges = previewHasNoChanges(preview)

  const [grantError, setGrantError] = useState<string | null>(null)

  // handleGrant records the standing grant (if any), then calls this, which resolves the
  // SAME tool_use_id regardless of scope. The scope only decided whether a standing grant was
  // written; it is passed on so the answered card remembers WHICH answer it was (`resolution`).
  function handleConfirm(scope: GrantScope = 'once') {
    setLocalResponse('confirmed')
    onRespond?.('confirmed')
    // A permission card is checked FIRST and resolves EVERY gated tool the SAME way,
    // because the gate covers ~162 write tools and enumerating a bespoke sentence per tool
    // (as the legacy branches below do per change TYPE) would guarantee that whichever tool
    // was added last falls through to a vague generic. Resolution happens SERVER-SIDE
    // against the SAME tool_use_id this card is bound to (RFC 2026-07-22) — never a model
    // re-call, so there is nothing to tell the model and no token to hand back.
    if (change.grant_tool) {
      onResolvePendingToolUse?.(change.id ?? '', 'approve', { always: scope === 'always' })
      return
    }
    // external_mcp_call migrated onto the SAME tool_use_id-keyed resolve path 2026-08-22
    // (ai.tools.external_mcp / apply_external_mcp_call + its nonce store are deleted — see
    // docs/adr/2026-08-22-external-mcp-confirm-migrates-onto-generic-resolve.md). It
    // deliberately does NOT carry `grant_tool` (that key is reserved for a canonical
    // registry tool name the Allow-once/Always-allow grant machinery can resolve an owner
    // for — an external server's tool name is not one), so it needs its own branch here
    // rather than falling into the grant_tool check above; it still resolves the SAME way,
    // by tool_use_id, with a bare Confirm/Dismiss (not the four-way grant card).
    if (change.type === 'external_mcp_call') {
      onResolvePendingToolUse?.(change.id ?? '', 'approve', { always: scope === 'always' })
      return
    }
    // EVERY confirm card resolves server-side now (ADR
    // 2026-08-25-one-confirm-mechanism-for-every-tool). The branches that used to live here
    // sent Lucius a sentence — "Confirmed — create the \"X\" skill (call apply_skill_change)"
    // — and hoped he re-called the apply tool with arguments he RECONSTRUCTED. What committed
    // was therefore whatever he remembered, not what the card showed; on the position/lot
    // cards he was told to pass back a token he had never been given, so a ✓ drew a second
    // identical card and wrote nothing at all. Staging is universal, so a card carries either
    // `grant_tool` or `external_mcp_call`, and both were answered above.
    //
    // A card with neither is a bug, not a fallback: answering it by asking the model would
    // reintroduce exactly the reconstruct-the-args path this replaced.
    console.error('confirm card has no server-side binding', change.type)
  }

  async function handleGrant(scope: GrantScope) {
    if (grantBusy) return
    setGrantBusy(scope)
    setGrantedScope(scope)
    setGrantError(null)
    let answered: GrantScope = scope
    try {
      await grantTool({
        tool: change.grant_tool as string,
        scope,
        session_id: change.session_id,
      })
    } catch (err) {
      // The approval for THIS call still stands — the user tapped it and the card showed
      // exactly what will happen. Only the STANDING part failed, so we proceed and say so
      // rather than either silently pretending it stuck or discarding an approval the user
      // actually gave. A failed standing grant costs the user a belief about their own
      // permissions that is not true, so it is reported rather than swallowed — and the call
      // is recorded as Approved (once), never "Always allowed", since nothing was allowed.
      answered = 'once'
      setGrantedScope('once')
      setGrantError(
        err instanceof PermissionRefusedError
          ? `${err.message} This call is approved this once.`
          : scope === 'always'
            ? "Couldn't save the permanent permission — this call is approved, but you'll be asked again."
            : "Couldn't save the session permission — this call is approved, but you'll be asked again.",
      )
    } finally {
      setGrantBusy(null)
    }
    handleConfirm(answered)
  }

  function handleDeny() {
    setLocalResponse('dismissed')
    onRespond?.('dismissed')
    onResolvePendingToolUse?.(change.id ?? '', 'deny')
  }

  // ── external_mcp_call permission card actions ───────────────────────────────────────────
  //
  // A dedicated 3-way card (Deny / Always allow / Allow once — see the early return below),
  // matching the visual reference the owner supplied from Claude Desktop's own MCP
  // permission popup. It is a SIBLING of the grant_tool permission card above, not a variant
  // of it: external_mcp_call deliberately never carries `grant_tool` (backend
  // mcp/client_manager.py's `_build_proposed_change` docstring — an external server's
  // self-reported tool name must never become a grantable standing permission through the
  // generic registry machinery, which resolves an owner by looking the name up in the
  // REGISTRY). So "Always allow" here writes through the separate, connector-scoped
  // tool-permissions route instead — the SAME one the Connectors tab's own per-tool ladder
  // and the MCP add-flow's step 4 already use for an MCP server's tools
  // (`components/tabs/connectors/mcpApi.ts::setToolPermission`, keyed on `mcp:<server_id>` +
  // the tool name, never resolved through the registry).
  async function handleMcpAlwaysAllow() {
    if (grantBusy) return
    setGrantBusy('always')
    setGrantedScope('always')
    setGrantError(null)
    try {
      const ok = await setMcpToolPermission(
        mcpConnectorId(change.server_id ?? ''),
        change.tool ?? '',
        'always_allow',
      )
      if (!ok) throw new Error('permission update failed')
    } catch {
      // Mirrors handleGrant above: the approval for THIS call still stands (tapping Always
      // allow IS an approval of the current call) — only the STANDING permission failed to
      // save, so we proceed and say so rather than silently pretending it stuck.
      setGrantError(
        "Couldn't save the permanent permission — this call is approved, but you'll be asked again.",
      )
    } finally {
      setGrantBusy(null)
    }
    handleConfirm('always')
  }

  function handleMcpAllowOnce() {
    setGrantedScope('once')
    handleConfirm('once')
  }

  return (
    <div className="change-preview">
      <CardHeader change={change} />
      <CardBody change={change} />

      {/* A failed standing grant, surfaced next to the buttons that caused it. The call was
          still approved — this says only that the PERMISSION did not stick. */}
      {grantError && (
        <div className="change-grant-error" role="alert">
          {grantError}
        </div>
      )}

      {change.refused ? (
        <RefusedRow />
      ) : responded !== null ? (
        responded === 'confirmed' ? (
          <div className="change-confirmed">
            {isPermissionCard || isExternalMcpCard
              ? change.resolution === 'always_allowed' ||
                (!change.resolution && grantedScope === 'always')
                ? '✓ Always allowed'
                : '✓ Approved'
              : '✓ Confirmed'}
          </div>
        ) : (
          <div className="change-confirmed" style={{ color: 'var(--muted)' }}>
            {isPermissionCard || isExternalMcpCard ? '✗ Denied' : '✗ Dismissed'}
          </div>
        )
      ) : isPermissionCard || isExternalMcpCard ? (
        // [Allow once] [Always allow] … [Deny] — grouped into a split button on a narrow card
        // (see GrantActions). A grant names this TOOL and nothing else — no amount ceiling, no
        // account set, no argument matching (RFC §13.3b).
        //
        // external_mcp_call reuses this EXACT layout (owner, 2026-08-22) but routes Once/Always
        // through the MCP-specific handlers — it never carries grant_tool (see
        // handleMcpAlwaysAllow), so its standing grant writes through the connector-scoped
        // tool-permissions route, not the registry grant machinery.
        <GrantActions
          busy={grantBusy}
          disabled={isPreviewNoChanges}
          onOnce={() => (isExternalMcpCard ? handleMcpAllowOnce() : handleGrant('once'))}
          onAlways={() =>
            isExternalMcpCard ? void handleMcpAlwaysAllow() : void handleGrant('always')
          }
          onDeny={handleDeny}
          inAutomation={Boolean(change.automation_id)}
        />
      ) : (
        // Unreachable: every staged card carries `grant_tool` or is `external_mcp_call` — both
        // handled above. A card with neither never resolves, so it is a bug to surface, not a
        // bare Confirm to fall back on.
        <div className="change-preview-actions">
          <span className="change-grant-error">
            This change has no way to be applied — please report it.
          </span>
        </div>
      )}
    </div>
  )
}
