// The screen stays on for a call: the phone locked itself partway through, with the call's screen
// and its approvals gone with it (owner, 2026-11-08). A browser can't do this, or refuses (low
// battery): the call simply goes on without it.

let wanted = false
let lock: WakeLockSentinel | null = null
let asking = false

async function take(): Promise<void> {
  if (!wanted || lock || asking) return
  if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return
  asking = true
  try {
    const taken = await navigator.wakeLock.request('screen')
    if (!wanted) {
      void taken.release().catch(() => {})
      return
    }
    lock = taken
    taken.addEventListener('release', () => {
      if (lock === taken) lock = null
    })
  } catch {
    // Refused: nothing to undo, and nothing the owner could do about it mid-call.
  } finally {
    asking = false
  }
}

// The browser drops the lock whenever the page is hidden, so coming back takes it again.
function onVisibility(): void {
  void take()
}

export function keepScreenOn(on: boolean): void {
  if (on === wanted) return
  wanted = on
  if (on) {
    document.addEventListener('visibilitychange', onVisibility)
    void take()
    return
  }
  document.removeEventListener('visibilitychange', onVisibility)
  const held = lock
  lock = null
  void held?.release().catch(() => {})
}
