// NewVersionBanner — an installed phone app RESUMES the page it already has when you switch
// back to it; it only loads a new build after a full close. So a deploy looked "not fixed" on
// the owner's phone while the server was already serving the fix. Whenever the app comes back
// to the foreground (and every few minutes while open), compare the entry files (script and
// stylesheet) the server serves now with the ones running, and offer a one-tap reload when they differ. index.html is
// served no-store, so the fetch is always current. Never reloads on its own: a composer draft
// or a half-filled form must not vanish.
import { useEffect, useState } from 'react'

// The build's entry files: its script AND its stylesheet. A style-only change rebuilds the CSS
// under a new name while the script keeps its old one, so comparing the script alone missed it.
const ENTRY = /\/assets\/index-[\w-]+\.(?:js|css)/g
const EVERY_MS = 5 * 60_000

function runningEntries(): Set<string> {
  const urls = [
    ...document.querySelectorAll<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]'),
  ].map((s) => s.src)
  urls.push(
    ...[
      ...document.querySelectorAll<HTMLLinkElement>(
        'link[rel="stylesheet"][href*="/assets/index-"]',
      ),
    ].map((l) => l.href),
  )
  return new Set(urls.map((u) => new URL(u, window.location.href).pathname))
}

async function servedEntries(): Promise<string[]> {
  const r = await fetch('/', { cache: 'no-store', credentials: 'same-origin' })
  if (!r.ok) return []
  return (await r.text()).match(ENTRY) ?? []
}

export function NewVersionBanner() {
  const [stale, setStale] = useState(false)
  // A reload takes a moment to show (longest on a phone); the button says it's happening.
  const [reloading, setReloading] = useState(false)
  useEffect(() => {
    const running = runningEntries()
    if (!running.size) return // the dev server has no hashed entry to compare
    let checking = false
    const check = async () => {
      if (checking || document.visibilityState !== 'visible') return
      checking = true
      try {
        // Stale when the server lists an entry file this page did not load.
        const served = await servedEntries()
        if (served.some((path) => !running.has(path))) setStale(true)
      } catch {
        // Offline or the server is restarting — the next return to the app tries again.
      } finally {
        checking = false
      }
    }
    document.addEventListener('visibilitychange', check)
    window.addEventListener('focus', check)
    window.addEventListener('pageshow', check)
    const timer = window.setInterval(check, EVERY_MS)
    return () => {
      document.removeEventListener('visibilitychange', check)
      window.removeEventListener('focus', check)
      window.removeEventListener('pageshow', check)
      window.clearInterval(timer)
    }
  }, [])
  if (!stale) return null
  return (
    // data-portal-layer: tappable over an open modal (a Radix dialog makes everything outside
    // it inert, and would read the tap as "dismiss") — see ui/Modal.tsx's isOutsideExempt.
    <div className="app-update" role="status" data-portal-layer>
      <span>A new version is ready</span>
      <button
        type="button"
        className="app-update-btn"
        aria-label={reloading ? 'Reloading' : 'Reload'}
        aria-busy={reloading || undefined}
        disabled={reloading}
        onClick={() => {
          setReloading(true)
          // Let the spinner paint before the page goes.
          requestAnimationFrame(() => window.setTimeout(() => window.location.reload(), 0))
        }}
      >
        {reloading ? <span className="ui-btn__spinner" aria-hidden="true" /> : 'Reload'}
      </button>
    </div>
  )
}
