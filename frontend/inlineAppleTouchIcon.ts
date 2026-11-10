import { readFileSync } from 'node:fs'
import type { Plugin } from 'vite'

// iOS 27 Safari stopped using a URL-referenced apple-touch-icon (and the manifest icons) for
// "Add to Home Screen" — the shortcut became a letter tile ("N"), while the same PNG embedded
// as a data: URI still works. Inline it at build time from the one file in public/, so the PNG
// stays the single source of truth (generate-brand-assets.mjs writes it). The placeholder must
// be present: a silent miss would ship the letter tile again.
//
// Its own module, not vite.config.ts: the test that pins it lives under src/, so importing the
// whole config would pull it into the browser app's typecheck.
const APPLE_TOUCH_ICON_PLACEHOLDER = '%APPLE_TOUCH_ICON%'

export function inlineAppleTouchIcon(): Plugin {
  return {
    name: 'onw-inline-apple-touch-icon',
    transformIndexHtml(html) {
      if (!html.includes(APPLE_TOUCH_ICON_PLACEHOLDER)) {
        throw new Error(`index.html is missing the ${APPLE_TOUCH_ICON_PLACEHOLDER} placeholder`)
      }
      const png = readFileSync(new URL('public/icons/apple-touch-icon.png', import.meta.url))
      return html.replace(
        APPLE_TOUCH_ICON_PLACEHOLDER,
        `data:image/png;base64,${png.toString('base64')}`,
      )
    },
  }
}
