// The Performance cards' ⓘ tooltip opens below its icon (`top:`); the Plan tab's opens above
// (`bottom:`). They once shared the class names `.stat-info`/`.stat-pop`, so with both stylesheets
// loaded one box got `top` AND `bottom` and collapsed to a single line with the text spilling out.
// Investments keeps its own `mk-` names; this pins that no stylesheet reuses the other's.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, it } from 'vitest'

const here = __dirname
const investmentsCss = readFileSync(join(here, 'design.css'), 'utf8')
const planCss = readFileSync(join(here, '../plan/plan.css'), 'utf8')
const performanceTab = readFileSync(join(here, 'PerformanceTab.tsx'), 'utf8')

it('the Investments tooltip has its own class names', () => {
  expect(investmentsCss).toMatch(/^\.mk-stat-pop \{/m)
  expect(investmentsCss).not.toMatch(/^\.stat-(pop|info)\b/m)
  expect(performanceTab).toContain('"mk-stat-info"')
  expect(performanceTab).not.toMatch(/['"]stat-(pop|info)['" ]/)
})

it('the Plan tooltip does not style the Investments one', () => {
  expect(planCss).not.toMatch(/\.mk-stat-(pop|info)\b/)
})
