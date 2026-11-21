import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'
import { fetchModels, type ModelOption, type ModelSource } from '../api/models'

// Shared model-picker hook for the chat + Settings model fields. Loads the LIVE
// model list for the active provider (GET /api/models) plus the instance default
// model (GET /api/model-config, for the "Instance default" sentinel label).
//
// When multi=true it fetches GET /api/models?all=1 — the UNION across ALL
// configured providers (active + any additional provider credentials). Each entry
// carries provider_key and provider_label so the caller can group options by
// provider (e.g. Claude / OpenAI / Groq grouped <optgroup>).
//
// Replaces the old hint-only useModelOptions: instead of a hardcoded list that
// drifted from reality, the options come from the provider's real model set —
// with a graceful static fallback the backend serves when offline (source tells
// the UI which it got, so it can show a subtle "fallback" hint).
//
// Re-syncs whenever Settings dispatches a "modelconfigchanged" window event after
// a successful PUT /api/model-config or PUT /api/model-config/providers, so the
// "Default (X)" label in ChatPanel reflects the real current default immediately.

export const MODEL_CONFIG_CHANGED_EVENT = 'modelconfigchanged'

// ─── Pure helper ─────────────────────────────────────────────────────────────

/** Return `override` when it is a valid selector value in the current model list;
 *  return `''` (use instance default) when it isn't found.
 *
 *  Valid values are:
 *    - '' (the default sentinel — always valid)
 *    - a plain model id present in `models`
 *    - a cross-provider "catalog_key:model_id" string for any model in `models`
 *    - the `defaultModel` id itself (even if the list was already folded — belt-and-suspenders)
 *
 *  Used for non-authoritative fallbacks such as an Automation's configured model. Explicit
 *  human conversation picks deliberately do not pass through it: if their provider becomes
 *  disabled, the backend refuses them loudly instead of silently changing the selection. */
export function resolveModelOverride(
  override: string,
  models: ModelOption[],
  defaultModel: string,
): string {
  if (!override) return ''
  const valid = new Set<string>()
  for (const m of models) {
    valid.add(m.id)
    if (m.provider_key) valid.add(`${m.provider_key}:${m.id}`)
  }
  if (defaultModel) valid.add(defaultModel)
  return valid.has(override) ? override : ''
}

/** The app's full effort ladder, low → max. */
export const EFFORT_LADDER = ['low', 'medium', 'high', 'xhigh', 'max'] as const
export type EffortLevel = (typeof EFFORT_LADDER)[number]

/** The effort tiers the currently-selected model actually accepts, in ladder order.
 *
 *  Effort controls only the reasoning budget (owner decision 2026-07-18), so the
 *  UI shows exactly what a model supports and hides the dial when the list is
 *  empty. '' → the instance default. Unknown / not-found / no `effort_levels` field →
 *  full ladder (never hide or shrink a control we can't positively rule out). */
export function effortLevelsFor(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): EffortLevel[] {
  const full = [...EFFORT_LADDER]
  const target = active || defaultModel
  if (!target) return full
  const bareId = target.includes(':') ? target.slice(target.indexOf(':') + 1) : target
  const found = models.find(
    (m) =>
      m.id === target ||
      m.id === bareId ||
      (m.provider_key ? `${m.provider_key}:${m.id}` === target : false),
  )
  if (!found || found.effort_levels === undefined) return full
  // Keep ladder order regardless of how the backend ordered the list.
  return full.filter((l) => found.effort_levels!.includes(l))
}

/** Whether the effort dial does anything for the selected model (non-empty levels). */
export function modelSupportsReasoning(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): boolean {
  return effortLevelsFor(active, models, defaultModel).length > 0
}

/** Whether the selected model is KNOWN to reject image input — i.e. attaching an image
 *  will not let it see the image.
 *
 *  Only an explicit `vision: false` from the backend counts. Unknown (field absent) and
 *  not-found both return false ("no warning"), because a wrong warning tells the user a
 *  capability is missing when it isn't, and the backend's pre-send path handles the
 *  unknown case by trying it and learning the answer.
 *
 *  Same model lookup as effortLevelsFor / canDisableReasoningFor. */
export function modelRejectsImages(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): boolean {
  const target = active || defaultModel
  if (!target) return false
  const bareId = target.includes(':') ? target.slice(target.indexOf(':') + 1) : target
  const found = models.find(
    (m) =>
      m.id === target ||
      m.id === bareId ||
      (m.provider_key ? `${m.provider_key}:${m.id}` === target : false),
  )
  return found?.vision === false
}

/** The model NAME to warn with when the active model can't see images, else null.
 *
 *  Strips the `provider_key:` prefix a stored override carries (`glm:glm-4.6` → `glm-4.6`)
 *  since the provider is noise in the sentence. Kept as a pure helper rather than a hook so
 *  both composer surfaces share one derivation (and one wording) without either paying a
 *  second /api/models round trip. */
export function noVisionLabel(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): string | null {
  if (!modelRejectsImages(active, models, defaultModel)) return null
  const ref = active || defaultModel
  const bare = ref.includes(':') ? ref.slice(ref.indexOf(':') + 1) : ref
  return bare || 'This model'
}

/** The "no reasoning" sentinel — a first-class effort CHOICE, distinct from the ladder
 *  tiers (it disables reasoning entirely, it isn't a lower rung of it). Sent to the
 *  backend as effort="off" (accepted alongside "none"). */
export const EFFORT_OFF = 'off' as const
/** An effort selection: a ladder tier OR the "off" (no-reasoning) sentinel. */
export type EffortChoice = EffortLevel | typeof EFFORT_OFF

/** Whether the selected model can HONESTLY offer an "Off" (reasoning fully disabled).
 *  Backend-derived (`can_disable`, OpenRouter reasoning models only today). Same model
 *  lookup as effortLevelsFor; unknown / not-found / absent field → false (never claim an
 *  Off we can't deliver). */
export function canDisableReasoningFor(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): boolean {
  const target = active || defaultModel
  if (!target) return false
  const bareId = target.includes(':') ? target.slice(target.indexOf(':') + 1) : target
  const found = models.find(
    (m) =>
      m.id === target ||
      m.id === bareId ||
      (m.provider_key ? `${m.provider_key}:${m.id}` === target : false),
  )
  return !!found?.can_disable
}

/** The full set of effort CHOICES to render in the picker, in display order: the "Off"
 *  sentinel FIRST (only when the model can honestly disable), then the ladder tiers.
 *  Empty → hide the dial entirely (a non-reasoning model). */
export function effortChoicesFor(
  active: string,
  models: ModelOption[],
  defaultModel: string,
): EffortChoice[] {
  const levels = effortLevelsFor(active, models, defaultModel)
  if (!levels.length) return []
  return canDisableReasoningFor(active, models, defaultModel) ? [EFFORT_OFF, ...levels] : levels
}

/** Clamp a carried-over selection to the current model's CHOICES (ladder tiers + optional
 *  Off). Keeps "off" only when still offered; otherwise a carried "off" falls to the lowest
 *  real tier (a model that can't disable must reason at least a little), and any real tier
 *  clamps down the ladder as before. */
export function clampEffortChoice(current: string, choices: EffortChoice[]): EffortChoice {
  if (!choices.length) return current as EffortChoice
  if (choices.includes(current as EffortChoice)) return current as EffortChoice
  const realTiers = choices.filter((c) => c !== EFFORT_OFF) as EffortLevel[]
  if (current === EFFORT_OFF) return realTiers[0] ?? choices[0]!
  return clampEffort(current, realTiers)
}

/** Adopt an Automation's current configuration into an interactive conversation.
 *
 * Automation configuration is a fallback, not a human's explicit conversation pick: a
 * model absent from the enabled catalogue safely becomes the instance-default sentinel.
 * Effort is then clamped against the model that will actually run, including that fallback.
 */
export function resolveAutomationConversationSelection(
  selection: { model: string; effort: string },
  models: ModelOption[],
  defaultModel: string,
): { model: string; effort: EffortChoice } {
  const model = resolveModelOverride(selection.model, models, defaultModel)
  const choices = effortChoicesFor(model, models, defaultModel)
  return {
    model,
    effort: clampEffortChoice(selection.effort || 'medium', choices),
  }
}

/** Human label for an effort choice ("off" → "Off", else Capitalised tier). */
export function effortChoiceLabel(choice: EffortChoice): string {
  return choice === EFFORT_OFF ? 'Off' : choice.charAt(0).toUpperCase() + choice.slice(1)
}

/** Clamp a carried-over effort to the nearest supported tier when switching models:
 *  the highest supported level ≤ current, else the lowest supported. Returns the
 *  input unchanged if already supported or if the list is empty. */
export function clampEffort(current: string, levels: EffortLevel[]): EffortLevel {
  if (!levels.length) return current as EffortLevel
  if (levels.includes(current as EffortLevel)) return current as EffortLevel
  const ci = EFFORT_LADDER.indexOf(current as EffortLevel)
  const idx = ci < 0 ? EFFORT_LADDER.length - 1 : ci
  for (let i = idx; i >= 0; i--) {
    if (levels.includes(EFFORT_LADDER[i]!)) return EFFORT_LADDER[i]!
  }
  return levels[0]!
}

export interface UseModels {
  models: ModelOption[] // live (or fallback) ids for the active provider (or all)
  source: ModelSource | null // 'live' | 'fallback' (null until loaded)
  defaultModel: string // the instance default (configured) model
  defaultProvider: string // the instance default provider transport
  // The model that would READ an image attachment for a chat model with no vision
  // (backend-derived: the chat_vision pin, else the instance default when it can see).
  // '' = nothing is configured, so an attachment to a blind model won't be read at all.
  imageReader: string
  // Per-task pins ("provider:model") from Settings → Models; an absent key = the default.
  taskModels: Record<string, string>
  loaded: boolean // false until both fetches resolve (success OR failure)
}

// Module-level stale-while-revalidate cache (keyed by `multi`) so re-opening the picker
// — dock, side panel, or Settings — shows the list INSTANTLY from the last result
// instead of flashing "Auto/Default" while /api/models re-fetches. Cleared on a
// model-config change so it never serves a list from a since-removed/disabled provider.
const _modelsCache = new Map<boolean, UseModels>()

const _emptyModelsState: UseModels = {
  models: [],
  source: null,
  defaultModel: '',
  defaultProvider: '',
  imageReader: '',
  taskModels: {},
  loaded: false,
}

export function useModels(multi = false): UseModels {
  // Seed from the cache so a remount paints the full list immediately (no empty flash).
  const [state, setState] = useState<UseModels>(() => _modelsCache.get(multi) ?? _emptyModelsState)
  // Increment to force a refetch without changing the `multi` dependency.
  const [refetchGen, setRefetchGen] = useState(0)

  // Re-sync when Settings notifies that the instance default changed.
  useEffect(() => {
    function onChanged() {
      _modelsCache.clear() // config changed — a stale list may name a removed provider
      setRefetchGen((g) => g + 1)
    }
    window.addEventListener(MODEL_CONFIG_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(MODEL_CONFIG_CHANGED_EVENT, onChanged)
  }, [])

  useEffect(() => {
    let cancelled = false
    // Always revalidate in the background — the seeded/cached state is shown meanwhile.
    Promise.all([
      fetchModels(multi).catch(() => null),
      authFetch('/api/model-config')
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null),
    ]).then(([list, cfg]) => {
      if (cancelled) return
      // `effective_model` names what a blank CLI model really runs (the CLI's own default).
      const defaultModel: string = (cfg?.model || cfg?.effective_model || '').trim()
      const defaultProvider: string = (cfg?.provider || '').trim()
      const imageReader: string = (cfg?.image_reader || '').trim()
      const taskModels: Record<string, string> = {}
      for (const [k, v] of Object.entries(cfg?.task_models ?? {}))
        if (typeof v === 'string' && v.trim()) taskModels[k] = v.trim()
      // Fold the configured default into the list if the provider didn't return it
      // (e.g. a custom local model that isn't in /v1/models), so it's always pickable.
      const models = list?.models ?? []
      const ids = new Set(models.map((m) => m.id))
      const merged =
        defaultModel && !ids.has(defaultModel) ? [{ id: defaultModel }, ...models] : models
      const next: UseModels = {
        models: merged,
        source: list?.source ?? null,
        defaultModel,
        defaultProvider,
        imageReader,
        taskModels,
        loaded: true,
      }
      _modelsCache.set(multi, next)
      setState(next)
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [multi, refetchGen])

  return state
}
