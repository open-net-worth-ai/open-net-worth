// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { compositeOver, parseColor, topEdgeTint } from './statusBarTint'

afterEach(() => {
  document.body.innerHTML = ''
  document.documentElement.removeAttribute('style')
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

describe('parseColor', () => {
  it('reads the legacy and color(srgb) forms getComputedStyle returns', () => {
    expect(parseColor('rgba(6, 9, 16, 0.66)')).toEqual([6, 9, 16, 0.66])
    expect(parseColor('rgb(255, 255, 255)')).toEqual([255, 255, 255, 1])
    expect(parseColor('color(srgb 1 0 0.5 / 0.5)')).toEqual([255, 0, 127.5, 0.5])
    expect(parseColor('transparent')).toBeNull()
  })
})

describe('compositeOver', () => {
  it('stacks layers top-down over an opaque base', () => {
    const [r, g, b] = compositeOver([255, 255, 255, 1], [[6, 8, 12, 0.62]])
    expect([r, g, b].map(Math.round)).toEqual([101, 102, 104])
  })
})

describe('topEdgeTint', () => {
  it('is null with no full-screen overlay, so the bar keeps the theme colour', () => {
    document.documentElement.style.backgroundColor = 'rgb(255, 255, 255)'
    const sampler = box('background-color: rgb(255, 255, 255); position: fixed')
    stackAtTopEdge(sampler, box('background-color: rgb(240, 240, 240)'))
    expect(topEdgeTint(sampler)).toBeNull()
  })

  // Owner report 2026-10-31: Browse & add (its own .rd-modal-scrim) left the bar white, and the
  // phone review's deeper 82% scrim got a lighter grey than the page under it.
  it('takes whatever scrim covers the top edge, composited over the theme background', () => {
    document.documentElement.style.backgroundColor = 'rgb(255, 255, 255)'
    const sampler = box('background-color: rgb(255, 255, 255); position: fixed')
    const scrim = box('background-color: rgba(6, 8, 12, 0.82); position: fixed', fullScreen)
    stackAtTopEdge(
      sampler,
      box('background-color: transparent'),
      scrim,
      box('background-color: red'),
    )
    expect(topEdgeTint(sampler)).toBe('rgb(51, 52, 56)')
  })

  it('counts an opaque full-height card above the scrim, and a fading scrim at its opacity', () => {
    document.documentElement.style.backgroundColor = 'rgb(255, 255, 255)'
    const sampler = box('position: fixed')
    const card = box('background-color: rgb(20, 20, 20)')
    const scrim = box('background-color: rgba(0, 0, 0, 0.5); position: fixed', fullScreen)
    stackAtTopEdge(sampler, card, scrim)
    expect(topEdgeTint(sampler)).toBe('rgb(20, 20, 20)')

    const fading = box(
      'background-color: rgba(0, 0, 0, 0.5); position: fixed; opacity: 0.5',
      fullScreen,
    )
    stackAtTopEdge(sampler, fading)
    expect(topEdgeTint(sampler)).toBe('rgb(191, 191, 191)')
  })

  // iOS repaints the bar a beat after the box changes, so the box takes where a fade is heading.
  it('takes a running fade at its end state: full as it opens, gone as it closes', () => {
    document.documentElement.style.backgroundColor = 'rgb(255, 255, 255)'
    const sampler = box('position: fixed')
    const fade = (from: number, to: number) => {
      const scrim = box(
        `background-color: rgba(0, 0, 0, 0.5); position: fixed; opacity: ${from}`,
        fullScreen,
      )
      const frames = [
        { offset: 0, computedOffset: 0, opacity: String(from) },
        { offset: 1, computedOffset: 1, opacity: String(to) },
      ]
      scrim.getAnimations = () =>
        [{ playState: 'running', effect: { getKeyframes: () => frames } }] as unknown as Animation[]
      return scrim
    }
    stackAtTopEdge(sampler, fade(0.05, 1))
    expect(topEdgeTint(sampler)).toBe('rgb(128, 128, 128)')
    stackAtTopEdge(sampler, fade(0.95, 0))
    expect(topEdgeTint(sampler)).toBeNull()
  })
})
