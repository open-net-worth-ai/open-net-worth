// Statements left reading in the background ("Continue in background" in the import modal).
//
// Tracked here so the app can say when each read finishes from ANY tab (owner, 2026-10-06: "at
// least in app as a popup") — BackgroundStatementNotices polls these ids — and so its "Review"
// tap can hand the statement back to the Accounts tab, which owns the import modal. Same shape
// as platform/desktop.ts's native-import queue: a module store plus subscribe/take.
//
// The tracked list is kept in localStorage so a reload, or a tab switch that unmounts the
// Accounts tab, does not lose a read the owner is waiting on.

export interface BackgroundStatement {
  id: string
  accountId: string | null
  filename?: string
  /** When "Continue in background" was tapped — the notice stack shows the read as under way
   *  for a few seconds from here, in the same spot its "Ready to review" will take. */
  startedAt?: number
}

const KEY = 'onw.backgroundStatements'
const listeners = new Set<() => void>()

function read(): BackgroundStatement[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(parsed)
      ? parsed.filter(
          (s): s is BackgroundStatement =>
            !!s && typeof s === 'object' && typeof (s as BackgroundStatement).id === 'string',
        )
      : []
  } catch {
    return []
  }
}

function write(list: BackgroundStatement[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* storage full or blocked — tracking is a convenience, never worth an error */
  }
  listeners.forEach((listener) => listener())
}

export function trackBackgroundStatement(statement: BackgroundStatement): void {
  write([...read().filter((s) => s.id !== statement.id), statement])
}

export function untrackBackgroundStatement(id: string): void {
  const list = read()
  if (list.some((s) => s.id === id)) write(list.filter((s) => s.id !== id))
}

export function backgroundStatements(): BackgroundStatement[] {
  return read()
}

export function subscribeBackgroundStatements(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// ── "Review" hand-off: the popup asks, the Accounts tab opens the statement's review ──────
let pendingReview: BackgroundStatement | null = null
const reviewListeners = new Set<() => void>()

export function requestStatementReview(statement: BackgroundStatement): void {
  pendingReview = statement
  reviewListeners.forEach((listener) => listener())
}

export function takeStatementReview(): BackgroundStatement | null {
  const pending = pendingReview
  pendingReview = null
  return pending
}

export function subscribeStatementReview(listener: () => void): () => void {
  reviewListeners.add(listener)
  return () => {
    reviewListeners.delete(listener)
  }
}
