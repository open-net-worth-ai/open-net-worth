// screenCommands.ts — the screen-command vocabulary (pure leaf; no React).
// SPECS are static module-scope definitions — the single source for BOTH the visible
// screen's live manifest (registered at mount via useRegisterScreenCommands) and the
// OFFSCREEN_COMMAND_CATALOG that rides every snapshot so Lucius can park a command +
// offer a navigation chip when the screen isn't open. The backend never learns screen
// specifics — it validates against whatever manifest this module sent this turn.
// NAMING RULE: command names must be prefix-free (no name may be a strict prefix of
// another) — the bus matches subscriptions by startsWith (see bus.ts onScreenCommand).
import type { ScreenCommandWire, ScreenSnapshotWire } from './screenSnapshotWire'

export interface ScreenCommandParamSpec {
  name: string
  type: 'string' | 'number' | 'boolean' | 'array' | 'object'
  required?: boolean
  enum?: string[]
  minimum?: number
  maximum?: number
  /** Item-shape hint for arrays — rendered as `name:array<hint>` in the prose manifest.
   *  REQUIRED in practice for array params: the command description truncates at 160
   *  chars server-side, so a shape documented only there never reaches the model. */
  items?: string
}

export interface ScreenCommandSpec {
  name: string
  description: string
  params: ScreenCommandParamSpec[]
  route: string
}

// Arg names are camelCase to match the persisted plan tools (set_plan_baseline /
// create_plan_scenario use retireAge/lifeExp/endAge) — ONE vocabulary across both plan
// surfaces, so the model can't cross-contaminate field-name conventions between them.
export const PLAN_STAGE_WHATIF_SPEC: ScreenCommandSpec = {
  name: 'plan.stage_whatif',
  // Keep under the 160-char server-side prose cap — array item shapes ride the
  // per-param `items` hints below, never this description.
  description:
    "Stage an ephemeral what-if (UI only, nothing saved). Levers reset to baseline; events/streams stack on what's staged — remove or plan.reset to clear.",
  route: 'plan-outlook',
  params: [
    { name: 'saving', type: 'number', minimum: 0 },
    { name: 'ret', type: 'number', minimum: 0, maximum: 100 },
    { name: 'vol', type: 'number', minimum: 0, maximum: 100 },
    { name: 'monthlyRetirementSpending', type: 'number', minimum: 0 },
    { name: 'swr', type: 'number', minimum: 0, maximum: 100 },
    { name: 'lifeExp', type: 'number', minimum: 1, maximum: 150 },
    { name: 'retireAge', type: 'number', minimum: 1, maximum: 150 },
    { name: 'atNumber', type: 'boolean' },
    { name: 'addEvents', type: 'array', items: '{label, amount(-spend/+windfall), age}' },
    {
      name: 'removeEvents',
      type: 'array',
      items: 'label of any CURRENTLY-staged event (partial text ok) to drop just that one',
    },
    { name: 'addStreams', type: 'array', items: '{label, amount/mo, age, endAge?}' },
    {
      name: 'removeStreams',
      type: 'array',
      items: 'label of any CURRENTLY-staged stream (partial text ok) to drop just that one',
    },
    { name: 'crash', type: 'boolean' },
    { name: 'weakSeq', type: 'boolean' },
    { name: 'lostDec', type: 'boolean' },
    { name: 'highInfl', type: 'boolean' },
  ],
}

export const PLAN_RESET_SPEC: ScreenCommandSpec = {
  name: 'plan.reset',
  description:
    'Clears the ENTIRE staged what-if — every lever, event and stream — back to the saved FIRE plan. To drop just one staged item, use plan.stage_whatif removeEvents/removeStreams instead.',
  route: 'plan-outlook',
  params: [],
}

export const AUTOMATIONS_REVIEW_DRAFT_SPEC: ScreenCommandSpec = {
  name: 'automations.review_draft',
  description:
    'Open a validated, unsaved Lucius Automation draft in the review editor. Nothing is created until the user confirms it there.',
  route: 'automations',
  params: [
    {
      name: 'automation',
      type: 'object',
      required: true,
      items: '{name, instructions, description?, trigger, approval?, model?, effort?}',
    },
  ],
}

export const CONNECTORS_OPEN_SETUP_SPEC: ScreenCommandSpec = {
  name: 'connectors.open_setup',
  description:
    'Open a directory connector’s setup form, or id "custom" for the Add custom server form (UI only). The user enters credentials and clicks Connect; nothing is added until then.',
  route: 'connectors',
  params: [
    { name: 'id', type: 'string', required: true },
    { name: 'reason', type: 'string' },
  ],
}

export const OFFSCREEN_COMMAND_CATALOG: ScreenCommandSpec[] = [
  PLAN_STAGE_WHATIF_SPEC,
  PLAN_RESET_SPEC,
  AUTOMATIONS_REVIEW_DRAFT_SPEC,
  CONNECTORS_OPEN_SETUP_SPEC,
]

const MAX_MANIFEST_COMMANDS = 12

let _live: ScreenCommandSpec[] = []

export function setLiveCommands(specs: ScreenCommandSpec[]): void {
  _live = specs
}

export function getLiveCommands(): ScreenCommandSpec[] {
  return _live
}

function toWire(s: ScreenCommandSpec, live: boolean): ScreenCommandWire {
  return {
    name: s.name,
    description: s.description,
    params: s.params.map((p) => ({
      name: p.name,
      type: p.type,
      required: p.required === true,
      enum: p.enum ?? null,
      minimum: p.minimum ?? null,
      maximum: p.maximum ?? null,
      items: p.items ?? null,
    })),
    route: s.route,
    live,
  }
}

export function buildCommandManifest(): ScreenCommandWire[] {
  const seen = new Set<string>()
  const out: ScreenCommandWire[] = []
  for (const s of _live) {
    if (seen.has(s.name)) continue
    seen.add(s.name)
    out.push(toWire(s, true))
  }
  for (const s of OFFSCREEN_COMMAND_CATALOG) {
    if (seen.has(s.name)) continue
    seen.add(s.name)
    out.push(toWire(s, false))
  }
  return out.slice(0, MAX_MANIFEST_COMMANDS)
}

/** The command catalog ALONE, every entry parked (live:false), and no screen content at all.
 *  For a send from a surface that REPLACES the screen (the phone's Lucius tab, the desktop
 *  Lucius home), where screen awareness is forced off. Sending nothing there cleared the
 *  turn's manifest, so "Try a what-if with Lucius" — which on a phone hands off to the Lucius
 *  tab — failed run_screen_command with "available this turn: none" instead of parking the
 *  command and offering the chip back to the Outlook. Static specs only: never anything
 *  about what the user has open. */
export function commandCatalogSnapshot(): ScreenSnapshotWire {
  return {
    id: '',
    title: '',
    view_state: {},
    facts: {},
    visible_text: '',
    handles: [],
    commands: OFFSCREEN_COMMAND_CATALOG.slice(0, MAX_MANIFEST_COMMANDS).map((s) =>
      toWire(s, false),
    ),
    as_of: null,
  }
}
