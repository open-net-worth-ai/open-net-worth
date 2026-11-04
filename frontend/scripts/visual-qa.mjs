// Visual-QA assert suite — the enforcement leg of the design-system net.
//
//   node scripts/visual-qa.mjs [--base-url http://localhost:8080] [--out-dir /tmp/onw-visual-qa]
//   node scripts/visual-qa.mjs --boot          # self-boot a hardened disposable instance
//
// With --boot it spins up its OWN throwaway demo instance (frontend/qa/boot.mjs:
// all runtime dirs isolated to /tmp + ONW_QA_MODE, never :8080 / live data) and
// runs the gate against it, tearing it down on exit — so `make ui-gate` is one
// safe command. Without --boot it runs against a RUNNING server (like
// scripts/shoot.mjs); visits every app tab at 3 widths with
// the Lucius dock CLOSED (collapsed pill) and asserts per (tab, width):
//   (a) no horizontal overflow  — document.documentElement.scrollWidth <= innerWidth + 1
//   (b) no console errors
// Plus one chat-OPEN pass at 1440 per tab: clicks the collapsed dock's pill
// (the way a real user opens it — components/chat/lucius/LuciusDock.tsx) and
// asserts:
//   (c) nothing else paints on top of the expanded .lx-dock-inner card (sampled via
//       elementFromPoint — the floating dock is designed to sit over page content, so
//       a plain bounding-box intersection isn't the right test; see the inline comment).
// A screenshot per (tab, width[, chat]) lands in --out-dir for human review.
// Prints a PASS/FAIL table and EXITS NON-ZERO on any failure.
//
// Hang-safe: a hard overall timeout always closes the browser and exits.
// This is a DEV tool (the app never imports it); on-demand, not CI-blocking yet.
//
// Requires the dev `playwright` package + `npx playwright install chromium`.

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

let baseUrl = String(arg('base-url', 'http://localhost:8080')).replace(/\/$/, '')
const outDir = String(arg('out-dir', '/tmp/onw-visual-qa'))
const BOOT = arg('boot') === true

// Tab keys — keep in sync with TABS in src/App.tsx (driven via the wealthTab
// localStorage seed the shoot harness already uses).
const TABS = [
  'dashboard',
  'investments',
  'trading',
  'accounts',
  'assets',
  'commitments',
  'cashflow',
  'plan',
  'intel',
  'skills',
  'build',
  'profile',
  'connectors',
]

// Widths from the RFC breakpoints { mobile, tablet, desktop }.
const WIDTHS = [
  { label: 'mobile', w: 390 },
  { label: 'tablet', w: 834 },
  { label: 'desktop', w: 1440 },
]
const HEIGHT = 1000
const CHAT_OPEN_WIDTH = 1440 // chat-open overlap pass runs at desktop only

const HARD_TIMEOUT_MS = 120000
let _teardown = () => {} // set when --boot spins up a disposable instance
const killer = setTimeout(() => {
  console.log('\nTIMEOUT — exiting (hard 120s cap)')
  try {
    _teardown()
  } catch {
    /* ignore */
  }
  process.exit(1)
}, HARD_TIMEOUT_MS)

mkdirSync(outDir, { recursive: true })

// Optional self-boot: a hardened DISPOSABLE instance (all runtime dirs isolated
// to /tmp + ONW_QA_MODE; never :8080 or live data) so `make ui-gate` is one safe
// command. Point the gate at it and tear it down on exit (finally + killer).
if (BOOT) {
  const { bootDisposableInstance } = await import('../qa/boot.mjs')
  const inst = await bootDisposableInstance({ port: Number(arg('port', '8093')) })
  _teardown = inst.stop
  baseUrl = inst.baseUrl
}

// One page check: seed the tab + chat state, load, run asserts, screenshot.
async function checkCell(browser, { tab, width, label, chatOpen }) {
  const failures = []
  const consoleErrors = []
  const ctx = await browser.newContext({
    viewport: { width, height: HEIGHT },
    deviceScaleFactor: 1,
  })
  await ctx.addInitScript((t) => {
    localStorage.setItem('wealthTab', t)
    localStorage.setItem('chat-open', 'false')
  }, tab)
  const page = await ctx.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(String(err.message || err)))

  try {
    // `domcontentloaded` (not `networkidle`): the SPA holds long-lived
    // connections (SSE/polling), so networkidle would stall the full timeout
    // per cell. A short settle lets the seeded tab render.
    await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(900)
    if (tab === 'build') {
      // Engine readiness shells out to the local CLIs for their versions. The
      // first probe can be slower than the generic SPA settle on a cold process,
      // especially on mobile emulation, so assert layout only after readiness.
      await page.locator('.builder-layout').waitFor({ state: 'visible', timeout: 10000 })
    }

    // (a) no horizontal overflow
    const overflow = await page.evaluate(() => {
      const el = document.documentElement
      return { scrollWidth: el.scrollWidth, innerWidth: window.innerWidth }
    })
    if (overflow.scrollWidth > overflow.innerWidth + 1) {
      failures.push(
        `h-overflow: scrollWidth ${overflow.scrollWidth} > innerWidth ${overflow.innerWidth}`,
      )
    }

    // Builder keeps the user's request and active work ahead of engine/workspace
    // diagnostics on tablet and mobile. Returning users should not have to scroll
    // through setup detail before reaching their task.
    if (tab === 'build' && width <= 1024 && !chatOpen) {
      const order = await page.evaluate(() => {
        const main = document.querySelector('.builder-main')
        const side = document.querySelector('.builder-side')
        if (!main || !side) return null
        return {
          mainTop: main.getBoundingClientRect().top,
          sideTop: side.getBoundingClientRect().top,
        }
      })
      if (!order) {
        failures.push('builder-order: Builder columns were not rendered')
      } else if (order.sideTop < order.mainTop) {
        failures.push('builder-order: engine diagnostics render before the request')
      }
    }

    // (c) nothing paints OVER the open Lucius dock (chat-open pass only) — open it
    // the way a real user does: click the collapsed pill (button.lx-dock-pill), which
    // drops .lx-collapsed off .lx-dock and expands .lx-dock-inner (see LuciusDock.tsx /
    // its own test coverage in LuciusDock.test.tsx). The re-skin retired the old
    // always-open .chat-panel side panel (which reflowed content away via
    // .app-shell.chat-open rules) for a bottom-right FLOATING overlay that's DESIGNED
    // to sit on top of ordinary page content — so a bounding-box "does anything
    // intersect the panel's box" check is the wrong tool here (the main content column
    // always geometrically extends behind a corner-anchored floating widget; that's not
    // a bug). The real regression class (see 91377029/d972102a) is something else  // pii-lint: ok (git SHAs)
    // — a toast, modal, or mispositioned element — winning the paint order and
    // rendering on TOP of the dock. Sample points across the dock's box and confirm
    // its own subtree is what's actually painted there via elementFromPoint.
    if (chatOpen) {
      await page.click('.lx-dock-pill')
      // .lx-dock-inner's opacity/transform transition is ~320ms (lucius.css); give it
      // room to finish so the box we measure is the fully-expanded one.
      await page.waitForTimeout(400)
      const occluded = await page.evaluate(() => {
        const dock = document.querySelector('.lx-dock')
        const panel = document.querySelector('.lx-dock-inner')
        if (!dock || !panel) return { noPanel: true }
        const p = panel.getBoundingClientRect()
        if (p.width === 0 || p.height === 0) return { noPanel: true }
        const inset = 6 // stay off the rounded-corner edge of the card
        const steps = 3
        for (let i = 0; i <= steps; i++) {
          for (let j = 0; j <= steps; j++) {
            const x = Math.min(
              p.right - inset,
              p.left + inset + ((p.width - 2 * inset) * i) / steps,
            )
            const y = Math.min(
              p.bottom - inset,
              p.top + inset + ((p.height - 2 * inset) * j) / steps,
            )
            if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue
            const el = document.elementFromPoint(x, y)
            if (el && !dock.contains(el)) {
              return {
                tag: el.tagName.toLowerCase(),
                cls: typeof el.className === 'string' ? el.className.slice(0, 60) : '',
              }
            }
          }
        }
        return null
      })
      if (occluded && occluded.noPanel) {
        failures.push('chat-overlap: .lx-dock-inner not visible (could not verify)')
      } else if (occluded) {
        failures.push(
          `chat-overlap: <${occluded.tag} class="${occluded.cls}"> paints over .lx-dock-inner`,
        )
      }
    }

    // (b) no console errors
    if (consoleErrors.length) {
      failures.push(`console-errors(${consoleErrors.length}): ${consoleErrors[0].slice(0, 120)}`)
    }

    const suffix = chatOpen ? `${label}-chatopen` : label
    await page.screenshot({ path: `${outDir}/${tab}-${suffix}.png` })
  } catch (e) {
    failures.push(`load-error: ${e.message}`)
  } finally {
    await ctx.close()
  }
  return failures
}

// browser launched INSIDE the try so a launch failure still tears down the
// disposable backend (--boot) via the finally — no orphaned server.
let browser
const results = []
try {
  browser = await chromium.launch()
  for (const tab of TABS) {
    for (const { w, label } of WIDTHS) {
      const failures = await checkCell(browser, { tab, width: w, label, chatOpen: false })
      results.push({ tab, width: label, chat: 'closed', failures })
    }
    // one chat-open overlap pass per tab at desktop
    const failuresOpen = await checkCell(browser, {
      tab,
      width: CHAT_OPEN_WIDTH,
      label: 'desktop',
      chatOpen: true,
    })
    results.push({ tab, width: 'desktop', chat: 'open', failures: failuresOpen })
  }
} catch (e) {
  console.log('FATAL', e.message)
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  clearTimeout(killer)
  // MUST tear down the --boot disposable backend here: its piped stdout keeps the
  // Node event loop alive, so without this `make ui-gate` would hang forever (the
  // killer that would otherwise teardown was just cleared). No-op without --boot.
  try {
    _teardown()
  } catch {
    /* ignore */
  }
}

// PASS/FAIL summary table.
const pad = (s, n) => String(s).padEnd(n)
console.log('\nVISUAL-QA RESULTS  (base-url ' + baseUrl + ', screenshots → ' + outDir + ')')
console.log(pad('TAB', 14) + pad('WIDTH', 10) + pad('CHAT', 8) + 'RESULT')
console.log('-'.repeat(72))
let failed = 0
for (const r of results) {
  const ok = r.failures.length === 0
  if (!ok) failed++
  console.log(
    pad(r.tab, 14) +
      pad(r.width, 10) +
      pad(r.chat, 8) +
      (ok ? 'PASS' : 'FAIL  ' + r.failures.join(' | ')),
  )
}
console.log('-'.repeat(72))
const total = results.length
console.log(`${total - failed}/${total} cells passed · ${failed} failed`)

// Explicit exit (not just exitCode): after --boot, child stdio handles can keep
// the event loop alive even post-teardown — force termination so `make ui-gate`
// always returns. Mirrors frontend/qa/run.mjs.
process.exit(failed > 0 ? 1 : 0)
