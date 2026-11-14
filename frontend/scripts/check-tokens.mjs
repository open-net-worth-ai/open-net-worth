#!/usr/bin/env node
/**
 * check-tokens.mjs
 *
 * Scans CSS source files for "frozen literal" anti-patterns that should instead
 * use a design token from src/design/tokens.source.mjs:
 *
 *   1. Raw hex literals whose value equals a defined token  → FAIL
 *      Exception: hex values inside  var(--token, #hex)  fallback positions
 *      are fine (they degrade gracefully).  Also allowed: color-mix() tints —
 *      where a token's raw hex appears as a mix operand — because they're still
 *      *referencing* the value; the dynamic tint is the point.
 *      See ALLOWLIST_HEX_CONTEXTS below for other explicit carve-outs.
 *
 *   2. Raw z-index integer literals  → FAIL
 *      All z-index declarations must use  var(--z-*)  from the z-index scale.
 *
 *   3. Raw  border-radius: 999px  → FAIL
 *      Pill radius must use  var(--radius-pill).
 *
 *   4. Raw  font-size: <px>  → WARNING only
 *      Type-scale rounding decisions are still pending (.claude/rfcs/
 *      offscale-type-space-decisions.md); these are flagged but do not fail CI.
 *
 * Runs against the CSS files listed in CSS_FILES.  Companion to
 * frontend/scripts/check-css-classes.mjs.
 *
 * Usage:
 *   node scripts/check-tokens.mjs     # exits 0 if no FAIL-level violations
 *   npm run lint:tokens
 */

import { readFileSync, existsSync } from 'fs'
import { join, resolve } from 'path'
import { fileURLToPath } from 'url'

const ROOT = fileURLToPath(new URL('../../', import.meta.url))

// ── CSS files to scan ────────────────────────────────────────────────────────
// App.css contains a 3D logo animation using very small local z-index values
// (0, 1) that are intentionally within a single stacking context and cannot
// meaningfully map to the global z-index scale.  It is excluded from this scan.
const CSS_FILES = [
  join(ROOT, 'frontend/src/index.css'),
  join(ROOT, 'frontend/src/chat.css'),
  join(ROOT, 'frontend/src/components/skills/skills.css'),
  join(ROOT, 'frontend/src/components/tabs/plan/plan.css'),
  join(ROOT, 'frontend/src/components/intel/following/following.css'),
]

// ── Token definitions — parsed from the generated CSS ────────────────────────
const GENERATED_CSS = join(ROOT, 'frontend/src/index.tokens.generated.css')

/** Parse all  --name: value  declarations from a CSS string. */
function parseTokenValues(css) {
  const map = new Map() // value → [tokenName, …] (multiple tokens can share a value)
  const re = /--([\w-]+)\s*:\s*([^;]+);/g
  let m
  while ((m = re.exec(css)) !== null) {
    const name = '--' + m[1].trim()
    const value = m[2].trim().toLowerCase()
    if (!map.has(value)) map.set(value, [])
    map.get(value).push(name)
  }
  return map
}

// Only hex values that are direct token values (not aliases like var(...))
function hexTokenValues(tokenMap) {
  const hexMap = new Map() // lowercase hex → token name
  for (const [value, names] of tokenMap) {
    if (/^#[0-9a-f]{3,8}$/.test(value)) {
      hexMap.set(value, names[0]) // pick first canonical name
    }
  }
  return hexMap
}

// ── Contexts where a raw hex equal to a token value is ALLOWED ───────────────
// Each entry is a comment explaining why it's exempt.
//
// Pattern: we strip text matching these patterns from a line before checking
// for raw-hex violations.  The allowlist targets structural contexts, not
// specific hex values, so it stays valid even as token values evolve.
const ALLOWLIST_HEX_CONTEXTS = [
  // Fallback in  var(--token, #hex)  — graceful degradation, acceptable.
  { name: 'var() fallback', re: /var\([^)]+,\s*[^)]+\)/g },
  // color-mix() tints — the hex is a mix operand, not a standalone literal.
  { name: 'color-mix tint', re: /color-mix\([^)]+\)/g },
  // rgba/rgb/hsl — not hex, skip (but belt-and-braces since regex below is hex-specific)
  { name: 'rgba/rgb', re: /rgba?\([^)]+\)|hsla?\([^)]+\)/g },
]

// Additional single-line patterns to suppress for specific known-safe cases.
// Add here with a comment when a hex appears in a genuinely-dynamic context
// that the structural allowlist above doesn't cover.
const ALLOWLIST_LINE_PATTERNS = [
  // generated CSS file — the :root block itself defines the tokens; scanning it
  // for "violations" would produce false positives for every token value.
  // (We skip the generated file entirely in CSS_FILES above.)

  // plan.css: rainbow hue-slider gradient (line ~327) — these are dynamic
  // per-hue stop values for a full-spectrum picker, not semantic colors;
  // no token would be meaningful here.
  { name: 'hue-gradient', re: /linear-gradient\([^)]*#f00[^)]*\)/g },

  // plan.css: #f08a85 is a lightened danger-text tint (≈ #e2554f at ~50% lightness)
  // deliberately chosen for legibility on dark recessed backgrounds; no token for it.
  { name: 'danger-text-tint', re: /#f08a85\b/g },
]

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Remove CSS block comments  /* … * /  from a CSS string, replacing each
 * comment with the same number of newline characters so line numbers are
 * preserved.  Single-line // comments are NOT valid CSS, so they are ignored.
 */
function stripCssComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (match) => '\n'.repeat((match.match(/\n/g) ?? []).length))
}

function stripAllowlistedContexts(line) {
  let s = line
  for (const { re } of ALLOWLIST_HEX_CONTEXTS) {
    re.lastIndex = 0
    s = s.replace(re, (match) => ' '.repeat(match.length))
  }
  return s
}

function linesOf(text) {
  return text.split('\n')
}

// ── Violation collectors ─────────────────────────────────────────────────────

/** FAIL: raw hex value that equals a defined token, outside an allowlisted context. */
function checkHex(lines, hexTokenMap, filename, violations) {
  const HEX_RE = /#([0-9a-fA-F]{3,8})\b/g
  lines.forEach((raw, idx) => {
    // Strip structural contexts (color-mix, var fallbacks, rgba) then strip
    // any additional line-level allowlisted patterns before scanning for violations.
    let line = stripAllowlistedContexts(raw)
    for (const { re } of ALLOWLIST_LINE_PATTERNS) {
      re.lastIndex = 0
      line = line.replace(re, (match) => ' '.repeat(match.length))
    }
    HEX_RE.lastIndex = 0
    let m
    while ((m = HEX_RE.exec(line)) !== null) {
      const hex = ('#' + m[1]).toLowerCase()
      if (hexTokenMap.has(hex)) {
        violations.push({
          level: 'FAIL',
          file: filename,
          line: idx + 1,
          rule: 'hex-equals-token',
          message: `raw hex ${hex} should be var(${hexTokenMap.get(hex)})`,
          source: raw.trim(),
        })
      }
    }
  })
}

/** FAIL: raw z-index integer literal (z-index: <number>) not using var(--z-*).
 *
 *  Escape hatch: lines containing  /* z-local * /  (without the space) are
 *  intentional LOCAL stacking values within a component's own stacking context
 *  (e.g. chart layers, sticky sub-headers) where the coarse global --z-* scale
 *  would produce wrong stacking behaviour.  Add the comment with a brief note.
 *
 *  IMPORTANT: rawLines (pre-comment-strip) is used for the allowlist check so
 *  the /* z-local * / marker survives stripCssComments.  strippedLines is used
 *  for the actual z-index detection so comment contents are not scanned.
 */
function checkZIndex(rawLines, strippedLines, filename, violations) {
  // Matches  z-index: <digits>  (with optional whitespace) — the value is a raw integer.
  // Does NOT match  z-index: var(...)  or  z-index: inherit/auto.
  const RE = /z-index\s*:\s*([0-9]+)/
  strippedLines.forEach((stripped, idx) => {
    if (rawLines[idx].includes('/* z-local')) return // allowlisted local stacking value
    const m = RE.exec(stripped)
    if (m) {
      violations.push({
        level: 'FAIL',
        file: filename,
        line: idx + 1,
        rule: 'z-index-literal',
        message: `raw z-index: ${m[1]} — use var(--z-*) instead`,
        source: rawLines[idx].trim(),
      })
    }
  })
}

/** FAIL: raw border-radius: 999px (not using var(--radius-pill)). */
function checkRadiusPill(lines, filename, violations) {
  // Match 999px in a border-radius declaration that is NOT inside a var() fallback.
  const RE = /border-radius\s*:/
  lines.forEach((raw, idx) => {
    if (!RE.test(raw)) return
    // Strip var() fallbacks before checking
    const stripped = stripAllowlistedContexts(raw)
    if (/\b999px\b/.test(stripped)) {
      violations.push({
        level: 'FAIL',
        file: filename,
        line: idx + 1,
        rule: 'radius-pill-literal',
        message: 'raw 999px — use var(--radius-pill)',
        source: raw.trim(),
      })
    }
  })
}

/** WARNING: raw font-size: <px> (type-scale decisions pending). */
function checkFontSize(lines, filename, violations) {
  const RE = /font-size\s*:\s*([0-9]+(?:\.[0-9]+)?(?:px|rem|em))/
  lines.forEach((raw, idx) => {
    const m = RE.exec(raw)
    if (m) {
      // Skip if it's already using a var() — only flag naked literals.
      if (/font-size\s*:\s*var\(/.test(raw)) return
      violations.push({
        level: 'WARN',
        file: filename,
        line: idx + 1,
        rule: 'font-size-literal',
        message: `raw font-size: ${m[1]} — consider var(--text-*) once type-scale decisions land`,
        source: raw.trim(),
      })
    }
  })
}

// ── Main ──────────────────────────────────────────────────────────────────────

const generatedCss = readFileSync(GENERATED_CSS, 'utf8')
const tokenMap = parseTokenValues(generatedCss)
const hexTokenMap = hexTokenValues(tokenMap)

const allViolations = []

for (const cssPath of CSS_FILES) {
  if (!existsSync(cssPath)) continue
  const rawText = readFileSync(cssPath, 'utf8')
  // Strip block comments before scanning so hex values / literals inside
  // comments don't produce false-positive violations.
  const text = stripCssComments(rawText)
  const rawLines = linesOf(rawText)   // kept for allowlist marker detection (/* z-local */)
  const lines = linesOf(text)
  const filename = cssPath.replace(join(ROOT, 'frontend/'), '')

  checkHex(lines, hexTokenMap, filename, allViolations)
  checkZIndex(rawLines, lines, filename, allViolations)
  checkRadiusPill(lines, filename, allViolations)
  checkFontSize(lines, filename, allViolations)
}

const fails = allViolations.filter((v) => v.level === 'FAIL')
const warns = allViolations.filter((v) => v.level === 'WARN')

// ── Report ───────────────────────────────────────────────────────────────────

if (warns.length > 0) {
  // eslint-disable-next-line no-console
  console.warn(`\n⚠  check-tokens: ${warns.length} WARNING(s) (font-size — non-blocking)\n`)
  const byRule = new Map()
  for (const v of warns) {
    const key = v.rule
    if (!byRule.has(key)) byRule.set(key, [])
    byRule.get(key).push(v)
  }
  for (const [, vs] of byRule) {
    for (const v of vs.slice(0, 5)) {
      // eslint-disable-next-line no-console
      console.warn(`  ${v.file}:${v.line}  ${v.message}`)
    }
    if (vs.length > 5) {
      // eslint-disable-next-line no-console
      console.warn(`  … and ${vs.length - 5} more`)
    }
  }
}

if (fails.length === 0) {
  // eslint-disable-next-line no-console
  console.log(`✓ check-tokens: no FAIL-level violations (${warns.length} warning(s))`)
  process.exit(0)
}

// eslint-disable-next-line no-console
console.error(`\n✗ check-tokens: ${fails.length} FAIL-level violation(s)\n`)

const byRule = new Map()
for (const v of fails) {
  if (!byRule.has(v.rule)) byRule.set(v.rule, [])
  byRule.get(v.rule).push(v)
}

for (const [rule, vs] of byRule) {
  // eslint-disable-next-line no-console
  console.error(`  [${rule}]`)
  for (const v of vs) {
    // eslint-disable-next-line no-console
    console.error(`    ${v.file}:${v.line}  ${v.message}`)
    // eslint-disable-next-line no-console
    console.error(`      ${v.source}`)
  }
}

// eslint-disable-next-line no-console
console.error(
  `\nFix: replace raw values with the corresponding var(--token) reference.\n` +
    `If a value is genuinely dynamic (computed at runtime) or is a vendor-specific\n` +
    `value with no token equivalent, add it to ALLOWLIST_HEX_CONTEXTS or\n` +
    `ALLOWLIST_LINE_PATTERNS in frontend/scripts/check-tokens.mjs with a comment.`,
)
process.exit(1)
