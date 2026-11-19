// A call the page itself was taken away from — reloaded, or closed and reopened by the phone —
// leaves nothing behind to say so: the call's state lived in this page, and the next one opened
// idle as if no call had been on (owner's phone, 2026-11-13: "it just ends and doesn't show
// anything"). So a live call keeps a mark here, refreshed while it runs and cleared however it
// ends in the page. A mark the next page finds, that nothing goes on refreshing, is a call cut off.

const KEY = 'onw_voice_call_on'
/** Longer than the gap between refreshes, so another tab's live call keeps it moving. */
export const CUT_OFF_SETTLE_MS = 4_000
/** Older than this is no longer news worth a notice. */
const STALE_MS = 10 * 60_000

interface Mark {
  sessionId: string
  at: number
}

function read(): Mark | null {
  try {
    const raw = localStorage.getItem(KEY)
    const mark = raw ? (JSON.parse(raw) as Mark) : null
    return mark && typeof mark.at === 'number' && typeof mark.sessionId === 'string' ? mark : null
  } catch {
    return null
  }
}

export function markCallOn(sessionId: string): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ sessionId, at: Date.now() }))
  } catch {
    // Storage blocked: a cut-off call just goes unexplained, as before.
  }
}

export function clearCallOn(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // Nothing was remembered.
  }
}

/** On a fresh page: if a call was on when the last one went away, says so once. Waits a moment
 *  first, as a call live in another tab keeps refreshing its mark, and that is not this one. */
export function watchForCutOff(onCutOff: (sessionId: string) => void): void {
  const first = read()
  if (!first) return
  if (Date.now() - first.at > STALE_MS) {
    clearCallOn()
    return
  }
  window.setTimeout(() => {
    const now = read()
    if (!now || now.at !== first.at) return // ended, or another tab's call is still on
    clearCallOn()
    onCutOff(first.sessionId)
  }, CUT_OFF_SETTLE_MS)
}
