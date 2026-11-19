// @vitest-environment jsdom
/**
 * Foundation tests — StatInfo and the Lucius/gold token regression guard.
 *
 * Test plan per docs/rfc/investments/00-foundation.md §Test plan / vitest.
 */
import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { StatInfo } from '../../ui/StatInfo'

// ── StatInfo ──────────────────────────────────────────────────────────────────

describe('StatInfo', () => {
  it('renders a button with role="button" and tabIndex=0', () => {
    const html = renderToStaticMarkup(<StatInfo text="Explanation" />)
    expect(html).toContain('role="button"')
    expect(html).toContain('tabindex="0"')
  })

  it('uses the stat-info-btn class (serif font via CSS, not inline)', () => {
    const html = renderToStaticMarkup(<StatInfo text="Explanation" />)
    expect(html).toContain('stat-info-btn')
    // Font is set via CSS class, not inline style
    expect(html).not.toContain('font-family')
  })

  it('wraps with stat-info class', () => {
    const html = renderToStaticMarkup(<StatInfo text="Explanation" />)
    expect(html).toContain('stat-info')
  })

  it('popover text is not rendered in static markup (hidden until focus/hover)', () => {
    // In static markup (SSR), the popover starts hidden (visible=false state),
    // so the tooltip text should not appear.
    const html = renderToStaticMarkup(<StatInfo text="My unique tooltip text" />)
    // visible starts as false — popover not in initial render
    expect(html).not.toContain('My unique tooltip text')
  })

  it('renders aria-expanded=false initially', () => {
    const html = renderToStaticMarkup(<StatInfo text="Explanation" />)
    expect(html).toContain('aria-expanded="false"')
  })
})

// ── Migration regression: --lucius not --warn on Lucius-identity classes ──────

describe('amber→Lucius migration regression', () => {
  const CSS_PATH = resolve(__dirname, '../../../index.css')

  it('index.css is readable', () => {
    // Verify the CSS file exists for subsequent checks
    const css = readFileSync(CSS_PATH, 'utf8')
    expect(css.length).toBeGreaterThan(1000)
  })

  it('.lx-mark uses var(--lucius), not var(--warn)', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    // Find the .lx-mark rule block
    const lxMarkBlock = css.match(/\.lx-mark\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(lxMarkBlock).toContain('var(--lucius)')
    expect(lxMarkBlock).not.toContain('var(--warn)')
  })

  it('.lx-surface uses var(--lucius), not var(--warn)', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    const block = css.match(/\.lx-surface\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(block).toContain('var(--lucius)')
    expect(block).not.toContain('var(--warn)')
  })

  it('.lx-pulse uses var(--lucius), not var(--warn)', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    const block = css.match(/\.lx-pulse\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(block).toContain('var(--lucius)')
    expect(block).not.toContain('var(--warn)')
  })

  it('.intel-brief-ask-spark uses var(--lucius), not var(--warn)', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    const block = css.match(/\.intel-brief-ask-spark\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(block).toContain('var(--lucius)')
    expect(block).not.toContain('var(--warn)')
  })

  it('.intel-lx-draft uses var(--lucius), not var(--warn)', () => {
    const css = readFileSync(CSS_PATH, 'utf8')
    const block = css.match(/\.intel-lx-draft\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(block).toContain('var(--lucius)')
    expect(block).not.toContain('var(--warn)')
  })
})

// ── Token drift: --lucius present in generated artifacts ──────────────────────

describe('--lucius token in generated artifacts', () => {
  it('defines one --lucius token that follows the theme ink', () => {
    const css = readFileSync(resolve(__dirname, '../../../index.tokens.generated.css'), 'utf8')
    expect(css.match(/--lucius:\s*var\(--ink\);/g)).toHaveLength(1)
    const warmTheme = css.match(/:root\[data-theme='warm'\]\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
    const lightTheme = css.match(/:root\[data-theme='light'\]\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(warmTheme).not.toContain('--lucius:')
    expect(lightTheme).not.toContain('--lucius:')
  })

  it('defines the preserved --violet token in each theme', () => {
    const css = readFileSync(resolve(__dirname, '../../../index.tokens.generated.css'), 'utf8')
    expect(css.match(/--violet:\s*#7C6CF5;/g)).toHaveLength(1)
    const warmTheme = css.match(/:root\[data-theme='warm'\]\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
    const lightTheme = css.match(/:root\[data-theme='light'\]\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(warmTheme).toContain('--violet: #5D4BD8;')
    expect(lightTheme).toContain('--violet: #5D4BD8;')
  })

  it('keeps Lucius purple centralized across the direct themes', () => {
    const tokenSource = readFileSync(
      resolve(__dirname, '../../../design/tokens.source.mjs'),
      'utf8',
    )
    const generatedCss = readFileSync(
      resolve(__dirname, '../../../index.tokens.generated.css'),
      'utf8',
    )
    const mentionColors = readFileSync(
      resolve(__dirname, '../../../lucius/mentionColors.ts'),
      'utf8',
    )
    const widgetDisplay = readFileSync(
      resolve(__dirname, '../overview/widgetDisplayShared.ts'),
      'utf8',
    )
    const luciusCss = [
      'index.css',
      'chat.css',
      'components/tabs/accounts/accounts.css',
      'components/tabs/commitments/commitments.css',
    ]
      .map((path) => readFileSync(resolve(__dirname, '../../../', path), 'utf8'))
      .join('\n')
    const luciusPurpleHex = /#(?:7C6CF5|5D4BD8)/gi
    const allowedPurple = new Map([
      [tokenSource, 2],
      [generatedCss, 3],
      [widgetDisplay, 1],
    ])

    expect(tokenSource).toContain("['violet', '#7C6CF5', '#5D4BD8']")
    expect(tokenSource).toContain("['lucius', 'var(--ink)']")
    for (const [source, occurrences] of allowedPurple) {
      expect(source.match(luciusPurpleHex) ?? []).toHaveLength(occurrences)
    }
    expect(mentionColors).not.toMatch(/lucius:\s*['"]#(?:7C6CF5|5D4BD8)/)
    expect(luciusCss).not.toMatch(/var\(--lucius,\s*#(?:7C6CF5|5D4BD8)\)/i)
  })

  it('tokens.generated.ts union contains --lucius', () => {
    const ts = readFileSync(resolve(__dirname, '../../ui/tokens.generated.ts'), 'utf8')
    expect(ts).toContain("'--lucius'")
  })
})
