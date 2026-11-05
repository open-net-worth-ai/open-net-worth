import { useEffect, useState } from 'react'
import { PROFILE_KEY, fetchJson } from '../hooks/serverState'
import { fetchCachedQuery, getCachedQueryData } from '../state/queryCache'

type Profile = Record<string, unknown>

/** One profile preference: seeded SYNCHRONOUSLY from the cached profile (persisted across
 *  reloads), then confirmed by a fetch and re-read on `event`. Seeding is the point: each
 *  hook used to start from its default and correct itself a tick later, so with "Show cost
 *  per turn" off every chip rendered, then vanished — and the conversation jumped as the
 *  rows collapsed. `loaded` is true from the first render whenever the profile is cached.
 *  A failed load keeps the current value rather than resetting it. */
function useProfilePref<T>(
  read: (profile: Profile) => T,
  fallback: T,
  event: string,
): { value: T; loaded: boolean } {
  const [state, setState] = useState(() => {
    const cached = getCachedQueryData<Profile | null>(PROFILE_KEY)
    return cached ? { value: read(cached), loaded: true } : { value: fallback, loaded: false }
  })

  useEffect(() => {
    let alive = true
    function fetchPref() {
      fetchCachedQuery<Profile | null>(PROFILE_KEY, () => fetchJson<Profile>(PROFILE_KEY))
        .catch(() => null)
        .then((data) => {
          if (alive) setState((s) => ({ value: data != null ? read(data) : s.value, loaded: true }))
        })
    }
    void Promise.resolve().then(fetchPref)
    const onChanged = () => void Promise.resolve().then(fetchPref)
    window.addEventListener(event, onChanged)
    return () => {
      alive = false
      window.removeEventListener(event, onChanged)
    }
    // `read` is a pure projection of the profile, fixed per caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event])

  return state
}

// Fired by the Settings → Appearance → Chat toggle after a successful save so the
// (already-mounted) chat panel re-reads the preference without a reload. Mirrors
// MEMORY_SETTINGS_CHANGED_EVENT.
export const CHAT_SUGGESTIONS_CHANGED_EVENT = 'onw:chat-suggestions-changed'

// Reads profile.chat_suggestions_enabled (GET /api/profile) and re-reads it when
// CHAT_SUGGESTIONS_CHANGED_EVENT fires. Default OFF (owner, 2026-08-10):
// the chips cost a model call per turn, so an unset key or a failed load must NOT opt you
// in. Mirrors the backend default.

export interface UseChatSuggestionsEnabled {
  enabled: boolean
  loaded: boolean
}

export function useChatSuggestionsEnabled(): UseChatSuggestionsEnabled {
  const { value, loaded } = useProfilePref(
    (p) => p.chat_suggestions_enabled === true,
    false,
    CHAT_SUGGESTIONS_CHANGED_EVENT,
  )
  return { enabled: value, loaded }
}

// ── per-turn cost chip ───────────────────────────────────────────────────────
export const CHAT_COST_VISIBLE_CHANGED_EVENT = 'onw:chat-cost-visible-changed'

/** Reads profile.chat_cost_visible. Default ON: spend stays visible unless you hide it. */
export function useChatCostVisible(): { visible: boolean; loaded: boolean } {
  const { value, loaded } = useProfilePref(
    (p) => p.chat_cost_visible !== false,
    true,
    CHAT_COST_VISIBLE_CHANGED_EVENT,
  )
  return { visible: value, loaded }
}

// ── thinking layout ─────────────────────────────────────────────────────────
export const CHAT_THINKING_GROUPED_CHANGED_EVENT = 'onw:chat-thinking-grouped-changed'

/**
 * Reads profile.chat_thinking_grouped. Default OFF — reasoning is SPLIT, one block per round
 * in the position it happened.
 *
 * A turn that thinks, calls a tool, then thinks again has two thoughts belonging either side
 * of that call; merging them puts the second above the call it followed. Purely
 * presentational: the transcript records the rounds separately either way, so this only
 * chooses how they are drawn — and an unset key or a failed load must land on the honest
 * layout, not the merged one.
 */
export function useChatThinkingGrouped(): { grouped: boolean; loaded: boolean } {
  const { value, loaded } = useProfilePref(
    (p) => p.chat_thinking_grouped === true,
    false,
    CHAT_THINKING_GROUPED_CHANGED_EVENT,
  )
  return { grouped: value, loaded }
}

// ── multi-turn (mid-turn injection) ─────────────────────────────────────────
export const CHAT_MULTI_TURN_CHANGED_EVENT = 'onw:chat-multi-turn-changed'

/** Reads profile.chat_multi_turn_enabled. Default OFF ("one turn only", owner
 *  2026-08-10): Send is disabled while busy — see ADR
 *  docs/adr/2026-08-10-multi-turn-chat-defaults-off.md. */
export function useChatMultiTurnEnabled(): { enabled: boolean; loaded: boolean } {
  const { value, loaded } = useProfilePref(
    (p) => p.chat_multi_turn_enabled === true,
    false,
    CHAT_MULTI_TURN_CHANGED_EVENT,
  )
  return { enabled: value, loaded }
}

// ── Screen awareness ────────────────────────────────────────────────────────
export const SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT = 'onw:screen-awareness-default-changed'

/** Reads profile.screen_awareness_default: a device's Screen awareness until it makes its own
 *  choice with the switch. Default ON, as it was before the setting existed. */
export function useScreenAwarenessDefault(): { enabled: boolean; loaded: boolean } {
  const { value, loaded } = useProfilePref(
    (p) => p.screen_awareness_default !== false,
    true,
    SCREEN_AWARENESS_DEFAULT_CHANGED_EVENT,
  )
  return { enabled: value, loaded }
}
