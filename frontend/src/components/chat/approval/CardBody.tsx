import type { ProposedChange } from '../../../types/chat'
import { ApprovalNote } from './ApprovalCard'
import { BudgetChangePreview, BudgetOperationChangePreview } from './bodies/BudgetBodies'
import { CodeRunPreview, CodeSkillPreview, ExternalMcpCallPreview } from './bodies/CodeBodies'
import { DisposalPreview } from './bodies/DisposalPreview'
import { BatchBody } from './bodies/BatchBody'
import {
  GenericChangePreview,
  PermissionArgumentsPreview,
  ToolPreviewBody,
} from './bodies/GenericBodies'
import {
  IncomeBody,
  LotAcquireBody,
  LotDeleteBody,
  PositionAddBody,
  PositionRemoveBody,
  PositionUpdateBody,
} from './bodies/PositionBodies'
import { AgentCreatePreview, AgentUpdatePreview, SkillUpdateBody } from './bodies/SkillAgentBodies'
import { TxnBulkBody, TxnCreateBody, TxnUpdateBody } from './bodies/TxnBodies'
import {
  HANDLED_TYPES,
  isBudgetChange,
  isBudgetOperationGrant,
  permissionTextEdits,
  previewFromDisplay,
} from './cardTypes'

/** Everything between a card's header and its buttons, per change type — ONE dispatch shared
 * by the live card (ChangePreview) and the read-only diff (ProposedChangeDiff), which used to
 * keep two copies of every body. A type without a bespoke body falls to the generic one, so a
 * card is never a blind header + buttons. */
export function CardBody({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const preview = previewFromDisplay(change.display)
  const isExternalMcp = change.type === 'external_mcp_call'
  // `registry.stage_batch` kinds (update_batch, lot_dispose_batch, …) share one {op, count, rows}
  // shape. A batch without `rows` (the budget / envelope batches) is read by the generic body.
  const isBatch = change.type.endsWith('_batch') && Array.isArray(d.rows)
  return (
    <>
      {change.type === 'update' && <PositionUpdateBody change={change} />}
      {change.type === 'add' && <PositionAddBody change={change} />}
      {change.type === 'remove' && <PositionRemoveBody change={change} />}
      {change.type === 'skill_update' && <SkillUpdateBody change={change} />}
      {change.type === 'lot_acquire' && <LotAcquireBody change={change} />}
      {change.type === 'lot_dispose' && <DisposalPreview change={change} />}
      {change.type === 'lot_delete' && <LotDeleteBody change={change} />}
      {change.type === 'record_income' && <IncomeBody change={change} />}
      {change.type === 'agent_create' && <AgentCreatePreview change={change} />}
      {change.type === 'agent_update' && <AgentUpdatePreview change={change} />}
      {change.type === 'code_run' && <CodeRunPreview change={change} />}
      {change.type === 'code_skill_create' && <CodeSkillPreview change={change} />}
      {isBudgetChange(change) && <BudgetChangePreview change={change} />}
      {change.type === 'txn_create' && <TxnCreateBody change={change} />}
      {change.type === 'txn_update' && <TxnUpdateBody change={change} />}
      {change.type === 'txn_bulk' && <TxnBulkBody change={change} />}
      {isExternalMcp && <ExternalMcpCallPreview change={change} />}

      {isBatch && <BatchBody change={change} />}

      {!HANDLED_TYPES.includes(change.type) &&
        !isBatch &&
        (preview ? (
          <ToolPreviewBody preview={preview} />
        ) : isBudgetOperationGrant(change) ? (
          <BudgetOperationChangePreview change={change} />
        ) : change.grant_tool && permissionTextEdits(change).length > 0 ? (
          <PermissionArgumentsPreview change={change} />
        ) : (
          <GenericChangePreview change={change} />
        ))}

      {/* A generic gate card's reason is boilerplate ("<tool> needs your approval…") that the
          card itself already says; any other reason (why a lot is being deleted) is real
          context, and goes last so the lead stays the card's first line. */}
      {change.reason && !isExternalMcp && change.type !== 'tool_permission' && (
        <ApprovalNote>Reason: {change.reason}</ApprovalNote>
      )}
    </>
  )
}
