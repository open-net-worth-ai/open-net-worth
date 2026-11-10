// iOS tints the installed app's status bar from #status-bar-sampler (index.html), the fixed box
// at the top edge. The bar is outside the page, so a modal's scrim can't dim it, and the sampler
// sits above every scrim: a bright band over a dimmed page. While a full-screen fixed overlay
// covers the top edge, the sampler takes the colour actually painted there — every layer down to
// that overlay, over the theme background — and inherits the theme again once it closes. Read
// from the DOM rather than mirrored per scrim: the app has a dozen, each with its own alpha.
import { isStandalonePwa } from './pwa'

export type Rgba = [number, number, number, number]

export function parseColor(value: string): Rgba | null {
  const srgb = value.match(/^color\(srgb ([^)]+)\)$/)
  const legacy = value.match(/^rgba?\(([^)]+)\)$/)
  const body = srgb?.[1] ?? legacy?.[1]
  if (!body) return null
  const [r, g, b, a = 1, ...rest] = body
    .split(/[\s,/]+/)
    .filter(Boolean)
    .map(Number)
  if (r === undefined || g === undefined || b === undefined || rest.length) return null
  if ([r, g, b, a].some(Number.isNaN)) return null
  const scale = srgb ? 255 : 1
  return [r * scale, g * scale, b * scale, a]
}

/** `layers` top → bottom, each already at its effective alpha; `base` is opaque. */
export function compositeOver(base: Rgba, layers: Rgba[]): Rgba {
  return layers.reduceRight<Rgba>(
    (acc, [r, g, b, a]) => [
      r * a + acc[0] * (1 - a),
      g * a + acc[1] * (1 - a),
      b * a + acc[2] * (1 - a),
      1,
    ],
    base,
  )
}

// Where a running fade is heading, not where it is: iOS repaints the bar a beat after the box
// changes, so the box must take a scrim's final colour as its fade starts, not when it ends.
function restingOpacity(el: Element): number {
  let opacity = Number(getComputedStyle(el).opacity || 1)
  for (const anim of el.getAnimations?.() ?? []) {
    if (anim.playState !== 'running') continue
    const frames = (anim.effect as KeyframeEffect | null)?.getKeyframes?.() ?? []
    const last = frames.filter((f) => f.opacity !== undefined).at(-1)
    if (last) opacity = (last.computedOffset ?? 1) >= 1 ? Number(last.opacity) : 1
  }
  return opacity
}

function effectiveAlpha(el: Element, alpha: number): number {
  let a = alpha
  for (let n: Element | null = el; n && n !== document.documentElement; n = n.parentElement) {
    a *= restingOpacity(n)
  }
  return a
}

function coversViewport(el: Element, style: CSSStyleDeclaration): boolean {
  if (style.position !== 'fixed') return false
  const r = el.getBoundingClientRect()
  return r.top <= 1 && r.height >= window.innerHeight * 0.9 && r.width >= window.innerWidth * 0.5
}

/** The colour painted at the top edge while an overlay covers it, else null. */
export function topEdgeTint(sampler: Element): string | null {
  const root = document.documentElement
  const base = parseColor(getComputedStyle(root).backgroundColor)
  if (!base) return null
  const layers: Rgba[] = []
  for (const el of document.elementsFromPoint(window.innerWidth / 2, 1)) {
    if (el === sampler || el === root || el === document.body) continue
    const style = getComputedStyle(el)
    const bg = parseColor(style.backgroundColor)
    const alpha = bg ? effectiveAlpha(el, bg[3]) : 0
    if (!bg || alpha <= 0) continue
    layers.push([bg[0], bg[1], bg[2], alpha])
    if (coversViewport(el, style)) {
      const [r, g, b] = compositeOver([base[0], base[1], base[2], 1], layers).map(Math.round)
      return `rgb(${r}, ${g}, ${b})`
    }
  }
  return null
}

export function watchStatusBarTint(): void {
  const sampler = document.getElementById('status-bar-sampler')
  if (!sampler || !isStandalonePwa()) return
  // Straight from the observer, not a frame later: the box changes in the scrim's own frame.
  const sync = () => {
    const next = topEdgeTint(sampler) ?? ''
    if (sampler.style.backgroundColor !== next) sampler.style.backgroundColor = next
  }
  new MutationObserver(sync).observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'data-state', 'style'],
  })
  for (const type of ['animationstart', 'animationend', 'transitionend']) {
    document.addEventListener(type, sync, true)
  }
  window.addEventListener('resize', sync)
}
