// ─────────────────────────────────────────────────────────────────────────────
// DESIGN TOKEN SOURCE OF TRUTH
//
// Single source for the design system's CSS custom properties AND their typed
// TypeScript handles. Consumed ONLY by the codegen `frontend/scripts/build-tokens.mjs`,
// which emits two checked-in artifacts:
//
//   • src/index.tokens.generated.css        — the :root { --* } block
//   • src/components/ui/tokens.generated.ts  — typed token unions + var() helpers
//
// Nothing imports THIS file at runtime. Edit values here, then run
// `npm run build:tokens` (also runs automatically on `npm run build` via prebuild).
//
// RFC: .claude/rfcs/frontend-styling-pwa-ideal.md  (Phase A — token pipeline)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ordered token groups. Each entry is
 * [nameWithoutDashes, darkCssValue, warmCssValue?, lightCssValue?].
 * `category` decides which typed union the token lands in (space|text|radius|color|shadow|z).
 * Order is preserved verbatim into the generated :root, so the output mirrors the
 * original hand-authored block — same names, same values, zero visual diff.
 *
 * THEMES: the second element is the DARK (default) value; an optional third element
 * is the WARM-theme value, emitted under `:root[data-theme='warm']`. The optional
 * fourth element overrides that value in the complete `:root[data-theme='light']` block.
 * Only color/shadow tokens may fork; a token with no third element is theme-invariant.
 * Dark stays the default — an install that never touches the theme setting renders
 * byte-identically to before.
 * Warm palette: ivory page / cream cards, never plain white; value
 * colors re-tuned darker for readable contrast on light surfaces.
 * Spec: docs/superpowers/specs/2026-07-21-dark-light-theme-design.md
 */
export const tokenGroups = [
  {
    label: 'Account & value palette',
    category: 'color',
    tokens: [
      ['bg', '#0e1014', '#edeae3', '#ffffff'],
      ['surface', '#0e1014', '#edeae3', '#ffffff'],
      ['surface2', '#0e1014', '#edeae3', '#ffffff'],
      // A PANEL BESIDE the canvas, not another canvas — the settings modal's nav rail is
      // the only user of this. It keeps the pre-2026-08-24 --surface shade on purpose:
      // once every card surface collapsed onto --bg, a rail that shares the page colour
      // has nothing left to separate it, and a border reads as a seam rather than a
      // panel (owner, 2026-08-24). Do NOT reach for this for cards.
      ['panel', '#171a21', '#f5f3ec', '#ffffff'],
      ['line', '#1f242d', '#ddd8cc', '#e2e2e2'],
      ['ink', '#f3f5f9', '#2d2a24', '#202124'],
      ['muted', '#9aa3b2', '#6d675c', '#656970'],
      ['faint', '#646c7c', '#999387', '#969aa1'],
      ['liquid', '#34b08a', '#177a5c'],
      ['contingent', '#e0a032', '#96690a'],
      ['locked', '#6b7484', '#77808f'],
      ['good', '#34b08a', '#177a5c'],
      ['bad', '#e2554f', '#bc3a34'],
      ['bad-light', '#f08a85', '#d4736e'],
      ['warn', '#e0a032', '#96690a'],
      // The warn hue for WASHING a surface (the Budget hero's status tint), in every theme:
      // warm/light's --warn is darkened for text, and a few percent of it on a pale page reads
      // beige, not yellow. Good and bad keep their hue when darkened, so they need no twin.
      ['warn-tint', '#e0a032'],
      ['onwai', '#1d9e75', '#116b4e'],
      ['ibkr', '#3b86dd', '#2666b8'],
      ['apt', '#bd7a1a', '#8a5c0e'],
      ['recv', '#d4537e', '#b23560'],
      ['cash-c', '#7c8595', '#6b7280'],
      ['crypto', '#9a7fd0', '#6f56b0'],
      ['bond', '#2ba39a', '#15756e'],
      ['overfunded', '#5aa9f0', '#2b6fb4'],
      ['tax', '#c98a2b', '#8f6410'],
    ],
  },
  {
    label: 'Intel "markets desk" shades (inset well, stronger border, faintest text tier)',
    category: 'color',
    tokens: [
      ['bg-2', '#15181f', '#e6e2d8', '#f5f5f4'],
      ['line-strong', '#2c323f', '#c8c2b3', '#d2d2d2'],
      ['fg-3', '#4a515f', '#afaba2', '#a4a7ad'],
    ],
  },
  {
    label: 'Action / interactive color + legacy aliases (ADR 0011: action != value)',
    category: 'color',
    tokens: [
      ['primary', '#3b86dd', '#2666b8'],
      ['violet', '#7C6CF5', '#5D4BD8'],
      ['lucius', '#7C6CF5'],
      ['act-teal', '#22b8cf', '#0d7f92'],
      ['accent', 'var(--primary)'],
      // The main call to action is INK, not blue: near-black on a light page, near-white on
      // dark. Blue (--primary/--accent) stays for links, focus and info; CTA ≠ selection ≠ link.
      ['cta', 'var(--ink)'],
      ['cta-hover', 'color-mix(in srgb, var(--ink) 86%, var(--bg))'],
      ['text', 'var(--ink)'],
      ['card', 'var(--surface2)'],
      ['border', 'var(--line)'],
      ['pos', 'var(--good)'],
      ['neg', 'var(--bad)'],
    ],
  },
  {
    // Assets tab category accents (docs/adr/2026-09-06-assets-tab-faithful-port.md) — mostly
    // plain aliases onto the existing value palette, so a category swatch reads consistently
    // with the rest of the app's color language instead of inventing a seventh hue family.
    label: 'Assets tab category accents',
    category: 'color',
    tokens: [
      ['cat-property', 'var(--locked)'],
      ['cat-vehicle', 'var(--contingent)'],
      ['cat-watch', 'var(--crypto)'],
      ['cat-art', 'var(--liquid)'],
      ['cat-metal', 'var(--warn)'],
      ['cat-other', 'var(--muted)'],
      ['cat-policy', 'var(--recv)'],
      ['cat-cash', 'var(--liquid)'],
      ['cat-invest', 'var(--ibkr)'],
      // The two net-worth BANDS the Assets tab also draws a swatch for. `receivable` is a
      // plain alias like the rest; `retirement` is the one value in this group with no
      // palette parent — it used --cash-c, which made the Retirement and Cash bands the same
      // grey in every chart that draws both. Deep violet: the same family as --crypto, a
      // clear luminance step away from it (1.6:1), and >=3.5:1 on all three theme grounds.
      ['cat-retirement', '#6b5fb8'],
      ['cat-receivable', 'var(--recv)'],
    ],
  },
  {
    label: 'Modal surfaces',
    category: 'color',
    tokens: [
      // DARK ONLY. Its old value (#161b27) was blue-tinted and read as a foreign card
      // floating over the app — clearest on first run, where the shell paints plain --bg
      // behind it; a dialog reads as elevated from the scrim, border and shadow, never from
      // a different hue. Warm and light are untouched: on a light ground the modal being
      // LIGHTER than the page is what separates it, so the same move there would flatten it.
      ['modal-bg', '#0e1014', '#f8f6f0', '#ffffff'],
      ['modal-border', '#1f242d', '#d5cfc1', '#dcdcdc'],
    ],
  },
  {
    // A toast is the theme INVERTED — light on dark, dark on light. On the page's own
    // surface it disappeared into the register rows behind it (owner, 2026-10-25).
    label: 'Toast surfaces',
    category: 'color',
    tokens: [
      ['toast-bg', '#f3f5f9', '#2d2a24', '#202124'],
      ['toast-ink', '#0e1014', '#f8f6f0', '#ffffff'],
      ['toast-muted', '#5c6370', '#b3ac9f', '#a9adb5'],
      ['toast-accent', '#2666b8', '#8ab8f0', '#8ab8f0'],
    ],
  },
  {
    // A hover LIFTS whatever is already under the cursor; it never repaints it to a
    // different shade. Mixing against --ink keeps that correct per theme automatically:
    // on dark --ink is near-white so the veil lightens, on light it is near-black so it
    // darkens. The opaque variants exist because an element that already carries a fill
    // cannot take a translucent background without the page showing through.
    label: 'Hover veils',
    category: 'color',
    tokens: [
      ['hover', 'color-mix(in srgb, var(--ink) 6%, transparent)'],
      ['hover-strong', 'color-mix(in srgb, var(--ink) 10%, transparent)'],
      ['hover-surface', 'color-mix(in srgb, var(--ink) 6%, var(--surface))'],
      ['hover-surface2', 'color-mix(in srgb, var(--ink) 6%, var(--surface2))'],
      ['hover-bg-2', 'color-mix(in srgb, var(--ink) 6%, var(--bg-2))'],
      // A SELECTED state must out-read a hover, or the two are indistinguishable once
      // every surface shares the page colour. 13% is the first step that stays legible
      // on #0e1014 without becoming a second card.
      ['selected', 'color-mix(in srgb, var(--ink) 13%, transparent)'],
      // A hover BORDER lifts too. --line-strong would do the job but is a fixed blue-grey
      // slab (#313749, 19% sat); mixing against --ink keeps the edge in the same neutral
      // family as the fill veil, so a hovered card never gains a hue it did not have.
      ['hover-line', 'color-mix(in srgb, var(--ink) 16%, transparent)'],
    ],
  },
  {
    label: 'Spacing scale',
    category: 'space',
    tokens: [
      ['space-0', '2px'],
      ['space-1', '4px'],
      ['space-2', '6px'],
      ['space-3', '8px'],
      ['space-4', '10px'],
      ['space-5', '12px'],
      ['space-6', '16px'],
      ['space-7', '20px'],
      ['space-8', '24px'],
      ['space-9', '32px'],
    ],
  },
  {
    label: 'Type scale (px)',
    category: 'text',
    tokens: [
      ['text-2xs', '10px'],
      ['text-xs', '11px'],
      ['text-sm', '12px'],
      ['text-base', '13px'],
      ['text-md', '14px'],
      ['text-lg', '16px'],
      ['text-18', '18px'],
      ['text-xl', '20px'],
      ['text-2xl', '22px'],
    ],
  },
  {
    label: 'Display type scale — hero numerals (26–44px)',
    category: 'text',
    tokens: [
      ['text-display-sm', '26px'],
      ['text-display-md', '32px'],
      ['text-display-lg', '40px'],
      ['text-display-xl', '44px'],
    ],
  },
  {
    label: 'Touch / tap target minimum (WCAG 2.5.5 / HIG 44pt)',
    category: 'space',
    tokens: [['tap-min', '44px']],
  },
  {
    label: 'Fixed component sizes',
    category: 'space',
    tokens: [['qr-size', '180px']],
  },
  {
    label: 'Font stacks (no typed union — consumed by CSS only)',
    category: 'font',
    tokens: [['font-mono', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace']],
  },
  {
    label: 'Safe-area insets (env() with 0px fallback)',
    category: 'space',
    tokens: [
      ['safe-t', 'env(safe-area-inset-top, 0px)'],
      ['safe-b', 'env(safe-area-inset-bottom, 0px)'],
      ['safe-l', 'env(safe-area-inset-left, 0px)'],
      ['safe-r', 'env(safe-area-inset-right, 0px)'],
    ],
  },
  {
    label:
      'Phone sheet ceiling — iOS large-detent rule: a bottom sheet stops a fixed gap short of the status bar (~10pt on iOS), never a share of the viewport',
    category: 'space',
    tokens: [
      ['sheet-top-inset', 'calc(env(safe-area-inset-top, 0px) + 12px)'],
      ['sheet-max-height', 'calc(100dvh - var(--sheet-top-inset))'],
    ],
  },
  {
    label: 'Radius scale',
    category: 'radius',
    tokens: [
      ['radius-xs', '4px'],
      ['radius-sm', '6px'],
      ['radius-md', '8px'],
      ['radius-lg', '10px'],
      ['radius-xl', '14px'],
      ['radius-2xl', '18px'],
      ['radius-pill', '999px'],
    ],
  },
  {
    label: 'Elevation',
    category: 'shadow',
    tokens: [
      [
        'shadow-modal',
        '0 24px 60px -18px rgba(0, 0, 0, 0.75)',
        '0 24px 60px -18px rgba(48, 42, 30, 0.38)',
        '0 24px 60px -18px rgba(17, 24, 39, 0.22)',
      ],
      [
        'shadow-lg',
        '0 8px 24px rgba(0, 0, 0, 0.4)',
        '0 8px 24px rgba(48, 42, 30, 0.16)',
        '0 8px 24px rgba(17, 24, 39, 0.1)',
      ],
      // Every floating toast / nudge pill. Already lifted by its inverted fill, so this only
      // seats it: a hairline contact shadow plus a short, soft falloff — never a halo.
      [
        'shadow-toast',
        '0 1px 2px rgba(0, 0, 0, 0.24), 0 6px 18px -6px rgba(0, 0, 0, 0.45)',
        '0 1px 2px rgba(48, 42, 30, 0.08), 0 6px 18px -6px rgba(48, 42, 30, 0.24)',
        '0 1px 2px rgba(17, 24, 39, 0.06), 0 6px 18px -6px rgba(17, 24, 39, 0.2)',
      ],
      // The quietest lift there is: a hint of depth UNDER a surface that carries its own soft
      // outline (the chat input block). It must not read as a shadow in its own right — the
      // border draws the edge, this only stops the block sitting flat on the page.
      [
        'shadow-sm',
        '0 1px 2px rgba(0, 0, 0, 0.18), 0 2px 5px rgba(0, 0, 0, 0.12)',
        '0 1px 2px rgba(48, 42, 30, 0.03), 0 2px 5px rgba(48, 42, 30, 0.035)',
        '0 1px 2px rgba(17, 24, 39, 0.025), 0 2px 5px rgba(17, 24, 39, 0.035)',
      ],
    ],
  },
  {
    label: 'Ink on accent fill',
    category: 'color',
    tokens: [
      ['ink-on-accent', '#fff'],
      ['ink-on-cta', 'var(--bg)'],
    ],
  },
  {
    label: 'Z-index scale',
    category: 'z',
    tokens: [
      ['z-base', 0],
      ['z-sticky', 50],
      ['z-dropdown', 100],
      ['z-overlay', 300],
      ['z-modal', 500],
      ['z-toast', 700],
    ],
  },
]
