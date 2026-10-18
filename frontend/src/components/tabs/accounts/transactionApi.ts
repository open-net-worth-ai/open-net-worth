// The transaction writes and reads the review cards and the detail drawer need, as plain API
// calls — no register state. They were the Accounts tab's own closures over its ledger hook,
// which is why the review and the drawer could only ever mount inside that tab; the app-level
// host (TransactionSurfaces.tsx) uses these instead and announces each write on the data bus,
// so every view that watches the ledger — the register, the counts, Lucius's landing — refetches.

import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { invalidateQueryCache } from '../../../state/queryCache'
import { fromApiSourceEntry, fromApiStatementLink, fromApiTxn } from './data'
import type { CatKind } from './AddCategoryModal'
import type {
  ApiTxn,
  Category,
  Payee,
  SourceEntry,
  StatementLink,
  TransactionUpdate,
  Txn,
  TxnActivity,
} from './types'

/** Colours a new category cycles through — the Accounts tab's own palette. */
export const CATEGORY_PALETTE = [
  '#e2554f',
  '#e0a032',
  '#34b08a',
  '#3b86dd',
  '#9a7fd0',
  '#22b8cf',
  '#d4537e',
  '#bd7a1a',
]

/** The server's refusal as a sentence to show where the action was taken: FastAPI's `{detail}`
 *  envelope unwrapped, the raw text when the body is not one. */
async function failed(res: Response, fallback: string): Promise<Error> {
  const text = await res.text().catch(() => '')
  try {
    const detail = (JSON.parse(text) as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string' && detail) return new Error(detail)
  } catch {
    /* not JSON — the text is the message */
  }
  return new Error(text || fallback)
}

/** A write landed: drop the cached reads it may have changed and tell every watcher. The
 *  Accounts tab's own hook does the same after its writes (fireMutate); this is that for a
 *  write made outside it. */
export function announceLedgerWrite(domains: string[] = ['ledger']): void {
  invalidateQueryCache(
    (key) =>
      key === '/api/accounts' ||
      key === '/api/categories' ||
      key === '/api/payees' ||
      key.startsWith('/api/transactions'),
  )
  emitDataChanged(domains)
}

export async function updateTransaction(id: string, data: TransactionUpdate): Promise<Txn> {
  const res = await authFetch(`/api/transactions/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
  if (!res.ok) throw await failed(res, 'Update failed')
  const txn = fromApiTxn((await res.json()) as ApiTxn)
  announceLedgerWrite()
  return txn
}

// The register's own cleared route — the drawer's View switch flips through it too.
export async function setTransactionCleared(id: string, cleared: boolean): Promise<Txn> {
  const res = await authFetch(`/api/transactions/${id}/cleared`, {
    method: 'PATCH',
    body: JSON.stringify({ cleared }),
  })
  if (!res.ok) throw await failed(res, 'Update failed')
  const txn = fromApiTxn((await res.json()) as ApiTxn)
  announceLedgerWrite()
  return txn
}

export async function deleteTransaction(id: string): Promise<void> {
  const res = await authFetch(`/api/transactions/${id}`, { method: 'DELETE' })
  if (!res.ok) throw await failed(res, 'Delete failed')
  announceLedgerWrite()
}

/** Delete PERMANENTLY — no undo. The ordinary delete above stays the soft delete. */
export async function purgeTransaction(id: string): Promise<void> {
  const res = await authFetch('/api/transactions/purge', {
    method: 'POST',
    body: JSON.stringify({ ids: [id] }),
  })
  if (!res.ok) throw await failed(res, 'Delete failed')
  announceLedgerWrite()
}

export async function detachTransactionSourceRef(
  txn: Txn,
  source: string,
  ref: string,
): Promise<Txn> {
  const res = await authFetch(`/api/transactions/${txn.id}/refs/detach`, {
    method: 'POST',
    body: JSON.stringify({ source, ref }),
  })
  if (!res.ok) throw await failed(res, 'Could not unlink the source')
  const body = (await res.json()) as {
    external_refs?: Array<{ source?: string | null; ref: string }>
  }
  announceLedgerWrite()
  return {
    ...txn,
    externalRefs: (body.external_refs ?? []).map((e) => ({ source: e.source ?? null, ref: e.ref })),
  }
}

export async function loadTransactionActivity(id: string): Promise<TxnActivity> {
  const res = await authFetch(`/api/transactions/${id}/activity`)
  if (!res.ok) throw new Error('No activity')
  return res.json() as Promise<TxnActivity>
}

/** Every source snapshot behind a booked row — email or connector. 404 means no provenance of
 *  either kind: a normal outcome (null), not a failure. */
export async function loadTransactionSourceEntries(id: string): Promise<SourceEntry[] | null> {
  const res = await authFetch(`/api/transactions/${id}/sources`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error('No source entries')
  const body = (await res.json()) as { entries?: unknown[] }
  return (body.entries ?? []).map((e) => fromApiSourceEntry(e as Record<string, unknown>))
}

export async function loadTransactionStatementLinks(id: string): Promise<StatementLink[]> {
  const res = await authFetch(`/api/transactions/${id}/statements`)
  if (res.status === 404) return []
  if (!res.ok) throw new Error('Could not load statement links')
  const body = (await res.json()) as { statements?: unknown[] }
  return (body.statements ?? []).map((x) => fromApiStatementLink(x as Record<string, never>))
}

/** A typed payee name on an EDIT must become a real link: an update never resolves or creates
 *  a payee from note text (only a create does), so an unknown name is created here and the
 *  real id awaited — never a placeholder. */
export async function resolveOrCreatePayeeId(
  name: string,
  payees: Pick<Payee, 'id' | 'name'>[],
): Promise<string | null> {
  const trimmed = name.trim()
  if (!trimmed) return null
  const lower = trimmed.toLowerCase()
  const existing = payees.find((p) => p.name.toLowerCase() === lower)
  if (existing) return existing.id
  const res = await authFetch('/api/payees', {
    method: 'POST',
    body: JSON.stringify({ name: trimmed }),
  })
  if (!res.ok) throw await failed(res, 'Could not create the payee')
  const created = (await res.json()) as { id: string }
  announceLedgerWrite(['payees'])
  return created.id
}

/** The server mints the id; the colour cycles the palette by how many categories exist. */
export async function createCategory(
  name: string,
  kind: CatKind,
  existingCount: number,
): Promise<Category> {
  const res = await authFetch('/api/categories', {
    method: 'POST',
    body: JSON.stringify({
      label: name.trim(),
      kind,
      color: CATEGORY_PALETTE[existingCount % CATEGORY_PALETTE.length],
    }),
  })
  if (!res.ok) throw await failed(res, 'Failed to add category')
  const category = (await res.json()) as Category
  announceLedgerWrite(['categories'])
  return category
}
