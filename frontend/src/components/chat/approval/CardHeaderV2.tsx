import type { ProposedChange } from '../../../types/chat'
import {
  budgetOpLabel,
  budgetOperationHeaderLabel,
  humanizeType,
  isBudgetChange,
  isBudgetOperationGrant,
  isDestructive,
  isTxnChange,
  stagedTitle,
  txnHeaderLabel,
} from './cardTypes'
import { HANDLED_TYPES_V2, isCodeRunGrant } from './cardTypesV2'

/** The card's title line — what kind of write, and on what — shared by the live card and the
 * read-only diff so the two can never name a change differently. It names the action and its
 * subject ONCE; a body's lead adds only what this can't say (docs/APPROVAL_CARDS.md). A
 * destructive write is toned here, so its lead doesn't need a second red line. */
export function CardHeaderV2({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  return (
    <div
      className={`change-preview-header${isDestructive(change) ? ' change-preview-header--danger' : ''}`}
    >
      {change.type === 'update' && 'Update position'}
      {change.type === 'add' && 'Add position'}
      {change.type === 'remove' && 'Remove position'}
      {change.type === 'skill_create' && 'Create skill'}
      {change.type === 'skill_update' && 'Update skill'}
      {change.type === 'skill_delete' && 'Delete skill'}
      {change.type === 'lot_acquire' && 'Record acquisition'}
      {change.type === 'lot_delete' && 'Delete lot'}
      {change.type === 'lot_dispose' &&
        (d.is_taxable !== false
          ? 'Record sale'
          : d.transfer_to_account_name || d.transfer_to_account_id
            ? 'Record transfer'
            : 'Record transfer-out')}
      {change.type === 'record_income' && (d.is_drp ? 'Record DRP reinvestment' : 'Record income')}
      {change.type === 'agent_create' && 'Create agent'}
      {change.type === 'agent_update' && 'Update agent'}
      {isCodeRunGrant(change) && 'Run a script'}
      {change.type === 'code_skill_create' && 'Save as script-skill'}
      {change.type === 'external_mcp_call' && (
        <>
          <span className="change-preview-tool">
            {String(d.name || change.tool || 'Tool call')}
          </span>
          {(d.server || change.server_id) && (
            <span className="change-preview-label">{String(d.server || change.server_id)}</span>
          )}
        </>
      )}
      {isBudgetChange(change) && budgetOpLabel(change)}
      {isBudgetOperationGrant(change) && budgetOperationHeaderLabel(change)}
      {isTxnChange(change) && txnHeaderLabel(change)}
      {!HANDLED_TYPES_V2.includes(change.type) &&
        !isBudgetOperationGrant(change) &&
        !isCodeRunGrant(change) &&
        (stagedTitle(change) ??
          humanizeType(
            change.type === 'tool_permission' && change.grant_tool
              ? change.grant_tool
              : change.type,
          ))}
      {(d.symbol || d.name) && !isBudgetChange(change) && change.type !== 'external_mcp_call' && (
        <span className="change-preview-label">
          {d.symbol || d.name}
          {d.venue && ` • ${d.venue}`}
        </span>
      )}
    </div>
  )
}
