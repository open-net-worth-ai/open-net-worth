/**
 * @vitest-environment jsdom
 *
 * Per-device theme preference (src/theme.ts) + generated palette overrides.
 * Specs: docs/superpowers/specs/2026-07-21-dark-light-theme-design.md and
 * docs/superpowers/specs/2026-08-11-light-theme-design.md
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  THEME_STORAGE_KEY,
  getThemePreference,
  resolveTheme,
  setThemePreference,
  initTheme,
  subscribeTheme,
  useTheme,
} from '../theme'
import { act, renderHook } from '@testing-library/react'
import { generateCss } from '../../scripts/build-tokens.mjs'
import { tokenGroups } from '../design/tokens.source.mjs'

const SOURCE_ROOT = join(process.cwd(), 'src')

function cssSources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return cssSources(path)
    if (!entry.name.endsWith('.css') || entry.name === 'index.tokens.generated.css') return []
    return [path]
  })
}

function metaThemeColor(): string | null {
  return document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.querySelector('meta[name="theme-color"]')?.remove()
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'theme-color')
  meta.setAttribute('content', '#0e1014')
  document.head.appendChild(meta)
})

describe('theme preference', () => {
  it('defaults to following the system when nothing is stored or the value is garbage', () => {
    // Changed 2026-08-23: the default was 'dark', which imposed a dark app on someone
    // running a light desktop before they had expressed any preference at all. An explicit
    // pick still wins forever — this only governs the never-chosen case.
    expect(getThemePreference()).toBe('system')
    localStorage.setItem(THEME_STORAGE_KEY, 'neon')
    expect(getThemePreference()).toBe('system')
  })

  it('an explicit choice still beats the system', () => {
    setThemePreference('dark')
    expect(getThemePreference()).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('persists and applies Warm as a direct named theme', () => {
    setThemePreference('warm')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('warm')
    expect(document.documentElement.getAttribute('data-theme')).toBe('warm')
    expect(metaThemeColor()).toBe('#edeae3')
    setThemePreference('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(metaThemeColor()).toBe('#0e1014')
  })

  it('applies Light and Warm as direct themes without a hidden variant', () => {
    setThemePreference('light')

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(metaThemeColor()).toBe('#ffffff')

    setThemePreference('warm')
    expect(document.documentElement.getAttribute('data-theme')).toBe('warm')
    expect(metaThemeColor()).toBe('#edeae3')
  })

  it("resolves 'system' via prefers-color-scheme (dark without matchMedia support)", () => {
    expect(resolveTheme('system')).toBe('dark')
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn() }),
    )
    expect(resolveTheme('system')).toBe('light')
    vi.unstubAllGlobals()
  })

  it('initTheme stamps the attribute and notifies subscribers on changes', () => {
    initTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    const seen: string[] = []
    const unsub = subscribeTheme(() => {
      seen.push(document.documentElement.getAttribute('data-theme') ?? '')
    })
    setThemePreference('warm')
    expect(seen).toEqual(['warm'])
    unsub()
    setThemePreference('dark')
    expect(seen).toEqual(['warm'])
  })

  it('useTheme reports the APPLIED theme, never an OS flip whose change event has not fired', () => {
    // matchMedia flips before its change event fires. A render in that gap used to see the new
    // theme while data-theme (and every CSS var a canvas chart reads) was still the old one —
    // the chart re-coloured itself with the OLD palette (a white light-theme grid on a dark
    // page), and the event's re-notify then changed nothing, so it never re-rendered.
    let osLight = false
    const handlers: Array<() => void> = []
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        get matches() {
          return osLight
        },
        addEventListener: (_: string, fn: () => void) => handlers.push(fn),
      })),
    )
    initTheme()
    const { result, rerender } = renderHook(() => useTheme())
    expect(result.current.resolved).toBe('dark')

    osLight = true
    rerender()
    expect(result.current.resolved).toBe('dark')

    act(() => handlers.forEach((fn) => fn()))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(result.current.resolved).toBe('light')
    vi.unstubAllGlobals()
  })
})

describe('theme token codegen', () => {
  it('emits every forked token under the direct Warm block, and only forked ones', () => {
    const css = generateCss()
    const warmBlock = css.split(":root[data-theme='warm'] {")[1]?.split('\n}')[0] ?? ''
    for (const group of tokenGroups) {
      for (const t of group.tokens) {
        const [name, , warm] = t
        if (t.length > 2) {
          expect(warmBlock).toContain(`--${name}: ${warm};`)
        } else {
          expect(warmBlock).not.toContain(`--${name}:`)
        }
      }
    }
  })

  it('only color/shadow tokens fork, and both blocks declare color-scheme', () => {
    for (const group of tokenGroups) {
      const forked = group.tokens.some((t) => t.length > 2)
      if (forked) expect(['color', 'shadow']).toContain(group.category)
    }
    const css = generateCss()
    expect(css).toContain('color-scheme: dark;')
    expect(css).toContain('color-scheme: light;')
  })

  it('emits Light as a complete direct theme with Warm fallbacks', () => {
    const css = generateCss()
    const selector = ":root[data-theme='light'] {"
    const lightBlock = css.split(selector)[1]?.split('\n}')[0] ?? ''

    expect(lightBlock).toContain('--bg: #ffffff;')
    expect(lightBlock).toContain('--surface: #ffffff;')
    expect(lightBlock).toContain('--surface2: #ffffff;')
    expect(lightBlock).toContain('--bg-2: #f5f5f4;')
    expect(lightBlock).toContain('--line: #e2e2e2;')
    expect(lightBlock).toContain('--line-strong: #d2d2d2;')
    // --panel is the rail/composer/dock step, and now means the SAME thing in every theme:
    // Light's panel shares the page white while borders retain visual separation.
    expect(lightBlock).toContain('--panel: #ffffff;')
    expect(lightBlock).not.toMatch(/#(?:edeae3|f5f3ec|fbf9f4|ddd8cc|e6e2d8|c8c2b3|f8f6f0|d5cfc1)/i)
    for (const group of tokenGroups) {
      for (const token of group.tokens) {
        const [name, , warm, light] = token
        if (token.length > 2) expect(lightBlock).toContain(`--${name}: ${light ?? warm};`)
        else expect(lightBlock).not.toContain(`--${name}:`)
      }
    }
    expect([...css.matchAll(/:root\[data-theme='([^']+)'\]/g)].map((match) => match[1])).toEqual([
      'warm',
      'light',
    ])
    expect(css).not.toContain('data-theme-variant')
  })

  it('keeps bright-theme hierarchy centralized and visible in every page stylesheet', () => {
    const pageCss = cssSources(SOURCE_ROOT)
      .map((path) => `${path}\n${readFileSync(path, 'utf8')}`)
      .join('\n')
    const settingsCss = readFileSync(
      join(SOURCE_ROOT, 'components/tabs/settings/settings.css'),
      'utf8',
    )
    const accountsCss = readFileSync(
      join(SOURCE_ROOT, 'components/tabs/accounts/accounts.css'),
      'utf8',
    )
    const luciusCss = readFileSync(join(SOURCE_ROOT, 'lucius.css'), 'utf8')
    const chatCss = readFileSync(join(SOURCE_ROOT, 'chat.css'), 'utf8')

    const namedThemeSelectors = [...pageCss.matchAll(/data-theme\s*=\s*['"]([^'"]+)['"]/g)].map(
      (match) => match[1],
    )
    expect(new Set(namedThemeSelectors)).toEqual(new Set(['warm', 'light']))
    expect(pageCss).not.toContain('data-theme-variant')
    expect(pageCss).not.toMatch(/#(?:edeae3|f5f3ec|fbf9f4|ddd8cc|e6e2d8|c8c2b3|f8f6f0|d5cfc1)\b/i)
    // The settings rail is a PANEL beside the modal canvas. It used to pin --surface,
    // but --surface collapsed onto --bg when card surfaces stopped being a lighter slab
    // (2026-08-24), which would have merged the rail into the modal body. --panel is the
    // token that still carries "raised beside the canvas" in every theme.
    expect(settingsCss).toMatch(/\.onw-settings \.rd-set-nav \{[^}]*background: var\(--bg-2\)/s)
    // The Accounts master pane is navigation on the page CANVAS, not a raised card —
    // qa/accounts-sidebar-surface.mjs pins the computed color to --bg in all three
    // themes ("using --surface paints the whole resizable column as a contrasting slab").
    expect(accountsCss).toMatch(/\.acct-side \{[^}]*background: var\(--bg\)/s)
    // Lucius routes its panel background through a component token: the dark transcript
    // stays on the app canvas, bright themes take their dedicated panel step (see the
    // "Component semantic" comment beside the rule).
    // ONE value in every theme now: the side panel IS the app background. The composer
    // inside it lifts on --panel instead of the panel being tinted behind it.
    expect(luciusCss).toMatch(/\.lx-side \{[^}]*--lx-panel-bg: var\(--bg\)/s)
    expect(luciusCss).not.toMatch(/data-theme='light'\]\) \.lx-side \{\s*--lx-panel-bg/s)
    expect(chatCss).toMatch(/\.conv-menu \{[^}]*background: var\(--surface\)/s)
  })
})
