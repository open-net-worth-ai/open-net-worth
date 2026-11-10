// Detects the installed home-screen app (standalone display mode), as opposed to a regular
// browser tab — `display-mode: standalone` is the modern, cross-browser signal;
// `navigator.standalone` is iOS Safari's own older/more reliable flag for the same thing,
// checked as a fallback since not every iOS version reports `display-mode` accurately for
// home-screen web apps.
export function isStandalonePwa(): boolean {
  if (typeof window === 'undefined') return false
  const mediaMatch = window.matchMedia?.('(display-mode: standalone)').matches ?? false
  const iosFlag = (navigator as Navigator & { standalone?: boolean }).standalone === true
  return mediaMatch || iosFlag
}

export function stampPwaDisplayMode(): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.onwDisplayMode = isStandalonePwa() ? 'standalone' : 'browser'
  document.documentElement.dataset.onwDevice = /iPhone/.test(navigator.userAgent)
    ? 'iphone'
    : 'other'
  document.documentElement.dataset.onwEngine = isWebKit(navigator.userAgent) ? 'webkit' : 'other'
}

/** Safari's engine: every browser on an iPhone, Chrome included ("CriOS"), and Safari on a Mac.
 *  It doesn't hold your place as a lazily drawn message turns from its estimate into its real
 *  height, so scrolling up jumped 62 times in a long chat where Chromium jumped twice (measured,
 *  2026-11-08); the chat draws every message in full there. */
export function isWebKit(userAgent: string): boolean {
  return /AppleWebKit/.test(userAgent) && !/(Chrome|Chromium|Edg|Electron)\//.test(userAgent)
}

/** An installed app on an iPhone keeps its portrait layout offset after the device rotates:
 *  the whole page sits a status band lower until something makes iOS lay it out again — in
 *  the owner's screen recording a touch did, ~5s later. Nudge that layout pass ourselves on
 *  every rotation: re-assert the scroll origin and toggle the root's height for one frame. */
export function relayoutAfterRotation(): void {
  if (typeof window === 'undefined') return
  if (document.documentElement.dataset.onwDevice !== 'iphone') return
  const nudge = () =>
    requestAnimationFrame(() => {
      const root = document.documentElement
      window.scrollTo(0, 0)
      root.style.minHeight = 'calc(100% + 1px)'
      requestAnimationFrame(() => {
        root.style.minHeight = ''
        window.scrollTo(0, 0)
      })
    })
  // iOS settles the rotated layout at an unannounced moment, so nudge right away, a few times
  // while it settles, and on each resize it reports — the first one after it settles wins.
  let lastWidth = window.innerWidth
  const onRotate = () => {
    nudge()
    for (const ms of [60, 150, 300, 500]) window.setTimeout(nudge, ms)
  }
  const onResize = () => {
    if (window.innerWidth === lastWidth) return
    lastWidth = window.innerWidth
    onRotate()
  }
  screen.orientation?.addEventListener?.('change', onRotate)
  window.addEventListener('orientationchange', onRotate)
  window.addEventListener('resize', onResize)
  window.visualViewport?.addEventListener('resize', onResize)
}
