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
  const { inlineAppleTouchIcon } = await import('../vite.config')
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  const transform = inlineAppleTouchIcon().transformIndexHtml as (html: string) => string
  const out = transform(html)
  expect(out).toMatch(
    /<link rel="apple-touch-icon" sizes="180x180" href="data:image\/png;base64,iVBORw0KGgo[^"]+"/,
  )
  expect(out).not.toContain('%APPLE_TOUCH_ICON%')
  expect(() => transform('<head></head>')).toThrow(/placeholder/)
})
