// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { isWebKit, stampPwaDisplayMode } from './pwa'

describe('isWebKit', () => {
  it.each([
    [
      'iPhone Safari',
      'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1',
      true,
    ],
    [
      'Chrome on an iPhone',
      'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1',
      true,
    ],
    [
      'Safari on a Mac',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15',
      true,
    ],
    [
      'Chrome',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      false,
    ],
    [
      'Edge',
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
      false,
    ],
    [
      'the desktop app',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) OpenNetWorth/1.0 Chrome/130.0.0.0 Electron/33.0.0 Safari/537.36',
      false,
    ],
    [
      'Firefox',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0',
      false,
    ],
  ])('knows %s', (_name, agent, expected) => {
    expect(isWebKit(agent)).toBe(expected)
  })
})

const originalMatchMedia = window.matchMedia
const originalStandalone = (navigator as Navigator & { standalone?: boolean }).standalone

afterEach(() => {
  window.matchMedia = originalMatchMedia
  Object.defineProperty(navigator, 'standalone', {
    configurable: true,
    value: originalStandalone,
  })
  delete document.documentElement.dataset.onwDisplayMode
  delete document.documentElement.dataset.onwEngine
})

describe('stampPwaDisplayMode', () => {
  it('stamps an ordinary browser tab before shell CSS chooses its viewport', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: false })
    Object.defineProperty(navigator, 'standalone', { configurable: true, value: false })

    stampPwaDisplayMode()

    expect(document.documentElement.dataset.onwDisplayMode).toBe('browser')
  })

  it('trusts the iOS standalone flag when the display-mode query misreports browser mode', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: false })
    Object.defineProperty(navigator, 'standalone', { configurable: true, value: true })

    stampPwaDisplayMode()

    expect(document.documentElement.dataset.onwDisplayMode).toBe('standalone')
  })
})
