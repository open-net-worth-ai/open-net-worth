// Classification and formatting helpers shared by the card header, the per-type bodies and
// ChangePreview itself. Moved verbatim out of ChangePreview.tsx (no behaviour change).
import type { AutomationUpdateDisplay, ProposedChange, ToolPreview } from '../../../types/chat'
import { fMonth } from '../../../utils/format'
import { pluralize } from '../../../utils/pluralize'

export const SKILL_TYPES = ['skill_create', 'skill_update', 'skill_delete'] as const

export const AGENT_TYPES = ['agent_create', 'agent_update'] as const

// Budget change types (Planning / YNAB envelope ops) — all rendered by BudgetChangePreview.
export const BUDGET_TYPES = [
  'assign',
  'move_money',
  'set_target',
  'group_create',
  'group_rename',
  'group_reorder',
  'group_delete',
  'move_category',
  'reorder_categories',
] as const

// Ledger write-confirm types (txn_create / txn_update / txn_bulk) — tool_use_id-keyed
// (RFC 2026-07-22), like external_mcp_call since 2026-08-22. All rendered by the Txn*Body
// components below.
export const TXN_TYPES = ['txn_create', 'txn_update', 'txn_bulk'] as const

// Change types ChangePreview renders with a bespoke body. Anything NOT here falls through
// to the generic fallback (a humanized title + a key/value table) so a card is never a blind
// header+buttons. Keep this list = the union of the explicit `change.type === …` cases below.
export const HANDLED_TYPES: readonly string[] = [
  'update',
  'add',
  'remove',
  ...SKILL_TYPES,
  'lot_acquire',
  'lot_dispose',
  'lot_delete',
  'record_income',
  ...AGENT_TYPES,
  'external_mcp_call',
  'code_skill_create',
  ...BUDGET_TYPES,
  ...TXN_TYPES,
]

export function isBudgetChange(change: ProposedChange): boolean {
  return (BUDGET_TYPES as readonly string[]).includes(change.type)
}

// The two pluggable-budget-engines write tools (RFC 2026-08-22-pluggable-budget-engines.md,
// Decision F/Phase 2-3: apply_budget_operation — `apply_budget_verb` before 2026-09-26, kept
// here so an older card still reads — and set_home_budget_engine) are ordinary gated tools
// — they carry no bespoke proposed_change shape of their own, so a confirm card for either
// arrives as the SAME generic `type: 'tool_permission'` card every other gated tool gets
// (`stage_generic` in the backend confirm gate). Verbs are DECLARED, not a fixed list this
// file would have to keep in sync (mirrors BUDGET_TYPES's own smell) — so rather than
// hardcoding per-verb labels/bodies, this renders straight off whatever `engine_id`/
// `operation`/`params` the card's own arguments already carry, the same "humanize the
// identifier" strategy HANDLED_TYPES's own generic fallback already uses for any
// unrecognised type.
export const BUDGET_OPERATION_TOOLS = [
  'apply_budget_operation',
  'apply_budget_verb',
  'set_home_budget_engine',
] as const

/** The budget methods by the names the Plan tab shows — never the engine ids. */
export const BUDGET_METHOD_NAMES: Record<string, string> = {
  proportional: '50/30/20',
  zero_based: 'Zero-Based',
}

export const budgetMethodName = (engineId: string) =>
  BUDGET_METHOD_NAMES[engineId] ?? humanizeType(engineId)

export function isBudgetOperationGrant(change: ProposedChange): boolean {
  return (BUDGET_OPERATION_TOOLS as readonly string[]).includes(change.grant_tool || '')
}

export function budgetOperationOf(args: Record<string, unknown>): string | null {
  const op = args.operation ?? args.verb_id
  return typeof op === 'string' && op ? op : null
}

export function budgetOperationHeaderLabel(change: ProposedChange): string {
  const args = permissionArguments(change)
  const engineId = typeof args.engine_id === 'string' ? args.engine_id : null
  if (change.grant_tool === 'set_home_budget_engine') {
    return engineId
      ? `Switch home budget lens to ${budgetMethodName(engineId)}`
      : 'Switch home budget lens'
  }
  const op = budgetOperationOf(args)
  if (!op) return 'Apply budget change'
  return engineId ? `${humanizeType(op)} (${budgetMethodName(engineId)})` : humanizeType(op)
}

// run_code is an ordinary gated tool (ADR 2026-10-11-permissions-are-one-value-per-tool): its
// card is the generic `tool_permission` card, but what the owner is approving is the CODE, so the
// body renders the source from the card's own arguments instead of a raw JSON dump.
export function isCodeRunGrant(change: ProposedChange): boolean {
  return change.grant_tool === 'run_code'
}

export function isTxnChange(change: ProposedChange): boolean {
  return (TXN_TYPES as readonly string[]).includes(change.type)
}

// Header label for the three ledger write-confirm types — shared by ChangePreview and
// ProposedChangeDiff so the two stay in sync (mirrors budgetOpLabel's role for BUDGET_TYPES).
export function txnHeaderLabel(change: ProposedChange): string {
  if (change.type === 'txn_create') return 'Book transaction'
  if (change.type === 'txn_update') return 'Update transaction'
  return 'Import transactions'
}

// Humanize an unknown change type for the generic fallback, e.g. "create_agent" → "Create agent".
export function humanizeType(type: string): string {
  const words = type.replace(/[_-]+/g, ' ').trim()
  if (!words) return 'Proposed change'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function fmt(v: unknown): string {
  if (v == null || v === '') return '—'
  return String(v)
}

// Formats a YYYY-MM budget month as a human-readable label ("June", "June 2027").
export function fBudgetMonth(ym: string | null | undefined): string {
  if (!ym) return '—'
  return fMonth(ym, 'long')
}

// Human-readable header line for each budget op type — used in both the card title and the
// confirm message so they stay in sync.
export function budgetOpLabel(change: ProposedChange): string {
  const d = change.display || {}
  if (change.type === 'assign') return 'Assign budget'
  if (change.type === 'move_money') return 'Move money'
  if (change.type === 'set_target') return String(d.op || 'Set target')
  if (change.type === 'group_create') return 'Create group'
  if (change.type === 'group_rename') return `Rename group`
  if (change.type === 'group_reorder') return 'Reorder groups'
  if (change.type === 'group_delete') return `Delete group`
  if (change.type === 'move_category') return `Move category`
  if (change.type === 'reorder_categories') return 'Reorder categories'
  return String(d.op || humanizeType(change.type))
}

// Shared by GenericChangePreview and BudgetOperationChangePreview: a plain value renders as-is,
// an array joins its rendered members, and a nested object stringifies (there's no generic
// way to know its shape ahead of time).
export function renderGenericValue(v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => renderGenericValue(x)).join(', ')
  if (v != null && typeof v === 'object') {
    try {
      return JSON.stringify(v)
    } catch {
      return String(v)
    }
  }
  return String(v)
}

export interface PermissionTextEdit {
  path: string
  old: string
  new: string
}

export function permissionArguments(change: ProposedChange): Record<string, unknown> {
  const display = (change.display || {}) as Record<string, unknown>
  const args = display.arguments
  return args != null && typeof args === 'object' && !Array.isArray(args)
    ? (args as Record<string, unknown>)
    : {}
}

export function permissionTextEdits(change: ProposedChange): PermissionTextEdit[] {
  const edits: PermissionTextEdit[] = []

  function visit(value: unknown, path: string[]) {
    if (value == null || typeof value !== 'object' || Array.isArray(value)) return
    const row = value as Record<string, unknown>
    if (typeof row.old === 'string' && typeof row.new === 'string') {
      edits.push({ path: path.join('.'), old: row.old, new: row.new })
      return
    }
    Object.entries(row).forEach(([key, child]) => visit(child, [...path, key]))
  }

  visit(permissionArguments(change), [])
  return edits
}

export function previewHasNoChanges(preview: ToolPreview | undefined): boolean {
  if (preview?.kind !== 'field_diff') return false
  const items = Array.isArray(preview.data?.items) ? preview.data.items : []
  return !items.some(
    (item) =>
      item &&
      typeof item === 'object' &&
      Object.keys((item as AutomationUpdateDisplay).changes || {}).length > 0,
  )
}

export function previewFromDisplay(
  display: NonNullable<ProposedChange['display']> | undefined,
): ToolPreview | undefined {
  if (display?.preview) return display.preview

  // Cards created before the adapter envelope shipped stored resolved updates directly
  // under display.arguments. Keep those cards readable after a reload or deployment.
  const args = display?.arguments
  const items = args?.updates
  if (
    !Array.isArray(items) ||
    !items.some((item) => item && typeof item === 'object' && 'changes' in item)
  ) {
    return undefined
  }
  return {
    kind: 'field_diff',
    data: { items },
    raw_arguments: display?.raw_arguments ?? args,
  }
}

const DESTRUCTIVE_TYPES = [
  'remove',
  'skill_delete',
  'lot_delete',
  'group_delete',
  'remove_batch',
  'lot_delete_batch',
  'envelope_delete',
  'group_delete_batch',
]

/** A write that deletes or removes something — its card header is toned as a warning. A
 * generic gate card counts by its tool's own verb (`delete_transactions`, `remove_payee_rules`). */
export function isDestructive(change: ProposedChange): boolean {
  if (DESTRUCTIVE_TYPES.includes(change.type)) return true
  return change.type === 'tool_permission' && /^(delete|remove)_/.test(change.grant_tool || '')
}

/** A card without a bespoke body is titled by the verb its backend staged it with —
 * `display.op` ("Record disposals", "Delete envelope") — not its kind id; a batch without
 * one falls back to the humanized kind. Null when there is no such verb. */
export function stagedTitle(change: ProposedChange): string | null {
  const op = (change.display as { op?: unknown } | undefined)?.op
  if (typeof op === 'string' && op) return op
  return change.type.endsWith('_batch') ? humanizeType(change.type) : null
}

// The backend puts a name beside an id it can resolve (`envelope_id` → `envelope_name`,
// `group_ids` → `group_names`); the card shows the name, not both.
export function namedElsewhere(key: string, display: Record<string, unknown>): boolean {
  const name = nameKeyFor(key)
  return name !== null && display[name] != null && display[name] !== ''
}

// `category` / `categories` are the budget displays' pre-envelope keys (they hold envelope ids);
// everything else follows `x_id` → `x_name`, `x_ids` → `x_names`.
function nameKeyFor(key: string): string | null {
  if (key === 'category') return 'category_name'
  if (key === 'categories') return 'category_names'
  if (key.endsWith('_ids')) return `${key.slice(0, -4)}_names`
  if (key.endsWith('_id')) return `${key.slice(0, -3)}_name`
  return null
}

/** A name standing in for its hidden id is labelled as the thing itself: `envelope_name`
 * beside `envelope_id` reads "Envelope", `group_names` beside `group_ids` reads "Groups". */
export function standInLabel(key: string, display: Record<string, unknown>): string {
  const idKey = Object.keys(display).find((k) => nameKeyFor(k) === key)
  if (!idKey) return key
  if (idKey === 'category' || idKey === 'categories') return idKey
  if (!key.endsWith('_names')) return key.slice(0, -5)
  return pluralize(key.slice(0, -6), 2)
}

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  v != null && typeof v === 'object' && !Array.isArray(v)

export function plainText(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.map(plainText).join(', ') : 'none'
  if (isRecord(v)) {
    const pairs = Object.entries(v)
    if (pairs.length === 0) return 'none'
    return pairs.map(([k, x]) => `${k.replace(/_/g, ' ')}: ${plainText(x)}`).join(', ')
  }
  return scalarText(v)
}

export const scalarText = (v: unknown): string => {
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  return v == null || v === '' ? '—' : String(v)
}

/** A record's entries with each resolved name IN PLACE of its id — `envelope_id` becomes
 * ["envelope", "Dining"] at the id's own position, so a table's Envelope column stays where the
 * id was; the name key itself is not repeated. */
export function withNamesInPlace(record: Record<string, unknown>): Array<[string, unknown]> {
  const out: Array<[string, unknown]> = []
  const shownNames = new Set<string>()
  for (const [key, value] of Object.entries(record)) {
    const nameKey = nameKeyFor(key)
    if (nameKey && record[nameKey] != null && record[nameKey] !== '') {
      out.push([standInLabel(nameKey, record), record[nameKey]])
      shownNames.add(nameKey)
    } else if (!shownNames.has(key) && !isNameOfAnId(key, record)) {
      out.push([key, value])
    }
  }
  return out
}

const isNameOfAnId = (key: string, record: Record<string, unknown>) =>
  Object.keys(record).some((k) => nameKeyFor(k) === key)
