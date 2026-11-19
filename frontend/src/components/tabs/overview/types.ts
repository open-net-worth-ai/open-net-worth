// Overview v2 — the unified widget-dashboard data model.
//
// One shape for EVERY card on the grid — a hand-built widget (net worth, spending
// pulse, …) and a Lucius-generated one (Ask Lucius, Lucius custom) are both plain
// `WidgetInstance`s resolved against the SAME `WidgetDefinition` registry
// (`definitions.ts`). A "predefined widget" is nothing more than a preset instance
// seeded from a def; there is no parallel data model for generated content.
// See docs/adr/2026-07-22-overview-tab-redesign-faithful-port.md.
//
// Naming note (the task's "eliminate duplicate identifiers" requirement): the
// design handoff's prototype used `uid` (stable per-instance key) and `id` (the
// WIDGET_DEFS lookup key) — two different concepts wearing near-identical names.
// Renamed here to `instanceId` (this placed widget's own identity) and `defId`
// (which registry entry it resolves against) so the two are never confusable by
// name alone. `WidgetDefinition` uses the SAME `defId` field name for the SAME
// concept, instead of calling it `id` on one side and something else on the other.

import type { ReactNode } from 'react'

/** Which picker tab / catalog group a definition belongs to. */
export type WidgetGroup =
  | 'Net worth'
  | 'Overview'
  | 'Cash flow'
  | 'Investing'
  | 'Planning'
  | 'Reports'
  | 'Content'
  // Hidden from the picker (the public demo layout places them).
  | 'Tracking'
  | 'Budgeting'

/** A widget's data SHAPE decides which chart-type representations are valid
 * (Grafana-style) — the shape, not the widget's identity, so a Lucius-built
 * chart just declares a shape and inherits the same control as every
 * hand-built widget of that shape. Stored per-instance at `config.viz`. */
export type WidgetShape = 'breakdown' | 'nw-headline' | 'nw-chart' | 'lux'

export interface VizOption {
  id: string
  label: string
}

/** A per-def settings-schema entry — seeds `WidgetInstance.config` (`seedConfig`). */
export interface WidgetSettingSchema {
  key: string
  /** `categories` = budget envelope labels; `categoryIds` = any ledger category, by id;
   *  `tags` = transaction tags (none chosen = no filter). */
  type: 'segment' | 'url' | 'pairs' | 'categories' | 'categoryIds' | 'tags'
  label: string
  options?: string[]
  default: unknown
  /** `categoryIds` only: the category label to use when none of the stored ids exist in
   * this book (see `resolveCategoryIds`). */
  fallbackLabel?: string
}

/** Where a widget's "View in <tab>" deep link goes. Named fields (not the
 * prototype's positional `[tabKey, tabLabel]` tuple) so a call site never has
 * to remember which index means what. */
export interface WidgetSource {
  tab: string
  label: string
}

/** A registry entry — one per widget TYPE. `defId` is the lookup key every
 * `WidgetInstance.defId` resolves against; `WIDGET_DEFINITIONS` (definitions.ts)
 * is the one array of these, whether the entry is hand-built or one of the
 * three Lucius-generated rows (`lux-ask` / `lux-chart` / `lux-html`). */
export interface WidgetDefinition {
  defId: string
  label: string
  group: WidgetGroup
  /** Default column span (12-col grid). */
  dsize: number
  /** Default row height, in px row-units. */
  dh: number
  /** Row-height cap for auto-fit ("prose") widgets. */
  maxh?: number
  desc: string
  /** Default rendered title — the instance's own `title` override wins over this. */
  title?: string
  shape?: WidgetShape
  source?: WidgetSource
  settings?: WidgetSettingSchema[]
  icon?: ReactNode
  /** True for the three Lucius-generated defs — excluded from the normal picker
   * (added via "Build with Lucius" instead), but otherwise an ordinary def. */
  lucius?: boolean
  /** Excluded from the picker AND from `lucius` handling. */
  hidden?: boolean
  /** Prose widget — auto-fits its content between a floor and `maxh` (AUTOH). */
  prose?: boolean
  /** A list widget's "Rows" choices (`'all'` = no cap) and the count it shows until the user
   *  picks one. Its presence is what puts the Rows control in the widget's editor. */
  rows?: { options: Array<number | 'all'>; default: number | 'all' }
}

export type HeightMode = 'fixed'

export interface WidgetAlert {
  op: '>' | '<' | '>=' | '<=' | '=='
  value: number
}

/** A full-width section divider — a hard floor in the compaction pass. Not a
 * widget: no `defId`, no `config`. Discriminated from `WidgetInstance` by `kind`. */
export interface SectionDivider {
  kind: 'section'
  instanceId: string
  title: string
  collapsed?: boolean
  x: number
  y: number
  w: number
  h: number
}

/** One placed widget. `instanceId` is this card's own stable identity (survives
 * duplicate/drag/resize — two "Movers" widgets on one view get two different
 * `instanceId`s); `defId` is which `WidgetDefinition` it resolves against. */
export interface WidgetInstance {
  kind?: undefined
  instanceId: string
  defId: string
  x: number
  y: number
  w: number
  h: number
  /** Per-widget data controls, seeded from the def's `settings` schema
   * (`seedConfig`) — range, chart detail, watch pairs, embed URL, Lucius
   * prompt/spec/artifactId, … the single source for a widget's own data config. */
  config: Record<string, unknown>
  /** Rename override — wins over the def's `title`. Kept as a SEPARATE field
   * (never merged into the def's title string) so a rename can never eat a
   * live figure or timestamp the widget renders beside its title. */
  title?: string
  hideTitle?: boolean
  /** Visible-row cap for list-shaped widgets: a count, 0 = All, null/undefined = the def's
   *  `rows.default`. Read it through `rowCap()`, never directly. */
  limit?: number | null
  /** Auto-fit ("prose") widgets default to content-fit; pinning to 'fixed'
   * restores manual vertical resize + inner scroll. */
  heightMode?: HeightMode
  /** Named curated accent ('blue'|'green'|'amber'|'violet') or an arbitrary hex. */
  accent?: string | null
  /** Front-end-only threshold mock — no evaluation engine (matches the handoff's
   * own documented scope; see the ADR). */
  alert?: WidgetAlert | null
  collapsed?: boolean
}

export type GridItem = WidgetInstance | SectionDivider

export function isSection(item: GridItem): item is SectionDivider {
  return item.kind === 'section'
}

/** One named, independently-arranged dashboard. */
export interface DashboardView {
  viewId: string
  name: string
  widgets: GridItem[]
  /** The order cards stack in on a phone, by instanceId — set by the phone's ↑↓ buttons, so
   *  reordering on a phone never moves anything on the desktop grid. Ids not listed (added
   *  later) follow in desktop order. */
  phoneOrder?: string[]
}

export interface DashboardState {
  activeViewId: string | null
  views: DashboardView[]
}

/** Props every widget content component receives — the SAME shape whether its
 * def is hand-built or Lucius-generated. */
export interface WidgetContentProps {
  instance: WidgetInstance
  def: WidgetDefinition
  /** Merge a partial config/title/limit patch into this instance. */
  onPatch: (patch: Partial<WidgetInstance>) => void
  onNavigate?: (tab: string) => void
  onOpenAsset?: (label: string) => void
  /** Open the Accounts register pre-filtered — one exact day (the calendar's day cell) or a
   *  set of category ids (the category widgets' "Open register"). */
  onOpenRegister?: (filter: RegisterDeepLink) => void
  /** Ask the REAL Lucius panel/dock to open with a prefilled prompt (never a
   * fabricated inline answer) — see ADR decision 5. */
  onAskLucius?: (prompt: string) => void
  /** Stage an `@Label` entity-mention chip into the real Lucius panel. */
  onShareWithLucius?: (label: string) => void
}

/** A dashboard → register deep link. `dateOn` is ISO `YYYY-MM-DD`; `category` is one or
 * more category ids (OR'd, the register's own multi-category filter). */
export interface RegisterDeepLink {
  dateOn?: string
  category?: string | string[]
  /** A payee's display name (the register's own `payee` token) — Top merchants' rows. */
  payee?: string
  /** `YYYY-MM` — the month a figure was taken from. */
  month?: string
  /** With `month`: the last month of a range (`YYYY-MM`) — a report period's span. */
  monthTo?: string
  /** Transactions carrying any of these tags — a report narrowed to tags. */
  tag?: string[]
  /** Blank-category transactions; `kind` keeps Uncategorized spending apart from income. */
  uncategorized?: boolean
  kind?: string
}
