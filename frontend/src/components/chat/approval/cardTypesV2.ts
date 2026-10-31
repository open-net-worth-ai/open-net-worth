import type { ProposedChange } from '../../../types/chat'
import { HANDLED_TYPES } from './cardTypes'

// permissions_v2: run_code is an ordinary gated tool (ADR 2026-10-11-permissions-are-one-value-per-tool),
// so there is no bespoke `code_run` card type — it is the generic `tool_permission` card.
export const HANDLED_TYPES_V2: readonly string[] = HANDLED_TYPES.filter((t) => t !== 'code_run')

// Its card is the generic `tool_permission` card, but what the owner is approving is the CODE, so
// the body renders the source from the card's own arguments instead of a raw JSON dump.
export function isCodeRunGrant(change: ProposedChange): boolean {
  return change.grant_tool === 'run_code'
}
