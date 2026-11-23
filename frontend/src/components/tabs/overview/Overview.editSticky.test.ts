// Phone Customize mode: the action bar (Add widget / Cancel / Save) pins where the view tabs
// normally do, so a long dashboard never means scrolling back to the top to save (owner,
// 2026-09-20). And the widget picker's sticky toolbar carries no drop shadow — on a light
// surface it read as a smudge under the group chips.

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const overviewCss = readFileSync(new URL('./overview.css', import.meta.url), 'utf8')
const phoneRules = overviewCss.slice(overviewCss.indexOf('@media (max-width: 640px)'))

function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\>]/g, '\\$&')
  const m = css.match(new RegExp(escaped + '\\s*\\{([^}]*)\\}'))
  return m?.[1] ?? ''
}

describe('Dashboard Customize mode on a phone', () => {
  it('pins the add controls and view tabs as one surface', () => {
    const head = rule(phoneRules, '.ov-page--editing .ov-pagehead')
    expect(head).toContain('display: flex')
    expect(head).toContain('position: sticky')
    expect(head).toContain('background: var(--bg)')

    const titles = rule(
      phoneRules,
      '.ov-page--editing .ov-pagehead > .ui-page-header .ui-page-header__titles',
    )
    expect(titles).toContain('display: none')

    const tabsWhileEditing = rule(phoneRules, '.ov-page--editing .ov-view-tabs')
    expect(tabsWhileEditing).toContain('position: relative')
  })
})

describe('Narrow widget card header', () => {
  it('drops a toolbar with real controls under the title instead of squeezing it', () => {
    const narrow = overviewCss.slice(overviewCss.indexOf('@container ovcard (max-width: 360px)'))
    const head = rule(
      narrow,
      '.ov-widget-panel.panel\n    > .ui-panel__head:has(\n      .ov-responsive-toolbar > :not(.ov-widget-header-kebab):not(.ov-header-compact)\n    )',
    )
    expect(head).toContain('flex-wrap: wrap')
  })

  it('keeps a compact control (the net-worth range menu) on the title row', () => {
    const narrow = overviewCss.slice(overviewCss.indexOf('@container ovcard (max-width: 360px)'))
    const toolbar = rule(
      narrow,
      '.ov-responsive-toolbar:not(:has(> :not(.ov-widget-header-kebab):not(.ov-header-compact)))',
    )
    expect(toolbar).toContain('width: auto')
  })
})

describe('Add widgets picker toolbar', () => {
  it('has no drop shadow under the search + group chips', () => {
    const toolbar = rule(overviewCss, '.ov-picker-toolbar')
    expect(toolbar).toContain('position: sticky')
    expect(toolbar).not.toContain('box-shadow')
  })
})
