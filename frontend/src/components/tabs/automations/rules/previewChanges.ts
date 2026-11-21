import type { RuleMatch } from '../../../../api/automations'

interface Labels {
  categories: Array<{ id: string; label: string }>
}

const name = (labels: Labels, id: string | null) =>
  id ? (labels.categories.find((c) => c.id === id)?.label ?? id) : 'Uncategorized'

/** What changes on one row, as short "field: before → after" phrases. */
export function describeChanges(match: RuleMatch, labels: Labels): string[] {
  const after = match.after
  if (!after) return []
  const out: string[] = []
  // A split row's category is the ledger's "split" marker — "Split into N lines" says it.
  if (after.category !== match.category && after.category !== 'split') {
    out.push(`Category: ${name(labels, match.category)} → ${name(labels, after.category)}`)
  }
  // The server sends null for a row with no tags or no split lines.
  const before = match.tags ?? []
  const added = (after.tags ?? []).filter((tag) => !before.includes(tag))
  if (added.length) out.push(`Tags: + ${added.join(', ')}`)
  if (after.payee_id !== match.payee_id) {
    out.push(`Payee: ${match.payee_name ?? 'none'} → changed`)
  }
  if (after.description !== match.description) out.push(`Description: ${after.description}`)
  if (after.memo !== match.memo) out.push(`Memo: ${after.memo}`)
  const lines = after.splits?.length ?? 0
  if (lines !== (match.splits?.length ?? 0) && lines > 0) {
    out.push(`Split into ${lines} ${lines === 1 ? 'line' : 'lines'}`)
  }
  return out
}
