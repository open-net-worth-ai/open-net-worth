import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Regression: the Connectors masthead rendered three different ways depending on which tab
 * had been visited first — 174px on a fresh /connectors, 215px arriving from Automations,
 * 201px after Connectors → Automations → Connectors — losing spec §9's "search pinned left,
 * primary action pinned right" toolbar in the last two.
 *
 * Root cause: `connectors.css` and `tabs/automations.css` are BOTH unlayered and BOTH define
 * the same six bare class names (rd-masthead, rd-input, rd-kebab, rd-kebab-btn, rd-kebab-item,
 * rd-kebab-pop). Equal specificity, equal origin, no layer — so the cascade falls through to
 * source order, which for two lazy route chunks is whichever tab React loaded last.
 *
 * `npm run lint:classes` cannot see this: both definitions are legitimately "used", and the
 * guard checks that a className has *a* definition, never that it has exactly one. So the
 * invariant is pinned here instead, in the two halves the collision actually has:
 *
 *   A. OUTBOUND — every selector in connectors.css is scoped under `.cn-page`. This both
 *      raises it above the foreign rule (0,2,0 vs 0,1,0, so load order stops mattering) and
 *      stops this sheet restyling the other tab, which it was doing: `flex-direction: column`
 *      is declared HERE and by nothing in automations.css.
 *
 *   B. INBOUND — scoping settles a property both sheets declare, but a property only the
 *      FOREIGN sheet declares still lands, because it has nothing to lose to. Those must be
 *      neutralised explicitly. This half is recomputed from both sheets on every run, so a
 *      declaration added to automations.css tomorrow fails here rather than in a screenshot.
 *
 * A alone would leave `align-items: center` + `border-bottom` on the masthead; B alone would
 * leave `padding` and `gap` racing on load order. Both, or neither is a fix.
 */

const HERE = __dirname
const SRC = resolve(HERE, '../../..')
const CONNECTORS_CSS = join(HERE, 'connectors.css')

/** The page root every rule in this sheet must hang off. */
const SCOPE = '.cn-page'

// ── a small CSS reader ──────────────────────────────────────────────────────
// Enough of a parser for this job: comments dropped, at-rule preludes recognised, keyframe
// blocks skipped (their "selectors" are percentages), and commas split only at nesting depth
// zero so a `:is(a, b)` argument list is not mistaken for two selectors.

interface Rule {
  selector: string
  props: Set<string>
  file: string
}

function splitTopLevel(list: string): string[] {
  const out: string[] = []
  let depth = 0
  let buf = ''
  for (const ch of list) {
    if (ch === '(') depth++
    else if (ch === ')') depth--
    if (ch === ',' && depth === 0) {
      out.push(buf)
      buf = ''
    } else buf += ch
  }
  out.push(buf)
  return out.map((s) => s.trim()).filter(Boolean)
}

function parse(file: string): Rule[] {
  // Blank comments out rather than deleting them, so offsets stay honest.
  const css = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, (m) =>
    m.replace(/[^\n]/g, ' '),
  )
  const rules: Rule[] = []
  const open: string[] = [] // the at-rule name owning each open block ('' for a style rule)
  let buf = ''
  for (let i = 0; i < css.length; i++) {
    const ch = css[i]
    if (ch === ';') {
      buf = ''
      continue
    }
    if (ch === '}') {
      open.pop()
      buf = ''
      continue
    }
    if (ch !== '{') {
      buf += ch
      continue
    }
    const prelude = buf.trim()
    buf = ''
    if (prelude.startsWith('@')) {
      open.push((prelude.split(/[\s({]/)[0] ?? '').toLowerCase())
      continue
    }
    // Walk to the matching brace to collect this rule's declared property names.
    let depth = 1
    let j = i + 1
    while (j < css.length && depth > 0) {
      if (css[j] === '{') depth++
      else if (css[j] === '}') depth--
      j++
    }
    const props = new Set<string>()
    for (const decl of css.slice(i + 1, j - 1).split(';')) {
      const m = decl.match(/^\s*([-a-zA-Z]+)\s*:/)
      if (m?.[1]) props.add(m[1])
    }
    if (!open.includes('@keyframes'))
      for (const s of splitTopLevel(prelude)) rules.push({ selector: s, props, file })
    open.push('')
  }
  return rules
}

function classesIn(selector: string): string[] {
  return [...selector.matchAll(/\.([a-zA-Z][\w-]*)/g)].flatMap((m) => m[1] ?? [])
}

function findCss(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...findCss(p))
    else if (e.name.endsWith('.css')) out.push(p)
  }
  return out
}

const connectors = parse(CONNECTORS_CSS)
/** Every class name this sheet styles, anywhere in a selector. */
const ours = new Set(connectors.flatMap((r) => classesIn(r.selector)))

describe('connectors.css — A • every selector is scoped to the page root', () => {
  it('has rules to check at all', () => {
    expect(connectors.length).toBeGreaterThan(100)
  })

  it('hangs every selector off .cn-page', () => {
    const unscoped = connectors
      .map((r) => r.selector)
      .filter((s) => s !== SCOPE && !s.startsWith(SCOPE + ' ') && !s.startsWith(SCOPE + ':'))
    expect(
      unscoped,
      `unscoped selector(s) in connectors.css — prefix each with "${SCOPE} ". Both this sheet ` +
        'and tabs/automations.css are unlayered, so an unscoped rule here is decided by lazy-' +
        'chunk load order, not by the cascade.',
    ).toEqual([])
  })
})

describe('connectors.css — B • nothing leaks in from a sibling unlayered sheet', () => {
  /**
   * A foreign rule reaches a Connectors element when every compound in its selector is
   * class-only and every one of those classes is also defined by connectors.css. That is
   * deliberately generous: it over-collects rather than risk missing a real collision, and
   * the assertion below is satisfied by declaring the property, not by narrowing the scan.
   */
  const reaching = findCss(SRC)
    .filter((f) => f !== CONNECTORS_CSS)
    .flatMap(parse)
    .filter((r) => {
      const compounds = r.selector
        .replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '')
        .replace(/[>+~]/g, ' ')
        .trim()
        .split(/\s+/)
        .filter(Boolean)
      if (!compounds.length) return false
      return compounds.every(
        (c) => /^(\.[a-zA-Z][\w-]*)+$/.test(c) && classesIn(c).every((cls) => ours.has(cls)),
      )
    })

  it('still has the collision this guard exists for', () => {
    // If automations.css ever stops shipping these, this test is the thing that tells you —
    // the scoping half above stands on its own, but the neutralisers below become dead weight.
    const automations = reaching.filter((r) => r.file.endsWith('tabs/automations.css'))
    expect(new Set(automations.flatMap((r) => classesIn(r.selector)))).toEqual(
      new Set([
        'rd-masthead',
        'rd-input',
        'rd-kebab',
        'rd-kebab-btn',
        'rd-kebab-item',
        'rd-kebab-pop',
        'danger',
      ]),
    )
  })

  it('declares every property a reaching foreign rule declares', () => {
    const leaks: string[] = []
    for (const foreign of reaching) {
      const target = classesIn(foreign.selector).pop()
      if (!target) continue
      // Everything connectors.css declares on a rule ENDING at that class — the rules that
      // out-specify the foreign one by exactly the .cn-page scope.
      const mine = new Set(
        connectors
          .filter((r) => classesIn(r.selector).pop() === target)
          .flatMap((r) => [...r.props]),
      )
      for (const p of foreign.props)
        if (!mine.has(p))
          leaks.push(`${p}  (${relative(SRC, foreign.file)} :: ${foreign.selector})`)
    }
    expect(
      [...new Set(leaks)].sort(),
      'a sibling unlayered sheet declares these on a class connectors.css also styles, and ' +
        'connectors.css leaves them undeclared — so they land on this page unconditionally. ' +
        'Add each to the matching "collision neutraliser" rule in connectors.css.',
    ).toEqual([])
  })
})

/**
 * The MCP add/edit flow's tall form step vs. its short connecting step used to visibly resize
 * `.rd-modal` between them — a `max-height` ceiling shrinks the dialog to hug whatever content
 * is showing. `.xl` (the one size those multi-step flows use) must be a genuinely FIXED height
 * instead — `height`, never `max-height` — while every other size (the base modal, and `.lg`,
 * which alone hosts "Remove connector?" and a connector's own config form: short and long,
 * single-view content) stays shrink-to-fit on purpose, so a two-line confirm never floats in a
 * mostly-empty ~800px box. See the "Post-port fix" note atop this file for the full reasoning.
 */
describe('connectors.css — .rd-modal.xl is a fixed height; the base modal and .lg still hug their content', () => {
  function propsFor(selector: string): Set<string> {
    const rule = connectors.find((r) => r.selector === selector)
    if (!rule) throw new Error(`no rule for "${selector}" in connectors.css`)
    return rule.props
  }

  it('.rd-modal.xl declares height, not max-height', () => {
    const xl = propsFor(`${SCOPE} .rd-modal.xl`)
    expect(xl.has('height')).toBe(true)
    expect(xl.has('max-height')).toBe(false)
  })

  it(
    'the base .rd-modal still declares max-height — the shrink-to-fit ceiling every ' +
      'one-shot confirm/form (Remove connector?, a config form, Find your bank) relies on',
    () => {
      expect(propsFor(`${SCOPE} .rd-modal`).has('max-height')).toBe(true)
    },
  )

  it(
    ".rd-modal.lg overrides only width — it inherits the base rule's max-height rather " +
      'than re-declaring a fixed height of its own',
    () => {
      const lg = propsFor(`${SCOPE} .rd-modal.lg`)
      expect(lg.has('height')).toBe(false)
      expect(lg.has('max-height')).toBe(false)
    },
  )
})

/**
 * Regression: on a phone the masthead is `display: contents`, which drops its box but NOT its
 * pseudo-elements. The desktop scroll shadow (`.rd-masthead::after`, absolute at top:100%) kept
 * rendering once the page had scrolled, anchored to the scroller, and washed out the first line
 * of the "Want Claude Desktop…" footer. The phone layout must switch it off.
 */
describe('connectors.css — the dissolved phone masthead casts no shadow of its own', () => {
  it('hides .rd-masthead::after alongside display: contents', () => {
    const css = readFileSync(CONNECTORS_CSS, 'utf8')
    const phone = css.slice(css.indexOf('@media (max-width: 700px)'))
    expect(phone).toMatch(/\.cn-page \.rd-masthead \{\s*display: contents;/)
    expect(phone).toMatch(/\.cn-page \.rd-masthead::after \{\s*display: none;/)
  })
})
