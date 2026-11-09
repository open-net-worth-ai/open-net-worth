import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const src = (path: string) => readFileSync(resolve(__dirname, '..', path), 'utf8')

// An installed iOS app with the `default` status bar lays out below it, yet iOS resolves
// 100vh/100dvh to the full screen — the shell overran by the status bar's height and the tab
// bar fell off the bottom (2026-10-19). Percentages resolve against the real web view.
it('sizes the installed iOS phone shell from the containing block, not viewport units', () => {
  const css = src('index.css')
  expect(css).toMatch(
    /:root\[data-onw-display-mode='standalone'\] body \*:has\(\.app-shell\.mb-shell\)\s*\{\s*height: 100%;/,
  )
  expect(css).toMatch(
    /:root\[data-onw-display-mode='standalone'\] \.app-shell\.mb-shell\s*\{\s*height: 100%;\s*min-height: 0;/,
  )
})

it('sizes the installed iOS full-screen Settings pane from its fixed overlay', () => {
  expect(src('components/tabs/settings/settings.css')).toMatch(
    /:root\[data-onw-display-mode='standalone'\] \.ui-modal-card--workspace\.onw-settings\s*\{\s*height: 100%;\s*max-height: 100%;/,
  )
})

// On a phone Settings is a bottom sheet with a gap under the status bar. Applied there, the
// 100% rule made the white card reach the top edge, and the installed app's status bar took
// its white instead of the scrim's dim (2026-11-09).
it('keeps the installed-app full-height Settings rule off phones', () => {
  expect(src('components/tabs/settings/settings.css')).toMatch(
    /@media \(min-width: 640px\) and \(max-width: 1023\.98px\) \{\s*:root\[data-onw-display-mode='standalone'\] \.ui-modal-card--workspace\.onw-settings\s*\{/,
  )
})
