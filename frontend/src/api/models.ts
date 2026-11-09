// Live model-list read API.
//
// fetchModels hits GET /api/models — the ACTIVE AI provider's available model
// ids, fetched live by the backend (Anthropic /v1/models or an
// OpenAI-compatible {base_url}/v1/models), cached briefly server-side. The
// backend NEVER raises: when a live call is impossible (no key / no network) it
// returns a small static floor with source="fallback" (vs "live"), so the
// pickers always have a usable, non-empty list.
//
// With all=true, the backend returns the UNION across ALL configured providers
// (active + any additional provider credentials stored). Each entry carries
// provider_key and provider_label so the frontend can render grouped <optgroup>
// option groups.

import { authFetch } from './client'

export interface ModelOption {
  id: string
  label?: string
  // Whether the effort dial does anything for this model (backend derives it:
  // OpenRouter supported_parameters / reasoning families / Anthropic). Absent →
  // treat as capable (don't hide a control we can't positively rule out).
  reasoning?: boolean
  // The EXACT effort tiers this model accepts, in ladder order (backend-derived).
  // Empty → hide the dial. Absent → treat as the full ladder (don't over-hide).
  effort_levels?: string[]
  // True when effort="off" genuinely disables reasoning for this model (backend-derived
  // via capabilities.can_disable_reasoning — OpenRouter reasoning models only today).
  // Drives the picker's leading "Off" option. Absent/false → no honest "Off" to show.
  can_disable?: boolean
  // True when this model is in the user's routing allowlist (Settings → Model
  // routing → Preferred models). Surfaced as a "Preferred" group atop the pickers.
  preferred?: boolean
  // Whether this model accepts IMAGE INPUT. Backend-derived: the provider's declared
  // input_modalities where published (OpenRouter), a stored override/learned refusal,
  // else a name heuristic. ABSENT = unknown, and unknown must be treated as capable —
  // claiming "no vision" for a model that has it silently removes a working feature.
  // Only `false` licenses the UI to warn.
  vision?: boolean
  // Present only when fetched with all=true:
  provider_key?: string | null
  provider_label?: string | null
}

export type ModelSource = 'live' | 'fallback'

export interface ModelsResponse {
  models: ModelOption[]
  source: ModelSource
  provider_labels?: Record<string, string>
}

// Provider key → the name the owner sees, for every configured provider. A selection whose
// provider listed no models (an unreachable endpoint) has no row to read its name from.
let providerLabels: Record<string, string> = {}

export function providerLabelFor(key: string): string | undefined {
  return providerLabels[key] || undefined
}

export async function fetchModels(all = false): Promise<ModelsResponse> {
  const url = all ? '/api/models?all=1' : '/api/models'
  const r = await authFetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const body = (await r.json()) as ModelsResponse
  if (all && body.provider_labels) providerLabels = body.provider_labels
  return body
}
