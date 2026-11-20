// Display helpers shared by the TradingAgents surfaces (asset card, setup form, approval card).
// Formatting only: every figure shown comes from the backend.
import { providerLabelFor } from '../api/models'
import {
  TA_ANALYST_LABEL,
  TA_CLI_LABEL,
  TA_CLI_PROVIDERS,
  TA_STAGE_OF_NODE,
  TA_STAGES,
} from '../types/tradingAgentsConstants'
import type { TaDisclosure, TaRun, TaStageId } from '../types/tradingAgents'

export interface TaModelRef {
  /** The model's own id, never carrying a provider prefix. */
  model: string
  /** The provider's name as the owner knows it. */
  provider: string
  /** "Claude Code plan" and its kin when the call runs on a subscription CLI, else null. */
  plan: string | null
}

/** A `<provider_key>:<model>` ref, as the resolved models of a run name them. */
export function modelRef(ref: string, transport?: string | null): TaModelRef | null {
  const colon = ref.indexOf(':')
  if (colon <= 0) return null
  const key = ref.slice(0, colon)
  const cli = TA_CLI_PROVIDERS.has(transport ?? key) ? (transport ?? key) : null
  return {
    model: ref.slice(colon + 1),
    provider: providerLabelFor(key) ?? TA_CLI_LABEL[key] ?? key,
    plan: cli ? `${TA_CLI_LABEL[cli] ?? cli} plan` : null,
  }
}

/** "model · Provider" with the plan appended for a subscription. */
export function modelText(parts: TaModelRef): string {
  return [parts.model, parts.provider, parts.plan].filter(Boolean).join(' · ')
}

/** The approval card's "provider_key:model via transport" display string, split. */
export function modelFromDisplay(text: string): TaModelRef | null {
  const via = text.lastIndexOf(' via ')
  if (via <= 0) return null
  return modelRef(text.slice(0, via), text.slice(via + 5))
}

/** One converted figure: "EUR→USD 1.08 (cash)". */
export function fxLabel(fx: TaDisclosure['fx_converted'][number]): string {
  const rate = Number(fx.rate.toPrecision(5))
  const what = fx.what ?? fx.ticker
  return `${fx.from}→${fx.to} ${rate}${what ? ` (${what})` : ''}`
}

/** "1m 12s" — a run's elapsed time, which `fDuration` rounds to whole minutes. */
export function fElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

/** The subscription a run's models ran on ("Claude Code plan"), from its config snapshot;
 *  null when every call was metered. */
export function runPlanLabel(snapshot: {
  transports?: { deep?: string; quick?: string }
}): string | null {
  for (const transport of [snapshot.transports?.deep, snapshot.transports?.quick]) {
    if (transport && TA_CLI_PROVIDERS.has(transport)) {
      return `${TA_CLI_LABEL[transport] ?? transport} plan`
    }
  }
  return null
}

/** The names of the AI providers a run's two models belong to, de-duplicated. */
export function runProviders(snapshot: {
  deep?: string
  quick?: string
  transports?: { deep?: string; quick?: string }
}): string[] {
  const names = [
    snapshot.deep ? modelRef(snapshot.deep, snapshot.transports?.deep) : null,
    snapshot.quick ? modelRef(snapshot.quick, snapshot.transports?.quick) : null,
  ].flatMap((m) => (m ? [m.provider] : []))
  return [...new Set(names)]
}

export type StageState = 'done' | 'active' | 'pending'

export interface StageRow {
  id: TaStageId
  label: string
  state: StageState
  sub: string
  seconds: number | null
}

/** Where each of the five stages stands, from the run's graph-node progress log. A node in no
 *  stage is ignored; every stage before the furthest one reached counts as done (an analyst the
 *  owner did not pick leaves no event of its own). */
export function stageRows(run: TaRun, now: number): StageRow[] {
  const spans = new Map<TaStageId, { first: number; last: number | null }>()
  let furthest = -1
  for (const event of run.progress ?? []) {
    const stage = TA_STAGE_OF_NODE[event.stage]
    const at = Date.parse(event.at)
    if (!stage || Number.isNaN(at)) continue
    furthest = Math.max(
      furthest,
      TA_STAGES.findIndex((s) => s.id === stage),
    )
    const span = spans.get(stage) ?? { first: at, last: null }
    span.first = Math.min(span.first, at)
    span.last = Math.max(span.last ?? at, at)
    spans.set(stage, span)
  }
  return TA_STAGES.map((stage, i) => {
    const state: StageState = i < furthest ? 'done' : i === furthest ? 'active' : 'pending'
    const span = spans.get(stage.id)
    const end = state === 'active' ? now : (span?.last ?? null)
    const sub =
      state === 'active'
        ? stage.active
        : state === 'done' && stage.id === 'analysts'
          ? run.analysts.map((a) => TA_ANALYST_LABEL[a] ?? a).join(' · ')
          : ''
    return {
      id: stage.id,
      label: stage.label,
      state,
      sub,
      seconds: span && end !== null && state !== 'pending' ? (end - span.first) / 1000 : null,
    }
  })
}

const DECISION_SENTENCES = 3
const DECISION_CHARS = 400

/** How a rating chip words a run's age: "today", "yesterday", "3 days ago". */
export function runAgeText(ageDays: number): string {
  if (ageDays <= 0) return 'today'
  if (ageDays === 1) return 'yesterday'
  return `${ageDays} days ago`
}

/** The short form of a decision: its first few sentences, within a character budget, cut at a
 *  sentence boundary (a newline counts as one) — a single sentence longer than the budget is cut
 *  at a word with an ellipsis. `cut` says whether anything was left out. */
export function condenseDecision(
  text: string,
  maxSentences = DECISION_SENTENCES,
  maxChars = DECISION_CHARS,
): { text: string; cut: boolean } {
  const full = text.trim()
  const ends: number[] = []
  for (const m of full.matchAll(/[.!?]+(?=\s)|\n+/g)) {
    const end = m[0].startsWith('\n') ? m.index : m.index + m[0].length
    if (end > (ends.at(-1) ?? 0)) ends.push(end)
  }
  const bySentences = ends.length >= maxSentences ? ends[maxSentences - 1]! : full.length
  let limit = bySentences
  if (limit > maxChars) {
    const fitting = ends.filter((e) => e <= maxChars).at(-1)
    if (fitting) limit = fitting
    else {
      const head = full.slice(0, maxChars)
      const space = head.lastIndexOf(' ')
      const word = space > maxChars / 2 ? head.slice(0, space) : head
      return { text: `${word.trimEnd()}…`, cut: true }
    }
  }
  return { text: full.slice(0, limit).trimEnd(), cut: limit < full.length }
}
