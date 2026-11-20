import { describe, expect, it } from 'vitest'
import {
  snapBandBottoms,
  AUTOH_IDS,
  fitMode,
  heightModeFor,
  compact,
  COLS,
  computeDisplayLayout,
  SNAP_PX,
  phoneOrdered,
  MAX_H,
  MIN_H,
  MIN_W,
  normalize,
  seedConfig,
  seedWidgets,
  topmostFit,
} from './grid'
import type { GridItem, WidgetDefinition, WidgetInstance } from './types'

function widget(
  overrides: Partial<WidgetInstance> & Pick<WidgetInstance, 'instanceId' | 'defId'>,
): WidgetInstance {
  return { x: 0, y: 0, w: 4, h: 100, config: {}, ...overrides }
}

const DEFS: WidgetDefinition[] = [
  { defId: 'hero', label: 'Hero', group: 'Net worth', dsize: 12, dh: 224, desc: '' },
  { defId: 'summary', label: 'Summary', group: 'Overview', dsize: 4, dh: 128, desc: '' },
  {
    defId: 'movers',
    label: 'Movers',
    group: 'Investing',
    dsize: 6,
    dh: 168,
    desc: '',
    settings: [
      { key: 'range', type: 'segment', label: 'Range', options: ['1M', '1Y'], default: '1M' },
    ],
  },
]

describe('compact', () => {
  it('leaves non-overlapping items in place', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'a', defId: 'hero', x: 0, y: 0, w: 4, h: 100 }),
      widget({ instanceId: 'b', defId: 'hero', x: 4, y: 0, w: 4, h: 100 }),
    ]
    const out = compact(items)
    expect(out.find((it) => it.instanceId === 'a')!.y).toBe(0)
    expect(out.find((it) => it.instanceId === 'b')!.y).toBe(0)
  })

  it('floats an item up to fill a gap left by a removed widget', () => {
    // b sits at y=200 with nothing above it in its columns — should float to y=0.
    const items: GridItem[] = [
      widget({ instanceId: 'b', defId: 'hero', x: 0, y: 200, w: 4, h: 100 }),
    ]
    const out = compact(items)
    expect(out[0]!.y).toBe(0)
  })

  it('stacks overlapping-column items in y-order without collision', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'top', defId: 'hero', x: 0, y: 0, w: 12, h: 100 }),
      widget({ instanceId: 'bottom', defId: 'hero', x: 0, y: 50, w: 12, h: 100 }), // would overlap `top` before compaction
    ]
    const out = compact(items)
    const top = out.find((it) => it.instanceId === 'top')!
    const bottom = out.find((it) => it.instanceId === 'bottom')!
    expect(top.y).toBe(0)
    expect(bottom.y).toBe(top.y + top.h) // sits directly below, no overlap
  })

  it('a section divider is a hard floor — nothing below it floats above it', () => {
    const items: GridItem[] = [
      { kind: 'section', instanceId: 'sec', title: 'Group', x: 0, y: 50, w: 12, h: 30 },
      widget({ instanceId: 'below', defId: 'hero', x: 0, y: 200, w: 12, h: 100 }),
    ]
    const out = compact(items)
    const sec = out.find((it) => it.instanceId === 'sec')!
    const below = out.find((it) => it.instanceId === 'below')!
    expect(below.y).toBeGreaterThanOrEqual(sec.y + sec.h)
  })

  it('drag: the moved widget is ordered by the pointer row (dragKey), not its stored y', () => {
    // `dragged` is stored far below `stationary`, but its pointer is now ABOVE it —
    // compact() with dragKey should let it claim the top slot.
    const items: GridItem[] = [
      widget({ instanceId: 'stationary', defId: 'hero', x: 0, y: 0, w: 12, h: 100 }),
      widget({ instanceId: 'dragged', defId: 'hero', x: 0, y: 500, w: 12, h: 100 }),
    ]
    const out = compact(items, 'dragged', 0)
    const dragged = out.find((it) => it.instanceId === 'dragged')!
    const stationary = out.find((it) => it.instanceId === 'stationary')!
    expect(dragged.y).toBe(0)
    expect(stationary.y).toBe(dragged.y + dragged.h)
  })

  it('is a pure function — never mutates the input array or its items', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'a', defId: 'hero', x: 0, y: 500, w: 4, h: 100 }),
    ]
    const snapshot = JSON.parse(JSON.stringify(items))
    compact(items)
    expect(items).toEqual(snapshot)
  })
})

describe('topmostFit', () => {
  it('returns 0 when there is nothing to collide with', () => {
    expect(topmostFit(0, 4, 100, [])).toBe(0)
  })

  it('skips a row occupied by an overlapping-column widget', () => {
    const others = [{ x: 0, y: 0, w: 4, h: 100 }]
    expect(topmostFit(0, 4, 100, others)).toBe(100)
  })

  it('ignores a widget in different, non-overlapping columns', () => {
    const others = [{ x: 8, y: 0, w: 4, h: 100 }]
    expect(topmostFit(0, 4, 100, others)).toBe(0)
  })

  it('finds a gap ABOVE the current occupant when one opens up', () => {
    // Two widgets stacked in the same columns at y=0 and y=200 (a gap at 100-200
    // if the first one shrinks) — topmostFit should find the earliest free slot.
    const others = [
      { x: 0, y: 0, w: 4, h: 50 },
      { x: 0, y: 200, w: 4, h: 100 },
    ]
    expect(topmostFit(0, 4, 100, others)).toBe(50)
  })
})

describe('seedConfig', () => {
  it('fills in every declared setting default when nothing is saved', () => {
    const cfg = seedConfig(DEFS[2], undefined)
    expect(cfg.range).toBe('1M')
  })

  it('keeps a saved value over the schema default', () => {
    const cfg = seedConfig(DEFS[2], { range: '1Y' })
    expect(cfg.range).toBe('1Y')
  })

  it('preserves saved keys the schema does not declare', () => {
    const cfg = seedConfig(DEFS[2], { viz: 'bar' })
    expect(cfg.viz).toBe('bar')
    expect(cfg.range).toBe('1M') // still seeded
  })

  it('treats an explicit null saved value as unset and re-seeds the default', () => {
    const cfg = seedConfig(DEFS[2], { range: null as unknown as string })
    expect(cfg.range).toBe('1M')
  })

  it('returns an empty object for a def with no settings schema and no saved config', () => {
    expect(seedConfig(DEFS[0], undefined)).toEqual({})
  })
})

describe('normalize', () => {
  it('drops a row whose defId is not in the registry', () => {
    const out = normalize([{ defId: 'not-a-real-widget', x: 0, y: 0, w: 4, h: 100 }], DEFS)
    expect(out).toHaveLength(0)
  })

  it('assigns a client-local instanceId when the saved row has none', () => {
    const out = normalize([{ defId: 'hero' }], DEFS)
    expect(out).toHaveLength(1)
    expect(out[0]!.instanceId).toContain('hero-local-')
  })

  it('preserves a caller-supplied instanceId', () => {
    const out = normalize([{ defId: 'hero', instanceId: 'keep-me' }], DEFS)
    expect(out[0]!.instanceId).toBe('keep-me')
  })

  it('falls back to the def default size/height when none is saved', () => {
    const out = normalize([{ defId: 'summary' }], DEFS)
    expect(out[0]!.w).toBe(4)
    expect(out[0]!.h).toBe(128)
  })

  it('clamps width to [MIN_W, COLS] and height to [MIN_H, MAX_H]', () => {
    const out = normalize([{ defId: 'hero', w: 1, h: 10000 }], DEFS)
    expect(out[0]!.w).toBe(MIN_W)
    expect(out[0]!.h).toBe(MAX_H)
    const out2 = normalize([{ defId: 'hero', w: 99, h: 1 }], DEFS)
    expect(out2[0]!.w).toBe(COLS)
    expect(out2[0]!.h).toBe(MIN_H)
  })

  it('auto-flows x position left-to-right, wrapping at 12 columns', () => {
    const out = normalize(
      [
        { defId: 'summary' }, // w=4 -> x=0
        { defId: 'summary' }, // w=4 -> x=4
        { defId: 'summary' }, // w=4 -> x=8
        { defId: 'summary' }, // w=4, 8+4>12 -> wraps to x=0
      ],
      DEFS,
    )
    expect(out.map((w) => w.x)).toEqual([0, 4, 8, 0])
  })

  it('seeds a section divider with a full-width, fixed height regardless of saved w/h', () => {
    const out = normalize([{ kind: 'section', title: 'Group', y: 40, w: 3, h: 999 }], DEFS)
    expect(out[0]!.kind).toBe('section')
    expect(out[0]!.w).toBe(COLS)
  })

  it('seeds per-widget config from the def settings schema', () => {
    const out = normalize([{ defId: 'movers' }], DEFS)
    expect((out[0] as WidgetInstance).config.range).toBe('1M')
  })

  it('runs the result through compact() so a hand-edited overlap resolves', () => {
    const out = normalize(
      [
        { defId: 'hero', x: 0, y: 0, w: 12, h: 100 },
        { defId: 'hero', x: 0, y: 0, w: 12, h: 100 }, // deliberately overlapping
      ],
      DEFS,
    )
    expect(out[0]!.y).not.toBe(out[1]!.y)
  })

  it('ignores non-array input (never throws)', () => {
    expect(normalize(null, DEFS)).toEqual([])
    expect(normalize(undefined, DEFS)).toEqual([])
    expect(normalize('garbage', DEFS)).toEqual([])
  })
})

describe('seedWidgets', () => {
  it('builds one instance per defId at its default size', () => {
    const out = seedWidgets(['hero', 'summary'], DEFS) as WidgetInstance[]
    expect(out).toHaveLength(2)
    expect(out.map((w) => w.defId).sort()).toEqual(['hero', 'summary'])
  })

  it('silently skips an unknown defId rather than throwing', () => {
    const out = seedWidgets(['hero', 'not-real'], DEFS)
    expect(out).toHaveLength(1)
  })

  it('packs a seed row whose column split differs from the next row without stranding an item', () => {
    // Reproduces a real default-layout bug: a 6+6 row (briefing/upcoming) seeded right
    // before a 4+4+4 row (alloc/reach/venues) doesn't share column boundaries with it, so
    // the SECOND row's rightmost item (x8-12) x-overlaps the FIRST row's second item
    // (x6-12) and, without a row hint, got compacted against it instead of its own row —
    // dragging a third item down with it. A brand-new user's default Overview tab showed
    // this exact gap. Widths mirror the real widgets (a 12-wide hero, then a 6+6 row, then
    // a 4+4+4 row) closely enough to reproduce the collision.
    const defs: WidgetDefinition[] = [
      { defId: 'wide', label: 'Wide', group: 'Overview', dsize: 12, dh: 200, desc: '' },
      { defId: 'left6', label: 'Left6', group: 'Overview', dsize: 6, dh: 150, desc: '' },
      { defId: 'right6', label: 'Right6', group: 'Overview', dsize: 6, dh: 150, desc: '' },
      { defId: 'third1', label: 'Third1', group: 'Overview', dsize: 4, dh: 200, desc: '' },
      { defId: 'third2', label: 'Third2', group: 'Overview', dsize: 4, dh: 180, desc: '' },
      { defId: 'third3', label: 'Third3', group: 'Overview', dsize: 4, dh: 200, desc: '' },
    ]
    const out = seedWidgets(
      ['wide', 'left6', 'right6', 'third1', 'third2', 'third3'],
      defs,
    ) as WidgetInstance[]
    const by = (defId: string) => out.find((w) => w.defId === defId)!

    // The 6+6 row sits directly below the wide widget, side by side — not stacked.
    expect(by('left6').y).toBe(by('right6').y)
    expect(by('left6').y).toBe(by('wide').h)

    // The 4+4+4 row sits directly below the 6+6 row, side by side — not stranded below
    // one of the 6-wide widgets with a gap where it could have tiled instead.
    const rowCTop = by('left6').y + Math.max(by('left6').h, by('right6').h)
    expect(by('third1').y).toBe(rowCTop)
    expect(by('third2').y).toBe(rowCTop)
    expect(by('third3').y).toBe(rowCTop)
  })
})

describe('responsive widget height contract', () => {
  it('grows a fixed-content widget to its measured responsive height and repacks the widget below it', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'breakdown', x: 0, y: 0, w: 12, h: 224 }),
      widget({ instanceId: 'below', defId: 'inv-alloc', x: 0, y: 224, w: 12, h: 168 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 318]]))

    const netWorth = displayItems.find((item) => item.instanceId === 'net-worth')!
    const below = displayItems.find((item) => item.instanceId === 'below')!
    expect(netWorth.h).toBe(318)
    expect(below.y).toBe(318)
  })

  it('keeps a user-resized fixed height as the floor when responsive content is shorter', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'breakdown', x: 0, y: 0, w: 12, h: 400 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 260]]))

    expect(displayItems[0]!.h).toBe(400)
  })

  it('never grows a FILL_IDS canvas widget (hero) past its own stored height — content redraws to fill any box', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 12, h: 150 }),
    ]

    // Simulates the real regression: a Chart.js canvas reports back whatever height it
    // was last drawn at (400px, tall), which must never override a deliberate shrink.
    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 400]]))

    expect(displayItems[0]!.h).toBe(150)
  })

  it('lets content-fit widgets shrink back to their measured height when space returns', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'summary', defId: 'summary', x: 0, y: 0, w: 4, h: 240 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['summary', 132]]))

    expect(displayItems[0]!.h).toBe(132)
  })

  it('lets a widget with a Fit alternative go SHORTER than its content when switched to Fixed', () => {
    const items: GridItem[] = [
      widget({
        instanceId: 'summary',
        defId: 'summary',
        heightMode: 'fixed',
        x: 0,
        y: 0,
        w: 4,
        h: 150,
      }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['summary', 400]]))

    expect(displayItems[0]!.h).toBe(150)
  })

  it('still grows a fixed-only widget with NO Fit alternative to avoid hiding content', () => {
    const items: GridItem[] = [
      widget({
        instanceId: 'net-worth',
        defId: 'breakdown',
        heightMode: 'fixed',
        x: 0,
        y: 0,
        w: 12,
        h: 150,
      }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 400]]))

    expect(displayItems[0]!.h).toBe(400)
  })

  it('ignores stale measured heights while a widget is collapsed', () => {
    const items: GridItem[] = [
      widget({
        instanceId: 'net-worth',
        defId: 'breakdown',
        x: 0,
        y: 0,
        w: 12,
        h: 224,
        collapsed: true,
      }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 318]]))

    expect(displayItems[0]!.h).toBeLessThan(318)
  })
})

describe('row heights: each card its own, small gaps snapped shut', () => {
  const at = (out: GridItem[], id: string) => out.find((it) => it.instanceId === id)!

  it('leaves a short card at its own height beside a much taller one (no half-empty card)', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 6, h: 260 }),
      widget({ instanceId: 'cashflow', defId: 'summary', x: 6, y: 0, w: 6, h: 112 }),
    ]
    // 'summary' fits its content (150px) — 110px short of its row-mate: a real difference.
    const { displayItems } = computeDisplayLayout(items, false, new Map([['cashflow', 150]]))
    expect(at(displayItems, 'net-worth').h).toBe(260)
    expect(at(displayItems, 'cashflow').h).toBe(150)
  })

  it(`snaps a small gap (≤ ${SNAP_PX}px) so near-equal cards line up`, () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 6, h: 200 }),
      widget({ instanceId: 'cashflow', defId: 'summary', x: 6, y: 0, w: 6, h: 112 }),
    ]
    const { displayItems } = computeDisplayLayout(items, false, new Map([['cashflow', 240]]))
    expect(at(displayItems, 'cashflow').h).toBe(240)
    expect(at(displayItems, 'net-worth').h).toBe(240)
  })

  it('never grows a card the user pinned to a fixed height', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 6, h: 260 }),
      widget({
        instanceId: 'pulse',
        defId: 'spending',
        x: 6,
        y: 0,
        w: 6,
        h: 230,
        heightMode: 'fixed',
      }),
    ]
    const { displayItems } = computeDisplayLayout(items, false)
    expect(at(displayItems, 'pulse').h).toBe(230)
  })

  it('lines up the bottom card of a stack of two beside a tall card when the gap is small', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'tall', defId: 'hero', x: 0, y: 0, w: 6, h: 600 }),
      widget({ instanceId: 'b', defId: 'hero', x: 6, y: 0, w: 6, h: 280 }),
      widget({ instanceId: 'c', defId: 'hero', x: 6, y: 280, w: 6, h: 290 }),
    ]
    const { displayItems } = computeDisplayLayout(items, false)
    expect(at(displayItems, 'b').h).toBe(280)
    expect(at(displayItems, 'c').h).toBe(320)
    expect(at(displayItems, 'tall').h).toBe(600)
  })

  it('does the same for a stack of three, and leaves a big gap alone', () => {
    const close: GridItem[] = [
      widget({ instanceId: 'tall', defId: 'hero', x: 0, y: 0, w: 6, h: 600 }),
      widget({ instanceId: 'b', defId: 'hero', x: 6, y: 0, w: 6, h: 190 }),
      widget({ instanceId: 'c', defId: 'hero', x: 6, y: 190, w: 6, h: 190 }),
      widget({ instanceId: 'd', defId: 'hero', x: 6, y: 380, w: 6, h: 180 }),
    ]
    const snapped = computeDisplayLayout(close, false).displayItems
    expect([at(snapped, 'b').h, at(snapped, 'c').h, at(snapped, 'd').h]).toEqual([190, 190, 220])

    const far = close.map((it) => (it.instanceId === 'd' ? { ...it, h: 100 } : it))
    const left = computeDisplayLayout(far, false).displayItems
    expect(at(left, 'd').h).toBe(100)
  })

  it('leaves a solo full-width widget at its own natural height (no row-mate to equalize with)', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'breakdown', x: 0, y: 0, w: 12, h: 224 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false, new Map([['net-worth', 318]]))

    expect(displayItems[0]!.h).toBe(318)
  })

  it('does not stretch a widget stacked below (different row) to match one above', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'top', defId: 'hero', x: 0, y: 0, w: 12, h: 300 }),
      widget({ instanceId: 'bottom', defId: 'movers', x: 0, y: 300, w: 12, h: 100 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false)

    expect(displayItems.find((it) => it.instanceId === 'bottom')!.h).toBe(100)
  })

  it('does not stretch a deliberately collapsed widget, nor let it shrink its row-mate', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 6, h: 260 }),
      widget({
        instanceId: 'cashflow',
        defId: 'summary',
        x: 6,
        y: 0,
        w: 6,
        h: 112,
        collapsed: true,
      }),
    ]

    const { displayItems } = computeDisplayLayout(items, false)

    const netWorth = displayItems.find((item) => item.instanceId === 'net-worth')!
    const cashflow = displayItems.find((item) => item.instanceId === 'cashflow')!
    expect(netWorth.h).toBe(260)
    expect(cashflow.h).toBeLessThan(260)
  })

  it('lays out Customize exactly as the normal view, so what you arrange is what you get', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 6, h: 260 }),
      widget({ instanceId: 'cashflow', defId: 'summary', x: 6, y: 0, w: 6, h: 112 }),
    ]
    const measured = new Map([['cashflow', 230]])
    const view = computeDisplayLayout(items, false, measured).displayItems
    const edit = computeDisplayLayout(items, true, measured).displayItems
    expect(edit).toEqual(view)
  })

  it('leaves a tall widget beside two widgets stacked to match its height untouched (not one flat row)', () => {
    // Spending Pulse (tall, left) sits beside Cash Flow + Budget vs Actual stacked
    // on the right, together matching its height — a deliberate composed layout,
    // not a simple side-by-side row. Y-overlap chains through Spending Pulse to
    // BOTH stacked widgets, but Cash Flow and Budget vs Actual also x-overlap
    // each other (same column) — that stacking must veto equalizing any of them.
    const items: GridItem[] = [
      widget({ instanceId: 'spending-pulse', defId: 'movers', x: 0, y: 0, w: 6, h: 300 }),
      widget({ instanceId: 'cash-flow', defId: 'summary', x: 6, y: 0, w: 6, h: 150 }),
      widget({ instanceId: 'budget-vs-actual', defId: 'summary', x: 6, y: 150, w: 6, h: 150 }),
    ]

    const { displayItems } = computeDisplayLayout(items, false)

    expect(displayItems.find((it) => it.instanceId === 'spending-pulse')!.h).toBe(300)
    expect(displayItems.find((it) => it.instanceId === 'cash-flow')!.h).toBe(150)
    expect(displayItems.find((it) => it.instanceId === 'budget-vs-actual')!.h).toBe(150)
  })
})

describe('view layout keeps saved rows together', () => {
  // The default Overview's two lower rows: a briefing beside "Next 30 days" (both
  // content-fit), then three 4-wide breakdowns.
  const defaultRows = (): GridItem[] => [
    widget({ instanceId: 'briefing', defId: 'summary', x: 0, y: 0, w: 6, h: 196 }),
    widget({ instanceId: 'upcoming', defId: 'summary', x: 6, y: 0, w: 6, h: 196 }),
    widget({ instanceId: 'alloc', defId: 'inv-alloc', x: 0, y: 196, w: 4, h: 260 }),
    widget({ instanceId: 'reach', defId: 'inv-alloc', x: 4, y: 196, w: 4, h: 260 }),
    widget({ instanceId: 'venues', defId: 'inv-alloc', x: 8, y: 196, w: 4, h: 260 }),
  ]
  const at = (items: GridItem[], id: string) => items.find((it) => it.instanceId === id)!

  it('never lets a card float up into the gap a short row-mate leaves (the new-user staircase)', () => {
    const measured = new Map([
      ['briefing', 427],
      ['upcoming', 115],
    ])
    const { displayItems } = computeDisplayLayout(defaultRows(), false, measured)
    // Next 30 days keeps its own short height; the row below still starts under the briefing.
    expect(at(displayItems, 'upcoming').h).toBe(115)
    for (const id of ['alloc', 'reach', 'venues']) expect(at(displayItems, id).y).toBe(427)
  })

  it('renders a layout whose measured heights match the saved ones exactly as saved', () => {
    const items = defaultRows()
    const { displayItems } = computeDisplayLayout(items, false)
    for (const it of items) {
      expect(at(displayItems, it.instanceId)).toMatchObject({ x: it.x, y: it.y, w: it.w, h: it.h })
    }
  })
})

describe('fit-content signal', () => {
  it('shrinks a fixed-size widget to its content only while it asks to', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'hero', defId: 'hero', x: 0, y: 0, w: 12, h: 224 }),
      widget({ instanceId: 'below', defId: 'inv-alloc', x: 0, y: 224, w: 12, h: 168 }),
    ]
    const measured = new Map([['hero', 96]])
    const at = (out: GridItem[], id: string) => out.find((it) => it.instanceId === id)!

    const fixed = computeDisplayLayout(items, false, measured).displayItems
    expect(at(fixed, 'hero').h).toBe(224)

    const fit = computeDisplayLayout(items, false, measured, {
      fitIds: new Set(['hero']),
    }).displayItems
    expect(at(fit, 'hero').h).toBe(96)
    expect(at(fit, 'below').y).toBe(96)
  })
})

describe('Customize lays out like the normal view', () => {
  it('keeps a short card from leaving a hole the row below floats into', () => {
    const items: GridItem[] = [
      widget({ instanceId: 'upcoming', defId: 'summary', x: 0, y: 0, w: 6, h: 248 }),
      widget({ instanceId: 'calendar', defId: 'inv-alloc', x: 6, y: 0, w: 6, h: 248 }),
      widget({ instanceId: 'cashflow', defId: 'inv-alloc', x: 0, y: 248, w: 4, h: 240 }),
      widget({ instanceId: 'recent', defId: 'inv-alloc', x: 4, y: 248, w: 4, h: 240 }),
      widget({ instanceId: 'subs', defId: 'inv-alloc', x: 8, y: 248, w: 4, h: 240 }),
    ]
    const measured = new Map([
      ['upcoming', 115],
      ['calendar', 377],
    ])
    const { displayItems } = computeDisplayLayout(items, true, measured)
    for (const id of ['cashflow', 'recent', 'subs']) {
      expect(displayItems.find((it) => it.instanceId === id)!.y).toBe(377)
    }
  })
})

// A phone's ↑↓ used to nudge the card's DESKTOP row, so reordering on a phone reshuffled the
// desktop grid. The phone keeps its own order; the desktop geometry is never touched.
describe('phone order', () => {
  const items = [
    widget({ instanceId: 'a', defId: 'hero', x: 0, y: 0, w: 6, h: 200 }),
    widget({ instanceId: 'b', defId: 'hero', x: 6, y: 0, w: 6, h: 200 }),
    widget({ instanceId: 'c', defId: 'hero', x: 0, y: 200, w: 12, h: 200 }),
  ]
  const ids = (out: GridItem[]) => out.map((it) => it.instanceId)

  it('follows the saved phone order, leaving the desktop order alone without one', () => {
    expect(ids(phoneOrdered(items))).toEqual(['a', 'b', 'c'])
    expect(ids(phoneOrdered(items, ['c', 'a', 'b']))).toEqual(['c', 'a', 'b'])
  })

  it('puts cards added since the phone order was saved after it, in desktop order', () => {
    expect(ids(phoneOrdered(items, ['b']))).toEqual(['b', 'a', 'c'])
    expect(ids(phoneOrdered(items, ['gone', 'c']))).toEqual(['c', 'a', 'b'])
  })
})

describe('a breakdown keeps the height it is given', () => {
  it('does not grow Spending by category to a long pie legend', async () => {
    const { responsiveHeight } = await import('./grid')
    const item = { instanceId: 'w', defId: 'rep-spendcat', x: 0, y: 0, w: 6, h: 300 }
    // Its legend scrolls inside the box; growing to it pushed every widget below down.
    expect(responsiveHeight(item as never, 760)).toBe(300)
  })
})

describe('Fit / Fixed', () => {
  it('gives every list and number card both, and no chart', () => {
    for (const id of ['networth-nums', 'fx', 'recent-txns', 'subscriptions', 'rep-savings'])
      expect(AUTOH_IDS.has(id)).toBe(true)
    for (const id of ['hero', 'networth-chart', 'rep-trends', 'rep-sankey'])
      expect(AUTOH_IDS.has(id)).toBe(false)
  })

  it('starts lists on Fit and Spending by category on Fixed, and stores only a change', () => {
    expect(fitMode('fx', undefined)).toBe(true)
    expect(fitMode('fx', heightModeFor('fx', false))).toBe(false)
    expect(heightModeFor('fx', true)).toBeUndefined()
    expect(fitMode('rep-spendcat', undefined)).toBe(false)
    expect(fitMode('rep-spendcat', heightModeFor('rep-spendcat', true))).toBe(true)
    expect(heightModeFor('rep-spendcat', false)).toBeUndefined()
  })
})

describe('two stacks side by side', () => {
  it("lines up their top cards, moving what's under them down", () => {
    const w = (instanceId: string, x: number, y: number, h: number) =>
      ({ kind: 'widget', instanceId, defId: 'summary', x, y, w: 6, h }) as unknown as GridItem
    // Net worth (148) beside Cash flow (160), each with another card under it.
    const out = snapBandBottoms([
      w('nw', 0, 0, 148),
      w('a', 0, 148, 200),
      w('cf', 6, 0, 160),
      w('b', 6, 160, 300),
    ])
    const by = new Map(out.map((it) => [it.instanceId, it]))
    expect(by.get('nw')!.h).toBe(160)
    expect(by.get('a')!.y).toBe(160)
    expect(by.get('cf')!.h).toBe(160)
  })

  it('leaves a big difference alone', () => {
    const w = (instanceId: string, x: number, h: number) =>
      ({ kind: 'widget', instanceId, defId: 'summary', x, y: 0, w: 6, h }) as unknown as GridItem
    const out = snapBandBottoms([w('short', 0, 100), w('tall', 6, 100 + SNAP_PX + 1)])
    expect(out.find((it) => it.instanceId === 'short')!.h).toBe(100)
  })
})
