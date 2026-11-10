import { useSyncExternalStore } from 'react'

// Per-DEVICE palette (distinct from the instance-wide brand config the
// ThemeProvider fetches), persisted in localStorage and applied directly as
// `data-theme="dark|warm|light"`. The token generator emits one complete block per
// named theme; no theme hides behind another theme's key. `system` resolves to Dark
// or Light here, never in CSS.
// The inline pre-paint script in index.html performs the same resolution before
// the stylesheets load so a reload never flashes the wrong theme.
// Specs: docs/superpowers/specs/2026-07-21-dark-light-theme-design.md and
// docs/superpowers/specs/2026-08-11-light-theme-design.md

export type ThemePreference = 'dark' | 'warm' | 'light' | 'system'
export type ResolvedTheme = 'dark' | 'warm' | 'light'

export const THEME_STORAGE_KEY = 'onw-theme'

// Mirrors --bg per palette (tokens.source.mjs). The pre-paint script in index.html
// duplicates these literals — keep all three places in sync.
const META_THEME_COLOR: Record<ResolvedTheme, string> = {
  dark: '#0e1014',
  warm: '#edeae3',
  light: '#ffffff',
}

export const THEME_OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string }> = [
  { value: 'system', label: 'System' },
  { value: 'dark', label: 'Dark' },
  { value: 'warm', label: 'Warm' },
  { value: 'light', label: 'Light' },
]

const THEME_PREFERENCES = new Set<ThemePreference>(THEME_OPTIONS.map(({ value }) => value))
const BRIGHT_SCHEME_QUERY = '(prefers-color-scheme: light)'

const listeners = new Set<() => void>()

export function getThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    // No stored choice means the user has never expressed one — follow the OS rather than
    // impose dark on someone running a light desktop. An explicit pick still wins forever.
    if (!stored || !THEME_PREFERENCES.has(stored as ThemePreference)) return 'system'
    return stored as ThemePreference
  } catch {
    return 'system'
  }
}

export function resolveTheme(pref: ThemePreference = getThemePreference()): ResolvedTheme {
  if (pref === 'system') {
    return typeof window.matchMedia === 'function' && window.matchMedia(BRIGHT_SCHEME_QUERY).matches
      ? 'light'
      : 'dark'
  }
  return pref
}

function apply() {
  const resolved = resolveTheme()
  document.documentElement.setAttribute('data-theme', resolved)
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', META_THEME_COLOR[resolved])
  listeners.forEach((l) => l())
}

export function setThemePreference(pref: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, pref)
  } catch {
    // Private-mode storage failure: the theme still applies for this page load.
  }
  apply()
}

/** Boot-time wiring: stamp the attribute (idempotent with the pre-paint script)
 *  and follow OS scheme flips while the preference is 'system'. */
export function initTheme(): void {
  apply()
  if (typeof window.matchMedia === 'function') {
    window.matchMedia(BRIGHT_SCHEME_QUERY).addEventListener?.('change', () => {
      if (getThemePreference() === 'system') apply()
    })
  }
}

export function subscribeTheme(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** The theme the page is painted in NOW — the `data-theme` apply() stamped. React reads this,
 *  never resolveTheme(): matchMedia flips before its change event fires, so a render in that gap
 *  saw the new theme while the CSS vars a canvas chart reads were still the old palette, and the
 *  event's re-notify then changed nothing to re-render for (a stuck white grid on a dark page). */
function getAppliedTheme(): ResolvedTheme {
  const stamped = document.documentElement.getAttribute('data-theme')
  return stamped === 'dark' || stamped === 'warm' || stamped === 'light' ? stamped : resolveTheme()
}

export function useTheme() {
  const preference = useSyncExternalStore(subscribeTheme, getThemePreference)
  const resolved = useSyncExternalStore(subscribeTheme, getAppliedTheme)
  return { preference, resolved, setPreference: setThemePreference }
}
