// identity.tsx — visual identity system for mention chips and the picker.
//
// Ports the prototype's GLYPH_SVG / TYPE_MARKER_SVG / initials / IdentityInner / Spark
// verbatim into real TS/React. The only difference from the prototype is that type keys
// are the real MentionType names (e.g. 'holding' not 'holding', 'scheduled_task' not
// 'scheduled'). The visual output must match the prototype pixel-for-pixel.
//
// Rule: symbol characters (⬡ ◈ ↕) render optically off-centre in their box;
// these inline SVGs sit dead-centre at any size.

import { colorFor } from './mentionColors'

// ── inline SVGs for common entity chips ──────────────────────────────────────
// Keys are the real MentionType values. 'rule' and 'scheduled_task' use
// TYPE_MARKER_SVG (uniform type markers) rather than GLYPH_SVG.

export const GLYPH_SVG: Record<string, string> = {
  account:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 8 12 3.5 20 8 20 16 12 20.5 4 16Z"/></svg>',
  holding:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3 21 12 12 21 3 12Z"/><circle cx="12" cy="12" r="2.3" fill="currentColor" stroke="none"/></svg>',
  instrument:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3 21 12 12 21 3 12Z"/><circle cx="12" cy="12" r="2.3" fill="currentColor" stroke="none"/></svg>',
  transaction:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 21V4M8.5 4 5.5 7M8.5 4 11.5 7"/><path d="M15.5 3v17M15.5 20 12.5 17M15.5 20 18.5 17"/></svg>',
  agent:
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/></svg>',
  skill:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4 9.5 20"/></svg>',
}

// TYPE_MARKER_SVG — uniform markers for user-created rule/scheduled_task types.
// Users don't draw logos; rules/schedules get one consistent type marker.
export const TYPE_MARKER_SVG: Record<string, string> = {
  rule: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h18l-7 8.2V20l-4-2.2v-4.6z"/></svg>',
  scheduled_task:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.3"/><path d="M12 7.5V12l3 1.8"/></svg>',
  automation:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3"/><circle cx="12" cy="12" r="3.2"/></svg>',
}

// HSVG — inline SVGs for handle types (screen-context handles in the Peek panel).
// The symbol glyphs (◞ ▢ …) render off-centre in their box; these SVGs are dead-centred.
export const HSVG: Record<string, string> = {
  story:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h11l3 3v13H5Z"/><path d="M8 9h7M8 13h7M8 17h4"/></svg>',
  table:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="1.5"/><path d="M4 10h16M4 14.5h16M10 5v14"/></svg>',
  series:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5M4 19h16"/><path d="M7 15l3.5-4 3 2.5L20 7"/></svg>',
  view: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16"/></svg>',
  entity: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="5"/></svg>',
}

/** Return an SVG string for a handle type, falling back to 'story'. */
export function hSvg(type: string): string {
  return HSVG[type] ?? HSVG['story'] ?? ''
}

// ── Name initials — up to 2 letters from a display name ──────────────────────
// Strips non-letter/digit chars, splits on whitespace.
// Single word → first two chars; multiple words → first + last initials.
export function initials(name: string): string {
  const w = (name || '')
    .replace(/[^\p{L}\p{N} ]/gu, '')
    .split(/\s+/)
    .filter(Boolean)
  if (!w.length) return '?'
  const first = w[0] ?? ''
  const last = w[w.length - 1] ?? ''
  return (w.length === 1 ? first.slice(0, 2) : (first[0] ?? '') + (last[0] ?? '')).toUpperCase()
}

// ── Spark — the Lucius identity mark (star SVG, used in brand/pill) ───────────
export function Spark({
  size = 15,
  className,
  filled = false,
  ariaHidden,
}: {
  size?: number
  className?: string
  /** Solid star instead of outline — a thin stroke reads as washed-out at icon-only
   *  sizes (e.g. a collapsed mobile button with no label to carry the rest of the
   *  affordance). The two small accent crosses stay stroke-only either way: they are
   *  open line segments with no enclosed area, so `fill` has nothing to fill. */
  filled?: boolean
  ariaHidden?: boolean | 'true' | 'false'
}) {
  return (
    <svg
      className={className ? `acct-spark ${className}` : 'acct-spark'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={ariaHidden}
    >
      <path d="M9.94 14.06A2 2 0 0 0 8.5 12.6l-5.4-1.4a.4.4 0 0 1 0-.78l5.4-1.4A2 2 0 0 0 9.94 7.6l1.4-5.4a.4.4 0 0 1 .78 0l1.4 5.4A2 2 0 0 0 14.96 9l5.4 1.4a.4.4 0 0 1 0 .78l-5.4 1.4a2 2 0 0 0-1.44 1.44l-1.4 5.4a.4.4 0 0 1-.78 0z" />
      <path d="M19 4v3M20.5 5.5h-3M18 17v2M19 18h-2" />
    </svg>
  )
}

// ── IdentityItem — the shape the Identity component understands ───────────────
// Decoupled from MentionRef so IdentityInner can also drive the picker rows.
export interface IdentityItem {
  type: string
  label: string // display name (used for initials fallback)
  /** agent id; 'lucius' (or primary=true) triggers the brand mark */
  id?: string
  /** true → brand (Spark) mark; overrides mono/emoji */
  primary?: boolean
  /** explicit per-item color override (e.g. account color, transaction direction tint);
   *  passed as the first step in colorFor() — takes precedence over tone and type defaults */
  dot?: string
  /** semantic tone key ('cash' | 'invest' | 'credit' | 'crypto' | 'loan' | 'recv' | 'lucius');
   *  used by colorFor() at step 2 when dot is absent */
  tone?: string
  /** single emoji replaces the glyph/mono for any type that supports it */
  emoji?: string
  /** a brand mark (a connector's logo) — replaces every glyph when set */
  logo?: string
}

// ── IdentityInner — the inner identity node; caller wraps it in a sized box ──
//
// Decision tree (matches the prototype exactly):
//   agent + (primary || id===lucius) → brand (Spark)
//   agent + emoji                    → emoji
//   agent                            → monogram on dot-tinted bg
//   skill                            → /command slash
//   TYPE_MARKER_SVG[type] + emoji    → emoji
//   TYPE_MARKER_SVG[type]            → marker SVG in dot color
//   emoji (any other type)           → emoji
//   GLYPH_SVG[type]                  → glyph SVG in dot color
//   fallback                         → color swatch square
export function IdentityInner({ item }: { item: IdentityItem }) {
  const t = item.type
  const color = colorFor(item)

  if (item.logo) return <img className="id-logo" src={item.logo} alt="" />

  if (t === 'agent') {
    if (item.primary || item.id === 'lucius') {
      return (
        <span className="id-brand" style={{ color }}>
          <Spark size={15} />
        </span>
      )
    }
    if (item.emoji) return <span className="id-emoji">{item.emoji}</span>
    const bg = color + '26'
    return (
      <span className="id-mono" style={{ background: bg, color }}>
        {initials(item.label)}
      </span>
    )
  }

  if (t === 'skill') return <span className="id-cmd">/</span>

  if (TYPE_MARKER_SVG[t] !== undefined) {
    if (item.emoji) return <span className="id-emoji">{item.emoji}</span>
    return (
      <span
        className="id-marker"
        style={{ color }}
        dangerouslySetInnerHTML={{ __html: TYPE_MARKER_SVG[t] ?? '' }}
      />
    )
  }

  if (item.emoji) return <span className="id-emoji">{item.emoji}</span>

  if (GLYPH_SVG[t] !== undefined) {
    return (
      <span
        className="id-glyph"
        style={{ color }}
        dangerouslySetInnerHTML={{ __html: GLYPH_SVG[t] ?? '' }}
      />
    )
  }

  return <span className="id-swatch" style={{ background: color }} />
}

// ── Identity — sized wrapper around IdentityInner ─────────────────────────────
// Renders the identity box used in the picker list items (.ac-item-ic.id-box).
// Callers that need a raw inner node can use IdentityInner directly.
export function Identity({ item }: { item: IdentityItem }) {
  const isMono = item.type === 'agent' && !item.primary && item.id !== 'lucius' && !item.emoji
  return (
    <span className={'ac-item-ic id-box' + (isMono ? ' mono' : '')}>
      <IdentityInner item={item} />
    </span>
  )
}
