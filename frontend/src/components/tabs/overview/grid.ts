// Overview v2 — the grid/compaction engine. Ported verbatim from the design
// handoff's `overview_v2b/app.jsx` (`compact`, `topmostFit`, `seedConfig`,
// `normalize`) with two changes: field names follow types.ts (`instanceId`/
// `defId`, not `uid`/`id`), and the prototype's legacy-saved-layout migration
// branches (string[]/`{size,col,h}`/the retired combined `balance` widget/
// row-unit rescale heuristics) are dropped — this is a green-field feature in
// this app, so there is no prior saved layout to migrate from (ADR
// 2026-07-22-overview-tab-redesign-faithful-port.md, decision 3).

import type { GridItem, WidgetDefinition, WidgetInstance } from './types'
import { isSection } from './types'
import { COLLAPSED_WIDGET_H_PX } from './layoutConstants'

export const COLS = 12
export const MIN_W = 3
export const MIN_H = 48
export const MAX_H = 880
export const ROW_UNIT = 2 // px per grid row unit
export const SECTION_H = 30 // grid units for a full-width section header row

// Canvas-types: content stretches to fill the cell.
export const FILL_IDS = new Set(['hero', 'image', 'embed'])

// A breakdown whose legend scrolls inside its own box (Spending by category's list, bars or
// pie). It keeps the height it is given: grown to its content, a long pie legend pushed every
// widget below it down.
export const OWN_SCROLL_IDS = new Set(['rep-spendcat'])

// List-type widgets whose visible row count the user can cap (3/5/10/All).
export const LIST_IDS = new Set([
  'movers',
  'upcoming',
  'goals',
  'dividends',
  'spending',
  'attention',
  'bud-vsactual',
  'inv-gains',
  // A ranked list (RankedList): its height is its row count (5–25), so it fits like the lists do.
  'rep-merchants',
])

// Prose widgets fit their box between a floor and a px cap (past the cap the
// inner body scrolls). Every LIST_IDS widget gets the same Fit alternative —
// a list's natural height is arbitrary, so Fixed must be able to go shorter
// than its content and scroll, same as prose.
export const AUTOH_IDS = new Set([
  'briefing',
  'lux-ask',
  'cashflow',
  'summary',
  'text',
  'lux-html',
  ...LIST_IDS,
  // Lists and number cards whose content has a natural height (a chart has none: it redraws to
  // fill its box, so it has no Fit).
  'networth-nums',
  'fx',
  'recent-added-txns',
  'recent-txns',
  'category-txns',
  'subscriptions',
  'venues',
  'reach',
  'calendar',
  'rep-savings',
  'rep-table',
  'rep-spendcat',
])

// Fit/Fixed widgets that start Fixed: Spending by category's long pie legend, grown to its
// content, pushed every widget below it down.
export const FIXED_BY_DEFAULT = new Set(['rep-spendcat'])
export const AUTOH_MIN = 40

/** Is this widget currently auto-height (content-fit), or pinned to a fixed size? */
export function fitMode(defId: string, heightMode: WidgetInstance['heightMode']): boolean {
  if (!AUTOH_IDS.has(defId) || heightMode === 'fixed') return false
  return heightMode === 'fit' || !FIXED_BY_DEFAULT.has(defId)
}

/** The stored heightMode for a Fit / Fixed pick: nothing when it is the widget's default. */
export function heightModeFor(defId: string, fit: boolean): WidgetInstance['heightMode'] {
  if (FIXED_BY_DEFAULT.has(defId)) return fit ? 'fit' : undefined
  return fit ? undefined : 'fixed'
}

const overlaps = (a: { x: number; w: number }, b: { x: number; w: number }): boolean =>
  a.x < b.x + b.w && b.x < a.x + a.w

/**
 * Vertical-gravity compactor (react-grid-layout model). Items are placed in
 * y-order and float up into the first free slot; a widget above the moved one
 * stays above. `priInstanceId` only breaks y-ties in the moved widget's favor
 * so it can claim a contested slot. `dragKey` (drag only): the pointer's grid
 * row — the dragged widget is ordered by where the CURSOR is, compared against
 * every other widget's vertical CENTER, so a neighbor swaps once the cursor
 * passes its midpoint regardless of the two widgets' relative sizes.
 * `otherTop`: order the OTHER widgets by their TOP edge, not center — used
 * when dragging a full-width SECTION divider so it snaps to the row boundary
 * under the cursor.
 */
export function compact<T extends GridItem>(
  items: T[],
  priInstanceId?: string | null,
  dragKey?: number | null,
  otherTop?: boolean,
): T[] {
  const key = (it: T): number => {
    if (dragKey == null) return it.y
    if (it.instanceId === priInstanceId) return dragKey
    return otherTop ? it.y : it.y + it.h / 2
  }
  const arr = [...items].sort((a, b) => {
    const dk = key(a) - key(b)
    if (dk !== 0) return dk
    if (a.instanceId === priInstanceId) return -1
    if (b.instanceId === priInstanceId) return 1
    return a.x - b.x
  })
  const placed: T[] = []
  let sectionFloor = 0 // a section header is a hard barrier: everything after it stays below
  for (const it of arr) {
    let y = sectionFloor
    for (const p of placed) if (overlaps(it, p)) y = Math.max(y, p.y + p.h)
    placed.push({ ...it, y })
    if (it.kind === 'section') sectionFloor = y + it.h
  }
  return items.map((it) => placed.find((p) => p.instanceId === it.instanceId) as T)
}

/**
 * Topmost grid row where a rect {x,w,h} fits without overlapping any of
 * `others`. Used to lift a just-resized widget into the highest open slot in
 * its columns, including a gap ABOVE its current row.
 */
export function topmostFit(
  x: number,
  w: number,
  h: number,
  others: Array<{ x: number; y: number; w: number; h: number }>,
): number {
  const hit = (y: number) =>
    others.some((o) => x < o.x + o.w && o.x < x + w && y < o.y + o.h && o.y < y + h)
  const cands = [0, ...others.map((o) => o.y + o.h)].sort((a, b) => a - b)
  for (const y of cands) if (!hit(y)) return y
  return 0
}

/** Seed a widget's config object from its declared settings schema, keeping
 * any saved values — the single source for per-widget data controls. */
export function seedConfig(
  def: WidgetDefinition | undefined,
  saved: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const cfg: Record<string, unknown> = { ...(saved && typeof saved === 'object' ? saved : {}) }
  ;(def?.settings || []).forEach((s) => {
    if (cfg[s.key] === undefined || cfg[s.key] === null) cfg[s.key] = s.default
  })
  return cfg
}

let _localCounter = 0
/** A client-local instance id — the server mints the durable one on save
 * (`DashboardLayoutStore._stamp_widget_ids`); this only needs to be unique for
 * the current session (React keys, drag tracking) until the next save round-trips. */
export function localInstanceId(defId: string): string {
  _localCounter += 1
  return `${defId}-local-${Date.now().toString(36)}-${_localCounter}`
}

const dhOf = (defId: string, defs: WidgetDefinition[]): number =>
  defs.find((d) => d.defId === defId)?.dh || 160

/** Fill in any missing grid coordinates, seed config, then run everything
 * through `compact()` once so a hand-edited or partially-saved layout can't
 * leave overlaps or gaps. */
export function normalize(saved: unknown, defs: WidgetDefinition[]): GridItem[] {
  let cx = 0
  // Bumped every time the auto-x cursor wraps to a new line, and used as that
  // row's `y` HINT (not a real pixel height — just an ordering key). Without
  // it every freshly-seeded widget starts at y=0, so compact()'s first pass
  // (which sorts by y, falling through to x on ties) ends up ordering PURELY
  // by x. Two seed "rows" with a different column split (say a 6+6 row above
  // a 4+4+4 row) don't share column boundaries, so a wide item from the
  // second row can x-overlap a narrow item from the first — compact() then
  // settles it against whichever of the first row's items it happens to
  // collide with, instead of the row it was actually seeded for, and a later
  // item chained off THAT collision gets pushed down again. Real symptom: a
  // brand-new user's default Overview tab had "Next 30 days" and "Where it's
  // held" stranded rows below where they visually belonged, gaps and all —
  // reproducible from the default Overview preset alone, nothing the user did.
  let rowHint = 0
  const out: GridItem[] = []
  const rows = Array.isArray(saved) ? (saved as Record<string, unknown>[]) : []
  for (const s of rows) {
    if (!s || typeof s !== 'object') continue
    if (s.kind === 'section') {
      out.push({
        kind: 'section',
        instanceId: (s.instanceId as string) || localInstanceId('section'),
        title: (s.title as string) || 'Section',
        collapsed: !!s.collapsed,
        x: 0,
        y: typeof s.y === 'number' ? s.y : 0,
        w: COLS,
        h: SECTION_H,
      })
      continue
    }
    const defId = s.defId as string
    const def = defs.find((d) => d.defId === defId)
    if (!def) continue
    let w = typeof s.w === 'number' ? s.w : def.dsize
    w = Math.max(MIN_W, Math.min(COLS, w))
    let h = typeof s.h === 'number' ? s.h : dhOf(defId, defs)
    h = Math.max(MIN_H, Math.min(MAX_H, h))
    let x = typeof s.x === 'number' ? s.x : null
    let rowHintForThis: number | null = null
    if (x == null) {
      if (cx + w > COLS) {
        cx = 0
        rowHint += 1
      }
      x = cx
      cx += w
      rowHintForThis = rowHint
    }
    x = Math.max(0, Math.min(COLS - w, x))
    const y = typeof s.y === 'number' ? s.y : (rowHintForThis ?? 0)
    out.push({
      instanceId: (s.instanceId as string) || localInstanceId(defId),
      defId,
      x,
      y,
      w,
      h,
      collapsed: !!s.collapsed,
      title: s.title as string | undefined,
      hideTitle: !!s.hideTitle,
      limit: typeof s.limit === 'number' ? s.limit : null,
      accent: (s.accent as string) || null,
      heightMode: s.heightMode as WidgetInstance['heightMode'],
      alert: (s.alert as WidgetInstance['alert']) || null,
      config: seedConfig(def, s.config as Record<string, unknown> | undefined),
    })
  }
  return compact(out)
}

/** One widget of a preset view: a bare defId, or a defId with the width,
 * title and config this preset gives it. */
export type PresetWidget =
  | string
  | { defId: string; w?: number; title?: string; config?: Record<string, unknown> }

/** Build a fresh instance array from a preset view's widgets — mirrors the
 * prototype's `mkWidgets`. Widgets pack left to right, wrapping at 12 columns,
 * and every widget in a row takes the row's tallest default height: with mixed
 * heights the row below would settle against the shorter one and start life
 * as a staircase. */
export function seedWidgets(entries: PresetWidget[], defs: WidgetDefinition[]): GridItem[] {
  const seeds = entries.map((entry) => {
    const { defId, ...rest } = typeof entry === 'string' ? { defId: entry } : entry
    const w = Math.max(
      MIN_W,
      Math.min(COLS, rest.w ?? defs.find((d) => d.defId === defId)?.dsize ?? COLS),
    )
    return { defId, w, h: dhOf(defId, defs), title: rest.title, config: rest.config }
  })
  let rowStart = 0
  let used = 0
  const rowHeight = (from: number, to: number) => {
    const h = Math.max(...seeds.slice(from, to).map((s) => s.h))
    for (let i = from; i < to; i++) seeds[i]!.h = h
  }
  seeds.forEach((seed, i) => {
    if (used + seed.w > COLS) {
      rowHeight(rowStart, i)
      rowStart = i
      used = 0
    }
    used += seed.w
  })
  rowHeight(rowStart, seeds.length)
  return normalize(seeds, defs)
}

/**
 * The DISPLAY (rendered) height of an item — collapse is a display-only
 * transform, never touching the stored `h`/`w` (mirrors the prototype's
 * `effH`). A section always renders at its fixed header height; a collapsed
 * widget renders as a slim title bar.
 */
export function effectiveHeight(item: GridItem): number {
  if (isSection(item)) return SECTION_H
  return item.collapsed ? COLLAPSED_WIDGET_H_PX : item.h
}

/**
 * Height used by the rendered grid after the dashboard has measured a
 * widget's natural content. Content-fit widgets follow their measurement in
 * both directions. A widget with NO Fit alternative (not in AUTOH_IDS) grows
 * past a too-small stored `h` so it never silently hides content behind a
 * scroll the user has no other way to reveal. A widget WITH a Fit
 * alternative trusts an explicit Fixed size at face value — switching to Fit
 * is the escape hatch, so Fixed can go shorter than content and scroll.
 * FILL_IDS widgets (a chart canvas — `hero`/`image`/`embed`) ALSO trust the
 * stored height at face value: their content redraws to fill whatever box
 * it's given, so there's no natural minimum to protect against clipping —
 * and their own "measured" height is circular anyway (a canvas reports back
 * whatever size it was last drawn at, so once tall it could never measure
 * smaller, permanently defeating a shrink). Collapsed widgets and sections
 * never consume a stale expanded measurement.
 */
export function responsiveHeight(item: GridItem, measuredHeight?: number, fitNow = false): number {
  const base = effectiveHeight(item)
  if (isSection(item) || item.collapsed || measuredHeight == null) return base
  const measured = Math.max(MIN_H, Math.min(MAX_H, Math.ceil(measuredHeight)))
  // `fitNow`: the widget asked to fit its content for now (useFitContentWhen) —
  // e.g. the net-worth card while it has no chart to fill its box with.
  if (fitNow || fitMode(item.defId, item.heightMode)) return measured
  if (AUTOH_IDS.has(item.defId) || FILL_IDS.has(item.defId) || OWN_SCROLL_IDS.has(item.defId))
    return base
  return Math.max(base, measured)
}

/**
 * Section membership: each widget belongs to the nearest section header
 * above it (by committed y). Returns the set of widget instanceIds hidden by
 * a COLLAPSED section, plus a member count per section — mirrors the
 * prototype's `secInfo`. Caller decides whether to honor `hiddenByCollapse`
 * (VIEW mode only — Customize keeps every widget visible so the layout stays
 * rearrangeable).
 */
export function sectionMembership(items: GridItem[]): {
  hiddenByCollapse: Set<string>
  countBySection: Map<string, number>
} {
  const ordered = [...items].sort((a, b) => a.y - b.y || a.x - b.x)
  let cur: Extract<GridItem, { kind: 'section' }> | null = null
  const hiddenByCollapse = new Set<string>()
  const countBySection = new Map<string, number>()
  for (const it of ordered) {
    if (isSection(it)) {
      cur = it
      countBySection.set(it.instanceId, 0)
    } else if (cur) {
      countBySection.set(cur.instanceId, (countBySection.get(cur.instanceId) ?? 0) + 1)
      if (cur.collapsed) hiddenByCollapse.add(it.instanceId)
    }
  }
  return { hiddenByCollapse, countBySection }
}

/** The most a card grows to line up with the bottom of its row or stack. A gap this small
 *  reads as a misalignment; a bigger one reads as layout and is left alone. */
export const SNAP_PX = 60

/**
 * Each card keeps its own height — a Fit card its content's, a Fixed card the height the user
 * set — except that a SMALL gap closes: a card whose bottom sits up to `SNAP_PX` above the
 * bottom of a card beside it grows to line up, and whatever is stacked under it moves down with
 * it. One rule covers a flat row, the top cards of two stacks side by side (Net worth beside
 * Cash flow, each with more cards under it) and a short column beside one tall card.
 *
 * It used to stretch every card in a row to the row's tallest, so a two-item "Next 30 days"
 * beside a long list was half empty — it read as broken, not short. It then lined up only the
 * bottom card of each column, which left two stacks' top cards a few pixels apart. A card the
 * user pinned `fixed` never grows past the height they chose. Display-only, like
 * `responsiveHeight`.
 */
export function snapBandBottoms<T extends GridItem>(items: T[]): T[] {
  let cur = items
  // Each pass grows one card, and only ever grows: bounded by the number of cards.
  for (let pass = 0; pass <= items.length; pass++) {
    const live = cur.filter((it) => !isSection(it) && !it.collapsed)
    if (live.length < 2) return cur
    const byBottom = [...live].sort((a, b) => a.y + a.h - (b.y + b.h))
    let grow: { id: string; h: number } | null = null
    for (const it of byBottom) {
      if (it.heightMode === 'fixed') continue
      const end = it.y + it.h
      const beside = live.filter((o) => {
        const gap = o.y + o.h - end
        return o !== it && !overlaps(o, it) && o.y < end && gap > 0 && gap <= SNAP_PX
      })
      if (!beside.length) continue
      grow = { id: it.instanceId, h: Math.max(...beside.map((o) => o.y + o.h)) - it.y }
      break
    }
    if (!grow) return cur
    const { id, h } = grow
    cur = compact(cur.map((it) => (it.instanceId === id ? { ...it, h } : it)))
  }
  return cur
}

/**
 * The SAVED layout split into bands: maximal runs of items whose saved rows
 * overlap vertically. A saved layout is always compacted against saved
 * heights, so bands stack strictly — each one starts where the last ends.
 */
export function rowBands(items: GridItem[]): GridItem[][] {
  const sorted = [...items].sort((a, b) => a.y - b.y || a.x - b.x)
  const bands: GridItem[][] = []
  let bottom = -Infinity
  for (const it of sorted) {
    const band = bands[bands.length - 1]
    if (band && it.y < bottom) {
      band.push(it)
      bottom = Math.max(bottom, it.y + it.h)
    } else {
      bands.push([it])
      bottom = it.y + it.h
    }
  }
  return bands
}

function layoutBand(
  members: GridItem[],
  measuredHeights: ReadonlyMap<string, number>,
  fitIds: ReadonlySet<string>,
): GridItem[] {
  const own = compact(
    members.map((it) => ({
      ...it,
      h: responsiveHeight(it, measuredHeights.get(it.instanceId), fitIds.has(it.instanceId)),
    })),
  )
  return compact(snapBandBottoms(own))
}

/**
 * The layout actually rendered, in both VIEW and Customize: display heights
 * applied (`effectiveHeight`, measured content, `fitIds`), laid out band by
 * band, each card its own height with small gaps snapped shut (`snapBandBottoms`). VIEW mode also drops
 * the members of a collapsed section; Customize keeps every widget so the
 * layout stays rearrangeable. Mid-gesture the grid draws its live preview
 * as-is instead (DashboardGrid), so a resize handle shows exactly the size
 * being dragged.
 *
 * Measured heights differ from the saved ones (a briefing grows, an empty
 * list shrinks), and one global compaction let a card from a lower row float
 * up into the gap a short card left — the default Overview rendered as a
 * staircase. A band never rises past the bottom of the band above it.
 */
export function computeDisplayLayout(
  items: GridItem[],
  editing: boolean,
  measuredHeights: ReadonlyMap<string, number> = new Map(),
  { fitIds = new Set() }: { fitIds?: ReadonlySet<string> } = {},
): { displayItems: GridItem[]; countBySection: Map<string, number> } {
  const { hiddenByCollapse, countBySection } = sectionMembership(items)
  const displayItems: GridItem[] = []
  let top = 0
  for (const band of rowBands(items)) {
    const shown = editing ? band : band.filter((it) => !hiddenByCollapse.has(it.instanceId))
    if (!shown.length) continue
    const laid = layoutBand(shown, measuredHeights, fitIds)
    for (const it of laid) displayItems.push({ ...it, y: it.y + top })
    top += Math.max(...laid.map((it) => it.y + it.h))
  }
  return { displayItems, countBySection }
}

/** Cards in the order a phone stacks them: the view's own `phoneOrder` first, anything it
 *  doesn't list (added since) after, in desktop reading order (`items` arrives sorted). */
export function phoneOrdered<T extends GridItem>(items: T[], phoneOrder?: string[]): T[] {
  if (!phoneOrder?.length) return items
  const rank = new Map(phoneOrder.map((id, i) => [id, i]))
  const listed = items.filter((it) => rank.has(it.instanceId))
  listed.sort((a, b) => rank.get(a.instanceId)! - rank.get(b.instanceId)!)
  return [...listed, ...items.filter((it) => !rank.has(it.instanceId))]
}
