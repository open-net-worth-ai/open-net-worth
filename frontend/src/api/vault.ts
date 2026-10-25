import { emitDataChanged } from '../lucius/bus'
import { authFetch } from './client'
import type { ArtifactType } from '../types/chat'

// Documents (internally the Vault) — one place for the files a user keeps. Mirrors
// backend/api/vault.py. It
// is a VIEW over existing records (kept chat files, saved artifacts, statements, receipts),
// so every item points at its own record's existing file route (`file_url`); artifacts have
// none and open in the sandboxed artifact viewer instead.

export type VaultKind = 'file' | 'artifact' | 'statement' | 'receipt' | 'asset'
export interface VaultItem {
  id: string
  kind: VaultKind
  source_id: string
  title: string
  filename: string
  media_type: string | null
  size_bytes: number | null
  added_at: string | null
  year: number | null
  private: boolean
  file_url: string | null
  origin?: 'vault' | 'chat'
  artifact_type?: ArtifactType
  /** A saved artifact whose conversation is gone — removing it deletes it. */
  detached?: boolean
  account_id?: string | null
  account_name?: string | null
  statement_date?: string | null
  transaction_date?: string | null
  /** A file attached to an owned asset — opened with /assets?id=<asset_id>. */
  asset_id?: string | null
  asset_name?: string | null
  /** A receipt's transaction — opened with /accounts?txn=<id>. */
  transaction_id?: string | null
  /** The user's own note on it — Lucius reads it, Private or not. */
  note?: string | null
  /** The user's own tags on it — how they group items across kinds. */
  tags?: string[]
}

export type VaultCounts = Record<'all' | VaultKind, number>

export interface VaultListing {
  items: VaultItem[]
  counts: VaultCounts
}

export interface VaultFileDetails {
  title?: string | null
  year?: number | null
  private?: boolean
}

async function detailOf(r: Response, fallback: string): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: unknown }
    return typeof body.detail === 'string' && body.detail ? body.detail : fallback
  } catch {
    return fallback
  }
}

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
  if (!r.ok) throw new Error(await detailOf(r, `${what} failed (${r.status})`))
  return (await r.json()) as T
}

export async function fetchVault(): Promise<VaultListing> {
  return jsonOrThrow<VaultListing>(await authFetch('/api/vault'), 'Loading documents')
}

export async function addVaultFile(file: File, details: VaultFileDetails = {}): Promise<VaultItem> {
  const form = new FormData()
  form.append('file', file)
  if (details.title) form.append('title', details.title)
  if (details.year != null) form.append('year', String(details.year))
  if (details.private) form.append('private', 'true')
  const r = await authFetch('/api/vault/files', { method: 'POST', body: form })
  return (await jsonOrThrow<{ item: VaultItem }>(r, `Adding ${file.name}`)).item
}

export async function updateVaultFile(id: string, changes: VaultFileDetails): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })
  return (await jsonOrThrow<{ item: VaultItem }>(r, 'Saving the change')).item
}

/** One page of one sheet of a CSV/Excel item from `offset`, as the file has it. `sheets` is
 *  empty for a CSV; `total_rows` is null when an .xlsx records no size. */
export interface VaultTable {
  sheets: string[]
  sheet: string
  rows: string[][]
  columns: number
  offset: number
  has_more: boolean
  total_rows: number | null
  more_columns: boolean
}

/** One item by its Vault id (`statement-<id>` for a statement) — what a single file preview
 *  needs without listing every document. */
export async function fetchVaultItem(id: string): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}`)
  return (await jsonOrThrow<{ item: VaultItem }>(r, 'Opening the file')).item
}

export async function fetchVaultTable(id: string, sheet?: string, offset = 0): Promise<VaultTable> {
  const q = new URLSearchParams()
  if (sheet) q.set('sheet', sheet)
  if (offset) q.set('offset', String(offset))
  const qs = q.toString() ? `?${q}` : ''
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}/table${qs}`)
  return jsonOrThrow<VaultTable>(r, 'Previewing the file')
}

/** Renames any item. `null` puts a file back to its file name and a statement or receipt
 *  back to its automatic name; an artifact or asset file needs a name. */
export async function renameVaultItem(id: string, title: string | null): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}/name`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  return announceAsset(await jsonOrThrow<{ item: VaultItem }>(r, 'Renaming')).item
}

/** An asset file's name and note are its asset page's too — that page refreshes on `assets`. */
function announceAsset<T extends { item: VaultItem }>(body: T): T {
  if (body.item.kind === 'asset') emitDataChanged(['assets'])
  return body
}

/** Writes the user's note on any item; `null` removes it. */
export async function setVaultNote(id: string, note: string | null): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}/note`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  })
  return announceAsset(await jsonOrThrow<{ item: VaultItem }>(r, 'Saving the note')).item
}

/** Sets the user's tags on any item; `[]` clears them. */
export async function setVaultTags(id: string, tags: string[]): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}/tags`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags }),
  })
  return announceAsset(await jsonOrThrow<{ item: VaultItem }>(r, 'Saving the tags')).item
}

/** `deleted` — the file's only home was Documents; `removed` — it stays where it came from.
 *  `deleteFile` deletes one of the user's own files for good, even one kept from a chat. */
export async function removeVaultItem(
  id: string,
  options: { deleteFile?: boolean } = {},
): Promise<{ action: 'deleted' | 'removed' }> {
  const query = options.deleteFile ? '?delete_file=true' : ''
  const r = await authFetch(`/api/vault/items/${encodeURIComponent(id)}${query}`, {
    method: 'DELETE',
  })
  return jsonOrThrow<{ action: 'deleted' | 'removed' }>(r, 'Removing it')
}

export async function saveArtifactToVault(artifactId: string): Promise<VaultItem> {
  const r = await authFetch(`/api/vault/artifacts/${encodeURIComponent(artifactId)}`, {
    method: 'PUT',
  })
  return (await jsonOrThrow<{ item: VaultItem }>(r, 'Saving to Documents')).item
}

export async function removeArtifactFromVault(artifactId: string): Promise<void> {
  const r = await authFetch(`/api/vault/artifacts/${encodeURIComponent(artifactId)}`, {
    method: 'DELETE',
  })
  await jsonOrThrow<unknown>(r, 'Removing from Documents')
}
