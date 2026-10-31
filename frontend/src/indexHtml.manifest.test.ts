import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

// Behind the Private Cloud sign-in the manifest is only reachable with the session cookie, and a
// browser fetches a manifest WITHOUT credentials unless told otherwise — so "add to home screen"
// read the sign-in page instead and the app could not be installed.
it('the manifest link sends credentials', () => {
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  expect(html).toMatch(/<link rel="manifest"[^>]*crossorigin="use-credentials"/)
})

// iOS 27 ignores a URL-referenced apple-touch-icon (and the manifest icons) and shows a letter
// tile on the Home Screen; the same PNG inlined as a data: URI still works.
it('the apple-touch-icon is inlined as a data: URI at build time', async () => {
  const { inlineAppleTouchIcon } = await import('../inlineAppleTouchIcon')
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  const transform = inlineAppleTouchIcon().transformIndexHtml as (html: string) => string
  const out = transform(html)
  expect(out).toMatch(
    /<link rel="apple-touch-icon" sizes="180x180" href="data:image\/png;base64,iVBORw0KGgo[^"]+"/,
  )
  expect(out).not.toContain('%APPLE_TOUCH_ICON%')
  expect(() => transform('<head></head>')).toThrow(/placeholder/)
})

// iOS 27 draws a system blur over the top of an installed web app unless WebKit finds an opaque
// position:fixed box at the top edge, ~6px tall or more (a 1px strip was ignored). The box is
// painted invisible with background-clip: text so it never covers the page.
it('the installed app has a fixed top box iOS 27 accepts in place of its blur', () => {
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  expect(html).toMatch(/<body>\s*<div id="status-bar-sampler" aria-hidden="true"><\/div>/)
  const rule = html.match(/\n\s*#status-bar-sampler \{([^}]*)\}/)?.[1] ?? ''
  expect(rule).toContain('position: fixed;')
  expect(rule).toContain('top: 0;')
  expect(Number(rule.match(/height: (\d+)px;/)?.[1])).toBeGreaterThanOrEqual(8)
  expect(rule).toContain('background-color: inherit;')
  expect(rule).toContain('background-clip: text;')
  // Shown in the installed app by EITHER signal — iOS does not always report display-mode.
  expect(html).toMatch(
    /@media \(display-mode: standalone\) \{\s*#status-bar-sampler \{\s*display: block;/,
  )
  expect(html).toMatch(
    /:root\[data-onw-display-mode='standalone'\] #status-bar-sampler \{\s*display: block;/,
  )
})

// The status bar is native, so a scrim can't dim it, and the box iOS tints it from sits above
// every scrim: without this the bar stayed the bright theme colour over a dimmed page.
it('the status-bar box takes each scrim colour while a modal or sheet is open', () => {
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  const css = readFileSync(resolve(__dirname, 'index.css'), 'utf8')
  const alpha = (selector: string) =>
    css.match(
      new RegExp(`\\n\\s*${selector} \\{[^}]*?rgba\\(6, \\d+, \\d+, (?:calc\\()?([\\d.]+)`),
    )?.[1]
  const scrim = (selector: string) =>
    html.match(
      new RegExp(`:root:has\\(\\s*${selector}[^{]*\\{\\s*--onw-status-bar-scrim: (\\d+)%`),
    )?.[1]
  expect(scrim("\\.modal-overlay\\[data-state='open'\\]\\)")).toBe(
    String(Number(alpha('\\.modal-overlay')) * 100),
  )
  expect(scrim('\\.modal-overlay--workspace')).toBe(
    String(
      Number(alpha('\\.modal-overlay--workspace,\\s*\\.modal-overlay\\.acct-review-overlay')) * 100,
    ),
  )
  expect(scrim('\\.mb-sheet-scrim')).toBe(String(Number(alpha('\\.mb-sheet-scrim')) * 100))
  expect(alpha('\\.kebab-sheet-scrim')).toBe(alpha('\\.mb-sheet-scrim'))
  expect(html).toMatch(
    /\.kebab-sheet-scrim\s*\)\s*#status-bar-sampler \{\s*background-color: color-mix\(\s*in srgb,\s*rgb\(6, 9, 16\) var\(--onw-status-bar-scrim\),\s*var\(--onw-prepaint-bg\)/,
  )
})
