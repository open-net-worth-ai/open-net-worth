import { afterEach, expect, it } from 'vitest'
import {
  AUTOMATIONS_REVIEW_DRAFT_SPEC,
  OFFSCREEN_COMMAND_CATALOG,
  PLAN_RESET_SPEC,
  PLAN_STAGE_WHATIF_SPEC,
  TRADING_REVIEW_DRAFT_SPEC,
  buildCommandManifest,
  commandCatalogSnapshot,
  setLiveCommands,
} from './screenCommands'

afterEach(() => setLiveCommands([]))

it('manifest = live commands first (live:true) then catalog (live:false), deduped by name', () => {
  setLiveCommands([PLAN_STAGE_WHATIF_SPEC, PLAN_RESET_SPEC])
  const m = buildCommandManifest()
  const whatif = m.find((c) => c.name === 'plan.stage_whatif')
  expect(whatif?.live).toBe(true)
  expect(m.filter((c) => c.name === 'plan.stage_whatif')).toHaveLength(1)
})

it('with nothing live the catalog rides as live:false with a route', () => {
  const m = buildCommandManifest()
  const whatif = m.find((c) => c.name === 'plan.stage_whatif')
  expect(whatif?.live).toBe(false)
  expect(whatif?.route).toBe('plan-outlook')
  expect(whatif?.params.some((p) => p.name === 'monthlyRetirementSpending')).toBe(true)
  expect(whatif?.params.some((p) => p.name === 'spend')).toBe(false)
  expect(OFFSCREEN_COMMAND_CATALOG.length).toBeGreaterThanOrEqual(2)
  const review = m.find((c) => c.name === 'automations.review_draft')
  expect(review?.live).toBe(false)
  expect(review?.route).toBe('automations')
  expect(review?.params).toEqual([
    expect.objectContaining({ name: 'automation', type: 'object', required: true }),
  ])
  expect(AUTOMATIONS_REVIEW_DRAFT_SPEC.description).toContain('unsaved')
  const trading = m.find((c) => c.name === 'trading.review_draft')
  expect(trading?.live).toBe(false)
  expect(trading?.route).toBe('trading')
  expect(trading?.params).toEqual([
    expect.objectContaining({ name: 'delegation', type: 'object', required: true }),
  ])
  expect(TRADING_REVIEW_DRAFT_SPEC.description).toContain('unsaved')
})

it('caps at 12 and keeps wire params compact', () => {
  setLiveCommands(
    Array.from({ length: 20 }, (_, i) => ({
      name: `t.c${i}`,
      description: 'x',
      params: [],
      route: 'plan-outlook',
    })),
  )
  const m = buildCommandManifest()
  expect(m).toHaveLength(12)
  const first = m.find((c) => c.name === 't.c0')
  expect(first?.params).toEqual([])
})

it('the catalog snapshot carries every command parked and nothing about the screen', () => {
  // Even with the Outlook live, a surface that replaces the screen never claims it is open.
  setLiveCommands([PLAN_STAGE_WHATIF_SPEC])
  const s = commandCatalogSnapshot()
  expect(s).toMatchObject({ id: '', title: '', facts: {}, visible_text: '', handles: [] })
  expect(s.commands.map((c) => c.name)).toEqual(OFFSCREEN_COMMAND_CATALOG.map((c) => c.name))
  expect(s.commands.every((c) => c.live === false && c.route)).toBe(true)
})
