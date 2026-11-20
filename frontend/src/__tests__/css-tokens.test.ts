/**
 * CSS custom-property guard.
 *
 * Ensures every bare `var(--foo)` reference (no fallback) in global CSS files
 * resolves to a DEFINED token.  `var(--foo, fallback)` references are allowed
 * because they degrade gracefully.
 *
 * If this test turns red it means a token was *used* without being *defined*
 * and without a safe fallback — the browser renders nothing/inherited/initial.
 *
 * Allowlist (RUNTIME_INJECTED_TOKENS): tokens intentionally set at runtime by
 * JS, not in the static CSS.  Only add here with a comment explaining the source.
 *
 * To verify the guard works:  temporarily add `color: var(--zzznope);` to any
 * CSS file — this test will fail listing `--zzznope` as an offender.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

// ── Files to scan (relative to this file's location) ───────────────────────
const CSS_FILES = [
  // The :root token block now lives in this generated file (Phase A token pipeline).
  resolve(__dirname, '../index.tokens.generated.css'),
  resolve(__dirname, '../index.css'),
  resolve(__dirname, '../chat.css'),
]

// ── Runtime-injected tokens (NOT in static CSS, but set by JS at runtime) ──
// Only add tokens here that you have confirmed are injected by JavaScript code
// rather than defined in a :root { } block.
//
// --accent: ThemeProvider.tsx (frontend/src/ThemeProvider.tsx) calls
//   document.documentElement.style.setProperty('--accent', accent_color)
//   on every page load to support white-label branding.  The :root also defines
//   a static fallback `--accent: var(--ibkr)`, so this entry is belt-and-braces
//   for any site that relies on the runtime override path only.
//
// --chat-width: set by ChatPanel.tsx drag-resize handler as an inline style on
//   document.documentElement so the app-shell knows how wide the chat panel is.
//
// --ui-money-prefix-chars: MoneyField.tsx / ThesisEditor.tsx set it as an inline
//   style (the currency prefix length) so the input's left padding clears it.
//
// --hue, --x, --peak, --fall, --spin, --flip: DesktopUpdateNotices.tsx sets them
//   as per-element inline styles on each "What's new" confetti piece and list
//   icon; the whats-new keyframes and tiles read them.
const RUNTIME_INJECTED_TOKENS = new Set<string>([
  '--accent', // ThemeProvider.tsx: setProperty('--accent', accent_color)
  '--chat-width', // ChatPanel.tsx: setProperty('--chat-width', px)
  '--ui-money-prefix-chars', // MoneyField.tsx / ThesisEditor.tsx: inline style
  '--hue', // DesktopUpdateNotices.tsx: inline style per confetti piece / icon
  '--x', // DesktopUpdateNotices.tsx: inline style per confetti piece
  '--peak', // DesktopUpdateNotices.tsx: inline style per confetti piece
  '--fall', // DesktopUpdateNotices.tsx: inline style per confetti piece
  '--spin', // DesktopUpdateNotices.tsx: inline style per confetti piece
  '--flip', // DesktopUpdateNotices.tsx: inline style per confetti piece
])

// ── Token extraction helpers ────────────────────────────────────────────────

/** Extract every `--foo:` declaration found anywhere in a CSS string. */
function extractDefinedTokens(css: string): Set<string> {
  const defined = new Set<string>()
  // Matches:  --token-name:  (the definition side)
  const re = /--([a-zA-Z][a-zA-Z0-9_-]*)\s*:/g
  let m: RegExpExecArray | null
  while ((m = re.exec(css)) !== null) {
    defined.add('--' + m[1])
  }
  return defined
}

interface Usage {
  token: string
  file: string
  line: number
  source: string
}

/**
 * Extract every bare `var(--foo)` reference — those WITHOUT a fallback value.
 * `var(--foo, fallback)` is skipped because it degrades gracefully.
 */
function extractBareUsages(css: string, filename: string): Usage[] {
  const usages: Usage[] = []
  const lines = css.split('\n')
  // Matches var(--foo) with NO comma after the token name inside the parens.
  // Negative lookahead (?![^)]*,) ensures there is no comma before the closing ).
  // This handles simple cases; nested var() fallbacks are caught by the fallback
  // check (presence of comma right after token name).
  const reVar = /var\((--[a-zA-Z][a-zA-Z0-9_-]*)\)/g
  lines.forEach((line, idx) => {
    let m: RegExpExecArray | null
    reVar.lastIndex = 0
    while ((m = reVar.exec(line)) !== null) {
      const token = m[1]
      if (!token) continue
      usages.push({
        token,
        file: filename,
        line: idx + 1,
        source: line.trim(),
      })
    }
  })
  return usages
}

// ── Test ───────────────────────────────────────────────────────────────────

describe('CSS custom property tokens', () => {
  it('every bare var(--token) is defined in a CSS file or the runtime allowlist', () => {
    // Collect all defined tokens across all scanned files.
    const allDefined = new Set<string>()
    const cssContents = CSS_FILES.map((f) => {
      const text = readFileSync(f, 'utf8')
      for (const tok of extractDefinedTokens(text)) {
        allDefined.add(tok)
      }
      return { file: f.split('/').slice(-1)[0] ?? f, text }
    })

    // Collect all bare usages across all scanned files.
    const allBareUsages: Usage[] = []
    for (const { file, text } of cssContents) {
      allBareUsages.push(...extractBareUsages(text, file))
    }

    // Find offenders: bare usage of a token that is neither defined nor allowed.
    const offenders = allBareUsages.filter(
      (u) => !allDefined.has(u.token) && !RUNTIME_INJECTED_TOKENS.has(u.token),
    )

    if (offenders.length > 0) {
      const report = offenders
        .map((o) => `  ${o.file}:${o.line}  ${o.token}\n    context: ${o.source}`)
        .join('\n')
      throw new Error(
        `Found ${offenders.length} bare var() reference(s) to undefined tokens.\n` +
          `These will render as nothing/inherited in the browser.\n\n` +
          `Offenders:\n${report}\n\n` +
          `Fix: either define the token in :root in index.css, add a fallback\n` +
          `  var(${offenders[0]?.token ?? ''}, <value>), or add it to RUNTIME_INJECTED_TOKENS\n` +
          `  with a comment explaining where it is injected.`,
      )
    }

    // Sanity: assert we actually scanned something (guard against empty regex).
    expect(allDefined.size).toBeGreaterThan(10)
    expect(allBareUsages.length).toBeGreaterThan(50)
  })
})
