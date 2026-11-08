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
  document.documentElement.dataset.onwEngine = isWebKit(navigator.userAgent) ? 'webkit' : 'other'
}

/** Safari's engine: every browser on an iPhone, Chrome included ("CriOS"), and Safari on a Mac.
 *  It doesn't hold your place as a lazily drawn message turns from its estimate into its real
 *  height, so scrolling up jumped 62 times in a long chat where Chromium jumped twice (measured,
 *  2026-11-08); the chat draws every message in full there. */
export function isWebKit(userAgent: string): boolean {
  return /AppleWebKit/.test(userAgent) && !/(Chrome|Chromium|Edg|Electron)\//.test(userAgent)
}
