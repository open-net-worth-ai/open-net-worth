// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { overlayCoversTopEdge, parseAlpha } from './statusBarTint'

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

function box(style: string, rect: Partial<DOMRect> = {}): HTMLElement {
  const el = document.createElement('div')
  el.setAttribute('style', style)
  el.getBoundingClientRect = () => ({ top: 0, width: 0, height: 0, ...rect }) as DOMRect
  document.body.appendChild(el)
  return el
}

function stackAtTopEdge(...els: Element[]) {
  document.elementsFromPoint = vi.fn().mockReturnValue(els)
}

const fullScreen = { top: 0, width: window.innerWidth, height: window.innerHeight }

describe('parseAlpha', () => {
  it('reads the alpha of every form getComputedStyle returns', () => {
    expect(parseAlpha('rgba(6, 9, 16, 0.66)')).toBe(0.66)
    expect(parseAlpha('rgb(255, 255, 255)')).toBe(1)
    expect(parseAlpha('color(srgb 1 0 0.5 / 0.5)')).toBe(0.5)
    expect(parseAlpha('color(srgb 1 0 0.5)')).toBe(1)
    expect(parseAlpha('rgba(0, 0, 0, 0)')).toBe(0)
    expect(parseAlpha('transparent')).toBe(0)
  })
})

// Owner report 2026-10-31: the bar stayed white over a dimmed modal, then — tinted to match —
// changed a beat after the page. The box now steps aside so iOS finds the scrim itself.
describe('overlayCoversTopEdge', () => {
  it('is false for page content, so the box keeps blocking the iOS 27 blur', () => {
    const sampler = box('position: fixed')
    stackAtTopEdge(sampler, box('background-color: rgb(240, 240, 240)'))
    expect(overlayCoversTopEdge(sampler)).toBe(false)
  })

  it('is true for a full-screen fixed scrim under a transparent card', () => {
    const sampler = box('position: fixed')
    const scrim = box('background-color: rgba(6, 8, 12, 0.62); position: fixed', fullScreen)
    stackAtTopEdge(sampler, box('background-color: transparent'), scrim)
    expect(overlayCoversTopEdge(sampler)).toBe(true)
  })

  it('ignores a fixed header and a transparent full-screen layer', () => {
    const sampler = box('position: fixed')
    const header = box('background-color: white; position: fixed', {
      top: 0,
      width: 400,
      height: 60,
    })
    const clear = box('background-color: transparent; position: fixed', fullScreen)
    stackAtTopEdge(sampler, header, clear)
    expect(overlayCoversTopEdge(sampler)).toBe(false)
  })
})
