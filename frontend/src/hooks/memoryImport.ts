// The memory import and export calls (Settings → Memory). Import is two steps on the server —
// a preview that writes nothing, then an apply of the lines the user kept — and the export is a
// zip fetched with the session's auth, then handed to the browser as a download.
import { authFetch } from '../api/client'

export const IMPORT_MAX_CHARS = 20_000

export interface ImportItem {
  line: string
  destination: 'profile' | 'instructions' | 'memory' | 'skip'
  reason: string
  field?: string
  value?: string
  section?: string
  topic?: string | null
  content?: string
  sensitive?: boolean
}

export interface ImportResult {
  applied: { profile: number; instructions: number; memory: number }
  rejected: { line: string; destination: string; reason: string }[]
}

async function failure(r: Response, fallback: string): Promise<Error> {
  const body = (await r.json().catch(() => ({}))) as { detail?: unknown }
  return new Error(typeof body.detail === 'string' ? body.detail : `${fallback} (HTTP ${r.status})`)
}

export async function getImportPrompt(): Promise<string> {
  const r = await authFetch('/api/memory/import/prompt')
  if (!r.ok) throw await failure(r, 'Could not load the prompt')
  return ((await r.json()) as { prompt: string }).prompt
}

async function post<T>(path: string, body: unknown, fallback: string): Promise<T> {
  const r = await authFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await failure(r, fallback)
  return (await r.json()) as T
}

export async function previewImport(text: string): Promise<ImportItem[]> {
  const out = await post<{ items: ImportItem[] }>(
    '/api/memory/import/preview',
    { text },
    'Could not read that',
  )
  return out.items
}

export const applyImport = (items: ImportItem[]) =>
  post<ImportResult>('/api/memory/import/apply', { items }, 'Could not import')

/** Download the memory zip. A plain link would skip the session's auth, so it is fetched, turned
 *  into an object URL and clicked. */
export async function downloadMemoryExport(): Promise<void> {
  const r = await authFetch('/api/memory/export')
  if (!r.ok) throw await failure(r, 'Could not export')
  const name = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') ?? '')?.[1]
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name ?? 'memory.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
