// iOS tints the installed app's status bar from #status-bar-sampler (index.html), the fixed box
// at the top edge. It sits above every scrim, so an open modal left a bright band over a dimmed
// page — and re-tinting it to the scrim's colour lagged the page visibly, iOS repainting the bar
// a beat after the box changed. While a full-screen fixed overlay covers the top edge the box
// steps aside, so the scrim itself is what iOS finds there and extends under the bar; it comes
// back once the overlay closes. Read from the DOM: the app has a dozen scrims.
import { isStandalonePwa } from './pwa'

export function parseAlpha(value: string): number {
  if (value === 'transparent') return 0
  const body = value.match(/^(?:rgba?|color)\(([^)]+)\)$/)?.[1]
  const parts = body?.split(/[\s,/]+/).filter(Boolean) ?? []
  const alpha = value.startsWith('color(') ? parts[4] : parts[3]
  return body ? Number(alpha ?? 1) : 0
}

function coversViewport(el: Element, style: CSSStyleDeclaration): boolean {
  if (style.position !== 'fixed') return false
  const r = el.getBoundingClientRect()
  return r.top <= 1 && r.height >= window.innerHeight * 0.9 && r.width >= window.innerWidth * 0.5
}

/** A full-screen fixed overlay with a visible background is painted at the top edge. */
export function overlayCoversTopEdge(sampler: Element): boolean {
  for (const el of document.elementsFromPoint(window.innerWidth / 2, 1)) {
    if (el === sampler) continue
    const style = getComputedStyle(el)
    if (parseAlpha(style.backgroundColor) > 0 && coversViewport(el, style)) return true
  }
  return false
}

export function watchStatusBarTint(): void {
  const sampler = document.getElementById('status-bar-sampler')
  if (!sampler || !isStandalonePwa()) return
  // Straight from the observer, not a frame later: the box must leave in the scrim's own frame.
  const sync = () => {
    const next = overlayCoversTopEdge(sampler) ? 'none' : ''
    if (sampler.style.display !== next) sampler.style.display = next
  }
  new MutationObserver(sync).observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'data-state', 'style'],
  })
  for (const type of ['animationend', 'transitionend']) {
    document.addEventListener(type, sync, true)
  }
  window.addEventListener('resize', sync)
}
