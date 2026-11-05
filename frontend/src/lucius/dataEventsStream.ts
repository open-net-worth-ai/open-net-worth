// dataEventsStream — the app-wide GET /api/events subscription (backend core/data_events.py).
//
// A chat turn streams `data_changed` only to the tab driving it; a write from anywhere else
// (Claude Desktop over MCP, Telegram, a scheduled Automation, another tab) arrives HERE and is
// re-emitted on the same bus, so every screen refreshes the same way. Bursts (a bulk import
// writes per call) are merged into one emit. After a dropped connection the first reconnect
// emits '*': whatever was written during the gap was never heard, so everything refreshes.
import { authFetch } from '../api/client'
import { emitDataChanged, emitScreenRequest } from './bus'

const MERGE_MS = 250
const MAX_BACKOFF_MS = 15_000

export function connectDataEvents(): () => void {
  const ctrl = new AbortController()
  let pending = new Set<string>()
  let flushTimer: ReturnType<typeof setTimeout> | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let everConnected = false
  let failures = 0

  const queue = (domains: string[]) => {
    for (const d of domains) pending.add(d)
    if (flushTimer !== null) return
    flushTimer = setTimeout(() => {
      flushTimer = null
      const batch = [...pending]
      pending = new Set()
      if (batch.length) emitDataChanged(batch)
    }, MERGE_MS)
  }

  const run = async () => {
    try {
      const res = await authFetch('/api/events', { signal: ctrl.signal })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      if (everConnected) queue(['*'])
      everConnected = true
      failures = 0
      await readEvents(res.body, queue)
    } catch {
      if (ctrl.signal.aborted) return
      failures += 1
    }
    if (ctrl.signal.aborted) return
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(failures, 4))
    retryTimer = setTimeout(() => void run(), delay)
  }
  void run()

  return () => {
    ctrl.abort()
    if (flushTimer !== null) clearTimeout(flushTimer)
    if (retryTimer !== null) clearTimeout(retryTimer)
  }
}

export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onDomains: (domains: string[]) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return
    buffer += decoder.decode(value, { stream: true })
    let cut = buffer.indexOf('\n\n')
    while (cut !== -1) {
      const frame = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          const event = JSON.parse(line.slice(5).trim()) as {
            type?: string
            domains?: unknown
            request_id?: unknown
            session_id?: unknown
          }
          if (event.type === 'data_changed' && Array.isArray(event.domains)) {
            onDomains(event.domains.filter((d): d is string => typeof d === 'string'))
          } else if (
            event.type === 'screen_request' &&
            typeof event.request_id === 'string' &&
            typeof event.session_id === 'string'
          ) {
            emitScreenRequest({ request_id: event.request_id, session_id: event.session_id })
          }
        } catch {
          /* a malformed frame is dropped, never fatal to the stream */
        }
      }
      cut = buffer.indexOf('\n\n')
    }
  }
}
